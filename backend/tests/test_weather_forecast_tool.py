from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.models import WeatherForecastResult, WeatherForecastSlot
from app.errors import AppError, ProviderTimeoutError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.tools.weather_forecast import (
    ForecastSelectionMethod,
    GetWeatherForecastTool,
    WeatherForecastQuery,
    WeatherToolStatus,
)

KST = ZoneInfo("Asia/Seoul")
FIXED_NOW = datetime(2026, 7, 23, 6, 20, tzinfo=UTC)


def _slot(hour: int, sky_code: str = "1", precipitation_type: str = "0") -> WeatherForecastSlot:
    """기상청 코드 그대로 만든다 — D-051 이후 slot에는 판정값이 없다."""
    return WeatherForecastSlot(
        forecast_for=datetime(2026, 7, 23, hour, tzinfo=KST),
        sky_code=sky_code,
        precipitation_type=precipitation_type,
    )


class ForecastProvider:
    def __init__(
        self,
        slots: tuple[WeatherForecastSlot, ...] = (),
        error: AppError | None = None,
    ) -> None:
        self.slots = slots
        self.error = error

    async def get_forecast_slots(
        self, latitude: float, longitude: float
    ) -> ProviderResult[WeatherForecastResult]:
        if self.error:
            raise self.error
        return provider_result(
            WeatherForecastResult(
                latitude=latitude,
                longitude=longitude,
                grid_x=60,
                grid_y=127,
                slots=self.slots,
                provider="test",
            ),
            source=ProviderSource.FAKE_WEATHER,
        )


@pytest.mark.asyncio
async def test_selects_nearest_forecast_and_assumes_kst_for_naive_visit_at() -> None:
    provider = ForecastProvider(
        (
            _slot(14),
            _slot(15, sky_code="4"),
            _slot(16, sky_code="4", precipitation_type="1"),
        )
    )
    tool = GetWeatherForecastTool(provider, clock=lambda: FIXED_NOW)

    result = await tool.execute(
        WeatherForecastQuery(
            37.5788,
            126.9770,
            visit_at=datetime(2026, 7, 23, 15, 10),
        )
    )

    assert result.status is WeatherToolStatus.SUCCESS
    assert result.forecast is not None
    assert result.forecast.forecast_for.hour == 15
    assert result.forecast.timezone == "Asia/Seoul"
    assert result.forecast.timezone_assumed is True
    assert result.forecast.data_type == "forecast"
    assert result.forecast.observed_at is None
    assert result.forecast.retrieved_at == FIXED_NOW
    assert result.provider_metadata[0].source is ProviderSource.FAKE_WEATHER


@pytest.mark.asyncio
async def test_tie_prefers_future_forecast() -> None:
    provider = ForecastProvider(
        (
            _slot(15),
            _slot(16, sky_code="4", precipitation_type="1"),
        )
    )

    result = await GetWeatherForecastTool(
        provider,
        clock=lambda: FIXED_NOW,
    ).execute(
        WeatherForecastQuery(
            37.5788,
            126.9770,
            visit_at=datetime(2026, 7, 23, 15, 30, tzinfo=KST),
        )
    )

    assert result.forecast is not None
    assert result.forecast.forecast_for.hour == 16
    assert result.forecast.timezone_assumed is False


@pytest.mark.asyncio
async def test_immediate_visit_uses_earliest_future_slot() -> None:
    provider = ForecastProvider(
        (
            _slot(16),
            _slot(17, sky_code="4", precipitation_type="1"),
        )
    )

    result = await GetWeatherForecastTool(
        provider,
        clock=lambda: FIXED_NOW,
    ).execute(WeatherForecastQuery(37.5788, 126.9770))

    assert result.forecast is not None
    assert result.forecast.forecast_for.hour == 16
    assert (
        result.forecast.selection_method
        is ForecastSelectionMethod.EARLIEST_AVAILABLE
    )
    assert result.forecast.timezone_assumed is False


@pytest.mark.asyncio
async def test_explicit_now_before_first_slot_uses_earliest_available() -> None:
    """visit_at이 첫 예보시각보다 일러도 unsupported로 막지 않는다.

    초단기예보는 발표시각+1시간부터 시작한다. 매시 45분이 넘으면 provider가
    이번 시각 발표분(HH30)으로 갈아타므로 첫 예보시각이 (HH+1):00이 되고,
    visit_at=현재 시각으로 조회하는 agent_context 경로는 매시 45~59분 사이
    항상 첫 slot보다 이른 시각을 요청하게 된다. 이때 가장 이른 slot으로
    답할 수 있는데도 outside_forecast_range로 막혀서 그 15분 동안 날씨가
    통째로 빠졌다 — 뒤쪽 초과와 달리 앞쪽은 막을 이유가 없다.
    """
    now = datetime(2026, 7, 23, 15, 50, tzinfo=KST)
    provider = ForecastProvider((_slot(16), _slot(17, sky_code="4")))

    result = await GetWeatherForecastTool(provider, clock=lambda: now).execute(
        WeatherForecastQuery(37.5788, 126.9770, visit_at=now)
    )

    assert result.status is WeatherToolStatus.SUCCESS
    assert result.forecast is not None
    assert result.forecast.forecast_for.hour == 16
    assert (
        result.forecast.selection_method
        is ForecastSelectionMethod.EARLIEST_AVAILABLE
    )


@pytest.mark.asyncio
async def test_explicit_visit_outside_forecast_range_is_unsupported() -> None:
    result = await GetWeatherForecastTool(
        ForecastProvider((_slot(15),)),
        clock=lambda: FIXED_NOW,
    ).execute(
        WeatherForecastQuery(
            37.5788,
            126.9770,
            visit_at=datetime(2026, 7, 24, 15, tzinfo=KST),
        )
    )

    assert result.status is WeatherToolStatus.UNSUPPORTED
    assert result.error is not None
    assert result.error.cause == "outside_forecast_range"
    assert result.provider_metadata[0].source is ProviderSource.FAKE_WEATHER
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None


@pytest.mark.asyncio
async def test_empty_slots_are_no_data() -> None:
    result = await GetWeatherForecastTool(
        ForecastProvider(),
        clock=lambda: FIXED_NOW,
    ).execute(WeatherForecastQuery(37.5788, 126.9770))

    assert result.status is WeatherToolStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "forecast_not_found"
    assert result.provider_metadata[0].source is ProviderSource.FAKE_WEATHER
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None


@pytest.mark.asyncio
async def test_provider_no_data_error_is_no_data_not_unavailable() -> None:
    """provider가 weather_no_data를 올리면 결측으로 내려간다.

    이 분기는 오래 죽어 있었다 — provider가 KMA resultCode를 전부
    weather_unavailable로 뭉뚱그려서 weather_no_data를 던지는 곳이 없었고,
    NODATA가 재시도 가능한 장애로 둔갑했다.
    """
    result = await GetWeatherForecastTool(
        ForecastProvider(
            error=AppError(
                code="weather_no_data",
                message="사용 가능한 날씨 예보가 없습니다.",
                status_code=502,
                retryable=False,
            )
        ),
        clock=lambda: FIXED_NOW,
    ).execute(WeatherForecastQuery(37.5788, 126.9770))

    assert result.status is WeatherToolStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "forecast_not_found"
    assert result.error.retryable is False


@pytest.mark.asyncio
async def test_provider_timeout_is_unavailable() -> None:
    result = await GetWeatherForecastTool(
        ForecastProvider(error=ProviderTimeoutError("KMA")),
        clock=lambda: FIXED_NOW,
    ).execute(WeatherForecastQuery(37.5788, 126.9770))

    assert result.status is WeatherToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.cause == "timeout"
    assert result.error.retryable is True


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(91, 127), (-91, 127), (37.5, 181), (37.5, -181)],
)
def test_validates_coordinates(latitude: float, longitude: float) -> None:
    with pytest.raises(ValueError):
        WeatherForecastQuery(latitude, longitude)

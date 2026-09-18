"""방문 예정 시각과 가장 가까운 기상청 초단기예보를 선택하는 Tool."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.domain.models import WeatherForecastSlot
from app.errors import AppError
from app.providers.contracts import ProviderMetadata
from app.providers.protocols import WeatherProvider
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


WeatherToolStatus = ToolStatus


class ForecastSelectionMethod(StrEnum):
    NEAREST = "nearest"
    EARLIEST_AVAILABLE = "earliest_available"


@dataclass(frozen=True)
class WeatherForecastQuery:
    latitude: float
    longitude: float
    visit_at: datetime | None = None

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude는 -90 이상 90 이하여야 합니다.")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude는 -180 이상 180 이하여야 합니다.")


@dataclass(frozen=True)
class SelectedWeatherForecast:
    latitude: float
    longitude: float
    grid_x: int
    grid_y: int
    requested_visit_at: datetime
    forecast_for: datetime
    sky_code: str | None
    precipitation_type: str | None
    data_type: str
    observed_at: None
    retrieved_at: datetime
    timezone: str
    timezone_assumed: bool
    selection_method: ForecastSelectionMethod
    # 기존 호출부(테스트 포함)를 깨지 않도록 기본값을 둔다.
    temperature_celsius: float | None = None


WeatherToolError = ToolError


@dataclass(frozen=True)
class WeatherForecastToolResult:
    status: WeatherToolStatus
    forecast: SelectedWeatherForecast | None
    error: WeatherToolError | None
    warnings: tuple[str, ...] = ()
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetWeatherForecastTool:
    def __init__(
        self,
        provider: WeatherProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(
        self,
        query: WeatherForecastQuery,
    ) -> WeatherForecastToolResult:
        now = _as_kst(self._clock())
        timezone_assumed = query.visit_at is not None and query.visit_at.tzinfo is None
        requested_visit_at = (
            now if query.visit_at is None else _as_kst(query.visit_at)
        )

        try:
            provider_result = await self._provider.get_forecast_slots(
                query.latitude,
                query.longitude,
            )
            result = provider_result.data
        except AppError as exc:
            if exc.code == "weather_no_data":
                return _error_result(
                    WeatherToolStatus.NO_DATA,
                    "no_data",
                    "forecast_not_found",
                    False,
                )
            # 여기서 삼킨 오류는 200 응답으로 나가므로 로그가 유일한 흔적이다.
            logger.warning(
                "날씨 정보 없이 진행 (code=%s, provider=%s, details=%s)",
                exc.code,
                exc.provider,
                exc.details,
            )
            return _error_result(
                WeatherToolStatus.UNAVAILABLE,
                "unavailable",
                "timeout" if exc.code == "provider_timeout" else "upstream_error",
                exc.retryable,
            )

        slots = tuple(sorted(result.slots, key=lambda item: item.forecast_for))
        if not slots:
            return _error_result(
                WeatherToolStatus.NO_DATA,
                "no_data",
                "forecast_not_found",
                False,
                provider_metadata=(provider_result.metadata,),
            )

        # 범위 밖 판정은 뒤쪽(예보 마지막 시각 초과)에만 적용한다. 앞쪽으로 벗어나는
        # 건 초단기예보가 발표시각+1시간부터 시작하는 데서 오는 정상 상황이라,
        # 가장 이른 slot으로 대체하면 답할 수 있다 - 막을 이유가 없다.
        #
        # 매시 45분이 넘으면 이번 시각 발표분(HH30)으로 갈아타는데(resolve_base_date_time),
        # 그 발표분의 첫 예보시각은 (HH+1):00이다. 그래서 visit_at=현재 시각으로
        # 조회하는 호출부(agent_context)는 매시 45~59분 사이 항상 앞쪽으로 벗어났고,
        # 아래 EARLIEST_AVAILABLE 폴백은 visit_at이 None일 때만 걸려서 그 15분 동안
        # 날씨가 통째로 unsupported로 나갔다.
        if query.visit_at is not None and requested_visit_at > slots[-1].forecast_for:
            return _error_result(
                WeatherToolStatus.UNSUPPORTED,
                "unsupported",
                "outside_forecast_range",
                False,
                provider_metadata=(provider_result.metadata,),
            )

        selection_method = ForecastSelectionMethod.NEAREST
        if requested_visit_at < slots[0].forecast_for:
            selected = slots[0]
            selection_method = ForecastSelectionMethod.EARLIEST_AVAILABLE
        else:
            selected = _nearest_slot(slots, requested_visit_at)

        return WeatherForecastToolResult(
            status=WeatherToolStatus.SUCCESS,
            forecast=SelectedWeatherForecast(
                latitude=result.latitude,
                longitude=result.longitude,
                grid_x=result.grid_x,
                grid_y=result.grid_y,
                requested_visit_at=requested_visit_at,
                forecast_for=selected.forecast_for,
                sky_code=selected.sky_code,
                precipitation_type=selected.precipitation_type,
                temperature_celsius=selected.temperature_celsius,
                data_type="forecast",
                observed_at=None,
                retrieved_at=_as_utc(self._clock()),
                timezone="Asia/Seoul",
                timezone_assumed=timezone_assumed,
                selection_method=selection_method,
            ),
            error=None,
            provider_metadata=(provider_result.metadata,),
        )


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_KST)
    return value.astimezone(_KST)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_KST).astimezone(UTC)
    return value.astimezone(UTC)


def _nearest_slot(
    slots: tuple[WeatherForecastSlot, ...],
    requested_visit_at: datetime,
) -> WeatherForecastSlot:
    return min(
        slots,
        key=lambda slot: (
            abs((slot.forecast_for - requested_visit_at).total_seconds()),
            slot.forecast_for < requested_visit_at,
        ),
    )


def _error_result(
    status: WeatherToolStatus,
    code: str,
    cause: str,
    retryable: bool,
    provider_metadata: tuple[ProviderMetadata, ...] = (),
) -> WeatherForecastToolResult:
    message = {
        "forecast_not_found": "사용 가능한 날씨 예보가 없습니다.",
        "outside_forecast_range": "요청한 방문 시각은 제공되는 예보 범위 밖입니다.",
    }.get(cause, "날씨 정보를 가져오지 못했습니다.")
    return WeatherForecastToolResult(
        status=status,
        forecast=None,
        error=WeatherToolError(
            code=code,
            message=message,
            cause=cause,
            retryable=retryable,
        ),
        provider_metadata=provider_metadata,
    )

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest

from app.errors import AppError
from app.providers.weather import (
    RealWeatherProvider,
    has_usable_sky_or_precipitation,
    map_items_to_forecast_slots,
    resolve_base_date_time,
)


def test_resolve_base_date_time_before_45_uses_previous_hour() -> None:
    now = datetime(2026, 7, 22, 10, 20)
    assert resolve_base_date_time(now) == ("20260722", "0930")


def test_resolve_base_date_time_after_45_uses_current_hour() -> None:
    now = datetime(2026, 7, 22, 10, 50)
    assert resolve_base_date_time(now) == ("20260722", "1030")


def test_resolve_base_date_time_crosses_midnight() -> None:
    now = datetime(2026, 7, 22, 0, 10)
    assert resolve_base_date_time(now) == ("20260721", "2330")


def _fcst_item(category: str, fcst_time: str, value: str) -> dict:
    return {
        "baseDate": "20260722",
        "baseTime": "1030",
        "category": category,
        "fcstDate": "20260722",
        "fcstTime": fcst_time,
        "fcstValue": value,
        "nx": 60,
        "ny": 127,
    }


def test_maps_items_to_time_aware_forecast_slots() -> None:
    slots = map_items_to_forecast_slots(
        [
            _fcst_item("SKY", "1100", "1"),
            _fcst_item("PTY", "1100", "0"),
            _fcst_item("SKY", "1200", "4"),
            _fcst_item("PTY", "1200", "1"),
        ]
    )

    assert [slot.forecast_for.hour for slot in slots] == [11, 12]
    assert slots[0].forecast_for.tzinfo is not None
    assert slots[0].sky_code == "1"
    assert slots[0].precipitation_type == "0"
    assert slots[1].sky_code == "4"
    assert slots[1].precipitation_type == "1"


def test_parses_temperature_into_forecast_slots() -> None:
    """T1H(기온)를 버리지 않는다.

    SKY=맑음만 보면 폭염도 좋은 날씨로 읽힌다. 기온을 함께 넘겨야 D가 그 경우를
    판정할 수 있다(D-051).
    """
    slots = map_items_to_forecast_slots(
        [
            _fcst_item("SKY", "1100", "1"),
            _fcst_item("PTY", "1100", "0"),
            _fcst_item("T1H", "1100", "35.0"),
        ]
    )

    assert len(slots) == 1
    assert slots[0].temperature_celsius == 35.0


def test_ignores_non_numeric_temperature() -> None:
    """결측·비정상 값이 와도 슬롯 자체를 버리지 않는다."""
    slots = map_items_to_forecast_slots(
        [
            _fcst_item("SKY", "1100", "1"),
            _fcst_item("PTY", "1100", "0"),
            _fcst_item("T1H", "1100", "-"),
        ]
    )

    assert len(slots) == 1
    assert slots[0].temperature_celsius is None


@pytest.mark.parametrize(
    ("sky", "pty", "expected"),
    [
        ("1", "0", True),  # 둘 다 알려진 코드
        (None, "1", True),  # PTY만 있어도 살린다
        ("4", None, True),  # SKY만 있어도 살린다
        (None, None, False),  # 둘 다 결측
        ("9", "9", False),  # 둘 다 미지의 코드
        ("9", "0", False),  # PTY=0(없음)은 강수 코드가 아니라 판정 재료가 못 된다
    ],
)
def test_has_usable_sky_or_precipitation(sky: str | None, pty: str | None, expected: bool) -> None:
    assert has_usable_sky_or_precipitation(sky, pty) is expected


def test_drops_slots_without_usable_sky_or_precipitation() -> None:
    """SKY·PTY가 모두 쓸 수 없으면 그 시각 slot을 버린다.

    이 드롭 규칙은 원래 map_sky_pty_to_condition()의 예외로만 표현돼 있었다.
    D-051로 그 판정 함수를 제거할 때 필터까지 함께 사라지면 사실이 하나도 없는
    빈 slot이 D로 흘러간다 — 그걸 막으려고 규칙을 여기 못 박는다.
    """
    slots = map_items_to_forecast_slots(
        [
            _fcst_item("SKY", "1100", "1"),
            _fcst_item("PTY", "1100", "0"),
            # 1200은 기온만 있고 SKY·PTY가 없다.
            _fcst_item("T1H", "1200", "28.0"),
            # 1300은 SKY·PTY가 둘 다 미지의 코드다.
            _fcst_item("SKY", "1300", "9"),
            _fcst_item("PTY", "1300", "9"),
        ]
    )

    assert [slot.forecast_for.hour for slot in slots] == [11]


@pytest.mark.asyncio
async def test_real_weather_provider_picks_earliest_forecast_slot() -> None:
    items = [
        _fcst_item("SKY", "1300", "4"),
        _fcst_item("SKY", "1100", "1"),
        _fcst_item("PTY", "1300", "1"),
        _fcst_item("PTY", "1100", "0"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {"dataType": "JSON", "items": {"item": items}},
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RealWeatherProvider(api_key="dummy", client=client)

    slots = (await provider.get_forecast_slots(37.5636, 126.9976)).data.slots

    assert slots[0].sky_code == "1"
    assert slots[0].precipitation_type == "0"
    await client.aclose()


@pytest.mark.asyncio
async def test_real_weather_provider_returns_all_forecast_slots() -> None:
    items = [
        _fcst_item("SKY", "1100", "1"),
        _fcst_item("PTY", "1100", "0"),
        _fcst_item("SKY", "1200", "4"),
        _fcst_item("PTY", "1200", "1"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {"items": {"item": items}},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RealWeatherProvider(
            api_key="dummy",
            client=client,
        ).get_forecast_slots(37.5636, 126.9976)
        result = result.data

    assert result.grid_x > 0
    assert result.grid_y > 0
    assert len(result.slots) == 2
    assert result.provider == "kma_ultra_short_forecast"


def _result_code_handler(
    result_code: str, result_msg: str
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": result_code, "resultMsg": result_msg},
                    "body": {},
                }
            },
        )

    return handler


@pytest.mark.asyncio
async def test_real_weather_provider_raises_no_data_on_nodata_result_code() -> None:
    """03(NODATA_ERROR)은 결측이지 장애가 아니다.

    업스트림이 정상 처리한 끝에 "그 격자·발표시각에 자료가 없다"고 답한 것이라
    즉시 재시도해도 결과가 같다. weather_unavailable로 뭉뚱그리면 소비 측이
    재시도 가능한 장애로 읽고, 로그에서도 서비스키 사고와 안 갈린다.
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_result_code_handler("03", "NODATA_ERROR"))
    ) as client:
        provider = RealWeatherProvider(api_key="dummy", client=client)
        with pytest.raises(AppError) as exc_info:
            await provider.get_forecast_slots(37.5636, 126.9976)

    assert exc_info.value.code == "weather_no_data"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_real_weather_provider_raises_unavailable_on_other_result_codes() -> None:
    """03 이외의 실패 코드는 장애로 남긴다 — 결측 분기가 전부를 삼키지 않게."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            _result_code_handler(
                "22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
            )
        )
    ) as client:
        provider = RealWeatherProvider(api_key="dummy", client=client)
        with pytest.raises(AppError) as exc_info:
            await provider.get_forecast_slots(37.5636, 126.9976)

    assert exc_info.value.code == "weather_unavailable"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_real_weather_provider_does_not_chain_sensitive_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealWeatherProvider(api_key="sensitive-key", client=client)
        with pytest.raises(AppError) as exc_info:
            await provider.get_forecast_slots(37.5636, 126.9976)

    assert exc_info.value.code == "weather_unavailable"
    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_real_weather_provider_logs_gateway_reason_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """403은 인증·기간만료·쿼터를 모두 뭉뚱그리므로 errMsg가 로그에 남아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "OpenAPI_ServiceResponse": {
                    "cmmMsgHeader": {
                        "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
                        "returnAuthMsg": "등록되지 않은 서비스키",
                        "returnReasonCode": "30",
                    }
                }
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealWeatherProvider(api_key="sensitive-key", client=client)
        with caplog.at_level(logging.ERROR, logger="app.providers.weather"):
            with pytest.raises(AppError):
                await provider.get_forecast_slots(37.5636, 126.9976)

    logged = caplog.text
    assert "http_status=403" in logged
    assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in logged
    assert "returnReasonCode=30" in logged
    assert "sensitive-key" not in logged


@pytest.mark.asyncio
async def test_real_weather_provider_logs_result_code_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {
                        "resultCode": "22",
                        "resultMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
                    },
                    "body": {},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealWeatherProvider(api_key="sensitive-key", client=client)
        with caplog.at_level(logging.ERROR, logger="app.providers.weather"):
            with pytest.raises(AppError):
                await provider.get_forecast_slots(37.5636, 126.9976)

    assert "resultCode=22" in caplog.text
    assert "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR" in caplog.text

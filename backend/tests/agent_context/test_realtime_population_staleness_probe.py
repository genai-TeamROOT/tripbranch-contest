"""인구 혼잡도 조회의 낡음 감지 probe 계약을 검증한다(TP-141/D-084).

핵심 불변: probe가 성공하든 실패하든 응답(대체 지역·수치)은 절대 바뀌지 않는다.
probe는 오직 감사용 `stale_area_detected` 필드에만 영향을 준다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context import service as service_module
from app.agent_context.info_schemas import InfoContextRequest, RealtimePopulationInfoResult
from app.agent_context.service import ContextService, ContextTools
from app.config import settings
from app.domain.models import GeocodeResult
from app.errors import ProviderUnavailableError
from app.providers.contracts import ProviderSource, provider_result
from app.providers.holiday import FakeHolidayProvider
from app.providers.seoul_citydata import (
    FakeRealtimeCityDataProvider,
    FakeRealtimeCommercialProvider,
)
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.realtime_citydata import GetRealtimeCityDataTool
from app.tools.realtime_commercial import GetRealtimeCommercialTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

# 경복궁(POI008) 대표 좌표 근처. 121개 목록에 있으므로 최근접 대체는 항상
# "경복궁"으로 잡힌다 — place_name을 다른 문자열로 주면 대체가 일어난 것으로
# 취급돼 probe가 발동한다.
_NEAR_GYEONGBOKGUNG = {"latitude": 37.5798, "longitude": 126.9768}
_UNKNOWN_PLACE_NAME = "가상의궁궐"


class _FixedGeocodingProvider:
    def __init__(self, *, latitude: float, longitude: float) -> None:
        self._latitude = latitude
        self._longitude = longitude

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        del use_alias
        return provider_result(
            GeocodeResult(
                query=location_query,
                resolved_name=location_query,
                latitude=self._latitude,
                longitude=self._longitude,
            ),
            source=ProviderSource.FAKE_GEOCODING,
        )


class _ProbeFailingRealtimeCityDataProvider(FakeRealtimeCityDataProvider):
    """지정된 이름으로 조회하면 실패한다 — probe 실패 시나리오 전용."""

    def __init__(self, *, fail_for: str) -> None:
        self._fail_for = fail_for

    async def get_area_citydata(self, area_name_or_code: str):
        if area_name_or_code == self._fail_for:
            raise ProviderUnavailableError("test probe failure")
        return await super().get_area_citydata(area_name_or_code)


def _service(*, realtime_citydata_provider) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FixedGeocodingProvider(**_NEAR_GYEONGBOKGUNG),
                local_search_provider=None,
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            realtime_commercial=GetRealtimeCommercialTool(FakeRealtimeCommercialProvider()),
            realtime_citydata=GetRealtimeCityDataTool(realtime_citydata_provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime(2026, 8, 26, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )


def _request() -> InfoContextRequest:
    return InfoContextRequest(
        request_id="staleness-probe",
        place_name=_UNKNOWN_PLACE_NAME,
        place_context="explicit",
        question_type="concentration",
        specific_question=f"{_UNKNOWN_PLACE_NAME} 사람 많아?",
        visit_time="2026-08-26",
    )


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    service_module._stale_area_probe_cache.clear()
    yield
    service_module._stale_area_probe_cache.clear()


@pytest.mark.asyncio
async def test_probe_success_fills_debug_field_without_changing_response() -> None:
    response = await _service(
        realtime_citydata_provider=FakeRealtimeCityDataProvider()
    ).fetch_info_context(_request())

    assert response.status == "success"
    assert isinstance(response.result, RealtimePopulationInfoResult)
    # 응답은 여전히 최근접 대체(경복궁=POI008) 값이다 — place_name이 아니다.
    # fake provider는 조회에 쓴 문자열(area.code)을 그대로 area_name으로 되돌린다.
    assert response.result.area_name == "POI008"
    assert response.result.stale_area_detected is not None
    assert response.result.stale_area_detected.probed_area_name == _UNKNOWN_PLACE_NAME
    assert response.result.stale_area_detected.matched_area_name == "경복궁"
    assert response.result.stale_area_detected.matched_area_distance_km is not None


@pytest.mark.asyncio
async def test_probe_failure_leaves_debug_field_empty_without_changing_response() -> None:
    response = await _service(
        realtime_citydata_provider=_ProbeFailingRealtimeCityDataProvider(
            fail_for=_UNKNOWN_PLACE_NAME
        )
    ).fetch_info_context(_request())

    assert response.status == "success"
    assert isinstance(response.result, RealtimePopulationInfoResult)
    assert response.result.area_name == "POI008"
    assert response.result.stale_area_detected is None


@pytest.mark.asyncio
async def test_probe_disabled_by_setting_skips_call_entirely(monkeypatch) -> None:
    monkeypatch.setattr(settings, "seoul_area_staleness_probe_enabled", False)
    provider = _ProbeFailingRealtimeCityDataProvider(fail_for=_UNKNOWN_PLACE_NAME)

    response = await _service(realtime_citydata_provider=provider).fetch_info_context(_request())

    assert response.status == "success"
    assert isinstance(response.result, RealtimePopulationInfoResult)
    assert response.result.stale_area_detected is None

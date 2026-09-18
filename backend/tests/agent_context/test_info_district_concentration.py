"""구 이름으로 물은 혼잡도가 되묻기 없이 답으로 이어지는지 못 박는다(TP-261).

`"강서구 지금 사람 많아?"`가 되묻기로 끝나고 선택지가 `강서구` 하나뿐이라 눌러도
제자리로 돌아왔다. 서울시 실시간 인구 데이터에 구 단위 값이 없어서 생긴 일이다 —
핫스팟 121곳 기준의 장소 단위라 구를 지오코딩해 봐야 구청 좌표 하나가 나온다.

여기서 잠그는 것은 **곳 수에 따라 다르게 답하는가**다. 0곳·1곳·여러 곳이 각각
다른 답이어야 한다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    DistrictPopulationInfoResult,
    InfoContextRequest,
    RealtimePopulationInfoResult,
)
from app.agent_context.service import ContextService, ContextTools
from app.providers.holiday import FakeHolidayProvider
from app.providers.seoul_citydata import FakeRealtimeCityDataProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.realtime_citydata import GetRealtimeCityDataTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool


class _RefusingGeocodingProvider:
    """호출되면 테스트가 깨지도록 만든 지오코딩 대역.

    구 단위 혼잡도는 목록에서 지역을 직접 찾으므로 위치 해석을 거칠 이유가 없다.
    거치면 "강서구"가 다시 애매한 지명이 되어 되묻기가 살아난다.
    """

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        raise AssertionError(f"구 단위 혼잡도가 지오코딩을 불렀다: {location_query}")


class _CountingCityDataProvider(FakeRealtimeCityDataProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_area_citydata(self, area_name_or_code: str):
        self.calls.append(area_name_or_code)
        return await super().get_area_citydata(area_name_or_code)


def _service(citydata: FakeRealtimeCityDataProvider | None = None) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(_RefusingGeocodingProvider()),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            place_detail=GetPlaceDetailTool(place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            realtime_citydata=GetRealtimeCityDataTool(citydata or FakeRealtimeCityDataProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime(2026, 9, 9, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )


def _request(place_name: str) -> InfoContextRequest:
    return InfoContextRequest(
        request_id=f"district-concentration-{place_name}",
        place_name=place_name,
        place_context="explicit",
        question_type="concentration",
        specific_question=f"{place_name} 지금 사람 많아?",
    )


@pytest.mark.asyncio
async def test_district_with_several_areas_answers_with_all_of_them() -> None:
    """여러 곳이면 그 구의 지역을 전부 모아 답한다."""
    citydata = _CountingCityDataProvider()

    response = await _service(citydata).fetch_info_context(_request("강서구"))

    assert response.status == "success"
    assert isinstance(response.result, DistrictPopulationInfoResult)
    assert response.result.district_name == "강서구"
    # 강서구에 실린 4곳을 모두 부른다.
    assert len(citydata.calls) == 4
    assert {area.area_name for area in response.result.areas}


@pytest.mark.asyncio
async def test_areas_are_sorted_from_the_busiest() -> None:
    """화면이 순서를 다시 정하지 않도록 혼잡한 순으로 담아 보낸다."""
    from app.agent_context.service import CONGESTION_LEVEL_ORDER

    response = await _service().fetch_info_context(_request("종로구"))

    assert isinstance(response.result, DistrictPopulationInfoResult)
    ranks = [CONGESTION_LEVEL_ORDER.index(a.congestion_level) for a in response.result.areas]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_district_with_a_single_area_keeps_the_place_shaped_answer() -> None:
    """1곳뿐인 구는 그 지역을 물은 것과 같다.

    금천구·성북구·은평구·도봉구·노원구가 여기 해당한다. 12시간 예측과 지도가 의미를
    갖는 경우라, 여러 곳용 결과로 바꾸면 멀쩡한 정보를 이유 없이 버리게 된다.
    """

    citydata = _CountingCityDataProvider()

    response = await _service(citydata).fetch_info_context(_request("금천구"))

    assert isinstance(response.result, RealtimePopulationInfoResult)
    # 그 구의 유일한 지역(가산디지털단지역, POI013) 하나만 부른다.
    assert citydata.calls == ["POI013"]
    # 목록의 좌표를 그대로 썼으므로 "가까운 지역으로 대체"가 아니다.
    assert response.result.proxy_distance_km == 0.0
    # 여러 곳용 답과 달리 12시간 예측이 살아 있다.
    assert response.result.population_forecasts


@pytest.mark.asyncio
async def test_district_without_any_area_says_so_instead_of_asking_again() -> None:
    """중랑구에는 제공 지역이 하나도 없다.

    되물어봐야 사용자가 내놓을 수 있는 답이 없다 — 그게 이 카드의 출발점이었다.
    """
    citydata = _CountingCityDataProvider()

    response = await _service(citydata).fetch_info_context(_request("중랑구"))

    assert response.status == "no_data"
    assert isinstance(response.result, DistrictPopulationInfoResult)
    assert response.result.areas == []
    assert response.clarification is None
    assert citydata.calls == []

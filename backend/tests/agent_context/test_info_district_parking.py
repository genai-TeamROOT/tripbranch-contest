"""구 이름만으로 물은 주차 질문이 되묻기로 끝나지 않는지 못 박는다(TP-261).

`"강서구 공영주차장 자리 있어?"`가 되묻기로 끝나고, 그 되묻기의 선택지가 `강서구`
하나뿐이라 눌러도 같은 자리로 돌아오는 문제가 있었다. 구 단위 조회 경로 자체는
이미 있었지만 `question_type == "parking"`만 그 길에 들어갈 수 있어서, 공영주차장을
가장 분명히 말한 발화가 오히려 빠졌다.

여기서 잠그는 것은 **지역 검색을 건너뛰는가**다. 구 이름은 관광지 이름이 아니라
행정구역이므로 지역 검색에 넣으면 후보가 갈려 되묻기가 된다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import InfoContextRequest
from app.agent_context.service import ContextService, ContextTools
from app.domain.models import GeocodeResult, LocalSearchPlace
from app.providers.contracts import ProviderSource, provider_result
from app.providers.holiday import FakeHolidayProvider
from app.providers.municipal_parking import FakeMunicipalParkingProvider
from app.providers.seoul_citydata import (
    FakeRealtimeCityDataProvider,
    FakeRealtimeCommercialProvider,
)
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_municipal_parking import FakeMunicipalParkingCatalogRepository
from app.tools.holiday import GetHolidaysTool
from app.tools.municipal_parking import GetMunicipalParkingTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.place_detail import GetPlaceDetailTool
from app.tools.realtime_citydata import GetRealtimeCityDataTool
from app.tools.realtime_commercial import GetRealtimeCommercialTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool


class _DistrictGeocodingProvider:
    """행정구역 이름을 좌표로 바꿔 주는 지오코딩. 실제 응답처럼 주소에 구를 담는다."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        del use_alias
        self.queries.append(location_query)
        return provider_result(
            GeocodeResult(
                query=location_query,
                resolved_name=location_query,
                latitude=37.5509,
                longitude=126.8495,
            ),
            source=ProviderSource.FAKE_GEOCODING,
        )


class _AmbiguousLocalSearchProvider:
    """구 이름을 넣으면 후보가 갈리는 지역 검색.

    **일부러 여러 건을 돌려준다.** 지역 검색을 건너뛰지 못하면 여기서 후보가 갈려
    되묻기로 끝나므로, 이 대역이 호출되었는지 자체가 회귀 신호다.
    """

    def __init__(self) -> None:
        self.called = False

    async def search_places_by_name(self, query: str, *, display: int = 5):
        del query, display
        self.called = True
        return provider_result(
            (
                LocalSearchPlace(
                    name="강서구청",
                    address="서울 강서구 화곡로 302",
                    road_address="서울 강서구 화곡로 302",
                    category="공공기관",
                    latitude=37.5509,
                    longitude=126.8495,
                ),
                LocalSearchPlace(
                    name="강서구민회관",
                    address="서울 강서구 강서로 388",
                    road_address="서울 강서구 강서로 388",
                    category="문화시설",
                    latitude=37.5601,
                    longitude=126.8370,
                ),
            ),
            source=ProviderSource.FAKE_LOCAL_SEARCH,
        )


def _service(
    geocoding: _DistrictGeocodingProvider, local_search: _AmbiguousLocalSearchProvider
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(geocoding, local_search_provider=local_search),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            place_detail=GetPlaceDetailTool(place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            realtime_commercial=GetRealtimeCommercialTool(FakeRealtimeCommercialProvider()),
            realtime_citydata=GetRealtimeCityDataTool(FakeRealtimeCityDataProvider()),
            municipal_parking=GetMunicipalParkingTool(FakeMunicipalParkingProvider()),
            municipal_parking_catalog=FakeMunicipalParkingCatalogRepository(),
        ),
        candidate_limit=10,
        clock=lambda: datetime(2026, 9, 9, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )


def _request(question_type: str) -> InfoContextRequest:
    return InfoContextRequest(
        request_id=f"district-parking-{question_type}",
        place_name="강서구",
        place_context="explicit",
        question_type=question_type,  # type: ignore[arg-type]
        specific_question="강서구 공영주차장 자리 있어?",
    )


@pytest.mark.parametrize(
    "question_type",
    ["parking", "realtime_parking", "realtime_public_parking"],
)
@pytest.mark.asyncio
async def test_district_name_skips_local_search_for_every_parking_question(
    question_type: str,
) -> None:
    """세 유형 모두 지역 검색을 건너뛰고 행정구역으로 확정한다.

    전에는 `parking`만 이 경로에 들어갔다. 나머지 둘은 지역 검색으로 가서 후보가
    갈리고 되묻기로 끝났다.
    """
    geocoding = _DistrictGeocodingProvider()
    local_search = _AmbiguousLocalSearchProvider()

    response = await _service(geocoding, local_search).fetch_info_context(_request(question_type))

    assert local_search.called is False
    assert geocoding.queries == ["서울특별시 강서구"]
    assert response.status != "needs_clarification"


@pytest.mark.asyncio
async def test_place_name_still_goes_through_local_search() -> None:
    """구가 아닌 장소 이름은 지금까지처럼 지역 검색을 거친다.

    구 이름만 예외로 두는 것이지 주차 질문 전체를 행정구역 조회로 바꾸는 게 아니다.
    """
    geocoding = _DistrictGeocodingProvider()
    local_search = _AmbiguousLocalSearchProvider()
    request = _request("realtime_public_parking").model_copy(update={"place_name": "경복궁"})

    await _service(geocoding, local_search).fetch_info_context(request)

    assert "서울특별시 강서구" not in geocoding.queries

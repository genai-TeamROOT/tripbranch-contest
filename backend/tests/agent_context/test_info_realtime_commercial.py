"""INFO 실시간 카페 상권 경로의 위치·상권 대체 계약을 검증한다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agent_context.info_schemas import (
    InfoContextRequest,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimePopulationInfoResult,
)
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


class _FixedGeocodingProvider:
    def __init__(self, *, latitude: float, longitude: float) -> None:
        self._latitude = latitude
        self._longitude = longitude

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        del use_alias
        return provider_result(
            GeocodeResult(
                query=location_query,
                # 공영주차장 API는 구 단위라 실제 Geocoding처럼 주소에 자치구가
                # 포함된 결과를 준다.
                resolved_name=(
                    f"서울특별시 종로구 {location_query}"
                    if location_query == "경복궁"
                    else location_query
                ),
                latitude=self._latitude,
                longitude=self._longitude,
            ),
            source=ProviderSource.FAKE_GEOCODING,
        )


class _CafeLocalSearchProvider:
    async def search_places_by_name(self, query: str, *, display: int = 5):
        del query, display
        return provider_result(
            (
                LocalSearchPlace(
                    name="노우즈 창덕",
                    address="서울 종로구 율곡로 99",
                    road_address="서울 종로구 율곡로 99",
                    category="음식점>카페>디저트카페",
                    latitude=37.5795,
                    longitude=126.9901,
                ),
            ),
            source=ProviderSource.FAKE_LOCAL_SEARCH,
        )


class _TransitLocalSearchProvider:
    """관광 DB에는 없지만 좌표·주소는 확정되는 역을 재현한다."""

    async def search_places_by_name(self, query: str, *, display: int = 5):
        del query, display
        return provider_result(
            (
                LocalSearchPlace(
                    name="종각역 1호선",
                    address="서울특별시 종로구 종로1가",
                    road_address="서울특별시 종로구 종로 55",
                    category="교통>지하철역",
                    latitude=37.5702,
                    longitude=126.9826,
                ),
            ),
            source=ProviderSource.FAKE_LOCAL_SEARCH,
        )


class _CountingRealtimeCityDataProvider(FakeRealtimeCityDataProvider):
    """현재/미래 INFO 분기의 외부 API 호출 여부를 검증하는 테스트 더블."""

    def __init__(self) -> None:
        self.call_count = 0

    async def get_area_citydata(self, area_name_or_code: str):
        self.call_count += 1
        return await super().get_area_citydata(area_name_or_code)


def _service(
    *,
    latitude: float,
    longitude: float,
    with_cafe_local_search: bool = False,
    with_transit_local_search: bool = False,
    realtime_citydata_provider: FakeRealtimeCityDataProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FixedGeocodingProvider(latitude=latitude, longitude=longitude),
                local_search_provider=(
                    _TransitLocalSearchProvider()
                    if with_transit_local_search
                    else _CafeLocalSearchProvider()
                    if with_cafe_local_search
                    else None
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            place_detail=GetPlaceDetailTool(place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            realtime_commercial=GetRealtimeCommercialTool(FakeRealtimeCommercialProvider()),
            realtime_citydata=GetRealtimeCityDataTool(
                realtime_citydata_provider or FakeRealtimeCityDataProvider()
            ),
            municipal_parking=GetMunicipalParkingTool(FakeMunicipalParkingProvider()),
            municipal_parking_catalog=FakeMunicipalParkingCatalogRepository(),
        ),
        candidate_limit=10,
        clock=clock or (lambda: datetime.now(ZoneInfo("Asia/Seoul"))),
    )


def _request(place_name: str = "용리단길 카페") -> InfoContextRequest:
    return InfoContextRequest(
        request_id="realtime-commercial",
        place_name=place_name,
        place_context="explicit",
        question_type="realtime_commercial",
        specific_question=f"{place_name} 지금 사람 많아?",
    )


@pytest.mark.asyncio
async def test_external_commercial_area_bypasses_jongno_recommendation_boundary() -> None:
    # 용리단길은 종로구 밖이다. 그러나 서울시 상권 제공 지역이므로 위치 단계에서
    # 종로구 정책으로 막히지 않고, 카페 업종의 대체 상권 결과가 나와야 한다.
    response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(_request())

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCommercialInfoResult)
    assert response.result.is_proxy is True
    assert response.result.area_name == "용리단길"
    assert response.result.commercial_level == "바쁜 시간대"
    assert response.metadata.provider_metadata[-1].source == "fake_seoul_citydata"


@pytest.mark.asyncio
async def test_location_outside_citydata_coverage_is_explicitly_unsupported() -> None:
    response = await _service(latitude=35.1796, longitude=129.0756).fetch_info_context(
        _request("부산 카페")
    )

    assert response.status == "unsupported"
    assert response.error is not None
    assert response.error.code == "realtime_commercial_unsupported_region"


@pytest.mark.asyncio
async def test_current_cafe_question_reroutes_after_category_resolution() -> None:
    response = await _service(
        latitude=37.5795,
        longitude=126.9901,
        with_cafe_local_search=True,
    ).fetch_info_context(
        InfoContextRequest(
            request_id="cafe-reroute",
            place_name="노우즈 창덕",
            place_context="explicit",
            question_type="concentration",
            specific_question="노우즈 창덕 지금 사람 많아?",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCommercialInfoResult)
    assert response.result.population_forecasts


@pytest.mark.asyncio
async def test_current_general_concentration_prefers_nearby_realtime_population() -> None:
    """명소·역처럼 업종이 없는 현재형 질문도 실시간 인구를 먼저 쓴다."""

    provider = _CountingRealtimeCityDataProvider()
    response = await _service(
        latitude=37.5796,
        longitude=126.9770,
        realtime_citydata_provider=provider,
        clock=lambda: datetime(2026, 8, 20, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    ).fetch_info_context(
        InfoContextRequest(
            request_id="current-gyeongbokgung",
            place_name="경복궁",
            place_context="explicit",
            question_type="concentration",
            specific_question="경복궁 사람 많아?",
            visit_time="2026-08-20",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimePopulationInfoResult)
    # 82개 제공 지역의 대표 좌표 중 실제 최근접 지역을 고른다. 테스트 더블은
    # 코드만 area_name으로 되돌리므로 특정 지역명 대신 선택·거리 계약을 검증한다.
    assert response.result.area_name is not None
    assert response.result.proxy_distance_km is not None
    assert response.result.proxy_distance_km <= 1.0
    assert response.result.current_congestion_message is not None
    assert response.result.population_forecasts
    assert response.result.map_url is not None
    assert "hotspotNm=" in response.result.map_url
    assert "&y=" in response.result.map_url
    assert "&x=" in response.result.map_url
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_future_concentration_skips_realtime_citydata() -> None:
    """주말·내일처럼 미래 방문일 질문은 실시간 API를 호출하지 않는다."""

    provider = _CountingRealtimeCityDataProvider()
    response = await _service(
        latitude=37.5796,
        longitude=126.9770,
        realtime_citydata_provider=provider,
        clock=lambda: datetime(2026, 8, 20, 14, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    ).fetch_info_context(
        InfoContextRequest(
            request_id="future-gyeongbokgung",
            place_name="경복궁",
            place_context="explicit",
            question_type="concentration",
            specific_question="이번 주말 경복궁 사람 많아?",
            visit_time="2026-08-22",
        )
    )

    # 이 테스트 서비스에는 관광지 집중률 Tool을 주입하지 않았으므로 최종 상태는
    # unavailable일 수 있다. 핵심은 실시간 도시데이터 호출이 0건이라는 점이다.
    assert not isinstance(response.result, RealtimePopulationInfoResult)
    assert provider.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question_type", "question"),
    [
        ("realtime_parking", "지금 주차 자리 있어?"),
        ("realtime_subway", "지금 지하철 언제 와?"),
        ("realtime_bus", "주변 버스정류장 어디야?"),
        ("realtime_event", "오늘 주변 행사 있어?"),
        ("realtime_traffic", "지금 가는길 막혀?"),
    ],
)
async def test_realtime_citydata_question_types_return_card_fields(
    question_type: str, question: str
) -> None:
    response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(
        InfoContextRequest(
            request_id=f"citydata-{question_type}",
            place_name="용리단길",
            place_context="explicit",
            question_type=question_type,  # type: ignore[arg-type]
            specific_question=question,
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    assert response.result.question_type == question_type
    assert response.result.fields


@pytest.mark.asyncio
async def test_realtime_traffic_includes_map_preview_url_but_others_do_not() -> None:
    """도로소통 카드만 인구 혼잡도처럼 서울시 실시간 지도 미리보기 링크를 싣는다(2026-09-02 실사용
    요청).

    도로소통은 핫스팟 지역 하나에 대응하는 단일 스냅샷이라 지도로 위치를 바로
    확인하고 싶어한다는 요청이었다. 다른 도시데이터 유형(주차·지하철·버스·행사)은
    이미 카드 안에 여러 항목을 나열하므로 지도 링크가 없어도 위치를 잃지 않는다.
    """

    traffic_response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(
        InfoContextRequest(
            request_id="citydata-traffic-map-url",
            place_name="용리단길",
            place_context="explicit",
            question_type="realtime_traffic",
            specific_question="지금 가는길 막혀?",
        )
    )
    assert isinstance(traffic_response.result, RealtimeCityInfoResult)
    assert traffic_response.result.map_url is not None
    assert traffic_response.result.map_url.startswith(
        "https://data.seoul.go.kr/SeoulRtd/map?hotspotNm="
    )

    parking_response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(
        InfoContextRequest(
            request_id="citydata-parking-no-map-url",
            place_name="용리단길",
            place_context="explicit",
            question_type="realtime_parking",
            specific_question="지금 주차 자리 있어?",
        )
    )
    assert isinstance(parking_response.result, RealtimeCityInfoResult)
    assert parking_response.result.map_url is None


@pytest.mark.asyncio
async def test_realtime_subway_keeps_both_directions_of_same_station() -> None:
    """방향 충돌 버그(D-091) 회귀 — 같은 역·같은 호선의 두 방향이 모두 살아남아야 한다."""

    response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(
        InfoContextRequest(
            request_id="subway-directions",
            place_name="용리단길",
            place_context="explicit",
            question_type="realtime_subway",
            specific_question="지금 지하철 언제 와?",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    # FakeRealtimeCityDataProvider가 삼각지역 4호선 상행·하행 두 건을 준다.
    assert "삼각지역 4호선 · 상행" in response.result.fields
    assert "삼각지역 4호선 · 하행" in response.result.fields


@pytest.mark.asyncio
async def test_realtime_parking_groups_by_public_and_private() -> None:
    response = await _service(latitude=37.5311, longitude=126.9715).fetch_info_context(
        InfoContextRequest(
            request_id="parking-groups",
            place_name="용리단길",
            place_context="explicit",
            question_type="realtime_parking",
            specific_question="지금 주차 자리 있어?",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    # FakeRealtimeCityDataProvider가 공영 1곳·민영 1곳을 준다 — 둘 다 살아남아야 한다.
    assert "[공영] 테스트 공영주차장" in response.result.fields
    assert "[민영] 테스트 민영주차장" in response.result.fields


@pytest.mark.asyncio
async def test_realtime_public_parking_uses_municipal_live_counts() -> None:
    """공영을 명시하면 citydata의 민영 혼합 목록이 아닌 구 단위 API를 쓴다."""

    response = await _service(latitude=37.5788, longitude=126.9770).fetch_info_context(
        InfoContextRequest(
            request_id="public-parking",
            place_name="경복궁",
            place_context="explicit",
            question_type="realtime_public_parking",
            specific_question="경복궁 근처 공영주차장 자리 있어?",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    assert response.result.question_type == "realtime_public_parking"
    assert "[공영] 테스트 종로 공영주차장" in response.result.fields
    assert "현재 44대 주차 가능" in response.result.fields["[공영] 테스트 종로 공영주차장"]


@pytest.mark.asyncio
async def test_static_parking_without_tour_match_falls_back_to_nearby_public_parking() -> None:
    """역처럼 관광 DB에 없어도 좌표가 있으면 주변 공영주차장으로 계속 안내한다."""

    response = await _service(
        latitude=37.5702,
        longitude=126.9826,
        with_transit_local_search=True,
    ).fetch_info_context(
        InfoContextRequest(
            request_id="station-parking-fallback",
            place_name="종각역",
            place_context="explicit",
            question_type="parking",
            specific_question="종각역 주차장 정보",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    assert response.result.question_type == "realtime_public_parking"
    assert response.result.resolved_place_name == "종각역 1호선"
    assert "[공영] 테스트 종로 공영주차장" in response.result.fields


@pytest.mark.asyncio
async def test_supported_district_parking_bypasses_ambiguous_place_candidates() -> None:
    """'종로 주차장 정보'의 종로는 장소 선택이 아니라 구 단위 주차장 범위다."""

    response = await _service(
        latitude=37.5702,
        longitude=126.9826,
        # 종로와 무관한 지역 검색 응답이 있어도 행정구역 Geocoding을 바로 써야 한다.
        with_transit_local_search=True,
    ).fetch_info_context(
        InfoContextRequest(
            request_id="district-parking",
            place_name="종로",
            place_context="explicit",
            question_type="parking",
            specific_question="종로 주차장 정보",
        )
    )

    assert response.status == "success"
    assert isinstance(response.result, RealtimeCityInfoResult)
    assert response.result.question_type == "realtime_public_parking"
    assert response.result.area_name == "종로구"
    assert "[공영] 테스트 종로 공영주차장" in response.result.fields

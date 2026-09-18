"""실제 C ContextService가 조건에 따라 Tool을 조합하는 흐름을 검증한다."""

from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.agent_context.concentration_proxy import ConcentrationMappingCache
from app.agent_context.factory import get_context_provider
from app.agent_context.info_schemas import InfoContextRequest
from app.agent_context.schemas import AgentContextRequest, Coordinates, UserConditions
from app.agent_context.service import ContextService, ContextTools
from app.concentration_policy import INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT
from app.config import settings
from app.domain.models import (
    ConcentrationForecast,
    ConcentrationResult,
    GeocodeResult,
    LocalSearchPlace,
    PlaceCategoryFilter,
    StoredPlaceLocation,
    WeatherForecastResult,
)
from app.errors import AppError, ProviderUnavailableError
from app.place_search_policy import DEFAULT_PLACE_SEARCH_RADIUS_KM
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.protocols import GeocodingProvider, WeatherProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.repositories.fake_places import FakePlaceLocationRepository
from app.schemas import PlaceCandidate
from app.tools.concentration import GetConcentrationTool
from app.tools.holiday import GetHolidaysTool
from app.tools.nearby_place_details import NearbyPlaceDetailsTool
from app.tools.resolve_location import ResolveLocationTool
from app.tools.weather_forecast import GetWeatherForecastTool

KST = ZoneInfo("Asia/Seoul")


def _service(
    weather_provider: WeatherProvider | None = None,
    *,
    geocoding_provider: GeocodingProvider | None = None,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            # 집중률 조회는 매핑된 장소명으로만 나가므로(D-043) 저장소가 필요하다.
            # Factory의 fake 구성과 같은 저장소를 쓴다.
            location=ResolveLocationTool(
                geocoding_provider or FakeGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(weather_provider or FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        # FakeWeatherProvider도 현재 시각 기준 슬롯을 생성하므로 같은 기준을 쓴다.
        clock=lambda: datetime.now(KST),
    )


def _request(
    *,
    search_center: str | None = "경복궁",
    current_location: str | None = None,
    place_types: list[str] | None = None,
    place_tags: list[str] | None = None,
    max_travel_time: int | None = None,
    weather_intent: Literal["AVOID", "ENJOY", "NO_MENTION", "IGNORE"] | None = None,
    gps_location: Coordinates | None = None,
    exclude_tags: list[str] | None = None,
    excluded_place_ids: list[str] | None = None,
    resolved_search_center: Coordinates | None = None,
) -> AgentContextRequest:
    return AgentContextRequest(
        request_id="request-1",
        intent="RECOMMEND",
        gps_location=gps_location,
        excluded_place_ids=excluded_place_ids or [],
        resolved_search_center=resolved_search_center,
        conditions=UserConditions(
            search_center=search_center,
            current_location=current_location,
            place_types=place_types or [],
            place_tags=place_tags or [],
            max_travel_time=max_travel_time,
            weather_intent=weather_intent,
            exclude_tags=exclude_tags or [],
        ),
    )


class _StoredPlaceRepository:
    """INFO 집중률 요청이 지오코딩 전 DB 매핑을 쓰는지 검증하는 더블."""

    def __init__(self, place: StoredPlaceLocation) -> None:
        self._place = place

    async def find_active_places_by_name(
        self, name: str
    ) -> tuple[StoredPlaceLocation, ...]:
        return (self._place,) if name == self._place.title else ()




class _DirectNoDataConcentrationProvider:
    """첫 대상은 no_data, 인근 관광지는 정상 결과로 만드는 fallback 검사용 더블."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        self.calls.append(place_name)
        forecasts = ()
        status = ProviderStatus.NO_DATA
        if place_name == "창덕궁":
            forecasts = (
                ConcentrationForecast(
                    place_name="창덕궁",
                    forecast_date=datetime.now(KST).strftime("%Y%m%d"),
                    concentration_rate=58.0,
                    raw_data={},
                ),
            )
            status = ProviderStatus.SUCCESS
        return provider_result(
            ConcentrationResult(
                area_code=area_code,
                district_code=district_code,
                requested_place_name=place_name,
                forecasts=forecasts,
                provider="test_concentration",
            ),
            source=ProviderSource.FAKE_CONCENTRATION,
            status=status,
        )


class _MixedPlaceConcentrationProvider:
    """한 번의 조회에 여러 장소가 섞여 오는 상황을 재현하는 더블.

    tAtsNm이 부분 일치라 "종묘"로 조회하면 "종묘광장공원"도 함께 온다(2026-08-04 실측).
    """

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        self.calls.append(place_name)
        today = datetime.now(KST).strftime("%Y%m%d")
        forecasts = (
            # 응답 순서상 먼저 오는 쪽이 의도한 장소가 아니다.
            ConcentrationForecast(
                place_name="종묘광장공원",
                forecast_date=today,
                concentration_rate=35.28,
                raw_data={},
            ),
            ConcentrationForecast(
                place_name="종묘 [유네스코 세계유산]",
                forecast_date=today,
                concentration_rate=67.69,
                raw_data={},
            ),
        )
        return provider_result(
            ConcentrationResult(
                area_code=area_code,
                district_code=district_code,
                requested_place_name=place_name,
                forecasts=forecasts,
                provider="test_concentration",
            ),
            source=ProviderSource.FAKE_CONCENTRATION,
            status=ProviderStatus.SUCCESS,
        )


class _NearbyAttractionPlaceProvider(FakePlaceProvider):
    """INFO fallback의 관광지 검색 반경·유형을 검증하는 테스트용 Place Provider."""

    def __init__(self) -> None:
        self.fallback_queries: list[tuple[float, PlaceCategoryFilter | None]] = []

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = 20,
    ) -> ProviderResult[list[PlaceCandidate]]:
        if category_filter is not None and category_filter.content_type_id == "12":
            self.fallback_queries.append((search_radius_km, category_filter))
            return provider_result(
                [
                    PlaceCandidate(
                        place_id="fallback-attraction-1",
                        content_type_id="12",
                        name="창덕궁",
                        category="attraction",
                        latitude=latitude,
                        longitude=longitude,
                        raw_source="fake_place",
                    )
                ],
                source=ProviderSource.FAKE_PLACE,
            )
        return await super().search_places(
            latitude,
            longitude,
            preferred_categories,
            search_radius_km,
            region_code,
            district_code,
            category_filter,
            limit,
        )


class _MemoryConcentrationMappingRepository:
    """집중률 매핑이 있는 장소만 담은 저장소 대역."""

    def __init__(self, places: tuple[StoredPlaceLocation, ...]) -> None:
        self._places = places
        self.calls = 0

    async def find_concentration_mapped_places(self) -> tuple[StoredPlaceLocation, ...]:
        self.calls += 1
        return self._places


def _mapped_place(
    title: str, *, latitude: float, longitude: float, concentration_name: str | None = None
) -> StoredPlaceLocation:
    return StoredPlaceLocation(
        content_id=f"content-{title}",
        title=title,
        address=None,
        latitude=latitude,
        longitude=longitude,
        district_code="110",
        concentration_name=concentration_name or title,
    )


def _fallback_service(
    concentration_provider: _DirectNoDataConcentrationProvider,
    place_provider: _NearbyAttractionPlaceProvider,
    mapping_repository: _MemoryConcentrationMappingRepository | None = None,
) -> ContextService:
    # 요청 장소(경복궁)에 집중률 매핑이 있어야 직접 조회가 일어난다. 매핑이 없으면
    # 원문을 tAtsNm에 넣지 않고 곧장 인근 대체로 넘어간다.
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=_StoredPlaceRepository(
                    _mapped_place("경복궁", latitude=37.5788, longitude=126.9770)
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(concentration_provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
        concentration_mapping_cache=ConcentrationMappingCache(
            mapping_repository
            or _MemoryConcentrationMappingRepository(
                (_mapped_place("창덕궁", latitude=37.5794, longitude=126.9770),)
            )
        ),
    )


@pytest.mark.asyncio
async def test_collects_real_context_with_fake_external_providers() -> None:
    response = await _service().fetch_context(
        _request(place_types=["restaurant"], place_tags=["카페"])
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.location is not None
    assert response.context.weather is not None
    assert response.context.holidays is not None
    assert response.context.places is not None
    assert [item.place_id for item in response.context.places.data or []] == ["fake-cafe-1"]
    assert response.metadata.rule_versions == {
        "category": "tour-category-v1",
        "search_radius": "walking-radius-v1",
        "tool_execution": "context-tool-plan-v1",
    }


@pytest.mark.asyncio
async def test_missing_location_requests_clarification_without_calling_tools() -> None:
    response = await _service().fetch_context(_request(search_center=None))

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.code == "location_required"


@pytest.mark.asyncio
async def test_gps_is_used_when_spoken_location_is_missing() -> None:
    gps = Coordinates(latitude=37.5796, longitude=126.9770)

    response = await _service().fetch_context(
        _request(search_center=None, gps_location=gps)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.location is not None
    assert response.context.location.data is not None
    assert response.context.location.data.location == gps
    assert response.context.location.provider_metadata[0].source == "device_gps"


@pytest.mark.asyncio
async def test_user_location_is_kept_when_search_center_is_given() -> None:
    """검색 기준점이 따로 잡혀도 사용자 좌표는 버리지 않는다(TP-109).

    예전에는 `location_query`가 있으면 GPS를 읽지도 않아서, "경복궁 근처 카페"를
    물으면 사용자가 어디 있는지가 C에서 사라졌다. 근거 문장이 경복궁 기준 거리를
    "현재 위치에서"라고 말한 원인이다.
    """
    gps = Coordinates(latitude=37.4979, longitude=127.0276)  # 강남역

    response = await _service().fetch_context(
        _request(search_center="경복궁", gps_location=gps)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is not None
    assert response.context.user_location.data is not None
    assert response.context.user_location.data.location == gps
    assert response.context.user_location.data.source == "device_gps"
    # 기준점은 여전히 경복궁이다 — 사용자 좌표가 기준점을 밀어내지 않는다.
    assert response.context.location is not None
    assert response.context.location.data is not None
    assert response.context.location.data.source == "query"
    assert response.context.location.data.requested_query == "경복궁"
    assert response.context.location.data.location != gps


@pytest.mark.asyncio
async def test_device_gps_origin_always_has_user_location() -> None:
    """`source`가 device_gps인데 user_location이 비는 조합은 성립할 수 없다.

    발화 위치도 GPS도 없으면 요청 자체가 needs_clarification으로 끝나므로,
    기준점이 GPS라는 건 GPS가 있었다는 뜻이다.
    """
    gps = Coordinates(latitude=37.5796, longitude=126.9770)

    response = await _service().fetch_context(
        _request(search_center=None, gps_location=gps)
    )

    assert response.context is not None
    assert response.context.location is not None
    assert response.context.location.data is not None
    assert response.context.location.data.source == "device_gps"
    assert response.context.user_location is not None
    assert response.context.user_location.data is not None
    assert response.context.user_location.data.location == gps


@pytest.mark.asyncio
async def test_user_location_is_none_without_gps() -> None:
    """발화 위치도 GPS도 없으면 그 사실을 그대로 None으로 싣는다."""
    response = await _service().fetch_context(
        _request(search_center="경복궁", current_location=None, gps_location=None)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is None


class _CountingGeocodingProvider:
    """지오코딩 호출 횟수를 세는 더블. 발화 위치 해석이 호출을 몇 건 늘리는지 본다.

    질의 문자열은 기록하되 단언하지 않는다 — 종로구 랜드마크는 Provider에 닿기 전에
    formal 주소로 치환되므로("경복궁" → "서울특별시 종로구 사직로 161") 발화 문자열과
    다르다(geocoding.py::_LANDMARK_ADDRESS_ALIASES).
    """

    def __init__(self) -> None:
        self._delegate = FakeGeocodingProvider()
        self.queries: list[str] = []

    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        self.queries.append(location_query)
        return await self._delegate.geocode(location_query, use_alias=use_alias)


@pytest.mark.asyncio
async def test_spoken_location_wins_over_device_gps() -> None:
    """발화 위치와 기기 GPS가 다르면 발화가 이긴다(TP-112).

    기준점이 search_center → current_location → GPS 순인 것과 같은 우선순위다.
    한 요청 안에서 두 좌표가 서로 다른 규칙으로 정해지면 안 된다.
    """
    gps = Coordinates(latitude=37.4979, longitude=127.0276)  # 강남역

    response = await _service().fetch_context(
        _request(search_center="경복궁", current_location="인사동", gps_location=gps)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is not None
    user_location = response.context.user_location.data
    assert user_location is not None
    assert user_location.source == "query"
    # D가 "인사동에서"라고 부를 수 있는 이름이다. GPS였다면 부를 이름이 없다.
    assert user_location.requested_query == "인사동"
    assert user_location.location != gps
    # 기준점은 여전히 경복궁이다 — 사용자 위치가 기준점을 밀어내지 않는다.
    assert response.context.location is not None
    assert response.context.location.data is not None
    assert response.context.location.data.requested_query == "경복궁"


@pytest.mark.asyncio
async def test_spoken_location_is_resolved_without_gps() -> None:
    """GPS가 없어도 발화한 위치는 좌표가 된다(TP-112 문제 1).

    예전에는 `location_query = search_center or current_location`이라 search_center가
    이기면 current_location이 지오코딩조차 되지 않았다. "지금 인사동인데 경복궁 근처"
    에서 GPS가 만료되면(TTL 1시간) 사용자 위치가 통째로 사라졌다.
    """
    response = await _service().fetch_context(
        _request(search_center="경복궁", current_location="인사동", gps_location=None)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is not None
    user_location = response.context.user_location.data
    assert user_location is not None
    assert user_location.source == "query"
    assert user_location.requested_query == "인사동"


@pytest.mark.asyncio
async def test_spoken_location_reuses_search_center_resolution() -> None:
    """발화 위치와 검색 기준점이 같은 문자열이면 지오코딩을 두 번 하지 않는다.

    지오코딩까지 내려가는 이름을 쓴다 — "인사동"은 fake 저장소에 없고 fake
    지오코더는 안다. 저장소에 있는 이름(경복궁 등)은 거기서 해석이 끝나 지오코딩
    호출이 0건이라, 재사용이 깨져도 숫자가 그대로여서 이 테스트가 아무것도
    지키지 못한다.
    """
    geocoder = _CountingGeocodingProvider()

    response = await _service(geocoding_provider=geocoder).fetch_context(
        _request(search_center="인사동", current_location="인사동", gps_location=None)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is not None
    assert len(geocoder.queries) == 1


@pytest.mark.asyncio
async def test_spoken_location_adds_one_geocoding_call() -> None:
    """기준점과 다른 발화 위치는 지오코딩 호출을 정확히 1건 늘린다.

    둘 다 fake 저장소에 없는 이름이라 각각 지오코딩까지 내려간다.
    """
    geocoder = _CountingGeocodingProvider()

    await _service(geocoding_provider=geocoder).fetch_context(
        _request(search_center="광화문", current_location="인사동", gps_location=None)
    )

    assert len(geocoder.queries) == 2
    assert geocoder.queries[1] == "인사동"


@pytest.mark.asyncio
async def test_unresolvable_spoken_location_falls_back_to_device_gps() -> None:
    """발화 위치를 못 풀면 기기 GPS로 내려간다.

    D-042(Real 실패 시 Fake로 자동 전환하지 않는다)와는 다른 상황이다 — 지어낸 값이
    아니라 같은 질문("사용자가 어디 있나")에 대한 다른 사실이다.
    """
    gps = Coordinates(latitude=37.4979, longitude=127.0276)

    response = await _service().fetch_context(
        _request(search_center="경복궁", current_location="없는동네", gps_location=gps)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is not None
    user_location = response.context.user_location.data
    assert user_location is not None
    assert user_location.source == "device_gps"
    assert user_location.location == gps


@pytest.mark.asyncio
async def test_unresolvable_spoken_location_without_gps_is_none() -> None:
    """발화를 못 풀고 GPS도 없으면 사용자 위치를 지어내지 않는다."""
    response = await _service().fetch_context(
        _request(search_center="경복궁", current_location="없는동네", gps_location=None)
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.user_location is None


@pytest.mark.asyncio
async def test_unsupported_category_stops_before_external_calls() -> None:
    response = await _service().fetch_context(_request(place_types=["unknown"]))

    assert response.status == "unsupported"
    assert response.error is not None
    assert response.error.code == "unsupported_category"


@pytest.mark.asyncio
async def test_multiple_categories_are_merged_without_duplicate_places() -> None:
    response = await _service().fetch_context(
        _request(place_types=["cultural_facility", "restaurant"])
    )

    assert response.context is not None
    assert response.context.places is not None
    assert [item.place_id for item in response.context.places.data or []] == [
        "fake-museum-1",
        "fake-cafe-1",
    ]


@pytest.mark.asyncio
async def test_exclude_tags_drop_matching_candidates() -> None:
    """제외 태그가 실제로 후보를 줄인다 — 저장만 되고 무시되면 이 테스트가 깨진다."""

    request = _request(place_types=["cultural_facility", "restaurant"])
    before = await _service().fetch_context(request)
    assert before.context is not None
    assert before.context.places is not None
    assert [item.place_id for item in before.context.places.data or []] == [
        "fake-museum-1",
        "fake-cafe-1",
    ]

    after = await _service().fetch_context(
        _request(place_types=["cultural_facility", "restaurant"], exclude_tags=["박물관"])
    )

    assert after.status == "success"
    assert after.context is not None
    assert after.context.places is not None
    assert [item.place_id for item in after.context.places.data or []] == ["fake-cafe-1"]


@pytest.mark.asyncio
async def test_excluded_place_ids_drop_already_consumed_candidates() -> None:
    """소진분이 후보에서 빠진다 — 계약 필드만 받고 무시하면 이 테스트가 깨진다.

    이게 안 되면 "다른 곳 보여줘"에 같은 후보가 다시 와서 D가 전부 걸러내고
    추천이 0건이 된다.
    """

    request = _request(place_types=["cultural_facility", "restaurant"])
    before = await _service().fetch_context(request)
    assert before.context is not None
    assert before.context.places is not None
    assert [item.place_id for item in before.context.places.data or []] == [
        "fake-museum-1",
        "fake-cafe-1",
    ]

    after = await _service().fetch_context(
        _request(
            place_types=["cultural_facility", "restaurant"],
            excluded_place_ids=["fake-museum-1"],
        )
    )

    assert after.context is not None
    assert after.context.places is not None
    assert [item.place_id for item in after.context.places.data or []] == ["fake-cafe-1"]


@pytest.mark.asyncio
async def test_all_candidates_excluded_by_id_is_no_data_not_unavailable() -> None:
    """소진분으로 후보가 다 빠져도 장애가 아니라 "더 없음"이다."""

    response = await _service().fetch_context(
        _request(
            place_types=["cultural_facility", "restaurant"],
            excluded_place_ids=["fake-museum-1", "fake-cafe-1"],
        )
    )

    assert response.context is not None
    assert response.context.places is not None
    assert response.context.places.status == "no_data"
    assert response.context.places.error is None


@pytest.mark.asyncio
async def test_all_candidates_excluded_is_no_data_not_unavailable() -> None:
    """전부 제외되면 장애가 아니라 "조건에 맞는 후보 없음"이다."""

    response = await _service().fetch_context(
        _request(
            place_types=["cultural_facility", "restaurant"],
            exclude_tags=["박물관", "카페"],
        )
    )

    assert response.context is not None
    assert response.context.places is not None
    assert response.context.places.status == "no_data"
    assert response.context.places.error is None


@pytest.mark.asyncio
async def test_unmapped_exclude_tag_warns_instead_of_silently_passing() -> None:
    """분류 매핑이 없는 제외 태그는 걸러진 척하지 않고 경고로 드러난다."""

    response = await _service().fetch_context(
        _request(place_types=["restaurant"], exclude_tags=["없는태그"])
    )

    assert response.context is not None
    assert response.context.places is not None
    assert [item.place_id for item in response.context.places.data or []] == ["fake-cafe-1"]
    assert "exclude_tags_unmapped" in {
        warning.code for warning in response.context.places.warnings
    }


@pytest.mark.asyncio
async def test_factory_wires_fake_providers_into_common_context() -> None:
    """설정 기반 Factory도 수동 조립과 동일한 A–C 응답 계약을 사용한다."""

    async with httpx.AsyncClient() as client:
        provider = get_context_provider(client)
        response = await provider.fetch_context(
            _request(place_types=["restaurant"], place_tags=["카페"])
        )
        info_response = await provider.fetch_info_context(
            InfoContextRequest(
                request_id="factory-info-request",
                place_name="경복궁",
                place_context="explicit",
            )
        )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.places is not None
    assert [item.place_id for item in response.context.places.data or []] == [
        "fake-cafe-1"
    ]
    assert {
        metadata.source for metadata in response.metadata.provider_metadata
    } == {
        # 검색 중심점도 저장소를 먼저 본다. "경복궁"은 fake 저장소에 있으므로
        # 거기서 해석이 끝나고 지오코딩까지 가지 않는다.
        "fake_places",
        "fake_weather",
        "fake_place",
        "fake_holiday",
    }
    assert info_response.status == "success"
    assert info_response.result is not None
    assert info_response.result.concentration_rate == 58.0


@pytest.mark.asyncio
async def test_info_concentration_returns_direct_normalized_result() -> None:
    """INFO는 장소를 먼저 확인한 뒤 직접 집중률 한 건을 정규화해 반환한다."""

    response = await _service().fetch_info_context(
        InfoContextRequest(
            request_id="info-request-1",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is False
    assert response.result.requested_place_name == "경복궁"
    assert response.result.resolved_place_name == "경복궁"
    assert response.result.concentration_rate == 58.0
    assert response.result.concentration_level == "slightly_crowded"
    assert response.result.concentration_label == "다소 혼잡"


@pytest.mark.asyncio
async def test_info_concentration_uses_stored_mapping_before_geocoding() -> None:
    """쌈지길처럼 주소 지오코딩이 실패하는 상호명도 매핑된 집중률명을 사용한다."""
    place_provider = FakePlaceProvider()
    concentration_provider = _DirectNoDataConcentrationProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                _StoredPlaceRepository(
                    StoredPlaceLocation(
                        content_id="128553",
                        title="쌈지길",
                        address="서울특별시 종로구 인사동길 44",
                        latitude=37.5743062352,
                        longitude=126.9848674428,
                        district_code="110",
                        concentration_name="창덕궁",
                    )
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(concentration_provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="info-stored-mapping",
            place_name="쌈지길",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.requested_place_name == "쌈지길"
    assert response.result.resolved_place_name == "창덕궁"
    assert concentration_provider.calls == ["창덕궁"]


@pytest.mark.asyncio
async def test_info_concentration_queries_with_search_key_and_matches_by_name() -> None:
    """조회는 검색어로, 대조는 정식 명칭으로 한다(D-043).

    tAtsNm은 공백이 든 값에 0건을 돌려주므로 "종묘 [유네스코 세계유산]"은 "종묘"로
    조회해야 한다. 대신 그 응답에는 "종묘광장공원"도 섞여 오므로, 고를 때는 정식
    명칭으로 대조해야 엉뚱한 장소의 값을 답하지 않는다.
    """
    place_provider = FakePlaceProvider()
    concentration_provider = _MixedPlaceConcentrationProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                _StoredPlaceRepository(
                    StoredPlaceLocation(
                        content_id="126510",
                        title="종묘",
                        address="서울특별시 종로구 종로 157",
                        latitude=37.5739,
                        longitude=126.9945,
                        district_code="110",
                        concentration_name="종묘 [유네스코 세계유산]",
                        concentration_search_keys=("종묘",),
                    )
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(concentration_provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="info-search-key",
            place_name="종묘",
            place_context="explicit",
        )
    )

    assert concentration_provider.calls == ["종묘"]
    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is False
    # 함께 온 종묘광장공원(35.28)이 아니라 정식 명칭과 일치하는 값을 쓴다.
    assert response.result.concentration_rate == 67.69


@pytest.mark.asyncio
async def test_info_concentration_never_queries_unmapped_name() -> None:
    """매핑이 없으면 원문을 tAtsNm에 넣지 않고 인근 대체로 넘어간다(D-043).

    tAtsNm은 부분 일치 검색이라 "종로"를 그대로 넣으면 낙지볶음 골목·세종로공원·
    대학천 책방거리가 함께 걸리고, 그중 하나의 값이 "종로의 혼잡도"로 나간다
    (2026-08-04 실측). 활성 장소 847건 중 매핑은 100건뿐이라 이 경로가 다수다.
    """
    place_provider = FakePlaceProvider()
    concentration_provider = _DirectNoDataConcentrationProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                _StoredPlaceRepository(
                    StoredPlaceLocation(
                        content_id="264337",
                        title="쌈지길",
                        address="서울특별시 종로구 인사동길 44",
                        latitude=37.5743062352,
                        longitude=126.9848674428,
                        district_code="110",
                        concentration_name=None,
                    )
                ),
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(concentration_provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
        concentration_mapping_cache=ConcentrationMappingCache(
            _MemoryConcentrationMappingRepository(
                (_mapped_place("창덕궁", latitude=37.5744, longitude=126.9849),)
            )
        ),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="info-unmapped",
            place_name="쌈지길",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is True
    assert response.result.resolved_place_name == "창덕궁"
    # 원문("쌈지길")으로는 한 번도 조회하지 않는다.
    assert concentration_provider.calls == ["창덕궁"]


@pytest.mark.asyncio
async def test_info_concentration_returns_no_data_for_unavailable_forecast_date() -> None:
    """요청 날짜의 예측값이 없으면 다른 날짜로 바꾸지 않고 no_data를 반환한다."""

    response = await _service().fetch_info_context(
        InfoContextRequest(
            request_id="info-request-2",
            place_name="경복궁",
            place_context="explicit",
            visit_time="2030-01-01",
        )
    )

    assert response.status == "no_data"
    assert response.result is not None
    assert response.result.status == "no_data"
    assert response.result.is_proxy is False


@pytest.mark.asyncio
async def test_info_concentration_uses_nearby_attraction_only_after_direct_no_data() -> None:
    """D-036은 INFO 직접 조회가 no_data일 때만 0.5km 관광지를 대체 기준으로 쓴다."""

    concentration_provider = _DirectNoDataConcentrationProvider()
    place_provider = _NearbyAttractionPlaceProvider()
    response = await _fallback_service(
        concentration_provider, place_provider
    ).fetch_info_context(
        InfoContextRequest(
            request_id="info-fallback-request",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is True
    assert response.result.requested_place_name == "경복궁"
    assert response.result.resolved_place_name == "창덕궁"
    assert response.result.concentration_rate == 58.0
    assert concentration_provider.calls == ["경복궁", "창덕궁"]
    # 대체 장소는 집중률 매핑 테이블에서 고른다 — TourAPI 장소 검색을 쓰지 않는다.
    assert place_provider.fallback_queries == []
    assert [item.source for item in response.metadata.provider_metadata] == [
        "supabase_places",
        "fake_concentration",
        "fake_concentration",
    ]
    assert response.metadata.provider_metadata[1].status == "no_data"


@pytest.mark.asyncio
async def test_factory_uses_recommendation_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "recommendation_candidate_limit", 1)

    async with httpx.AsyncClient() as client:
        response = await get_context_provider(client).fetch_context(
            _request(place_types=["cultural_facility", "restaurant"])
        )

    assert response.context is not None
    assert response.context.places is not None
    assert len(response.context.places.data or []) == 1


class _RecordingPlaceProvider(FakePlaceProvider):
    def __init__(self) -> None:
        self.search_radii: list[float] = []

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = 20,
    ) -> ProviderResult[list[PlaceCandidate]]:
        self.search_radii.append(search_radius_km)
        return await super().search_places(
            latitude=latitude,
            longitude=longitude,
            preferred_categories=preferred_categories,
            search_radius_km=search_radius_km,
            region_code=region_code,
            district_code=district_code,
            category_filter=category_filter,
            limit=limit,
        )


class _RecordingWeatherProvider(FakeWeatherProvider):
    def __init__(self) -> None:
        super().__init__()
        self.forecast_calls = 0

    async def get_forecast_slots(
        self,
        latitude: float,
        longitude: float,
    ) -> ProviderResult[WeatherForecastResult]:
        self.forecast_calls += 1
        return await super().get_forecast_slots(latitude, longitude)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_travel_time", "expected_radius_km"),
    [
        (None, DEFAULT_PLACE_SEARCH_RADIUS_KM),
        (1, 0.3),
        (5, 0.35),
        (30, 2.1),
        (300, 20.0),
    ],
)
async def test_max_travel_time_controls_place_search_radius(
    max_travel_time: int | None,
    expected_radius_km: float,
) -> None:
    """최대 이동시간이 실제 장소 Tool Query의 검색 반경으로 전달되는지 검증한다."""

    place_provider = _RecordingPlaceProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(FakeGeocodingProvider()),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )

    response = await service.fetch_context(
        _request(
            place_types=["restaurant"],
            place_tags=["카페"],
            max_travel_time=max_travel_time,
        )
    )

    assert response.status == "success"
    # 이후 기록되는 1km 호출은 Fake 상세정보 구현의 내부 후보 재조회다.
    assert place_provider.search_radii[0] == pytest.approx(expected_radius_km)


@pytest.mark.asyncio
async def test_weather_ignore_returns_success_without_weather_context() -> None:
    weather_provider = _RecordingWeatherProvider()
    response = await _service(weather_provider).fetch_context(
        _request(
            place_types=["restaurant"],
            place_tags=["카페"],
            weather_intent="IGNORE",
        )
    )

    assert response.status == "success"
    assert response.context is not None
    assert response.context.weather is None
    assert weather_provider.forecast_calls == 0
    assert all(warning.code != "weather_missing" for warning in response.warnings)


@pytest.mark.asyncio
async def test_info_concentration_fallback_uses_mapped_concentration_name() -> None:
    """집중률 조회에는 TourAPI 장소명이 아니라 매핑 테이블의 이름을 쓴다."""
    concentration_provider = _DirectNoDataConcentrationProvider()
    repository = _MemoryConcentrationMappingRepository(
        (
            _mapped_place(
                "창덕궁과 창경궁",
                latitude=37.5794,
                longitude=126.9770,
                concentration_name="창덕궁",
            ),
        )
    )

    response = await _fallback_service(
        concentration_provider, _NearbyAttractionPlaceProvider(), repository
    ).fetch_info_context(
        InfoContextRequest(
            request_id="info-mapped-name",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert concentration_provider.calls == ["경복궁", "창덕궁"]


@pytest.mark.asyncio
async def test_info_concentration_fallback_returns_no_data_outside_radius() -> None:
    """반경 밖 매핑 장소는 대체 기준으로 쓰지 않는다."""
    concentration_provider = _DirectNoDataConcentrationProvider()
    repository = _MemoryConcentrationMappingRepository(
        (_mapped_place("해운대", latitude=35.1587, longitude=129.1604),)
    )

    response = await _fallback_service(
        concentration_provider, _NearbyAttractionPlaceProvider(), repository
    ).fetch_info_context(
        InfoContextRequest(
            request_id="info-out-of-radius",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "no_data"
    # 대체 후보가 없으면 집중률을 다시 조회하지 않는다.
    assert concentration_provider.calls == ["경복궁"]


@pytest.mark.asyncio
async def test_info_concentration_fallback_tries_next_place_when_nearest_has_no_data() -> None:
    """매핑에 이름이 있어도 조회가 실패할 수 있다 — 다음으로 가까운 곳을 시도한다.

    실측(2026-08-03): 안국역에서 가장 가까운 "서울 운현궁"이 이름 불일치로 no_data라
    한 곳만 시도하던 기존 구현은 그대로 실패했다.
    """
    concentration_provider = _DirectNoDataConcentrationProvider()
    repository = _MemoryConcentrationMappingRepository(
        (
            # 더 가깝지만 집중률 조회가 실패하는 장소.
            _mapped_place("운현궁", latitude=37.5789, longitude=126.9771),
            _mapped_place("창덕궁", latitude=37.5794, longitude=126.9770),
        )
    )

    response = await _fallback_service(
        concentration_provider, _NearbyAttractionPlaceProvider(), repository
    ).fetch_info_context(
        InfoContextRequest(
            request_id="info-second-candidate",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is True
    assert response.result.resolved_place_name == "창덕궁"
    # 직접 조회 → 1순위(실패) → 2순위(성공) 순으로 시도한다.
    assert concentration_provider.calls == ["경복궁", "운현궁", "창덕궁"]


@pytest.mark.asyncio
async def test_info_concentration_fallback_stops_at_attempt_limit() -> None:
    """상한을 넘겨 계속 시도하지 않는다 — 지연이 무한정 늘어나면 안 된다."""
    concentration_provider = _DirectNoDataConcentrationProvider()
    repository = _MemoryConcentrationMappingRepository(
        tuple(
            _mapped_place(f"실패장소{index}", latitude=37.5789, longitude=126.9771)
            for index in range(5)
        )
    )

    response = await _fallback_service(
        concentration_provider, _NearbyAttractionPlaceProvider(), repository
    ).fetch_info_context(
        InfoContextRequest(
            request_id="info-attempt-limit",
            place_name="경복궁",
            place_context="explicit",
        )
    )

    assert response.status == "no_data"
    # 직접 조회 1회 + 대체 후보 INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT회.
    assert len(concentration_provider.calls) == 1 + INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT


class _AlwaysFailingGeocodingProvider:
    """항상 장애를 내는 Geocoding 대역."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def geocode(self, location_query: str, *, use_alias: bool = True):
        self.calls.append(location_query)
        raise ProviderUnavailableError("NaverGeocoding", detail="테스트 장애")


@pytest.mark.asyncio
async def test_real_provider_failure_surfaces_as_unavailable_without_fake_fallback() -> None:
    """Real Provider가 실패해도 Fake 데이터로 대체하지 않는다(D-042).

    조용히 Fake로 낮추면 "테스트 카페" 같은 stub이 정상 응답처럼 나가, 개발자도
    사용자도 실데이터를 보고 있는지 알 수 없게 된다. 실패는 unavailable로 드러난다.
    """
    geocoding = _AlwaysFailingGeocodingProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(geocoding),
            places=NearbyPlaceDetailsTool(FakePlaceProvider(), FakePlaceProvider()),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )

    response = await service.fetch_context(_request(place_types=["restaurant"]))

    assert geocoding.calls, "Geocoding을 실제로 호출했어야 한다"
    assert response.status == "unavailable"
    assert response.error is not None
    # 실패 내역은 context에 남지만, Fake 좌표로 대체되지는 않는다.
    assert response.context is not None
    assert response.context.location is not None
    assert response.context.location.status == "unavailable"
    assert response.context.location.data is None
    # 후속 Tool도 돌지 않아 Fake 후보가 섞이지 않는다.
    assert response.context.places is None


class _KeyOrderConcentrationProvider:
    """지정한 검색어에만 응답하고 나머지는 0건을 돌려주는 더블(D-057).

    tAtsNm은 이름이 안 맞으면 오류가 아니라 빈 목록을 준다. 검색어 목록을 순서대로
    시도하는지, 결과가 나온 뒤에는 멈추는지를 호출 기록으로 확인한다.
    """

    def __init__(self, answering_key: str, place_name: str) -> None:
        self.answering_key = answering_key
        self.place_name = place_name
        self.calls: list[str | None] = []

    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        self.calls.append(place_name)
        if place_name != self.answering_key:
            return provider_result(
                ConcentrationResult(
                    area_code=area_code,
                    district_code=district_code,
                    requested_place_name=place_name,
                    forecasts=(),
                    provider="test_concentration",
                ),
                source=ProviderSource.FAKE_CONCENTRATION,
                status=ProviderStatus.NO_DATA,
            )
        return provider_result(
            ConcentrationResult(
                area_code=area_code,
                district_code=district_code,
                requested_place_name=place_name,
                forecasts=(
                    ConcentrationForecast(
                        place_name=self.place_name,
                        forecast_date=datetime.now(KST).strftime("%Y%m%d"),
                        concentration_rate=41.5,
                        raw_data={},
                    ),
                ),
                provider="test_concentration",
            ),
            source=ProviderSource.FAKE_CONCENTRATION,
            status=ProviderStatus.SUCCESS,
        )


def _concentration_service(provider: object, location: StoredPlaceLocation) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(), _StoredPlaceRepository(location)
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(provider),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


@pytest.mark.asyncio
async def test_info_concentration_stops_at_first_key_that_answers() -> None:
    """첫 검색어가 답하면 뒤 토큰은 호출하지 않는다 — 평상시 호출 수가 늘면 안 된다."""
    provider = _KeyOrderConcentrationProvider(
        "닭한마리", "서울 동대문 닭한마리 골목"
    )
    service = _concentration_service(
        provider,
        StoredPlaceLocation(
            content_id="704507",
            title="서울 동대문 닭한마리 골목",
            address="서울특별시 종로구 종로40가길",
            latitude=37.5706,
            longitude=127.0092,
            district_code="110",
            concentration_name="서울 동대문 닭한마리 골목",
            concentration_search_keys=("닭한마리", "동대문", "골목", "서울"),
        ),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="req-keys-1",
            # 이름 해석은 이 테스트의 관심사가 아니다 — 더블이 정식 명칭으로만
            # 조회되므로 그대로 넣고, 검증 대상은 검색어 호출 순서다.
            place_name="서울 동대문 닭한마리 골목",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert provider.calls == ["닭한마리"]


@pytest.mark.asyncio
async def test_info_concentration_falls_through_to_later_key() -> None:
    """앞 검색어가 0건이면 다음 토큰으로 넘어간다.

    검색어를 하나만 두던 때는 여기서 그대로 no_data가 됐다(D-057).
    """
    provider = _KeyOrderConcentrationProvider("청와대", "청와대 앞길")
    service = _concentration_service(
        provider,
        StoredPlaceLocation(
            content_id="126533",
            title="청와대 앞길",
            address="서울특별시 종로구 청와대로",
            latitude=37.5866,
            longitude=126.9748,
            district_code="110",
            concentration_name="청와대 앞길",
            concentration_search_keys=("앞길", "청와대"),
        ),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="req-keys-2",
            place_name="청와대 앞길",
            place_context="explicit",
        )
    )

    assert response.status == "success"
    assert provider.calls == ["앞길", "청와대"]


class _CountingPlaceLocationRepository:
    """조회 횟수를 세는 저장소. 아는 이름만 맞다고 답한다.

    예전에는 무엇을 물어도 맞다고 답했다 — 검색 중심점이 저장소를 아예 거치지
    않던 시절에는 호출 0건만 세면 됐기 때문이다. 지금은 검색 중심점도 저장소를
    보므로, 그대로 두면 어떤 이름을 넣어도 저장소에서 해석이 끝나 그 뒤 단계가
    한 줄도 실행되지 않는다.
    """

    def __init__(self, titles: tuple[str, ...] = ()) -> None:
        self._titles = titles
        self.calls: list[str] = []

    async def find_active_places_by_name(self, name: str):
        self.calls.append(name)
        if name.strip() not in self._titles:
            return ()
        return (
            StoredPlaceLocation(
                content_id="128553",
                title=name,
                address="서울특별시 종로구",
                latitude=37.5788,
                longitude=126.9770,
                district_code="110",
                concentration_name=name,
            ),
        )


class _EmptyLocalSearchProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_places_by_name(self, query: str, *, display: int = 5):
        self.calls.append(query)
        return provider_result((), source=ProviderSource.FAKE_LOCAL_SEARCH)


class _FailingGeocodingProvider:
    """찾지 못한 경우. 실제 Provider와 같은 code를 써야 NO_DATA로 매핑된다."""

    async def geocode(self, query: str, *, use_alias: bool = True):
        raise AppError(
            code="location_not_found", message="찾지 못했어요.", status_code=404
        )


def _search_center_service(
    repository: _CountingPlaceLocationRepository,
    local_search: _EmptyLocalSearchProvider,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FailingGeocodingProvider(),
                place_repository=repository,
                local_search_provider=local_search,
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


@pytest.mark.asyncio
async def test_recommend_asks_repository_once_for_search_center() -> None:
    """추천의 검색 중심점도 저장소를 보되 한 번만 묻는다.

    예전에는 아예 건너뛰었다 — 사다리를 한 칸씩 던지느라 코퍼스에 없는 이름에
    `places`를 4번 뒤졌기 때문이다("안국역" 실측, cc3da0ed). 필터를 or= 하나로
    합쳐 그 비용이 한 번으로 줄면서 전제가 바뀌었고, "명동성당 근처"처럼 저장소에
    있는 이름을 검색 중심으로 쓰는 요청을 살리려면 봐야 한다.

    코퍼스 밖 이름은 여전히 빈손이므로 지역 검색으로 내려간다.
    """
    repository = _CountingPlaceLocationRepository()
    local_search = _EmptyLocalSearchProvider()

    response = await _search_center_service(repository, local_search).fetch_context(
        _request(search_center="안국역")
    )

    assert repository.calls == ["안국역"]
    assert local_search.calls == ["안국역"]
    assert response.status == "needs_clarification"


@pytest.mark.asyncio
async def test_recommend_resolves_search_center_from_repository() -> None:
    """저장소에 있는 이름은 거기서 끝난다 — 지역 검색까지 가지 않는다.

    "명동성당 근처"가 되묻기로 새던 경로다. 지역 검색은 정확 일치나 첫 토큰
    일치만 받는데, 후보가 전부 주변 상호("르빵 명동성당점" 등)라 하나도 고르지
    못한다.
    """
    repository = _CountingPlaceLocationRepository(titles=("명동성당",))
    local_search = _EmptyLocalSearchProvider()

    response = await _search_center_service(repository, local_search).fetch_context(
        _request(search_center="명동성당")
    )

    assert repository.calls == ["명동성당"]
    assert local_search.calls == []
    assert response.status == "success"


@pytest.mark.asyncio
async def test_search_center_failure_asks_user_for_a_location() -> None:
    """지역검색·지오코딩이 모두 못 찾으면 저장소로 되돌아가지 않고 되묻는다.

    앞에서 이미 한 번 물어 빈손이었으므로 되돌아가도 같은 질의를 두 번 던지는
    것일 뿐이다. 위치를 못 찾았다는 사실을 그대로 올려 사용자에게 구체적인
    위치를 요청한다.
    """
    repository = _CountingPlaceLocationRepository()

    response = await _search_center_service(
        repository, _EmptyLocalSearchProvider()
    ).fetch_context(_request(search_center="없는장소이름"))

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.code == "location_required"
    assert repository.calls == ["없는장소이름"]


# --- TP-171: 오늘 혼잡 질문이 위치 해석에서 저장소 장소를 놓치지 않는다 ---


class _FixedLocalSearchProvider:
    """한 후보만 항상 돌려주는 지역 검색 대역."""

    def __init__(self, place: LocalSearchPlace) -> None:
        self._place = place
        self.calls: list[str] = []

    async def search_places_by_name(self, query: str, *, display: int = 5):
        self.calls.append(query)
        return provider_result((self._place,), source=ProviderSource.FAKE_LOCAL_SEARCH)


@pytest.mark.asyncio
async def test_info_concentration_today_question_resolves_via_database_first() -> None:
    """TP-171: 명동성당류 — 오늘 혼잡 질문도 저장소를 먼저 본다.

    REALTIME_CITYDATA로 위치를 풀던 예전 코드라면 저장소를 건너뛰고 곧장
    Geocoding으로 갔을 것이다. Geocoding을 일부러 장애로 만들어 두고, 실제로
    한 번도 불리지 않았음을 확인해 DB가 먼저 소비됐다는 걸 증명한다.
    """
    geocoding = _AlwaysFailingGeocodingProvider()
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                geocoding,
                place_repository=_StoredPlaceRepository(
                    # 121곳 실시간 인구 지역 반경(1km) 밖의 좌표를 골라 이 테스트가
                    # realtime_citydata Tool 없이도 곧장 집중률로 떨어지게 한다.
                    _mapped_place("명동성당", latitude=37.30, longitude=127.20)
                ),
            ),
            places=NearbyPlaceDetailsTool(FakePlaceProvider(), FakePlaceProvider()),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="today-population-db-hit",
            place_name="명동성당",
            place_context="explicit",
            specific_question="명동성당 지금 붐벼?",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.requested_place_name == "명동성당"
    assert response.result.resolved_place_name == "명동성당"
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_info_concentration_today_question_allows_realtime_hub_outside_service_area() -> None:
    """TP-171: 지원 25개 구(D-107, 서울 전역) 밖의 실시간 인구 허브류 — DB엔
    없고 지원 구 밖이어도 위치 단계에서 막히지 않는다.

    PLACE_IDENTITY로 바꾸면서 지역 제한까지 같이 켜졌다면 이 요청은 unsupported로
    끝났을 것이다 — enforce_service_area 오버라이드가 실제로 동작하는지 증명한다.
    좌표는 서울 25개 구 전체 밖(경기 남부)의 합성값이라, 실제 지명이 무엇이든
    이 판정에는 영향이 없다.
    """
    outside_lat, outside_lon = 37.30, 127.20
    local_search = _FixedLocalSearchProvider(
        LocalSearchPlace(
            name="강남역",
            address="서울특별시 강남구 역삼동",
            road_address=None,
            category="지하철역",
            latitude=outside_lat,
            longitude=outside_lon,
        )
    )
    service = ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FailingGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(()),
                local_search_provider=local_search,
            ),
            places=NearbyPlaceDetailsTool(FakePlaceProvider(), FakePlaceProvider()),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
        concentration_mapping_cache=ConcentrationMappingCache(
            _MemoryConcentrationMappingRepository(
                (_mapped_place("강남역 인근 명소", latitude=outside_lat, longitude=outside_lon),)
            )
        ),
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="today-population-outside-area",
            place_name="강남역",
            place_context="explicit",
            specific_question="강남역 지금 붐벼?",
        )
    )

    assert local_search.calls == ["강남역"]
    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is True
    assert response.result.resolved_place_name == "강남역 인근 명소"


def _name_only_fallback_service(
    mapping_repository: _MemoryConcentrationMappingRepository,
) -> ContextService:
    """위치 해석이 완전히 실패하는 환경(DB 미스·지역 검색 없음·Geocoding 장애)."""
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                _FailingGeocodingProvider(),
                place_repository=FakePlaceLocationRepository(()),
                local_search_provider=_EmptyLocalSearchProvider(),
            ),
            places=NearbyPlaceDetailsTool(FakePlaceProvider(), FakePlaceProvider()),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
        concentration_mapping_cache=ConcentrationMappingCache(mapping_repository),
    )


@pytest.mark.asyncio
async def test_info_concentration_falls_back_to_name_match_when_location_resolution_fails() -> (
    None
):
    """TP-171 플랜 B: 위치 해석이 완전히 실패해도 이름이 매핑에 정확히 하나면 답한다."""
    service = _name_only_fallback_service(
        _MemoryConcentrationMappingRepository(
            (_mapped_place("아시아프", latitude=37.5109, longitude=127.0600),)
        )
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="name-only-fallback-success",
            place_name="아시아프",
            place_context="explicit",
            specific_question="아시아프 붐벼?",
        )
    )

    assert response.status == "success"
    assert response.result is not None
    assert response.result.is_proxy is False
    assert response.result.requested_place_name == "아시아프"
    assert response.result.resolved_place_name == "아시아프"


@pytest.mark.asyncio
async def test_info_concentration_name_only_fallback_returns_no_data_without_match() -> None:
    """이름이 매핑에 없으면 억지로 대체하지 않고 no_data로 끝낸다."""
    service = _name_only_fallback_service(
        _MemoryConcentrationMappingRepository(
            (_mapped_place("다른장소", latitude=37.5109, longitude=127.0600),)
        )
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="name-only-fallback-no-match",
            place_name="아시아프",
            place_context="explicit",
            specific_question="아시아프 붐벼?",
        )
    )

    assert response.status == "no_data"


@pytest.mark.asyncio
async def test_info_concentration_name_only_fallback_skips_ambiguous_duplicates() -> None:
    """이름이 매핑에 둘 이상이면 하나를 임의로 고르지 않고 no_data로 끝낸다."""
    service = _name_only_fallback_service(
        _MemoryConcentrationMappingRepository(
            (
                _mapped_place("아시아프", latitude=37.5109, longitude=127.0600),
                _mapped_place("아시아프", latitude=37.5200, longitude=127.0700),
            )
        )
    )

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="name-only-fallback-ambiguous",
            place_name="아시아프",
            place_context="explicit",
            specific_question="아시아프 붐벼?",
        )
    )

    assert response.status == "no_data"


@pytest.mark.asyncio
async def test_info_event_does_not_use_name_only_concentration_fallback() -> None:
    """이름-일치 폴백은 concentration 문항 전용이다 — event 등은 그대로 no_data."""
    mapping_repository = _MemoryConcentrationMappingRepository(
        (_mapped_place("아시아프", latitude=37.5109, longitude=127.0600),)
    )
    service = _name_only_fallback_service(mapping_repository)

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="event-no-fallback",
            place_name="아시아프",
            place_context="explicit",
            question_type="event",
            specific_question="아시아프 오늘 행사 있어?",
        )
    )

    assert response.status == "no_data"
    assert mapping_repository.calls == 0


class _QueryKeyedLocalSearchProvider:
    """질의별로 다른 후보를 돌려주는 지역 검색 대역.

    되묻기 후보를 개별 재해석할 때(_filter_info_place_candidates) 처음 질의와
    후보 이름 재조회가 서로 다른 응답을 받아야 하는 테스트에 쓴다.
    """

    def __init__(self, responses: dict[str, tuple[LocalSearchPlace, ...]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def search_places_by_name(self, query: str, *, display: int = 5):
        self.calls.append(query)
        return provider_result(
            self._responses.get(query, ()), source=ProviderSource.FAKE_LOCAL_SEARCH
        )


def _ambiguous_landmark_places(
    first_name: str, second_name: str
) -> tuple[LocalSearchPlace, LocalSearchPlace]:
    """정확/첫토큰 어느 쪽으로도 안 좁혀지는 명소 카테고리 후보 2건.

    둘 다 종로구 안 좌표를 써 "지원 구 밖" 판정에 걸리지 않게 한다.
    """
    return (
        LocalSearchPlace(
            name=first_name,
            address="서울특별시 종로구",
            road_address=None,
            category="여행,명소>거리,골목",
            latitude=37.5788,
            longitude=126.9770,
        ),
        LocalSearchPlace(
            name=second_name,
            address="서울특별시 종로구",
            road_address=None,
            category="여행,명소>거리,골목",
            latitude=37.5789,
            longitude=126.9771,
        ),
    )


def _info_place_ambiguous_service(
    local_search: _QueryKeyedLocalSearchProvider,
    repository,
) -> ContextService:
    place_provider = FakePlaceProvider()
    return ContextService(
        ContextTools(
            location=ResolveLocationTool(
                FakeGeocodingProvider(),
                place_repository=repository,
                local_search_provider=local_search,
            ),
            places=NearbyPlaceDetailsTool(place_provider, place_provider),
            weather=GetWeatherForecastTool(FakeWeatherProvider()),
            holidays=GetHolidaysTool(FakeHolidayProvider()),
            concentration=GetConcentrationTool(FakeConcentrationProvider()),
        ),
        candidate_limit=10,
        clock=lambda: datetime.now(KST),
    )


@pytest.mark.asyncio
async def test_info_place_ambiguous_surfaces_candidate_names_as_clarification_candidates() -> None:
    """INFO도 지역 검색이 실제로 찾은 후보 이름을 되묻기 candidates로 보여준다.

    예전엔 candidates=[]로 항상 버려졌다(실측, 2026-08-27) — 이 테스트는 그
    회귀를 잠근다. 두 후보 다 저장소에 있어(시설 상세 질문의 place_id 기준)
    걸러지지 않고 둘 다 남는다.
    """
    repository = _CountingPlaceLocationRepository(("정자역", "정자동카페거리"))
    local_search = _QueryKeyedLocalSearchProvider(
        {"정자": _ambiguous_landmark_places("정자역", "정자동카페거리")}
    )
    service = _info_place_ambiguous_service(local_search, repository)

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="place-ambiguous-surfaces-candidates",
            place_name="정자",
            place_context="explicit",
            question_type="parking",
            specific_question="정자 주차장 정보",
        )
    )

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.code == "place_ambiguous"
    assert set(response.clarification.candidates) == {"정자역", "정자동카페거리"}


@pytest.mark.asyncio
async def test_info_place_ambiguous_filters_candidates_by_concentration_availability() -> None:
    """혼잡도 예측 질문은 집중률 매핑(concentration_name)이 있는 후보만 남는다."""
    repository = _CountingPlaceLocationRepository(("정자역",))
    local_search = _QueryKeyedLocalSearchProvider(
        {
            "정자": _ambiguous_landmark_places("정자역", "정자동카페거리"),
            # "정자동카페거리"는 저장소에 없어 재조회가 지역 검색으로 폴백한다 —
            # 자기 자신으로 유일하게 다시 잡혀야 SUCCESS(LOCAL_SEARCH, 집중률
            # 매핑 없음)가 되어 "확실히 실패"로 걸러진다.
            "정자동카페거리": (
                LocalSearchPlace(
                    name="정자동카페거리",
                    address="서울특별시 종로구",
                    road_address=None,
                    category="여행,명소>거리,골목",
                    latitude=37.5789,
                    longitude=126.9771,
                ),
            ),
        }
    )
    service = _info_place_ambiguous_service(local_search, repository)

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="place-ambiguous-concentration-filter",
            place_name="정자",
            place_context="explicit",
            question_type="concentration",
            specific_question="정자 내일 혼잡해?",
            visit_time=(datetime.now(KST) + timedelta(days=1)).date().isoformat(),
        )
    )

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert response.clarification.candidates == ["정자역"]


@pytest.mark.asyncio
async def test_info_place_ambiguous_falls_back_to_unfiltered_when_filter_empties_list() -> None:
    """필터링으로 후보가 0건이 되면 거르기 전 원본 목록을 그대로 보여준다."""
    repository = _CountingPlaceLocationRepository(())  # 둘 다 저장소에 없음
    local_search = _QueryKeyedLocalSearchProvider(
        {
            "정자": _ambiguous_landmark_places("정자역", "정자동카페거리"),
            "정자역": (
                LocalSearchPlace(
                    name="정자역",
                    address="서울특별시 종로구",
                    road_address=None,
                    category="여행,명소>거리,골목",
                    latitude=37.5788,
                    longitude=126.9770,
                ),
            ),
            "정자동카페거리": (
                LocalSearchPlace(
                    name="정자동카페거리",
                    address="서울특별시 종로구",
                    road_address=None,
                    category="여행,명소>거리,골목",
                    latitude=37.5789,
                    longitude=126.9771,
                ),
            ),
        }
    )
    service = _info_place_ambiguous_service(local_search, repository)

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="place-ambiguous-empty-filter-fallback",
            place_name="정자",
            place_context="explicit",
            question_type="parking",
            specific_question="정자 주차장 정보",
        )
    )

    # 시설 상세 질문 기준(place_id 있음)을 둘 다 못 만족하지만(저장소에 없음),
    # 버튼 없는 것보다 나으므로 원본 두 후보가 그대로 나온다.
    assert response.status == "needs_clarification"
    assert response.clarification is not None
    assert set(response.clarification.candidates) == {"정자역", "정자동카페거리"}


@pytest.mark.asyncio
async def test_info_place_ambiguous_keeps_candidate_when_re_resolve_is_itself_ambiguous() -> None:
    """재해석 자체가 또 애매하면(예: DB에 동명 타이틀 2건) 조회 불가로 버리지 않는다."""

    class _DuplicateTitleRepository:
        async def find_active_places_by_name(self, name: str):
            if name != "정자역":
                return ()
            return (
                StoredPlaceLocation(
                    content_id="1",
                    title="정자역",
                    address="서울특별시 종로구 1",
                    latitude=37.5788,
                    longitude=126.9770,
                    district_code="110",
                    concentration_name="정자역 1",
                ),
                StoredPlaceLocation(
                    content_id="2",
                    title="정자역",
                    address="서울특별시 종로구 2",
                    latitude=37.5789,
                    longitude=126.9771,
                    district_code="110",
                    concentration_name="정자역 2",
                ),
            )

    local_search = _QueryKeyedLocalSearchProvider(
        {
            "정자": _ambiguous_landmark_places("정자역", "정자동카페거리"),
            "정자동카페거리": (
                LocalSearchPlace(
                    name="정자동카페거리",
                    address="서울특별시 종로구",
                    road_address=None,
                    category="여행,명소>거리,골목",
                    latitude=37.5789,
                    longitude=126.9771,
                ),
            ),
        }
    )
    service = _info_place_ambiguous_service(local_search, _DuplicateTitleRepository())

    response = await service.fetch_info_context(
        InfoContextRequest(
            request_id="place-ambiguous-reresolve-still-ambiguous",
            place_name="정자",
            place_context="explicit",
            question_type="parking",
            specific_question="정자 주차장 정보",
        )
    )

    assert response.status == "needs_clarification"
    assert response.clarification is not None
    # "정자역"은 재해석도 애매하지만(동명 타이틀 2건) 조회 불가로 단정하지 않고
    # 후보로 유지된다. "정자동카페거리"는 저장소에 없어(place_id 없음) 걸러진다.
    assert response.clarification.candidates == ["정자역"]


class _CountingWeatherProvider:
    """날씨 조회가 실제로 호출됐는지 세는 더블."""

    def __init__(self) -> None:
        self._delegate = FakeWeatherProvider()
        self.calls = 0

    async def get_forecast_slots(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return await self._delegate.get_forecast_slots(*args, **kwargs)


@pytest.mark.asyncio
async def test_resolved_search_center_skips_location_and_weather() -> None:
    """보충 조회는 장소만 다시 받는다.

    A가 첫 조회에서 확정한 기준점을 넘기면 위치 해석·날씨·공휴일을 건너뛴다.
    그 셋의 결과는 보충 배치에서 어차피 버려지므로(A가 첫 배치 값을 그대로 쓴다)
    계산하지 않는 것뿐이고, 후보는 그대로 와야 한다.

    실측으로 보충 1회의 외부 호출이 7건에서 2건으로 준다.
    """

    geocoding = _CountingGeocodingProvider()
    weather = _CountingWeatherProvider()
    service = _service(weather, geocoding_provider=geocoding)

    response = await service.fetch_context(
        _request(resolved_search_center=Coordinates(latitude=37.5796, longitude=126.9770))
    )

    assert geocoding.queries == []
    assert weather.calls == 0
    assert response.context is not None
    assert response.context.weather is None
    assert response.context.holidays is None
    # 장소는 그대로 와야 한다 — 건너뛴 것은 쓰이지 않는 값뿐이다.
    assert response.context.places is not None
    assert response.context.places.data


@pytest.mark.asyncio
async def test_refill_still_resolves_the_user_location() -> None:
    """보충 조회도 사용자 위치는 채운다.

    이 배치의 `location`(검색 기준점)은 A가 병합에서 버리지만 사용자 위치는 버리지
    않는다 — 거리를 재는 기준점이기 때문이다(domain/ranking_origin.py). 전에는 둘을
    함께 껐고, 그래서 첫 배치는 사용자 위치에서, 보충 배치는 검색 기준점에서 잰 거리가
    한 카드 묶음에 섞였다. GPS를 강남에 두고 "강서구 갈만한곳"을 물으면 같은 응답에서
    가막골이 0.21km(강서구청 기준), 황금내근린공원이 16.07km(강남 기준)로 나왔다.

    기기 GPS는 좌표를 그대로 쓰므로 **외부 호출이 늘지 않는다.** 위 테스트가 지키는
    "보충은 장소만 다시 받는다"와 어긋나지 않는다.
    """

    geocoding = _CountingGeocodingProvider()
    weather = _CountingWeatherProvider()
    service = _service(weather, geocoding_provider=geocoding)

    response = await service.fetch_context(
        _request(
            gps_location=Coordinates(latitude=37.4979, longitude=127.0276),
            resolved_search_center=Coordinates(latitude=37.5796, longitude=126.9770),
        )
    )

    assert geocoding.queries == []
    assert weather.calls == 0
    assert response.context is not None
    assert response.context.user_location is not None
    assert response.context.user_location.data is not None
    assert response.context.user_location.data.location.latitude == 37.4979


@pytest.mark.asyncio
async def test_without_resolved_search_center_full_context_is_collected() -> None:
    """기준점을 안 넘기면 예전대로 전부 모은다(첫 조회 경로).

    위 테스트의 대조군이다. 지오코딩 호출로 대조하지 않는 이유는 종로구 랜드마크가
    저장소에서 먼저 풀려(D-097) 지오코딩까지 가지 않기 때문이다 — 위치가 해석됐다는
    것은 날씨·공휴일이 함께 모였다는 것으로 확인한다.
    """

    weather = _CountingWeatherProvider()
    service = _service(weather)

    response = await service.fetch_context(_request())

    assert weather.calls == 1
    assert response.context is not None
    assert response.context.location is not None
    assert response.context.weather is not None
    assert response.context.holidays is not None

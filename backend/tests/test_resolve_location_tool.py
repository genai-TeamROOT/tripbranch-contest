from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.domain.models import GeocodeResult, LocalSearchPlace, StoredPlaceLocation
from app.errors import AppError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.tools.resolve_location import (
    LocationPurpose,
    ResolutionConfidence,
    ResolutionMethod,
    ResolveLocationQuery,
    ResolveLocationStatus,
    ResolveLocationTool,
    is_address_query,
    strip_location_modifiers,
)


class SequenceGeocodingProvider:
    def __init__(self, responses: Iterable[GeocodeResult | AppError]) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[str, bool]] = []

    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        self.calls.append((location_query, use_alias))
        response = next(self._responses)
        if isinstance(response, AppError):
            raise response
        return provider_result(response, source=ProviderSource.FAKE_GEOCODING)


def _result(
    *,
    query: str = "서울특별시 종로구 사직로 161",
    district: str | None = "종로구",
    count: int = 1,
    labels: tuple[str, ...] = (),
) -> GeocodeResult:
    return GeocodeResult(
        query=query,
        resolved_name=query,
        latitude=37.5788,
        longitude=126.9770,
        candidate_count=count,
        administrative_district=district,
        candidate_labels=labels,
    )


class MemoryLocalSearchProvider:
    def __init__(self, places: tuple[LocalSearchPlace, ...]) -> None:
        self._places = places
        self.calls: list[str] = []

    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        self.calls.append(query)
        return provider_result(self._places, source=ProviderSource.FAKE_LOCAL_SEARCH)


class QueryKeyedLocalSearchProvider:
    """질의 문자열별로 다른 결과를 돌려준다 — "역" 재검색처럼 원 질의와 재검색
    질의의 응답이 달라야 하는 테스트 전용(`MemoryLocalSearchProvider`는 질의와
    무관하게 항상 같은 결과라 이 시나리오를 표현하지 못한다)."""

    def __init__(self, places_by_query: dict[str, tuple[LocalSearchPlace, ...]]) -> None:
        self._places_by_query = places_by_query
        self.calls: list[str] = []

    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        self.calls.append(query)
        return provider_result(
            self._places_by_query.get(query, ()), source=ProviderSource.FAKE_LOCAL_SEARCH
        )


class MemoryPlaceLocationRepository:
    def __init__(self, matches: tuple[StoredPlaceLocation, ...]) -> None:
        self._matches = matches
        self.calls: list[str] = []

    async def find_active_places_by_name(self, name: str) -> tuple[StoredPlaceLocation, ...]:
        self.calls.append(name)
        return self._matches


@pytest.mark.asyncio
async def test_resolves_stored_tour_place_before_geocoding() -> None:
    repository = MemoryPlaceLocationRepository(
        (
            StoredPlaceLocation(
                content_id="128553",
                title="쌈지길",
                address="서울특별시 종로구 인사동길 44",
                latitude=37.5743062352,
                longitude=126.9848674428,
                district_code="110",
                concentration_name="쌈지길",
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(provider, repository).execute(ResolveLocationQuery("쌈지길"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.DATABASE
    assert result.location.place_id == "128553"
    assert result.location.concentration_name == "쌈지길"
    assert result.provider_metadata[0].source is ProviderSource.SUPABASE_PLACES
    assert repository.calls == ["쌈지길"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_resolves_local_search_place_when_database_has_no_match() -> None:
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="쌈지길",
                address="서울특별시 종로구 관훈동 38",
                road_address="서울특별시 종로구 인사동길 44",
                category="쇼핑",
                latitude=37.5743062352,
                longitude=126.9848674428,
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(
        provider,
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(ResolveLocationQuery("쌈지길"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.LOCAL_SEARCH
    assert result.location.address == "서울특별시 종로구 인사동길 44"
    assert result.warnings == ("local_search_used",)
    assert local_search.calls == ["쌈지길"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_local_search_result_outside_service_area_is_unsupported() -> None:
    """지원 지역 밖은 unsupported로 알린다(D-044).

    좌표만 얻고 지역을 확인하지 않으면 종로구로 고정된 장소 검색과 교집합이 0건이 되어
    "조건에 맞는 곳을 찾지 못했어요. 검색 범위를 넓혀볼까요?"가 나간다. 넓혀도 영영
    나오지 않는 안내라 사용자를 헛돌게 한다.
    """
    # D-107으로 서울 25개 구 전체가 지원 구가 돼, 서울 안에는 "밖" 예시가 더
    # 없다 — 서울 밖(경기 구리시)의 실제 좌표로 대신한다.
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="구리역",
                address="경기도 구리시 교문동",
                road_address="경기도 구리시 이문안로 8",
                category="지하철역",
                latitude=37.5991,
                longitude=127.1397,
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(
        provider,
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(ResolveLocationQuery("구리역"))

    assert result.status is ResolveLocationStatus.UNSUPPORTED
    assert result.location is None
    assert result.error is not None
    assert result.error.code == "unsupported_region"
    assert result.error.cause == "outside_supported_region"
    # 지역 문제를 확인했으면 지오코딩까지 갈 이유가 없다.
    assert provider.calls == []


@pytest.mark.asyncio
async def test_ambiguous_local_search_outside_area_reports_region_not_clarification() -> None:
    """후보를 못 좁혔어도 전부 지역 밖이면 되묻지 않는다.

    "부산 해운대"에 "종로구 안에서 어느 장소인지 알려주세요"라고 되물으면 안 된다.
    """
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="해운대해수욕장",
                address="부산광역시 해운대구 우동",
                road_address=None,
                category="해수욕장",
                latitude=35.1587,
                longitude=129.1604,
            ),
            LocalSearchPlace(
                name="해운대시장",
                address="부산광역시 해운대구 중동",
                road_address=None,
                category="시장",
                latitude=35.1631,
                longitude=129.1633,
            ),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]),
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(ResolveLocationQuery("해운대"))

    assert result.status is ResolveLocationStatus.UNSUPPORTED
    assert result.error is not None
    assert result.error.cause == "outside_supported_region"


@pytest.mark.asyncio
async def test_realtime_citydata_location_allows_outside_jongno() -> None:
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="용리단길 카페",
                address="서울특별시 용산구 한강대로",
                road_address=None,
                category="카페",
                latitude=37.5311,
                longitude=126.9715,
            ),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]),
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(
        ResolveLocationQuery("용리단길 카페", purpose=LocationPurpose.REALTIME_CITYDATA)
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.latitude == 37.5311


@pytest.mark.asyncio
async def test_place_identity_with_area_override_checks_database_first() -> None:
    """TP-171: enforce_service_area를 꺼도 PLACE_IDENTITY는 여전히 저장소를 먼저 본다.

    지역 제한을 끄는 것과 저장소 조회를 건너뛰는 것은 서로 다른 결정이다 —
    override는 전자만 끈다.
    """
    repository = MemoryPlaceLocationRepository(
        (
            StoredPlaceLocation(
                content_id="999001",
                title="명동성당",
                address="서울특별시 중구 명동길 74",
                latitude=37.5633,
                longitude=126.9873,
                district_code="140",
                concentration_name="명동성당",
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(provider, repository).execute(
        ResolveLocationQuery(
            "명동성당", purpose=LocationPurpose.PLACE_IDENTITY, enforce_service_area=False
        )
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.DATABASE
    assert result.location.district_code == "140"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_place_identity_with_area_override_false_allows_outside_service_area() -> None:
    """TP-171: 저장소 미스가 지역 검색으로 넘어가도, 끈 지역 제한은 그대로 유지된다.

    D-107(서울 25개 구 전체 지원)으로 강남역은 더 이상 "지원 구 밖" 예시가 아니다
    — 서울 밖의 실시간 인구 허브(구리역)로 대신한다. DB엔 없고(지하철역이라
    TourAPI 코퍼스 밖), 지역 검색으로 풀리는데 지원 구 밖이다.
    """
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="구리역",
                address="경기도 구리시 교문동",
                road_address=None,
                category="지하철역",
                latitude=37.5991,
                longitude=127.1397,
            ),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]),
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(
        ResolveLocationQuery(
            "구리역", purpose=LocationPurpose.PLACE_IDENTITY, enforce_service_area=False
        )
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.LOCAL_SEARCH


@pytest.mark.asyncio
async def test_place_identity_without_override_still_enforces_service_area() -> None:
    """override를 안 주면 오늘과 완전히 같다 — PLACE_IDENTITY는 계속 지역을 제한한다.

    D-107으로 서울 25개 구 전체가 지원 구가 돼, 서울 안에는 "밖" 예시가 더
    없다 — 서울 밖(경기 구리시)의 실제 좌표로 대신한다.
    """
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="구리역",
                address="경기도 구리시 교문동",
                road_address=None,
                category="지하철역",
                latitude=37.5991,
                longitude=127.1397,
            ),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]),
        MemoryPlaceLocationRepository(()),
        local_search,
    ).execute(ResolveLocationQuery("구리역", purpose=LocationPurpose.PLACE_IDENTITY))

    assert result.status is ResolveLocationStatus.UNSUPPORTED
    assert result.error is not None
    assert result.error.cause == "outside_supported_region"


class _NamedPlaceLocationRepository:
    """조회한 이름에 따라 다른 결과를 주는 저장소 대역."""

    def __init__(self, by_name: dict[str, tuple[StoredPlaceLocation, ...]]) -> None:
        self._by_name = by_name
        self.calls: list[str] = []

    async def find_active_places_by_name(self, name: str) -> tuple[StoredPlaceLocation, ...]:
        self.calls.append(name)
        return self._by_name.get(name, ())


@pytest.mark.asyncio
async def test_place_identity_second_database_reattempt_ignores_ambiguous_duplicates() -> None:
    """TP-171: 지역 검색 이름 재조회가 동명이인이어도 지역 검색 결과를 그대로 쓴다.

    오늘 혼잡 질문이 PLACE_IDENTITY로 풀리면서 새로 열리는 경로다 — 예전엔
    REALTIME_CITYDATA가 저장소를 통째로 건너뛰어 이 재조회 자체가 없었다. 코드
    주석대로("재조회가 실패해도 지역 검색 결과는 그대로 쓴다") 동명이인으로
    재조회가 실패해도 사용자에게 새로 되묻기가 뜨면 안 된다.
    """
    repository = _NamedPlaceLocationRepository(
        {
            "강남타워 빌딩": (
                StoredPlaceLocation(
                    content_id="1",
                    title="강남타워 빌딩",
                    address="서울특별시 강남구 테헤란로 1",
                    latitude=37.50,
                    longitude=127.03,
                ),
                StoredPlaceLocation(
                    content_id="2",
                    title="강남타워 빌딩",
                    address="서울특별시 강남구 테헤란로 2",
                    latitude=37.51,
                    longitude=127.04,
                ),
            )
        }
    )
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="강남타워 빌딩",
                address="서울특별시 강남구 테헤란로 1",
                road_address=None,
                category="빌딩",
                latitude=37.50,
                longitude=127.03,
            ),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), repository, local_search
    ).execute(
        ResolveLocationQuery(
            "강남타워", purpose=LocationPurpose.PLACE_IDENTITY, enforce_service_area=False
        )
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.LOCAL_SEARCH
    assert repository.calls == ["강남타워", "강남타워 빌딩"]


@pytest.mark.asyncio
async def test_place_name_uses_local_search_before_geocoding() -> None:
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="쌈지길",
                address="서울특별시 종로구 관훈동 38",
                road_address="서울특별시 종로구 인사동길 44",
                category="쇼핑",
                latitude=37.5743062352,
                longitude=126.9848674428,
            ),
        )
    )
    repository = MemoryPlaceLocationRepository(())
    geocoding = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(geocoding, repository, local_search).execute(
        ResolveLocationQuery("쌈지길")
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.LOCAL_SEARCH
    assert repository.calls == ["쌈지길"]
    assert local_search.calls == ["쌈지길"]
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_address_uses_geocoding_without_place_name_lookups() -> None:
    repository = MemoryPlaceLocationRepository(())
    local_search = MemoryLocalSearchProvider(())
    geocoding = SequenceGeocodingProvider([_result(query="서울특별시 종로구 인사동길 44")])

    result = await ResolveLocationTool(geocoding, repository, local_search).execute(
        ResolveLocationQuery("서울특별시 종로구 인사동길 44")
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.DIRECT
    assert repository.calls == []
    assert local_search.calls == []
    assert geocoding.calls == [("서울특별시 종로구 인사동길 44", False)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("쌈지길", False),
        ("서울특별시 종로구 인사동길 44", True),
        ("서울특별시 종로구 관훈동 38", True),
    ],
)
def test_detects_address_query_conservatively(query: str, expected: bool) -> None:
    assert is_address_query(query) is expected


@pytest.mark.asyncio
async def test_resolves_alias_in_jongno() -> None:
    provider = SequenceGeocodingProvider([_result()])
    tool = ResolveLocationTool(provider)

    result = await tool.execute(ResolveLocationQuery("경복궁"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.ALIAS
    assert result.location.confidence is ResolutionConfidence.EXACT
    assert result.location.provider_query == "서울특별시 종로구 사직로 161"
    assert result.provider_metadata[0].source is ProviderSource.FAKE_GEOCODING
    assert provider.calls == [("서울특별시 종로구 사직로 161", False)]


@pytest.mark.asyncio
async def test_falls_back_to_original_only_after_alias_no_data() -> None:
    provider = SequenceGeocodingProvider(
        [
            AppError(code="location_not_found", message="없음", status_code=404),
            _result(query="서울특별시 종로구 경복궁"),
        ]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("경복궁"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolution_method is ResolutionMethod.FALLBACK
    assert result.warnings == ("fallback_used",)
    assert len(result.provider_metadata) == 1
    assert provider.calls == [
        ("서울특별시 종로구 사직로 161", False),
        ("경복궁", False),
    ]


@pytest.mark.asyncio
async def test_does_not_fallback_after_provider_failure() -> None:
    provider = SequenceGeocodingProvider(
        [
            AppError(
                code="geocoding_unavailable",
                message="장애",
                status_code=502,
                retryable=True,
            )
        ]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("경복궁"))

    assert result.status is ResolveLocationStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "unavailable"
    assert result.error.retryable is True
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_resolves_search_center_outside_jongno_before_candidate_filtering() -> None:
    provider = SequenceGeocodingProvider(
        [_result(query="서울특별시 용산구 한강대로", district="용산구")]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("서울역"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "서울특별시 용산구 한강대로"
    assert result.provider_metadata[0].source is ProviderSource.FAKE_GEOCODING
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None


@pytest.mark.asyncio
async def test_ambiguous_location_requires_clarification() -> None:
    provider = SequenceGeocodingProvider([_result(count=3)])

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("인사동"))

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"
    assert result.error.details["reason"] == "ambiguous_location"
    assert result.provider_metadata[0].source is ProviderSource.FAKE_GEOCODING
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None


@pytest.mark.asyncio
async def test_indistinguishable_candidates_do_not_ask_again() -> None:
    """이름표가 전부 같으면 되묻지 않는다.

    되묻기의 목적은 고르게 하는 것인데, 선택지가 서로 구분되지 않으면 어느 버튼을 눌러도
    같은 문자열이 다시 들어가 같은 되묻기로 돌아온다 — 사용자가 빠져나갈 길이 없다.

    "강서구"가 그랬다(2026-09-09). 네이버 지오코딩이 부산 강서구까지 2건을 주는데
    이름표는 둘 다 "강서구"라 선택지가 하나로 합쳐졌고, 그 하나를 눌러도 제자리였다.
    """

    provider = SequenceGeocodingProvider(
        [_result(query="서울특별시 강서구", count=2, labels=("강서구", "강서구"))]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("강서구"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "서울특별시 강서구"


@pytest.mark.asyncio
async def test_distinct_candidates_still_ask() -> None:
    """이름표가 서로 다르면 고르는 행위에 뜻이 있으므로 지금처럼 되묻는다.

    "익선동"은 종로구와 창원시 진해구에 둘 다 있다(2026-09-09 실측).
    """

    provider = SequenceGeocodingProvider(
        [_result(count=2, labels=("종로구 익선동", "창원시 진해구 익선동"))]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("익선동"))

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"
    assert "종로구 익선동" in result.error.details["candidate_names"]
    assert "창원시 진해구 익선동" in result.error.details["candidate_names"]


@pytest.mark.asyncio
async def test_duplicate_labels_are_shown_once() -> None:
    """같은 이름표가 여러 번 와도 선택지에는 한 번만 싣는다.

    같은 글자를 두 번 보여주면 사용자는 둘이 다른 곳이라 여기고 고르는데, 어느 쪽을
    골라도 결과가 같다.
    """

    provider = SequenceGeocodingProvider(
        [_result(count=3, labels=("종로구 익선동", "창원시 진해구 익선동", "종로구 익선동"))]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("익선동"))

    assert result.error is not None
    names = result.error.details["candidate_names"]
    assert names.count("종로구 익선동") == 1


@pytest.mark.asyncio
async def test_unknown_location_is_no_data() -> None:
    provider = SequenceGeocodingProvider(
        [AppError(code="location_not_found", message="없음", status_code=404)]
    )

    result = await ResolveLocationTool(provider).execute(ResolveLocationQuery("알 수 없는 장소"))

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "location_not_found"


@pytest.mark.parametrize("value", ["", " ", "가" * 201])
def test_validates_query(value: str) -> None:
    with pytest.raises(ValueError):
        ResolveLocationQuery(value)


def _local_place(
    name: str,
    *,
    category: str = "음식점>한식",
    latitude: float = 37.5743,
    longitude: float = 126.9848,
) -> LocalSearchPlace:
    return LocalSearchPlace(
        name=name,
        address="서울특별시 종로구 관훈동 38",
        road_address="서울특별시 종로구 인사동길 44",
        category=category,
        latitude=latitude,
        longitude=longitude,
    )


async def _resolve_with_local_search(
    places: tuple[LocalSearchPlace, ...],
    query: str,
    *,
    geocoding_responses: Iterable[GeocodeResult | AppError] = (),
):
    local_search = MemoryLocalSearchProvider(places)
    geocoding = SequenceGeocodingProvider(geocoding_responses)
    result = await ResolveLocationTool(
        geocoding, MemoryPlaceLocationRepository(()), local_search
    ).execute(ResolveLocationQuery(query))
    return result, geocoding


@pytest.mark.asyncio
async def test_local_search_selects_head_token_match_among_nearby_shops() -> None:
    """Local Search는 주변 상호까지 함께 준다. "안국역 3호선"만 역으로 인정한다.

    실측(2026-08-03): "안국역" 조회 시 역 1건 + 주변 음식점 4건이 반환됐다.
    """
    result, geocoding = await _resolve_with_local_search(
        (
            _local_place("안국역 3호선", category="교통,운수>지하철,전철"),
            _local_place("쿄와우동", category="음식점>일식>우동,소바"),
            _local_place("익선끝집", category="한식>육류,고기요리"),
        ),
        "안국역",
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "안국역 3호선"
    assert result.location.resolution_method is ResolutionMethod.LOCAL_SEARCH
    # 이름으로 확정했으므로 Geocoding fallback까지 가지 않는다.
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_local_search_does_not_pick_similar_prefix_name() -> None:
    """"안국역사거리"는 "안국역"과 다른 장소다 — startswith였다면 잘못 선택된다.

    정확 일치도, 역/명소 후보도 없으니 Geocoding으로 넘어간다(성수동 폴백과 같은
    경로). "안국역"은 상호명이라 Geocoding이 인식하지 못하므로(docs/api-samples.md)
    결국 location_not_found로 끝난다 — "여러 곳으로 해석돼요"보다 정확한 결과다.
    """
    result, geocoding = await _resolve_with_local_search(
        (_local_place("안국역사거리"),),
        "안국역",
        geocoding_responses=[
            AppError(code="location_not_found", message="위치를 찾을 수 없어요.")
        ],
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "location_not_found"
    assert geocoding.calls != []


@pytest.mark.asyncio
async def test_local_search_selects_edit_distance_match_for_landmark_candidate() -> None:
    """"성수 카페거리"(질의)와 "성수동카페거리"(실제 이름)는 "동" 한 글자 차이다.

    정확 일치도 첫토큰 일치도 안 되지만(공백 제거해도 "성수카페거리"≠
    "성수동카페거리"), 편집거리 1에 역/명소 카테고리라 채택된다(실측, 2026-08-27).
    """
    result, geocoding = await _resolve_with_local_search(
        (_local_place("성수동카페거리", category="여행,명소>거리,골목"),),
        "성수 카페거리",
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "성수동카페거리"
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_local_search_edit_distance_tier_ignores_non_pickable_candidates() -> None:
    """같은 한 글자 차이여도 역/명소가 아니면(식당 등) 편집거리 단계를 안 탄다.

    부분 일치를 상호명까지 넓히지 않는다는 파일 전체 원칙을 지킨다.
    """
    result, geocoding = await _resolve_with_local_search(
        (_local_place("성수동카페거리", category="음식점>카페"),),
        "성수 카페거리",
        geocoding_responses=[
            AppError(code="location_not_found", message="위치를 찾을 수 없어요.")
        ],
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert geocoding.calls != []


@pytest.mark.asyncio
async def test_local_search_edit_distance_tier_stays_ambiguous_with_two_near_matches() -> None:
    """편집거리 1인 후보가 둘이면 여전히 하나로 못 좁힌다."""
    result, _ = await _resolve_with_local_search(
        (
            _local_place("성수동카페거리", category="여행,명소>거리,골목"),
            _local_place("성수도카페거리", category="여행,명소>거리,골목"),
        ),
        "성수 카페거리",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"


@pytest.mark.asyncio
async def test_local_search_edit_distance_tier_skips_short_queries() -> None:
    """2글자 질의는 편집거리 단계를 아예 안 탄다 — "신촌"↔"신천"처럼 완전히 다른
    동네가 편집거리 1일 수 있다. 길이 가드가 없으면 "신천"이 "신촌"의 정답으로
    잘못 채택된다 — 대신 역/명소 후보가 있는 정상적인 되묻기로 남아야 한다.
    """
    result, geocoding = await _resolve_with_local_search(
        (_local_place("신천", category="여행,명소>거리,골목"),),
        "신촌",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"
    assert result.error.details["candidate_names"] == "신천"
    # 후보가 있으니(명소 카테고리) Geocoding까지 갈 이유가 없다.
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_local_search_groups_transit_candidates_of_one_station() -> None:
    """같은 역의 노선별 후보는 하나로 본다(D-045).

    지역 검색은 "종로3가역"에 1·3·5호선을 각각 돌려준다. 몇 호선인지 되물어도 카페를
    찾는 사용자에게는 답이 될 수 없고, 검색 반경이 2km라 어느 출입구를 골라도 결과가
    같다. 실측 최대 거리는 청량리역 381m다.
    """
    result, _ = await _resolve_with_local_search(
        (
            _local_place(
                "종로3가역 3호선",
                category="교통,운수>지하철,전철",
                latitude=37.5714,
                longitude=126.9920,
            ),
            _local_place(
                "종로3가역 1호선",
                category="교통,운수>지하철,전철",
                latitude=37.5703,
                longitude=126.9918,
            ),
            _local_place(
                "종로3가역 5호선",
                category="교통,운수>지하철,전철",
                latitude=37.5710,
                longitude=126.9945,
            ),
        ),
        "종로3가역",
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "종로3가역 3호선"


@pytest.mark.asyncio
async def test_local_search_asks_again_when_transit_candidates_are_far_apart() -> None:
    """교통 시설이어도 멀리 떨어져 있으면 같은 역이 아니다."""
    result, _ = await _resolve_with_local_search(
        (
            _local_place(
                "OO역 3호선",
                category="교통,운수>지하철,전철",
                latitude=37.5743,
                longitude=126.9848,
            ),
            _local_place(
                "OO역 1호선",
                category="교통,운수>지하철,전철",
                latitude=37.5900,
                longitude=126.9848,
            ),
        ),
        "OO역",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"


@pytest.mark.asyncio
async def test_local_search_does_not_group_when_a_shop_shares_the_name() -> None:
    """상호가 하나라도 섞이면 묶지 않는다.

    거리만 보면 "종각역 김밥천국"처럼 역명을 그대로 앞에 붙인 상호가 함께 묶여, 첫
    후보를 임의로 고르지 않는다는 원칙이 깨진다.
    """
    result, _ = await _resolve_with_local_search(
        (
            _local_place("종각역 1호선", category="교통,운수>지하철,전철"),
            _local_place("종각역 김밥천국", category="음식점>한식"),
        ),
        "종각역",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"
    # 되묻기 버튼용 후보 이름을 실제로 찾아낸 후보에서 채우되, 식당류는 위치 후보로
    # 부적절해 제외한다(docs/design/clarification-options.md 7절 확장, 실사용
    # 피드백 2026-08-13) — 지하철역만 남는다.
    assert result.error.details["candidate_names"] == "종각역 1호선"


@pytest.mark.asyncio
async def test_local_search_ambiguous_candidates_exclude_restaurants() -> None:
    """지역 검색이 식당·상점까지 함께 돌려줘도(주변 상호 포함), 되묻기 버튼은
    지하철역/명소류만 남긴다 — "종각" 검색에서 식당이 위치 후보로 뜨면 사용자가
    혼란스럽다는 실사용 피드백을 반영했다(2026-08-13). 넷 다 첫 토큰이 "종각"과
    정확히 안 맞아 전부 원본 후보 그대로 애매 판정으로 떨어진다(실사용 재현)."""
    result, _ = await _resolve_with_local_search(
        (
            _local_place("종각역 1호선", category="교통,운수>지하철,전철"),
            _local_place("종각타워", category="관광,명소"),
            _local_place("숙썽수산 종로본점", category="음식점>수산물"),
            _local_place("어망집", category="음식점>한식"),
        ),
        "종각",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.details["candidate_names"] == "종각역 1호선|종각타워"


@pytest.mark.asyncio
async def test_local_search_ambiguous_all_shops_falls_back_to_geocoding() -> None:
    """후보가 전부 식당·상점뿐이면(지하철역/명소가 하나도 없으면) 되묻지 않고
    Geocoding으로 넘어간다 — "성수동"처럼 동 이름이 지역 검색에서 카페·식당
    상호명으로만 잡히는 경우다(실측, 2026-08-26). Naver Geocoding은 행정동/법정동
    이름을 직접 인식하므로(docs/api-samples.md) 여기서 좌표로 풀린다."""
    result, geocoding = await _resolve_with_local_search(
        (
            _local_place("종각 스타벅스", category="음식점>카페"),
            _local_place("종각 노브랜드버거", category="음식점>패스트푸드"),
        ),
        "종각",
        geocoding_responses=[_result(query="종각")],
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert geocoding.calls != []


@pytest.mark.asyncio
async def test_local_search_ambiguous_all_shops_and_geocoding_fails_still_asks_again() -> None:
    """Geocoding도 실패하면(진짜 알아들을 수 없는 입력) 기존처럼 되묻는다 —
    다만 원인이 location_not_found라 "여러 곳으로 해석돼요"는 더는 안 나간다."""
    result, geocoding = await _resolve_with_local_search(
        (
            _local_place("종각 스타벅스", category="음식점>카페"),
            _local_place("종각 노브랜드버거", category="음식점>패스트푸드"),
        ),
        "종각",
        geocoding_responses=[
            AppError(code="location_not_found", message="위치를 찾을 수 없어요.")
        ],
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "location_not_found"
    assert geocoding.calls != []


@pytest.mark.asyncio
async def test_local_search_ambiguous_all_shops_and_geocoding_outside_service_area() -> None:
    """Geocoding이 지원 구 밖 좌표를 주면 되묻기가 아니라 unsupported_region이다
    — "부산 해운대"류 지역 밖 지명에 "지원 구 안 어디인지" 되묻지 않는다."""
    result, geocoding = await _resolve_with_local_search(
        (
            _local_place("해운대 스타벅스", category="음식점>카페"),
            _local_place("해운대 노브랜드버거", category="음식점>패스트푸드"),
        ),
        "해운대",
        geocoding_responses=[
            GeocodeResult(
                query="해운대",
                resolved_name="해운대",
                latitude=35.1587,
                longitude=129.1604,
                candidate_count=1,
                administrative_district="해운대구",
            )
        ],
    )

    assert result.status is ResolveLocationStatus.UNSUPPORTED
    assert result.error is not None
    assert result.error.cause == "outside_supported_region"
    assert geocoding.calls != []


@pytest.mark.asyncio
async def test_local_search_retries_station_abbreviation_with_station_suffix() -> None:
    """"교대"처럼 역 줄임말은 그대로 검색하면 동명 대학·상호에 밀려 역이 후보에
    아예 안 잡힌다(실측, 2026-09-09: 네이버 지역 검색 "교대" 상위 5건은
    서울교육대학교·식당뿐). "역"을 붙인 재검색으로 "교대역"을 찾아 채택한다.
    """
    provider = QueryKeyedLocalSearchProvider(
        {
            "교대": (
                _local_place("교대갈비집 시청점", category="한식>육류,고기요리"),
                _local_place("서울교육대학교", category="교육,학문>대학교"),
            ),
            "교대역": (
                _local_place("교대역 2호선", category="교통,운수>지하철,전철"),
                _local_place("교대역 3호선", category="교통,운수>지하철,전철"),
            ),
        }
    )
    geocoding = SequenceGeocodingProvider([])
    result = await ResolveLocationTool(
        geocoding, MemoryPlaceLocationRepository(()), provider
    ).execute(ResolveLocationQuery("교대"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name in ("교대역 2호선", "교대역 3호선")
    assert provider.calls == ["교대", "교대역"]
    # 이름으로 확정했으므로 Geocoding까지 가지 않는다.
    assert geocoding.calls == []


@pytest.mark.asyncio
async def test_local_search_station_retry_accepts_name_mismatch() -> None:
    """"홍대"+"역"="홍대역"의 실제 역명은 "홍대입구역"이라 이름이 다르다(실측,
    2026-09-09). 정확/첫토큰/편집거리 매칭은 다 실패하지만, "역"을 붙인
    재검색에서 교통 카테고리 후보가 단 하나면 이름이 달라도 채택한다.
    """
    provider = QueryKeyedLocalSearchProvider(
        {
            "홍대": (_local_place("홍익대학교 서울캠퍼스", category="교육,학문>대학교"),),
            "홍대역": (
                _local_place("홍대입구역 2호선", category="교통,운수>지하철,전철"),
                _local_place("삼평식당 홍대본점", category="한식>육류,고기요리"),
            ),
        }
    )
    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), MemoryPlaceLocationRepository(()), provider
    ).execute(ResolveLocationQuery("홍대"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "홍대입구역 2호선"


@pytest.mark.asyncio
async def test_local_search_station_retry_skips_when_query_already_ends_with_station() -> None:
    """이미 "역"으로 끝나는 질의는 다시 붙이지 않는다 — "안국역역"을 만들지 않는다.

    후보가 아예 없는 상태를 재현한다("안국역" 그대로 검색해도 0건). 접미사를
    또 붙였다면 "안국역역"으로 한 번 더 호출됐을 텐데, 그러지 않고 바로
    Geocoding으로 넘어간다.
    """
    provider = QueryKeyedLocalSearchProvider({})
    result = await ResolveLocationTool(
        SequenceGeocodingProvider(
            [AppError(code="location_not_found", message="위치를 찾을 수 없어요.")]
        ),
        MemoryPlaceLocationRepository(()),
        provider,
    ).execute(ResolveLocationQuery("안국역"))

    assert result.status is ResolveLocationStatus.NO_DATA
    # "안국역역"으로 재조회하지 않는다 — 원 질의 한 번만 호출됐다.
    assert provider.calls == ["안국역"]


@pytest.mark.asyncio
async def test_local_search_station_retry_stays_ambiguous_for_different_stations() -> None:
    """"역"을 붙인 재검색에서 서로 다른 역이 여럿 나오면 임의로 고르지 않고
    실패시켜 상위 호출부가 별칭/Geocoding 사다리로 넘기게 한다."""
    provider = QueryKeyedLocalSearchProvider(
        {
            "OO": (_local_place("OO식당", category="음식점>한식"),),
            "OO역": (
                _local_place(
                    "OO역 3호선",
                    category="교통,운수>지하철,전철",
                    latitude=37.5743,
                    longitude=126.9848,
                ),
                _local_place(
                    "XX역 1호선",
                    category="교통,운수>지하철,전철",
                    latitude=37.5900,
                    longitude=126.9848,
                ),
            ),
        }
    )
    result = await ResolveLocationTool(
        SequenceGeocodingProvider(
            [AppError(code="location_not_found", message="위치를 찾을 수 없어요.")]
        ),
        MemoryPlaceLocationRepository(()),
        provider,
    ).execute(ResolveLocationQuery("OO"))

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "location_not_found"


@pytest.mark.asyncio
async def test_local_search_asks_again_when_exact_name_duplicated() -> None:
    """정확 일치가 여러 건이면 첫 토큰으로도 못 좁히므로 바로 재질문한다."""
    result, _ = await _resolve_with_local_search(
        (_local_place("쌈지길"), _local_place("쌈지길")), "쌈지길"
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"


@pytest.mark.asyncio
async def test_local_search_prefers_exact_match_over_head_token() -> None:
    """정확 일치가 있으면 첫 토큰 단계로 내려가지 않는다."""
    result, _ = await _resolve_with_local_search(
        (_local_place("안국역 3호선"), _local_place("안국역")), "안국역"
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "안국역"


@pytest.mark.asyncio
async def test_local_search_without_candidates_falls_back_to_geocoding() -> None:
    """후보가 아예 없는 것과 좁히지 못한 것은 다르다.

    없으면 Geocoding이 받아야 하고(행정동 등), 있는데 못 좁혔으면 재질문한다.
    """
    local_search = MemoryLocalSearchProvider(())
    geocoding = SequenceGeocodingProvider([_result(query="청운효자동")])

    result = await ResolveLocationTool(
        geocoding, MemoryPlaceLocationRepository(()), local_search
    ).execute(ResolveLocationQuery("청운효자동"))

    assert result.status is ResolveLocationStatus.SUCCESS
    # 후보가 아예 없으면 "역"을 붙인 재검색을 한 번 더 시도한 뒤 Geocoding으로 넘어간다.
    assert local_search.calls == ["청운효자동", "청운효자동역"]
    assert geocoding.calls != []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("안국역 근처", "안국역"),
        ("경복궁 주변", "경복궁"),
        ("쌈지길 인근", "쌈지길"),
        ("서울역 부근", "서울역"),
        # 수식어만 있는 입력은 잘라낼 게 없어 원문을 유지한다.
        ("근처", "근처"),
        # 이름에 붙어 있으면 건드리지 않는다.
        ("역근처식당", "역근처식당"),
        ("근처식당 근처", "근처식당"),
    ],
)
def test_strip_location_modifiers(query: str, expected: str) -> None:
    assert strip_location_modifiers(query) == expected


@pytest.mark.asyncio
async def test_place_name_lookup_ignores_trailing_modifier() -> None:
    """A가 "안국역 근처"로 넘겨도 "안국역"으로 조회한다.

    실측(2026-08-03): "안국역 근처"로 지역 검색하면 엘리베이터·모텔·돈까스집이
    나와 정답인 "안국역 3호선"이 후보에 없었다.
    """
    local_search = MemoryLocalSearchProvider((_local_place("안국역 3호선"),))
    geocoding = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(
        geocoding, MemoryPlaceLocationRepository(()), local_search
    ).execute(ResolveLocationQuery("안국역 근처"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "안국역 3호선"
    # Provider에는 수식어를 뺀 값이 전달된다.
    assert local_search.calls == ["안국역"]


@pytest.mark.asyncio
async def test_address_with_modifier_is_still_treated_as_address() -> None:
    """"인사동길 44 근처"처럼 수식어가 붙은 주소도 Geocoding으로 보낸다."""
    local_search = MemoryLocalSearchProvider(())
    geocoding = SequenceGeocodingProvider([_result(query="서울특별시 종로구 인사동길 44")])

    result = await ResolveLocationTool(
        geocoding, MemoryPlaceLocationRepository(()), local_search
    ).execute(ResolveLocationQuery("서울특별시 종로구 인사동길 44 근처"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert local_search.calls == []
    assert geocoding.calls == [("서울특별시 종로구 인사동길 44", False)]


@pytest.mark.asyncio
async def test_search_center_resolves_from_repository_first() -> None:
    """검색 중심점도 저장소를 먼저 본다. 저장소에 있으면 지역 검색까지 가지 않는다.

    예전에는 건너뛰었다 — 코퍼스에 없는 이름(역명·상호·지명)은 조회가 반드시
    실패하는데 그 실패에 `places` 4회를 썼기 때문이다("안국역" 실측, cc3da0ed).
    필터를 or= 하나로 합쳐 그 실패가 제목 1회 + 별칭 1회로 줄면서 전제가 바뀌었고,
    지원 지역이 네 구로 늘며 "명동성당 근처"처럼 저장소에 있는 장소를 검색 중심으로
    쓰는 요청이 실제로 들어온다. 지역 검색은 그런 이름을 못 좁혀 되묻기로 끝난다.
    """
    repository = MemoryPlaceLocationRepository(
        (
            StoredPlaceLocation(
                content_id="128553",
                title="쌈지길",
                address="서울특별시 종로구 인사동길 44",
                latitude=37.5743062352,
                longitude=126.9848674428,
                district_code="110",
                concentration_name="쌈지길",
            ),
        )
    )
    local_search = MemoryLocalSearchProvider(
        (
            LocalSearchPlace(
                name="쌈지길",
                address="서울특별시 종로구 관훈동 38",
                road_address="서울특별시 종로구 인사동길 44",
                category="쇼핑",
                latitude=37.5743062352,
                longitude=126.9848674428,
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(provider, repository, local_search).execute(
        ResolveLocationQuery("쌈지길", purpose=LocationPurpose.SEARCH_CENTER)
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    # 저장소에 정확히 같은 이름이 있으므로 거기서 끝난다 — 지역 검색을 부르지 않는다.
    assert repository.calls == ["쌈지길"]
    assert local_search.calls == []
    assert result.location.resolution_method is ResolutionMethod.DATABASE


@pytest.mark.asyncio
async def test_search_center_does_not_requery_repository_after_local_search() -> None:
    """지역 검색이 다른 이름을 줘도 재조회하지 않는다.

    재조회는 집중률 매핑을 붙이기 위한 것인데, 검색 중심점은 그 필드를 쓰지 않는다.
    지역 검색 앞의 첫 조회는 저장소에 없어 빈손으로 끝나고, 그 뒤로는 다시 묻지
    않는다 — 조회가 정확히 한 번이어야 한다.
    """
    repository = MemoryPlaceLocationRepository(())
    local_search = MemoryLocalSearchProvider(
        (
            # 첫 토큰("북촌")이 질의와 같아야 셀렉터가 유일 후보로 고른다(D-045).
            LocalSearchPlace(
                name="북촌 한옥마을",
                address="서울특별시 종로구 계동길 37",
                road_address="서울특별시 종로구 계동길 37",
                category="관광,명소",
                latitude=37.5826,
                longitude=126.9831,
            ),
        )
    )
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(provider, repository, local_search).execute(
        ResolveLocationQuery("북촌", purpose=LocationPurpose.SEARCH_CENTER)
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    # 지역 검색 앞에서 한 번만 묻는다. 지역 검색이 알아낸 "북촌 한옥마을"로 다시
    # 묻지 않는다.
    assert repository.calls == ["북촌"]
    assert result.location is not None
    assert result.location.concentration_name is None


@pytest.mark.asyncio
async def test_place_identity_keeps_repository_first() -> None:
    """INFO 혼잡도는 D-043 그대로 — 저장소 정체성 확정이 먼저다."""
    repository = MemoryPlaceLocationRepository(
        (
            StoredPlaceLocation(
                content_id="128553",
                title="쌈지길",
                address="서울특별시 종로구 인사동길 44",
                latitude=37.5743062352,
                longitude=126.9848674428,
                district_code="110",
                concentration_name="쌈지길",
            ),
        )
    )
    local_search = MemoryLocalSearchProvider(())
    provider = SequenceGeocodingProvider([])

    result = await ResolveLocationTool(provider, repository, local_search).execute(
        ResolveLocationQuery("쌈지길", purpose=LocationPurpose.PLACE_IDENTITY)
    )

    assert repository.calls == ["쌈지길"]
    assert local_search.calls == []
    assert result.location is not None
    assert result.location.concentration_name == "쌈지길"


@pytest.mark.asyncio
async def test_purpose_defaults_to_place_identity() -> None:
    """인자를 안 주면 기존 동작 그대로다 — 기존 호출부가 바뀌지 않는다."""
    assert (
        ResolveLocationQuery("쌈지길").purpose is LocationPurpose.PLACE_IDENTITY
    )


@pytest.mark.asyncio
async def test_search_center_returns_no_data_without_falling_back_to_repository() -> None:
    """지역 검색·지오코딩이 모두 못 찾으면 저장소로 되돌아가지 않고 no_data로 끝낸다.

    첫 조회에서 저장소가 빈손이었다면 그 뒤로 다시 묻지 않는다. 여기서 되돌아가면
    같은 질의를 두 번 던지는 것일 뿐이라 결과가 바뀌지 않는다. 위치를 못 찾았다는
    사실을 그대로 돌려주어 상위에서 사용자에게 구체적인 위치를 되묻게 한다.
    """
    # 어느 단계에서도 찾히지 않는 이름이다.
    repository = MemoryPlaceLocationRepository(())
    local_search = MemoryLocalSearchProvider(())
    provider = SequenceGeocodingProvider(
        [AppError(code="no_data", message="찾지 못했어요.", status_code=404)]
    )

    result = await ResolveLocationTool(provider, repository, local_search).execute(
        ResolveLocationQuery("없는역", purpose=LocationPurpose.SEARCH_CENTER)
    )

    assert result.status is not ResolveLocationStatus.SUCCESS
    # 앞에서 한 번 묻고 끝이다. 지역 검색·지오코딩이 실패했다고 다시 묻지 않는다.
    assert repository.calls == ["없는역"]


# --- 끝나지 않던 되묻기 (2026-08-27 실사용) ---------------------------------
#
# "운현궁 주차장 있어?"는 답이 나오는데, 이어진 "근처 공영 주차장 자리 있어?"가
# `여러 장소 중 어느 곳을 말씀하시는 건가요?`로 끝나고, "운현궁"이라고 답하면 같은
# 되묻기가 다시 나오는 것이 무한히 반복됐다.
#
# 아래 후보 구성은 실측값이다 — 네이버 지역검색이 "운현궁"에 이름이 완전히 같은 후보를
# 3건(중식당·궁궐·한식당) 돌려준다.


def _unhyeongung_candidates() -> tuple[LocalSearchPlace, ...]:
    return (
        _local_place("운현궁한방삼계탕", category="음식점>한식"),
        _local_place("운현궁식당", category="한식>한정식"),
        _local_place("운현궁", category="중식>중식당"),
        _local_place("운현궁", category="여행,명소>궁궐"),
        _local_place("운현궁", category="음식점>한식"),
    )


@pytest.mark.asyncio
async def test_same_name_candidates_resolve_to_the_only_usable_location() -> None:
    """이름이 같아도 위치 후보로 쓸 수 있는 것이 하나면 그것으로 정한다.

    식당·상점은 위치 후보로 쓰지 않는다는 규칙이 이미 있었지만 되묻기 목록을 만들 때만
    쓰였다. 고르는 단계에서도 보지 않으면 "못 좁혔다"로 끝나, 궁궐 하나가 정답인데도
    되묻게 된다.
    """
    result, _ = await _resolve_with_local_search(_unhyeongung_candidates(), "운현궁")

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "운현궁"


@pytest.mark.asyncio
async def test_clarification_is_not_raised_when_the_answer_would_repeat_the_question() -> None:
    """되묻기의 답이 질문과 같은 문자열이면 물어봐야 소용이 없다.

    후보 이름이 질의와 같으면, 사용자가 그 버튼을 눌러도 같은 조회가 다시 돌아 같은
    되묻기가 나온다 — 입력이 하나도 바뀌지 않아 영영 끝나지 않는다.
    """
    result, _ = await _resolve_with_local_search(
        (
            _local_place("운현궁", category="여행,명소>궁궐"),
            _local_place("운현궁", category="음식점>한식"),
            _local_place("운현궁", category="중식>중식당"),
        ),
        "운현궁",
    )

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "운현궁"


@pytest.mark.asyncio
async def test_still_asks_when_two_real_locations_share_the_name() -> None:
    """걸러낸 뒤에도 둘 이상 남으면 그때는 진짜로 못 고른다 — 그대로 되묻는다.

    이 경우의 되묻기는 여전히 답이 질문과 같아 반복될 수 있다. 이름이 같은 서로 다른
    장소를 어떻게 가를지는 별개 문제이고, 이 테스트는 그 상황에서 임의로 한 곳을 고르지
    않는다는 것만 잠근다.
    """
    result, _ = await _resolve_with_local_search(
        (
            _local_place("운현궁", category="여행,명소>궁궐"),
            _local_place("운현궁", category="여행,명소>고궁,종교유적"),
        ),
        "운현궁",
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"


@pytest.mark.asyncio
async def test_지오코딩_후보가_여럿이면_되묻기에_후보를_싣는다() -> None:
    """후보를 안 실으면 그 위층이 GPS로 짐작한 구의 스팟으로 버튼을 메운다(TP-182).

    "익선동"을 물은 사람에게 강서구 장소가 나가던 문제다. 진짜 답을 손에 들고도
    짐작을 보여주는 셈이라, 버튼이 비는 것보다 나쁘다.
    """
    provider = SequenceGeocodingProvider(
        [
            _result(
                query="서울특별시 종로구 익선동",
                count=2,
                labels=("종로구 익선동", "창원시 진해구 익선동"),
            )
        ]
    )
    tool = ResolveLocationTool(provider)

    result = await tool.execute(
        ResolveLocationQuery("익선동", purpose=LocationPurpose.SEARCH_CENTER)
    )

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    assert result.error.cause == "ambiguous_location"
    assert (
        result.error.details["candidate_names"]
        == "종로구 익선동|창원시 진해구 익선동"
    )


@pytest.mark.asyncio
async def test_후보_이름이_없으면_되묻기_후보도_비운다() -> None:
    """Provider가 이름을 못 주면 없는 대로 둔다 — 빈 버튼을 만들지 않는다."""
    provider = SequenceGeocodingProvider([_result(count=2)])
    tool = ResolveLocationTool(provider)

    result = await tool.execute(
        ResolveLocationQuery("익선동", purpose=LocationPurpose.SEARCH_CENTER)
    )

    assert result.error is not None
    assert "candidate_names" not in result.error.details


def _stored(title: str, *, content_id: str, address: str, lat: float, lon: float):
    return StoredPlaceLocation(
        content_id=content_id,
        title=title,
        address=address,
        latitude=lat,
        longitude=lon,
        district_code=None,
        concentration_name=title,
    )


@pytest.mark.asyncio
async def test_same_place_stored_twice_does_not_ask_again() -> None:
    """같은 장소가 분류만 달리해 두 번 적재된 경우는 되묻지 않는다.

    TourAPI가 "오설록 티하우스 북촌점"을 음식점과 쇼핑으로 각각 준다. 좌표는 8m 차이다.
    그런 행이 둘이라고 되물으면 선택지가 같은 이름 둘이라 화면에는 하나로 보이고, 눌러도
    같은 조회가 다시 돌아 빠져나갈 수 없다(2026-09-11 실측).
    """

    repository = MemoryPlaceLocationRepository(
        (
            _stored("오설록 티하우스 북촌점", content_id="3446417",
                    address="서울특별시 종로구 북촌로 45", lat=37.581067, lon=126.984587),
            _stored("오설록 티하우스 북촌점", content_id="4012699",
                    address="서울특별시 종로구 북촌로 45", lat=37.580999, lon=126.984601),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), repository
    ).execute(ResolveLocationQuery("오설록 티하우스 북촌점"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    assert result.location.resolved_name == "오설록 티하우스 북촌점"


@pytest.mark.asyncio
async def test_same_name_far_apart_still_asks_with_the_district() -> None:
    """이름이 같아도 멀리 떨어져 있으면 서로 다른 가게다.

    "전주식당"이 종로구와 중구에 1.7km 떨어져 있다. 이때는 되묻는 것이 맞지만, 이름만
    실으면 두 버튼이 같은 글자라 고를 수가 없다 — 자치구를 붙여 구분한다.
    """

    repository = MemoryPlaceLocationRepository(
        (
            _stored("전주식당", content_id="1", address="서울특별시 종로구 수표로20길 16-17",
                    lat=37.5698, lon=126.9911),
            _stored("전주식당", content_id="2", address="서울특별시 중구 남대문시장길 18-7",
                    lat=37.5598, lon=126.9770),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), repository
    ).execute(ResolveLocationQuery("전주식당"))

    assert result.status is ResolveLocationStatus.NO_DATA
    assert result.error is not None
    names = result.error.details["candidate_names"]
    assert "전주식당 (종로구)" in names
    assert "전주식당 (중구)" in names


@pytest.mark.asyncio
async def test_picked_district_label_resolves_to_that_row() -> None:
    """되묻기 버튼을 누르면 그 구의 행으로 풀린다.

    버튼 라벨이 그대로 다음 턴의 검색어가 된다. 붙여 둔 자치구를 다시 갈라 읽지 않으면
    "전주식당 (종로구)"이라는 이름을 저장소에서 못 찾아 해소가 실패한다.
    """

    repository = MemoryPlaceLocationRepository(
        (
            _stored("전주식당", content_id="1", address="서울특별시 종로구 수표로20길 16-17",
                    lat=37.5698, lon=126.9911),
            _stored("전주식당", content_id="2", address="서울특별시 중구 남대문시장길 18-7",
                    lat=37.5598, lon=126.9770),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), repository
    ).execute(ResolveLocationQuery("전주식당 (중구)"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert result.location is not None
    # 이름은 원래대로 돌려준다 — 라벨은 고르기 위한 표기이지 장소 이름이 아니다.
    assert result.location.resolved_name == "전주식당"
    assert result.location.latitude == pytest.approx(37.5598)
    # 저장소에는 붙인 표기 없이 조회한다.
    assert repository.calls == ["전주식당"]


@pytest.mark.asyncio
async def test_parenthesis_in_the_place_name_is_not_treated_as_a_district() -> None:
    """상호에 들어간 괄호는 자르지 않는다.

    "광장(전통)시장"에서 괄호를 자르면 "광장"이 되어 다른 곳을 찾는다. 괄호 안이
    자치구로 끝날 때만 힌트로 읽는다.
    """

    repository = MemoryPlaceLocationRepository(
        (
            _stored("광장(전통)시장", content_id="1", address="서울특별시 종로구 창경궁로 88",
                    lat=37.5701, lon=126.9997),
        )
    )

    result = await ResolveLocationTool(
        SequenceGeocodingProvider([]), repository
    ).execute(ResolveLocationQuery("광장(전통)시장"))

    assert result.status is ResolveLocationStatus.SUCCESS
    assert repository.calls == ["광장(전통)시장"]

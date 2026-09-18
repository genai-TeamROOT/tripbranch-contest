from __future__ import annotations

import asyncio

import pytest

from app.domain.models import PlaceDetails
from app.errors import ProviderUnavailableError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.recommendation_limits import MAX_RECOMMENDATION_CANDIDATE_LIMIT
from app.schemas import PlaceCandidate
from app.tools.nearby_place_details import (
    CANDIDATE_OVERFETCH_FACTOR,
    CANDIDATE_POOL_EXHAUSTED_WARNING,
    CANDIDATE_POOL_TRUNCATED_WARNING,
    MAX_PLACE_PROVIDER_ROWS,
    DetailStatus,
    NearbyPlaceDetailsQuery,
    NearbyPlaceDetailsTool,
    ToolStatus,
)


def _candidate(index: int, content_type_id: str | None = "12") -> PlaceCandidate:
    return PlaceCandidate(
        place_id=f"place-{index}",
        content_type_id=content_type_id,
        name=f"장소 {index}",
        category="attraction",
        latitude=37.5 + index / 1000,
        longitude=127.0,
        raw_source="test",
    )


def _details(candidate: PlaceCandidate) -> PlaceDetails:
    return PlaceDetails(
        content_id=candidate.place_id,
        content_type_id=candidate.content_type_id or "",
        title=candidate.name,
        address=None,
        overview="상세정보",
        homepage=None,
        telephone=None,
        operating_hours="09:00~18:00",
        rest_date="매주 월요일",
        raw_common={},
        raw_intro={},
        provider="test",
    )


class SearchProvider:
    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self.candidates = candidates
        self.seen_limit: int | None = None
        self.seen_region_code: str | None = None
        self.seen_district_code: str | None = None

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter=None,
        limit: int = 20,
    ) -> ProviderResult[list[PlaceCandidate]]:
        self.seen_limit = limit
        self.seen_region_code = region_code
        self.seen_district_code = district_code
        return provider_result(
            self.candidates[:limit],
            source=ProviderSource.FAKE_PLACE,
        )


class FilteringSearchProvider(SearchProvider):
    """행을 고른 **뒤에** 일부를 걸러내고 돌려주는 Provider.

    RealPlaceProvider의 실제 동작이다 — TourAPI가 numOfRows만큼 고른 행에서
    `map_tour_api_response()`가 미지원 분류와 지원 구 밖 장소를 제거한다. 그래서
    요청한 행 수보다 적게 돌아오고, 그 결손은 요청 행 수를 키워야만 메워진다.

    이 대역이 없으면 결손 자체가 재현되지 않아, 과요청을 되돌려도 테스트가 통과한다.
    """

    def __init__(
        self, candidates: list[PlaceCandidate], *, keep: int, every: int
    ) -> None:
        super().__init__(candidates)
        if not 0 < keep <= every:
            raise ValueError("keep은 1 이상 every 이하여야 합니다.")
        self._keep = keep
        self._every = every

    async def search_places(
        self, *args, **kwargs
    ) -> ProviderResult[list[PlaceCandidate]]:
        result = await super().search_places(*args, **kwargs)
        kept = [
            candidate
            for index, candidate in enumerate(result.data)
            if index % self._every < self._keep
        ]
        return provider_result(kept, source=ProviderSource.FAKE_PLACE)


class DetailsProvider:
    def __init__(self, failures: frozenset[str] = frozenset()) -> None:
        self.failures = failures
        self.active = 0
        self.max_active = 0

    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.001)
        self.active -= 1
        if content_id in self.failures:
            raise ProviderUnavailableError("test")
        return provider_result(
            _details(_candidate(int(content_id.rsplit("-", 1)[1]), content_type_id)),
            source=ProviderSource.FAKE_PLACE,
        )


@pytest.mark.asyncio
async def test_tool_combines_separate_search_and_details_providers() -> None:
    search = SearchProvider([_candidate(index) for index in range(3)])
    details = DetailsProvider()
    tool = NearbyPlaceDetailsTool(search, details)

    result = await tool.execute(NearbyPlaceDetailsQuery(37.5, 127.0))

    assert result.status is ToolStatus.SUCCESS
    assert [item.candidate.place_id for item in result.places] == [
        "place-0",
        "place-1",
        "place-2",
    ]
    assert all(item.detail_status is DetailStatus.SUCCESS for item in result.places)
    assert result.retrieved_at.tzinfo is not None
    assert result.elapsed_ms >= 0
    assert len(result.provider_metadata) == 4
    assert search.seen_region_code == "11"
    # 구는 요청에 싣지 않는다 — 지원 구 판정은 응답의 lDongSignguCd로 한다
    # (D-025). 한 구로 좁히면 반경 안의 옆 지원 구 후보가 잘린다.
    assert search.seen_district_code is None


@pytest.mark.asyncio
async def test_tool_applies_exclusions_limit_and_concurrency() -> None:
    search = SearchProvider([_candidate(index) for index in range(10)])
    details = DetailsProvider()
    tool = NearbyPlaceDetailsTool(search, details, max_concurrency=3)

    result = await tool.execute(
        NearbyPlaceDetailsQuery(
            37.5,
            127.0,
            limit=5,
            excluded_place_ids=frozenset({"place-0", "place-1"}),
        )
    )

    # 필요분 7곳(limit 5 + 제외 2)의 CANDIDATE_OVERFETCH_FACTOR배를 요청한다.
    assert search.seen_limit == 7 * CANDIDATE_OVERFETCH_FACTOR
    assert [item.candidate.place_id for item in result.places] == [
        "place-2",
        "place-3",
        "place-4",
        "place-5",
        "place-6",
    ]
    assert details.max_active <= 3


@pytest.mark.asyncio
async def test_tool_marks_exhausted_only_when_returned_candidates_are_all_excluded() -> None:
    search = SearchProvider([_candidate(index) for index in range(3)])
    result = await NearbyPlaceDetailsTool(search, DetailsProvider()).execute(
        NearbyPlaceDetailsQuery(
            37.5,
            127.0,
            limit=3,
            excluded_place_ids=frozenset({"place-0", "place-1", "place-2"}),
        )
    )

    assert result.status is ToolStatus.NO_DATA
    assert CANDIDATE_POOL_EXHAUSTED_WARNING in result.warnings


@pytest.mark.asyncio
async def test_tool_warns_when_row_cap_blocks_new_candidates() -> None:
    """상한에 걸린 빈 결과는 "더 없음"이 아니라 "더 못 받아옴"이다.

    이 경고가 없으면 반경 안에 후보가 남았는데도 소진됐다고 답하게 된다.
    """

    excluded = frozenset(f"place-{index}" for index in range(MAX_PLACE_PROVIDER_ROWS))
    search = SearchProvider(
        [_candidate(index) for index in range(MAX_PLACE_PROVIDER_ROWS)]
    )
    tool = NearbyPlaceDetailsTool(search, DetailsProvider())

    result = await tool.execute(
        NearbyPlaceDetailsQuery(37.5, 127.0, limit=5, excluded_place_ids=excluded)
    )

    # (5 + 100) x 3 = 315를 원하지만 상한이 100이라 제외분을 다 건너뛰지 못하고,
    # 새 후보가 하나도 안 남는다.
    assert search.seen_limit == MAX_PLACE_PROVIDER_ROWS
    assert result.status is ToolStatus.NO_DATA
    assert CANDIDATE_POOL_TRUNCATED_WARNING in result.warnings


@pytest.mark.asyncio
async def test_tool_does_not_warn_when_row_cap_is_reached_but_limit_is_filled() -> None:
    """상한에 걸려도 limit을 채웠으면 경고하지 않는다.

    과요청을 시작한 뒤로는 상한에 걸리는 것과 못 채우는 것이 더는 같은 뜻이 아니다.
    상한에 걸렸다는 이유만으로 경고하면, 후보가 넉넉한데도 소비 측이 보충 조회를
    멈춘다 — 고치려던 것과 같은 종류의 오독이다.
    """

    # 필요분 x CANDIDATE_OVERFETCH_FACTOR가 행 상한을 넘도록 제외분을 잡되,
    # 상한만큼 받으면 제외분을 건너뛰고도 limit이 남게 한다.
    limit = MAX_RECOMMENDATION_CANDIDATE_LIMIT
    excluded_count = (
        MAX_PLACE_PROVIDER_ROWS // CANDIDATE_OVERFETCH_FACTOR - limit + 1
    )
    excluded = frozenset(f"place-{index}" for index in range(excluded_count))
    search = SearchProvider(
        [_candidate(index) for index in range(MAX_PLACE_PROVIDER_ROWS * 2)]
    )
    tool = NearbyPlaceDetailsTool(search, DetailsProvider())

    result = await tool.execute(
        NearbyPlaceDetailsQuery(37.5, 127.0, limit=limit, excluded_place_ids=excluded)
    )

    assert search.seen_limit == MAX_PLACE_PROVIDER_ROWS
    assert len(result.places) == limit
    assert CANDIDATE_POOL_TRUNCATED_WARNING not in result.warnings


@pytest.mark.asyncio
async def test_tool_fills_limit_when_provider_filters_rows_out() -> None:
    """Provider가 걸러낸 뒤 돌려줘도 limit을 채운다.

    RealPlaceProvider는 TourAPI가 numOfRows만큼 고른 행에서 미지원 분류
    (숙박·여행코스)와 지원 구 밖 장소를 제거한 뒤 반환한다. 그래서 필요분만
    요청하면 반경에 후보가 아무리 남아 있어도 limit을 못 채운다.

    이 결손이 실제로 추천을 망가뜨렸다 — 안국역 반경 2km는 TourAPI totalCount가
    364곳인데 10 요청에 9곳만 와서, A의 `_candidate_pool_exhausted()`가 이를
    "반경 소진"으로 읽고 보충 조회를 한 번도 돌리지 않았다.
    """

    # 5행 중 2행만 살아남는다 = 생존율 40%. 실측 최저(홍대입구, 반경 2km)와 같다.
    search = FilteringSearchProvider(
        [_candidate(index) for index in range(300)], keep=2, every=5
    )
    tool = NearbyPlaceDetailsTool(search, DetailsProvider())

    result = await tool.execute(NearbyPlaceDetailsQuery(37.5, 127.0, limit=10))

    # 과요청이 없었다면 10행을 요청해 4곳만 남았을 자리다.
    assert search.seen_limit == 10 * CANDIDATE_OVERFETCH_FACTOR
    assert len(result.places) == 10
    assert CANDIDATE_POOL_TRUNCATED_WARNING not in result.warnings


@pytest.mark.asyncio
async def test_tool_does_not_warn_when_row_cap_is_not_reached() -> None:
    """상한 아래에서는 경고를 붙이지 않는다 — 정상 소진과 구분돼야 한다."""

    search = SearchProvider([_candidate(index) for index in range(10)])
    tool = NearbyPlaceDetailsTool(search, DetailsProvider())

    result = await tool.execute(
        NearbyPlaceDetailsQuery(
            37.5, 127.0, limit=5, excluded_place_ids=frozenset({"place-0"})
        )
    )

    assert CANDIDATE_POOL_TRUNCATED_WARNING not in result.warnings


@pytest.mark.asyncio
async def test_tool_returns_partial_for_detail_failures_and_missing_type() -> None:
    search = SearchProvider([_candidate(0), _candidate(1), _candidate(2, None)])
    details = DetailsProvider(failures=frozenset({"place-1"}))
    tool = NearbyPlaceDetailsTool(search, details)

    result = await tool.execute(NearbyPlaceDetailsQuery(37.5, 127.0))

    assert result.status is ToolStatus.PARTIAL
    assert [item.detail_status for item in result.places] == [
        DetailStatus.SUCCESS,
        DetailStatus.UNAVAILABLE,
        DetailStatus.NO_DATA,
    ]
    assert result.places[1].error_code == "provider_unavailable"
    assert result.places[2].error_code == "missing_content_type_id"
    assert result.warnings == ("partial_data",)
    assert len(result.provider_metadata) == 2
    assert all(
        metadata.source is ProviderSource.FAKE_PLACE
        and metadata.status is ProviderStatus.SUCCESS
        and metadata.retrieved_at.tzinfo is not None
        for metadata in result.provider_metadata
    )


@pytest.mark.asyncio
async def test_tool_returns_no_data_without_candidates() -> None:
    result = await NearbyPlaceDetailsTool(
        SearchProvider([]),
        DetailsProvider(),
    ).execute(NearbyPlaceDetailsQuery(37.5, 127.0))

    assert result.status is ToolStatus.NO_DATA
    assert result.places == ()
    assert result.provider_metadata[0].source is ProviderSource.FAKE_PLACE
    assert result.provider_metadata[0].status is ProviderStatus.SUCCESS
    assert result.provider_metadata[0].retrieved_at.tzinfo is not None


@pytest.mark.asyncio
async def test_tool_maps_search_failure_to_unavailable() -> None:
    class FailingSearchProvider(SearchProvider):
        async def search_places(self, *args, **kwargs):
            raise ProviderUnavailableError("test")

    result = await NearbyPlaceDetailsTool(
        FailingSearchProvider([]),
        DetailsProvider(),
    ).execute(NearbyPlaceDetailsQuery(37.5, 127.0))

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "unavailable"
    assert result.provider_metadata == ()


@pytest.mark.parametrize(
    "query",
    [
        NearbyPlaceDetailsQuery,
    ],
)
def test_query_validation(query) -> None:
    with pytest.raises(ValueError):
        query(91, 127)
    with pytest.raises(ValueError):
        query(37.5, 181)
    with pytest.raises(ValueError):
        query(37.5, 127, search_radius_km=0)
    with pytest.raises(ValueError):
        query(37.5, 127, limit=MAX_RECOMMENDATION_CANDIDATE_LIMIT + 1)


def test_tool_validates_concurrency() -> None:
    with pytest.raises(ValueError):
        NearbyPlaceDetailsTool(SearchProvider([]), DetailsProvider(), max_concurrency=0)

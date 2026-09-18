"""주변 후보를 검색하고 상세정보를 제한 병렬로 보완하는 내부 Tool."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter

from app.agent_context.district_selection import (
    MIN_TURN_CANDIDATES,
    select_district_candidates,
)
from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    PlaceCategoryFilter,
    PlaceDetails,
)
from app.errors import AppError
from app.place_search_policy import (
    DEFAULT_PLACE_SEARCH_RADIUS_KM,
    MAX_PLACE_PROVIDER_ROWS,
    MAX_PLACE_SEARCH_RADIUS_KM,
    PLACE_SEARCH_LDONG_REGION_CODE,
)
from app.providers.contracts import ProviderMetadata
from app.providers.protocols import (
    BarrierFreePlaceSearch,
    BarrierFreePlaceSearchProvider,
    BatchPlaceDetailsProvider,
    DistrictPlaceSearchProvider,
    PlaceDetailsProvider,
    PlaceSearchProvider,
)
from app.recommendation_limits import (
    DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT,
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)
from app.schemas import PlaceCandidate
from app.tools.contracts import ToolError, ToolStatus

# 필요한 후보 수의 몇 배를 Provider에 요청할지.
#
# **응답에서 쓸 수 없는 행이 빠지기 때문에 필요분만 요청하면 항상 모자란다.**
# Provider는 미지원 분류(숙박·여행코스)와 지원 구 밖 장소를 걸러낸 뒤에 돌려주는데,
# 그 필터는 TourAPI가 numOfRows만큼 행을 고른 **다음에** 걸린다. 그래서 상위 N행에
# 숙박이 하나라도 섞이면 반경에 후보가 아무리 남아 있어도 N곳을 못 채운다.
#
# 3배로 잡은 근거는 실측 생존율이다(2026-08-27, 반경 2km, 30행·60행 요청):
#
#   성수동 97~98% / 안국역 80~87% / 명동 77~78% / 경복궁 62~70%
#   북촌 53~67% / 홍대입구 40~58%
#
# 최저가 홍대입구 40%였고 3배면 그 아래(33%)까지 덮는다. 홍대·북촌이 낮은 이유는
# 게스트하우스가 밀집해 상위 행이 숙박으로 채워지기 때문이다.
#
# **행 수를 늘리는 비용은 응답 크기뿐이다.** TourAPI 목록 조회는 numOfRows를 키워도
# 호출 1회고 일일 한도는 호출 수 기준이라, 한도 소모가 늘지 않는다. 그래서 넉넉히
# 받아 자르는 편이 "모자라면 다시 부르기"보다 싸다.
CANDIDATE_OVERFETCH_FACTOR = 3

# 상한(MAX_PLACE_PROVIDER_ROWS)까지 받고도 요청한 만큼 새 후보를 채우지 못했다.
CANDIDATE_POOL_TRUNCATED_WARNING = "candidate_pool_truncated"

# Provider가 실제 후보를 돌려줬지만, 모두 이전에 노출·거절한 place_id여서 남은
# 후보가 없을 때만 붙인다. 단순히 Provider 호출이 성공했다는 것과는 다르다.
CANDIDATE_POOL_EXHAUSTED_WARNING = "candidate_pool_exhausted"

# A가 보낸 무장애 어휘 중 C가 모르는 값이 있었다. 그 값은 무시하고 나머지로 좁힌다.
#
# **무시하되 조용히 넘기지는 않는다.** C의 조건 계약은 `list[str]`이라 A가 어휘를
# 늘려도 요청이 깨지지 않는데(그게 의도다), 아무 흔적도 남기지 않으면 사용자가
# 요구한 조건 하나가 사라진 것을 아무도 모른다. 결과는 정상으로 보이고 오류도 없다.
UNKNOWN_ACCESSIBILITY_NEED_WARNING = "unknown_accessibility_need"


class DetailStatus(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class NearbyPlaceDetailsQuery:
    latitude: float
    longitude: float
    search_radius_km: float = DEFAULT_PLACE_SEARCH_RADIUS_KM
    region_code: str = PLACE_SEARCH_LDONG_REGION_CODE
    # 구는 요청에 싣지 않는다 — 지원 구 판정은 응답의 lDongSignguCd로 한다(D-025).
    # 한 구로 좁히면 반경 안에 있는 옆 지원 구 후보가 잘리고, 구마다 호출하면
    # 호출 수가 구 수만큼 늘어난다.
    district_code: str | None = None
    # 값이 있으면 **반경을 버리고 그 구 전체를 후보 모집단으로 삼는다**(D-119).
    # 위 district_code와 다른 것이다 — 저쪽은 TourAPI 반경 검색에 구 필터를 얹는
    # 인자이고(지금 아무도 안 쓴다), 이쪽은 검색 방식 자체를 바꾼다.
    #
    # 이 모드에서 latitude·longitude는 쓰이지 않는다. 구 이름을 푼 대표점이 그대로
    # 들어오지만 후보 수집에도 정렬에도 관여하지 않는다 — 필드를 지우지 않은 것은
    # Context 조립이 기준점 좌표를 그대로 싣기 때문이다.
    district_scope: str | None = None
    # 구 단위 모드에서만 쓴다. 사용자가 말한 분류 조건 전부를 한 번에 받는다.
    # 반경 검색은 분류마다 따로 호출하지만(위 category_filter) 구 단위는 한 번에
    # 모아야 한다 — 분류마다 부르면 구 전량을 그 수만큼 읽고, 무엇보다 분류 몫이
    # 호출마다 따로 적용돼 합친 결과가 몫을 넘는다.
    category_filters: tuple[PlaceCategoryFilter | None, ...] = ()
    limit: int = DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT
    preferred_categories: tuple[str, ...] = ()
    category_filter: PlaceCategoryFilter | None = None
    excluded_place_ids: frozenset[str] = frozenset()
    # 요구된 무장애 편의. 비어 있으면 지금까지의 TourAPI 경로를 그대로 쓴다.
    #
    # 값이 있으면 후보 출처가 저장소로 바뀐다. 무장애 정보가 Supabase에만 있어
    # TourAPI 목록 조회로는 이 조건을 표현할 수 없기 때문이다. 그 출처 변경을
    # 무장애 요청에만 가두려고 조건부로 둔다.
    accessibility_needs: tuple[AccessibilityNeed, ...] = ()

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude는 -90 이상 90 이하여야 합니다.")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude는 -180 이상 180 이하여야 합니다.")
        if not 0 < self.search_radius_km <= MAX_PLACE_SEARCH_RADIUS_KM:
            raise ValueError(
                "search_radius_km는 0 초과 "
                f"{MAX_PLACE_SEARCH_RADIUS_KM:g} 이하여야 합니다."
            )
        if not (
            MIN_RECOMMENDATION_LIMIT
            <= self.limit
            <= MAX_RECOMMENDATION_CANDIDATE_LIMIT
        ):
            raise ValueError(
                "limit은 "
                f"{MIN_RECOMMENDATION_LIMIT} 이상 "
                f"{MAX_RECOMMENDATION_CANDIDATE_LIMIT} 이하여야 합니다."
            )


@dataclass(frozen=True)
class EnrichedPlace:
    candidate: PlaceCandidate
    details: PlaceDetails | None
    detail_status: DetailStatus
    error_code: str | None = None
    # 무장애 조건이 있는 요청에서만 채워진다. 후보에 남았다는 것은 접근 불가가
    # 아니라는 뜻이지 온전히 가능하다는 뜻은 아니라서(`partial`도 후보로 남긴다),
    # 답변이 "일부 구역은 접근이 어렵다"를 말할 수 있게 값을 함께 올린다.
    #
    # 요구한 어휘만 담는다. 판정표가 없는 여섯 어휘는 여기 오지 않는다.
    accessibility_verdicts: Mapping[AccessibilityNeed, AccessibilityVerdict] | None = (
        None
    )


@dataclass(frozen=True)
class NearbyPlaceDetailsResult:
    places: tuple[EnrichedPlace, ...]
    status: ToolStatus
    source: str
    retrieved_at: datetime
    elapsed_ms: float
    error: ToolError | None = None
    warnings: tuple[str, ...] = ()
    provider_metadata: tuple[ProviderMetadata, ...] = ()


def _matches_category(candidate: PlaceCandidate, category_filter: PlaceCategoryFilter) -> bool:
    """후보가 그 분류 조건에 드는지. 지정된 자리만 대조한다.

    반경 검색은 이 판정을 TourAPI가 해주지만(요청에 분류 코드를 실는다) 구 단위는
    저장소에서 구 전량을 받으므로 여기서 한다. 조건에 없는(None) 자리는 보지 않는다
    — "음식점"만 지정한 조건이 소분류까지 같아야 하는 것으로 좁혀지면 안 된다.
    """
    pairs = (
        (category_filter.content_type_id, candidate.content_type_id),
        (category_filter.lcls_systm1, candidate.lcls_systm1),
        (category_filter.lcls_systm2, candidate.lcls_systm2),
        (category_filter.lcls_systm3, candidate.lcls_systm3),
    )
    return all(required is None or required == actual for required, actual in pairs)


def _with_verdicts(
    result: NearbyPlaceDetailsResult,
    verdicts: Mapping[str, Mapping[AccessibilityNeed, AccessibilityVerdict]],
) -> NearbyPlaceDetailsResult:
    """무장애 판정을 결과에 붙인다. 판정이 없으면 결과를 그대로 돌려준다.

    후보를 만들 때가 아니라 다 만든 뒤에 붙인다. `EnrichedPlace`는 상세 보완의
    갈래마다 따로 만들어지는데(성공·상세 없음·조회 실패·유형 없음), 그 자리마다
    판정을 넣으면 한 곳을 빠뜨렸을 때 그 갈래로 들어온 장소만 안내를 잃는다 —
    오류는 나지 않고 안내만 사라진다.
    """
    if not verdicts:
        return result
    return replace(
        result,
        places=tuple(
            replace(
                place,
                accessibility_verdicts=verdicts.get(place.candidate.place_id),
            )
            for place in result.places
        ),
    )


class NearbyPlaceDetailsTool:
    def __init__(
        self,
        search_provider: PlaceSearchProvider,
        details_provider: PlaceDetailsProvider,
        max_concurrency: int = 3,
        barrier_free_search_provider: BarrierFreePlaceSearchProvider | None = None,
        district_search_provider: DistrictPlaceSearchProvider | None = None,
    ) -> None:
        if not 1 <= max_concurrency <= 10:
            raise ValueError("max_concurrency는 1 이상 10 이하여야 합니다.")
        self._search_provider = search_provider
        self._details_provider = details_provider
        self._max_concurrency = max_concurrency
        # 없으면 구 단위 요청을 처리하지 못한다. 그때는 반경 검색으로 조용히
        # 되돌아가지 않고 unavailable로 답한다 — 아래 _search_district()를 본다.
        self._district_search_provider = district_search_provider
        # 없으면 무장애 조건이 와도 좁히지 못한다. 그때는 조용히 넓은 결과를 주는
        # 대신 unavailable로 답한다 — 아래 _search()를 본다.
        self._barrier_free_search_provider = barrier_free_search_provider

    async def execute(
        self, query: NearbyPlaceDetailsQuery
    ) -> NearbyPlaceDetailsResult:
        started_at = perf_counter()
        if query.district_scope is not None:
            return await self._execute_district(query, started_at)

        # 제외분만큼 더 받아야 새 후보가 limit만큼 남는다. 장소 검색은 거리순 고정
        # 정렬이라 행 수만 늘리면 이전 결과의 상위집합이 와서 페이지 번호가 필요 없다.
        # 여기에 CANDIDATE_OVERFETCH_FACTOR를 곱하는 이유는 그 상수 주석에 있다 —
        # Provider가 걸러낸 뒤에 돌려주므로 필요분만 요청하면 limit을 못 채운다.
        needed_rows = query.limit + len(query.excluded_place_ids)
        wanted_rows = needed_rows * CANDIDATE_OVERFETCH_FACTOR
        provider_limit = min(MAX_PLACE_PROVIDER_ROWS, wanted_rows)
        row_cap_reached = wanted_rows > MAX_PLACE_PROVIDER_ROWS

        barrier_free_provider = self._barrier_free_search_provider
        if query.accessibility_needs and barrier_free_provider is None:
            # 무장애 조건을 좁힐 수단이 없다. 조건을 무시한 넓은 결과를 주면
            # 사용자는 요구가 반영된 줄 알고 못 가는 곳을 받는다 — 실패를 첫
            # 결과가 아니라 여기서 드러낸다(D-042와 같은 이유).
            return self._result(
                places=(),
                status=ToolStatus.UNAVAILABLE,
                started_at=started_at,
                provider_metadata=(),
                error=ToolError(
                    code="unavailable",
                    message="무장애 조건으로 장소를 검색할 수 없습니다.",
                    cause="upstream_error",
                    retryable=False,
                ),
            )

        try:
            # **검색은 여기서만 갈린다.** 무장애 조건이 있으면 저장소, 없으면
            # 지금까지의 TourAPI다. 아래 상세 보완·정렬·경고는 후보가 어디서
            # 왔는지 모르므로 두 경로가 같은 코드를 탄다.
            if query.accessibility_needs and barrier_free_provider is not None:
                search_result = (
                    await barrier_free_provider.search_places_with_accessibility(
                        latitude=query.latitude,
                        longitude=query.longitude,
                        search_radius_km=query.search_radius_km,
                        needs=query.accessibility_needs,
                        category_filter=query.category_filter,
                        limit=provider_limit,
                    )
                )
            else:
                search_result = await self._search_provider.search_places(
                    latitude=query.latitude,
                    longitude=query.longitude,
                    preferred_categories=list(query.preferred_categories),
                    search_radius_km=query.search_radius_km,
                    region_code=query.region_code,
                    district_code=query.district_code,
                    category_filter=query.category_filter,
                    limit=provider_limit,
                )
        except AppError as exc:
            return self._result(
                places=(),
                status=ToolStatus.UNAVAILABLE,
                started_at=started_at,
                provider_metadata=(),
                error=ToolError(
                    code="unavailable",
                    message="주변 장소를 검색하지 못했습니다.",
                    cause=(
                        "timeout"
                        if exc.code == "provider_timeout"
                        else "upstream_error"
                    ),
                    retryable=exc.retryable,
                ),
            )
        if isinstance(search_result.data, BarrierFreePlaceSearch):
            candidates = search_result.data.candidates
            accessibility_verdicts = search_result.data.verdicts
        else:
            candidates = search_result.data
            accessibility_verdicts = {}
        selected = tuple(
            candidate
            for candidate in candidates
            if candidate.place_id not in query.excluded_place_ids
        )[: query.limit]

        # 상한에 걸려 요청한 만큼 못 채운 경우를 표시한다. 판정을 "행 상한에 걸릴
        # 것 같다"가 아니라 **실제로 못 채웠다**로 두는 이유는, 넉넉히 받게 된
        # 뒤로는 상한에 걸리고도 limit을 다 채우는 경우가 흔해졌기 때문이다.
        # 이 표시가 붙은 결과는 "이 근처에 더 없음"이 아니라 "더 받아올 수 없음"이다
        # — 아직 후보가 남았는데 소진됐다고 답하는 걸 막는다.
        truncated = row_cap_reached and len(selected) < query.limit
        exhausted = bool(candidates) and bool(query.excluded_place_ids) and not selected

        if not selected:
            return self._result(
                places=(),
                status=ToolStatus.NO_DATA,
                started_at=started_at,
                provider_metadata=(search_result.metadata,),
                truncated=truncated,
                exhausted=exhausted,
            )

        return _with_verdicts(
            await self._enrich_selected(
                selected, search_result.metadata, started_at, truncated=truncated
            ),
            accessibility_verdicts,
        )

    async def _execute_district(
        self, query: NearbyPlaceDetailsQuery, started_at: float
    ) -> NearbyPlaceDetailsResult:
        """구 전체를 후보 모집단으로 삼는 경로(D-119).

        반경 검색과 갈리는 것은 **후보를 어디서 얼마나 가져오느냐** 두 가지뿐이다.
        저장소에서 구 전량을 받고, 앞에서 자르는 대신 `select_district_candidates()`가
        고른다. 상세 보완부터는 같은 코드를 탄다.

        과요청(CANDIDATE_OVERFETCH_FACTOR)을 하지 않는다. 그건 Provider가 필터링
        뒤에 돌려줘서 필요분만 요청하면 모자라는 반경 검색의 사정이고, 여기서는
        구 전량을 이미 손에 들고 있다.
        """
        provider = self._district_search_provider
        if provider is None:
            # 반경 검색으로 조용히 되돌아가면 "강남구"가 대표점 주변 수백 미터로
            # 좁혀진 결과를 내면서 사용자도 우리도 그 사실을 모른다(D-042와 같은 이유).
            return self._result(
                places=(),
                status=ToolStatus.UNAVAILABLE,
                started_at=started_at,
                provider_metadata=(),
                error=ToolError(
                    code="unavailable",
                    message="구 단위로 장소를 검색할 수 없습니다.",
                    cause="upstream_error",
                    retryable=False,
                ),
            )

        try:
            search_result = await provider.search_places_in_district(
                district_code=query.district_scope or ""
            )
        except AppError as exc:
            return self._result(
                places=(),
                status=ToolStatus.UNAVAILABLE,
                started_at=started_at,
                provider_metadata=(),
                error=ToolError(
                    code="unavailable",
                    message="구 단위로 장소를 검색하지 못했습니다.",
                    cause=("timeout" if exc.code == "provider_timeout" else "upstream_error"),
                    retryable=exc.retryable,
                ),
            )

        filters = tuple(item for item in query.category_filters if item is not None)
        pool = tuple(
            candidate
            for candidate in search_result.data
            if not filters or any(_matches_category(candidate, item) for item in filters)
        )
        selected = select_district_candidates(
            pool,
            excluded_place_ids=query.excluded_place_ids,
            limit=query.limit,
            has_category_condition=bool(filters),
        )
        # 이 구에서 더 줄 것이 남지 않았다는 신호. 반경 검색의 "행 상한에 걸렸다"와는
        # 다른 사정이라 truncated가 아니라 exhausted로 표시한다.
        exhausted = len(selected) < MIN_TURN_CANDIDATES
        if not selected:
            return self._result(
                places=(),
                status=ToolStatus.NO_DATA,
                started_at=started_at,
                provider_metadata=(search_result.metadata,),
                exhausted=bool(query.excluded_place_ids),
            )
        enriched = await self._enrich_selected(
            selected, search_result.metadata, started_at
        )
        if not exhausted:
            return enriched
        return replace(
            enriched,
            warnings=(*enriched.warnings, CANDIDATE_POOL_EXHAUSTED_WARNING),
        )

    async def _enrich_selected(
        self,
        selected: tuple[PlaceCandidate, ...],
        search_metadata: ProviderMetadata,
        started_at: float,
        *,
        truncated: bool = False,
    ) -> NearbyPlaceDetailsResult:
        """고른 후보에 상세정보를 붙인다.

        **후보가 어디서 왔는지 모른다.** 반경 검색·무장애 저장소·구 단위 저장소가
        모두 이 한 곳으로 모인다 — 검색만 갈리고 그 뒤는 같은 코드를 타야 세 경로의
        결과 모양이 어긋나지 않는다.
        """
        if isinstance(self._details_provider, BatchPlaceDetailsProvider):
            return await self._enrich_in_batch(
                selected, search_metadata, started_at, truncated=truncated
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def enrich(
            candidate: PlaceCandidate,
        ) -> tuple[EnrichedPlace, ProviderMetadata | None]:
            if not candidate.content_type_id:
                return (
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.NO_DATA,
                        error_code="missing_content_type_id",
                    ),
                    None,
                )

            try:
                async with semaphore:
                    details_result = await self._details_provider.get_details(
                        candidate.place_id,
                        candidate.content_type_id,
                    )
                    details = details_result.data
            except AppError as exc:
                return (
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.UNAVAILABLE,
                        error_code=exc.code,
                    ),
                    None,
                )

            if not self._has_detail_data(details):
                return (
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.NO_DATA,
                        error_code="detail_no_data",
                    ),
                    details_result.metadata,
                )
            return (
                EnrichedPlace(
                    candidate=candidate,
                    details=details,
                    detail_status=DetailStatus.SUCCESS,
                ),
                details_result.metadata,
            )

        enriched = tuple(await asyncio.gather(*(enrich(item) for item in selected)))
        places = tuple(item for item, _ in enriched)
        provider_metadata = (search_metadata,) + tuple(
            metadata for _, metadata in enriched if metadata is not None
        )
        status = (
            ToolStatus.SUCCESS
            if all(item.detail_status is DetailStatus.SUCCESS for item in places)
            else ToolStatus.PARTIAL
        )
        return self._result(
            places=places,
            status=status,
            started_at=started_at,
            provider_metadata=provider_metadata,
            truncated=truncated,
        )

    async def _enrich_in_batch(
        self,
        selected: tuple[PlaceCandidate, ...],
        search_metadata: ProviderMetadata,
        started_at: float,
        *,
        truncated: bool = False,
    ) -> NearbyPlaceDetailsResult:
        """다건 조회를 지원하는 Provider로 후보 상세정보를 한 번에 가져온다.

        후보 순서는 검색 결과(selected) 순회로 재조립해 보존한다.
        """
        # content_type_id가 없는 후보는 단건 경로와 동일하게 조회 대상에서 제외한다
        # (원본인 TourAPI에 유형 정보가 없는 장소이므로 저장소에서도 신뢰하지 않는다).
        target_ids = [
            candidate.place_id for candidate in selected if candidate.content_type_id
        ]

        details_by_id: dict[str, PlaceDetails] = {}
        details_metadata: ProviderMetadata | None = None
        error_code: str | None = None
        if target_ids:
            try:
                batch_result = await self._details_provider.get_details_batch(target_ids)
                details_by_id = batch_result.data
                details_metadata = batch_result.metadata
            except AppError as exc:
                error_code = exc.code

        places: list[EnrichedPlace] = []
        for candidate in selected:
            if not candidate.content_type_id:
                places.append(
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.NO_DATA,
                        error_code="missing_content_type_id",
                    )
                )
                continue
            if error_code is not None:
                places.append(
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.UNAVAILABLE,
                        error_code=error_code,
                    )
                )
                continue
            details = details_by_id.get(candidate.place_id)
            if details is None or not self._has_detail_data(details):
                places.append(
                    EnrichedPlace(
                        candidate=candidate,
                        details=None,
                        detail_status=DetailStatus.NO_DATA,
                        error_code="detail_no_data",
                    )
                )
                continue
            places.append(
                EnrichedPlace(
                    candidate=candidate,
                    details=details,
                    detail_status=DetailStatus.SUCCESS,
                )
            )

        provider_metadata = (search_metadata,) + (
            (details_metadata,) if details_metadata is not None else ()
        )
        return self._result(
            places=tuple(places),
            status=self._batch_status(tuple(places), unavailable=error_code is not None),
            started_at=started_at,
            provider_metadata=provider_metadata,
            truncated=truncated,
        )

    @staticmethod
    def _batch_status(
        places: tuple[EnrichedPlace, ...], *, unavailable: bool
    ) -> ToolStatus:
        if unavailable:
            return ToolStatus.UNAVAILABLE
        if all(item.detail_status is DetailStatus.SUCCESS for item in places):
            return ToolStatus.SUCCESS
        if all(item.detail_status is not DetailStatus.SUCCESS for item in places):
            return ToolStatus.NO_DATA
        return ToolStatus.PARTIAL

    @staticmethod
    def _has_detail_data(details: PlaceDetails) -> bool:
        return any(
            (
                details.title,
                details.address,
                details.overview,
                details.homepage,
                details.telephone,
                details.operating_hours,
                details.rest_date,
                details.raw_common,
                details.raw_intro,
            )
        )

    @staticmethod
    def _result(
        places: tuple[EnrichedPlace, ...],
        status: ToolStatus,
        started_at: float,
        provider_metadata: tuple[ProviderMetadata, ...],
        error: ToolError | None = None,
        truncated: bool = False,
        exhausted: bool = False,
    ) -> NearbyPlaceDetailsResult:
        warnings = ("partial_data",) if status is ToolStatus.PARTIAL else ()
        if truncated:
            warnings = (*warnings, CANDIDATE_POOL_TRUNCATED_WARNING)
        if exhausted:
            warnings = (*warnings, CANDIDATE_POOL_EXHAUSTED_WARNING)
        return NearbyPlaceDetailsResult(
            places=places,
            status=status,
            source="nearby_place_details_tool",
            retrieved_at=datetime.now(UTC),
            elapsed_ms=(perf_counter() - started_at) * 1000,
            error=error,
            warnings=warnings,
            provider_metadata=provider_metadata,
        )

"""무장애 편의를 요구한 요청의 후보를 Supabase에서 찾는 Provider.

역할: `RealPlaceProvider.search_places`(TourAPI locationBasedList2)를 **대체하지
않고 나란히 선다.** 무장애 조건이 있는 요청만 이쪽으로 오고, 조건이 없으면 지금까지의
TourAPI 경로가 그대로 쓰인다.

왜 나누는가. TourAPI 목록 조회에 실을 수 있는 조건은 반경과 분류 코드뿐이라
"무장애 정보가 있는 곳만"을 표현할 자리가 없다. 무장애 정보는 `place_barrier_free`
에만 있으므로(D-077) 그 조건이 붙은 요청은 저장소에서 후보를 뽑는다. 대신 후보 출처가
실시간 조회에서 스냅샷으로 바뀌므로, 그 변경을 무장애 요청에만 가둔다.

이 Provider가 돌려주는 것은 TourAPI 경로와 같은 `PlaceCandidate`다. 그래서 뒤이은
상세 보완·분류별 병합·Context 조립은 후보가 어디서 왔는지 몰라도 된다 —
`NearbyPlaceDetailsTool`에서 갈리는 것은 검색 한 단계뿐이다.

`operating_hours`를 채우지 않는 것도 TourAPI 경로와 맞춘 것이다. 운영시간은 뒤이은
상세 보완이 채우므로, 검색 단계에서 미리 넣으면 같은 값을 두 경로가 서로 다른 규칙으로
만들게 된다.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    BarrierFreePlaceRow,
    PlaceCategoryFilter,
)
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.mappers import resolve_place_category
from app.providers.protocols import BarrierFreePlaceSearch
from app.repositories.protocols import BarrierFreePlaceSearchRepository
from app.schemas import PlaceCandidate

_RAW_SOURCE = "supabase_barrier_free"


class SupabaseBarrierFreePlaceSearchProvider:
    """저장소 조회를 감싸 `PlaceCandidate` 목록으로 옮긴다."""

    def __init__(self, repository: BarrierFreePlaceSearchRepository) -> None:
        self._repository = repository

    async def search_places_with_accessibility(
        self,
        *,
        latitude: float,
        longitude: float,
        search_radius_km: float,
        needs: Sequence[AccessibilityNeed],
        category_filter: PlaceCategoryFilter | None = None,
        limit: int,
    ) -> ProviderResult[BarrierFreePlaceSearch]:
        rows = await self._repository.search_places_barrier_free(
            latitude=latitude,
            longitude=longitude,
            radius_km=search_radius_km,
            needs=needs,
            category_filter=category_filter,
            limit=limit,
        )
        candidates: list[PlaceCandidate] = []
        verdicts: dict[str, dict[AccessibilityNeed, AccessibilityVerdict]] = {}
        for row in rows:
            candidate = _to_candidate(row)
            if candidate is None:
                continue
            candidates.append(candidate)
            resolved = _requested_verdicts(row, needs)
            if resolved:
                verdicts[candidate.place_id] = resolved
        return provider_result(
            BarrierFreePlaceSearch(candidates=candidates, verdicts=verdicts),
            source=ProviderSource.SUPABASE_BARRIER_FREE_PLACES,
            status=ProviderStatus.SUCCESS if candidates else ProviderStatus.NO_DATA,
        )


# 어휘 → 그 판정을 담은 행의 필드. 판정표가 있는 셋만 있다. 나머지 여섯은
# RPC가 원문 규칙으로 거르므로 "후보에 있다" 말고는 올릴 값이 없다.
_VERDICT_FIELDS = {
    AccessibilityNeed.WHEELCHAIR_ACCESS: "wheelchair_access_verdict",
    AccessibilityNeed.STROLLER_ACCESS: "stroller_access_verdict",
    AccessibilityNeed.VISUAL_GUIDE: "visual_guide_verdict",
}


def _requested_verdicts(
    row: BarrierFreePlaceRow, needs: Sequence[AccessibilityNeed]
) -> dict[AccessibilityNeed, AccessibilityVerdict]:
    """요구한 어휘의 판정만 고른다.

    요구하지 않은 편의의 판정까지 올리면 사용자가 묻지 않은 것을 답변이 말하게
    된다. 시각안내를 물었는데 "유모차는 일부 구역이 어렵다"가 붙는 식이다.
    """
    resolved: dict[AccessibilityNeed, AccessibilityVerdict] = {}
    for need in needs:
        field = _VERDICT_FIELDS.get(need)
        if field is None:
            continue
        verdict = getattr(row, field)
        if verdict is not None:
            resolved[need] = verdict
    return resolved


def _to_candidate(row: BarrierFreePlaceRow) -> PlaceCandidate | None:
    """저장소 행을 후보로 옮긴다. 추천 대상이 아닌 유형이면 None.

    분류 판정은 `resolve_place_category()`에 맡긴다 — TourAPI 경로가 쓰는 것과
    같은 함수다. 여기서 따로 판정하면 같은 장소가 경로에 따라 다른 분류로 나가고,
    숙박·여행코스를 거르는 규칙도 한쪽에만 남는다.

    `title`이 빈 행은 버린다. 이름 없이 카드를 만들 수 없고, 이름이 비어 있다는 것은
    `places` 적재가 어긋났다는 뜻이라 조용히 통과시킬 값이 아니다.
    """
    if not row.title.strip():
        return None
    content_type_id = row.content_type_id or ""
    category = resolve_place_category(content_type_id)
    if category is None:
        return None
    return PlaceCandidate(
        place_id=row.content_id,
        content_type_id=row.content_type_id,
        lcls_systm1=row.lcls_systm1,
        lcls_systm2=row.lcls_systm2,
        lcls_systm3=row.lcls_systm3,
        name=row.title,
        category=category,
        latitude=row.latitude,
        longitude=row.longitude,
        address=row.address,
        # 운영시간은 상세 보완이 채운다. TourAPI 경로와 같은 자리를 비워 둔다.
        operating_hours=None,
        raw_source=_RAW_SOURCE,
    )

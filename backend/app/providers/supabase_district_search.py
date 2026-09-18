"""구 단위 요청의 후보를 Supabase에서 모으는 Provider(D-119).

역할: `RealPlaceProvider.search_places`(TourAPI locationBasedList2)를 **대체하지
않고 나란히 선다.** 구 이름으로 들어온 요청만 이쪽으로 오고, 장소·역 이름으로 들어온
요청은 지금까지의 반경 검색이 그대로 쓰인다.

왜 나누는가. TourAPI 목록 조회는 좌표와 반경이 필수라 "이 구 전체"를 표현할 자리가
없다. 구 코드를 실을 수는 있지만 그건 반경 검색에 구 필터가 얹히는 것이지 반경이
사라지는 게 아니다. 반경 2km 원은 12.6km²인데 강남구는 39.5km²다.

이 Provider가 돌려주는 것은 TourAPI 경로와 같은 `PlaceCandidate`다. 그래서 뒤이은
상세 보완·병합·Context 조립은 후보가 어디서 왔는지 몰라도 된다 —
`NearbyPlaceDetailsTool`에서 갈리는 것은 검색 한 단계뿐이다. 무장애 경로가 먼저
같은 자리를 잡아 뒀다(`supabase_barrier_free_search.py`).

**개수를 여기서 자르지 않는다.** 구 전량을 그대로 올리고, 몇 곳을 쓸지는
`agent_context.district_selection`이 정한다. 자름과 고름을 한곳에 두면 "앞에서
N곳"이 결과를 정해버리는데, 구 단위에는 의미 있는 순서가 없다.
"""

from __future__ import annotations

from app.domain.models import DistrictPlaceRow
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.mappers import resolve_place_category
from app.repositories.protocols import DistrictPlaceRepository
from app.schemas import PlaceCandidate

_RAW_SOURCE = "supabase_district"


class SupabaseDistrictPlaceSearchProvider:
    """저장소 조회를 감싸 `PlaceCandidate` 목록으로 옮긴다."""

    def __init__(self, repository: DistrictPlaceRepository) -> None:
        self._repository = repository

    async def search_places_in_district(
        self, *, district_code: str
    ) -> ProviderResult[list[PlaceCandidate]]:
        rows = await self._repository.list_active_places_in_district(district_code)
        candidates = [
            candidate for candidate in (_to_candidate(row) for row in rows) if candidate
        ]
        return provider_result(
            candidates,
            source=ProviderSource.SUPABASE_PLACES,
            status=ProviderStatus.SUCCESS if candidates else ProviderStatus.NO_DATA,
        )


def _to_candidate(row: DistrictPlaceRow) -> PlaceCandidate | None:
    """저장소 행을 후보로 옮긴다. 추천 대상이 아닌 유형이면 None.

    분류 판정은 `resolve_place_category()`에 맡긴다 — TourAPI 경로가 쓰는 것과 같은
    함수다. 여기서 따로 판정하면 같은 장소가 경로에 따라 다른 분류로 나가고,
    숙박·여행코스를 거르는 규칙도 한쪽에만 남는다.

    `title`이 빈 행은 버린다. 이름 없이 카드를 만들 수 없다.
    """
    if not row.title.strip():
        return None
    category = resolve_place_category(row.content_type_id or "")
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


__all__ = ["SupabaseDistrictPlaceSearchProvider"]

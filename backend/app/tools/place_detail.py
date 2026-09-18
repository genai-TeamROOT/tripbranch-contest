"""장소 1건의 상세정보를 이름으로 조회하는 내부 Tool.

NearbyPlaceDetailsTool과 나누는 이유는 조회 대상이 다르기 때문이다 — 그쪽은
좌표 주변 후보 N건을 검색해 상세를 보강하고, 이쪽은 이미 특정된 장소 1건만 본다.

**추천 후보용 PLACE_DETAILS_SOURCE를 따르지 않는다(D-060).** 경로가 달라서다 —
그쪽은 후보 N건 배치라 외부 호출이 0회여야 의미가 있고, 이쪽은 1건이지만 overview가
필요해 detailCommon2를 한 번은 불러야 한다.

HybridPlaceDetailsProvider가 places 캐시로 이름·운영시간·주차·요금·안내처·편의시설을
채우고 overview·homepage만 detailCommon2로 가져온다. 외부 호출이 3회
(searchKeyword2 + detailCommon2 + detailIntro2)에서 1회로 준다.

**D-054를 대체한다.** 그 결정은 "캐시에는 INFO가 답할 데이터가 없다"가 전제였고,
당시 동기화 대상은 operating_hours_raw/rest_date_raw뿐이었다. D-056(주차·요금)과
D-060(안내처·편의시설)으로 캐시가 question_type 전부를 덮게 되면서 전제가 사라졌다.
출처를 고르는 설정도 두지 않는다 — 두 경로가 같은 질문에 답하므로 고를 이유가 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.domain.models import PlaceDetails
from app.errors import AppError
from app.place_search_policy import PLACE_SEARCH_LDONG_REGION_CODE
from app.providers.contracts import ProviderMetadata, ProviderStatus
from app.providers.protocols import PlaceDetailByNameProvider
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaceDetailQuery:
    """이미 해석된 장소명으로 상세정보를 조회한다.

    place_name은 ResolveLocationTool이 돌려준 resolved_name을 쓴다 — 사용자 발화
    그대로가 아니라 저장소·API 기준 명칭이라야 provider의 정확 일치 검색에 걸린다.
    """

    place_name: str
    region_code: str = PLACE_SEARCH_LDONG_REGION_CODE
    # 구는 요청에 싣지 않고 응답으로 거른다(D-025). nearby_place_details와 같다.
    district_code: str | None = None

    def __post_init__(self) -> None:
        if not self.place_name.strip():
            raise ValueError("place_name은 비어 있을 수 없습니다.")


@dataclass(frozen=True)
class PlaceDetailResult:
    status: ToolStatus
    details: PlaceDetails | None
    error: ToolError | None = None
    provider_metadata: tuple[ProviderMetadata, ...] = ()


class GetPlaceDetailTool:
    """장소명으로 상세정보 1건을 조회하고 실패를 ToolStatus로 정규화한다."""

    def __init__(self, place_provider: PlaceDetailByNameProvider) -> None:
        self._place_provider = place_provider

    async def execute(self, query: PlaceDetailQuery) -> PlaceDetailResult:
        try:
            result = await self._place_provider.find_details_by_name(
                query.place_name.strip(),
                region_code=query.region_code,
                district_code=query.district_code,
            )
        except AppError as exc:
            # 여기서 삼킨 오류는 200 응답으로 나가므로 로그가 유일한 흔적이다.
            logger.warning(
                "장소 상세 조회 실패 (place=%s, code=%s, provider=%s, details=%s)",
                query.place_name,
                exc.code,
                exc.provider,
                exc.details,
            )
            # provider는 이름이 정확히 일치하지 않으면 place_not_found(404)를 던진다.
            # 장애가 아니라 "그 장소를 못 찾았다"이므로 no_data로 낮춘다.
            if exc.status_code == 404:
                return PlaceDetailResult(
                    status=ToolStatus.NO_DATA,
                    details=None,
                    error=ToolError(
                        code="no_data",
                        message="장소 상세정보를 찾지 못했습니다.",
                        cause="place_not_found",
                        retryable=False,
                    ),
                )
            return PlaceDetailResult(
                status=ToolStatus.UNAVAILABLE,
                details=None,
                error=ToolError(
                    code="unavailable",
                    message="장소 상세정보를 가져오지 못했습니다.",
                    cause=(
                        "timeout" if exc.code == "provider_timeout" else "upstream_error"
                    ),
                    retryable=exc.retryable,
                ),
            )

        status = (
            ToolStatus.NO_DATA
            if result.metadata.status is ProviderStatus.NO_DATA
            else ToolStatus.SUCCESS
        )
        return PlaceDetailResult(
            status=status,
            details=result.data,
            error=None,
            provider_metadata=(result.metadata,),
        )


__all__ = [
    "GetPlaceDetailTool",
    "PlaceDetailQuery",
    "PlaceDetailResult",
]

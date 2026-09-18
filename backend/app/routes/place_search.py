"""위치 설정 화면의 장소 검색 API.

`GET /api/places/search`는 검색어 하나를 받아 Naver 지역 검색 후보를 서울 안으로
좁혀 돌려준다. 위치 설정 화면(프론트 `LocationPage`)에서 "어디를 기준으로 찾을지"를
사용자가 직접 고르게 하는 목록이다.

**서울 밖 후보는 내보내지 않는다.** 이 화면이 정하는 값은 사용자의 현재 위치가
아니라 추천을 찾을 위치이고, 지원 지역은 서울 25개 구다(`app.service_area`).
밖을 고를 수 있게 두면 그 뒤 추천이 이유 없이 "결과 없음"으로만 끝난다(D-044).

**검색어 앞에 "서울"을 붙여 부른다.** 지역 검색은 전국을 뒤지는데 한 번에 받을 수
있는 결과가 5건뿐이라(Naver API 상한, `providers/local_search.py`), "중앙동 카페"
같은 검색어에서는 지방 결과 다섯 개가 서울 결과를 통째로 밀어낸다. 사용자가 이미
"서울"이나 구 이름을 적었으면 그대로 둔다.

**후보가 하나도 없으면 `ResolveLocationTool`에 넘긴다.** 지역 검색은 상호·시설
이름만 찾아서 "율곡로 62" 같은 주소를 못 푼다(실측 2026-09-03: 0건). 그 도구가
저장소·지역 검색·지오코딩을 순서대로 쓰는 위치 해석 사다리를 이미 갖고 있으므로,
여기서 같은 사다리를 다시 만들지 않고 그대로 부른다 — 두 벌이 되면 같은 검색어에
답이 갈리고, 그쪽에 들어가는 개선(LLM 질의 정리 등)이 이 화면에 오지 않는다.

결과가 한 곳뿐인 것은 이 경로에서는 문제가 되지 않는다. 주소는 원래 한 곳이고,
고를 것이 여럿인 검색어는 위에서 이미 지역 검색이 목록으로 돌려줬다.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Query

from app.auth.dependency import OptionalPrincipal
from app.domain.models import LocalSearchPlace
from app.errors import AppError
from app.observability.api_usage import create_external_client
from app.providers.factory import (
    get_geocoding_provider,
    get_local_search_provider,
    get_place_location_repository,
)
from app.schemas import PlaceSearchCandidate, PlaceSearchResponse
from app.service_area import SUPPORTED_DISTRICTS, is_within_service_area
from app.tools.contracts import ToolStatus
from app.tools.resolve_location import (
    LocationPurpose,
    ResolveLocationQuery,
    ResolveLocationTool,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["place-search"])

# Naver 지역 검색이 한 번에 주는 최대 건수. providers/local_search.py가 6 이상을
# ValueError로 거부한다.
_DISPLAY = 5

# 이 말이 검색어에 이미 들어 있으면 "서울"을 덧붙이지 않는다. 구 이름을 목록에서
# 읽으므로 지원 구가 늘어도 여기를 고칠 일이 없다.
_SEOUL_KEYWORDS: tuple[str, ...] = ("서울",) + tuple(
    district.name for district in SUPPORTED_DISTRICTS
)


def seoul_scoped_query(query: str) -> str:
    """검색어를 서울로 좁힌다. 이미 서울을 가리키고 있으면 그대로 둔다."""
    if any(keyword in query for keyword in _SEOUL_KEYWORDS):
        return query
    return f"서울 {query}"


def _to_candidate(place: LocalSearchPlace) -> PlaceSearchCandidate | None:
    """서울 안의 장소면 응답 항목으로 바꾸고, 아니면 None."""
    if place.latitude is None or place.longitude is None:
        return None
    if not is_within_service_area(place.latitude, place.longitude):
        return None
    return PlaceSearchCandidate(
        name=place.name,
        address=place.address,
        road_address=place.road_address,
        category=place.category,
        latitude=place.latitude,
        longitude=place.longitude,
    )


async def _resolve_single_place(
    query: str, client: httpx.AsyncClient
) -> tuple[PlaceSearchCandidate | None, int]:
    """위치 해석 사다리로 한 곳을 푼다. (후보, 서울 밖이라 뺀 수)를 돌려준다.

    도구가 예외 대신 상태로 답한다 — 못 찾으면 NO_DATA, 서울 밖이면 UNSUPPORTED다.
    서울 밖은 개수로 세어 화면이 "서울 지역만 검색할 수 있어요"라고 말하게 한다.
    """
    tool = ResolveLocationTool(
        provider=get_geocoding_provider(client),
        place_repository=get_place_location_repository(client),
        local_search_provider=get_local_search_provider(client),
    )
    result = await tool.execute(
        ResolveLocationQuery(location_query=query, purpose=LocationPurpose.SEARCH_CENTER)
    )

    if result.status is ToolStatus.SUCCESS and result.location is not None:
        location = result.location
        return (
            PlaceSearchCandidate(
                name=location.resolved_name,
                address=location.address,
                road_address=None,
                category=location.place_category,
                latitude=location.latitude,
                longitude=location.longitude,
            ),
            0,
        )
    if result.status is ToolStatus.UNSUPPORTED:
        return None, 1
    if result.status is ToolStatus.UNAVAILABLE:
        # 외부 조회가 실패한 것을 "찾은 곳이 없어요"로 보여주면 사용자는 검색어를
        # 고치며 헤맨다. 실패는 실패로 알린다(D-042와 같은 방향).
        raise AppError(
            code=result.error.code if result.error else "provider_unavailable",
            message=(
                result.error.message
                if result.error
                else "장소를 찾는 중에 문제가 생겼어요. 잠시 후 다시 시도해주세요."
            ),
            status_code=503,
            retryable=True,
        )
    return None, 0


@router.get("/places/search", response_model=PlaceSearchResponse)
async def search_places(
    principal: OptionalPrincipal,
    query: Annotated[
        str, Query(min_length=1, max_length=100, description='검색어. 예: "안국역"')
    ],
) -> PlaceSearchResponse:
    """검색어로 서울 안의 장소 후보를 찾는다.

    좌표가 없는 후보는 검색 위치로 쓸 수 없어 뺀다. 서울 밖이라 뺀 수는 따로
    세어 돌려준다 — 화면이 "찾은 곳이 없어요"와 "서울 지역만 검색할 수 있어요"를
    갈라 말할 수 있어야 한다.
    """
    normalized_query = query.strip()
    # 공백만 들어온 요청은 여기서 끊는다. 그대로 두면 "서울 "만 남은 질의로 외부
    # API를 부르게 된다. Query(min_length=1)은 공백도 한 글자로 세서 못 막는다.
    if not normalized_query:
        raise AppError(
            code="invalid_request",
            message="검색할 장소를 입력해주세요.",
            status_code=400,
        )

    async with create_external_client() as client:
        provider = get_local_search_provider(client)
        result = await provider.search_places_by_name(
            seoul_scoped_query(normalized_query), display=_DISPLAY
        )

        candidates: list[PlaceSearchCandidate] = []
        outside_count = 0
        for place in result.data:
            candidate = _to_candidate(place)
            if candidate is None:
                # 좌표가 아예 없는 경우와 서울 밖인 경우를 가른다. 좌표 없는 후보는
                # 화면이 안내할 것이 없어 그냥 빠지지만, 서울 밖은 이유를 말해야 한다.
                if place.latitude is not None and place.longitude is not None:
                    outside_count += 1
                continue
            candidates.append(candidate)

        if not candidates and outside_count == 0:
            # 상호로는 못 찾았다. 주소이거나, 사다리의 다른 단계가 아는 이름일 수
            # 있다. 서울 밖이라 걸러낸 것이 있으면 이유가 이미 분명하므로 넘어간다.
            resolved, resolved_outside = await _resolve_single_place(normalized_query, client)
            if resolved is not None:
                candidates.append(resolved)
            outside_count += resolved_outside

    logger.info(
        "장소 검색: 질의=%s 후보=%d 서울밖=%d",
        normalized_query,
        len(candidates),
        outside_count,
    )
    return PlaceSearchResponse(
        places=candidates, outside_service_area_count=outside_count
    )

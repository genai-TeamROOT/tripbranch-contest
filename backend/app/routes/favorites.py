"""계정 단위 즐겨찾기 API 라우터. (위치 설정 화면, PR #361 후속)

역할: 위치 설정 화면에서 담은 장소를 계정에 붙여 보관하고 돌려준다.
입력: GET /api/favorites, PUT /api/favorites
출력: UserFavoritesResponse.
호출 시점: 프론트가 위치 설정 화면이나 사이드바를 열 때, 담거나 지울 때.

**/state/{session_id} 아래에 두지 않았다.** 즐겨찾기는 세션에 속하지 않는다 —
대화를 새로 시작해도 남아야 하는 값이라 경로에 session_id가 들어갈 자리가 없다.
`/preferences`와 같은 판단이다.

**RequiredPrincipal을 쓴다.** 신원이 곧 저장 키라, 토큰이 없으면 어디에 저장할지
자체가 정해지지 않는다. 401로 끊는 편이 "아무 데도 저장되지 않았는데 성공으로
보이는" 것보다 낫다. 게스트(익명) 신원도 그대로 받는다 — 게스트에게도 uid가 있어
즐겨찾기가 유지된다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.auth.dependency import RequiredPrincipal
from app.state import service as state_service
from app.state.schema import UserFavorite

router = APIRouter(tags=["favorites"])


class ReplaceFavoritesRequest(BaseModel):
    """즐겨찾기 전체 교체 요청.

    항목 단위 추가/삭제가 아니라 전체 교체인 이유는 화면이 목록을 통째로 다루기
    때문이다 — 순서가 있고 이름을 바꿀 수 있어서, 항목 하나의 변경도 결국 목록
    전체의 다음 상태로 표현된다. 빈 배열도 정상 요청이다(전부 지움).
    """

    items: list[UserFavorite] = Field(default_factory=list)


@router.get("/favorites", response_model=state_service.UserFavoritesResponse)
async def get_favorites(
    principal: RequiredPrincipal,
) -> state_service.UserFavoritesResponse:
    return state_service.get_user_favorites(principal.user_id)


@router.put("/favorites", response_model=state_service.UserFavoritesResponse)
async def replace_favorites(
    request: ReplaceFavoritesRequest,
    principal: RequiredPrincipal,
) -> state_service.UserFavoritesResponse:
    return state_service.replace_user_favorites(principal.user_id, request.items)

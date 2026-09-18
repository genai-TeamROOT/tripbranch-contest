"""계정 단위 취향 설정 API 라우터. (TP-222 후속)

역할: 취향 설정 화면에서 고른 값을 계정에 붙여 보관하고 돌려준다.
입력: GET /api/preferences, PUT /api/preferences
출력: UserPreferencesResponse.
호출 시점: 프론트가 취향 화면을 열거나 저장할 때, 홈이 저장된 취향을 그릴 때.

**/state/{session_id} 아래에 두지 않았다.** 취향은 세션에 속하지 않는다 — 대화를
새로 시작해도 유지돼야 하는 값이라 경로에 session_id가 들어갈 자리가 없다.

**이 프로젝트에서 RequiredPrincipal을 쓰는 첫 라우트다.** 다른 라우트는 전부
OptionalPrincipal이라 토큰 없는 요청도 통과하는데(Phase 4 전까지의 과도기),
여기서는 신원이 곧 저장 키라서 토큰이 없으면 어디에 저장할지 자체가 정해지지
않는다. 401로 끊는 편이 "아무 데도 저장되지 않았는데 성공으로 보이는" 것보다 낫다.

게스트(익명) 신원도 그대로 받는다. 게스트에게도 uid가 있어 취향이 유지되고,
계정으로 승계될 때(2차 범위) 옮길 대상이 분명해진다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.auth.dependency import RequiredPrincipal
from app.state import service as state_service
from app.state.schema import UserPreference

router = APIRouter(tags=["preferences"])


class ReplacePreferencesRequest(BaseModel):
    """취향 전체 교체 요청.

    항목 단위 추가/삭제가 아니라 전체 교체인 이유는 화면이 그렇게 동작하기
    때문이다 — 칩을 여러 개 고른 뒤 "저장"을 한 번 누르는 흐름이라 중간 상태를
    서버에 보낼 일이 없다. 빈 배열도 정상 요청이다(전부 해제).
    """

    items: list[UserPreference] = Field(default_factory=list)


@router.get("/preferences", response_model=state_service.UserPreferencesResponse)
async def get_preferences(
    principal: RequiredPrincipal,
) -> state_service.UserPreferencesResponse:
    return state_service.get_user_preferences(principal.user_id)


@router.put("/preferences", response_model=state_service.UserPreferencesResponse)
async def replace_preferences(
    request: ReplacePreferencesRequest,
    principal: RequiredPrincipal,
) -> state_service.UserPreferencesResponse:
    return state_service.replace_user_preferences(principal.user_id, request.items)

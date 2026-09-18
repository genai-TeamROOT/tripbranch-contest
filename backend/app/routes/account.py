"""회원 탈퇴 엔드포인트.

역할: DELETE /account — 요청한 본인의 계정과 데이터를 지운다.
입력: Authorization 헤더의 액세스 토큰(본문 없음).
출력: 지운 건수 요약.
호출 시점: 사이드바 계정 팝업의 "회원 탈퇴"(frontend/src/components/layout/SidebarAccount.tsx).

**RequiredPrincipal을 쓴다.** preferences.py·favorites.py와 같은 이유에 더해, 여기는
신원이 곧 삭제 대상이라 토큰이 없으면 아무것도 할 수 없다.

**본문을 받지 않는 것이 이 파일의 보안 경계다.** user_id를 본문이나 경로에서 받으면
남의 계정을 지울 수 있다. 서명 검증을 통과한 토큰의 sub만 쓴다(auth/verify.py).

**게스트는 부를 수 없다.** 게스트에게는 "탈퇴"라는 개념이 없고 — 지우면 되돌릴 계정이
없다 — 익명 계정은 만든 지 30일이 지나면 정기 정리가 걷어간다
(scripts/cleanup_anonymous_users.py). 그래서 is_anonymous면 403으로 막는다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.accounts.deletion import delete_account
from app.auth.admin import AccountAdminError
from app.auth.dependency import RequiredPrincipal
from app.errors import AppError
from app.state.errors import StateStoreError

router = APIRouter(tags=["account"])


class DeleteAccountResponse(BaseModel):
    """지운 건수. 화면은 쓰지 않지만 실패 조사에 필요해 남긴다."""

    deleted_sessions: int = Field(default=0)
    deleted_schedules: int = Field(default=0)


@router.delete("/account", response_model=DeleteAccountResponse)
async def delete_my_account(principal: RequiredPrincipal) -> DeleteAccountResponse:
    # **HTTPException이 아니라 AppError를 쓴다.** main.py의 HTTPException 핸들러는
    # detail을 버리고 "요청 내용을 확인해주세요"로 덮어써서, 서버 쪽 실패(503)인데도
    # 사용자 입력이 잘못된 것처럼 보인다. AppError만 message가 그대로 화면에 닿는다.
    if principal.is_anonymous:
        raise AppError(
            code="account_deletion_not_applicable",
            message="게스트는 탈퇴할 계정이 없어요.",
            status_code=403,
        )

    try:
        summary = delete_account(principal.user_id)
    except (StateStoreError, AccountAdminError) as exc:
        # **원문을 그대로 내보내지 않는다.** 저장소 오류 문자열에 내부 경로가 섞인다.
        # 데이터를 먼저 지우는 순서라, 여기서 실패해도 계정은 살아 있어 다시 누르면 된다.
        raise AppError(
            code="account_deletion_failed",
            message="탈퇴 처리를 마치지 못했어요. 잠시 후 다시 시도해주세요.",
            status_code=503,
            retryable=True,
        ) from exc

    return DeleteAccountResponse(
        deleted_sessions=summary.sessions,
        deleted_schedules=summary.schedules,
    )

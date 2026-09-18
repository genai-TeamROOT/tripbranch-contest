"""회원 탈퇴 — 계정과 그 계정의 데이터를 지운다.

역할: user_id 하나를 받아 데이터를 먼저 지우고 계정을 마지막에 지운다.
입력: user_id(서명 검증을 통과한 토큰의 sub).
출력: DeletionSummary(무엇을 몇 건 지웠는지).
호출 시점: DELETE /account (routes/account.py).

**순서가 이 파일의 전부다. 데이터 먼저, 계정 마지막.**
GoTrue(계정)와 PostgREST(데이터)는 다른 API라 한 트랜잭션으로 묶을 수 없다. 중간에
실패하는 것을 막을 수 없으니, 실패했을 때 복구 가능한 순서를 고른다.

  - 계정을 먼저 지우면: user_id를 다시 알아낼 방법이 사라져 남은 데이터가 영영
    고아가 된다. 사용자가 다시 시도할 수도 없다(토큰이 죽는다).
  - 데이터를 먼저 지우면: 계정이 남아 있어 다시 로그인해 또 누를 수 있고, 이미
    지운 것을 또 지워도 문제가 없다(모두 멱등).

**세션은 user_id가 붙은 것만 지운다.** agent_states.user_id가 없는 세션(가입 전
게스트 시절의 대화)은 여기서 못 찾는다 — 그쪽은 마지막 활동일 30일 기준의 정기
정리(scripts/cleanup_expired_sessions.py)가 맡는다.

**response_feedback은 지우지 않는다(2026-09-15 협의).** 세션 정리에서도 제외돼 있는
분석 데이터라 같은 취급을 한다. user_input·assistant_message에 발화가 남으므로
개인정보처리방침에 이 예외를 적어 두었다(TermsModal 제3조).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.admin import AccountAdminClient
from app.config import settings
from app.state.store import StateStore, get_store

# 일정 조회의 상한. 화면 목록과 달리 여기서는 "전부"가 필요해서 넉넉히 잡는다.
# 이보다 많으면 한 번에 다 못 지우지만, 남은 것은 다시 눌러 지울 수 있다(멱등).
# 세션은 상한 없는 전용 조회를 쓴다(list_session_ids_for_user).
_SWEEP_LIMIT = 1000


@dataclass(frozen=True)
class DeletionSummary:
    """무엇을 몇 건 지웠는지. 실패 조사용이라 사용자에게 그대로 보여주지 않는다."""

    sessions: int
    schedules: int


def _delete_session(store: StateStore, session_id: str) -> None:
    """세션에 딸린 것을 먼저 지우고 agent_states를 마지막에 지운다.

    cleanup_expired_sessions.py::_delete_one()과 같은 순서다 — agent_states를 먼저
    지우면 나머지 테이블의 행을 찾을 열쇠가 사라져 고아가 된다.
    """
    store.delete_change_logs(session_id)
    store.delete_traces(session_id)
    store.delete_session_messages(session_id)
    store.delete_history(session_id)
    store.delete_saved_places(session_id)
    store.delete_state(session_id)


def delete_account(user_id: str) -> DeletionSummary:
    """탈퇴를 실행한다. 데이터를 다 지운 뒤에만 계정을 지운다."""
    store = get_store()

    # 계정 단위 데이터. 빈 목록 저장이 아니라 행 삭제여야 한다 — 계정이 사라지면
    # user_id만 남은 행은 주인 없는 개인정보가 된다(store.py 주석 참고).
    store.delete_preferences(user_id)
    store.delete_favorites(user_id)

    schedules = store.list_schedules_for_user(user_id, _SWEEP_LIMIT)
    for schedule in schedules:
        store.delete_schedule(schedule.id)

    # 사이드바 목록(list_sessions_for_user)이 아니라 전용 조회를 쓴다 — 저쪽은 제목
    # 없는 세션을 거르는데, 그 기준이면 44%가 남는다(store.py 주석의 실측).
    session_ids = store.list_session_ids_for_user(user_id)
    for session_id in session_ids:
        _delete_session(store, session_id)

    # 여기까지 왔을 때만 계정을 지운다.
    admin = AccountAdminClient(settings.supabase_url, settings.supabase_secret_key)
    admin.delete_user(user_id)

    return DeletionSummary(sessions=len(session_ids), schedules=len(schedules))

"""만료된 익명 세션·이력을 정리한다 (TP-134, D-074).

역할: agent_states.last_active_at 기준으로 오래(기본 30일) 활동이 없는 세션을
찾아, 그 세션에 딸린 5개 테이블(agent_states/recommendation_histories/
condition_change_logs/trace_records/session_messages) 행을 전부 지운다. response_feedback은
세션 생애주기와 무관한 별도 분석 데이터라 대상에서 제외한다.

세션 TTL(30분, session.py::SESSION_TTL)은 세션이 다시 조회될 때만 상태를
'expired'로 바꾸는 lazy 판정이라 실제 행을 지우지 않는다 — 이 스크립트가
실제 삭제를 담당한다.

입력: --days(기준 일수, 기본 30), --dry-run(삭제 없이 대상만 출력).
출력: 정리한(또는 정리할) 세션 수, 실패한 세션 id.
호출 시점: `python -m scripts.cleanup_expired_sessions` (수동 실행), 그리고
.github/workflows/cleanup-sessions.yml이 매일 자동으로 호출한다(2026-09-15, D+C
협의). --days를 넘기지 않는 한 방침 문구("마지막 이용일로부터 30일")와 기본값이
같은 값을 가리킨다.

**auth.users(익명 계정) 정리(scripts/cleanup_anonymous_users.py)는 여기 함께
자동화하지 않는다.** 계정 기준(생성일)과 이 스크립트의 기준(마지막 활동일)이
갈려서, 계정 정리를 무작정 같이 돌리면 아직 쓰고 있는 게스트의 계정이 먼저
지워지고 세션만 고아로 남을 수 있다.

STATE_STORE_BACKEND=memory에서는 프로세스가 재시작되면 데이터가 사라지므로
이 스크립트가 의미 없다 — supabase가 아니면 즉시 종료한다.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import timedelta

from app.config import settings
from app.state.errors import StateStoreError
from app.state.schema import now_kst
from app.state.store import StateStore, get_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="만료된 익명 세션·이력 정리")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="이 일수보다 오래 활동이 없는 세션을 정리 대상으로 삼는다 (기본 30일)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="삭제하지 않고 정리 대상 세션 수·id만 출력한다",
    )
    return parser


def _delete_one(store: StateStore, session_id: str) -> None:
    """자식 데이터부터 지우고 agent_states를 마지막에 지운다.

    도중에 실패해도 agent_states 행이 남아 있으면 다음 실행이 같은 session_id를
    다시 정리 대상으로 찾아낸다(list_stale_session_ids가 agent_states 기준이라)
    — agent_states를 먼저 지우면 나머지 테이블의 행이 영원히 못 찾는 고아가 된다.
    """
    store.delete_change_logs(session_id)
    store.delete_traces(session_id)
    # 화면 기록도 세션 수명에 묶여 있다(TP-222 후속). 여기서 지우지 않으면
    # 세션 행이 사라진 뒤에도 대화 원문이 남는다.
    store.delete_session_messages(session_id)
    store.delete_history(session_id)
    # 보관함도 세션 수명에 묶여 있다(SCHEDULE-12).
    store.delete_saved_places(session_id)
    store.delete_state(session_id)


def cleanup(days: int, dry_run: bool) -> int:
    """정리를 실행하고 실패한 세션 수를 반환한다."""
    if settings.state_store_backend != "supabase":
        raise SystemExit(
            "STATE_STORE_BACKEND=supabase가 아닙니다 — memory 백엔드는 재시작하면"
            " 어차피 비워지므로 이 스크립트가 필요 없습니다."
        )

    store = get_store()
    cutoff = now_kst() - timedelta(days=days)
    stale_ids = store.list_stale_session_ids(cutoff)

    if not stale_ids:
        print(f"{cutoff.isoformat()} 이전 활동 세션 없음. 정리할 것이 없습니다.")
        return 0

    if dry_run:
        print(
            f"[dry-run] {len(stale_ids)}개 세션이 정리 대상입니다 "
            f"(기준: {cutoff.isoformat()})."
        )
        for session_id in stale_ids:
            print(f"  - {session_id}")
        return 0

    failed: list[str] = []
    for session_id in stale_ids:
        try:
            _delete_one(store, session_id)
        except StateStoreError as exc:
            failed.append(session_id)
            print(f"  실패: {session_id} ({exc})")

    succeeded = len(stale_ids) - len(failed)
    print(
        f"{succeeded}/{len(stale_ids)}개 세션을 정리했습니다 "
        f"(기준: {cutoff.isoformat()})."
    )
    if failed:
        print(f"{len(failed)}개 실패 — 다음 실행에서 다시 시도됩니다.")
    return len(failed)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    failed_count = cleanup(args.days, args.dry_run)
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())

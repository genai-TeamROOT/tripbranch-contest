"""만료된 익명 세션 정리 스크립트(TP-134) 테스트."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import Settings
from app.state import saved_schedules
from app.state.schema import (
    AgentState,
    ConditionChangeLog,
    RecommendationHistory,
    TraceRecord,
    now_kst,
)
from app.state.store import InMemoryStateStore
from scripts import cleanup_expired_sessions as script


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryStateStore:
    memory_store = InMemoryStateStore()
    monkeypatch.setattr(script, "get_store", lambda: memory_store)
    monkeypatch.setattr(
        script, "settings", Settings(_env_file=None, state_store_backend="supabase")
    )
    return memory_store


# 저장한 일정은 계정 단위라 user_id가 필요하다. 세션마다 다른 사람이 아니라
# 한 사람이 여러 세션을 쓴 것으로 둔다 — 정리는 세션 단위로 돈다.
_USER_ID = "3f1a9c04-0000-4000-8000-000000000001"


def _seed_session(store: InMemoryStateStore, session_id: str, *, days_old: int) -> None:
    last_active_at = now_kst() - timedelta(days=days_old)
    store.save_state(AgentState(session_id=session_id, last_active_at=last_active_at))
    store.save_history(RecommendationHistory(session_id=session_id))
    store.append_change_logs(
        [
            ConditionChangeLog(
                session_id=session_id, run_id="r1", seq=0, op="Update", field="budget"
            )
        ]
    )
    store.append_traces(
        [TraceRecord(session_id=session_id, run_id="r1", trace_id="t1", step="interpret")]
    )
    # 그 세션에서 저장한 일정. **세션 수명에 묶이지 않는다** — 아래 테스트가
    # 그 사실을 잠근다(TP-233 완료 조건 5).
    saved_schedules.save(
        store,
        _USER_ID,
        title=f"{session_id}에서 저장한 일정",
        payload={"items": [], "total_duration_min": 0},
        session_id=session_id,
        # 세션마다 다른 run_id를 준다 — save()는 (user_id, run_id)로 멱등이라
        # 같은 값을 쓰면 세 세션이 한 줄로 합쳐져 아래 개수 단정이 무의미해진다.
        run_id=f"run_{session_id}",
    )


def test_memory_백엔드는_즉시_종료한다(monkeypatch: pytest.MonkeyPatch, store) -> None:
    monkeypatch.setattr(
        script, "settings", Settings(_env_file=None, state_store_backend="memory")
    )

    with pytest.raises(SystemExit):
        script.cleanup(days=30, dry_run=False)


def test_대상이_없으면_아무것도_하지_않는다(store) -> None:
    assert script.cleanup(days=30, dry_run=False) == 0


def test_오래된_세션의_네_테이블을_전부_지운다(store) -> None:
    _seed_session(store, "sess_old", days_old=40)

    failed = script.cleanup(days=30, dry_run=False)

    assert failed == 0
    assert store.get_state("sess_old") is None
    assert store.get_history("sess_old") is None
    assert store.get_change_logs("sess_old") == []
    assert store.get_traces("sess_old") == []


def test_최근_세션은_지우지_않는다(store) -> None:
    _seed_session(store, "sess_recent", days_old=1)

    script.cleanup(days=30, dry_run=False)

    assert store.get_state("sess_recent") is not None
    assert store.get_history("sess_recent") is not None
    assert len(store.get_change_logs("sess_recent")) == 1
    assert len(store.get_traces("sess_recent")) == 1


def test_오래된_세션과_최근_세션이_섞여도_오래된_것만_지운다(store) -> None:
    _seed_session(store, "sess_old", days_old=40)
    _seed_session(store, "sess_recent", days_old=1)

    script.cleanup(days=30, dry_run=False)

    assert store.get_state("sess_old") is None
    assert store.get_state("sess_recent") is not None


def test_dry_run은_아무것도_지우지_않는다(store) -> None:
    _seed_session(store, "sess_old", days_old=40)

    failed = script.cleanup(days=30, dry_run=True)

    assert failed == 0
    assert store.get_state("sess_old") is not None
    assert store.get_change_logs("sess_old") != []


def test_days_인자로_기준을_조정할_수_있다(store) -> None:
    """10일 전 세션은 --days 30 기준으로는 안 지워지고 --days 5 기준으로는 지워진다."""
    _seed_session(store, "sess_10일전", days_old=10)

    script.cleanup(days=30, dry_run=False)
    assert store.get_state("sess_10일전") is not None

    script.cleanup(days=5, dry_run=False)
    assert store.get_state("sess_10일전") is None


# ---------------------------------------------------------------- 저장한 일정
#
# 저장한 일정은 **계정에 딸려 있다.** 세션이 30일 정리로 사라져도 남아야 한다 —
# 사용자가 이름을 붙여 저장한 것이 조용히 사라지면 그것은 저장이 아니다
# (202609030005_create_saved_schedules.sql 주석, TP-233).
#
# 지금 이 규칙은 `_delete_one()`이 지우는 목록에 saved_schedules가 **없다는
# 것으로만** 성립한다. 부재는 테스트가 못 잡는다 — 누가 거기에 한 줄을 더해도
# 다른 어떤 테스트도 깨지지 않는다. 그래서 여기서 명시적으로 잠근다.


def test_세션을_지워도_저장한_일정은_남는다(store) -> None:
    """30일 정리 대상 세션에서 저장한 일정이라도 지우지 않는다. (TP-233)"""
    _seed_session(store, "sess_old", days_old=40)
    assert len(saved_schedules.list_for_user(store, _USER_ID)) == 1

    failed = script.cleanup(days=30, dry_run=False)

    assert failed == 0
    # 세션 쪽은 전부 사라졌다 — 대조군이다. 이게 없으면 정리가 아예 안 돌아도
    # 아래 단정이 통과한다.
    assert store.get_state("sess_old") is None
    assert store.get_history("sess_old") is None

    remaining = saved_schedules.list_for_user(store, _USER_ID)
    assert len(remaining) == 1
    assert remaining[0].title == "sess_old에서 저장한 일정"
    # 출처 표시는 남는다. 세션이 사라진 뒤에도 "없을 수 있다"를 전제로 다루는
    # 값이지, 정리 때 비우는 값이 아니다.
    assert remaining[0].session_id == "sess_old"


def test_여러_세션이_정리돼도_저장한_일정은_전부_남는다(store) -> None:
    """세션 단위로 도는 정리가 계정 단위 저장소를 건드리지 않는지 본다. (TP-233)"""
    _seed_session(store, "sess_old_1", days_old=40)
    _seed_session(store, "sess_old_2", days_old=50)
    _seed_session(store, "sess_recent", days_old=1)

    script.cleanup(days=30, dry_run=False)

    assert store.get_state("sess_old_1") is None
    assert store.get_state("sess_old_2") is None
    assert store.get_state("sess_recent") is not None
    assert len(saved_schedules.list_for_user(store, _USER_ID)) == 3

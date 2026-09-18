"""인메모리 State 저장소.

계약 문서: docs/package-b/agent-state-contract-v1.md (Phase 1 전제)
"""

from datetime import timedelta

import pytest

from app.state.schema import (
    AgentState,
    ConditionChangeLog,
    RecommendationHistory,
    RecommendedItem,
    SavedPlaceItem,
    SavedPlaceList,
    TraceRecord,
    UserConditions,
    now_kst,
)
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


class TestStateCrud:
    def test_저장한_State를_조회한다(self, store):
        state = AgentState(
            session_id="sess_A", user_conditions=UserConditions(budget="free")
        )
        store.save_state(state)

        got = store.get_state("sess_A")
        assert got is not None
        assert got.user_conditions.budget == "free"

    def test_없는_세션은_None을_반환한다(self, store):
        assert store.get_state("sess_없음") is None

    def test_삭제하면_조회되지_않는다(self, store):
        store.save_state(AgentState(session_id="sess_A"))
        store.delete_state("sess_A")
        assert store.get_state("sess_A") is None

    def test_없는_세션_삭제는_오류가_아니다(self, store):
        store.delete_state("sess_없음")

    def test_같은_세션_저장은_덮어쓴다(self, store):
        store.save_state(AgentState(session_id="sess_A", condition_version=1))
        store.save_state(AgentState(session_id="sess_A", condition_version=2))

        assert store.get_state("sess_A").condition_version == 2


class TestIsolation:
    """저장소 내부 객체가 외부로 새지 않는지 검사한다.

    인메모리 구현에서 참조를 그대로 반환하면 save 없이도 변경이 반영되어,
    Phase 2에서 DB로 교체할 때 동작이 달라진다.
    """

    def test_조회한_객체를_수정해도_저장소에_반영되지_않는다(self, store):
        store.save_state(AgentState(session_id="sess_A"))

        got = store.get_state("sess_A")
        got.condition_version = 999

        assert store.get_state("sess_A").condition_version == 0

    def test_저장한_원본을_수정해도_저장소에_반영되지_않는다(self, store):
        state = AgentState(session_id="sess_A")
        store.save_state(state)

        state.condition_version = 777

        assert store.get_state("sess_A").condition_version == 0

    def test_중첩된_조건_객체도_격리된다(self, store):
        store.save_state(
            AgentState(
                session_id="sess_A",
                user_conditions=UserConditions(place_tags=["카페"]),
            )
        )

        got = store.get_state("sess_A")
        got.user_conditions.place_tags.append("박물관")

        assert store.get_state("sess_A").user_conditions.place_tags == ["카페"]

    def test_조회할_때마다_다른_객체를_반환한다(self, store):
        store.save_state(AgentState(session_id="sess_A"))
        assert store.get_state("sess_A") is not store.get_state("sess_A")


class TestSessionSeparation:
    def test_세션별로_State가_분리된다(self, store):
        store.save_state(
            AgentState(
                session_id="sess_A", user_conditions=UserConditions(budget="free")
            )
        )
        store.save_state(AgentState(session_id="sess_B"))

        assert store.get_state("sess_A").user_conditions.budget == "free"
        assert store.get_state("sess_B").user_conditions.budget is None

    def test_한_세션_삭제가_다른_세션에_영향을_주지_않는다(self, store):
        store.save_state(AgentState(session_id="sess_A"))
        store.save_state(AgentState(session_id="sess_B"))

        store.delete_state("sess_A")

        assert store.get_state("sess_B") is not None


class TestHistory:
    def test_이력을_저장하고_조회한다(self, store):
        history = RecommendationHistory(
            session_id="sess_A",
            recommended=[RecommendedItem(place_id="126511", run_id="r1", rank=1)],
        )
        store.save_history(history)

        got = store.get_history("sess_A")
        assert len(got.recommended) == 1
        assert got.recommended[0].place_id == "126511"

    def test_이력도_격리된다(self, store):
        store.save_history(RecommendationHistory(session_id="sess_A"))

        got = store.get_history("sess_A")
        got.recommended.append(RecommendedItem(place_id="x", run_id="r", rank=1))

        assert store.get_history("sess_A").recommended == []

    def test_없는_세션의_이력은_None이다(self, store):
        assert store.get_history("sess_없음") is None


class TestSavedPlaces:
    """보관함은 이력과 별도 엔티티다. (SCHEDULE-12)"""

    def test_보관함을_저장하고_조회한다(self, store):
        store.save_saved_places(
            SavedPlaceList(
                session_id="sess_A",
                items=[
                    SavedPlaceItem(
                        place_id="126511", name="경복궁", saved_from_run_id="r1"
                    )
                ],
            )
        )

        got = store.get_saved_places("sess_A")
        assert [item.place_id for item in got.items] == ["126511"]
        assert got.items[0].name == "경복궁"

    def test_보관함도_격리된다(self, store):
        store.save_saved_places(SavedPlaceList(session_id="sess_A"))

        got = store.get_saved_places("sess_A")
        got.items.append(
            SavedPlaceItem(place_id="x", name="x", saved_from_run_id="r")
        )

        assert store.get_saved_places("sess_A").items == []

    def test_없는_세션의_보관함은_None이다(self, store):
        assert store.get_saved_places("sess_없음") is None

    def test_보관함_삭제가_이력에_영향을_주지_않는다(self, store):
        store.save_history(
            RecommendationHistory(
                session_id="sess_A",
                recommended=[RecommendedItem(place_id="126511", run_id="r1", rank=1)],
            )
        )
        store.save_saved_places(SavedPlaceList(session_id="sess_A"))

        store.delete_saved_places("sess_A")

        assert store.get_saved_places("sess_A") is None
        assert store.get_history("sess_A") is not None


class TestChangeLog:
    def test_기록이_누적된다(self, store):
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r2", seq=0, op="Remove", field="budget"
                )
            ]
        )

        logs = store.get_change_logs("sess_A")
        assert [(log.run_id, log.op) for log in logs] == [
            ("r1", "Update"),
            ("r2", "Remove"),
        ]

    def test_기록은_세션별로_분리된다(self, store):
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )
        assert store.get_change_logs("sess_B") == []

    def test_기록이_없으면_빈_리스트다(self, store):
        assert store.get_change_logs("sess_없음") == []

    def test_빈_리스트_추가는_오류가_아니다(self, store):
        store.append_change_logs([])
        assert store.get_change_logs("sess_A") == []

    def test_append_change_logs에는_개별_행을_수정_삭제하는_경로가_없다(self, store):
        """append_change_logs/get_change_logs 자체는 여전히 추가·조회만 한다.

        (계약 2.8절) 세션 전체를 지우는 delete_change_logs(TP-134, 아래
        TestCleanup)는 별개의 정리 전용 경로이며, 개별 기록을 골라 수정·삭제하는
        기능은 여전히 없다.
        """
        assert not hasattr(store, "update_change_log")
        assert not hasattr(store, "delete_change_log")


class TestCleanup:
    """TP-134 — 만료된 세션 정리.

    list_stale_session_ids/delete_change_logs/delete_traces는 정리 스크립트
    (scripts/cleanup_expired_sessions.py) 전용 경로다.
    """

    def test_cutoff보다_오래된_세션만_찾는다(self, store):
        old_time = now_kst() - timedelta(days=40)
        recent_time = now_kst() - timedelta(days=1)
        store.save_state(AgentState(session_id="sess_old", last_active_at=old_time))
        store.save_state(
            AgentState(session_id="sess_recent", last_active_at=recent_time)
        )

        cutoff = now_kst() - timedelta(days=30)

        assert store.list_stale_session_ids(cutoff) == ["sess_old"]

    def test_대상이_없으면_빈_리스트다(self, store):
        assert store.list_stale_session_ids(now_kst()) == []

    def test_delete_change_logs가_세션의_기록을_전부_지운다(self, store):
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )

        store.delete_change_logs("sess_A")

        assert store.get_change_logs("sess_A") == []

    def test_delete_change_logs는_다른_세션에_영향을_주지_않는다(self, store):
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_B", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )

        store.delete_change_logs("sess_A")

        assert len(store.get_change_logs("sess_B")) == 1

    def test_delete_traces가_세션의_기록을_전부_지운다(self, store):
        store.append_traces(
            [TraceRecord(session_id="sess_A", run_id="r1", trace_id="t1", step="interpret")]
        )

        store.delete_traces("sess_A")

        assert store.get_traces("sess_A") == []

    def test_없는_세션의_정리_삭제는_오류가_아니다(self, store):
        store.delete_change_logs("sess_없음")
        store.delete_traces("sess_없음")


class TestClear:
    def test_전체_초기화된다(self, store):
        store.save_state(AgentState(session_id="sess_A"))
        store.save_history(RecommendationHistory(session_id="sess_A"))
        store.append_change_logs(
            [
                ConditionChangeLog(
                    session_id="sess_A", run_id="r1", seq=0, op="Update", field="budget"
                )
            ]
        )

        store.clear()

        assert store.get_state("sess_A") is None
        assert store.get_history("sess_A") is None
        assert store.get_change_logs("sess_A") == []
        assert store.session_ids() == []
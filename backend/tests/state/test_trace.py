"""Trace 기록 시나리오.

계약 문서: docs/package-b/llmops-trace-contract-v1.md
"""

from datetime import timedelta

import pytest

from app.state import service as svc
from app.state import trace as trace_module
from app.state.schema import TraceRecord, now_kst
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


def record_trace(store, **kwargs) -> svc.RecordTraceResponse:
    """실행 단계 기록 호출. 테스트 편의용 헬퍼."""
    kwargs.setdefault("session_id", "sess_test")
    kwargs.setdefault("run_id", "run_test")
    kwargs.setdefault("step", "llm_interpret")
    return svc.record_trace(svc.RecordTraceRequest(**kwargs), store=store)


class TestRecordTrace:
    def test_trace_id를_발급하고_반환한다(self, store):
        response = record_trace(store)

        assert response.trace_id.startswith("trace_")

    def test_호출마다_다른_trace_id가_발급된다(self, store):
        first = record_trace(store)
        second = record_trace(store)

        assert first.trace_id != second.trace_id

    def test_전달한_값이_그대로_저장된다(self, store):
        record_trace(
            store,
            step="scoring",
            prompt_version="intent_v1.2",
            scoring_version="score_v0.3",
            variant_id="variant_b",
            latency_ms=120,
            token_usage=350,
            error_type=None,
        )

        [saved] = trace_module.get_traces(store, "sess_test")
        assert saved.step == "scoring"
        assert saved.prompt_version == "intent_v1.2"
        assert saved.scoring_version == "score_v0.3"
        assert saved.variant_id == "variant_b"
        assert saved.latency_ms == 120
        assert saved.token_usage == 350
        assert saved.error_type is None

    def test_임의의_step_문자열도_거부하지_않는다(self, store):
        """B는 step 이름의 의미를 판단하지 않는다. (경계 원칙)"""
        record_trace(store, step="anything_the_caller_wants")

        [saved] = trace_module.get_traces(store, "sess_test")
        assert saved.step == "anything_the_caller_wants"

    def test_선택_필드를_생략하면_None으로_남는다(self, store):
        record_trace(store, step="tool_fetch")

        [saved] = trace_module.get_traces(store, "sess_test")
        assert saved.prompt_version is None
        assert saved.scoring_version is None
        assert saved.variant_id is None
        assert saved.latency_ms is None
        assert saved.token_usage is None
        assert saved.error_type is None

    def test_오류_기록도_받는다(self, store):
        record_trace(store, step="llm_interpret", error_type="timeout")

        [saved] = trace_module.get_traces(store, "sess_test")
        assert saved.error_type == "timeout"


class TestAppendOnly:
    def test_같은_세션에서_여러_건이_순서대로_쌓인다(self, store):
        record_trace(store, run_id="run_1", step="llm_interpret")
        record_trace(store, run_id="run_1", step="tool_fetch")
        record_trace(store, run_id="run_2", step="scoring")

        saved = trace_module.get_traces(store, "sess_test")
        assert [t.step for t in saved] == ["llm_interpret", "tool_fetch", "scoring"]

    def test_기존_기록을_지우는_메서드가_없다(self, store):
        assert not hasattr(store, "delete_trace")


class TestSessionIsolation:
    def test_다른_세션의_기록은_섞이지_않는다(self, store):
        record_trace(store, session_id="sess_a", step="llm_interpret")
        record_trace(store, session_id="sess_b", step="scoring")

        assert [t.step for t in trace_module.get_traces(store, "sess_a")] == [
            "llm_interpret"
        ]
        assert [t.step for t in trace_module.get_traces(store, "sess_b")] == [
            "scoring"
        ]

    def test_기록이_없는_세션은_빈_목록을_반환한다(self, store):
        assert trace_module.get_traces(store, "sess_never_used") == []


class TestTraceStats:
    """svc.get_trace_stats()의 집계 결과를 확인한다. (TP-157)"""

    def test_비어있으면_전부_비어있다(self, store):
        response = svc.get_trace_stats(store=store)

        assert response.total == 0
        assert response.step_stats == []
        assert response.recent_errors == []

    def test_step별로_묶어서_건수를_센다(self, store):
        record_trace(store, session_id="sess_a", step="llm_interpret")
        record_trace(store, session_id="sess_b", step="llm_interpret")
        record_trace(store, session_id="sess_c", step="scoring")

        response = svc.get_trace_stats(store=store)

        by_step = {s.step: s.count for s in response.step_stats}
        assert by_step == {"llm_interpret": 2, "scoring": 1}
        assert response.total == 3

    def test_등장한_step만_담긴다(self, store):
        """reason_code_counts와 달리 step은 고정된 값 집합이 없다."""
        record_trace(store, step="tool_fetch")

        response = svc.get_trace_stats(store=store)

        assert [s.step for s in response.step_stats] == ["tool_fetch"]

    def test_평균과_최대_latency를_계산한다(self, store):
        record_trace(store, session_id="sess_a", step="scoring", latency_ms=100)
        record_trace(store, session_id="sess_b", step="scoring", latency_ms=300)

        response = svc.get_trace_stats(store=store)

        [stat] = response.step_stats
        assert stat.avg_latency_ms == 200
        assert stat.max_latency_ms == 300

    def test_latency가_없는_행은_평균_계산에서_빠진다(self, store):
        record_trace(store, session_id="sess_a", step="scoring", latency_ms=100)
        record_trace(store, session_id="sess_b", step="scoring", latency_ms=None)

        response = svc.get_trace_stats(store=store)

        [stat] = response.step_stats
        assert stat.avg_latency_ms == 100
        assert stat.max_latency_ms == 100

    def test_latency가_한_건도_없으면_None이다(self, store):
        record_trace(store, step="scoring", latency_ms=None)

        response = svc.get_trace_stats(store=store)

        [stat] = response.step_stats
        assert stat.avg_latency_ms is None
        assert stat.max_latency_ms is None

    def test_step별_에러_건수를_센다(self, store):
        record_trace(store, session_id="sess_a", step="llm_interpret", error_type="timeout")
        record_trace(store, session_id="sess_b", step="llm_interpret")
        record_trace(store, session_id="sess_c", step="scoring")

        response = svc.get_trace_stats(store=store)

        by_step = {s.step: s.error_count for s in response.step_stats}
        assert by_step == {"llm_interpret": 1, "scoring": 0}

    def test_최근_에러가_최신순으로_담긴다(self, store):
        now = now_kst()
        store.append_traces(
            [
                TraceRecord(
                    session_id="sess_a",
                    run_id="run_1",
                    trace_id="trace_1",
                    step="llm_interpret",
                    error_type="timeout",
                    recorded_at=now - timedelta(minutes=10),
                ),
                TraceRecord(
                    session_id="sess_b",
                    run_id="run_2",
                    trace_id="trace_2",
                    step="scoring",
                    error_type="value_error",
                    recorded_at=now,
                ),
            ]
        )

        response = svc.get_trace_stats(store=store)

        assert [e.session_id for e in response.recent_errors] == ["sess_b", "sess_a"]

    def test_에러가_없는_행은_최근_에러_목록에_안_담긴다(self, store):
        record_trace(store, step="llm_interpret")

        response = svc.get_trace_stats(store=store)

        assert response.recent_errors == []

    def test_최근_에러_limit을_넘으면_잘린다(self, store):
        for i in range(5):
            record_trace(
                store,
                session_id=f"sess_{i}",
                run_id=f"run_{i}",
                step="llm_interpret",
                error_type="timeout",
            )

        response = svc.get_trace_stats(store=store, recent_errors_limit=3)

        assert len(response.recent_errors) == 3

    def test_since까지만_필터하면_그_전_기록은_빠진다(self, store):
        now = now_kst()
        store.append_traces(
            [
                TraceRecord(
                    session_id="sess_old",
                    run_id="run_1",
                    trace_id="trace_1",
                    step="scoring",
                    recorded_at=now - timedelta(days=10),
                ),
                TraceRecord(
                    session_id="sess_new",
                    run_id="run_2",
                    trace_id="trace_2",
                    step="scoring",
                    recorded_at=now,
                ),
            ]
        )

        response = svc.get_trace_stats(store=store, since=now - timedelta(days=1))

        assert response.total == 1

    def test_until은_그_시각_이전까지만_포함한다(self, store):
        now = now_kst()
        store.append_traces(
            [
                TraceRecord(
                    session_id="sess_before",
                    run_id="run_1",
                    trace_id="trace_1",
                    step="scoring",
                    recorded_at=now - timedelta(days=1),
                ),
                TraceRecord(
                    session_id="sess_after",
                    run_id="run_2",
                    trace_id="trace_2",
                    step="scoring",
                    recorded_at=now + timedelta(days=1),
                ),
            ]
        )

        response = svc.get_trace_stats(store=store, until=now)

        assert response.total == 1

    def test_since와_until이_응답에도_그대로_담긴다(self, store):
        since = now_kst() - timedelta(days=7)
        until = now_kst()

        response = svc.get_trace_stats(store=store, since=since, until=until)

        assert response.since == since
        assert response.until == until


class TestTraceMetrics:
    """도메인 지표를 담는 자리. (TP-242)

    B는 키·값의 의미를 판단하지 않는다 — 계약 1절의 경계 원칙을 지표에도
    그대로 적용한다.
    """

    def test_지표를_그대로_저장한다(self, store):
        record_trace(
            store,
            step="schedule_quality",
            metrics={"time_budget_status": "over", "time_budget_delta_min": 80},
        )

        saved = trace_module.get_traces(store, "sess_test")[0]

        assert saved.metrics == {
            "time_budget_status": "over",
            "time_budget_delta_min": 80,
        }

    def test_넘기지_않으면_None이다(self, store):
        """지표를 안 싣는 단계(llm_interpret·tool·scoring)는 계속 None이다.
        빈 객체로 채우면 "지표가 없는 단계"와 "지표가 비어 있는 턴"을 구분할 수
        없다."""

        record_trace(store, step="scoring")

        assert trace_module.get_traces(store, "sess_test")[0].metrics is None

    def test_키를_검증하지_않는다(self, store):
        """무엇을 셀지는 그 기능을 아는 쪽이 정한다 — B가 목록을 들고 있으면
        지표를 늘릴 때마다 B를 고쳐야 한다."""

        record_trace(store, step="whatever", metrics={"처음_보는_키": [1, 2, 3]})

        assert trace_module.get_traces(store, "sess_test")[0].metrics == {
            "처음_보는_키": [1, 2, 3]
        }

    def test_지표가_붙어도_다른_필드는_그대로다(self, store):
        record_trace(
            store,
            step="schedule_quality",
            latency_ms=120,
            prompt_version="schedule.plan@2.0.0",
            metrics={"item_count": 3},
        )

        saved = trace_module.get_traces(store, "sess_test")[0]

        assert saved.latency_ms == 120
        assert saved.prompt_version == "schedule.plan@2.0.0"
        assert saved.metrics == {"item_count": 3}

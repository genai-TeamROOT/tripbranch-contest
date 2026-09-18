"""Package B - 실행 Trace 기록.

계약 문서: docs/package-b/llmops-trace-contract-v1.md

trace_id는 run 내부 한 단계(LLM 호출·Tool 호출·Scoring 등)를 가리킨다.
B는 step 이름이나 버전 값의 의미를 해석하지 않고, 호출자(A/C/D)가 넘긴
그대로 저장한다. (agent-state-contract-v1.md 1절 경계 원칙과 동일)
"""

from datetime import datetime
from typing import Any

from app.state.schema import TraceRecord, now_kst
from app.state.session import new_trace_id
from app.state.store import StateStore


def record(
    store: StateStore,
    session_id: str,
    run_id: str,
    step: str,
    *,
    prompt_version: str | None = None,
    scoring_version: str | None = None,
    variant_id: str | None = None,
    latency_ms: int | None = None,
    token_usage: int | None = None,
    error_type: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> TraceRecord:
    """실행 단계 1건을 기록한다. (llmops-trace-contract-v1.md 3·4절)

    trace_id는 여기서 발급한다 — 단계가 끝난 시점에 호출되므로
    latency_ms 등 결과값을 이미 들고 있는 상태로 들어온다.
    """
    trace = TraceRecord(
        session_id=session_id,
        run_id=run_id,
        trace_id=new_trace_id(),
        step=step,
        prompt_version=prompt_version,
        scoring_version=scoring_version,
        variant_id=variant_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        error_type=error_type,
        metrics=metrics,
        recorded_at=now_kst(),
    )
    store.append_traces([trace])
    return trace


def get_traces(store: StateStore, session_id: str) -> list[TraceRecord]:
    """세션의 trace 기록 전체를 조회한다. append-only이므로 순서를 보존한다."""
    return store.get_traces(session_id)


def list_for_stats(
    store: StateStore, since: datetime | None = None, until: datetime | None = None
) -> list[TraceRecord]:
    """집계(TP-157)를 위해 세션을 가리지 않고 전체 trace를 모은다.

    get_traces와 달리 세션 하나로 좁히지 않는다 — step별 평균 지연시간이나
    최근 에러처럼 세션 단위로 물을 수 없는 질문에 답하기 위함이다.
    since/until은 recorded_at 기준 반열린구간이며 둘 다 선택이다.
    """
    return store.list_traces_for_stats(since=since, until=until)


__all__ = ["record", "get_traces", "list_for_stats"]

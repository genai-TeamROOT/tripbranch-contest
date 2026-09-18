"""인텐트 라우팅 그래프 — 조기 반환 경로(Tool/Scoring 없이 끝나는 턴)를 맡는다.

설계·단계 계획은 docs/design/langgraph-adoption.md 참고. 지금 범위는 §6.1의
2단계다 — 분류·조건 병합은 여전히 `run_agent_flow()`가 하고, 그 뒤 답변 문구를
만드는 일만 이 그래프가 맡는다. RECOMMEND/MODIFY/SCHEDULE은 Tool·Scoring이 붙어
있어 기존 경로 그대로다(3단계 대상).

**이 그래프는 출력이 기존과 같아야 한다.** 구조만 옮기는 작업이라 응답 문자열이나
SSE 이벤트 순서가 달라지면 그건 버그다(§6.2).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.observability.langfuse_tracing import observe_step, trace_attributes
from app.providers.protocols import LLMProvider
from app.schemas import AgentResponse, ConversationTurnView, LLMOutput
from app.services.runtime.graph.nodes.general import general_answer_node
from app.services.runtime.graph.nodes.pipeline import (
    PipelineDeps,
    finalize_node,
    schedule_node,
    scoring_node,
    tool_fetch_node,
    widen_search_retry_node,
)
from app.services.runtime.graph.nodes.static_answer import static_answer_node
from app.services.runtime.graph.pipeline_state import RecommendPipelineState
from app.services.runtime.graph.routing import (
    ROUTE_DONE,
    ROUTE_FINALIZE,
    ROUTE_GENERAL,
    ROUTE_RETRY_TOOL_FETCH,
    ROUTE_SCHEDULE,
    ROUTE_SCORING,
    ROUTE_STATIC,
    route_after_scoring,
    route_after_tool_fetch,
    route_early_return,
)
from app.services.runtime.graph.sink import DEPS_CONFIG_KEY, LLM_CONFIG_KEY, SINK_CONFIG_KEY
from app.services.runtime.graph.state import EarlyReturnState
from app.services.runtime.stream_events import StreamEventSink

logger = logging.getLogger(__name__)


def build_early_return_graph():
    """조기 반환 경로 그래프를 조립한다.

    ```
    START → ◇route_early_return
              ├─ general → [general_answer]  (조각으로 흘려보냄)
              └─ static  → [static_answer]   (한 번에 만듦)
                                 ↓
                                END
    ```

    갈래가 둘뿐이라 지금은 if/else로도 되지만(강의 61-2가 인정하는 지점), 3단계에서
    인텐트별 노드가 붙을 자리를 여기 만들어 둔다.
    """

    graph = StateGraph(EarlyReturnState)
    graph.add_node(
        "general_answer", _observed("general_answer", general_answer_node, _summarize_answer)
    )
    graph.add_node(
        "static_answer", _observed("static_answer", static_answer_node, _summarize_answer)
    )
    graph.add_conditional_edges(
        START,
        route_early_return,
        {ROUTE_GENERAL: "general_answer", ROUTE_STATIC: "static_answer"},
    )
    graph.add_edge("general_answer", END)
    graph.add_edge("static_answer", END)
    # checkpointer는 달지 않는다(§9.9). 우리 그래프는 한 턴 안에서 시작하고 끝나며,
    # 턴 사이 상태는 B(StateStore)가 이미 자기 계약으로 관리한다. 보관함을 달면
    # 같은 session_id의 다음 턴에 이전 턴 값이 남아 섞이고(실측 확인), 세션마다
    # 체크포인트가 RAM에 무한정 쌓인다.
    return graph.compile()


# 한 span에 실을 후보 수 상한. 1차 Scoring 후보 예산이 10이라 그 이상은 안 나오지만,
# 예산이 늘어도 span 하나가 수 KB로 부풀지 않게 막아 둔다.
_SUMMARY_ITEM_LIMIT = 10

# 후보 하나에 실을 취향 근거 문장 수. `taste_evidence`는 RPC가 찾은 만큼 전부
# 들어 있어 상한이 없는데, 한 이벤트가 너무 커지면 Langfuse가 통째로 버려서
# span 자체가 사라진다. 잘렸는지는 `taste_evidence_count`로 본다.
_EVIDENCE_QUOTE_LIMIT = 10


def _round_scores(scores: Mapping[str, float | None] | None) -> dict[str, float | None]:
    """소수점을 줄여 화면에서 읽히게 한다. 0.7234891은 볼 때 방해만 된다."""
    if not scores:
        return {}
    return {
        key: (round(value, 3) if isinstance(value, int | float) else None)
        for key, value in scores.items()
    }


def _location(location: Any) -> dict[str, Any] | None:
    """`LocationDebug` 하나를 span에 실을 모양으로 편다. **좌표는 뺀다.**

    `source`가 이 요약의 존재 이유다 — 검색 위치·사용자 위치·경로 시작점 셋이
    서로 다를 수 있고 **다른 것 자체가 관측 대상이다**(TP-112). `route_origin`이
    `search_center`로 대체됐는지는 좌표 없이도 이 한 필드로 읽힌다.

    **위경도는 싣지 않는다.** 팀원이 테스트하는 자리의 실좌표라, 켜고 끄는 스위치
    하나에 맡길 값이 아니다(2026-08-26 결정). `name`은 발화에서 온 지명이고
    (`"경복궁"`), 기기 GPS로 온 좌표에는 애초에 이름이 없어 `None`이다.
    """

    if location is None:
        return None
    return {"name": location.name, "source": location.source}


def concentration_source_rows(execution: Any) -> list[dict[str, Any]]:
    """후보별 혼잡도가 어디서 온 값인지를 span에 실을 모양으로 편다.

    **근사치가 섞이는 게 정상 상태다**(활성 844건 중 집중률 매핑 100건). 그래서
    상태 집계만 보면 직접 조회한 값과 인근 장소에서 빌려온 값이 "success 5건"으로
    같아 보인다 — 근사치의 타당성은 "어느 장소에서 얼마나 떨어진 값인가"로 판단해야
    하므로 후보별로 남긴다(`CandidateConcentrationDebug`).

    `tool_fetch` span과 `concentration_enrichment` span이 **같은 함수를 쓴다.** 같은
    사실을 두 모양으로 적으면 한쪽만 고쳤을 때 조용히 어긋난다.
    """

    return [
        {
            "place_id": row.place_id,
            "name": row.name,
            "status": row.status,
            "is_proxy": row.is_proxy,
            "proxy_place_name": row.proxy_place_name,
            "proxy_distance_km": row.proxy_distance_km,
        }
        for row in execution.candidate_concentration[:_SUMMARY_ITEM_LIMIT]
    ]


def _tool_call_summary(execution: Any) -> dict[str, Any]:
    """C 호출 한 건을 span에 실을 모양으로 편다.

    `ToolExecutionDebug`가 개발자 Audit용으로 이미 만들어 둔 값이라 새로 수집하는
    게 아니다 — 고르기만 한다.
    """

    return {
        "operation": execution.operation,
        "status": execution.status,
        "latency_ms": execution.latency_ms,
        "providers": [
            {"source": provider.source, "status": provider.status}
            for provider in execution.providers
        ],
        # fetched=False는 실패가 아니라 "아예 조회하지 않음"이다
        # (발화에 이미 값이 있어 생략한 경우 등). 둘을 구분해 적는다.
        "items": {
            item.key: (item.status or "unknown") if item.fetched else "skipped"
            for item in execution.context_items
        },
        "item_errors": {
            item.key: item.error_code for item in execution.context_items if item.error_code
        },
        "rule_versions": dict(execution.rule_versions),
        "resolved_location_name": execution.resolved_location_name,
        "resolved_location_address": execution.resolved_location_address,
        # 셋은 서로 다를 수 있고, **다른 것 자체가 관측 대상이다**(TP-112). 특히
        # route_origin.source가 "search_center"면 사용자 위치를 몰라 검색 위치로
        # 대체한 턴이라 거리·경로 표기가 사실과 어긋날 수 있다.
        "locations": {
            "search": _location(execution.search_location),
            "user": _location(execution.user_location),
            "route_origin": _location(execution.route_origin),
        },
        "error_code": execution.error_code,
        "clarification_code": execution.clarification_code,
        "is_proxy": execution.is_proxy,
        "candidate_status_counts": dict(execution.candidate_status_counts),
        "candidate_concentration": concentration_source_rows(execution),
    }


def _summarize_tool_fetch(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """`tool_fetch` span에 실을 값을 고른다 — 개발자 Audit "C Tool" 탭과 같은 값이다.

    **원래는 상태·개수만 남기고 좌표·해석된 장소명·주소를 뺐다.** 외부 SaaS로 나가는
    값이라 `capture_content`를 켜도 안 새게 두 겹으로 막은 것이었다. 2026-08-26에
    **한 겹으로 줄이기로 했다** — 화면에 보이는 값이 trace에는 없어서, 원인을 쫓을
    때마다 결국 API 응답을 따로 받아야 했다.

    **이제 방어선은 `capture_content` 하나뿐이다.** 꺼져 있으면 이 output 전체가
    `<redacted>`로 치환되지만(`langfuse_tracing._mask`), 켜면 **사용자 좌표와
    사용자가 찾는 장소명이 그대로 Langfuse로 나간다.** 켜는 것이 명시적 선택이어야
    한다는 뜻이고, 한 번 올라간 trace는 스위치를 도로 꺼도 남는다.

    조기 종료(`response`만 채워 그 턴을 끝낸 경우)는 Tool 기록이 아예 없으므로
    `terminal`만 남긴다 — 값이 없는 것과 단계를 건너뛴 것이 구분돼야 한다.
    """

    executions = list(result.get("tool_executions") or [])
    terminal = result.get("response") is not None
    if not executions and not terminal:
        return None
    return {
        "terminal": terminal,
        "call_count": len(executions),
        "calls": [_tool_call_summary(execution) for execution in executions[:_SUMMARY_ITEM_LIMIT]],
    }


def _summarize_scoring(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """`scoring` span에 실을 값을 고른다 — 개발자 Audit "D Scoring" 탭과 같은 값이다.

    **원래는 점수 축만 남기고 `explanations`·`taste_evidence`를 뺐다** — 화면에서
    점수를 읽는 데 방해가 된다는 이유였다. 2026-08-26에 되돌렸다: "왜 이 점수가
    나왔나"를 쫓을 때 근거 문장이 없으면 span만으로는 답이 안 나온다.

    **후보는 상위 10건, 근거 문장은 후보당 10개까지** 싣는다(`_EVIDENCE_QUOTE_LIMIT`).
    `taste_evidence`는 상한이 없어 그대로 실으면 이벤트가 얼마나 커질지 모른다.

    좌표는 여기 없다. C가 장소를 찾을 때만 쓰고 D로 넘어올 땐 `distance_km`로
    접힌다(`ScoringCandidate`) — `tool_fetch` span과 달리 이쪽은 열어도 위치가
    새지 않는다.
    """

    response = result.get("recommendations")
    if response is None:
        return None
    items = list(getattr(response, "recommendations", []) or [])
    return {
        "ranked": [
            {
                "place_id": item.place_id,
                "name": item.name,
                "category": item.category,
                "distance_km": item.distance_km,
                "score": round(item.score, 3),
                "features": _round_scores(item.feature_scores),
                # 취향·혼잡도가 켜졌는지에 따라 세트가 달라지는데 지금은 눈에 안 보인다.
                "weights": _round_scores(item.weights_used),
                "explanations": list(item.explanations),
                "warnings": list(item.warnings),
                "taste_evidence": [
                    {"text": quote.text, "similarity": round(quote.similarity, 3)}
                    for quote in item.taste_evidence[:_EVIDENCE_QUOTE_LIMIT]
                ],
                # 위 목록이 잘렸는지 여기서 본다. 빈 목록이면 "컷을 넘는 근거가
                # 없었다"는 뜻이고, 검색이 실패한 것과는 다르다.
                "taste_evidence_count": len(item.taste_evidence),
            }
            for item in items[:_SUMMARY_ITEM_LIMIT]
        ],
        "ranked_count": len(items),
        "unverified_count": len(getattr(response, "unverified_recommendations", []) or []),
        "excluded_closed_count": len(getattr(response, "excluded_closed_place_ids", []) or []),
        "excluded_all_closed": getattr(response, "excluded_all_closed", False),
    }


def _summarize_finalize(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """`finalize` span에 실을 값을 고른다.

    **답변 문장은 길이만 남긴다.** 원문은 발화만큼이나 사용자 것이고, 여기서 알고 싶은
    건 "문장이 나갔나"와 "카드가 몇 장 어떤 순서로 나갔나"다. 원문이 필요하면
    generation의 output에 이미 있다.
    """
    response = result.get("response")
    if response is None:
        return None
    recommendations = getattr(response, "recommendations", None)
    shown = list(getattr(recommendations, "recommendations", []) or [])
    unverified = list(getattr(recommendations, "unverified_recommendations", []) or [])
    message = getattr(response, "message", None)
    return {
        "card_order": [item.place_id for item in (shown + unverified)[:_SUMMARY_ITEM_LIMIT]],
        "card_count": len(shown) + len(unverified),
        "message_length": len(message) if message else 0,
        "has_schedule": getattr(response, "schedule", None) is not None,
        "has_comparison": getattr(response, "comparison", None) is not None,
    }


def _summarize_answer(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """`general_answer`·`static_answer` span에 실을 값을 고른다.

    **이 두 노드는 span이 아예 없었다.** `_observed()`를 안 씌워서 GENERAL 턴의
    trace에는 `agent_turn` 밑에 generation 하나만 떠 있었다 — 노드가 얼마나 걸렸고
    답변이 실제로 나갔는지가 화면에서 안 보였다.

    답변 원문은 싣지 않는다. 같은 문자열이 바로 아래 generation의 output에 이미
    있어서, 두 번 실으면 이벤트만 커지고 읽히는 건 늘지 않는다.
    """

    answer = result.get("answer")
    if answer is None:
        return None
    return {"answer_length": len(answer)}


def _observed(
    name: str,
    node: Callable[..., Awaitable[Any]],
    summarize: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None,
) -> Callable[..., Awaitable[Any]]:
    """노드 하나를 관측 span 하나로 감싼다.

    **Langfuse가 주는 LangChain CallbackHandler를 안 쓰는 이유**: 그 핸들러가
    `langchain` **본체**를 import한다. 우리는 langgraph가 끌고 온 `langchain-core`만
    두고 본체·통합 패키지는 의도적으로 안 넣었다(pyproject.toml). 2026-08-25에
    콜백으로 붙여봤다가 `ModuleNotFoundError`로 조용히 꺼지는 걸 실측으로 확인했다 —
    앱은 멀쩡했고 노드 span만 통째로 안 생겼다.

    직접 감싸면 의존성도 안 늘고 **이름을 우리가 정한다.** `tool_fetch`·`scoring`은
    B의 Trace `step`과 같은 이름이라 두 기록을 나란히 읽을 수 있다.

    `summarize`를 주면 노드 결과에서 **고른 값만** span에 남긴다. 주지 않으면 이름과
    지연만 남는다 — `schedule`이 그렇다. 고르는 일은 요약 함수가 하고, 좌표·발화
    원문처럼 실으면 안 되는 값은 거기서 걸러낸다(`_summarize_tool_fetch` 참고).

    관측이 꺼져 있으면(기본값) `observe_step()`이 no-op이라 호출 한 겹만 는다.

    `wraps`는 표시용이 아니라 **동작 조건이다.** LangGraph는 노드의 시그니처를 보고
    `config`를 넘길지 정하는데(`_internal/_runnable.py`), 감싼 함수가 `*args`로만
    보이면 `state` 하나만 넘겨서 `TypeError: missing 1 required positional argument:
    'config'`로 죽는다. `wraps`가 남기는 `__wrapped__`를 `inspect.signature`가 따라가
    원본 시그니처를 보게 한다.
    """

    @wraps(node)
    async def _run(*args: Any, **kwargs: Any) -> Any:
        with observe_step(name) as step:
            result = await node(*args, **kwargs)
            if summarize is not None and isinstance(result, Mapping):
                # 요약이 터져도 노드 결과는 그대로 나가야 한다 — 관측이 삼켜도 되는
                # 건 자기 실패뿐이다(langfuse_tracing._guard와 같은 원칙).
                try:
                    step.record(output=summarize(result))
                except Exception:
                    logger.warning("관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
            return result

    return _run


# 프로세스 수명 동안 한 번만 조립한다 — 그래프 컴파일은 요청마다 할 일이 아니다.
_EARLY_RETURN_GRAPH = build_early_return_graph()


def _intent_tag(llm_output: LLMOutput) -> list[str]:
    """이 턴의 intent를 trace 태그로 만든다.

    **태그는 trace를 열 때 정해지는 게 아니다.** 2026-08-25 실측으로 확인했다 —
    루트 span을 연 뒤에 `trace_attributes(tags=...)`를 열어도 trace 태그에 합쳐진다.
    그 전까지는 "intent는 분류 이후에나 알 수 있으니 태그로 못 싣는다"고 적어뒀는데
    틀린 진단이었다.

    **다만 그 범위 안에 observation이 하나는 생겨야 한다** — 태그는 observation에
    실려 올라가고 trace 태그는 그것들을 합친 결과다. 그래서 여기(그래프 진입)에
    두었다. 안에서 노드 span이 반드시 생기고, intent는 이미 인자로 들어와 있다.

    이게 있으면 화면에서 **intent별 비용·지연을 가를 수 있다.** 지금은 `scoring:`과
    `env:` 태그뿐이라 RECOMMEND와 INFO가 한 덩어리로 섞여 있다.
    """
    return [f"intent:{llm_output.intent.value}"]


async def run_early_return_graph(
    llm_output: LLMOutput,
    *,
    llm: LLMProvider,
    stream_event_sink: StreamEventSink | None = None,
    stream_general: bool = False,
    rejected_offer_actions: list[str] | None = None,
    history: list[ConversationTurnView] | None = None,
) -> str:
    """조기 반환 경로의 답변 문구를 그래프로 만들어 문자열로 돌려준다.

    호출부(`run_agent_flow()`)가 기대하는 것은 기존 `compose_chat_message()`와 같은
    문자열 하나다 — 그래프로 바뀐 것을 호출부가 알 필요가 없게 시그니처를 맞췄다.

    rejected_offer_actions는 대화층 4단계 — GENERAL 답변이 이미 거절된 상황 제안을
    다시 권하지 않도록 session_context에서 여기까지 그대로 옮긴다. history도 같은
    이유로 함께 옮긴다 — 답변 문장이 앞 턴과 이어지려면 생성 단계가 대화를 봐야 한다.
    """

    with trace_attributes(tags=_intent_tag(llm_output)):
        result = await _EARLY_RETURN_GRAPH.ainvoke(
            {
                "llm_output": llm_output,
                "stream_general": stream_general,
                "rejected_offer_actions": rejected_offer_actions or [],
                "history": history or [],
                "answer": None,
            },
            config={
                "configurable": {
                    SINK_CONFIG_KEY: stream_event_sink,
                    LLM_CONFIG_KEY: llm,
                }
            },
        )
    return result["answer"] or ""


def build_recommend_pipeline_graph():
    """추천 파이프라인 그래프를 조립한다(3단계, A-1로 자기 교정 사이클 추가).

    ```
    START → [tool_fetch] ←────────────────────┐ (반경 넓혀 재조회)
              ↓                                │
       ◇route_after_tool_fetch                 │
         ├─ retry_tool_fetch → [widen_search_retry] ┘
         ├─ done    →────────────────┐  (그래도 no_data거나, no_data_empty가 아님)
         └─ scoring → [scoring]      │
                         ↓            │
                ◇route_after_scoring  │
                  ├─ schedule → [schedule] ┤
                  └─ finalize → [finalize] ┤
                                           ↓
                                          END
    ```

    `tool_fetch`↔`widen_search_retry` 사이클이 A-1(자기 교정 루프)이다 — no_data_empty
    (반경 안에 후보가 아예 없음)면 곧장 사용자에게 되묻는 대신, 반경을 한 번 넓혀
    스스로 다시 조회해보고 그래도 없을 때만 되묻는다(route_after_tool_fetch·
    `_MAX_TOOL_FETCH_RETRIES`가 재시도를 1회로 막아 무한 루프를 막는다). "부족하면
    스스로 다시 찾아본다"는 강의교재 90강의 자기 교정과 같은 결이지만, 범위는 이미
    있던 "검색 범위 넓히기" 되묻기 버튼(`_WIDEN_RADIUS`)이 사람 개입 없이 자동으로
    한 번 먼저 실행되는 것뿐이라 새 도구·새 되묻기 문구를 만들지 않는다.

    나머지 갈림길(중간에 끝나는가·SCHEDULE인가)은 그대로다 — 조기 반환 그래프
    (`build_early_return_graph`)와 달리 여기는 단계가 순차로 이어지는 파이프라인이라
    조건부 엣지를 최소한으로 쓴다(§9.8).
    """

    graph = StateGraph(RecommendPipelineState)
    graph.add_node("tool_fetch", _observed("tool_fetch", tool_fetch_node, _summarize_tool_fetch))
    graph.add_node("widen_search_retry", _observed("widen_search_retry", widen_search_retry_node))
    graph.add_node("scoring", _observed("scoring", scoring_node, _summarize_scoring))
    graph.add_node("schedule", _observed("schedule", schedule_node))
    graph.add_node("finalize", _observed("finalize", finalize_node, _summarize_finalize))

    graph.add_edge(START, "tool_fetch")
    graph.add_conditional_edges(
        "tool_fetch",
        route_after_tool_fetch,
        {
            ROUTE_DONE: END,
            ROUTE_SCORING: "scoring",
            ROUTE_RETRY_TOOL_FETCH: "widen_search_retry",
        },
    )
    graph.add_edge("widen_search_retry", "tool_fetch")
    graph.add_conditional_edges(
        "scoring",
        route_after_scoring,
        {ROUTE_SCHEDULE: "schedule", ROUTE_FINALIZE: "finalize"},
    )
    graph.add_edge("schedule", END)
    graph.add_edge("finalize", END)
    # checkpointer 미사용 — 이유는 build_early_return_graph() 주석과 §9.9 참고.
    return graph.compile()


_RECOMMEND_PIPELINE_GRAPH = build_recommend_pipeline_graph()


async def run_recommend_pipeline_graph(
    state: RecommendPipelineState,
    *,
    deps: PipelineDeps,
    stream_event_sink: StreamEventSink | None = None,
) -> AgentResponse:
    """Tool 조회부터 응답 조립까지를 그래프로 돌려 최종 응답을 돌려준다."""

    with trace_attributes(tags=_intent_tag(state["llm_output"])):
        result = await _RECOMMEND_PIPELINE_GRAPH.ainvoke(
            state,
            config={
                "configurable": {
                    SINK_CONFIG_KEY: stream_event_sink,
                    LLM_CONFIG_KEY: deps.llm,
                    DEPS_CONFIG_KEY: deps,
                }
            },
        )
    response = result.get("response")
    if response is None:  # 그래프가 응답 없이 끝나면 배선이 잘못된 것이다
        raise RuntimeError("추천 파이프라인 그래프가 응답을 만들지 못했습니다.")
    return response


__all__ = [
    "PipelineDeps",
    "concentration_source_rows",
    "build_early_return_graph",
    "build_recommend_pipeline_graph",
    "run_early_return_graph",
    "run_recommend_pipeline_graph",
]

"""추천 파이프라인 노드 — 이미 함수로 떼어낸 4단계를 그래프에 붙인다.

강의 91-3의 "부품을 노드로 포장한다"를 그대로 따른다. 각 노드가 하는 일은
(1) 서류철에서 입력을 꺼내고 (2) `agent_runtime`의 단계 함수를 **호출만** 하고
(3) 결과를 자기 칸에 담아 돌려주는 것뿐이다 — 단계 함수의 속은 건드리지 않는다.

노드가 얇아야 하는 이유는 실용적이다. 두꺼워지면 "이관"이 아니라 "재작성"이 되고,
기존 테스트가 지켜주던 범위를 벗어난다(langgraph-adoption.md §6.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig

from app.providers.protocols import LLMProvider
from app.services.runtime.graph.pipeline_state import RecommendPipelineState
from app.services.runtime.graph.sink import deps_from_config, sink_from_config
from app.services.runtime.protocols import (
    EnrichmentProvider,
    RecommendationProvider,
    ToolProvider,
    TravelRouteToolProvider,
)
from app.state.store import StateStore


@dataclass(frozen=True)
class PipelineDeps:
    """요청마다 달라지지 않고 주입되는 것들. 서류철이 아니라 config로 넘긴다."""

    llm: LLMProvider
    tool_provider: ToolProvider
    recommendation_provider: RecommendationProvider
    enrichment_provider: EnrichmentProvider
    travel_route_tool: TravelRouteToolProvider | None
    store: StateStore | None
    principal: object | None
    # 보관함 장소를 편성 후보에 주입할 때(SCHEDULE-12 후속)와 부분 재편성에서
    # 유지한 장소의 사진을 채울 때 쓴다. 없으면 둘 다 건너뛴다.
    place_details_repository: object | None = None


async def tool_fetch_node(
    state: RecommendPipelineState, config: RunnableConfig
) -> dict[str, object]:
    """A → C Tool 조회(5단계). 종료 상태면 ``response``를 채워 그 턴을 끝낸다.

    `retry_count`가 1 이상이면 A-1 자기 교정 재시도다 — `widen_search_retry_node`가
    되묻기 응답을 지우고 이리로 되돌려보낸 것이므로, C에 보내기 전에 반경을
    `_WIDEN_RADIUS_MAX_TRAVEL_TIME`까지 넓힌다(route_after_tool_fetch 참고).
    """

    from app.services.runtime.agent_runtime import (
        _WIDEN_RADIUS_MAX_TRAVEL_TIME,
        _fetch_tool_context,
        _revivable_place_ids,
    )

    deps: PipelineDeps = deps_from_config(config)
    retry_count = state.get("retry_count", 0)
    outcome = await _fetch_tool_context(
        state["request"],
        state["llm_output"],
        state["state_response"],
        valid_gps=state["valid_gps"],
        effective_ignore_operating_hours=state["effective_ignore_operating_hours"],
        llm=deps.llm,
        tool_provider=deps.tool_provider,
        travel_route_tool=deps.travel_route_tool,
        store=deps.store,
        shown_place_ids=_revivable_place_ids(
            state["llm_output"], state["session_context"]
        ),
        radius_override_max_travel_time=(
            _WIDEN_RADIUS_MAX_TRAVEL_TIME if retry_count > 0 else None
        ),
        stream_event_sink=sink_from_config(config),
    )
    # A-1 재시도 시 이전 시도의 tool_executions를 이어 붙인다 — LangGraph는 노드
    # 반환값으로 state 칸을 통째로 덮어쓰므로, 첫 시도가 되묻기(response)로 끝나며
    # state["tool_executions"]를 안 갱신하면 그 시도 기록 자체가 증발한다. 그래서
    # 되묻기로 끝나든 아니든 매번 누적값을 state에 남긴다 — 이번 시도가 재시도로
    # 이어지면 다음 tool_fetch_node 호출이 이 값을 이어받는다(실측: "강남 술집
    # 추천해줘"가 두 번째 시도까지 갔는데도 tool_executions가 1건으로만 보였다).
    prior_executions = state.get("tool_executions", [])
    if outcome.terminal is not None:
        merged_executions = [*prior_executions, *outcome.terminal.tool_executions]
        return {
            "response": outcome.terminal.model_copy(update={"tool_executions": merged_executions}),
            "tool_executions": merged_executions,
        }
    return {
        "tool_context": outcome.tool_context,
        "agent_conditions": outcome.agent_conditions,
        "context_gps": outcome.context_gps,
        "tool_execution": outcome.tool_execution,
        "tool_executions": [*prior_executions, *outcome.tool_executions],
    }


async def widen_search_retry_node(
    state: RecommendPipelineState, config: RunnableConfig
) -> dict[str, object]:
    """A-1 자기 교정: no_data_empty 되묻기를 지우고 재시도 횟수를 올린다.

    실제 반경 확대·재조회는 `tool_fetch_node`가 한다 — 이 노드는 그 앞에서
    "한 번 더 시도한다"는 결정만 상태에 남기는 얇은 판정 노드다. `config`는 쓰지
    않지만, LangGraph가 노드 시그니처로 config 전달 여부를 판단하므로 다른 노드와
    시그니처를 맞춰 둔다.
    """

    return {"retry_count": state.get("retry_count", 0) + 1, "response": None}


async def scoring_node(
    state: RecommendPipelineState, config: RunnableConfig
) -> dict[str, object]:
    """A → D 1차 Scoring과 후보 보충·혼잡도 재정렬(6단계)."""

    from app.schemas import Intent
    from app.services.runtime.agent_runtime import (
        _revivable_place_ids,
        _score_recommendations,
    )

    deps: PipelineDeps = deps_from_config(config)
    outcome = await _score_recommendations(
        state["state_response"],
        tool_context=state["tool_context"],
        agent_conditions=state["agent_conditions"],
        context_gps=state["context_gps"],
        is_schedule=state["llm_output"].intent is Intent.SCHEDULE,
        shown_place_ids=_revivable_place_ids(
            state["llm_output"], state["session_context"]
        ),
        saved_places=state["session_context"].saved_places,
        place_details_repository=deps.place_details_repository,
        tool_provider=deps.tool_provider,
        recommendation_provider=deps.recommendation_provider,
        enrichment_provider=deps.enrichment_provider,
        travel_route_tool=deps.travel_route_tool,
        store=deps.store,
        principal=deps.principal,
        tool_executions=state["tool_executions"],
        effective_ignore_operating_hours=state["effective_ignore_operating_hours"],
        stream_event_sink=sink_from_config(config),
        # 후보별 실측 이동수단을 일정과 같은 판정으로 정한다(TP-227).
        llm=deps.llm,
    )
    return {
        "recommendations": outcome.recommendations,
        # 보충 조회·보관함 주입으로 좌표가 합쳐진 컨텍스트로 state를 덮는다. 안
        # 덮으면 schedule_node·finalize_node가 tool_fetch_node가 넣은 원본을 읽어
        # 그렇게 들어온 후보의 좌표를 못 찾는다(TP-198).
        "tool_context": outcome.tool_context,
    }


async def schedule_node(
    state: RecommendPipelineState, config: RunnableConfig
) -> dict[str, object]:
    """SCHEDULE 편성(6-2단계)."""

    from app.services.runtime.agent_runtime import _run_schedule_branch

    deps: PipelineDeps = deps_from_config(config)
    response = await _run_schedule_branch(
        state["llm_output"],
        state["state_response"],
        state["recommendations"],
        tool_context=state["tool_context"],
        agent_conditions=state["agent_conditions"],
        session_context=state["session_context"],
        llm=deps.llm,
        store=deps.store,
        principal=deps.principal,
        tool_execution=state["tool_execution"],
        tool_executions=state["tool_executions"],
        effective_ignore_operating_hours=state["effective_ignore_operating_hours"],
        stream_event_sink=sink_from_config(config),
        # 구간 실측 조회에 쓴다(TP-216). 구 경로도 같은 값을 넘긴다 — 두 경로가
        # 다른 이동시간을 내면 같은 발화가 화면마다 다른 시각을 갖는다.
        travel_route_tool=deps.travel_route_tool,
        # 부분 재편성에서 유지한 장소의 사진을 다시 조회한다. 구 경로도 같은 값을
        # 넘긴다 — 한쪽만 넘기면 경로에 따라 유지한 자리의 사진이 빠진다.
        place_details_repository=deps.place_details_repository,
    )
    return {"response": response}


async def finalize_node(
    state: RecommendPipelineState, config: RunnableConfig
) -> dict[str, object]:
    """노출 이력 기록과 카드·요약 방출(7·8단계)."""

    from app.services.runtime.agent_runtime import _finalize_recommendation_response

    deps: PipelineDeps = deps_from_config(config)
    response = await _finalize_recommendation_response(
        state["llm_output"],
        state["state_response"],
        state["recommendations"],
        llm=deps.llm,
        store=deps.store,
        principal=deps.principal,
        tool_context=state["tool_context"],
        tool_execution=state["tool_execution"],
        tool_executions=state["tool_executions"],
        effective_ignore_operating_hours=state["effective_ignore_operating_hours"],
        stream_recommendation_summary=state["stream_recommendation_summary"],
        stream_event_sink=sink_from_config(config),
    )
    return {"response": response}


__all__ = [
    "PipelineDeps",
    "finalize_node",
    "schedule_node",
    "scoring_node",
    "tool_fetch_node",
    "widen_search_retry_node",
]

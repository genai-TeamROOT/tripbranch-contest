"""추천 파이프라인 그래프가 노드 사이에 돌리는 상태.

조기 반환 이후 구간(Tool 조회 → Scoring → SCHEDULE 편성 / 추천 마무리)이 범위다.
`EarlyReturnState`와 나눠 둔 이유: 두 그래프가 다루는 단계도, 필요한 칸도 다르다.
합치면 어느 쪽에서도 안 쓰는 칸이 절반씩 생긴다.

**의존성은 여기 담지 않는다.** LLMProvider·StateStore·Tool Provider처럼 "요청마다
달라지지 않고 주입되는 것"은 config로 넘긴다(graph/sink.py) — 서류철에는 이번 턴에
실제로 오가는 값만 둔다.
"""

from __future__ import annotations

from typing import TypedDict

from app.agent_context.schemas import RecommendationContext
from app.schemas import (
    AgentRequest,
    AgentResponse,
    LLMOutput,
    RecommendationResponse,
    UserConditions,
)
from app.services.runtime.tool_debug import ToolExecutionDebug
from app.state.service import SessionContextResponse, StateApplyResponse


class RecommendPipelineState(TypedDict, total=False):
    """Tool·Scoring을 거치는 한 턴의 작업 상태."""

    # 들어올 때 채워지는 입력.
    request: AgentRequest
    llm_output: LLMOutput
    state_response: StateApplyResponse
    valid_gps: str | None
    effective_ignore_operating_hours: bool
    # SCHEDULE 부분 재편성이 직전 턴 노출 목록을 참조한다(6-2-1단계).
    session_context: SessionContextResponse
    stream_recommendation_summary: bool

    # tool_fetch 노드가 채운다.
    tool_context: RecommendationContext | None
    agent_conditions: UserConditions | None
    context_gps: str | None
    tool_execution: ToolExecutionDebug | None
    tool_executions: list[ToolExecutionDebug]

    # A-1(자기 교정 루프): no_data_empty를 되묻지 않고 반경을 넓혀 한 번 더
    # 조회했는지 센다. 없으면(.get 기본값) 0 — 아직 재시도 전이라는 뜻이다.
    # widen_search_retry 노드가 올리고, tool_fetch 노드가 읽어 반경을 넓힐지
    # 정하고, route_after_tool_fetch가 상한(도달 시 되묻기로 종료)을 판단한다.
    retry_count: int

    # scoring 노드가 채운다.
    recommendations: RecommendationResponse | None

    # 어느 노드에서든 여기에 응답이 담기면 그 턴은 끝난다.
    response: AgentResponse | None


__all__ = ["RecommendPipelineState"]

"""조건부 엣지 — 상태를 보고 다음 노드 이름을 돌려준다(강의 61-2의 "길 안내원").

지금은 갈래가 둘이다. 3단계에서 인텐트별 노드가 붙으면 이 함수가
`run_agent_flow()`에 흩어진 인텐트 분기 40군데가 모이는 자리가 된다(§2).
"""

from __future__ import annotations

from app.schemas import Intent
from app.services.runtime.graph.pipeline_state import RecommendPipelineState
from app.services.runtime.graph.state import EarlyReturnState

# 조건부 엣지 매핑 키. add_conditional_edges의 변환표와 짝을 이룬다.
ROUTE_GENERAL = "general"
ROUTE_STATIC = "static"


def route_early_return(state: EarlyReturnState) -> str:
    """조각으로 흘려보낼 GENERAL 답변인지, 한 번에 만드는 나머지인지 가른다.

    GENERAL이라도 단발 `POST /api/chat`(sink 없음)에서는 흘려보낼 곳이 없어
    static으로 간다 — 기존 `run_agent_flow()`의 분기 조건과 같다.
    """

    if state["llm_output"].intent is Intent.GENERAL and state["stream_general"]:
        return ROUTE_GENERAL
    return ROUTE_STATIC


__all__ = [
    "ROUTE_DONE",
    "ROUTE_FINALIZE",
    "ROUTE_GENERAL",
    "ROUTE_RETRY_TOOL_FETCH",
    "ROUTE_SCHEDULE",
    "ROUTE_SCORING",
    "ROUTE_STATIC",
    "route_after_scoring",
    "route_after_tool_fetch",
    "route_early_return",
]


# ── 추천 파이프라인 조건부 엣지 ────────────────────────────────────────

ROUTE_DONE = "done"
ROUTE_SCORING = "scoring"
ROUTE_SCHEDULE = "schedule"
ROUTE_FINALIZE = "finalize"
ROUTE_RETRY_TOOL_FETCH = "retry_tool_fetch"

# A-1(자기 교정 루프): 반경을 넓히면 나아질 여지가 있는 no_data만 자동 재시도한다.
# no_data_exhausted(원인2 — 이전 노출·거절로 소진)는 반경 문제가 아니라 카테고리를
# 넓히거나 거절 이력을 정리해야 하는 상황이라 자동으로 못 고친다 — 그대로 되묻는다.
_AUTO_WIDEN_RETRY_CODES = frozenset({"no_data_empty"})
# 1회만 자동 재시도한다. 그래도 안 되면 카테고리 확대 등 다른 처방이 필요해
# 사람에게 되묻는 게 낫다 — 무한정 넓히면 엉뚱한 지역 결과까지 나온다.
_MAX_TOOL_FETCH_RETRIES = 1


def _clarification_code(response: object) -> str | None:
    """``response.llm_output.clarification.code``를 안전하게 꺼낸다.

    `response`가 온전한 `AgentResponse`가 아니어도(예: 라우팅만 보는 테스트의
    자리표시자) 죽지 않고 None을 돌려준다 — 그러면 자동 재시도 없이 기존처럼
    ROUTE_DONE으로 간다.
    """

    llm_output = getattr(response, "llm_output", None)
    clarification = getattr(llm_output, "clarification", None)
    return getattr(clarification, "code", None)


def route_after_tool_fetch(state: RecommendPipelineState) -> str:
    """C가 되묻기·no_data·unsupported로 끝냈으면 여기서 종료한다.

    다만 no_data_empty(반경 안에 후보 자체가 없음)는 아직 자동 재시도를 안 써봤으면
    곧장 되묻지 않고 `widen_search_retry`로 보내 반경을 넓혀 한 번 더 조회한다
    (강의교재 90강 "부족하면 스스로 다시 찾아본다"와 같은 자기 교정).
    """

    response = state.get("response")
    if response is not None:
        code = _clarification_code(response)
        if (
            code in _AUTO_WIDEN_RETRY_CODES
            and state.get("retry_count", 0) < _MAX_TOOL_FETCH_RETRIES
        ):
            return ROUTE_RETRY_TOOL_FETCH
        return ROUTE_DONE
    return ROUTE_SCORING


def route_after_scoring(state: RecommendPipelineState) -> str:
    """SCHEDULE이면 편성으로, 아니면 추천 마무리로 간다.

    `run_agent_flow()`에 남아 있던 `if is_schedule:` 하나가 여기로 옮겨온 것이다 —
    조기 반환 이후 구간의 인텐트 분기는 원래 이 하나뿐이었다(§9.8).
    """

    if state["llm_output"].intent is Intent.SCHEDULE:
        return ROUTE_SCHEDULE
    return ROUTE_FINALIZE

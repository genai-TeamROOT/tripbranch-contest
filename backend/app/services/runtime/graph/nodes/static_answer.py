"""조각 없이 한 번에 만드는 답변 노드.

OUT_OF_SCOPE(고정 템플릿)·되묻기(추출 단계가 만든 문구)·아직 Tool을 안 타는
INFO/COMPARE 낙오 케이스, 그리고 단발 `POST /api/chat`의 GENERAL이 여기로 온다.

`general_answer_node`와 갈리는 지점은 **흘려보낼 sink가 있느냐** 하나다. 문구를
만드는 일은 둘 다 `compose_chat_message()`가 하고, 그 안에서 Intent·status별
분기가 이미 끝나 있다 — 그래서 이 노드는 인텐트를 다시 보지 않는다.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.services.runtime.graph.sink import llm_from_config
from app.services.runtime.graph.state import EarlyReturnState
from app.services.runtime.response_composer import compose_chat_message


async def static_answer_node(
    state: EarlyReturnState, config: RunnableConfig
) -> dict[str, object]:
    """답변 문자열을 한 번에 만들어 ``answer`` 칸에 담는다. 이벤트는 내지 않는다."""

    message = await compose_chat_message(
        state["llm_output"],
        llm=llm_from_config(config),
        rejected_offer_actions=state.get("rejected_offer_actions") or [],
        history=state.get("history") or [],
    )
    return {"answer": message}


__all__ = ["static_answer_node"]

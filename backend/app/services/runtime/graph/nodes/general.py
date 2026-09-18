"""GENERAL 답변을 조각으로 흘려보내며 만드는 노드.

강의 91-3의 "부품을 노드로 포장한다"를 그대로 따른다 — 이미 동작하는
`compose_chat_message()`를 노드 안에서 **호출만** 하고 속은 건드리지 않는다.

SSE 이벤트는 노드가 직접 낸다(langgraph-adoption.md §9.6 "방식 1"). 그래프
스트리밍을 쓰지 않는 이유는 그 절에 적어뒀다.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.schemas import Intent
from app.services.runtime.graph.sink import llm_from_config, sink_from_config
from app.services.runtime.graph.state import EarlyReturnState
from app.services.runtime.response_composer import compose_chat_message
from app.services.runtime.stream_events import begin_streamed_message, emit_stream_event

# 기존 run_agent_flow()가 GENERAL에 쓰던 문구 그대로. 이관은 출력이 바뀌면 안 된다.
_PROGRESS_MESSAGE = "답변을 정리하고 있어요."


async def general_answer_node(
    state: EarlyReturnState, config: RunnableConfig
) -> dict[str, object]:
    """GENERAL 답변을 만들어 ``answer`` 칸에 담는다.

    이벤트 순서는 기존과 같다: ``progress`` → ``message_start`` →
    ``message_delta`` × N.

    ``config``는 반드시 ``RunnableConfig``로 어노테이션해야 LangGraph가 주입한다
    (sink.py 참고).
    """

    sink = sink_from_config(config)
    llm = llm_from_config(config)

    await begin_streamed_message(sink, intent=Intent.GENERAL, progress_message=_PROGRESS_MESSAGE)

    async def on_delta(text: str) -> None:
        await emit_stream_event(sink, "message_delta", {"text": text})

    message = await compose_chat_message(
        state["llm_output"],
        llm=llm,
        on_message_delta=on_delta,
        rejected_offer_actions=state.get("rejected_offer_actions") or [],
        history=state.get("history") or [],
    )
    return {"answer": message}


__all__ = ["general_answer_node"]

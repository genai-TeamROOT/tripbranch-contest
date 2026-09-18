"""그래프 노드가 기존 SSE sink를 꺼내 쓰는 통로.

0단계 스파이크(langgraph-adoption.md §9.6)에서 확정한 "방식 1"이다. LangGraph의
`astream_events`로 우리 SSE 계약을 재현하려 했더니 `message_delta`가 나오지 않았다 —
델타는 노드 하나 **안에서** LLM 스트림을 돌며 생기는 것이라 노드 경계 이벤트로는
관측되지 않기 때문이다. 그래서 그래프 스트리밍을 쓰지 않고, 지금 쓰는 sink 콜백을
`config`에 실어 노드가 직접 부른다.

이렇게 하면 `app/routes/chat.py`의 큐 기반 `emit()`을 한 줄도 고치지 않는다 — sink는
그냥 콜백이라 부르는 쪽이 `run_agent_flow()`든 노드든 차이가 없다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.services.runtime.stream_events import StreamEventSink

# config["configurable"]에 실어 보내는 값들의 키.
SINK_CONFIG_KEY = "stream_event_sink"
LLM_CONFIG_KEY = "llm_provider"
# 추천 파이프라인 노드가 함께 받는 의존성 묶음(graph/pipeline_state.py 참고).
DEPS_CONFIG_KEY = "pipeline_deps"


def sink_from_config(config: RunnableConfig | None) -> StreamEventSink | None:
    """노드에 주입된 config에서 SSE sink를 꺼낸다.

    없으면 None이다 — 비스트리밍 호출(`POST /api/chat`)에서는 sink 자체가 없고,
    그때는 기존 코드와 마찬가지로 이벤트를 내보내지 않는다.

    노드 함수는 두 번째 인자를 반드시 ``config: RunnableConfig``로 **타입
    어노테이션**해야 LangGraph가 주입한다. ``dict``로 적으면 주입되지 않고
    ``TypeError: missing 1 required positional argument``가 난다(1.2.x
    ``_runnable.py``가 어노테이션으로 판별 — 스파이크에서 실제로 겪었다).
    """

    if not config:
        return None
    configurable: dict[str, Any] = config.get("configurable") or {}
    return configurable.get(SINK_CONFIG_KEY)


def llm_from_config(config: RunnableConfig | None) -> Any:
    """노드에 주입된 config에서 LLMProvider를 꺼낸다.

    sink와 같은 통로로 넘긴다 — 그래프는 provider를 직접 만들지 않고 호출부가 이미
    조립해둔 것을 그대로 받는다(Fake/Real 전환이 factory 한 곳에 남게 하려고).
    """

    if not config:
        raise ValueError("그래프 config에 LLMProvider가 없습니다.")
    configurable: dict[str, Any] = config.get("configurable") or {}
    llm = configurable.get(LLM_CONFIG_KEY)
    if llm is None:
        raise ValueError("그래프 config에 LLMProvider가 없습니다.")
    return llm


def deps_from_config(config: RunnableConfig | None) -> Any:
    """추천 파이프라인 노드가 쓰는 의존성 묶음(PipelineDeps)을 꺼낸다."""

    if not config:
        raise ValueError("그래프 config에 파이프라인 의존성이 없습니다.")
    configurable: dict[str, Any] = config.get("configurable") or {}
    deps = configurable.get(DEPS_CONFIG_KEY)
    if deps is None:
        raise ValueError("그래프 config에 파이프라인 의존성이 없습니다.")
    return deps


__all__ = [
    "DEPS_CONFIG_KEY",
    "LLM_CONFIG_KEY",
    "SINK_CONFIG_KEY",
    "deps_from_config",
    "llm_from_config",
    "sink_from_config",
]

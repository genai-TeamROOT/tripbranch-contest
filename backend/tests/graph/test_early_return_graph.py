"""조기 반환 경로 라우팅 그래프 회귀 테스트(1·2단계).

이관은 **출력이 같아야 하는 작업**이다(docs/design/langgraph-adoption.md §6.2).
그래서 여기서 고정하는 것은 "그래프가 동작한다"가 아니라 **"그래프를 켜도 기존
경로와 결과가 같다"**는 쪽이다 — 켜고 끈 두 실행을 같은 Fake LLM으로 돌려 비교한다.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.providers.stub import FakeLLMProvider
from app.schemas import GeneralPayload, GeneralTopic, Intent, LLMOutput, OutputStatus
from app.services.runtime.graph import run_early_return_graph
from app.services.runtime.response_composer import compose_chat_message


def _general_output() -> LLMOutput:
    return LLMOutput(
        intent=Intent.GENERAL,
        status=OutputStatus.COMPLETE,
        general=GeneralPayload(
            topic=GeneralTopic.SERVICE_IDENTITY, original_question="안녕"
        ),
    )


@pytest.fixture(autouse=True)
def _restore_flag():
    original = settings.use_langgraph_early_return
    yield
    settings.use_langgraph_early_return = original


@pytest.mark.asyncio
async def test_graph_answer_matches_legacy_compose() -> None:
    """그래프가 낸 답변이 기존 compose_chat_message()와 같아야 한다."""

    llm_output = _general_output()

    legacy = await compose_chat_message(llm_output, llm=FakeLLMProvider())
    through_graph = await run_early_return_graph(
        llm_output, llm=FakeLLMProvider()
    )

    assert through_graph == legacy


@pytest.mark.asyncio
async def test_graph_emits_same_sse_sequence_as_legacy() -> None:
    """SSE 이벤트 이름 순서가 기존 경로와 같아야 한다.

    §9.4가 "3단계의 가장 큰 위험"으로 지목한 부분이라, 순서를 여기서 못 박는다.
    프론트가 이 순서에 의존한다(message_start로 로딩 말풍선을 열고 delta로 채운다).
    """

    events: list[str] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append(event)

    await run_early_return_graph(
        _general_output(),
        llm=FakeLLMProvider(),
        stream_event_sink=sink,
        stream_general=True,
    )

    assert events[0] == "progress"
    assert events[1] == "message_start"
    assert set(events[2:]) == {"message_delta"}
    assert len(events) >= 3


@pytest.mark.asyncio
async def test_graph_without_sink_emits_nothing() -> None:
    """단발 POST /api/chat 경로(sink 없음)에서는 이벤트를 내지 않는다."""

    answer = await run_early_return_graph(
        _general_output(), llm=FakeLLMProvider()
    )

    assert answer  # 답변은 정상적으로 나온다


@pytest.mark.asyncio
async def test_flag_off_falls_back_to_legacy_path() -> None:
    """플래그를 끄면 그래프를 아예 부르지 않는다 — 되돌리기 경로가 살아있는지 확인."""

    from unittest.mock import patch

    import app.services.runtime.agent_runtime as agent_runtime
    from app.schemas import AgentRequest

    settings.use_langgraph_early_return = False
    with patch.object(agent_runtime, "run_early_return_graph") as spy:
        response = await agent_runtime.run_agent(AgentRequest(user_input="넌 누구야?"))

    spy.assert_not_called()
    assert response.llm_output.intent is Intent.GENERAL
    assert response.message


@pytest.mark.asyncio
async def test_flag_on_routes_general_through_graph() -> None:
    """플래그가 켜져 있으면 GENERAL이 그래프를 탄다."""

    from unittest.mock import patch

    import app.services.runtime.agent_runtime as agent_runtime
    from app.schemas import AgentRequest

    settings.use_langgraph_early_return = True
    real = agent_runtime.run_early_return_graph
    seen: list[str] = []

    async def spy(llm_output, **kwargs):
        seen.append(llm_output.intent.value)
        return await real(llm_output, **kwargs)

    with patch.object(agent_runtime, "run_early_return_graph", spy):
        response = await agent_runtime.run_agent(AgentRequest(user_input="넌 누구야?"))

    assert seen == ["GENERAL"]
    assert response.llm_output.intent is Intent.GENERAL


# ── 2단계: 조기 반환 경로 전체 ────────────────────────────────────────


def _out_of_scope_output() -> LLMOutput:
    from app.schemas import OutOfScopeCategory, OutOfScopePayload, Severity

    return LLMOutput(
        intent=Intent.OUT_OF_SCOPE,
        status=OutputStatus.COMPLETE,
        out_of_scope=OutOfScopePayload(
            category=OutOfScopeCategory.UNRELATED, severity=Severity.LOW
        ),
    )


def _clarification_output() -> LLMOutput:
    from app.schemas import ClarificationPayload

    return LLMOutput(
        intent=Intent.RECOMMEND,
        status=OutputStatus.NEEDS_CLARIFICATION,
        clarification=ClarificationPayload(message="어디 근처에서 찾아드릴까요?"),
    )


@pytest.mark.parametrize(
    ("label", "factory"),
    [
        ("out_of_scope", _out_of_scope_output),
        ("clarification", _clarification_output),
        ("general", _general_output),
    ],
)
@pytest.mark.asyncio
async def test_static_paths_match_legacy_compose(label: str, factory) -> None:
    """조각 없이 만드는 경로들이 기존 compose_chat_message()와 같은 문자열을 낸다."""

    llm_output = factory()

    legacy = await compose_chat_message(llm_output, llm=FakeLLMProvider())
    through_graph = await run_early_return_graph(
        llm_output, llm=FakeLLMProvider()
    )

    assert through_graph == legacy


@pytest.mark.parametrize(
    ("label", "factory"),
    [("out_of_scope", _out_of_scope_output), ("clarification", _clarification_output)],
)
@pytest.mark.asyncio
async def test_non_general_emits_no_stream_events(label: str, factory) -> None:
    """GENERAL이 아니면 sink가 있어도 이벤트를 내지 않는다 — 기존 동작과 같다."""

    events: list[str] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append(event)

    await run_early_return_graph(
        factory(),
        llm=FakeLLMProvider(),
        stream_event_sink=sink,
        stream_general=True,
    )

    assert events == []


@pytest.mark.asyncio
async def test_general_without_streaming_goes_static() -> None:
    """단발 POST /api/chat(스트리밍 아님)의 GENERAL은 이벤트 없이 문자열만 낸다."""

    events: list[str] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append(event)

    answer = await run_early_return_graph(
        _general_output(),
        llm=FakeLLMProvider(),
        stream_event_sink=sink,
        stream_general=False,
    )

    assert events == []
    assert answer


def test_routing_table_covers_both_branches() -> None:
    """조건부 엣지 길 안내원의 판정을 표로 고정한다."""

    from app.services.runtime.graph.routing import (
        ROUTE_GENERAL,
        ROUTE_STATIC,
        route_early_return,
    )

    general = _general_output()
    assert route_early_return({"llm_output": general, "stream_general": True, "answer": None}) == (
        ROUTE_GENERAL
    )
    assert route_early_return(
        {"llm_output": general, "stream_general": False, "answer": None}
    ) == ROUTE_STATIC
    assert route_early_return(
        {"llm_output": _out_of_scope_output(), "stream_general": True, "answer": None}
    ) == ROUTE_STATIC

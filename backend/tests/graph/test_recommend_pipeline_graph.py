"""추천 파이프라인 그래프(3단계) 회귀 테스트.

조기 반환 그래프와 마찬가지로, 고정하는 것은 "그래프가 동작한다"가 아니라
**"그래프를 켜도 기존 경로와 결과가 같다"**는 쪽이다
(docs/design/langgraph-adoption.md §6.2).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.schemas import AgentRequest, Intent
from app.services.runtime import run_agent


@pytest.fixture(autouse=True)
def _restore_flag():
    original = settings.use_langgraph_pipeline
    yield
    settings.use_langgraph_pipeline = original


async def _run(*, flag: bool, text: str):
    settings.use_langgraph_pipeline = flag
    return await run_agent(AgentRequest(user_input=text))


@pytest.mark.parametrize(
    ("label", "text", "expected_intent"),
    [
        ("recommend", "경복궁 근처 카페 추천해줘", Intent.RECOMMEND),
        ("schedule", "경복궁이랑 인사동 3시간 일정 짜줘", Intent.SCHEDULE),
    ],
)
@pytest.mark.asyncio
async def test_graph_matches_legacy_pipeline(
    label: str, text: str, expected_intent: Intent
) -> None:
    """Tool·Scoring을 거치는 경로에서 그래프 on/off 결과가 같아야 한다."""

    legacy = await _run(flag=False, text=text)
    through_graph = await _run(flag=True, text=text)

    assert legacy.llm_output.intent is expected_intent
    assert through_graph.llm_output.intent is expected_intent
    assert through_graph.message == legacy.message
    assert (through_graph.recommendations is None) == (legacy.recommendations is None)
    assert (through_graph.schedule is None) == (legacy.schedule is None)


@pytest.mark.asyncio
async def test_flag_off_skips_the_graph() -> None:
    """플래그를 끄면 파이프라인 그래프를 아예 부르지 않는다 — 되돌리기 경로 확인."""

    from unittest.mock import patch

    import app.services.runtime.agent_runtime as agent_runtime

    settings.use_langgraph_pipeline = False
    with patch.object(agent_runtime, "run_recommend_pipeline_graph") as spy:
        response = await run_agent(AgentRequest(user_input="경복궁 근처 카페 추천해줘"))

    spy.assert_not_called()
    assert response.message


@pytest.mark.asyncio
async def test_flag_on_routes_through_the_graph() -> None:
    """플래그가 켜져 있으면 Tool 경로가 그래프를 탄다."""

    from unittest.mock import patch

    import app.services.runtime.agent_runtime as agent_runtime

    settings.use_langgraph_pipeline = True
    real = agent_runtime.run_recommend_pipeline_graph
    seen: list[str] = []

    async def spy(state, **kwargs):
        seen.append(state["llm_output"].intent.value)
        return await real(state, **kwargs)

    with patch.object(agent_runtime, "run_recommend_pipeline_graph", spy):
        await run_agent(AgentRequest(user_input="경복궁 근처 카페 추천해줘"))

    assert seen == ["RECOMMEND"]


def test_pipeline_routing_table() -> None:
    """조건부 엣지 판정을 표로 고정한다."""

    from types import SimpleNamespace

    from app.schemas import ClarificationPayload, LLMOutput, OutputStatus
    from app.services.runtime.graph.routing import (
        ROUTE_DONE,
        ROUTE_FINALIZE,
        ROUTE_RETRY_TOOL_FETCH,
        ROUTE_SCHEDULE,
        ROUTE_SCORING,
        route_after_scoring,
        route_after_tool_fetch,
    )

    assert route_after_tool_fetch({"response": object()}) == ROUTE_DONE
    assert route_after_tool_fetch({"response": None}) == ROUTE_SCORING

    def _clarified(code: str) -> SimpleNamespace:
        return SimpleNamespace(
            llm_output=SimpleNamespace(clarification=ClarificationPayload(code=code, message=""))
        )

    # A-1: no_data_empty는 아직 재시도 전이면(retry_count 없음/0) 곧장 안 끝내고
    # widen_search_retry로 보낸다. 한 번 재시도한 뒤에는 다른 종료 status처럼 끝낸다.
    assert (
        route_after_tool_fetch({"response": _clarified("no_data_empty")}) == ROUTE_RETRY_TOOL_FETCH
    )
    assert (
        route_after_tool_fetch({"response": _clarified("no_data_empty"), "retry_count": 1})
        == ROUTE_DONE
    )
    # no_data_exhausted(카테고리 확대 등 다른 처방이 필요)는 반경을 넓혀도 소용없어
    # 자동 재시도하지 않는다.
    assert route_after_tool_fetch({"response": _clarified("no_data_exhausted")}) == ROUTE_DONE

    schedule = LLMOutput(intent=Intent.SCHEDULE, status=OutputStatus.COMPLETE)
    recommend = LLMOutput(intent=Intent.RECOMMEND, status=OutputStatus.COMPLETE)
    assert route_after_scoring({"llm_output": schedule}) == ROUTE_SCHEDULE
    assert route_after_scoring({"llm_output": recommend}) == ROUTE_FINALIZE


def test_graphs_have_no_checkpointer() -> None:
    """그래프에 보관함(checkpointer)을 달지 않는다 — §9.9.

    달면 두 가지가 실제로 깨진다(2026-08-24 실측):
    1. 같은 thread_id의 다음 턴에서, 입력에 안 넘긴 칸이 이전 턴 값으로 남는다
    2. 세션마다 체크포인트가 RAM에 쌓여 줄지 않는다(세션 6개 -> 21건)

    우리 그래프는 한 턴 안에서 시작하고 끝나며 턴 사이 상태는 B(StateStore)가
    자기 계약으로 관리하므로, 보관함은 이득 없이 위 두 위험만 안는다. 실수로
    다시 달리는 것을 막으려고 여기서 고정한다.
    """

    from app.services.runtime.graph import (
        build_early_return_graph,
        build_recommend_pipeline_graph,
    )

    for build in (build_early_return_graph, build_recommend_pipeline_graph):
        assert build().checkpointer is None, f"{build.__name__}에 checkpointer가 붙었다"

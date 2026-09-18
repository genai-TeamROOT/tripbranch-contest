"""LLM 실행 기록이 LangGraph 노드 경계를 넘어오는지 고정한다.

**이 테스트가 왜 따로 필요한가.** 기존 그래프 회귀 테스트(같은 폴더의 두 파일)는
`FakeLLMProvider`로 돈다. 그런데 `record_llm_call()`을 실제로 부르는 것은
`RealGeminiProvider` 하나뿐이라, Fake로 도는 테스트에서는 `llm_execution`이 항상
`None`이다 — 즉 이 값에 대해서는 **아무것도 검증하지 않는 상태**였고, 그 탓에
"그래프 on/off 결과가 같다"는 비교도 이 값만은 양쪽 다 `None`인 채로 통과했다
(langgraph-adoption.md §9.13).

그래서 여기서는 **기록을 실제로 남기는 LLM 더블**을 쓴다. Fake를 그대로 쓰면 이
테스트는 고치기 전에도 통과해버려서 아무것도 지켜주지 못한다.

고정하는 것은 세 가지다.
1. 노드 안에서 남긴 기록이 그래프 밖 응답에 실린다 (조기 반환 경로)
2. 노드 안에서 LLM이 실패해도 시도 모델 목록이 전역 오류 핸들러까지 간다
3. 앞 노드가 남긴 기록을 뒤 노드가 읽는다 (추천 파이프라인 — 잠복 회귀 방지)
"""

from __future__ import annotations

import asyncio
from contextvars import copy_context

import pytest

from app.config import settings
from app.errors import AppError
from app.providers.stub import FakeLLMProvider
from app.schemas import AgentRequest, GeneralTopic, Intent
from app.services.runtime.agent_runtime import run_agent_flow
from app.services.runtime.llm_execution import (
    get_llm_execution_metadata,
    record_llm_call,
    reset_llm_execution_metadata,
)
from app.services.runtime.stubs import (
    FakeEnrichmentProvider,
    FakeRecommendationProvider,
    FakeToolProvider,
)
from app.state.store import InMemoryStateStore

DEVICE_LOCATION = "37.5788,126.9770"


class _RecordingLLMProvider(FakeLLMProvider):
    """답변을 만들 때 `record_llm_call()`을 부르는 더블.

    RealGeminiProvider가 `_stream_text`/`_generate` 안에서 하는 일을 최소한으로
    흉내 낸다 — 폴백이 일어난 상황(시도 모델 2개)을 재현해, 감사 패널의 "LLM 폴백"
    칸이 읽는 값까지 함께 고정한다.
    """

    ATTEMPTED = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    SERVED = "gemini-3.5-flash-lite"

    async def generate_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history=None,
    ):
        record_llm_call(
            operation="generate_general_answer",
            attempted_models=list(self.ATTEMPTED),
            served_model=self.SERVED,
            latency_ms=12,
        )
        return await super().generate_general_answer(
            topic, original_question, offer_content=offer_content, history=history
        )


class _FailingLLMProvider(FakeLLMProvider):
    """기록을 남긴 직후 실패하는 더블 — RealGeminiProvider의 실패 경로를 흉내 낸다."""

    async def generate_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history=None,
    ):
        record_llm_call(
            operation="generate_general_answer",
            attempted_models=["gemini-3.5-flash", "gemini-3.5-flash-lite"],
            served_model=None,
        )
        raise AppError(
            code="llm_unavailable",
            message="LLM을 사용할 수 없어요.",
            status_code=502,
            provider="gemini",
        )


def _providers(llm):
    return {
        "llm": llm,
        "tool_provider": FakeToolProvider(),
        "recommendation_provider": FakeRecommendationProvider(),
        "enrichment_provider": FakeEnrichmentProvider(),
    }


def _llm_backed_general_request() -> AgentRequest:
    """고정 안내문이 아닌, LLM으로 답하는 GENERAL 발화를 만든다.

    ``넌 누구야?``는 서비스 기능 안내를 결정적으로 보여주는
    ``SERVICE_IDENTITY`` 경로라 LLM을 호출하지 않는다. 이 파일은 LLM 실행 기록의
    전달을 검증하므로, 실제 생성 경로인 여행 팁 질문을 사용한다.
    """

    return AgentRequest(user_input="서울 여행 팁 알려줘", session_id=None)


# ── 1. 조기 반환 경로 — 노드 안 기록이 그래프 밖까지 온다 ──────────────


@pytest.fixture(autouse=True)
def _restore_flags():
    early = settings.use_langgraph_early_return
    pipeline = settings.use_langgraph_pipeline
    yield
    settings.use_langgraph_early_return = early
    settings.use_langgraph_pipeline = pipeline


@pytest.mark.parametrize("graph_on", [True, False])
@pytest.mark.asyncio
async def test_general_llm_record_survives_the_graph(graph_on: bool) -> None:
    """GENERAL 답변은 노드 안에서 만들어진다. 그 기록이 응답에 실려야 한다.

    플래그 on/off 양쪽을 같은 단정으로 돌린다 — 이관은 출력이 같아야 하는
    작업이므로(§6.2), 한쪽만 통과하면 그게 곧 회귀다.
    """

    settings.use_langgraph_early_return = graph_on
    response = await run_agent_flow(
        _llm_backed_general_request(),
        store=InMemoryStateStore(),
        **_providers(_RecordingLLMProvider()),
    )

    assert response.llm_output.intent is Intent.GENERAL
    assert response.llm_execution is not None, "노드 안에서 남긴 기록이 사라졌다"
    operations = [call.operation for call in response.llm_execution.calls]
    assert "generate_general_answer" in operations

    call = next(
        c for c in response.llm_execution.calls if c.operation == "generate_general_answer"
    )
    # 감사 패널의 "LLM 응답 모델"·"LLM 폴백" 칸이 읽는 두 값.
    assert call.served_model == _RecordingLLMProvider.SERVED
    assert len(call.attempted_models) > 1, "폴백 여부를 판정할 근거가 사라졌다"


@pytest.mark.asyncio
async def test_graph_and_legacy_report_the_same_records() -> None:
    """그래프 on/off의 기록 목록이 같아야 한다 — §9.10 비교에서 빠졌던 축이다."""

    async def _run(*, graph_on: bool) -> list[str]:
        settings.use_langgraph_early_return = graph_on
        response = await run_agent_flow(
            _llm_backed_general_request(),
            store=InMemoryStateStore(),
            **_providers(_RecordingLLMProvider()),
        )
        assert response.llm_execution is not None
        return [call.operation for call in response.llm_execution.calls]

    assert await _run(graph_on=True) == await _run(graph_on=False)


# ── 2. 실패 경로 — 시도 모델 목록이 전역 오류 핸들러까지 간다 ──────────


@pytest.mark.parametrize("graph_on", [True, False])
@pytest.mark.asyncio
async def test_failure_inside_node_leaves_records_for_the_error_handler(
    graph_on: bool,
) -> None:
    """노드 안에서 LLM이 죽어도 요청 문맥에 시도 모델이 남아야 한다.

    `main.py`의 `handle_app_error()`가 502 응답 본문에 실을 때 읽는 값이다. 그
    핸들러는 요청 문맥에서 읽으므로, 여기서도 예외를 잡은 **바깥**에서 읽는다.
    """

    settings.use_langgraph_early_return = graph_on

    with pytest.raises(AppError):
        await run_agent_flow(
            _llm_backed_general_request(),
            store=InMemoryStateStore(),
            **_providers(_FailingLLMProvider()),
        )

    metadata = get_llm_execution_metadata()
    assert metadata is not None, "실패한 턴의 시도 모델 목록이 사라졌다"
    assert metadata.calls[-1].attempted_models == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert metadata.calls[-1].served_model is None


# ── 3. 추천 파이프라인 — 앞 노드 기록을 뒤 노드가 읽는다 ────────────────


@pytest.mark.asyncio
async def test_earlier_pipeline_node_records_reach_the_final_response(monkeypatch) -> None:
    """앞 노드(scoring)가 남긴 기록이 최종 응답에 실려야 한다.

    지금 `tool_fetch`·`scoring`은 LLM을 부르지 않아 실제로 잃을 기록이 0건이다.
    그래서 이 테스트는 **앞 노드에 LLM 호출이 하나 생긴 상황을 만들어** 고정한다 —
    누가 그런 호출을 추가했을 때 그 한 줄이 조용히 빠지는 것이 원래의 잠복 회귀였고
    (§9.13), 예외도 로그도 나지 않아 아무도 알아채지 못하는 종류였다.
    """

    import app.services.runtime.agent_runtime as agent_runtime

    settings.use_langgraph_pipeline = True
    original = agent_runtime._score_recommendations

    async def scoring_with_llm_call(*args, **kwargs):
        record_llm_call(
            operation="scoring_probe",
            attempted_models=["gemini-3.5-flash"],
            served_model="gemini-3.5-flash",
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(agent_runtime, "_score_recommendations", scoring_with_llm_call)

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=InMemoryStateStore(),
        **_providers(_RecordingLLMProvider()),
    )

    assert response.llm_execution is not None
    assert "scoring_probe" in [call.operation for call in response.llm_execution.calls], (
        "앞 노드가 남긴 기록이 뒤 노드의 응답 조립에서 빠졌다"
    )


# ── 4. reset을 거치지 않은 문맥 — 오류 핸들러가 터지지 않아야 한다 ───────


def test_reading_without_reset_returns_none_instead_of_raising() -> None:
    """`reset_llm_execution_metadata()`를 안 부른 문맥에서도 읽기가 안전해야 한다.

    `handle_app_error()`는 **전역** AppError 핸들러다. `/api/transcribe`처럼
    `run_agent()`를 거치지 않는 라우트도 이 핸들러를 타므로, 여기서 예외가 나면
    502 계약이 500 미처리 예외로 깨진다. ContextVar의 기본값을 지우면 정확히 그
    일이 일어나므로(LookupError), 기본값이 남아 있는지를 여기서 못 박는다.
    """

    assert copy_context().run(get_llm_execution_metadata) is None


def test_records_do_not_leak_between_requests() -> None:
    """기본값에 리스트를 두면 모든 요청이 같은 이력을 공유한다 — 그것도 막는다."""

    def _one_request(operation: str) -> list[str]:
        reset_llm_execution_metadata()
        record_llm_call(
            operation=operation, attempted_models=["m"], served_model="m"
        )
        metadata = get_llm_execution_metadata()
        assert metadata is not None
        return [call.operation for call in metadata.calls]

    assert copy_context().run(_one_request, "first") == ["first"]
    assert copy_context().run(_one_request, "second") == ["second"]


@pytest.mark.asyncio
async def test_records_do_not_leak_between_concurrent_requests() -> None:
    """동시에 도는 두 요청의 이력이 섞이지 않아야 한다 — ContextVar를 쓰는 이유."""

    async def _one_turn(operation: str) -> list[str]:
        reset_llm_execution_metadata()
        await asyncio.sleep(0)  # 두 턴이 실제로 겹치게 만든다
        record_llm_call(operation=operation, attempted_models=["m"], served_model="m")
        await asyncio.sleep(0)
        metadata = get_llm_execution_metadata()
        assert metadata is not None
        return [call.operation for call in metadata.calls]

    first, second = await asyncio.gather(_one_turn("first"), _one_turn("second"))

    assert first == ["first"]
    assert second == ["second"]

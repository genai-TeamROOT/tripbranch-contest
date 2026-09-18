"""LLM 토큰 사용량이 응답에서 B의 Trace까지 실제로 도달하는지 검증한다.

2026-08-25까지 `record_trace(token_usage=...)`는 **항상 None**이었다. 계약
(llmops-trace-contract-v1.md 2절)에 필드는 있었는데 `providers/gemini.py`가 응답의
`usage_metadata`를 아예 안 읽어서, A가 넘길 값 자체가 없었다. 이 스위트가 그 통로를
고정한다 — Gemini 응답 → `record_llm_call()` → `consumed_tokens()`.

**None과 0을 구분하는 것이 요점이다.** 값을 못 읽으면 0이 아니라 None이어야 한다.
0으로 채우면 "안 썼다"와 "모른다"가 같아져서, 토큰이 안 잡히는 회귀가 조용히 묻힌다.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.gemini import RealGeminiProvider, _token_usage, _usage_details
from app.schemas import Intent, IntentClassificationResult
from app.services.runtime.llm_execution import (
    consumed_tokens,
    get_llm_execution_metadata,
    record_llm_call,
    reset_llm_execution_metadata,
)


class _Usage:
    """google-genai `usage_metadata` 흉내. 없는 필드는 아예 두지 않는다."""

    def __init__(self, **fields: int) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


class _FakeResponse:
    def __init__(self, parsed: Any, usage: _Usage | None = None) -> None:
        self.parsed = parsed
        self.text = None
        self.usage_metadata = usage


def _classification() -> IntentClassificationResult:
    return IntentClassificationResult(intent=Intent.RECOMMEND)


@pytest.fixture(autouse=True)
def _fresh_execution_context() -> Any:
    reset_llm_execution_metadata()
    yield
    reset_llm_execution_metadata()


# --- 필드 매핑 -----------------------------------------------------------------


def test_token_usage_maps_every_field_we_care_about() -> None:
    usage = _token_usage(
        _Usage(
            prompt_token_count=120,
            candidates_token_count=45,
            thoughts_token_count=200,
            total_token_count=365,
        )
    )

    assert usage == {
        "input_tokens": 120,
        "output_tokens": 45,
        "thoughts_tokens": 200,
        "total_tokens": 365,
    }


def test_token_usage_maps_the_cached_input_share() -> None:
    """자동 캐싱 적중분. prompt_token_count에 이미 포함돼 오므로 따로 센다."""
    usage = _token_usage(
        _Usage(prompt_token_count=5000, cached_content_token_count=4200)
    )

    assert usage == {"input_tokens": 5000, "cached_tokens": 4200}


def test_token_usage_omits_missing_fields_instead_of_zeroing_them() -> None:
    """사고를 안 하는 모델은 thoughts_token_count 자체가 없다.

    0으로 채우면 "사고 토큰을 안 썼다"가 되는데, 실제로는 "이 모델은 그 값을 안
    준다"이다. 키를 빼서 둘을 구분한다.
    """
    usage = _token_usage(_Usage(prompt_token_count=10, total_token_count=10))

    assert usage == {"input_tokens": 10, "total_tokens": 10}


def test_token_usage_of_a_response_without_metadata_is_empty() -> None:
    assert _token_usage(None) == {}
    assert _token_usage(_Usage()) == {}


def test_token_usage_ignores_non_integer_values() -> None:
    """SDK가 형태를 바꾸거나 필드가 None으로 오면 조용히 버린다."""
    usage = _token_usage(_Usage(prompt_token_count=None, candidates_token_count=7))  # type: ignore[arg-type]

    assert usage == {"output_tokens": 7}


# --- Langfuse usage_details ----------------------------------------------------


def test_usage_details_bills_thinking_tokens_as_output() -> None:
    """Gemini 3.x의 thoughts는 candidates_token_count에 안 잡히는데 과금은 된다.

    빼고 보내면 Langfuse 비용 화면이 과소 집계된다. 원래 값은 thoughts로 따로 남겨
    어느 쪽이 얼마인지 볼 수 있게 한다.
    """
    details = _usage_details(
        {
            "input_tokens": 120,
            "output_tokens": 45,
            "thoughts_tokens": 200,
            "total_tokens": 365,
        }
    )

    assert details == {"input": 120, "output": 245, "thoughts": 200, "total": 365}


def test_usage_details_without_thinking_leaves_output_alone() -> None:
    details = _usage_details({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

    assert details == {"input": 10, "output": 5, "total": 15}


def test_usage_details_reports_cache_without_shrinking_input() -> None:
    """캐시분을 input에서 빼지 않는다.

    빼면 Langfuse의 입력 토큰 합계가 실제 전송량과 어긋난다. 캐시는 요율의
    문제이지 전송량의 문제가 아니다 — 별도 키로만 보인다.
    """
    details = _usage_details(
        {"input_tokens": 5000, "cached_tokens": 4200, "output_tokens": 120}
    )

    assert details == {"input": 5000, "cached": 4200, "output": 120}


def test_usage_details_of_nothing_is_none() -> None:
    """빈 dict를 보내면 Langfuse에 "토큰 0"으로 찍힌다 — 그건 사실이 아니다."""
    assert _usage_details({}) is None


# --- 통로: 응답 → record_llm_call → consumed_tokens ----------------------------


@pytest.mark.asyncio
async def test_tokens_from_the_response_reach_the_execution_metadata() -> None:
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)
    response = _FakeResponse(
        _classification(),
        _Usage(
            prompt_token_count=120,
            candidates_token_count=45,
            thoughts_token_count=200,
            total_token_count=365,
        ),
    )

    with patch.object(
        provider._client.aio.models, "generate_content", new=AsyncMock(return_value=response)
    ):
        await provider._generate("sys", "user", IntentClassificationResult, "classify_intent")

    metadata = get_llm_execution_metadata()
    assert metadata is not None
    call = metadata.calls[0]
    assert (call.input_tokens, call.output_tokens) == (120, 45)
    assert (call.thoughts_tokens, call.total_tokens) == (200, 365)
    assert consumed_tokens() == 365


@pytest.mark.asyncio
async def test_a_response_without_usage_metadata_records_none_not_zero() -> None:
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)

    with patch.object(
        provider._client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=_FakeResponse(_classification())),
    ):
        await provider._generate("sys", "user", IntentClassificationResult, "classify_intent")

    metadata = get_llm_execution_metadata()
    assert metadata is not None
    assert metadata.calls[0].total_tokens is None
    # 호출은 있었지만 값을 모른다 — 0이 아니라 None이어야 한다.
    assert consumed_tokens() is None


@pytest.mark.asyncio
async def test_fallback_records_the_serving_model_tokens_not_the_failed_one() -> None:
    """모델을 바꿀 때 앞 모델의 토큰이 남아 있으면 안 된다.

    1순위가 재시도를 소진하고 2순위가 응답하면, 기록되는 토큰은 2순위 것이다.
    """
    provider = RealGeminiProvider(
        api_key="dummy", model_names=["first", "second"], timeout_seconds=1.0, max_retries=0
    )
    served = _FakeResponse(_classification(), _Usage(prompt_token_count=7, total_token_count=9))

    async def by_model(*_: object, **kwargs: object) -> _FakeResponse:
        if kwargs.get("model") == "first":
            raise TimeoutError
        return served

    with (
        patch.object(provider._client.aio.models, "generate_content", side_effect=by_model),
        patch("app.providers.gemini.asyncio.sleep", new=AsyncMock()),
        patch("app.providers.gemini.httpx.TimeoutException", TimeoutError),
    ):
        await provider._generate("sys", "user", IntentClassificationResult, "classify_intent")

    metadata = get_llm_execution_metadata()
    assert metadata is not None
    call = metadata.calls[0]
    assert call.served_model == "second"
    assert (call.input_tokens, call.total_tokens) == (7, 9)


# --- consumed_tokens 합계 -------------------------------------------------------


def test_consumed_tokens_sums_every_call_in_the_turn() -> None:
    """한 턴은 분류 + 조건 추출로 최소 두 번 LLM을 부른다."""
    record_llm_call(
        operation="classify_intent",
        attempted_models=["a"],
        served_model="a",
        total_tokens=100,
    )
    record_llm_call(
        operation="extract_recommend_conditions",
        attempted_models=["a"],
        served_model="a",
        total_tokens=250,
    )

    assert consumed_tokens() == 350


def test_consumed_tokens_ignores_calls_that_reported_nothing() -> None:
    """값을 아는 호출이 하나라도 있으면 그 합계는 유효하다."""
    record_llm_call(operation="classify_intent", attempted_models=["a"], served_model=None)
    record_llm_call(
        operation="extract_recommend_conditions",
        attempted_models=["a"],
        served_model="a",
        total_tokens=40,
    )

    assert consumed_tokens() == 40


def test_consumed_tokens_is_none_when_nothing_ran() -> None:
    assert consumed_tokens() is None


# --- 스트리밍: 사용자가 실제로 읽는 문장을 만드는 호출 ---------------------------
#
# 2026-08-25까지 `_stream_text()`에는 계측이 아예 없었다. 14개 operation 중 11개만
# 잡혀서, 답변 생성 토큰이 턴당 비용에서 통째로 빠져 있었다. 이 절이 그 통로를 고정한다.


class _Chunk:
    """스트림 청크 흉내. usage_metadata는 청크마다 실려 오고 누적값이다."""

    def __init__(self, text: str | None = None, usage: _Usage | None = None) -> None:
        self.text = text
        self.usage_metadata = usage


async def _stream(*chunks: _Chunk) -> Any:
    async def _gen() -> Any:
        for chunk in chunks:
            yield chunk

    return _gen()


async def _drain(provider: RealGeminiProvider, chunks: list[_Chunk]) -> list[str]:
    with patch.object(
        provider._client.aio.models,
        "generate_content_stream",
        new=AsyncMock(return_value=await _stream(*chunks)),
    ):
        return [
            piece
            async for piece in provider._stream_text(
                instruction="sys", user_input="user", operation="stream_general_answer"
            )
        ]


@pytest.mark.asyncio
async def test_streaming_tokens_reach_the_execution_metadata() -> None:
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)

    pieces = await _drain(
        provider,
        [
            _Chunk("안녕", _Usage(prompt_token_count=30)),
            _Chunk(
                "하세요",
                _Usage(prompt_token_count=30, candidates_token_count=12, total_token_count=42),
            ),
        ],
    )

    assert pieces == ["안녕", "하세요"]
    metadata = get_llm_execution_metadata()
    assert metadata is not None
    call = metadata.calls[0]
    assert call.operation == "stream_general_answer"
    # 마지막 청크가 누적 총량이다 — 첫 청크의 부분값이 남으면 안 된다.
    assert (call.input_tokens, call.output_tokens, call.total_tokens) == (30, 12, 42)
    assert consumed_tokens() == 42


@pytest.mark.asyncio
async def test_a_stream_without_usage_records_none_not_zero() -> None:
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)

    await _drain(provider, [_Chunk("답변")])

    metadata = get_llm_execution_metadata()
    assert metadata is not None
    assert metadata.calls[0].total_tokens is None
    assert consumed_tokens() is None


@pytest.mark.asyncio
async def test_stream_records_first_chunk_time_as_completion_start() -> None:
    """스트리밍의 체감 지연은 전체 소요가 아니라 첫 글자까지의 시간이다."""
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)
    recorded: list[dict[str, Any]] = []

    class _Recorder:
        def record(self, **fields: Any) -> None:
            recorded.append(fields)

    from contextlib import contextmanager

    from app.providers import gemini as gemini_module

    @contextmanager
    def _fake_observe(name: str, **_: Any) -> Any:
        yield _Recorder()

    with patch.object(gemini_module, "observe_generation", _fake_observe):
        await _drain(provider, [_Chunk("가"), _Chunk("나"), _Chunk("다")])

    starts = [f for f in recorded if "completion_start_time" in f]
    # 첫 조각에서 한 번만 찍는다 — 청크마다 찍으면 마지막 조각 시각이 되어 의미가 없다.
    assert len(starts) == 1
    assert starts[0]["completion_start_time"].tzinfo is not None


@pytest.mark.asyncio
async def test_stream_generation_carries_the_prompt_slot_version() -> None:
    """원문 수집을 꺼도 남는 값이다 — 버전별 비교가 여기 걸려 있다."""
    provider = RealGeminiProvider(api_key="dummy", model_names=["dummy"], timeout_seconds=1.0)
    opened: list[dict[str, Any]] = []

    from contextlib import contextmanager

    from app.providers import gemini as gemini_module

    @contextmanager
    def _fake_observe(name: str, **kwargs: Any) -> Any:
        opened.append({"name": name, **kwargs})

        class _R:
            def record(self, **_: Any) -> None:
                return

        yield _R()

    with patch.object(gemini_module, "observe_generation", _fake_observe):
        await _drain(provider, [_Chunk("답")])

    assert opened[0]["name"] == "stream_general_answer"
    assert opened[0]["version"] == "general.answer@1.0.0"

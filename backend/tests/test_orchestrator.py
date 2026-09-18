"""orchestrator.build_interpretation()의 Intent별 분기 회귀 테스트.

SCHEDULE-04 이전에는 이 파일이 없었다 — SCHEDULE 분기가 extract_recommend_
conditions()를 재사용하도록 바뀌면서(docs/design/int-07-schedule.md 4절) 그
바꿔치기 로직을 직접 검증할 테스트가 필요해졌다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.providers.contracts import ProviderSource, provider_result
from app.providers.stub import FakeLLMProvider
from app.schemas import (
    InfoPayload,
    Intent,
    IntentClassificationResult,
    InteractionMode,
    InterpretRequest,
    LLMOutput,
    OutOfScopeCategory,
    OutOfScopePayload,
    OutputStatus,
    PlaceContext,
    PlaceTag,
    QuestionType,
    RecommendPayload,
    Severity,
    UserConditions,
)
from app.services.interpret.orchestrator import build_interpretation


@pytest.mark.asyncio
async def test_schedule_reuses_recommend_condition_extraction() -> None:
    """SCHEDULE도 RECOMMEND와 같은 15개 조건 추출을 타되, intent만 SCHEDULE로
    바꿔치기된다(6.1절 "기존 15개 조건 그대로 사용")."""
    request = InterpretRequest(
        user_input="경복궁 근처에서 반나절 코스 짜줘",
        has_previous_recommendation=False,
        shown_place_count=0,
        current_conditions=None,
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.SCHEDULE
    assert output.status is OutputStatus.COMPLETE
    assert output.recommend is not None
    assert output.recommend.conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_schedule_can_still_need_clarification() -> None:
    """추출 단계의 되묻기(예: 눈 관련 모호함)도 RECOMMEND와 동일하게 그대로
    전달돼야 한다 — intent 바꿔치기가 status/clarification까지 지우면 안 된다."""
    request = InterpretRequest(
        user_input="눈 오는 날 코스 짜줘",
        has_previous_recommendation=False,
        shown_place_count=0,
        current_conditions=None,
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.SCHEDULE
    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.clarification is not None


@pytest.mark.asyncio
async def test_schedule_with_cafe_marker_sets_place_tags() -> None:
    request = InterpretRequest(
        user_input="카페 위주로 일정 짜줘",
        has_previous_recommendation=False,
        shown_place_count=0,
        current_conditions=None,
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.SCHEDULE
    assert output.recommend is not None
    assert PlaceTag.CAFE in output.recommend.conditions.place_tags


@pytest.mark.asyncio
async def test_info_conversation_reference_uses_previous_info_card_place() -> None:
    """“여기 가는데”는 직전 INFO 카드의 장소를 대상으로 이어져야 한다."""
    request = InterpretRequest(
        user_input="여기 가는데 얼마나 걸려?",
        has_previous_recommendation=False,
        conversation_place_name="건청궁",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.INFO
    assert output.info is not None
    assert output.info.place_context is PlaceContext.FROM_CONVERSATION
    assert output.info.place_name == "건청궁"


@pytest.mark.asyncio
async def test_info_conversation_reference_does_not_overwrite_explicit_place() -> None:
    request = InterpretRequest(
        user_input="경복궁 주차 돼?",
        has_previous_recommendation=False,
        conversation_place_name="건청궁",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.info is not None
    assert output.info.place_context is PlaceContext.EXPLICIT
    assert output.info.place_name == "경복궁"


@pytest.mark.asyncio
async def test_info_extraction_receives_pending_question_context() -> None:
    """직전 INFO 되묻기(장소명 없음)가 저장해둔 질문 정보가 extract_info_query()에
    그대로 전달돼야 한다 — 안 넘기면 자유 텍스트로 장소만 답해도 원래 질문(혼잡도
    등)이 사라진다(2026-08-31 실사용 재현)."""
    llm = FakeLLMProvider()
    with patch.object(
        llm,
        "extract_info_query",
        AsyncMock(
            return_value=provider_result(
                LLMOutput(
                    intent=Intent.INFO,
                    status=OutputStatus.COMPLETE,
                    info=InfoPayload(
                        place_name="창덕궁",
                        place_context=PlaceContext.EXPLICIT,
                        question_type=QuestionType.CONCENTRATION,
                        specific_question="사람많아?",
                    ),
                ),
                source=ProviderSource.FAKE_LLM,
            )
        ),
    ) as mocked_extract:
        request = InterpretRequest(
            user_input="창덕궁",
            has_previous_recommendation=True,
            pending_clarification="missing:place_name",
            last_intent="INFO",
            pending_info_question_type="concentration",
            pending_info_specific_question="사람많아?",
        )
        output = await build_interpretation(request, llm)

    assert output.intent is Intent.INFO
    mocked_extract.assert_awaited_once()
    _, kwargs = mocked_extract.call_args
    assert kwargs["pending_info_question_type"] == "concentration"
    assert kwargs["pending_info_specific_question"] == "사람많아?"


# --- 케이스 4/5(PR 4, docs/design/clarification-options.md): 목적어 없는
# "처음부터 다시" 선제 차단. classify_intent()를 부르지 않고 결정적으로 되묻는지 —
# FakeLLMProvider가 이 발화를 어떻게 분류할지와 무관하게 항상 되물어야 한다.


@pytest.mark.asyncio
async def test_bare_restart_during_schedule_location_ask_triggers_clarification() -> None:
    """케이스 4: SCHEDULE 위치 되묻기 중 목적어 없는 "처음부터 다시"."""
    request = InterpretRequest(
        user_input="처음부터 다시",
        has_previous_recommendation=False,
        shown_place_count=0,
        current_conditions=UserConditions(time_available=240),
        pending_clarification="location_required",
        last_intent="SCHEDULE",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.SCHEDULE
    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.clarification is not None
    assert output.clarification.code == "schedule_bare_restart"
    option_ids = {option.id for option in output.clarification.options}
    assert option_ids == {"restart", "keep_asking"}


@pytest.mark.asyncio
async def test_object_ful_restart_during_schedule_location_ask_falls_through() -> None:
    """목적어가 붙으면("처음부터 다시 짜줘") 케이스 4가 아니라 평소 classify_intent()
    경로를 타야 한다 — D-053 등 기존 규칙과 충돌하지 않는다."""
    request = InterpretRequest(
        user_input="처음부터 다시 짜줘",
        has_previous_recommendation=False,
        shown_place_count=0,
        current_conditions=UserConditions(time_available=240),
        pending_clarification="location_required",
        last_intent="SCHEDULE",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.clarification is None or output.clarification.code != "schedule_bare_restart"


@pytest.mark.asyncio
async def test_bare_restart_during_active_search_uses_condition_phrase() -> None:
    """케이스 5: RECOMMEND 진행 중(되묻기 아님) 목적어 없는 "처음부터 다시"는 현재
    조건을 되묻기 문구/버튼에 그대로 반영한다."""
    request = InterpretRequest(
        user_input="처음부터 다시",
        has_previous_recommendation=True,
        shown_place_count=5,
        current_conditions=UserConditions(search_center="경복궁"),
        pending_clarification=None,
        last_intent="RECOMMEND",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.clarification is not None
    assert output.clarification.code == "bare_restart_active"
    assert output.clarification.message == (
        "경복궁 근처로 다시 알아볼까요, 아니면 새로운 목적지로 찾아볼까요?"
    )
    keep_context = next(o for o in output.clarification.options if o.id == "keep_context")
    assert keep_context.label == "경복궁 근처로 다시 찾아주세요"
    full_reset = next(o for o in output.clarification.options if o.id == "full_reset")
    assert full_reset.label == "새로 시작할게요"


@pytest.mark.asyncio
async def test_bare_restart_during_active_search_without_conditions_uses_generic_phrase() -> None:
    """조건이 전부 비어 있으면(장소/날씨/카테고리 언급 없음) 범용 문구로 폴백한다."""
    request = InterpretRequest(
        user_input="처음부터 다시",
        has_previous_recommendation=True,
        shown_place_count=5,
        current_conditions=UserConditions(),
        pending_clarification=None,
        last_intent="MODIFY",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.clarification is not None
    assert output.clarification.message == "다시 알아볼까요, 아니면 새로운 목적지로 찾아볼까요?"
    keep_context = next(o for o in output.clarification.options if o.id == "keep_context")
    assert keep_context.label == "이대로 다시 찾아주세요"


@pytest.mark.asyncio
async def test_bare_restart_after_schedule_completed_triggers_clarification() -> None:
    """SCHEDULE이 되묻기 없이 완료된 뒤(pending_clarification=None)의 "처음부터
    다시"는 케이스 4(SCHEDULE 위치 되묻기 전용)에도, 케이스 5(RECOMMEND/MODIFY
    전용)에도 안 걸린다 — 아무 규칙도 없으면 SCHEDULE-06이 무조건 같은 조건으로
    재편성을 시도해 후보 부족 시 실패 문구로 샌다(실사용 재현, 2026-08-13).
    SCHEDULE 전용 되묻기로 잡아야 한다."""
    request = InterpretRequest(
        user_input="처음부터 다시",
        has_previous_recommendation=True,
        shown_place_count=3,
        current_conditions=UserConditions(search_center="경복궁"),
        pending_clarification=None,
        last_intent="SCHEDULE",
    )

    output = await build_interpretation(request, FakeLLMProvider())

    assert output.intent is Intent.SCHEDULE
    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.clarification is not None
    assert output.clarification.code == "schedule_bare_restart_completed"
    assert output.clarification.message == (
        "경복궁 근처로 다시 짜드릴까요, 아니면 새로운 목적지로 찾아볼까요?"
    )
    option_ids = {option.id for option in output.clarification.options}
    assert option_ids == {"retry_schedule", "full_reset"}


# ---------------------------------------------------------------- 대화층 2단계
#
# 상황 축(interaction_mode)이 인텐트 라벨보다 안정적이라는 실측
# (2026-08-30, scripts/test_situational_utterances.py — 축 21/21)을 근거로
# orchestrator가 두 가지를 결정적으로 보정한다. 그 두 규칙을 여기서 못 박는다.


class _StubClassification:
    """classify_intent만 원하는 값으로 바꾼 FakeLLMProvider."""

    def __init__(self, intent: Intent, mode: InteractionMode, category=None) -> None:
        self._inner = FakeLLMProvider()
        self._result = IntentClassificationResult(
            intent=intent,
            interaction_mode=mode,
            out_of_scope_category=category,
            # 실제 분류기는 OUT_OF_SCOPE일 때 category와 severity를 함께 채운다.
            out_of_scope_severity=Severity.MEDIUM if category is not None else None,
        )

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def classify_intent(self, *args: object, **kwargs: object):
        return provider_result(self._result, source=ProviderSource.FAKE_LLM)


@pytest.mark.asyncio
async def test_situational_utterance_is_not_rejected_as_out_of_scope() -> None:
    """분류가 OUT_OF_SCOPE로 새도 상황 발화는 거절 문구로 끝나지 않는다.

    "너무 지친다" 같은 발화는 요청이 없어 범위 밖처럼 보이지만 우리가 도울 수
    있는 상황이다. 거절 템플릿이 나가면 대화 자체가 끊긴다.
    """
    llm = _StubClassification(
        Intent.OUT_OF_SCOPE, InteractionMode.SITUATIONAL, OutOfScopeCategory.UNRELATED
    )

    output = await build_interpretation(InterpretRequest(user_input="너무 지친다"), llm)

    assert output.intent is Intent.GENERAL
    assert output.out_of_scope is None
    assert output.general is not None
    assert output.interaction_mode is InteractionMode.SITUATIONAL


@pytest.mark.parametrize(
    "category", [OutOfScopeCategory.HARMFUL, OutOfScopeCategory.PROMPT_INJECTION]
)
@pytest.mark.asyncio
async def test_harmful_utterance_stays_blocked_even_when_situational(
    category: OutOfScopeCategory,
) -> None:
    """유해 발언·인젝션은 축이 situational이어도 차단이 우선이다.

    실측에서 "너 진짜 바보야?"가 situational로 분류됐다 — 축만 보고 구제하면
    욕설이 GENERAL 답변을 받는다.
    """
    llm = _StubClassification(Intent.OUT_OF_SCOPE, InteractionMode.SITUATIONAL, category)

    output = await build_interpretation(InterpretRequest(user_input="너 진짜 바보야?"), llm)

    assert output.intent is Intent.OUT_OF_SCOPE
    assert output.out_of_scope is not None


@pytest.mark.asyncio
async def test_general_extraction_cannot_override_the_classified_intent() -> None:
    """추출기가 OUT_OF_SCOPE를 돌려줘도 분류기의 GENERAL 판정을 뒤집지 못한다.

    extract_general_request()는 LLMOutput 전체를 모델이 채우게 두므로 intent도
    모델이 정한다. 실측에서 분류기가 GENERAL로 잘 보낸 "너무 지친다"를 추출기가
    OUT_OF_SCOPE로 되돌려 결국 거절 문구가 나갔다 — 인텐트를 정하는 건 분류
    단계의 책임이다.
    """
    llm = _StubClassification(Intent.GENERAL, InteractionMode.SITUATIONAL)
    hijacked = LLMOutput(
        intent=Intent.OUT_OF_SCOPE,
        status=OutputStatus.COMPLETE,
        out_of_scope=OutOfScopePayload(
            category=OutOfScopeCategory.UNRELATED, severity=Severity.MEDIUM
        ),
    )
    with patch.object(
        llm._inner,
        "extract_general_request",
        AsyncMock(return_value=provider_result(hijacked, source=ProviderSource.FAKE_LLM)),
    ):
        output = await build_interpretation(InterpretRequest(user_input="너무 지친다"), llm)

    assert output.intent is Intent.GENERAL
    assert output.out_of_scope is None
    # payload가 비면 답변 생성 단계가 쓸 수 없으므로 원문을 담아 채워 준다.
    assert output.general is not None
    assert output.general.original_question == "너무 지친다"


@pytest.mark.asyncio
async def test_direct_request_out_of_scope_still_rejects() -> None:
    """평범한 범위 밖 요청은 지금까지처럼 거절된다(가드가 넓어지지 않았는지)."""
    llm = _StubClassification(
        Intent.OUT_OF_SCOPE, InteractionMode.DIRECT_REQUEST, OutOfScopeCategory.UNRELATED
    )

    output = await build_interpretation(InterpretRequest(user_input="주식 추천해줘"), llm)

    assert output.intent is Intent.OUT_OF_SCOPE
    assert output.out_of_scope is not None


# --- TP-266: 조건 페이로드가 빈손으로 온 턴은 한 번 다시 뽑는다 ------------------
#
# 이 여섯은 "정상 시드로 도는가"가 아니라 **재시도 분기를 지우면 깨지는 입력**으로
# 짰다(함정 25). 1번은 첫 호출이 빈손이어야만 의미가 있고, 3번은 반대로 재시도가
# 항상 도는 것을 잡는 대조군이다.


class _EmptyThenFilledLLM(FakeLLMProvider):
    """첫 조건 추출은 빈손, 두 번째부터는 조건을 실어 오는 provider.

    실측에서 관측된 모양 그대로다 — `gemini-3.5-flash-lite`가 같은 발화에
    `recommend`를 통째로 비워 보내는 회차가 있었고, 같은 프롬프트를 폴백 모델로
    돌리면 채워져 왔다(2026-09-08).
    """

    def __init__(self, *, always_empty: bool = False) -> None:
        super().__init__()
        self.always_empty = always_empty
        self.calls: list[dict] = []

    async def classify_intent(self, user_input, **kwargs):
        return provider_result(
            IntentClassificationResult(
                intent=Intent.RECOMMEND,
                interaction_mode=InteractionMode.DIRECT_REQUEST,
            ),
            source=ProviderSource.FAKE_LLM,
        )

    async def extract_recommend_conditions(self, user_input, **kwargs):
        self.calls.append(kwargs)
        first = len(self.calls) == 1
        if first or self.always_empty:
            return provider_result(
                LLMOutput(intent=Intent.RECOMMEND, status=OutputStatus.COMPLETE),
                source=ProviderSource.FAKE_LLM,
            )
        return provider_result(
            LLMOutput(
                intent=Intent.RECOMMEND,
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(
                    conditions=UserConditions(search_center="경복궁", time_available=300)
                ),
            ),
            source=ProviderSource.FAKE_LLM,
        )


@pytest.mark.asyncio
async def test_empty_condition_payload_is_extracted_again() -> None:
    """빈손이면 다시 뽑고, 그 결과의 조건이 실제로 실려 나가야 한다.

    재시도 분기를 지우면 `output.recommend`가 None이라 이 단정이 깨진다.
    """
    llm = _EmptyThenFilledLLM()
    request = InterpretRequest(user_input="경복궁 근처에서 5시간 코스 짜줘")

    output = await build_interpretation(request, llm)

    assert len(llm.calls) == 2
    assert output.recommend is not None
    assert output.recommend.conditions.search_center == "경복궁"
    assert output.recommend.conditions.time_available == 300


@pytest.mark.asyncio
async def test_retry_uses_the_configured_fast_fallback_models() -> None:
    """재시도 모델을 새로 정하지 않고 `llm_fast_fallback_model_names`를 따른다.

    1순위는 방금 빈손을 낸 모델이므로 빼고 2순위부터 넘긴다. 이 단정이 깨지면
    같은 모델로 다시 물어 값만 두 번 내는 상태다.
    """
    llm = _EmptyThenFilledLLM()

    await build_interpretation(InterpretRequest(user_input="경복궁 코스 짜줘"), llm)

    assert "retry_models" not in llm.calls[0]
    assert llm.calls[1]["retry_models"] == settings.resolved_llm_fast_models[1:]
    assert settings.resolved_llm_fast_models[0] not in llm.calls[1]["retry_models"]


@pytest.mark.asyncio
async def test_filled_payload_is_not_extracted_twice() -> None:
    """처음부터 조건이 실려 오면 다시 뽑지 않는다 — 대조군.

    이것이 없으면 "항상 두 번 부른다"로 바꿔도 위 두 테스트가 통과한다. 그러면
    모든 턴이 값을 두 배로 낸다.
    """
    llm = _EmptyThenFilledLLM()
    llm.calls.append({})  # 첫 호출을 이미 쓴 것으로 만들어 곧바로 채워 오게 한다

    await build_interpretation(InterpretRequest(user_input="경복궁 코스 짜줘"), llm)

    assert len(llm.calls) == 2  # 미리 넣은 1건 + 실제 호출 1건


@pytest.mark.asyncio
async def test_retry_happens_only_once() -> None:
    """두 번째도 빈손이면 그대로 진행한다 — 발화당 호출이 무한히 늘지 않는다."""
    llm = _EmptyThenFilledLLM(always_empty=True)

    output = await build_interpretation(InterpretRequest(user_input="경복궁 코스 짜줘"), llm)

    assert len(llm.calls) == 2
    assert output.recommend is None


@pytest.mark.asyncio
async def test_schedule_turn_keeps_its_intent_after_the_retry() -> None:
    """SCHEDULE도 같은 재시도를 타되 intent 바꿔치기가 유지돼야 한다.

    재시도 결과를 그대로 돌려주면서 `model_copy(intent=SCHEDULE)`를 빠뜨리면
    SCHEDULE 발화가 RECOMMEND로 처리된다.
    """
    llm = _EmptyThenFilledLLM()
    with patch.object(
        llm,
        "classify_intent",
        AsyncMock(
            return_value=provider_result(
                IntentClassificationResult(
                    intent=Intent.SCHEDULE,
                    interaction_mode=InteractionMode.DIRECT_REQUEST,
                ),
                source=ProviderSource.FAKE_LLM,
            )
        ),
    ):
        output = await build_interpretation(
            InterpretRequest(user_input="경복궁 코스 짜줘"), llm
        )

    assert len(llm.calls) == 2
    assert output.intent is Intent.SCHEDULE
    assert output.recommend is not None


@pytest.mark.asyncio
async def test_intents_that_carry_no_conditions_never_retry() -> None:
    """INFO처럼 조건을 나르지 않는 턴은 `recommend`가 None인 것이 정상이다.

    여기서 재시도가 돌면 INFO·GENERAL·COMPARE 턴마다 쓸데없는 호출이 하나씩 붙는다.
    """
    llm = _EmptyThenFilledLLM()
    with patch.object(
        llm,
        "classify_intent",
        AsyncMock(
            return_value=provider_result(
                IntentClassificationResult(
                    intent=Intent.INFO,
                    interaction_mode=InteractionMode.DIRECT_REQUEST,
                ),
                source=ProviderSource.FAKE_LLM,
            )
        ),
    ):
        output = await build_interpretation(
            InterpretRequest(user_input="창덕궁 사람 많아?"), llm
        )

    assert llm.calls == []
    assert output.intent is Intent.INFO


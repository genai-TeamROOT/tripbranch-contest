"""LLMProvider 계약과 Gemini 실제 구현.

계약: 사용자 자연어 발화를 구조화된 Intent/Conditions(LLMOutput)로 변환한다. 1단계
Intent 분류와 2단계 Intent별 조건 추출을 별도 호출로 나눠 수행한다(설계는
docs/design/intent-definition.md, docs/design/llm-output-schema.md 참고). 구조화 출력
검증 실패는 1회 재시도하고, 그래도 실패하면 AppError(code="llm_output_invalid")를 던진다.
타임아웃/429/5xx 같은 일시적 오류는 지수 백오프로 별도 재시도한다(둘은 독립적인 재시도다 —
검증 재시도는 "응답은 왔지만 스키마가 안 맞음", 백오프 재시도는 "응답 자체가 안 옴"을 다룬다).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import TypeVar

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, Field, ValidationError

from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput
from app.errors import AppError, ProviderTimeoutError, ProviderUnavailableError
from app.observability import langfuse_prompts
from app.observability.api_usage import record_call
from app.observability.langfuse_tracing import observe_generation
from app.prompts.registry import operation_entry_template, operation_prompt_version
from app.providers import gemini_prompts
from app.providers.contracts import ProviderResult, ProviderSource, provider_result
from app.schedule.budget import clusterable_slot_count, derive_item_range
from app.schedule.schemas import (
    ScheduleLLMPlan,
    SchedulePartialFillRequest,
    SchedulePartialLLMPlan,
    SchedulePlanningRequest,
)
from app.schemas import (
    ComparisonResult,
    ConversationTurnView,
    GeneralTopic,
    Intent,
    IntentClassificationResult,
    LLMOutput,
    PlacePreferenceInsight,
    RecommendationItem,
    RecommendationResponse,
    UserConditions,
)
from app.services.runtime.llm_execution import record_llm_call

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class _GeneralAnswer(BaseModel):
    """generate_general_answer() 전용 구조화 출력 wire 모델. 다른 곳에서 쓰지 않는다."""

    answer: str


class _RecommendationSummary(BaseModel):
    """generate_recommendation_summary() 전용 wire 모델."""

    message: str


class _PlaceReason(BaseModel):
    """generate_place_reason() 전용 wire 모델."""

    reason: str


class _ReviewEvidenceSelection(BaseModel):
    """filter_review_evidence() 전용 wire 모델.

    `reason`은 사용자에게 보이지 않는다. 왜 그 문장을 골랐는지가 남아야 근거가
    이상할 때 프롬프트를 고칠 수 있어서 받는다.
    """

    keep: list[int]
    reason: str = ""


class _ComparisonSummary(BaseModel):
    """generate_compare_summary() 전용 wire 모델.

    줄 수를 구조화 출력 단계에서 제한해, 프롬프트 지시만으로는 보장되지 않는
    3~6줄 요구사항을 실제 응답 계약으로 강제한다.
    """

    lines: list[str] = Field(min_length=3, max_length=6)


class _NthWeekday(BaseModel):
    weekday: str = ""
    ordinals: list[int] = Field(default_factory=list)


class _ClosureExtraction(BaseModel):
    """extract_closure_rules() 전용 wire 모델.

    어휘 검증을 여기서 하지 않는다 — 호출부가 어느 원문의 답인지 아는 자리에서
    한다. 여기서 ValidationError를 내면 배치 한 건이 통째로 실패하고, 무엇이
    이상했는지는 사라진다.
    """

    weekly: list[str] = Field(default_factory=list)
    nth_weekday: list[_NthWeekday] = Field(default_factory=list)
    uncertain_weekdays: list[str] = Field(default_factory=list)
    note: str = ""


class _TravelModePlan(BaseModel):
    """judge_travel_modes() 전용 wire 모델.

    개수를 스키마로 묶지 않는다 — 구간 수가 요청마다 다르고, 개수 검증은 호출부
    (`tools/schedule_travel.py::select_modes_for_segments()`)가 어느 구간의 답인지
    아는 자리에서 한다. 여기서 ValidationError를 내면 "몇 개가 왔는지"만 남고
    "어느 구간이 빠졌는지"가 사라진다.
    """

    modes: list[str] = Field(default_factory=list)


class _FollowUpSuggestions(BaseModel):
    """generate_follow_up_suggestions() 전용 wire 모델.

    `_ComparisonSummary`와 달리 개수를 스키마로 묶지 않는다 — 빈 목록도 정상 결과이고
    (제안할 게 없으면 버튼을 안 띄운다), 상한을 넘겨 받으면 호출부가 잘라 쓰는 편이
    ValidationError로 턴 전체를 흔드는 것보다 안전하다.
    """

    suggestions: list[str] = Field(default_factory=list)


# 타임아웃 예외는 **전송 계층마다 다르고, 그 계층을 우리가 고르지 않는다.**
#
# google-genai는 `aiohttp`를 임포트할 수 있으면 그쪽으로 요청을 보내고(`_use_aiohttp()`),
# 아니면 httpx로 보낸다. 그런데 `aiohttp`는 이 프로젝트의 의존성이 아니다 —
# `pyproject.toml`에 없고, 환경에 따라 다른 패키지(kubernetes, langchain-community 등)가
# 딸려 들여올 뿐이다. 즉 **어느 쪽으로 나가는지가 그 머신에 뭐가 깔려 있느냐로 갈린다.**
#
# 두 라이브러리는 상속 관계가 없어서 한쪽만 잡으면 다른 쪽은 그대로 새어 나간다.
# 2026-08-27에 `httpx.TimeoutException`만 잡고 있다가 실제로 그렇게 됐다 — aiohttp가
# 있는 개발 머신에서 INFO 답변 스트림이 타임아웃하자 모델 폴백도, `AppError` 변환도
# 못 하고 턴 전체가 죽었다(C가 이미 가져온 장소 정보까지 함께 버려졌다). aiohttp가 없는
# 환경에서는 같은 코드가 멀쩡히 돌아서, 테스트로도 CI로도 드러나지 않았다.
#
# `asyncio.TimeoutError`는 Python 3.11+에서 builtin `TimeoutError`와 같은 객체다.
_TIMEOUT_ERRORS = (httpx.TimeoutException, TimeoutError)

# 429(rate limit)와 5xx(서버 과부하/일시 장애)만 재시도 대상. 4xx(인증 실패, 잘못된 요청 등)는
# 재시도해도 같은 결과이므로 즉시 실패시킨다.
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

_BACKOFF_BASE_SECONDS = 0.5

# 폴백 모델 보정 — gemini-2.5-flash-lite는 thinking이 기본 꺼져 있어 0을 걸어도 동작이
# 같고(미설정과 budget=0의 68건 예측이 한 건도 다르지 않다), 512를 줘야 대화 이력에
# 의존하는 판정(MODIFY/COMPARE/되묻기)이 산다 — 채점 대상 64건에서 56→59, 대조쌍
# 12건에서 9→12. 즉 예산의 최적값은 모델마다 반대다.
#
# 측정한 범위가 classify_intent뿐이라 그 호출에만 건다. 조건 추출·일정 편성은 폴백
# 모델로 재본 적이 없어 호출부가 정한 값을 그대로 둔다.
# 근거: backend/test_results/intent_experiments_2026-08.md §4
_MODEL_BUDGET_OVERRIDES: dict[tuple[str, str], int] = {
    ("gemini-2.5-flash-lite", "classify_intent"): 512,
}

# `thinking_budget`에 **숫자 0**을 실으면 400 INVALID_ARGUMENT를 돌려주는 모델.
# 400은 비재시도 오류라 폴백도 못 타고 즉시 실패한다(_try_model 참고) — 0이 숫자로
# 실리는 순간 그 호출은 죽는다. 세대별이 아니라 모델별이다.
# 근거: backend/test_results/intent_experiments_2026-08.md §5 (2026-08-14, eae832f)
#
# 실 API 재확인(2026-08-24) — 거부되는 것은 "0"뿐이다:
#   thinking_budget=0        → 400 INVALID_ARGUMENT
#   thinking_budget=512      → 성공   (숫자 자체가 문제인 것이 아니다)
#   thinking_level=MINIMAL   → 성공   (지금 우리가 실제로 보내는 값)
#
# **그래서 이 목록은 더 이상 "thinking을 끄지 않는" 근거가 아니다.** 예전에는 이
# 목록에 걸리면 thinking_config를 아예 싣지 않았는데, 그러면 400은 피하지만 "thinking
# 끄기"도 함께 사라진다. fast 모델이 gemini-3.5-flash-lite가 된 뒤(2026-08-18)
# `classify_intent`·`extract_recommend_conditions`에서 실제로 그 일이 일어났고,
# 코드가 바뀐 게 아니라 모델만 바뀐 것이라 아무도 알아채지 못했다.
#
# 지금은 `_thinking_config_for()`가 0을 숫자가 아니라 `thinking_level=MINIMAL`로
# 바꿔 보내므로 400이 날 입력을 애초에 만들지 않는다. 목록은 **실측으로 얻은 사실**
# 이라 지우지 않고, 그 사실을 지키는 불변식
# (`test_zero_budget_is_never_sent_as_a_number`)이 이 상수를 직접 읽어 검증한다 —
# 모델이 늘어나면 목록에만 추가하면 테스트가 따라온다.
#
# 참고로 이 변경의 목적은 속도가 아니다. gemini-3.5-flash-lite는 thinking 기본값이
# 이미 가벼워서 MINIMAL을 걸어도 지연이 같다(classify_intent 15회 중앙값
# 958ms → 949ms, -0.9%). 목적은 "기본 thinking이 무거운 모델로 바꾸는 순간 최적화가
# 조용히 사라지는" 구조를 없애는 것이다. 상세는 D-076와
# scripts/measure_fast_thinking_level.py.
_REJECTS_ZERO_THINKING_BUDGET = frozenset(
    {
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    }
)

# gemini-3.1-flash-lite는 **이 목록에 넣지 않는다** — 숫자 0을 거부하지 않는다.
# 2026-09-09 실 API로 셋을 나란히 재봤다(모델 하나, 같은 프롬프트):
#
#   thinking_budget=0 (숫자)   → 성공, thoughts_token_count=None
#   thinking_level=MINIMAL     → 성공, thoughts_token_count=None
#   미설정(모델 기본값)         → 성공, thoughts_token_count=None
#
# **셋 다 되고 셋 다 사고 토큰이 0이다.** 그래서 "무엇을 보내야 하는가"는 이 모델에서
# 선택의 문제가 아니고, `_thinking_config_for()`가 0을 MINIMAL로 바꾸는 지금 경로가
# 그대로 안전하다.
#
# **None이 "추론 0"을 뜻한다는 것은 대조군으로 확인했다.** 같은 모델에
# thinking_level=HIGH를 걸면 thoughts_token_count=766이 찍힌다(계산 문제 1건).
# 즉 그 필드는 채워지는 필드이고, MINIMAL에서 비는 것은 실제로 사고 토큰이 없다는
# 뜻이다 — "필드를 안 준다"가 아니다.
#
# **기본값에 기대지 않고 명시적으로 끈다.** 미설정도 지금은 사고 토큰이 0이지만,
# gemini-2.5-flash → 3.5-flash에서 기본값이 MEDIUM으로 바뀌며 답변 5곳의 최적화가
# 조용히 사라진 이력이 있다(`_thinking_config_for()` docstring). 모델 기본값은
# 계약이 아니다.
#
# SDK에 OFF 단계는 없다 — ThinkingLevel은 UNSPECIFIED/MINIMAL/LOW/MEDIUM/HIGH이고
# MINIMAL이 최하단이다(google-genai 2.14.0).


def _resolve_thinking_budget(model_name: str, operation: str, requested: int | None) -> int | None:
    """호출부가 요청한 thinking 예산을 모델 특성에 맞춰 보정한다.

    None을 돌려주면 thinking_config를 아예 싣지 않아 모델 기본 동작이 된다.
    실측으로 확인한 조합만 보정하고, 모르는 모델에는 요청값을 그대로 통과시킨다.
    """
    override = _MODEL_BUDGET_OVERRIDES.get((model_name, operation))
    if override is not None:
        return override
    return requested


def _thinking_config_for(thinking_budget: int | None) -> genai_types.ThinkingConfig | None:
    """thinking_budget 인자를 실제 SDK에 넘길 ThinkingConfig로 변환한다.

    (2026-08-18 추가) Gemini 3.x부터 숫자 기반 thinking_budget은 레거시
    취급이고, Google은 thinking_level(MINIMAL/LOW/MEDIUM/HIGH) 사용을
    권장한다 — 같은 요청에 thinking_budget과 thinking_level을 함께 넘기면
    400 오류가 나므로 항상 하나만 채운다. 이 코드베이스는 지금까지
    thinking_budget에 0 또는 None만 넘겨왔다(그 외 값은 쓰인 적 없음) —
    0("완전히 끔")은 가장 가까운 대응인 MINIMAL로 변환하고, None은 지금과
    동일하게 아무것도 안 넣어 모델 자체 기본값을 그대로 둔다.

    모델을 gemini-2.5-flash → gemini-3.5-flash로 바꾼 뒤 응답이 전반적으로
    느려진 문제의 일부가 이 부분이다 — 예전엔 thinking_budget=0이 SCHEDULE
    2곳과 classify_intent/extract_recommend_conditions에서 thinking을
    확실히 껐지만, 레거시 파라미터가 새 모델에서도 여전히 그대로 작동한다는
    보장이 없어 이 네 곳부터 명시적으로 고쳤다.

    (2026-08-20 해소) 문장 생성·요약류(답변·요약 5곳 — generate_general_answer/
    stream_general_answer/generate_recommendation_summary/
    stream_recommendation_summary/stream_info_answer/generate_compare_summary)는
    gemini-2.5-flash 기준 "모델 기본값이 가볍다"는 가정으로 의도적으로 손대지
    않았던 곳들인데, gemini-3.5-flash의 기본값은 MEDIUM(항상 켜짐)이라 그 가정이
    깨져 GENERAL 인사말 응답에도 6~7초 TTFT가 걸리는 게 실사용에서 확인됐다.
    scripts/compare_answer_thinking_budget.py로 5개 케이스 × 3회 실측한 결과
    thinking_budget=0이 평균 3.9배 빠르면서(예: 5.9초→1.3초) 답변 문구는 페르소나·
    자기소개("트리비")·문장 수 규칙을 그대로 지켰다(수동 확인, "문장 생성·요약류는
    품질 저하 리스크"라는 우려가 이 케이스들에서는 근거로 뒷받침되지 않음). 결과:
    test_results/answer_thinking_budget_latency.csv. 이 5곳도 이제 thinking_budget=0을
    쓴다 — 남은 호출부는 없다.
    """
    if thinking_budget is None:
        return None
    if thinking_budget <= 0:
        # 여기가 400을 막는 지점이다. 숫자 0을 그대로 실으면
        # _REJECTS_ZERO_THINKING_BUDGET의 모델들이 400으로 즉시 죽는다 — 0은 항상
        # thinking_level로 바꿔 내보내고, 숫자로는 절대 흘리지 않는다.
        return genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.MINIMAL)
    # 0/None 외의 값은 지금까지 쓰인 적이 없다 — 필요해지면 그때 thinking_level
    # 값으로 다시 매핑 기준을 정한다. 양수는 위 목록의 모델에서도 정상 동작한다
    # (2026-08-24 실측: thinking_budget=512는 두 모델 모두 성공).
    return genai_types.ThinkingConfig(thinking_budget=thinking_budget)


def _backoff_seconds(attempt: int) -> float:
    """지수 백오프 + 지터. attempt=0이 첫 번째 재시도 전 대기시간."""
    return _BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.25)


def _build_contents(
    user_input: str, history: Sequence[ConversationTurnView] | None
) -> str | list[genai_types.Content]:
    """이번 발화와 최근 대화를 `contents`로 만든다. (대화층 1단계)

    이력이 없으면 지금까지와 똑같이 문자열 하나를 그대로 넘긴다 — 기존 호출
    전부의 동작을 바꾸지 않기 위해서다.

    이력이 있으면 **system_instruction에 치환하지 않고** user/model 역할을 나눈
    Content 목록으로 만든다. 이 구분이 핵심이다: 사용자가 쓴 글을 시스템 지시문
    문자열 안에 끼워 넣으면 "이전 지시는 무시하고 ~해라" 같은 문장이 지시문처럼
    읽힐 수 있다. 서버 DB에 저장했다는 사실은 그 입력을 안전하게 만들지 않는다.

    assistant_summary가 없는 턴(응답을 만들다 실패한 턴 등)은 model 쪽을 비운
    채 사용자 발화만 싣는다 — 빈 model 파트를 넣으면 API가 거부한다.
    """
    if not history:
        return user_input

    contents: list[genai_types.Content] = []
    for turn in history:
        contents.append(
            genai_types.Content(role="user", parts=[genai_types.Part(text=turn.user_input)])
        )
        if turn.assistant_summary:
            contents.append(
                genai_types.Content(
                    role="model", parts=[genai_types.Part(text=turn.assistant_summary)]
                )
            )
    contents.append(
        genai_types.Content(role="user", parts=[genai_types.Part(text=user_input)])
    )
    return contents


def _record_gemini_call(model_name: str, started: float, *, ok: bool, status: str) -> None:
    """Gemini 호출 한 번을 관측 집계에 남긴다(추천 판정과 무관)."""
    record_call(
        "gemini",
        model_name,
        ok=ok,
        latency_ms=(time.perf_counter() - started) * 1000,
        status=status,
    )


def _now_utc() -> datetime:
    """`completion_start_time`에 넣을 현재 시각. Langfuse는 tz-aware를 기대한다."""
    return datetime.now(UTC)


def _token_usage(usage: object | None) -> dict[str, int]:
    """google-genai `usage_metadata`를 우리 필드 이름으로 옮긴다.

    빠진 값은 키 자체를 넣지 않는다 — 0으로 채우면 "안 썼다"와 "모른다"가
    구분되지 않고, 토큰이 안 잡히는 회귀가 조용히 묻힌다.
    """
    if usage is None:
        return {}
    fields = {
        "input_tokens": "prompt_token_count",
        "output_tokens": "candidates_token_count",
        "thoughts_tokens": "thoughts_token_count",
        # 캐시에서 읽힌 입력 몫. prompt_token_count에 이미 포함돼 오므로
        # 더하지 말고 "그중 얼마"로 읽는다.
        "cached_tokens": "cached_content_token_count",
        "total_tokens": "total_token_count",
    }
    collected: dict[str, int] = {}
    for name, source in fields.items():
        value = getattr(usage, source, None)
        if isinstance(value, int):
            collected[name] = value
    return collected


def _usage_details(usage: dict[str, int]) -> dict[str, int] | None:
    """우리 필드를 Langfuse `usage_details`로 옮긴다.

    **사고 토큰을 output에 더한다.** Gemini 3.x의 thoughts는 candidates_token_count에
    안 잡히는데 과금은 출력 요율로 된다 — 빼고 보내면 비용이 과소 집계된다.
    원래 값은 `thoughts`로 따로 남겨 어느 쪽이 얼마인지 볼 수 있게 한다.
    """
    if not usage:
        return None
    details: dict[str, int] = {}
    if "input_tokens" in usage:
        details["input"] = usage["input_tokens"]
    output = usage.get("output_tokens")
    if output is not None:
        details["output"] = output + usage.get("thoughts_tokens", 0)
    if "thoughts_tokens" in usage:
        details["thoughts"] = usage["thoughts_tokens"]
    # **input에서 빼지 않는다.** 캐시분은 prompt_token_count에 이미 들어 있고,
    # 여기서 빼면 Langfuse의 입력 토큰 합계가 실제 전송량과 어긋난다. 별도 키로
    # 얹어 "그중 얼마가 캐시였나"만 보이게 한다 — 단가 반영은 Langfuse 비용
    # 설정의 몫이라 이 함수가 정하지 않는다.
    if "cached_tokens" in usage:
        details["cached"] = usage["cached_tokens"]
    if "total_tokens" in usage:
        details["total"] = usage["total_tokens"]
    return details or None


def _linked_prompt(operation: str) -> object | None:
    """이 호출을 묶을 Langfuse 프롬프트 객체. 프롬프트 관리가 꺼져 있으면 `None`.

    **`version=` 문자열과 둘 다 싣는다.** 문자열은 프롬프트 관리를 안 써도 남고
    (`operation_prompt_version()`), 링크는 켰을 때 Langfuse가 버전별 지연·비용·Score를
    자동으로 묶어 준다. 하나로 다른 하나를 대신할 수 없다.

    조회 실패는 `None`이다 — 링크가 없어도 답변은 나가야 한다.
    """

    template = operation_entry_template(operation)
    if template is None:
        return None
    return langfuse_prompts.prompt_object(template)


class _RetryableExhaustedError(Exception):
    """한 모델의 재시도 예산이 소진됐다는 내부 신호(폴백 대상) — 사용자에게 그대로
    노출되지 않는다. _generate()가 이 예외를 잡아 다음 모델로 넘어가거나, 마지막
    모델이면 감싸고 있는 원본 오류를 그대로 raise한다. 4xx 같은 비재시도 오류는 이
    래퍼 없이 ProviderUnavailableError를 바로 던져 폴백 없이 즉시 실패한다."""

    def __init__(self, original: ProviderTimeoutError | ProviderUnavailableError) -> None:
        self.original = original


class RealGeminiProvider:
    """google-genai SDK로 Gemini 구조화 출력을 호출하는 실제 구현.

    다른 Real provider와 달리 공유 httpx.AsyncClient를 받지 않는다 — google-genai가
    자체 비동기 클라이언트를 관리하기 때문(SDK 사용 요구사항에 따른 의도적인 구조 차이).
    """

    def __init__(
        self,
        api_key: str,
        model_names: list[str] | None = None,
        *,
        fast_model_names: list[str] | None = None,
        generation_model_names: list[str] | None = None,
        place_reason_model_names: list[str] | None = None,
        mode_judge_model_names: list[str] | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        """역할별 모델 묶음을 설정한다.

        ``model_names``는 D-052 이전 단일 모델 생성자 호출과 테스트를 위한 호환
        인자다. 실제 앱 팩토리는 ``fast_model_names``와
        ``generation_model_names``를 각각 넘긴다.
        """
        legacy_models = model_names or []
        self._fast_model_names = fast_model_names or legacy_models
        self._generation_model_names = generation_model_names or legacy_models
        # 상세 카드 추천 이유 전용 티어. 안 넘기면 생성 묶음을 그대로 쓴다 —
        # 이 티어를 모르는 기존 생성자 호출(테스트 다수)이 그대로 돌아야 한다.
        self._place_reason_model_names = place_reason_model_names or self._generation_model_names
        # 구간 이동수단 판정 전용 묶음. 안 넘기면 생성 묶음을 그대로 쓴다 — 추천 이유 티어와
        # 같은 이유다(config.py `mode_judge_model_name`).
        self._mode_judge_model_names = mode_judge_model_names or self._generation_model_names
        if not self._fast_model_names or not self._generation_model_names:
            raise ValueError("빠른 판단·응답 생성 모델은 각각 최소 1개 이상이어야 합니다.")
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )
        self._max_retries = max_retries

    async def classify_intent(
        self,
        user_input: str,
        *,
        has_previous_recommendation: bool,
        shown_place_count: int,
        pending_clarification: str | None = None,
        last_intent: str | None = None,
        shown_place_names: list[str] | None = None,
        conversation_place_name: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[IntentClassificationResult]:
        instruction = gemini_prompts.build_intent_classification_instruction(
            has_previous_recommendation=has_previous_recommendation,
            shown_place_count=shown_place_count,
            pending_clarification=pending_clarification,
            last_intent=last_intent,
            shown_place_names=shown_place_names,
            conversation_place_name=conversation_place_name,
        )
        # thinking_budget=0 — SCHEDULE(generate_schedule_plan/fill)에 적용한 것과 같은
        # 이유. classify_intent는 정해진 스키마 중 하나를 고르는 얕은 판단이라 thinking
        # 없이도 규칙 기반 판별이 가능하다고 보고 실측(2026-08-13, 10개 대표 질문×2회,
        # scripts/compare_classify_extract_thinking_budget.py)으로 확인했다 — 평균
        # 3609ms→1561ms(2.3배), 정확도는 90%(18/20)로 thinking_on과 동일하게 유지됨
        # (유일한 오답 케이스도 thinking_on/off 양쪽에서 똑같이 틀려 이 변경과 무관한
        # 기존 프롬프트 이슈로 확인). 결과: test_results/classify_extract_thinking_budget.csv.
        result = await self._call_structured(
            instruction,
            user_input,
            IntentClassificationResult,
            operation="classify_intent",
            thinking_budget=0,
            model_names=self._fast_model_names,
            history=history,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def extract_recommend_conditions(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
        retry_models: list[str] | None = None,
    ) -> ProviderResult[LLMOutput]:
        instruction = gemini_prompts.build_recommend_extraction_instruction()
        # thinking_budget=0 — classify_intent()와 같은 이유로 실측 확인
        # (평균 3122ms→1745ms, 1.8배, search_center 추출 정확도 4/4로 동일 유지).
        #
        # TP-266: 재시도 호출은 operation 이름을 달리 남긴다. 요청 단위 호출
        # 이력(llm_execution)에서 "이 턴이 다시 뽑았는가"를 세는 자리가 이것뿐이다
        # — 같은 이름으로 남기면 _call_structured()의 스키마 보정 재시도와
        # 구분되지 않는다.
        #
        # **두 갈래를 펼쳐 쓰는 이유가 있다.** operation을 조건식으로 접으면
        # tests/test_prompt_operation_slots.py의 소스 스캐너(`operation="..."`
        # 리터럴을 훑는다)가 두 이름을 다 놓친다. 그러면 새 operation이 프롬프트
        # 슬롯 없이 기록돼도 아무도 모른다 — 그 테스트가 막으려는 것이 이것이다.
        if retry_models:
            result = await self._call_structured(
                instruction,
                user_input,
                LLMOutput,
                operation="extract_recommend_conditions_retry",
                thinking_budget=0,
                model_names=retry_models,
                history=history,
            )
        else:
            result = await self._call_structured(
                instruction,
                user_input,
                LLMOutput,
                operation="extract_recommend_conditions",
                thinking_budget=0,
                model_names=self._fast_model_names,
                history=history,
            )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def extract_modify_conditions(
        self,
        user_input: str,
        current_conditions: UserConditions,
        *,
        pending_clarification: str | None = None,
        shown_place_count: int = 0,
        shown_place_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        instruction = gemini_prompts.build_modify_extraction_instruction(
            current_conditions,
            pending_clarification=pending_clarification,
            shown_place_count=shown_place_count,
            shown_place_names=shown_place_names,
        )
        # thinking_budget=0 — classify_intent()와 같은 이유(TP-179). 실측(2026-08-27)
        # 으로는 fast 모델(gemini-3.5-flash-lite)에서 지연 차이가 없었다 — 그 모델은
        # 설정 없이도 이미 가볍다. 그래도 명시해 두는 이유는 fast 모델이 다시 무거운
        # 모델로 바뀌는 순간 이 네 곳만 조용히 최적화가 빠지는 것을 막기 위해서다
        # (D-076이 겪은 것과 같은 함정).
        result = await self._call_structured(
            instruction,
            user_input,
            LLMOutput,
            operation="extract_modify_conditions",
            thinking_budget=0,
            model_names=self._fast_model_names,
            history=history,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def extract_info_query(
        self,
        user_input: str,
        *,
        has_previous_recommendation: bool,
        reference_date: date,
        conversation_place_name: str | None = None,
        pending_info_question_type: str | None = None,
        pending_info_specific_question: str | None = None,
        pending_info_visit_time: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        instruction = gemini_prompts.build_info_extraction_instruction(
            has_previous_recommendation=has_previous_recommendation,
            reference_date=reference_date,
            conversation_place_name=conversation_place_name,
            pending_info_question_type=pending_info_question_type,
            pending_info_specific_question=pending_info_specific_question,
            pending_info_visit_time=pending_info_visit_time,
        )
        # thinking_budget=0 — extract_modify_conditions()와 같은 이유(TP-179).
        result = await self._call_structured(
            instruction,
            user_input,
            LLMOutput,
            operation="extract_info_query",
            thinking_budget=0,
            model_names=self._fast_model_names,
            history=history,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def answer_with_tools(
        self,
        instruction: str,
        *,
        tools: Sequence[Callable[..., Awaitable[str]]],
        max_tool_calls: int = 3,
    ) -> ProviderResult[str]:
        """자동 함수 호출(automatic function calling) — 이 provider 최초의 tool-calling
        호출이라 구조화 출력 전용인 `_generate()`/`_call_structured()`를 거치지 않고
        SDK를 직접 부른다(response_schema와 tools는 같은 호출에서 함께 못 쓴다).

        단순화한 점(첫 슬라이스라 의도적으로 뺀 것들, 로드맵 24번 후속 과제):
        모델 폴백 없이 fast 모델 1개만 쓰고, 타임아웃/API 오류를 백오프 없이
        1회로 바로 실패 처리한다. 다만 Langfuse generation 기록과 개발자 감사
        패널(`record_llm_call`)은 다른 메서드와 같은 방식으로 남긴다.

        **확인 필요**: automatic function calling은 내부적으로 여러 번 API를
        왕복할 수 있는데, `response.usage_metadata`가 그 왕복들을 전부 합산한
        값인지 마지막 왕복 한 번만의 값인지 SDK 문서로 확인하지 못했다(실측 몇
        건은 재시도 유무와 무관하게 비슷한 토큰 수를 보여 마지막 왕복만 잡을
        가능성이 있다). 재시도가 잦은 실제 사용량을 정확히 보려면 이 부분을
        먼저 확인해야 한다.
        """

        model_name = self._fast_model_names[0]
        operation = "answer_with_tools"
        started = time.perf_counter()
        with observe_generation(operation, model=model_name, input=instruction) as generation:
            try:
                response = await self._client.aio.models.generate_content(
                    model=model_name,
                    contents=instruction,
                    config=genai_types.GenerateContentConfig(
                        tools=list(tools),
                        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                            maximum_remote_calls=max_tool_calls
                        ),
                        temperature=0.0,
                    ),
                )
            except _TIMEOUT_ERRORS:
                _record_gemini_call(model_name, started, ok=False, status="timeout")
                record_llm_call(
                    operation=operation,
                    attempted_models=[model_name],
                    served_model=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
                raise ProviderTimeoutError("Gemini") from None
            except genai_errors.APIError as exc:
                status = f" {exc.status}" if hasattr(exc, "status") else ""
                _record_gemini_call(model_name, started, ok=False, status=str(exc.code))
                record_llm_call(
                    operation=operation,
                    attempted_models=[model_name],
                    served_model=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
                raise ProviderUnavailableError("Gemini", detail=f"{exc.code}{status}") from None
            _record_gemini_call(model_name, started, ok=True, status="ok")
            usage = _token_usage(getattr(response, "usage_metadata", None))
            generation.record(output=response.text, usage_details=_usage_details(usage))
            record_llm_call(
                operation=operation,
                attempted_models=[model_name],
                served_model=model_name,
                latency_ms=round((time.perf_counter() - started) * 1000),
                **usage,
            )
        return provider_result(response.text or "", source=ProviderSource.GEMINI)

    async def extract_compare_request(
        self,
        user_input: str,
        *,
        shown_place_count: int,
        shown_place_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        instruction = gemini_prompts.build_compare_extraction_instruction(
            shown_place_count=shown_place_count,
            shown_place_names=shown_place_names,
        )
        # thinking_budget=0 — extract_modify_conditions()와 같은 이유(TP-179).
        result = await self._call_structured(
            instruction,
            user_input,
            LLMOutput,
            operation="extract_compare_request",
            thinking_budget=0,
            model_names=self._fast_model_names,
            history=history,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def extract_general_request(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        instruction = gemini_prompts.build_general_extraction_instruction()
        # thinking_budget=0 — extract_modify_conditions()와 같은 이유(TP-179).
        result = await self._call_structured(
            instruction,
            user_input,
            LLMOutput,
            operation="extract_general_request",
            thinking_budget=0,
            model_names=self._fast_model_names,
            history=history,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def generate_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        instruction = gemini_prompts.build_general_answer_instruction(
            topic, offer_content=offer_content
        )
        result = await self._call_structured(
            instruction,
            original_question,
            _GeneralAnswer,
            operation="generate_general_answer",
            model_names=self._generation_model_names,
            # thinking_budget=0 — 답변·요약 계열 5곳에 공통 적용(2026-08-20 실측).
            # gemini-2.5-flash → gemini-3.5-flash 전환 후 기본 thinking이 MEDIUM(항상
            # 켜짐)으로 바뀌어 간단한 인사말에도 6~7초가 걸렸다(실사용 확인). 이 5곳만
            # thinking_budget을 안 넣은 채 남아 있었다(_thinking_config_for() docstring
            # 참고 — 그때는 "품질 저하 리스크"로 의도적으로 제외했던 곳들).
            # scripts/compare_answer_thinking_budget.py로 5개 케이스 × 3회 실측한 결과
            # 평균 3.9배 빨라졌고(예: general_identity 5.9초→1.3초), 답변 문구는
            # 페르소나·자기소개("트리비")·문장 수 규칙을 그대로 지켰다(수동 확인,
            # 결과: test_results/answer_thinking_budget_latency.csv).
            thinking_budget=0,
            history=history,
        )
        return provider_result(result.answer, source=ProviderSource.GEMINI)

    async def generate_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        instruction = gemini_prompts.build_recommendation_summary_instruction(
            intent, conditions=conditions
        )
        payload = {
            "recommendations": [
                self._recommendation_summary_item(item)
                for item in [
                    *recommendations.recommendations,
                    *recommendations.unverified_recommendations,
                ]
            ]
        }
        result = await self._call_structured(
            instruction,
            json.dumps(payload, ensure_ascii=False),
            _RecommendationSummary,
            operation="generate_recommendation_summary",
            model_names=self._generation_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유로 실측 확인.
            thinking_budget=0,
            history=history,
        )
        return provider_result(result.message, source=ProviderSource.GEMINI)

    # 상세 카드 문장에 넘길 태그 수와 태그별 근거 문장 수·길이 상한.
    #
    # 저장소는 한 장소에 태그 5개와 근거 최대 30건(각 500자)을 준다
    # (`find_preference_insights`). 그걸 그대로 넘기면 입력이 5,000토큰을 넘어가
    # 클릭당 호출로 아낀 것이 통째로 사라진다 — 상한이 이 기능의 비용 설계다.
    # 1~2문장을 쓰는 데 상위 3태그 × 1문장이면 충분하다는 것은 실측으로 확인했다
    # (2026-09-09: 태그 3개·근거 2문장으로 입력 494토큰).
    _REASON_MAX_TAGS = 3
    _REASON_MAX_EVIDENCE_PER_TAG = 1
    _REASON_MAX_EVIDENCE_CHARS = 220

    async def generate_place_reason(
        self,
        *,
        place_name: str,
        category_label: str | None,
        insights: Sequence[PlacePreferenceInsight],
        matched_preference_codes: Sequence[str] = (),
    ) -> ProviderResult[str]:
        instruction = gemini_prompts.build_place_reason_instruction()
        payload = self._place_reason_payload(
            place_name=place_name,
            category_label=category_label,
            insights=insights,
            matched_preference_codes=matched_preference_codes,
        )
        result = await self._call_structured(
            instruction,
            json.dumps(payload, ensure_ascii=False),
            _PlaceReason,
            operation="generate_place_reason",
            model_names=self._place_reason_model_names,
            # thinking_budget=0 — 답변·요약 계열과 같은 이유다. 이 호출은 사실 근거를
            # 전부 쥐어주고 말투만 맡기는 작업이라 추론이 만들 이득이 없고, 모달이
            # 열린 채 기다리는 시간에 그대로 붙는다. 3.1-flash-lite에서 0이 실제로
            # 사고 토큰 0을 만든다는 것은 실측했다(_REJECTS_ZERO_THINKING_BUDGET 주석).
            thinking_budget=0,
        )
        return provider_result(result.reason, source=ProviderSource.GEMINI)

    @classmethod
    def _place_reason_payload(
        cls,
        *,
        place_name: str,
        category_label: str | None,
        insights: Sequence[PlacePreferenceInsight],
        matched_preference_codes: Sequence[str] = (),
    ) -> dict[str, object]:
        """상세 카드 문장 생성에 넘겨도 되는 값만 상한 안에서 남긴다.

        **사용자 취향과 맞은 태그를 먼저 넣는다.** 저장소가 주는 순서는 그 장소에서
        후기에 많이 언급된 순서라, 사용자가 "조용한 데"를 말했어도 사진 이야기가
        앞설 수 있다. 태그를 상위 3개만 넘기므로 순서 문제가 아니라 **일치한 태그가
        아예 안 실리는** 문제가 된다. 그래서 `matched_preference_codes`(화면이
        들고 있던 `preference_tags[].is_query_match`)에 걸리는 태그를 앞으로 당기고,
        payload에서도 `matches_user_preference`로 표시해 프롬프트가 그 태그부터
        말하게 한다. 코드가 비면 예전과 똑같이 언급 수 순서다.

        **순위·점수·조건 축을 넣지 않는다.** 그 정보는 카드가 이미 들고 있는 고정
        문장이 말하고, 이 문장은 그 아래에 붙는다(recommend/HISTORY.md의
        place_reason v1.0.0). 초안 실측에서 순위·축을 넘겨도 모델이 문장에 쓰지
        않았는데, 쓰게 고치는 대신 같은 말을 두 번 하지 않는 쪽을 골랐다.

        `source_url`도 넣지 않는다 — 문장은 출처 링크를 말하지 않고, 링크는 화면이
        태그 근거로 따로 보여준다.

        **근거 문장은 태그를 가로질러 중복을 없앤다.** 후기 한 문장이 여러 태그에
        동시에 걸리는 것이 예외가 아니라 흔한 경우다 — 실측에서 선운각의 상위 3태그
        (문화·예술 / 전망 / 자연)가 **같은 문장 하나**를 각자의 근거로 들고 있었고,
        태그별로 모으면 그 문장이 3번 실려 입력의 2/3가 같은 글자였다(2026-09-09).
        중복은 비용만 늘리는 것이 아니라 그 문장을 세 번 강조된 근거처럼 보이게 한다.
        """

        matched = {code.strip() for code in matched_preference_codes if code.strip()}
        # 정렬은 안정 정렬이다 — 일치 여부만 앞으로 당기고, 같은 그룹 안에서는
        # 저장소가 준 언급 수 순서를 그대로 둔다.
        ordered = sorted(insights, key=lambda insight: insight.code not in matched)

        seen: set[str] = set()
        tags: list[dict[str, object]] = []
        evidence: list[str] = []
        negative: list[str] = []

        def _take(text: str, into: list[str]) -> None:
            """중복이 아니면 상한 안에서 담는다. 판정은 자르기 **전** 원문으로 한다."""
            normalized = " ".join(text.split())
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            into.append(normalized[: cls._REASON_MAX_EVIDENCE_CHARS])

        for insight in ordered[: cls._REASON_MAX_TAGS]:
            tag: dict[str, object] = {
                "label": insight.label,
                "mention_count": insight.mention_count,
            }
            if insight.code in matched:
                tag["matches_user_preference"] = True
            tags.append(tag)
            taken_here = len(evidence)
            for quote in insight.evidence:
                # 부정 근거는 태그 안에 섞지 않고 따로 모은다. 섞으면 모델이 그 문장을
                # 그 태그의 장점 근거로 읽어 "사진 찍기 좋은" 밑에 불만을 칭찬처럼
                # 옮길 수 있다.
                if quote.polarity == "negative":
                    _take(quote.text, negative)
                    continue
                if len(evidence) - taken_here >= cls._REASON_MAX_EVIDENCE_PER_TAG:
                    continue
                _take(quote.text, evidence)

        payload: dict[str, object] = {
            "name": place_name,
            "category_label": category_label,
            "preference_tags": tags,
            "tag_evidence": evidence,
        }
        if negative:
            payload["negative_evidence"] = negative[: cls._REASON_MAX_TAGS]
        return payload

    async def generate_follow_up_suggestions(
        self,
        *,
        user_input: str,
        intent: Intent,
        assistant_message: str,
        place_names: list[str],
        search_place: str | None,
        transport: str | None,
        already_suggested: list[str],
        max_suggestions: int,
        max_label_length: int,
    ) -> ProviderResult[list[str]]:
        instruction = gemini_prompts.build_follow_up_suggestion_instruction(
            max_suggestions=max_suggestions,
            max_label_length=max_label_length,
        )
        payload = {
            "intent": intent.value,
            "user_input": user_input,
            "assistant_message": assistant_message,
            "places_shown": place_names,
            "search_place": search_place,
            "transport": transport,
            "already_suggested": already_suggested,
        }
        result = await self._call_structured(
            instruction,
            json.dumps(payload, ensure_ascii=False),
            _FollowUpSuggestions,
            operation="generate_follow_up_suggestions",
            # 답변이 아니라 짧은 문구 목록이라 fast 모델을 쓴다. 이 호출은 사용자가
            # 답변을 이미 다 읽은 뒤에 붙는 것이라 지연이 곧 버튼이 늦게 뜨는 시간이다.
            model_names=self._fast_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유.
            thinking_budget=0,
        )
        return provider_result(result.suggestions, source=ProviderSource.GEMINI)

    async def stream_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        instruction = gemini_prompts.build_recommendation_summary_instruction(
            intent, conditions=conditions
        )
        payload = {
            "recommendations": [
                self._recommendation_summary_item(item)
                for item in [
                    *recommendations.recommendations,
                    *recommendations.unverified_recommendations,
                ]
            ]
        }
        async for text in self._stream_text(
            instruction=instruction,
            user_input=json.dumps(payload, ensure_ascii=False),
            operation="stream_recommendation_summary",
            model_names=self._generation_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유로 실측 확인.
            thinking_budget=0,
            history=history,
        ):
            yield text

    async def stream_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """GENERAL 자유 답변을 Gemini 스트림으로 전달한다."""

        async for text in self._stream_text(
            instruction=gemini_prompts.build_general_answer_instruction(
                topic, offer_content=offer_content
            ),
            user_input=original_question,
            operation="stream_general_answer",
            model_names=self._generation_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유로 실측 확인.
            thinking_budget=0,
            history=history,
        ):
            yield text

    async def stream_info_answer(
        self,
        *,
        place_name: str,
        question_type: str,
        specific_question: str | None,
        fields: dict[str, str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """C가 검증한 장소 INFO 필드만 근거로 답변을 스트리밍한다."""

        payload = {
            "place_name": place_name,
            "specific_question": specific_question,
            "fields": fields,
        }
        async for text in self._stream_text(
            instruction=gemini_prompts.build_info_answer_instruction(question_type),
            user_input=json.dumps(payload, ensure_ascii=False),
            operation="stream_info_answer",
            model_names=self._generation_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유로 실측 확인.
            thinking_budget=0,
            history=history,
        ):
            yield text

    async def filter_review_evidence(
        self,
        *,
        place_name: str,
        specific_question: str,
        snippets: Sequence[str],
    ) -> ProviderResult[tuple[int, ...]]:
        """후기 문장 중 그 장소 이야기이면서 질문에 답이 되는 것의 번호만 고른다.

        **검색 유사도로는 이 판정을 대신할 수 없다.** 실측(2026-09-14, 질문 24개)에서
        답할 수 있는 질문과 없는 질문의 유사도 분포가 0.45~0.70 구간에서 겹쳤다.
        예를 들어 "북촌한옥마을 뭐가 맛있대?"는 답이 될 근거가 없는데도 근처 만둣국집
        후기가 0.683으로 1위였다. 같은 문장을 이 판정에 넘기면 걸러진다.

        번호는 1부터 센다 — 모델에게 0-based를 시키면 사람이 읽는 프롬프트와 어긋난다.
        """
        payload = {
            "place_name": place_name,
            "question": specific_question,
            "evidence": "\n".join(
                f"{index}. {text}" for index, text in enumerate(snippets, start=1)
            ),
        }
        result = await self._call_structured(
            gemini_prompts.build_review_evidence_filter_instruction(),
            json.dumps(payload, ensure_ascii=False),
            _ReviewEvidenceSelection,
            operation="filter_review_evidence",
            model_names=self._fast_model_names,
            # 사실 판단만 하는 짧은 호출이라 추론이 만들 이득이 없다. 답변 생성 앞에
            # 붙는 단계라 여기서 늘어난 시간이 사용자 체감에 그대로 더해진다.
            thinking_budget=0,
        )
        kept = tuple(
            index for index in result.keep if 1 <= index <= len(snippets)
        )
        return provider_result(kept, source=ProviderSource.GEMINI)

    async def stream_review_answer(
        self,
        *,
        place_name: str,
        specific_question: str | None,
        evidence: Sequence[str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """선별된 후기 문장만 근거로 답변을 스트리밍한다.

        `source_url`은 넘기지 않는다 — 답변은 링크를 말하지 않고 화면이 출처를 따로
        보여준다(`generate_place_reason`과 같은 규칙).
        """
        payload = {
            "place_name": place_name,
            "specific_question": specific_question,
            "review_evidence": list(evidence),
        }
        async for text in self._stream_text(
            instruction=gemini_prompts.build_review_answer_instruction(),
            user_input=json.dumps(payload, ensure_ascii=False),
            operation="stream_review_answer",
            model_names=self._generation_model_names,
            thinking_budget=0,
            history=history,
        ):
            yield text

    async def _stream_text(
        self,
        *,
        instruction: str,
        user_input: str,
        operation: str,
        model_names: list[str] | None = None,
        thinking_budget: int | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """일반 텍스트 Gemini 스트림의 모델 폴백·관측을 공통 처리한다.

        첫 조각을 보낸 뒤 다른 모델로 옮기면 문장이 중복될 수 있다. 따라서 그 이후
        오류는 호출자에게 전파해 이미 전달된 텍스트를 보존한다.

        thinking_budget은 _call_structured()와 같은 규칙으로 모델별 보정을 거친다
        (_resolve_thinking_budget()/_thinking_config_for() 참고).

        **관측은 모델 시도 하나를 generation 하나로 남긴다** — `_try_model()`과 같은
        규칙이다. 스트리밍이라 두 가지가 더 붙는다.

        - `completion_start_time`: **첫 조각이 도착한 시각.** 스트리밍에서 사용자가
          체감하는 지연은 전체 소요가 아니라 이 값이다. 구조화 호출에는 없는 지표다.
        - 토큰은 마지막 청크의 `usage_metadata`에서 온다. 청크마다 실려 오되 누적값이라
          마지막 것을 쓴다. 없으면 키를 안 넣는다 — 0으로 채우면 "안 썼다"와 "모른다"가
          섞인다(`_token_usage` 참고).

        이 호출들이 **사용자가 실제로 읽는 문장**을 만든다. 여기가 빠져 있던 동안
        턴당 비용·토큰이 과소 집계됐다.
        """

        selected_models = model_names or self._generation_model_names
        attempted_models: list[str] = []
        last_error: ProviderTimeoutError | ProviderUnavailableError | None = None

        for model_name in selected_models:
            attempted_models.append(model_name)
            started = time.perf_counter()
            emitted = False
            usage: dict[str, int] = {}
            resolved_budget = _resolve_thinking_budget(model_name, operation, thinking_budget)
            with observe_generation(
                operation,
                model=model_name,
                version=operation_prompt_version(operation),
                input={"system_instruction": instruction, "user_input": user_input},
                prompt=_linked_prompt(operation),
            ) as generation:
                pieces: list[str] = []
                try:
                    stream = await self._client.aio.models.generate_content_stream(
                        model=model_name,
                        contents=_build_contents(user_input, history),
                        config=genai_types.GenerateContentConfig(
                            system_instruction=instruction,
                            temperature=0.0,
                            thinking_config=_thinking_config_for(resolved_budget),
                        ),
                    )
                    async for chunk in stream:
                        chunk_usage = _token_usage(getattr(chunk, "usage_metadata", None))
                        if chunk_usage:
                            # 청크마다 실려 오지만 누적값이다 — 마지막 것이 이 호출의 총량.
                            usage = chunk_usage
                        text = getattr(chunk, "text", None)
                        if not text:
                            continue
                        if not emitted:
                            # 사용자가 첫 글자를 본 시각. 스트리밍의 체감 지연이 이것이다.
                            generation.record(completion_start_time=_now_utc())
                        emitted = True
                        pieces.append(text)
                        yield text
                except _TIMEOUT_ERRORS:
                    _record_gemini_call(model_name, started, ok=False, status="timeout")
                    last_error = ProviderTimeoutError("Gemini")
                    generation.record(level="ERROR", status_message="timeout")
                except genai_errors.APIError as exc:
                    status_code = getattr(exc, "code", None)
                    _record_gemini_call(
                        model_name,
                        started,
                        ok=False,
                        status=str(status_code or "api_error"),
                    )
                    generation.record(level="ERROR", status_message=str(status_code or "api_error"))
                    if status_code not in _RETRYABLE_STATUS_CODES:
                        record_llm_call(
                            operation=operation,
                            attempted_models=attempted_models,
                            served_model=None,
                            retry_count=0,
                            **usage,
                        )
                        raise ProviderUnavailableError("Gemini", detail=str(exc)) from None
                    last_error = ProviderUnavailableError("Gemini", detail=str(exc))
                else:
                    _record_gemini_call(model_name, started, ok=True, status="success")
                    generation.record(output="".join(pieces), usage_details=_usage_details(usage))
                    record_llm_call(
                        operation=operation,
                        attempted_models=attempted_models,
                        served_model=model_name,
                        # 스트리밍은 모델별 재시도 루프가 없다 — 실패하면 그대로
                        # 다음 모델로 넘어가므로 항상 0이다.
                        retry_count=0,
                        **usage,
                    )
                    return

                if emitted:
                    generation.record(output="".join(pieces), usage_details=_usage_details(usage))
                    record_llm_call(
                        operation=operation,
                        attempted_models=attempted_models,
                        served_model=model_name,
                        retry_count=0,
                        **usage,
                    )
                    raise last_error

            logger.warning(
                "Gemini 스트림 시작 실패, 다음 모델로 폴백: operation=%s model=%s",
                operation,
                model_name,
            )

        record_llm_call(
            operation=operation,
            attempted_models=attempted_models,
            served_model=None,
            retry_count=0,
        )
        assert last_error is not None
        raise last_error

    async def generate_compare_summary(
        self,
        comparison: ComparisonResult,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        """C가 반환한 공개 비교 사실만 Gemini에 전달해 설명 문장을 생성한다."""

        instruction = gemini_prompts.build_compare_summary_instruction(comparison.criteria)
        result = await self._call_structured(
            instruction,
            comparison.model_dump_json(exclude_none=True),
            _ComparisonSummary,
            operation="generate_compare_summary",
            model_names=self._generation_model_names,
            # thinking_budget=0 — generate_general_answer()와 같은 이유로 실측 확인.
            thinking_budget=0,
            history=history,
        )
        return provider_result("\n".join(result.lines), source=ProviderSource.GEMINI)

    @staticmethod
    def _recommendation_summary_item(item: RecommendationItem) -> dict[str, object]:
        """추천 요약 LLM에 넘겨도 되는 사용자-facing 필드만 남긴다.

        ``taste_evidence``는 D가 취향 검색으로 찾은 공개 리뷰 근거다. 내부 유사도나
        원문 전체를 넘기지 않고, 후보별 상위 두 문장만 제한해 추천 말풍선이 카드의
        점수 설명을 되풀이하지 않으면서도 사용자의 취향과 연결되게 한다.
        """

        summary_item: dict[str, object] = {
            "name": item.name,
            "category": item.category,
            "distance_km": item.distance_km,
            "remaining_minutes": item.remaining_minutes,
            "recommendation_reason": item.recommendation_reason,
            "explanations": item.explanations,
        }
        review_evidence = [
            quote.text.strip()[:280]
            for quote in item.taste_evidence[:2]
            if quote.text.strip()
        ]
        if review_evidence:
            summary_item["review_evidence"] = review_evidence
        return summary_item

    async def extract_closure_rules(
        self, rest_date: str
    ) -> ProviderResult[dict[str, object]]:
        """휴무 안내 원문에서 정기 휴무를 뽑는다. (TP-231)

        **적재 배치에서만 부른다.** 읽기 경로는 저장된 결과를 쓰므로
        (`resolve_operating_schedule()`) 이 호출이 사용자 요청 지연에 붙지 않는다.

        빠른 모델을 쓴다 — 원문이 한 줄짜리 짧은 텍스트이고, 어려운 8건을 실측한
        결과 빠른 모델로도 전부 맞혔다. thinking은 끈다.

        검증하지 않은 값을 그대로 돌려준다. 어휘와 모양은 호출부가 확인한다.
        """

        instruction = gemini_prompts.build_closure_extraction_instruction()
        result = await self._call_structured(
            instruction,
            rest_date,
            _ClosureExtraction,
            operation="extract_closure_rules",
            thinking_budget=0,
            model_names=self._fast_model_names,
        )
        return provider_result(result.model_dump(), source=ProviderSource.GEMINI)

    async def judge_travel_modes(
        self,
        segments: Sequence[SegmentModeInput],
        context: ModeJudgmentContext,
    ) -> ProviderResult[tuple[str, ...]]:
        """구간별 이동수단을 전 구간 한 번에 정한다. (TP-227)

        구간 하나씩 부르지 않는 이유는 앞 구간을 봐야 뒤 구간의 강도를 조절할 수
        있기 때문이다. 구간별로 부르면 호출이 구간 수만큼 늘고 같은 문제가 남는다.

        thinking_budget=0 — 일정 편성(`generate_schedule_plan`)과 같은 이유다.
        판정 근거(거리·도보시간·조건)를 프롬프트에 이미 명시적으로 주고 있어
        thinking 없이도 고를 수 있고, 이 호출은 SCHEDULE·RECOMMEND 턴의 지연에
        그대로 더해진다.

        **값 검증은 여기서 하지 않는다.** 문자열 목록을 그대로 돌려주고, 개수와
        어휘는 호출부가 확인한다 — 어느 구간의 답인지 아는 쪽이 거기이기 때문이다.
        """

        instruction = gemini_prompts.build_mode_judge_instruction()
        context_text = gemini_prompts.format_mode_judge_context(segments, context)
        result = await self._call_structured(
            instruction,
            context_text,
            _TravelModePlan,
            operation="judge_travel_modes",
            thinking_budget=0,
            model_names=self._mode_judge_model_names,
        )
        return provider_result(tuple(result.modes), source=ProviderSource.GEMINI)

    async def generate_schedule_plan(
        self, request: SchedulePlanningRequest
    ) -> ProviderResult[ScheduleLLMPlan]:
        # visit_datetime은 app.schedule.planner가 fallback(현재 시각)까지 반영해서
        # 넘겨준다 — 여기서는 이미 값이 있다고 가정하고 표시 형식만 맞춘다.
        assert request.visit_datetime is not None
        start_time = request.visit_datetime.strftime("%H:%M")
        instruction = gemini_prompts.build_schedule_planning_instruction(
            time_available_min=request.conditions.time_available,
            # 상한을 프롬프트가 직접 계산하지 않는다 — planner가 후보 부족 가드와
            # 보관함 자르기에 쓰는 것과 같은 함수를 부른다(TP-239).
            item_range=derive_item_range(request),
            # 붙어 있는 후보가 있으면 짧게 제안해 달라고 부탁한다(TP-243).
            # 계약은 policy_for(clustered=True)와 개수 상한이 이미 걸고 있고,
            # 이 부탁이 없으면 예산이 넉넉한 요청에서 그 여유를 아무도 안 쓴다.
            clustered_candidates=clusterable_slot_count(request) >= 2,
        )
        context = gemini_prompts.format_schedule_planning_context(request, start_time)
        # thinking_budget=0 — 일정 편성은 구조화 출력이 무거워(3~5개 항목×6개 필드)
        # thinking이 지연시간의 상당 부분을 차지하는 것으로 보여 꺼서 응답 속도를
        # 줄인다(실사용 지연시간 개선 검토, 2026-08-13). 라우트 선택 근거는
        # pairwise_distances_km·조건을 프롬프트에 이미 명시적으로 주고 있어
        # thinking 없이도 규칙 기반 선택이 가능하다고 판단 — 다만 품질 저하가
        # 관측되면 0보다 큰 낮은 예산으로 조정할 수 있다(_call_structured 참고).
        result = await self._call_structured(
            instruction,
            context,
            ScheduleLLMPlan,
            operation="generate_schedule_plan",
            thinking_budget=0,
            model_names=self._generation_model_names,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def generate_schedule_fill(
        self, request: SchedulePartialFillRequest
    ) -> ProviderResult[SchedulePartialLLMPlan]:
        assert request.visit_datetime is not None
        start_time = request.visit_datetime.strftime("%H:%M")
        instruction = gemini_prompts.build_schedule_fill_instruction()
        context = gemini_prompts.format_schedule_fill_context(request, start_time)
        # thinking_budget=0 — generate_schedule_plan()과 같은 이유.
        result = await self._call_structured(
            instruction,
            context,
            SchedulePartialLLMPlan,
            operation="generate_schedule_fill",
            thinking_budget=0,
            model_names=self._generation_model_names,
        )
        return provider_result(result, source=ProviderSource.GEMINI)

    async def _call_structured(
        self,
        system_instruction: str,
        user_input: str,
        response_model: type[T],
        *,
        operation: str,
        thinking_budget: int | None = None,
        model_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> T:
        """호출별 thinking 예산과 모델 목록을 함께 전달한다.

        기본값 None이면 해당 모델의 기본 동작을 유지한다. thinking_budget=0은
        "완전히 끔"이 목적이지만, 실제로 어떤 값이 API에 실리는지는
        _try_model()의 _resolve_thinking_budget()/_thinking_config_for() 조합이
        모델·호출 종류별로 결정한다 — 일부 모델(Flash-Lite 등)은 thinking_budget=0
        자체를 거부해 thinking_config를 생략하고, 나머지는 Gemini 3.x 권장 방식인
        thinking_level=MINIMAL로 변환된다(2026-08-18,
        _thinking_config_for() docstring 참고). model_names를 넘기면 이번 호출에서만
        그 모델 목록을 쓴다(폴백 순서 포함) — 넘기지 않으면 생성자가 정한 기본
        목록(D-052 fast/generation 구분)을 쓴다.
        """
        generate_kwargs = {"model_names": model_names} if model_names is not None else {}
        if history:
            generate_kwargs["history"] = history
        try:
            return await self._generate(
                system_instruction,
                user_input,
                response_model,
                operation,
                thinking_budget=thinking_budget,
                **generate_kwargs,
            )
        except ValidationError as exc:
            retry_instruction = system_instruction + gemini_prompts.format_validation_retry_note(
                exc
            )
            try:
                return await self._generate(
                    retry_instruction,
                    user_input,
                    response_model,
                    operation,
                    thinking_budget=thinking_budget,
                    **generate_kwargs,
                )
            except ValidationError as retry_exc:
                raise AppError(
                    code="llm_output_invalid",
                    message="LLM 응답을 해석하지 못했습니다.",
                    status_code=502,
                    retryable=True,
                    provider="Gemini",
                    details={"validation_error": str(retry_exc)},
                ) from None

    async def _generate(
        self,
        system_instruction: str,
        user_input: str,
        response_model: type[T],
        operation: str,
        *,
        thinking_budget: int | None = None,
        model_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> T:
        """1순위 모델부터 순서대로 시도한다(D-052). 각 모델에서 타임아웃/429/5xx는
        _try_model()이 지수 백오프로 재시도하고, 그 예산이 소진되면 다음 모델로
        넘어간다. 마지막 모델까지 소진되면 오늘과 동일하게 ProviderTimeoutError/
        ProviderUnavailableError를 던진다. 4xx 등 비재시도 오류는 모델을 바꿔도
        결과가 같으므로 _try_model()에서 폴백 없이 즉시 실패한다(아래로 그대로
        전파됨 — 이 메서드는 잡지 않는다).
        """

        operation_started = time.perf_counter()
        last_error: ProviderTimeoutError | ProviderUnavailableError | None = None

        selected_models = model_names or self._generation_model_names
        attempted_models: list[str] = []
        # 이번 시도가 실제로 쓴 토큰. 모델을 바꿀 때마다 비운다 — 폴백 후 기록되는
        # 값이 앞 모델 것이면 안 된다. 응답이 왔지만 스키마 검증에서 실패한 경우
        # (ValidationError)에도 토큰은 이미 과금됐으므로 그 분기에서도 함께 남긴다.
        usage: dict[str, int] = {}
        for model_index, model_name in enumerate(selected_models):
            attempted_models.append(model_name)
            usage.clear()
            try:
                result, retry_count = await self._try_model(
                    model_name,
                    system_instruction,
                    user_input,
                    response_model,
                    operation=operation,
                    thinking_budget=thinking_budget,
                    usage_sink=usage,
                    history=history,
                )
            except _RetryableExhaustedError as exc:
                last_error = exc.original
                is_last_model = model_index == len(selected_models) - 1
                if is_last_model:
                    record_llm_call(
                        operation=operation,
                        attempted_models=attempted_models,
                        served_model=None,
                        latency_ms=round((time.perf_counter() - operation_started) * 1000),
                        # 이 모델에서 재시도를 전부 소진했다는 뜻이라 그 값 자체다.
                        retry_count=self._max_retries,
                        **usage,
                    )
                    logger.error(
                        "Gemini 전 모델 소진, 최종 실패 (models=%s): %s",
                        selected_models,
                        last_error,
                    )
                    raise last_error from None
                logger.warning(
                    "Gemini 모델 %s 재시도 소진, %s로 폴백: %s",
                    model_name,
                    selected_models[model_index + 1],
                    last_error,
                )
                continue
            except ProviderUnavailableError:
                # 4xx 등 비재시도 오류는 모델을 바꿔도 해결되지 않아 즉시 끝난다 —
                # _try_model()이 첫 시도에서 바로 던지므로 재시도는 없었다.
                record_llm_call(
                    operation=operation,
                    attempted_models=attempted_models,
                    served_model=None,
                    latency_ms=round((time.perf_counter() - operation_started) * 1000),
                    retry_count=0,
                    **usage,
                )
                raise
            except ValidationError:
                # Gemini는 응답했지만 구조화 스키마 검증에 실패한 경우다. 뒤의
                # _call_structured() 보정 재시도와 구분할 수 있게 모델을 남긴다.
                # 몇 번째 시도에서 검증이 깨졌는지는 _try_model() 밖으로 안 나와
                # retry_count를 알 수 없다 — None으로 둔다.
                record_llm_call(
                    operation=operation,
                    attempted_models=attempted_models,
                    served_model=model_name,
                    latency_ms=round((time.perf_counter() - operation_started) * 1000),
                    **usage,
                )
                raise

            record_llm_call(
                operation=operation,
                attempted_models=attempted_models,
                served_model=model_name,
                latency_ms=round((time.perf_counter() - operation_started) * 1000),
                retry_count=retry_count,
                **usage,
            )
            if model_index > 0:
                logger.warning(
                    "Gemini 폴백 모델로 응답 성공 (served_by=%s, primary=%s)",
                    model_name,
                    selected_models[0],
                )
            return result

        raise AssertionError("unreachable: model loop exited without returning or raising")

    async def _try_model(
        self,
        model_name: str,
        system_instruction: str,
        user_input: str,
        response_model: type[T],
        *,
        operation: str,
        thinking_budget: int | None = None,
        usage_sink: dict[str, int] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> tuple[T, int]:
        """모델 하나에 대해서만 타임아웃/429/5xx를 지수 백오프로 최대
        self._max_retries회 재시도한다. 재시도가 소진되면 _RetryableExhaustedError로
        감싸 던져 호출부(_generate)가 다음 모델로 넘어갈지 판단하게 한다. 4xx 등
        비재시도 오류는 감싸지 않고 ProviderUnavailableError를 바로 던진다 —
        모델을 바꿔도 같은 이유로 실패할 것이므로 폴백 대상이 아니다.

        반환값은 (결과, 이 모델에서 실제로 재시도한 횟수)다. 성공이 몇 번째
        시도에서 났는지를 호출부가 감사 기록(retry_count)에 남기기 위해서다 —
        재시도가 성공하면 로그도 안 남고 attempted_models도 안 늘어나, 이 값이
        없으면 "모델이 원래 느렸다"와 "타임아웃 후 재시도로 늦게 성공했다"를
        구분할 방법이 없다(D-076 검토 후속).

        thinking_budget이 None이면 GenerateContentConfig에 thinking_config를
        아예 안 넣어 모델 자체 기본 동작을 그대로 둔다 — 이 기본값이 어떤 값인지는
        모델마다 다르다(gemini-2.5-flash는 가벼운 동적 thinking이었지만,
        gemini-3.5-flash는 기본이 MEDIUM이라 더 무겁다, 2026-08-18). thinking_budget이
        None이 아니면 먼저 _resolve_thinking_budget()으로 모델·호출 종류에 맞춰
        보정한다 — override 테이블(예: gemini-2.5-flash-lite의 classify_intent) 또는
        thinking_budget=0을 거부하는 모델(Flash-Lite 계열)이면 None으로 바꿔
        thinking_config 자체를 생략한다(근거:
        backend/test_results/intent_experiments_2026-08.md §4, §5). 그렇게 보정된
        값을 _thinking_config_for()에 넘겨 실제 ThinkingConfig로 바꾼다 — 0은 레거시
        thinking_budget이 아니라 thinking_level=MINIMAL로 변환한다(레거시 파라미터를
        Gemini 3.x에서 계속 믿을 수 없어졌기 때문, 자세한 배경은
        _thinking_config_for() docstring 참고). 폴백으로 넘어가면 model_name이
        바뀌므로 같은 요청 안에서도 모델마다 다른 thinking_config가 실릴 수 있다.
        """

        resolved_budget = _resolve_thinking_budget(model_name, operation, thinking_budget)
        thinking_config = _thinking_config_for(resolved_budget)

        # 이 모델 시도 하나를 Langfuse generation 하나로 남긴다. 안쪽 재시도는
        # 여기 합산된다 — 백오프 대기까지 포함한 "이 모델에 실제로 쓴 시간"이다.
        # 재시도 횟수는 api_usage가 시도 단위로 따로 세고 있다.
        # 꺼져 있으면(기본값) 아무 동작도 하지 않는 no-op이다.
        with observe_generation(
            operation,
            model=model_name,
            # 어느 프롬프트 슬롯·버전이 이 호출을 냈는지. 원문 수집을 꺼도 남는다.
            version=operation_prompt_version(operation),
            input={"system_instruction": system_instruction, "user_input": user_input},
            # 프롬프트 관리를 켰으면 그 버전에 이 호출을 묶는다(집계용).
            prompt=_linked_prompt(operation),
        ) as generation:
            return await self._run_attempts(
                model_name,
                system_instruction,
                user_input,
                response_model,
                operation=operation,
                thinking_config=thinking_config,
                usage_sink=usage_sink,
                generation=generation,
                history=history,
            )

    async def _run_attempts(
        self,
        model_name: str,
        system_instruction: str,
        user_input: str,
        response_model: type[T],
        *,
        operation: str,
        thinking_config: object,
        usage_sink: dict[str, int] | None,
        generation: object,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> T:
        """한 모델에 대한 재시도 루프. 계측은 호출부(_try_model)가 감싼다."""

        contents = _build_contents(user_input, history)
        for attempt in range(self._max_retries + 1):
            # google-genai는 자체 전송 계층을 써서 MeteredTransport를 거치지 않는다.
            # 재시도 한 번도 별개의 과금·쿼터 소모라 시도 단위로 센다.
            started = time.perf_counter()
            try:
                response = await self._client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=response_model,
                        temperature=0.0,
                        thinking_config=thinking_config,
                    ),
                )
            except _TIMEOUT_ERRORS:
                _record_gemini_call(model_name, started, ok=False, status="timeout")
                if attempt >= self._max_retries:
                    raise _RetryableExhaustedError(ProviderTimeoutError("Gemini")) from None
            except genai_errors.APIError as exc:
                _record_gemini_call(model_name, started, ok=False, status=str(exc.code))
                if exc.code not in _RETRYABLE_STATUS_CODES:
                    status = f" {exc.status}" if hasattr(exc, "status") else ""
                    detail = f"{exc.code}{status} (첫 시도부터 실패)"
                    raise ProviderUnavailableError("Gemini", detail=detail) from None
                if attempt >= self._max_retries:
                    status = f" {exc.status}" if hasattr(exc, "status") else ""
                    detail = f"{exc.code}{status} (재시도 {attempt}회 소진)"
                    raise _RetryableExhaustedError(
                        ProviderUnavailableError("Gemini", detail=detail)
                    ) from None
            else:
                _record_gemini_call(model_name, started, ok=True, status="ok")
                usage = _token_usage(getattr(response, "usage_metadata", None))
                if usage_sink is not None:
                    usage_sink.update(usage)
                # 파싱이 실패해도(ValidationError) 토큰은 이미 과금됐다 — 기록을
                # 먼저 남긴다.
                generation.record(usage_details=_usage_details(usage))
                if response.parsed is not None:
                    parsed = response_model.model_validate(response.parsed)
                else:
                    # response_schema가 SDK 자동 파싱을 못 한 경우(빈 응답 등)
                    # 원문 텍스트로 직접 검증한다.
                    parsed = response_model.model_validate_json(response.text or "")
                generation.record(output=parsed)
                # attempt는 이 모델에서 실제로 재시도한 횟수다(develop의 retry_count).
                return parsed, attempt

            await asyncio.sleep(_backoff_seconds(attempt))

        raise AssertionError("unreachable: retry loop exited without returning or raising")


__all__ = ["RealGeminiProvider"]

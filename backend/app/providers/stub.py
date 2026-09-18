"""TripBranch fake provider 구현체 모음.

역할: 외부 API 호출 없이 고정/임시 데이터로 각 provider 계약을 만족시킨다.
입력: 각 provider protocol이 요구하는 파라미터.
출력: 각 provider protocol이 요구하는 응답 모델.
호출 시점: PLACE_PROVIDER=fake 등 설정이 fake일 때 provider 팩토리가 주입한다.
TODO: 실제 provider(RealPlaceProvider 등)가 준비되면 팩토리에서 설정값으로 분기한다.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    PlaceCategoryFilter,
    PlaceDetails,
    WeatherForecastResult,
    WeatherForecastSlot,
)
from app.domain.operating_hours import normalize_operating_schedule
from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput
from app.errors import AppError
from app.place_search_policy import DEFAULT_PLACE_PROVIDER_RESULT_LIMIT
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.mappers import resolve_place_category
from app.providers.protocols import BarrierFreePlaceSearch
from app.providers.tour_intro_keys import (
    BABY_CARRIAGE_KEYS,
    CREDIT_CARD_KEYS,
    PARKING_FEE_KEYS,
    PARKING_KEYS,
    PET_KEYS,
    RESTROOM_KEYS,
    USE_FEE_KEYS,
)
from app.schedule.schemas import (
    ScheduleLLMItem,
    ScheduleLLMPlan,
    SchedulePartialFillRequest,
    SchedulePartialLLMPlan,
    SchedulePlanningRequest,
)
from app.schemas import (
    ClarificationPayload,
    CompareCriteria,
    ComparePayload,
    ComparisonItem,
    ComparisonResult,
    ConcentrationIntent,
    ConversationTurnView,
    Environment,
    GeneralPayload,
    GeneralTopic,
    InfoPayload,
    Intent,
    IntentClassificationResult,
    LLMOutput,
    MissingField,
    ModifyPayload,
    ModifyType,
    OutOfScopeCategory,
    OutputStatus,
    PlaceCandidate,
    PlaceContext,
    PlacePreferenceInsight,
    PlaceTag,
    PlaceType,
    QuestionType,
    RecommendationResponse,
    RecommendPayload,
    Severity,
    StatedWeather,
    Transport,
    UserConditions,
    WeatherIntent,
)

# NOTE(비활성화, 팀 논의 후 결정 필요): 아래 FakeInterpretProvider는 669cc82(2026-07-22,
# 작성자 mac)에서 도입된 원본 코드다. 2026-07-24 LLM provider 1차 구현(21aad22)에서
# interpret_user_input()의 시그니처가
#   def interpret_user_input(user_input: str) -> InterpretedConditions
# 에서
#   async def interpret_user_input(request: InterpretRequest) -> LLMOutput
# 로 바뀌면서 아래 코드를 그대로 실행하면 깨진다(인자 개수·타입, sync/async 모두 불일치).
# 삭제하지 않고 주석으로만 남겨둔다 — 이 provider를 계속 쓸지, 새 LLMOutput 계약에 맞게
# 고쳐 쓸지는 팀 확인 후 결정한다.
#
# class FakeInterpretProvider:
#     """자연어 입력 해석을 고정 조건으로 대체하는 fake provider."""
#
#     def interpret(self, user_input: str) -> InterpretedConditions:
#         return interpret_user_input(user_input)

_KNOWN_PLACE_NAMES = (
    "경복궁",
    "창덕궁",
    "종묘",
    "인사동",
    "광화문",
    "북촌한옥마을",
    "북촌",
    "종로3가역",
)
_HARMFUL_MARKERS = ("바보", "미친", "죽어", "씨발", "개새끼")
_OFF_TOPIC_MARKERS = ("주식", "수학 문제", "코드 짜줘", "파이썬 코드")
_PROMPT_INJECTION_MARKERS = ("시스템 프롬프트", "프롬프트를 보여줘", "무시하고")
_REJECT_ALL_MARKERS = ("다른 곳", "다른 거", "전부 별로", "다 마음에 안", "다른거")
# _shared/rules/transport.md와 같은 매핑을 미러링한다(RECOMMEND/MODIFY 공유,
# TP-105 — 자동차 경로 네이버 실측이 transport=CAR를 봐야 실제로 호출된다).
# (조사까지 붙인 라벨, ComparisonItem 필드명) — summary_instruction.md의 나열
# 순서(도보·자동차·대중교통)를 Fake에서 미러링한다. "대중교통"은 받침이 있어
# "으로"를 붙여야 하므로("대중교통로"는 어색함) 조사까지 라벨에 미리 넣어둔다.
_TRAVEL_MODE_FIELDS: tuple[tuple[str, str], ...] = (
    ("도보로", "travel_walking_minutes"),
    ("자동차로", "travel_driving_minutes"),
    ("대중교통으로", "travel_transit_minutes"),
)


def _fastest_travel_minutes(item: ComparisonItem) -> int | None:
    candidates = [
        minutes
        for minutes in (
            item.travel_walking_minutes,
            item.travel_driving_minutes,
            item.travel_transit_minutes,
        )
        if minutes is not None
    ]
    return min(candidates) if candidates else None

_TRANSPORT_CAR_MARKERS = ("차로", "운전해서", "차 타고", "차로 가려는데")
_TRANSPORT_WALK_MARKERS = ("걸어서", "도보로", "걸어갈")
_TRANSPORT_PUBLIC_MARKERS = ("대중교통으로", "버스나 지하철", "지하철 타고", "버스 타고")


def _detect_transport(user_input: str) -> Transport | None:
    """RECOMMEND/MODIFY 양쪽이 같은 판정을 쓰도록 공유한다."""

    if any(marker in user_input for marker in _TRANSPORT_CAR_MARKERS):
        return Transport.CAR
    if any(marker in user_input for marker in _TRANSPORT_WALK_MARKERS):
        return Transport.WALK
    if any(marker in user_input for marker in _TRANSPORT_PUBLIC_MARKERS):
        return Transport.PUBLIC
    return None
# SCHEDULE-09: 순번 언급("두 번째는 별로야") → REJECT_SPECIFIC 판별용.
# ComparePayload.targets 파싱과 달리 여기서는 실제로 순번을 파싱해 target_indices를
# 채운다 — REJECT_SPECIFIC 자체가 이번에 신설된 값이라 테스트가 파싱 결과에 의존한다.
_ORDINAL_TO_INDEX = {
    "첫 번째": 1,
    "첫번째": 1,
    "두 번째": 2,
    "두번째": 2,
    "세 번째": 3,
    "세번째": 3,
    "네 번째": 4,
    "네번째": 4,
    "다섯 번째": 5,
    "다섯번째": 5,
}
_REJECT_SPECIFIC_CUE_MARKERS = ("별로", "빼줘", "빼줄래", "빼고", "다른 데로", "다른 곳으로")
# SCHEDULE-09 후속: "두 번째 말고는 다 마음에 안 들어"처럼 남길 자리를 지목하고
# 나머지 전부를 거부하는 표현 — target_indices를 "언급된 순번의 여집합"으로
# 계산해야 한다(직접 지목과 정반대 방향). "말고"는 이미 _MODIFY_CHANGE_MARKERS에
# 있어 classify_intent()의 MODIFY 라우팅은 별도 수정 없이 그대로 통과한다.
_EXCLUSION_MARKERS = ("말고는", "말고")


def _is_reject_specific_utterance(user_input: str) -> bool:
    """ "두 번째는 별로야"처럼 순번 언급과 거절 신호가 함께 있으면 True.

    classify_intent()(1단계, MODIFY로 라우팅 여부)와 extract_modify_conditions()
    (2단계, REJECT_SPECIFIC 판별)가 같은 기준을 쓰도록 공유한다 — 기준이
    갈리면 1단계는 MODIFY로 안 보내는데 2단계는 REJECT_SPECIFIC을 반환하려는
    (또는 그 반대) 모순이 생길 수 있다.
    """
    has_ordinal = any(marker in user_input for marker in _ORDINAL_TO_INDEX)
    has_cue = any(marker in user_input for marker in _REJECT_SPECIFIC_CUE_MARKERS)
    return has_ordinal and has_cue


def _mentions_shown_place_by_name(user_input: str, shown_place_names: list[str] | None) -> bool:
    """SCHEDULE-09 후속(이름 지목): 노출된 항목 이름이 발화에 그대로 들어있으면 True.

    빈 문자열(이름 미저장 과거 세션)은 건너뛴다.
    """
    return any(name and name in user_input for name in (shown_place_names or []))


_MODIFY_CHANGE_MARKERS = (
    "말고",
    "빼고",
    "무료",
    "가격 상관없",
    "예산 상관없",
    "가까운",
    "먼 곳",
    "실내로",
    "야외도",
    "주차",
    "근처로 바꿔",
)

# Fake도 Real Gemini 프롬프트의 MODIFY 장소 유형 교체/병합 규칙을 재현한다. 테스트
# 환경에서 "공원도 추천"이 카페+공원 누적으로 오인되면 Real 경로와 다른 C 분류 충돌을
# 놓칠 수 있으므로, 대표적인 교차 유형 태그를 명시한다.
_MODIFY_CATEGORY_TAGS = (
    ("카페", PlaceTag.CAFE, PlaceType.RESTAURANT),
    ("공원", PlaceTag.PARK, PlaceType.ATTRACTION),
)
_EXPLICIT_CATEGORY_ADD_MARKERS = ("포함", "함께 넣", "같이 넣")
_COMPARE_MARKERS = ("가까워", "오래 열어", "어디가 좋아", "뭐가 나아", "비교해")
_SCHEDULE_MARKERS = (
    "일정 짜",
    "일정 만들어",
    "일정 만들",
    "코스 짜",
    "코스 만들어",
    "코스 만들",
    "루트 만들어",
    "루트 만들",
    "순서 알려",
    "어디부터 갈",
)
# state_transform._RESET_SCOPE_PHRASES와 같은 문구를 미러링한다(D-059) — SCHEDULE
# 되묻기를 이어가는 도중에도 사용자가 명시적으로 재시작을 말하면 이어가기로 강제하지
# 않는다. Fake는 프로덕션 상태 모듈에 의존하지 않는 레이어 분리를 유지하므로 별도 상수로
# 둔다(문구 4개뿐이라 중복 비용이 적다).
_EXPLICIT_RESTART_MARKERS = (
    "처음부터 다시",
    "조건 다시 정할게",
    "조건 다시 정하고 싶어",
    "새로 시작",
)
# schedule06_ambiguous_recommend 되묻기("일정 계속 짤까요, 장소만 추천할까요?")의 두
# 선택지는 서로 다른 인텐트라, 위 _SCHEDULE_MARKERS 같은 "일단 SCHEDULE 유지" 규칙을
# 그대로 적용하면 "추천만 해줘"류 답변까지 SCHEDULE로 잘못 강제된다(2026-08-31 실사용
# 재현). context_rules.md의 같은 이름 규칙을 흉내낸다.
_SCHEDULE06_RECOMMEND_ONLY_MARKERS = ("추천만", "장소만", "그냥 추천")
_SCHEDULE06_CONTINUE_MARKERS = ("일정", "계속", "이어서")
_INFO_QUESTION_MARKERS = (
    "열어",
    "몇 시",
    "입장료",
    "얼마",
    "주차",
    "화장실",
    "휠체어",
    "전시",
    "행사",
    "어디에 있",
    "주소",
    "사람 많",
    "붐빌",
    "혼잡",
    "개요",
    "가는데 얼마나 걸",
)
_GENERAL_MARKERS = (
    "역사",
    "지어졌",
    "여행 팁",
    "언제 피어",
    "동네",
    "에티켓",
    "막차",
    "동선",
)
_SERVICE_IDENTITY_MARKERS = (
    "넌 누구",
    "너 누구",
    "이름이 뭐",
    "뭘 할 수",
    "뭐 할 수",
    "트리비",
    "TripBranch",
    "tripbranch",
)
_LOCATION_ONLY_REMAINDERS = frozenset(
    {
        "근처",
        "근처는",
        "근처에서",
        "근처로",
        "근처어때",
        "주변",
        "주변은",
        "주변에서",
        "주변으로",
        "주변어때",
        "에서",
        "으로",
        "로",
        "은",
        "는",
        "어때",
    }
)
_LOCATION_ANSWER_REMAINDERS = frozenset({"", "요", "이요", "입니다", "이에요"})
_LOCATION_CLARIFICATION_CODES = frozenset({"location_required", "location_ambiguous"})


def _find_known_place(user_input: str) -> str | None:
    return next((name for name in _KNOWN_PLACE_NAMES if name in user_input), None)


def _is_location_only_change(user_input: str) -> bool:
    """이전 추천 이력 뒤 검색 중심점만 바꾸는 짧은 발화인지 판정한다.

    지명 단독("광화문")은 제외한다 — 추천을 받은 뒤 지명만 던지는 건 검색 위치 변경이
    아니라 그 장소를 지목한 정보 질문이라, INFO 경계 사례로 남긴다(intent-definition.md §5).
    """

    place_name = _find_known_place(user_input)
    if place_name is None:
        return False

    remainder = user_input.replace(place_name, "", 1).strip()
    if not remainder:
        return False
    for prefix in ("그럼", "그러면", "아니"):
        if remainder.startswith(prefix):
            remainder = remainder[len(prefix) :].strip()
            break
    normalized = remainder.replace(" ", "").rstrip("?!.")
    return normalized in _LOCATION_ONLY_REMAINDERS


def _is_simple_location_answer(user_input: str) -> bool:
    """위치 되묻기에 답한 지명 단독/짧은 존댓말 답변인지 판정한다."""

    place_name = _find_known_place(user_input)
    if place_name is None:
        return False
    remainder = user_input.replace(place_name, "", 1).strip().replace(" ", "").rstrip("?!.")
    return remainder in _LOCATION_ANSWER_REMAINDERS


def _is_location_scoped_change(user_input: str) -> bool:
    """이전 추천 이력 뒤 "지명 + 근처/주변"으로 검색 범위를 옮기는 발화인지 판정한다.

    `_is_location_only_change()`가 잔여 조건이 전혀 없는 발화만 받는 데 비해, 이쪽은
    "경복궁 근처 카페 추천해줘"처럼 위치와 함께 다른 조건(카테고리 등)이 붙은 발화까지
    받는다 — 실 Gemini(프롬프트 1.0.2)가 이런 발화를 MODIFY로 분류하는 것과 맞추기
    위해서다(D-053). 지명 단독은 여기서도 제외되고(뒤에 근처/주변이 없다), 정보/일반
    질문 어휘가 섞이면 INFO·GENERAL 판정을 가리지 않도록 빠진다.
    """

    place_name = _find_known_place(user_input)
    if place_name is None:
        return False

    tail = user_input[user_input.find(place_name) + len(place_name) :]
    if not tail.strip().replace(" ", "").startswith(("근처", "주변")):
        return False
    return not any(marker in user_input for marker in _INFO_QUESTION_MARKERS + _GENERAL_MARKERS)


def _stub_visit_time(user_input: str, reference_date: date) -> str:
    """concentration-conditions.md §3.2 파싱 규칙의 최소 스텁 버전.

    "오늘"/"내일"/"이번 주말" 정도만 구분하고, 그 외(명시적 날짜 등)는 기준일로
    둔다 — 실제 자연어 날짜 파싱은 Real Gemini provider의 책임이다.
    """
    if "내일" in user_input:
        return (reference_date + timedelta(days=1)).isoformat()
    if "주말" in user_input:
        days_until_saturday = (5 - reference_date.weekday()) % 7
        days_ahead = days_until_saturday or 7
        return (reference_date + timedelta(days=days_ahead)).isoformat()
    return reference_date.isoformat()


# answer_with_tools() 기본 구현이 도구를 한 번씩 호출해볼 때 쓰는 자리표시자 인자.
# 실제로 유효한 지역명일 필요는 없다 — 이 기본 구현을 그대로 쓰는 테스트는 도구
# 자체의 동작(성공/실패 문자열)까지는 검증하지 않는다는 뜻이다.
_FAKE_TOOL_PROBE_ARG = "테스트지역"


class FakeLLMProvider:
    """실제 Gemini 호출 없이 키워드 매칭으로 LLMOutput을 흉내 내는 fake provider.

    FakeGeocodingProvider가 substring 매칭으로 소수 지명만 처리하는 것과 같은 결이다.
    test-cases.md TC-01~04(RECOMMEND), TC-07~09(MODIFY), TC-11(GENERAL),
    TC-12/13(OUT_OF_SCOPE)와 llm-output-schema.md §7의 needs_clarification 예시를
    재현할 수 있는 수준까지만 다룬다 — 실제 자연어 이해가 아니라 고정 회귀 테스트용.
    """

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
        if any(marker in user_input for marker in _PROMPT_INJECTION_MARKERS):
            result = IntentClassificationResult(
                intent=Intent.OUT_OF_SCOPE,
                out_of_scope_category=OutOfScopeCategory.PROMPT_INJECTION,
                out_of_scope_severity=Severity.HIGH,
            )
        elif any(marker in user_input for marker in _HARMFUL_MARKERS):
            result = IntentClassificationResult(
                intent=Intent.OUT_OF_SCOPE,
                out_of_scope_category=OutOfScopeCategory.HARMFUL,
                out_of_scope_severity=Severity.HIGH,
            )
        elif any(marker in user_input for marker in _OFF_TOPIC_MARKERS):
            result = IntentClassificationResult(
                intent=Intent.OUT_OF_SCOPE,
                out_of_scope_category=OutOfScopeCategory.UNRELATED,
                out_of_scope_severity=Severity.LOW,
            )
        elif any(marker in user_input for marker in _SCHEDULE_MARKERS):
            result = IntentClassificationResult(intent=Intent.SCHEDULE)
        elif pending_clarification == "schedule06_ambiguous_recommend" and any(
            marker in user_input for marker in _SCHEDULE06_RECOMMEND_ONLY_MARKERS
        ):
            result = IntentClassificationResult(intent=Intent.RECOMMEND)
        elif pending_clarification == "schedule06_ambiguous_recommend" and any(
            marker in user_input for marker in _SCHEDULE06_CONTINUE_MARKERS
        ):
            result = IntentClassificationResult(intent=Intent.SCHEDULE)
        elif (
            last_intent == Intent.SCHEDULE.value
            and pending_clarification is not None
            and not any(phrase in user_input for phrase in _EXPLICIT_RESTART_MARKERS)
        ):
            # D-059: 직전 턴이 SCHEDULE 되묻기로 끝났으면, 지명만 던지거나 조건만
            # 보충하는 짧은 답변도 새 MODIFY 요청이 아니라 그 SCHEDULE을 이어가는
            # 중이다. MODIFY 분기(바로 아래)보다 먼저 검사해 우선순위를 준다.
            result = IntentClassificationResult(intent=Intent.SCHEDULE)
        elif (
            last_intent == Intent.INFO.value
            and pending_clarification is not None
            and _find_known_place(user_input) is not None
        ):
            # 직전 INFO 되묻기(장소를 몰라서 되물었거나 후보가 여러 개라 되물은
            # 경우) 뒤에 알려진 장소명이 나오면 검색 중심점 변경(MODIFY)이 아니라
            # 방금 물어본 질문의 장소 답변이다 — context_rules.md의 같은 이름
            # 규칙을 흉내낸다. 아래 "이전 추천 있음 + 지명 단독 → MODIFY" 규칙보다
            # 먼저 검사해 우선순위를 준다(2026-08-31 실사용 재현).
            result = IntentClassificationResult(intent=Intent.INFO)
        elif (
            last_intent in (Intent.RECOMMEND.value, Intent.MODIFY.value)
            and pending_clarification in _LOCATION_CLARIFICATION_CODES
            and _is_simple_location_answer(user_input)
        ):
            # 위치를 물어본 직후의 단순 지명은 INFO가 아니라, 기존 조건에 검색 중심을
            # 보충하는 MODIFY다. "경복궁 오늘 열어?"처럼 질문이 붙으면 이 조건을
            # 통과하지 않아 아래 INFO 규칙으로 간다.
            result = IntentClassificationResult(intent=Intent.MODIFY)
        elif has_previous_recommendation and (
            any(marker in user_input for marker in _REJECT_ALL_MARKERS + _MODIFY_CHANGE_MARKERS)
            or _is_location_only_change(user_input)
            or _is_location_scoped_change(user_input)
            or _is_reject_specific_utterance(user_input)
            or (
                _mentions_shown_place_by_name(user_input, shown_place_names)
                and any(
                    marker in user_input
                    for marker in _REJECT_SPECIFIC_CUE_MARKERS + _EXCLUSION_MARKERS
                )
            )
        ):
            result = IntentClassificationResult(intent=Intent.MODIFY)
        elif shown_place_count >= 2 and any(marker in user_input for marker in _COMPARE_MARKERS):
            result = IntentClassificationResult(intent=Intent.COMPARE)
        elif any(marker in user_input for marker in _GENERAL_MARKERS + _SERVICE_IDENTITY_MARKERS):
            result = IntentClassificationResult(intent=Intent.GENERAL)
        elif (
            conversation_place_name is not None
            and any(reference in user_input for reference in ("여기", "이곳", "거기", "이리로"))
            and any(marker in user_input for marker in _INFO_QUESTION_MARKERS)
        ):
            result = IntentClassificationResult(intent=Intent.INFO)
        elif _find_known_place(user_input) and any(
            marker in user_input for marker in _INFO_QUESTION_MARKERS
        ):
            result = IntentClassificationResult(intent=Intent.INFO)
        elif any(marker in user_input for marker in _INFO_QUESTION_MARKERS):
            # 장소명 없이 정보 질문 마커만 있는 경우("사람 많아?")도 INFO다 —
            # extract_info_query()가 place_name 없음을 이유로 되묻는다(info/
            # extract.md와 같은 규칙). 위 분기와 달리 알려진 장소가 필요 없다.
            result = IntentClassificationResult(intent=Intent.INFO)
        elif _is_simple_location_answer(user_input):
            result = IntentClassificationResult(
                intent=Intent.MODIFY if has_previous_recommendation else Intent.RECOMMEND
            )
        else:
            result = IntentClassificationResult(intent=Intent.RECOMMEND)
        return provider_result(result, source=ProviderSource.FAKE_LLM)

    async def extract_recommend_conditions(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
        # TP-266: 스텁은 모델 개념이 없어 무시한다. 받아만 두는 이유는 재시도
        # 경로가 fake provider에서도 그대로 지나가야 하기 때문이다.
        retry_models: list[str] | None = None,
    ) -> ProviderResult[LLMOutput]:
        conditions = UserConditions()
        place_name = _find_known_place(user_input)
        if place_name and (
            "근처" in user_input or "주변" in user_input or _is_simple_location_answer(user_input)
        ):
            conditions.search_center = place_name
        if "나 지금" in user_input and place_name:
            conditions.current_location = place_name
            conditions.search_center = None

        if "카페" in user_input:
            conditions.place_types.append(PlaceType.RESTAURANT)
            conditions.place_tags.append(PlaceTag.CAFE)
        if "맛집" in user_input or "음식" in user_input:
            if PlaceType.RESTAURANT not in conditions.place_types:
                conditions.place_types.append(PlaceType.RESTAURANT)
        if "박물관" in user_input:
            conditions.place_types.append(PlaceType.CULTURAL_FACILITY)
            conditions.place_tags.append(PlaceTag.MUSEUM)

        # 날씨를 언급하지 않았으면 기본값은 NO_MENTION이다.
        conditions.weather_intent = WeatherIntent.NO_MENTION

        status = OutputStatus.COMPLETE
        clarification = None
        if "눈" in user_input and not any(
            marker in user_input for marker in ("피해", "피하고", "실내", "즐기고", "보고 싶")
        ):
            conditions.weather = StatedWeather.SNOW
            conditions.weather_intent = None
            status = OutputStatus.NEEDS_CLARIFICATION
            clarification = ClarificationPayload(
                ambiguous_fields=[
                    {
                        "field": "weather_intent",
                        "user_input": user_input,
                        "candidates": ["AVOID", "ENJOY"],
                        "reason": (
                            "눈을 피해 실내를 원하시는지, 눈 오는 풍경을 즐기고 싶으신지 "
                            "확인이 필요합니다"
                        ),
                    }
                ],
                message="눈 오는 풍경을 즐기고 싶으신가요, 아니면 실내 장소를 찾으시나요?",
            )
        elif "비" in user_input:
            conditions.weather = StatedWeather.RAIN
            conditions.weather_intent = WeatherIntent.AVOID
            conditions.environment = Environment.INDOOR
        elif any(marker in user_input for marker in ("날씨 상관없", "날씨는 상관없", "아무 날씨")):
            conditions.weather_intent = WeatherIntent.IGNORE

        if any(marker in user_input for marker in ("조용", "한적", "사람 없")):
            conditions.concentration_intent = ConcentrationIntent.AVOID
        elif any(marker in user_input for marker in ("핫한", "인기", "북적")):
            conditions.concentration_intent = ConcentrationIntent.SEEK

        conditions.transport = _detect_transport(user_input)

        result = LLMOutput(
            intent=Intent.RECOMMEND,
            status=status,
            recommend=RecommendPayload(conditions=conditions),
            clarification=clarification,
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

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
        ordinal_indices = {
            index for marker, index in _ORDINAL_TO_INDEX.items() if marker in user_input
        }
        # SCHEDULE-09 후속(이름 지목): "두가헌 레스토랑은 빼줘"처럼 순번 대신
        # 노출된 항목 이름을 그대로 언급해도 같은 순번으로 매칭한다. 1-indexed —
        # shown_place_names[0]이 1번이다. 빈 문자열(이름 미저장 과거 세션)은
        # 건너뛴다 — 빈 문자열이 user_input에 항상 포함되어 오매칭나는 것을 막는다.
        name_indices = {
            rank
            for rank, name in enumerate(shown_place_names or [], start=1)
            if name and name in user_input
        }
        mentioned_indices = sorted(ordinal_indices | name_indices)

        if mentioned_indices and any(marker in user_input for marker in _EXCLUSION_MARKERS):
            # "두 번째 말고는 다 마음에 안 들어" — 언급된 순번은 남기고 나머지
            # 전부를 거부한다. 아래 직접 지목 분기와 target_indices의 의미가
            # 정반대이므로(여집합) 먼저 검사한다 — 순서를 바꾸면 이 분기가
            # 죽는다.
            out_of_range = [i for i in mentioned_indices if i > shown_place_count]
            if out_of_range:
                result = LLMOutput(
                    intent=Intent.MODIFY,
                    status=OutputStatus.NEEDS_CLARIFICATION,
                    clarification=ClarificationPayload(
                        message=f"일정에는 {shown_place_count}개 항목만 있어요. "
                        "몇 번째를 남겨드릴까요?",
                    ),
                )
                return provider_result(result, source=ProviderSource.FAKE_LLM)

            target_indices = [
                i for i in range(1, shown_place_count + 1) if i not in mentioned_indices
            ]
            result = LLMOutput(
                intent=Intent.MODIFY,
                status=OutputStatus.COMPLETE,
                modify=ModifyPayload(
                    modify_type=ModifyType.REJECT_SPECIFIC,
                    target_indices=target_indices,
                ),
            )
            return provider_result(result, source=ProviderSource.FAKE_LLM)

        if mentioned_indices and any(
            marker in user_input for marker in _REJECT_SPECIFIC_CUE_MARKERS
        ):
            out_of_range = [i for i in mentioned_indices if i > shown_place_count]
            if out_of_range:
                result = LLMOutput(
                    intent=Intent.MODIFY,
                    status=OutputStatus.NEEDS_CLARIFICATION,
                    clarification=ClarificationPayload(
                        message=f"일정에는 {shown_place_count}개 항목만 있어요. "
                        "몇 번째를 바꿔드릴까요?",
                    ),
                )
                return provider_result(result, source=ProviderSource.FAKE_LLM)

            result = LLMOutput(
                intent=Intent.MODIFY,
                status=OutputStatus.COMPLETE,
                modify=ModifyPayload(
                    modify_type=ModifyType.REJECT_SPECIFIC,
                    target_indices=mentioned_indices,
                ),
            )
            return provider_result(result, source=ProviderSource.FAKE_LLM)

        if any(marker in user_input for marker in _REJECT_ALL_MARKERS):
            result = LLMOutput(
                intent=Intent.MODIFY,
                status=OutputStatus.COMPLETE,
                modify=ModifyPayload(modify_type=ModifyType.REJECT_ALL),
            )
            return provider_result(result, source=ProviderSource.FAKE_LLM)

        changed = current_conditions.model_copy(deep=True)
        changed_fields: list[str] = []
        if "무료" in user_input or "가격 상관없" in user_input or "예산 상관없" in user_input:
            changed.budget = "free" if "무료" in user_input else None
            changed_fields.append("budget")
        if "가까운" in user_input:
            base = changed.max_travel_time or 30
            changed.max_travel_time = max(5, base // 2)
            changed_fields.append("max_travel_time")
        if "먼 곳" in user_input:
            base = changed.max_travel_time or 15
            changed.max_travel_time = min(60, base + 15)
            changed_fields.append("max_travel_time")
        if "실내로" in user_input:
            changed.environment = Environment.INDOOR
            changed_fields.append("environment")
        if "야외도" in user_input:
            changed.environment = Environment.ANY
            changed_fields.append("environment")
        if "주차" in user_input:
            changed.special_requirements = [*changed.special_requirements, "주차"]
            changed_fields.append("special_requirements")
        mentioned_categories = [
            category for category in _MODIFY_CATEGORY_TAGS if category[0] in user_input
        ]
        replacement_categories = [
            category
            for category in mentioned_categories
            if "말고" in user_input and user_input.index(category[0]) > user_input.index("말고")
        ]
        if replacement_categories:
            changed.place_tags = [tag for _, tag, _ in replacement_categories]
            changed.place_types = [place_type for _, _, place_type in replacement_categories]
            changed_fields.extend(["place_types", "place_tags"])
        elif "말고" in user_input and "카페" in user_input:
            changed.place_types = [PlaceType.RESTAURANT]
            changed.place_tags = [t for t in changed.place_tags if t != PlaceTag.CAFE]
            changed_fields.extend(["place_types", "place_tags"])
        elif mentioned_categories:
            # "공원도 추천"의 '도'는 선택지를 늘린다는 뜻이 아니라 자연스러운 강조로
            # 취급한다. '포함'처럼 명시적인 추가일 때만 기존 목록과 합친다.
            if any(marker in user_input for marker in _EXPLICIT_CATEGORY_ADD_MARKERS):
                changed.place_tags = list(
                    dict.fromkeys(
                        [*changed.place_tags, *(tag for _, tag, _ in mentioned_categories)]
                    )
                )
                changed.place_types = list(
                    dict.fromkeys(
                        [
                            *changed.place_types,
                            *(place_type for _, _, place_type in mentioned_categories),
                        ]
                    )
                )
            else:
                # "카페와 공원 같이", "카페나 공원"처럼 발화에 둘 이상을 나열한 경우는
                # 둘 다 유지하고, 한 유형만 말하면 그 유형으로 교체한다.
                changed.place_tags = [tag for _, tag, _ in mentioned_categories]
                changed.place_types = [place_type for _, _, place_type in mentioned_categories]
            changed_fields.extend(["place_types", "place_tags"])
        # 날씨는 RECOMMEND 추출과 같은 결로 맞춘다(stub.py의 extract_recommend_conditions):
        # "비"는 피하고 싶은 날씨로 보고 실내로 좁힌다. MODIFY 경로에도 날씨가 필요한 건
        # "비 오는데 ~ 근처 카페" 같은 발화가 이제 MODIFY로 분류되기 때문이다(D-053).
        if "비" in user_input:
            changed.weather = StatedWeather.RAIN
            changed.weather_intent = WeatherIntent.AVOID
            changed.environment = Environment.INDOOR
            changed_fields.extend(["weather", "weather_intent", "environment"])

        # MODIFY도 RECOMMEND와 같은 혼잡도 의도 규칙을 적용한다. 이전 조건을 복사한
        # changed 객체를 쓰므로, 이 발화에서 혼잡도를 언급하지 않으면 changed_fields에
        # 넣지 않아 기존 concentration_intent가 그대로 유지된다.
        if any(marker in user_input for marker in ("조용", "한적", "사람 없")):
            changed.concentration_intent = ConcentrationIntent.AVOID
            changed_fields.append("concentration_intent")
        elif any(marker in user_input for marker in ("핫한", "인기", "북적")):
            changed.concentration_intent = ConcentrationIntent.SEEK
            changed_fields.append("concentration_intent")

        detected_transport = _detect_transport(user_input)
        if detected_transport is not None:
            changed.transport = detected_transport
            changed_fields.append("transport")

        new_place = _find_known_place(user_input)
        if new_place and (
            "근처로 바꿔" in user_input
            or _is_location_only_change(user_input)
            or _is_location_scoped_change(user_input)
            or _is_simple_location_answer(user_input)
            or (
                pending_clarification in _LOCATION_CLARIFICATION_CODES
                and _is_simple_location_answer(user_input)
            )
        ):
            changed.search_center = new_place
            changed_fields.append("search_center")

        result = LLMOutput(
            intent=Intent.MODIFY,
            status=OutputStatus.COMPLETE,
            modify=ModifyPayload(
                modify_type=ModifyType.CHANGE_CONDITION,
                condition_changes=changed,
                changed_fields=changed_fields,
            ),
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

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
        place_name = _find_known_place(user_input)
        if place_name:
            place_context = PlaceContext.EXPLICIT
        elif has_previous_recommendation and (
            "첫 번째" in user_input or "두 번째" in user_input or "거기" in user_input
        ):
            place_context = PlaceContext.FROM_RECOMMENDATION
        else:
            place_context = PlaceContext.FROM_CONVERSATION

        if place_context is PlaceContext.FROM_CONVERSATION and conversation_place_name:
            place_name = conversation_place_name

        # 직전 턴이 장소명 없이 되물은 INFO 되묻기였고(pending_info_question_type),
        # 이번 발화에서 알려진 장소명을 새로 찾았다면 그 질문에 대한 답으로 본다 —
        # 실제 Gemini의 info/pending_question_block.md 지시와 같은 판단을 결정론으로
        # 흉내낸다(회귀 테스트가 실제 API 없이도 이 병합을 검증할 수 있게 한다).
        if pending_info_question_type and place_name and place_context is PlaceContext.EXPLICIT:
            result = LLMOutput(
                intent=Intent.INFO,
                status=OutputStatus.COMPLETE,
                info=InfoPayload(
                    place_name=place_name,
                    place_context=place_context,
                    question_type=QuestionType(pending_info_question_type),
                    specific_question=pending_info_specific_question or user_input,
                    visit_time=pending_info_visit_time,
                ),
            )
            return provider_result(result, source=ProviderSource.FAKE_LLM)

        if any(marker in user_input for marker in ("지하철", "전철")) and any(
            marker in user_input for marker in ("언제", "도착", "몇 분", "몇분")
        ):
            question_type = QuestionType.REALTIME_SUBWAY
        elif "버스" in user_input and any(
            marker in user_input for marker in ("정류장", "어디", "언제", "도착")
        ):
            question_type = QuestionType.REALTIME_BUS
        elif "주차" in user_input and any(marker in user_input for marker in ("공영", "시영")):
            question_type = QuestionType.REALTIME_PUBLIC_PARKING
        elif "주차" in user_input and (
            any(marker in user_input for marker in ("지금", "현재", "실시간", "자리", "빈자리"))
            or any(marker in user_input for marker in ("근처", "주변", "어디"))
        ):
            question_type = QuestionType.REALTIME_PARKING
        elif ("행사" in user_input or "축제" in user_input) and any(
            marker in user_input for marker in ("지금", "현재", "오늘", "실시간")
        ):
            question_type = QuestionType.REALTIME_EVENT
        elif "열어" in user_input or "몇 시" in user_input:
            question_type = QuestionType.OPERATING_HOURS
        elif "가는데 얼마나 걸" in user_input:
            # "얼마"가 있어도 입장료가 아니라 이동시간 질문이다.
            question_type = QuestionType.LOCATION_INFO
        elif "입장료" in user_input or "얼마" in user_input:
            question_type = QuestionType.FEE
        elif "주차" in user_input:
            question_type = QuestionType.PARKING
        elif "화장실" in user_input and any(
            # 갈 곳을 찾는 표현이 붙으면 주변 공중화장실 위치 질문이다. 그 장소
            # 하나의 시설을 묻는 "경복궁 화장실 있어?"는 아래 FACILITY로 간다.
            marker in user_input
            for marker in ("근처", "주변", "가까운", "어디", "급한", "급해")
        ):
            question_type = QuestionType.PUBLIC_TOILET
        elif "화장실" in user_input or "휠체어" in user_input:
            question_type = QuestionType.FACILITY
        elif "전시" in user_input or "행사" in user_input:
            question_type = QuestionType.EVENT
        elif "어디에 있" in user_input or "주소" in user_input:
            question_type = QuestionType.LOCATION_INFO
        elif any(marker in user_input for marker in ("카페", "커피", "상권")) and any(
            marker in user_input for marker in ("지금", "사람 많", "붐빌", "혼잡")
        ):
            question_type = QuestionType.REALTIME_COMMERCIAL
        elif any(marker in user_input for marker in ("사람 많", "붐빌", "혼잡")):
            question_type = QuestionType.CONCENTRATION
        else:
            question_type = QuestionType.GENERAL_INFO

        visit_time = (
            _stub_visit_time(user_input, reference_date)
            if question_type is QuestionType.CONCENTRATION
            else None
        )

        # 장소명도 없고 참조할 맥락(직전 대화 장소)도 없으면 실제 info/extract.md와
        # 같은 규칙으로 되묻는다("반드시 info 필드를 채우고" — place_name만 비운다).
        # 단 공중화장실은 기기 위치로 답할 수 있어 되묻지 않는다(question_type_rules.md
        # v3.6.0: "지명이 없어도 이 유형이다").
        if (
            place_name is None
            and place_context is PlaceContext.FROM_CONVERSATION
            and question_type is not QuestionType.PUBLIC_TOILET
        ):
            result = LLMOutput(
                intent=Intent.INFO,
                status=OutputStatus.NEEDS_CLARIFICATION,
                info=InfoPayload(
                    place_name=None,
                    place_context=place_context,
                    question_type=question_type,
                    specific_question=user_input,
                    visit_time=visit_time,
                ),
                clarification=ClarificationPayload(
                    missing_fields=[
                        MissingField(field="place_name", reason="장소를 특정할 단서가 없습니다.")
                    ],
                    message="어떤 장소의 정보를 확인하고 싶으신가요?",
                ),
            )
            return provider_result(result, source=ProviderSource.FAKE_LLM)

        result = LLMOutput(
            intent=Intent.INFO,
            status=OutputStatus.COMPLETE,
            info=InfoPayload(
                place_name=place_name,
                place_context=place_context,
                question_type=question_type,
                specific_question=user_input,
                visit_time=visit_time,
            ),
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

    async def answer_with_tools(
        self,
        instruction: str,
        *,
        tools: Sequence[Callable[..., Awaitable[str]]],
        max_tool_calls: int = 3,
    ) -> ProviderResult[str]:
        """도구를 실제로 순서대로 호출해보는 최소 흉내 — 실 LLM의 판단(어떤 도구를,
        어떤 인자로, 언제 멈출지)은 흉내 내지 않는다. 이 경로를 자세히 검증하는
        테스트는 이 클래스를 상속해 직접 override한다."""

        del instruction
        outputs = [await tool(_FAKE_TOOL_PROBE_ARG) for tool in tools[:max_tool_calls]]
        return provider_result("\n".join(outputs), source=ProviderSource.FAKE_LLM)

    async def extract_compare_request(
        self,
        user_input: str,
        *,
        shown_place_count: int,
        shown_place_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        if "오래 열어" in user_input:
            criteria = CompareCriteria.TIME
        elif any(
            marker in user_input
            for marker in (
                "가까워",
                "거리 차이",
                "빨리 갈",
                "얼마나 걸려",
                "이동 시간",
                "덜 막혀",
                "덜 막힐",
            )
        ):
            criteria = CompareCriteria.TRAVEL_TIME
        else:
            criteria = CompareCriteria.OVERALL

        # 순번이든 이름이든 지목이 있으면 그 대상만 비교한다. MODIFY의
        # target_indices와 같은 매칭 규칙을 쓴다 — 1-indexed이고, 빈 이름(과거
        # 세션)은 건너뛴다(빈 문자열은 어떤 발화에도 포함돼 오매칭난다).
        ordinal_indices = {
            index for marker, index in _ORDINAL_TO_INDEX.items() if marker in user_input
        }
        name_indices = {
            rank
            for rank, name in enumerate(shown_place_names or [], start=1)
            if name and name in user_input
        }
        mentioned_indices = sorted(ordinal_indices | name_indices)

        targets: list[int] | Literal["all"] = "all"
        if mentioned_indices:
            out_of_range = [index for index in mentioned_indices if index > shown_place_count]
            if out_of_range:
                result = LLMOutput(
                    intent=Intent.COMPARE,
                    status=OutputStatus.NEEDS_CLARIFICATION,
                    clarification=ClarificationPayload(
                        missing_fields=[],
                        message=f"추천 결과는 {shown_place_count}개까지 있어요. "
                        "몇 번을 비교할까요?",
                    ),
                )
                return provider_result(result, source=ProviderSource.FAKE_LLM)
            targets = mentioned_indices

        result = LLMOutput(
            intent=Intent.COMPARE,
            status=OutputStatus.COMPLETE,
            compare=ComparePayload(targets=targets, criteria=criteria),
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

    async def extract_general_request(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        if any(marker in user_input for marker in _SERVICE_IDENTITY_MARKERS):
            topic = GeneralTopic.SERVICE_IDENTITY
        elif "역사" in user_input or "언제 지어졌" in user_input:
            topic = GeneralTopic.PLACE_KNOWLEDGE
        elif "언제 피어" in user_input:
            topic = GeneralTopic.SEASON_INFO
        elif "동네" in user_input:
            topic = GeneralTopic.AREA_INFO
        elif "에티켓" in user_input or "음식" in user_input:
            topic = GeneralTopic.FOOD_CULTURE
        elif "막차" in user_input or "지하철" in user_input:
            topic = GeneralTopic.TRANSPORT_INFO
        elif "여행 팁" in user_input:
            topic = GeneralTopic.TRAVEL_TIP
        else:
            topic = GeneralTopic.PLANNING_TIP

        result = LLMOutput(
            intent=Intent.GENERAL,
            status=OutputStatus.COMPLETE,
            general=GeneralPayload(topic=topic, original_question=user_input),
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

    async def generate_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        if topic is GeneralTopic.SERVICE_IDENTITY:
            answer = (
                "저는 TripBranch의 국내 여행 챗봇 트리비예요. "
                "원하는 지역이나 현재 위치를 기준으로 날씨, 운영시간, 거리, "
                "혼잡도 선호를 함께 보고 갈 만한 곳을 추천해드릴 수 있어요."
            )
        else:
            answer = "국내 여행에 참고할 만한 정보를 간단히 알려드릴게요."
        if offer_content:
            # 실 프롬프트의 질문형 제안 문구를 그대로 흉내내지 않고, 테스트가
            # offer_content 전달 여부만 확인할 수 있게 문자열로 남긴다.
            answer = f"{answer} {offer_content}을(를) 찾아드릴까요?"
        return provider_result(answer, source=ProviderSource.FAKE_LLM)

    async def generate_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        shown = [*recommendations.recommendations, *recommendations.unverified_recommendations]
        if not shown:
            return provider_result(
                "조건에 맞는 곳을 찾지 못했어요.", source=ProviderSource.FAKE_LLM
            )
        first = shown[0]
        return provider_result(
            f"{first.name}을(를) 중심으로 지금 가볼 만한 곳을 골라봤어요.",
            source=ProviderSource.FAKE_LLM,
        )

    async def judge_travel_modes(
        self,
        segments: Sequence[SegmentModeInput],
        context: ModeJudgmentContext,
    ) -> ProviderResult[tuple[str, ...]]:
        """이동수단 판정의 테스트용 결정적 대체 구현. (TP-227)

        **호출부가 실제로 읽는 것을 채운다.** 전부 도보로 돌려주면 소비 측
        (`select_modes_for_segments()`)의 검증과 표 조립이 돌긴 해도 "판정이 규칙과
        다른 답을 냈을 때"를 한 번도 안 지난다. 그래서 조건을 실제로 보고 가른다 —
        비가 오거나 동행·무장애 요구가 있으면 먼 구간을 대중교통으로 바꾼다.

        Fake가 조건을 안 읽으면 조건을 나르는 배선이 끊겨도 테스트가 통과한다.
        """

        has_reason = bool(
            context.companion
            or context.accessibility_needs
            or (context.weather is not None and context.weather.precipitation
                not in (None, "none"))
        )
        threshold = 10.0 if has_reason else 20.0
        modes = tuple(
            "transit" if segment.walk_minutes > threshold else "walking"
            for segment in segments
        )
        return provider_result(modes, source=ProviderSource.FAKE_LLM)

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
        """후속 질문 제안의 테스트용 결정적 대체 구현.

        **호출부가 실제로 읽는 것을 채운다.** 빈 목록을 돌려주면 소비 측
        (`follow_up_suggester.py`)의 정제·상한 로직이 한 줄도 안 돌면서 테스트는
        통과한다. 그래서 여기서는 이번 턴에 나간 장소 이름을 실제로 써서 문구를
        만들고, 상한을 넘는 개수를 일부러 반환한다 — 호출부가 자르는지 확인된다.

        **already_suggested는 여기서 거르지 않고 일부러 되돌려 준다.** 같은 이유다 —
        Fake가 미리 걸러 주면 호출부의 중복 제거가 한 줄도 안 돌면서 테스트는 통과한다.
        중복을 실제로 없애는 책임은 호출부에 있고, 그게 도는지 확인되어야 한다.
        """

        del assistant_message, max_label_length
        # 혼잡도 문구에는 장소명을 반드시 넣는다 — 소비 측이 그 유무로 걸러낸다.
        subject = place_names[0] if place_names else search_place
        if subject and "혼잡" in user_input:
            return provider_result(
                [f"주말에 {subject} 많이 혼잡해?"], source=ProviderSource.FAKE_LLM
            )
        # 이동수단이 차면 주차 질문을 섞는다 — 소비 측이 실제로 읽는 조건이다.
        if transport == "car" and place_names:
            return provider_result(
                [f"{place_names[0]} 근처에 주차할 데 있는지 알려줘"],
                source=ProviderSource.FAKE_LLM,
            )
        if intent in (Intent.OUT_OF_SCOPE, Intent.GENERAL) and not place_names:
            return provider_result(
                ["서울에서 갈 만한 곳 추천해줘"], source=ProviderSource.FAKE_LLM
            )
        suggestions = [f"{name} 운영시간 알려줘" for name in place_names[:max_suggestions]]
        suggestions.append("다른 곳도 보여줘")
        suggestions.append("이 장소들로 일정 짜줘")
        # 이미 보여준 문구를 맨 앞에 되돌려 준다(위 docstring 참고).
        return provider_result(
            [*already_suggested[-1:], *suggestions], source=ProviderSource.FAKE_LLM
        )

    async def generate_place_reason(
        self,
        *,
        place_name: str,
        category_label: str | None,
        insights: Sequence[PlacePreferenceInsight],
        matched_preference_codes: Sequence[str] = (),
    ) -> ProviderResult[str]:
        """결정적 한 문장. 상위 태그 라벨을 그대로 이어 붙인다.

        **문장을 그럴듯하게 만들지 않는다.** Fake가 실제 LLM처럼 읽히는 문장을
        내면 프롬프트가 깨진 것을 화면에서 알아챌 수 없다(D-042와 같은 성격) —
        태그가 실제로 넘어왔는지만 눈으로 확인할 수 있게 나열한다.
        """

        # 실제 구현과 같은 순서로 고른다 — 사용자 취향과 맞은 태그가 먼저다.
        # Fake로 띄운 화면에서도 "취향이 앞에 오는지"를 눈으로 볼 수 있어야 한다.
        matched = {code.strip() for code in matched_preference_codes if code.strip()}
        ordered = sorted(insights, key=lambda insight: insight.code not in matched)
        labels = [insight.label for insight in ordered[:3] if insight.label]
        if not labels:
            return provider_result("", source=ProviderSource.FAKE_LLM)
        return provider_result(
            f"후기에서 {' · '.join(labels)} 점이 자주 언급돼요.",
            source=ProviderSource.FAKE_LLM,
        )

    async def stream_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """SSE 테스트용: 결정적 요약을 두 조각으로 나눈다."""

        summary = await self.generate_recommendation_summary(
            intent, recommendations, conditions=conditions, history=history
        )
        text = summary.data
        midpoint = max(1, len(text) // 2)
        yield text[:midpoint]
        yield text[midpoint:]

    async def stream_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """SSE 테스트용 GENERAL 답변을 결정적으로 두 조각으로 나눈다."""

        answer = await self.generate_general_answer(
            topic, original_question, offer_content=offer_content, history=history
        )
        text = answer.data
        midpoint = max(1, len(text) // 2)
        yield text[:midpoint]
        yield text[midpoint:]

    async def stream_info_answer(
        self,
        *,
        place_name: str,
        question_type: str,
        specific_question: str | None,
        fields: dict[str, str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """SSE 테스트용 INFO 답변. 전달된 C fields 밖의 사실은 만들지 않는다."""

        del specific_question
        value = next(iter(fields.values()), "정보")
        text = (
            f"{place_name}의 {question_type} 정보를 확인했어요. "
            f"{value} 자세한 내용은 아래 상세 카드에서 확인해보세요."
        )
        midpoint = max(1, len(text) // 2)
        yield text[:midpoint]
        yield text[midpoint:]

    async def filter_review_evidence(
        self,
        *,
        place_name: str,
        specific_question: str,
        snippets: Sequence[str],
    ) -> ProviderResult[tuple[int, ...]]:
        """테스트용 근거 선별. 장소 이름이 들어 있는 문장만 남긴다.

        실제 판정은 문장의 주어를 읽어야 하지만(Fake가 흉내 낼 수 없다), "무엇을
        골랐는지에 따라 답변과 출처가 달라진다"는 계약은 이걸로도 확인된다.
        장소 이름이 어디에도 없으면 앞의 두 건을 남겨 빈손 경로와 구분한다.
        """
        del specific_question
        kept = tuple(
            index
            for index, text in enumerate(snippets, start=1)
            if place_name in text
        )
        return provider_result(
            kept or tuple(range(1, min(len(snippets), 2) + 1)),
            source=ProviderSource.STUB,
        )

    async def stream_review_answer(
        self,
        *,
        place_name: str,
        specific_question: str | None,
        evidence: Sequence[str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """테스트용 후기 답변. 넘겨받은 근거 밖의 사실은 만들지 않는다."""

        del specific_question, history
        first = evidence[0] if evidence else ""
        text = f"{place_name}은(는) 후기에서 이런 이야기가 있어요. {first}"
        midpoint = max(1, len(text) // 2)
        yield text[:midpoint]
        yield text[midpoint:]

    async def generate_compare_summary(
        self,
        comparison: ComparisonResult,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        """COMPARE LLM 요약의 테스트용 결정적 대체 구현.

        실제 Gemini와 달리 문체 다양화는 하지 않되, 3줄 이상이라는 출력 계약과
        전달된 사실만 쓴다는 원칙을 회귀 테스트에서 확인할 수 있게 한다.
        """

        items = comparison.items
        if comparison.criteria is CompareCriteria.TIME:
            candidates = [item for item in items if item.remaining_minutes is not None]
            recommended = (
                max(candidates, key=lambda item: item.remaining_minutes or 0)
                if candidates
                else items[0]
            )
        elif comparison.criteria is CompareCriteria.TRAVEL_TIME:
            candidates = [item for item in items if _fastest_travel_minutes(item) is not None]
            recommended = (
                min(candidates, key=lambda item: _fastest_travel_minutes(item) or 0)
                if candidates
                else items[0]
            )
        else:
            recommended = items[0]
        lines = [f"{recommended.place_name}{_object_particle(recommended.place_name)} 추천드려요."]
        for item in items[:3]:
            details: list[str] = []
            mode_parts = [
                f"{label} 약 {minutes}분"
                for label, field in _TRAVEL_MODE_FIELDS
                if (minutes := getattr(item, field)) is not None
            ]
            if mode_parts:
                if item.travel_distance_km is not None:
                    details.append(f"약 {item.travel_distance_km}km")
                details.extend(mode_parts)
            elif item.distance_km is not None:
                minutes = max(1, math.ceil(item.distance_km * 60 / 3.6))
                details.append(f"도보 약 {minutes}분")
            if item.remaining_minutes is not None:
                hours = max(1, math.floor(item.remaining_minutes / 60 + 0.5))
                details.append(f"약 {hours}시간 남음")
            if item.environment_type is not None:
                details.append(f"{item.environment_type} 환경")
            value = ", ".join(details) if details else "비교 정보 확인 필요"
            lines.append(f"{item.rank}번 {item.place_name}은 {value}이에요.")
        while len(lines) < 3:
            lines.append("제공된 비교 정보를 바탕으로 선택해보세요.")
        return provider_result("\n".join(lines[:6]), source=ProviderSource.FAKE_LLM)

    async def generate_schedule_plan(
        self, request: SchedulePlanningRequest
    ) -> ProviderResult[ScheduleLLMPlan]:
        """실제 Gemini 호출 없이 candidates 앞쪽 최대 3개를 순서대로 배치한
        고정 일정을 반환한다 — 회귀 테스트용, 실제 편성 판단이 아니다."""
        selected = request.candidates[:3]
        items = [
            ScheduleLLMItem(
                order=index + 1,
                place_id=candidate.place_id,
                place_name=candidate.name,
                estimated_duration_min=60,
                reason="Agent Runtime 골격 검증용 고정 일정입니다.",
            )
            for index, candidate in enumerate(selected)
        ]
        result = ScheduleLLMPlan(
            items=items,
            route_summary="고정 스텁 동선입니다.",
        )
        return provider_result(result, source=ProviderSource.FAKE_LLM)

    async def generate_schedule_fill(
        self, request: SchedulePartialFillRequest
    ) -> ProviderResult[SchedulePartialLLMPlan]:
        """실제 Gemini 호출 없이 candidates 앞쪽에서 필요한 개수만큼 순서대로
        target_orders에 배정한다 — SCHEDULE-09 2단계 회귀 테스트용, 실제
        편성 판단이 아니다.

        candidates가 target_orders보다 적으면(strict=False) new_items 개수가
        모자란 채로 반환된다 — 의도적이다. planner.py의 사후 검증(order 집합
        일치 확인)이 이 불일치를 잡아내는 경로를 테스트할 수 있게 한다.
        """
        orders = sorted(request.target_orders)
        selected = request.candidates[: len(orders)]
        new_items = [
            ScheduleLLMItem(
                order=order,
                place_id=candidate.place_id,
                place_name=candidate.name,
                estimated_duration_min=60,
                reason="Agent Runtime 골격 검증용 고정 대체 항목입니다.",
            )
            for order, candidate in zip(orders, selected, strict=False)
        ]
        result = SchedulePartialLLMPlan(new_items=new_items)
        return provider_result(result, source=ProviderSource.FAKE_LLM)


def _object_particle(value: str) -> str:
    """Fake 응답도 실제 화면처럼 자연스러운 목적격 조사를 쓴다."""

    last = value[-1] if value else ""
    is_hangul = "가" <= last <= "힣"
    return "을" if is_hangul and (ord(last) - ord("가")) % 28 else "를"


# SKY(하늘상태) 4 흐림, PTY(강수형태) 0 강수 없음 — 판정을 어느 쪽으로도 밀지 않는
# 중립 조합이다. fake도 실제 provider와 같은 "사실"을 내려줘야 한다: D는 사실
# 3종(precipitation/sky/temperature)으로 판정하므로(D-051) 코드를 비워두면 D
# 입력이 전부 None이 되어 어떤 날씨 시나리오도 재현되지 않는다.
_FAKE_DEFAULT_SKY_CODE = "4"
_FAKE_DEFAULT_PRECIPITATION_TYPE = "0"

# 폭염(33°C)·한파(-12°C) 경계에서 먼 값을 기본으로 둔다 — 기본 기온이 판정을
# 흔들면 sky·pty 인자의 의미가 흐려진다. 폭염·한파 시나리오는 생성자에
# temperature_celsius를 직접 넘겨서 만든다.
_FAKE_DEFAULT_TEMPERATURE_CELSIUS = 22.0


class FakeWeatherProvider:
    """설정한 공통 날씨 사실을 반환하는 가짜 구현.

    D-051 이후 Provider는 판정을 하지 않으므로 fake도 사실만 받는다. 기상청 코드를
    그대로 받는 이유는 실제 provider와 같은 모양이어야 D 판정 경로가 실제로
    실행되기 때문이다 — 맑음은 `("1", "0")`, 비는 `("4", "1")`.
    """

    def __init__(
        self,
        sky_code: str | None = _FAKE_DEFAULT_SKY_CODE,
        precipitation_type: str | None = _FAKE_DEFAULT_PRECIPITATION_TYPE,
        temperature_celsius: float | None = _FAKE_DEFAULT_TEMPERATURE_CELSIUS,
    ) -> None:
        self._sky_code = sky_code
        self._precipitation_type = precipitation_type
        self._temperature_celsius = temperature_celsius

    async def get_forecast_slots(
        self, latitude: float, longitude: float
    ) -> ProviderResult[WeatherForecastResult]:
        now = datetime.now(ZoneInfo("Asia/Seoul")).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        return provider_result(
            WeatherForecastResult(
                latitude=latitude,
                longitude=longitude,
                grid_x=60,
                grid_y=127,
                slots=tuple(
                    WeatherForecastSlot(
                        forecast_for=now + timedelta(hours=offset),
                        sky_code=self._sky_code,
                        precipitation_type=self._precipitation_type,
                        temperature_celsius=self._temperature_celsius,
                    )
                    for offset in range(6)
                ),
                provider="fake_weather",
            ),
            source=ProviderSource.FAKE_WEATHER,
        )


def _first_intro_text(intro: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    """실 provider의 _first_text와 같은 규칙으로 첫 값을 고른다."""
    for key in keys:
        value = intro.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _fake_intro(content_type_id: str) -> dict[str, object]:
    """detailIntro2 응답을 유형별 필드명까지 흉내 낸다.

    이 값을 비워두면 INFO 상세 질의(요금·주차·편의시설)의 필드 추출이 한 줄도
    실행되지 않은 채 테스트가 통과한다 — "값이 없다"와 "로직이 안 돌았다"를 구분할
    수 없어진다. 소비 측은 이제 raw_intro가 아니라 정규화 필드를 읽지만(D-060),
    get_details()가 그 필드를 여기서 뽑아 채우므로 이 dict가 비면 결과는 같다.

    **실 Provider와 같은 키 이름을 쓰는 것이 핵심이다**(문화시설 14는 usefee/
    parkingculture, 음식점 39는 parkingfood). 키 선택도 tour_intro_keys의 같은
    목록으로 하므로 fake가 실 응답과 어긋나면 테스트에서 드러난다.
    """

    if content_type_id == "39":
        return {
            "parkingfood": "가능(10대)",
            "chkcreditcardfood": "가능",
            "opentimefood": "08:00-22:00",
        }
    return {
        "usefee": "어른 3,000원 / 어린이 1,500원",
        "parkingculture": "주차 가능(무료)",
        "chkbabycarriageculture": "가능",
        "chkpetculture": "불가",
        "chkcreditcardculture": "가능",
    }


@dataclass(frozen=True)
class _FakeBarrierFreePlace:
    """Fake 무장애 후보 한 건. 어느 편의를 갖추었는지를 값으로 들고 있다."""

    place_id: str
    name: str
    content_type_id: str
    lcls_systm1: str | None
    category: str
    lat_offset: float
    lng_offset: float
    needs: frozenset[AccessibilityNeed]
    # 판정표가 있는 어휘(휠체어·유모차·시각안내)의 판정. 비워 두면 실 경로가
    # 올리는 값을 Fake는 한 번도 만들지 않아, 안내 문구가 붙는지 확인할 길이 없다.
    # 여기 없는 어휘는 실 경로에서도 판정을 올리지 않는다.
    verdicts: dict[AccessibilityNeed, AccessibilityVerdict] = field(
        default_factory=dict
    )


# **이 목록은 판정을 실제로 움직여야 한다.** 모든 장소가 모든 편의를 갖추게 두면
# 필터가 한 번도 걸리지 않고, 테스트는 통과하는데 검증하려던 로직은 실행되지 않는다.
# 그래서 편의 조합을 일부러 어긋나게 둔다.
#
#   무장애 카페      단차(휠체어·유모차) + 유아 시설 → 유모차 요청(둘 다)에 남는다
#   유아쉼터         유아 시설만                     → 유모차 요청에서 **빠진다**
#   경로당           의자식 테이블 + 저상버스 + 휠체어 대여 (단차 정보 없음)
#                    → 노인 동반 조건에는 남고 휠체어 요청에서는 **빠진다**
#   무장애 박물관    단차 + 화장실 + 시각 안내
#   게스트하우스     전부 갖췄지만 숙박(32)이라 분류 규칙이 버린다
_FAKE_BARRIER_FREE_PLACES: tuple[_FakeBarrierFreePlace, ...] = (
    _FakeBarrierFreePlace(
        place_id="fake-bf-cafe-1",
        name="테스트 무장애 카페",
        content_type_id="39",
        lcls_systm1="FD",
        category="restaurant",
        lat_offset=0.001,
        lng_offset=0.0,
        needs=frozenset(
            {
                AccessibilityNeed.WHEELCHAIR_ACCESS,
                AccessibilityNeed.STROLLER_ACCESS,
                AccessibilityNeed.INFANT_FACILITIES,
            }
        ),
        # **판정이 어휘마다 갈리는 유일한 Fake다.** 좁은 통로가 휠체어만 막고
        # 유모차는 지나가는 실제 문장을 본뜬 것이라, 같은 장소가 요구 어휘에 따라
        # 다른 안내를 받는지 여기서 확인한다.
        verdicts={
            AccessibilityNeed.WHEELCHAIR_ACCESS: AccessibilityVerdict.PARTIAL,
            AccessibilityNeed.STROLLER_ACCESS: AccessibilityVerdict.POSSIBLE,
        },
    ),
    _FakeBarrierFreePlace(
        place_id="fake-bf-nursery-1",
        name="테스트 유아쉼터",
        content_type_id="12",
        lcls_systm1="NA",
        category="attraction",
        lat_offset=0.002,
        lng_offset=0.0,
        # 단차 정보가 없다. 유모차를 끌고 갈 수 있는지는 이 장소에서 알 수 없다.
        needs=frozenset({AccessibilityNeed.INFANT_FACILITIES}),
    ),
    _FakeBarrierFreePlace(
        place_id="fake-bf-senior-1",
        name="테스트 경로당",
        content_type_id="14",
        lcls_systm1="VE",
        category="cultural_facility",
        lat_offset=0.0025,
        lng_offset=0.0,
        # 오래 걷기 힘든 동행에게 쓸모 있는 값만 있고 단차 정보는 없다.
        needs=frozenset(
            {
                AccessibilityNeed.SEATING_AVAILABLE,
                AccessibilityNeed.LOW_FLOOR_TRANSIT,
                AccessibilityNeed.WHEELCHAIR_RENTAL,
            }
        ),
    ),
    _FakeBarrierFreePlace(
        place_id="fake-bf-museum-1",
        name="테스트 무장애 박물관",
        content_type_id="14",
        lcls_systm1="VE",
        category="cultural_facility",
        lat_offset=0.003,
        lng_offset=0.0,
        needs=frozenset(
            {
                AccessibilityNeed.WHEELCHAIR_ACCESS,
                AccessibilityNeed.STROLLER_ACCESS,
                AccessibilityNeed.ACCESSIBLE_RESTROOM,
                AccessibilityNeed.VISUAL_GUIDE,
            }
        ),
        # 시각 안내만 부분이다. 점자블록이 일부 구역에만 있는 원문을 본뜬 것으로,
        # 단차는 문제없는데 안내 시설만 모자란 경우가 실제로 있다.
        verdicts={
            AccessibilityNeed.WHEELCHAIR_ACCESS: AccessibilityVerdict.POSSIBLE,
            AccessibilityNeed.STROLLER_ACCESS: AccessibilityVerdict.POSSIBLE,
            AccessibilityNeed.VISUAL_GUIDE: AccessibilityVerdict.PARTIAL,
        },
    ),
    _FakeBarrierFreePlace(
        place_id="fake-bf-lodging-1",
        name="테스트 무장애 게스트하우스",
        content_type_id="32",
        lcls_systm1="AC",
        category="lodging",
        lat_offset=0.004,
        lng_offset=0.0,
        # 편의는 다 갖췄지만 숙박이라 추천 대상이 아니다. 분류 규칙이 버려야 한다.
        needs=frozenset(AccessibilityNeed),
    ),
)


class FakeBarrierFreePlaceSearchProvider:
    """무장애 후보 검색을 고정 목록으로 대체하는 fake provider.

    실 provider와 같은 규칙을 적용한다 — 요구 편의를 **전부** 만족해야 하고,
    거리순으로 정렬하며, 추천 대상이 아닌 유형은 버린다. 규칙이 다르면 Fake로
    확인한 동작이 실 경로에서 달라진다.
    """

    async def search_places_with_accessibility(
        self,
        *,
        latitude: float,
        longitude: float,
        search_radius_km: float,
        needs: Sequence[AccessibilityNeed],
        category_filter: PlaceCategoryFilter | None = None,
        limit: int,
    ) -> ProviderResult[BarrierFreePlaceSearch]:
        required = frozenset(needs)
        if not required:
            raise ValueError(
                "needs가 비어 있습니다. 무장애 조건이 없으면 이 provider를 부르지 않습니다."
            )

        verdicts: dict[str, dict[AccessibilityNeed, AccessibilityVerdict]] = {}
        selected: list[PlaceCandidate] = []
        for place in _FAKE_BARRIER_FREE_PLACES:
            if not required.issubset(place.needs):
                continue
            if resolve_place_category(place.content_type_id) is None:
                continue
            if category_filter and category_filter.content_type_id:
                if place.content_type_id != category_filter.content_type_id:
                    continue
            if category_filter and category_filter.lcls_systm1:
                if place.lcls_systm1 != category_filter.lcls_systm1:
                    continue
            selected.append(
                PlaceCandidate(
                    place_id=place.place_id,
                    content_type_id=place.content_type_id,
                    lcls_systm1=place.lcls_systm1,
                    lcls_systm2=None,
                    lcls_systm3=None,
                    name=place.name,
                    category=place.category,
                    latitude=latitude + place.lat_offset,
                    longitude=longitude + place.lng_offset,
                    address="서울 종로구 어딘가",
                    # 실 provider와 같이 비워 둔다. 운영시간은 상세 보완이 채운다.
                    operating_hours=None,
                    raw_source="fake_barrier_free",
                )
            )
            # 실 provider와 같이 **요구한 어휘만** 올린다. 전부 올리면 사용자가
            # 묻지 않은 편의까지 답변이 말하게 된다.
            requested = {
                need: verdict
                for need, verdict in place.verdicts.items()
                if need in required
            }
            if requested:
                verdicts[place.place_id] = requested

        # 목록이 이미 거리순이지만 정렬을 생략하지 않는다. 항목을 더할 때 순서를
        # 지키지 않아도 동작이 같아야 한다.
        selected.sort(
            key=lambda candidate: (candidate.latitude - latitude) ** 2
            + (candidate.longitude - longitude) ** 2
        )
        selected = selected[: max(1, limit)]
        kept = {candidate.place_id for candidate in selected}
        return provider_result(
            BarrierFreePlaceSearch(
                candidates=selected,
                verdicts={
                    place_id: verdict
                    for place_id, verdict in verdicts.items()
                    if place_id in kept
                },
            ),
            source=ProviderSource.FAKE_BARRIER_FREE_PLACES,
            status=ProviderStatus.SUCCESS if selected else ProviderStatus.NO_DATA,
        )


# Fake 구가 담는 분류별 장소 수. 강남구 실측 구성을 줄여서 흉내 낸 것이다
# (관광지 39·문화시설 69·음식점 260·쇼핑 713·레포츠 6·축제 13).
#
# **비율을 살리는 것이 이 Fake의 전부다.** 쇼핑이 압도적으로 많고 레포츠·축제가
# 한 자릿수라는 그 모양이 선택 로직이 실제로 하는 일을 결정한다. 분류를 고르게
# 채우거나 좌표를 한 점에 몰아 두면 몫·격자·소진율이 한 줄도 작동하지 않는데
# 테스트는 통과한다 — 이 저장소에서 반복된 실패다(D-042 계열).
_FAKE_DISTRICT_COMPOSITION: tuple[tuple[str, int], ...] = (
    ("12", 12),  # 관광지
    ("14", 9),  # 문화시설
    ("39", 40),  # 음식점
    ("38", 90),  # 쇼핑
    ("28", 3),  # 레포츠
    ("15", 4),  # 축제공연
)

# Fake 구가 차지하는 좌표 범위. 종로구 언저리에 실제 구만 한 크기로 편다.
_FAKE_DISTRICT_ORIGIN = (37.56, 126.96)
_FAKE_DISTRICT_SPAN = 0.04


class FakeDistrictPlaceSearchProvider:
    """구 단위 후보 조회를 만들어 낸 목록으로 대체하는 fake provider.

    실 provider와 같은 규칙을 적용한다 — 추천 대상이 아닌 유형은 버리고, 개수를
    자르지 않고 구 전량을 올린다. 자르는 일은 선택 단계가 한다.

    좌표는 한 점에 몰지 않고 격자 전체에 편다. 몰아 두면 격자 분산이 아무 일도
    하지 않게 되어, Fake로 확인한 동작이 실 경로와 달라진다.
    """

    async def search_places_in_district(
        self, *, district_code: str
    ) -> ProviderResult[list[PlaceCandidate]]:
        candidates: list[PlaceCandidate] = []
        index = 0
        base_latitude, base_longitude = _FAKE_DISTRICT_ORIGIN
        for content_type_id, count in _FAKE_DISTRICT_COMPOSITION:
            category = resolve_place_category(content_type_id)
            if category is None:
                continue
            for _ in range(count):
                # 4x4 격자를 골고루 밟도록 두 축을 서로 다른 주기로 돌린다.
                latitude = base_latitude + (index % 4) * (_FAKE_DISTRICT_SPAN / 4)
                longitude = base_longitude + ((index // 4) % 4) * (_FAKE_DISTRICT_SPAN / 4)
                candidates.append(
                    PlaceCandidate(
                        place_id=f"fake-{district_code}-{index:04d}",
                        content_type_id=content_type_id,
                        lcls_systm1=None,
                        lcls_systm2=None,
                        lcls_systm3=None,
                        name=f"테스트 {category} {index}",
                        category=category,
                        latitude=latitude,
                        longitude=longitude,
                        address=f"서울특별시 어느구 {index}",
                        # 실 provider와 같이 비워 둔다. 운영시간은 상세 보완이 채운다.
                        operating_hours=None,
                        raw_source="fake_district",
                    )
                )
                index += 1
        return provider_result(
            candidates,
            source=ProviderSource.FAKE_PLACES,
            status=ProviderStatus.SUCCESS if candidates else ProviderStatus.NO_DATA,
        )


class FakePlaceProvider:
    """장소 검색 결과를 고정 후보 목록으로 대체하는 fake provider."""

    # 후보 category는 실 Provider와 같은 PlaceType 어휘를 쓴다. Fake로 검증한 동작이
    # 실 경로에서 달라지지 않게 하려는 것이다. 호출자는 place_types(영문 PlaceType)와
    # place_tags(한글)를 함께 넘기므로 양쪽을 모두 받는다.
    _CATEGORY_ALIASES = {
        "cultural_facility": frozenset({"cultural_facility"}),
        "restaurant": frozenset({"restaurant"}),
        "박물관": frozenset({"cultural_facility"}),
        "카페": frozenset({"restaurant"}),
    }

    async def search_places(
        self,
        latitude: float,
        longitude: float,
        preferred_categories: list[str],
        search_radius_km: float,
        region_code: str | None = None,
        district_code: str | None = None,
        category_filter: PlaceCategoryFilter | None = None,
        limit: int = DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        candidates = [
            PlaceCandidate(
                place_id="fake-museum-1",
                content_type_id="14",
                lcls_systm1="VE",
                lcls_systm2="VE07",
                lcls_systm3="VE070100",
                name="테스트 박물관",
                category="cultural_facility",
                latitude=latitude,
                longitude=longitude,
                address="서울 종로구 어딘가",
                operating_hours="09:00-18:00",
                raw_source="fake_place",
            ),
            PlaceCandidate(
                place_id="fake-cafe-1",
                content_type_id="39",
                lcls_systm1="FD",
                lcls_systm2="FD05",
                lcls_systm3="FD050100",
                name="테스트 카페",
                category="restaurant",
                latitude=latitude + 0.001,
                longitude=longitude + 0.001,
                address="서울 종로구 어딘가",
                operating_hours="08:00-22:00",
                raw_source="fake_place",
            ),
        ]
        if category_filter and category_filter.content_type_id:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.content_type_id == category_filter.content_type_id
            ]
        elif preferred_categories:
            # 명시적인 TourAPI 분류 필터가 없을 때만 레거시 선호 카테고리를 적용한다.
            normalized_categories = {
                category.strip().casefold() for category in preferred_categories if category.strip()
            }
            accepted_categories = {
                candidate_category
                for category in normalized_categories
                for candidate_category in self._CATEGORY_ALIASES.get(category, ())
            }
            candidates = [
                candidate for candidate in candidates if candidate.category in accepted_categories
            ]
        selected = candidates[: max(1, min(limit, 100))]
        return provider_result(
            selected,
            source=ProviderSource.FAKE_PLACE,
            status=ProviderStatus.SUCCESS if selected else ProviderStatus.NO_DATA,
        )

    async def search_by_keyword(
        self,
        keyword: str,
        region_code: str | None = None,
        district_code: str | None = None,
        limit: int = DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        candidates = (await self.search_places(37.5796, 126.9770, [], 1.0)).data
        normalized = keyword.strip().lower()
        selected = [candidate for candidate in candidates if normalized in candidate.name.lower()][
            :limit
        ]
        return provider_result(
            selected,
            source=ProviderSource.FAKE_PLACE,
            status=ProviderStatus.SUCCESS if selected else ProviderStatus.NO_DATA,
        )

    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        candidates = (await self.search_places(37.5796, 126.9770, [], 1.0)).data
        candidate = next((item for item in candidates if item.place_id == content_id), None)
        intro = _fake_intro(content_type_id) if candidate else {}
        operating_hours = candidate.operating_hours if candidate else None
        rest_date = (
            "매주 월요일"
            if candidate and candidate.category == "cultural_facility"
            else "연중무휴"
            if candidate
            else None
        )
        return provider_result(
            PlaceDetails(
                content_id=content_id,
                content_type_id=content_type_id,
                title=candidate.name if candidate else None,
                address=candidate.address if candidate else None,
                overview="Fake Provider의 장소 상세정보입니다.",
                homepage="https://example.test/fake-place",
                telephone="02-000-0000",
                operating_hours=operating_hours,
                rest_date=rest_date,
                raw_common={},
                raw_intro=intro,
                provider="fake_place",
                operating_schedule=normalize_operating_schedule(
                    content_type_id=content_type_id,
                    operating_hours=operating_hours,
                    rest_date=rest_date,
                ),
                # 정규화 필드도 실 provider와 같은 키 목록으로 뽑는다. 손으로 값을
                # 적어 넣으면 fake가 raw_intro와 어긋나도 아무도 모른다.
                parking=_first_intro_text(intro, PARKING_KEYS),
                parking_fee=_first_intro_text(intro, PARKING_FEE_KEYS),
                fee=_first_intro_text(intro, USE_FEE_KEYS),
                baby_carriage=_first_intro_text(intro, BABY_CARRIAGE_KEYS),
                pet=_first_intro_text(intro, PET_KEYS),
                credit_card=_first_intro_text(intro, CREDIT_CARD_KEYS),
                restroom=_first_intro_text(intro, RESTROOM_KEYS),
                # 무장애 정보(D-077)도 채운다. 비워 두면 INFO facility 배선이
                # 끊어져도 fake로 도는 테스트는 전부 통과하고, 실제 운영에서만
                # 값이 비는 상태가 된다.
                approach_route_raw="출입구까지 턱이 없어 휠체어 접근 가능함",
                entrance_access_raw="주출입구는 경사로가 있어 휠체어 접근 가능함",
                elevator_raw="엘리베이터 있음",
                accessible_restroom_raw="장애인 화장실 있음",
                accessible_parking_raw="장애인 주차장 있음(2대)",
                braille_block_raw="점자블록 있음",
                braille_promotion_raw="점자 안내물 있음",
                audio_guide_raw="음성 안내 있음",
                guide_dog_raw="동반가능",
                # 이름과 달리 출입이 아니라 대여다 — fake도 그 뜻으로 채운다.
                wheelchair_rental_raw="대여가능(2대, 안내데스크)",
                stroller_rental_raw="대여가능",
                nursing_room_raw="수유실 있음",
                infant_family_etc_raw="기저귀교환대 있음",
                public_transport_raw="저상버스 운행",
                disability_etc_raw="장애인 안내 도우미 있음",
                thumbnail_url=(
                    f"https://example.test/{content_id}-thumb.jpg" if candidate else None
                ),
            ),
            source=ProviderSource.FAKE_PLACE,
        )

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        normalized_name = name.strip()
        candidates = (
            await self.search_by_keyword(normalized_name, region_code, district_code, limit=100)
        ).data
        exact = next(
            (
                candidate
                for candidate in candidates
                if candidate.name.strip().casefold() == normalized_name.casefold()
            ),
            None,
        )
        if exact is None or not exact.content_type_id:
            raise AppError(
                code="place_not_found",
                message=f"'{normalized_name}' 장소를 정확히 찾을 수 없어요.",
                status_code=404,
            )
        return await self.get_details(exact.place_id, exact.content_type_id)

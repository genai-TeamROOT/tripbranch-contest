"""D의 RecommendationItem과 LLMOutput을 사용자에게 보여줄 텍스트로 조립한다.

역할: 두 계층을 담당한다(docs/design/agent-response-generation.md 참고).
1) compose_recommendation_message(): 장소 카드 1건에 들어갈 문장. D가 만든
   explanations(근거)/warnings(경고)를 D님과 협의한 순서(근거 먼저, 경고는
   "다만~"으로 마지막)로 이어붙인다. 문장 내용 자체는 재작문하지 않고 D가 만든
   그대로 쓴다.
2) compose_chat_message(): 카드들을 감싸는 챗봇 말풍선 텍스트(AgentResponse.message).
   Intent/status별로 고정 템플릿을 고르거나 LLM으로 문장을 생성한다. RECOMMEND/MODIFY는
   검증된 후보·취향 리뷰 근거만 LLM에 넘겨 추천 후 선택 팁을 만든다. SSE에서는 카드를
   먼저 전송해 지연 없이 보여주고, 이 요약은 그 뒤에 스트리밍한다 — 다만 화면
   캡션·배치는 프론트 몫이라 이 요약이 실제로 카드 위/아래 어디에 보이는지는 여기
   책임이 아니다(2026-09-09부터 프론트가 카드 앞자리에 배치한다).
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from app.agent_context.seoul_realtime_areas import names_match
from app.agent_context.service import BUSY_CONGESTION_LEVELS, CONGESTION_LEVEL_ORDER
from app.domain.travel_route import TravelRoute
from app.errors import AppError
from app.observability.langfuse_tracing import trace_attributes
from app.providers.protocols import LLMProvider
from app.schedule.budget import SCHEDULE_CLUSTER_WALK_MINUTES, classify_budget
from app.schemas import (
    CompareCriteria,
    ComparisonItem,
    ComparisonResult,
    ConversationTurnView,
    GeneralTopic,
    Intent,
    LLMOutput,
    OutputStatus,
    RecommendationItem,
    RecommendationResponse,
    ScheduleBudgetStatus,
    ScheduleResult,
    UserConditions,
)
from app.service_area import supported_district_label
from app.services.interpret.situational_offers import offer_for
from app.services.runtime.context_schemas import Clarification
from app.services.runtime.info_context_schemas import (
    DistrictPopulationInfoResult,
    EventInfoResult,
    InfoContextResponse,
    PlaceInfoResult,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimePopulationInfoResult,
)
from app.services.runtime.info_display import format_citydata_timestamp, format_parking_for_display

# C 단계에서 Recommendation으로 못 넘어가는 status. agent_runtime.py의
# _TOOL_TERMINAL_STATUSES와 같은 집합이어야 한다 — 순환 import를 피하려고 별도로
# 둔다. 두 집합이 어긋나면 메시지가 엉뚱한 분기로 새므로 테스트로 일치를 고정한다.
_TOOL_TERMINAL_STATUSES = frozenset(
    {"needs_clarification", "no_data", "unsupported", "unavailable"}
)

_RECOMMEND_WRAPPER_MESSAGE = "이런 곳들을 찾아봤어요:"

# 서비스 정체성 질문은 LLM이 매번 가능한 기능을 새로 나열하게 두지 않는다. 안내에
# 없는 기능을 약속하면 곧바로 OUT_OF_SCOPE로 이어지므로, 실제 연결된 기능·질문만
# 고정 카탈로그로 보여준다. GENERAL SERVICE_IDENTITY는 "넌 누구야?"도 포함하므로
# 짧은 소개와 기능 안내를 한 답변에 함께 둔다.
_SERVICE_IDENTITY_MESSAGE = """# TripBranch 여행 도우미

국내 여행에서 갈 만한 장소를 찾고, 방문에 필요한 정보를 확인해드려요.

## 장소 추천·조건 변경
- 비를 피할 실내 카페 추천해줘
- 조금 더 조용한 곳으로 바꿔줘
- 박물관은 빼줘

## 장소 비교·일정
- 1번이랑 3번 중 어디가 더 가까워?
- 추천한 곳으로 오후 일정 짜줘

## 장소 상세정보
- 경복궁 운영시간과 휴무일 알려줘
- 경복궁 주차나 입장료 있어?
- 휠체어로 이용하기 괜찮아?

## 혼잡도
- 경복궁 지금 사람 많아?
- 경복궁 주말에 붐빌까?

## 실시간 상권
- 용리단길 식당 지금 사람 많아?
- 성수 카페거리 카페 지금 사람 많아?

## 실시간 주차
- 경복궁 근처 주차할 곳 있어?
- 종로구 공영주차장 자리 있어?

## 실시간 행사·교통
- 오늘 여의도 근처 행사 있어?
- 강남역 지하철 언제 와?
- 경복궁 가는 길 막혀?

실시간 정보는 서울시 제공 지역과 데이터 제공 범위 안에서 확인할 수 있어요."""

MessageDeltaCallback = Callable[[str], Awaitable[None]]


def recommendation_wrapper_message() -> str:
    """RECOMMEND/MODIFY 카드 앞에 즉시 표시할 고정 안내문."""

    return _RECOMMEND_WRAPPER_MESSAGE


async def compose_recommendation_summary(
    *,
    intent: Intent,
    recommendations: RecommendationResponse,
    llm: LLMProvider,
    conditions: UserConditions | None = None,
    history: Sequence[ConversationTurnView] = (),
    on_message_delta: MessageDeltaCallback | None = None,
) -> str | None:
    """추천 카드 아래에 붙일 LLM 선택 팁을 만든다.

    카드·순위 자체는 이미 D가 확정했으므로 **사실 근거**로는 후보별 검증된 카드 필드와
    제한된 리뷰 근거만 전달된다. 실패 시 ``None``을 돌려 호출자가 고정 카드 안내만
    유지하게 한다. 특히 SSE에서는 실패한 부가 요약 때문에 추천 카드 전체가 늦어지지 않는다.

    conditions/history는 사실이 아니라 **말투와 강조점**을 위한 값이다. 이 두 개가
    없던 동안, 동행을 friend로 정확히 뽑아 놓고도 말풍선이 "혼자서도 가기 좋고"라고
    답하는 일이 있었다(2026-08-31 실사용) — 이 단계가 사용자가 무엇을 말했는지 전혀
    몰랐기 때문이다. 누적 조건은 최근 5턴 창 밖으로 밀려나도 살아 있어 이력보다
    견고하다(강의교재 36강의 "오래된 중요 정보 압축 요약"에 해당).
    """

    if on_message_delta is not None:
        try:
            message = await _collect_message_stream(
                llm.stream_recommendation_summary(
                    intent, recommendations, conditions=conditions, history=history or None
                ),
                on_message_delta,
            )
            if message:
                return message
        except AppError:
            logger.warning("추천 요약 스트리밍 실패", exc_info=True)

    try:
        result = await llm.generate_recommendation_summary(
            intent, recommendations, conditions=conditions, history=history or None
        )
    except AppError:
        logger.warning("추천 요약 LLM 생성 실패", exc_info=True)
        return None
    if on_message_delta is not None:
        await on_message_delta(result.data)
    return result.data

# int-03-modify.md §11 "후보 부족 처리" 정책 그대로 재사용 — 시스템이 임의로 조건을
# 완화하지 않고, 사용자에게 선택지를 제시한다.
_NO_DATA_MESSAGE = (
    "조건에 맞는 곳을 찾지 못했어요. 검색 범위를 넓혀볼까요? "
    "다른 종류의 장소도 포함할까요? 운영시간을 확인할 수 없는 장소도 볼까요?"
)

# C 단계 needs_clarification의 code별 템플릿. A 초안 — 팀 공유 후 피드백으로 보완 예정
# (docs/design/agent-response-generation.md §5 결정사항 3).
_CLARIFICATION_TEMPLATES: dict[str, str] = {
    "location_required": "어디 근처에서 찾아드릴까요? 현재 위치나 원하시는 지역을 알려주세요.",
    "location_ambiguous": (
        "말씀하신 장소가 여러 곳으로 해석돼요. 아래 말씀하신 장소가 있으면 선택해주세요. "
        "원하시는 장소가 없다면 더 구체적으로 말씀해주셔야 트리비가 이해할 수 있어요!"
    ),
    "place_required": "어떤 장소에 대해 알고 싶으신가요?",
    # 행사 질의는 장소를 물은 발화가 아니라 되묻는 말이 달라야 한다(TP-237).
    # 뒷문장은 유형을 가리지 않는다는 고지다 — 축제를 물어도 공연·전시가 함께 나온다.
    "event_place_required": (
        "어느 지역의 행사를 찾아드릴까요? 축제·공연·전시를 모두 모아서 알려드려요."
    ),
    "place_ambiguous": "여러 장소 중 어느 곳을 말씀하시는 건가요?",
}
_CLARIFICATION_FALLBACK_MESSAGE = "조건을 조금 더 자세히 알려주시겠어요?"


def tool_clarification_message(code: str | None) -> str:
    """C 단계 되묻기 code를 사용자 문구로 바꾼다.

    agent_runtime이 되묻기에 버튼을 붙일 때(예: location_required) 같은 문구를
    재사용하려고 노출한다 — 이 모듈 안의 compose_chat_message()도 내부적으로
    같은 테이블을 쓴다.
    """
    return _CLARIFICATION_TEMPLATES.get(code or "", _CLARIFICATION_FALLBACK_MESSAGE)


_TOOL_UNSUPPORTED_MESSAGE = "죄송하지만 아직 지원하지 않는 요청이에요."

# unsupported는 이유가 여러 가지다. 지원 지역 밖인데 "아직 지원하지 않는 요청"이라고만
# 하면 무엇을 바꿔야 할지 알 수 없다(D-044).
#
# unsupported_region 본문에는 지원 구 목록을 넣지 않는다(D-085) — 구가 12개, 앞으로도
# 계속 늘어날 예정이라 문장에 그대로 이어붙이면 매번 길어진다. 목록은
# AgentResponse.message_footnote로 따로 빼서 화면이 작고 옅은 글씨로 보여준다
# (unsupported_region_footnote() 참고).
_TOOL_UNSUPPORTED_TEMPLATES: dict[str, str] = {
    "unsupported_region": (
        "이 위치는 지금 서비스 지역이 아니에요. 다른 위치를 말씀해 주세요."
    ),
    "realtime_commercial_unsupported_region": (
        "해당 장소 주변은 서울시 실시간 상권 데이터 제공 지역이 아니에요. "
        "현재는 서울시 주요 82개 지역의 카페 상권 현황만 확인할 수 있어요."
    ),
}


def _unsupported_message(error_code: str | None) -> str:
    return _TOOL_UNSUPPORTED_TEMPLATES.get(error_code or "", _TOOL_UNSUPPORTED_MESSAGE)


def unsupported_region_footnote(error_code: str | None) -> str | None:
    """unsupported_region 본문에서 뺀 지원 구 목록. AgentResponse.message_footnote로 간다.

    error_code가 "unsupported_region"이 아니면 None — 그 자리에 footnote를 안 쓴다.
    `supported_district_label()`을 그대로 읽으므로 구가 늘어도 이 함수는 손댈 필요가
    없다(D-085).
    """
    if error_code != "unsupported_region":
        return None
    return f"현재 서비스 지역: {supported_district_label(with_city=True)}"


_TOOL_UNAVAILABLE_MESSAGE = "일시적으로 요청을 처리하지 못했어요. 잠시 후 다시 시도해주세요."

_OUT_OF_SCOPE_TEMPLATES: dict[str, str] = {
    "harmful": "죄송하지만 그런 요청은 도와드릴 수 없어요.",
    "unrelated": (
        "저는 국내 여행지 추천을 도와드리는 챗봇이에요. 여행 관련 질문을 해주시면 도와드릴게요!"
    ),
    "role_request": "저는 TripBranch 여행 추천 챗봇으로만 동작해요. 다른 역할은 수행할 수 없어요.",
    "prompt_injection": "죄송하지만 그 요청은 처리할 수 없어요.",
}

# INFO/COMPARE: 답변할 실제 데이터·로직 자체가 아직 없다(별도 트랙, agent-response-
# generation.md §3/§6 3차) — 임시 안내문. question_type=concentration은 예외
# (아래 compose_info_concentration_message).
_NOT_YET_SUPPORTED_MESSAGE = "죄송해요, 이 기능은 아직 준비 중이에요."
_SCHEDULE_NOT_YET_SUPPORTED_MESSAGE = "일정 추천 기능은 아직 준비 중이에요."

# concentration-conditions.md §7 데이터 한계와 응답 원칙.
_CONCENTRATION_NO_DATA_MESSAGE = "이 장소 유형은 혼잡도 데이터가 없어요."

# INFO question_type(concentration 제외 7종) 한글 라벨 — PlaceInfoResult.fields의
# 키와 no_data 문구 조립에 함께 쓴다(backend/docs/package-a/
# info-question-types-handoff.md). C가 fields에 값이 있는 키만 채워 보내므로,
# 이 라벨 맵은 "그 키가 있으면 이렇게 부른다"는 표시 규칙일 뿐이다.
_INFO_FIELD_LABELS: dict[str, str] = {
    "operating_hours": "운영시간",
    "rest_date": "휴무일",
    "fee": "요금",
    "parking": "주차",
    "parking_fee": "주차 요금",
    "baby_carriage": "유모차 대여",
    "pet": "반려동물 동반",
    "credit_card": "카드 결제",
    "restroom": "화장실",
    "address": "주소",
    "general_info": "장소 개요",
    "location_info": "위치",
    "overview": "개요",
    "homepage": "홈페이지",
}
_INFO_QUESTION_TYPE_LABELS: dict[str, str] = {
    "operating_hours": "운영시간",
    "fee": "요금",
    "parking": "주차",
    "facility": "편의시설",
    "location_info": "위치",
    "general_info": "개요",
}
# facility의 하위 필드 4개는 라벨이 고정이라 은/는을 미리 붙여둔다(값의 받침에
# 좌우되지 않게 "라벨+조사"까지 고정하고, 뒤에 값만 이어붙인다).
_FACILITY_FIELD_PHRASES: dict[str, str] = {
    "baby_carriage": "유모차 대여는",
    "pet": "반려동물 동반은",
    "credit_card": "카드 결제는",
    "restroom": "화장실은",
}

logger = logging.getLogger(__name__)


async def _collect_message_stream(
    stream: AsyncIterator[str], on_message_delta: MessageDeltaCallback
) -> str:
    """LLM 텍스트 스트림을 말풍선 이벤트로 전달하면서 최종 문장도 조립한다."""

    chunks: list[str] = []
    async for delta in stream:
        if not delta:
            continue
        chunks.append(delta)
        await on_message_delta(delta)
    return "".join(chunks).strip()


def compose_recommendation_message(item: RecommendationItem) -> str:
    """explanations를 먼저, warnings는 "다만, ~" 형태로 마지막에 붙인다.

    explanations/warnings 둘 다 이미 완결된 문장(마침표 포함)이라 공백으로
    이어붙인다. explanations는 빈 배열일 수 있다(임계값 미달 등) — 그 경우
    warnings만 "다만, ~"으로 반환한다.
    """
    parts: list[str] = []
    if item.explanations:
        parts.append(" ".join(item.explanations))
    if item.warnings:
        parts.append(f"다만, {' '.join(item.warnings)}")
    return " ".join(parts)


def compose_info_concentration_message(response: InfoContextResponse) -> str:
    """INFO(question_type=concentration) 응답 문구를 조립한다.

    concentration-conditions.md §3.3/§7의 고지 규칙을 고정 로직으로 강제한다 —
    LLM 스타일링이 아니라 정확성이 걸린 문제라서다. is_proxy=True면 반드시
    "근처 [관광지] 기준" 문구를 넣고, 요청한 장소 자체의 값처럼 말하지 않는다.
    """

    if response.status == "needs_clarification":
        code = response.clarification.code if response.clarification is not None else None
        return _CLARIFICATION_TEMPLATES.get(code, _CLARIFICATION_FALLBACK_MESSAGE)
    if response.status == "unsupported":
        return _unsupported_message(response.error.code if response.error else None)
    if response.status == "unavailable" or response.result is None:
        return _TOOL_UNAVAILABLE_MESSAGE

    result = response.result
    if result.status == "unavailable":
        return _TOOL_UNAVAILABLE_MESSAGE
    if result.status == "no_data":
        return _CONCENTRATION_NO_DATA_MESSAGE

    label = result.concentration_label or "알 수 없음"
    date_label = result.forecast_date or "해당 날짜"
    if result.is_proxy:
        return (
            f"{result.requested_place_name} 자체의 혼잡도 데이터는 없지만, "
            f"가장 가까운 관광지인 {result.resolved_place_name} 기준으로는 "
            f"{date_label} {label}인 편이에요. 비슷한 수준일 가능성이 있어요."
        )
    return f"{result.resolved_place_name}은(는) {date_label} 기준 {label} 것으로 예측돼요."


def compose_realtime_commercial_message(response: InfoContextResponse) -> str:
    """특정 카페를 최근접 서울시 상권의 카페 소비 활동으로 안내한다."""

    if response.status == "needs_clarification":
        code = response.clarification.code if response.clarification is not None else None
        return _CLARIFICATION_TEMPLATES.get(code, _CLARIFICATION_FALLBACK_MESSAGE)
    if response.status == "unsupported":
        return _unsupported_message(response.error.code if response.error else None)
    if response.status == "unavailable" or response.result is None:
        return _TOOL_UNAVAILABLE_MESSAGE

    result = response.result
    assert isinstance(result, RealtimeCommercialInfoResult)
    if result.status == "unavailable":
        return _TOOL_UNAVAILABLE_MESSAGE
    if result.status == "no_data":
        area = result.area_name or "가까운 제공 지역"
        return f"{area}의 카페 상권 실시간 데이터는 현재 확인할 수 없어요."

    place = result.resolved_place_name or result.requested_place_name or "해당 카페"
    area = result.area_name or "가까운 상권"
    category = result.category_label or "카페 업종"
    level = result.commercial_level or "확인할 수 없음"
    distance = (
        f"약 {result.proxy_distance_km:.1f}km 떨어진 "
        if result.proxy_distance_km is not None and result.proxy_distance_km >= 0.05
        else ""
    )
    observed_at = format_citydata_timestamp(result.observed_at)
    observed = f" {observed_at} 기준이에요." if observed_at else ""

    # area(실제 데이터를 받아온 상권)와 place(사용자가 물은 장소)가 사실상 같은
    # 이름이면 대체가 아니라 그 장소 자체의 값이다 — "떨어진 곳", "개별 매장
    # 혼잡도는 확인할 수 없지만"을 그대로 붙이면 자기 자신을 다른 곳인 것처럼
    # 말하는 문장이 된다(TP-141/D-084와 같은 패턴, "성수 카페거리" 실측
    # 2026-08-31). 공백만 비교하면 "성수카페거리"↔"성수동카페거리"처럼 글자가
    # 하나 끼어드는 표기 차이를 놓치므로 resolve_location.py와 같은 편집거리
    # 기준(names_match)으로 비교한다.
    if names_match(area, place):
        if result.commercial_scope == "area_overall":
            return (
                f"{area}의 요청 업종 세부값은 현재 제공되지 않았어요. 대신 {area} 전체 상권은 "
                f"현재 {level} 수준이에요. 이 값은 지역 전체 카드 소비 활동 기준이에요.{observed}"
            )
        return (
            f"{area}의 {category} 상권은 현재 {level} 수준이에요. "
            f"이 값은 지역·업종별 카드 소비 활동 기준이에요.{observed}"
        )

    if result.commercial_scope == "area_overall":
        return (
            f"{place} 개별 매장 혼잡도는 확인할 수 없고, {distance}{area}의 요청 업종 "
            f"세부값도 현재 제공되지 않았어요. 대신 {area} 전체 상권은 현재 {level} 수준이에요. "
            f"이 값은 지역 전체 카드 소비 활동 기준이에요.{observed}"
        )
    return (
        f"{place} 개별 매장 혼잡도는 확인할 수 없지만, {distance}{area}의 "
        f"{category} 상권은 현재 {level} 수준이에요. 이 값은 지역·업종별 카드 소비 활동 기준이에요."
        f"{observed}"
    )


def compose_district_population_message(response: InfoContextResponse) -> str:
    """구 하나를 통째로 물었을 때의 혼잡도 안내.

    **첫 줄이 답이다.** "종로구 14곳 중 지금 붐비는 곳은 2곳이에요" 한 줄이면 사용자가
    알고 싶었던 것이 끝난다. 나머지 목록은 근거이고, 카드에도 같은 내용이 실린다.

    등급으로 묶고 개수로 자르지 않는다. 다섯 개까지만 보여주면 그 경계가 등급 한가운데를
    지나 "이 외 9곳은 여유"가 사실과 어긋날 수 있다. 등급으로 묶으면 지역이 4곳이든
    14곳이든 최대 네 줄이고 버리는 정보도 없다.

    여유는 이름 대신 개수만 적는다 — 구 전체를 물었다는 것은 "지금 어디가 붐비나"에
    가깝고, 여유로운 곳까지 나열하면 정작 붐비는 곳이 묻힌다. **다만 여유밖에 없으면
    이름을 적는다.** 안 그러면 "여유 4곳"만 남아 어디를 말하는지 알 수 없다.
    """

    if response.status == "unavailable" or not isinstance(
        response.result, DistrictPopulationInfoResult
    ):
        return _TOOL_UNAVAILABLE_MESSAGE
    result = response.result
    district = result.district_name
    if not result.areas:
        return (
            f"{district}에는 서울시가 실시간 인구 정보를 제공하는 지역이 없어요. "
            "구 안의 장소 이름으로 물어보시면 그 주변 혼잡도를 찾아볼게요."
        )

    grouped: dict[str, list[str]] = {}
    for area in result.areas:
        grouped.setdefault(area.congestion_level, []).append(area.area_name)

    # 세는 규칙은 BUSY_CONGESTION_LEVELS가 정한다 — "보통"은 붐비는 쪽에 넣지 않는다.
    busy_count = sum(
        len(names) for level, names in grouped.items() if level in BUSY_CONGESTION_LEVELS
    )
    total = len(result.areas)
    headline = (
        f"{district} {total}곳 중 지금 붐비는 곳은 {busy_count}곳이에요."
        if busy_count
        else f"{district} {total}곳 모두 지금은 붐비지 않아요."
    )

    lines = [headline]
    for level in CONGESTION_LEVEL_ORDER:
        names = grouped.get(level)
        if not names:
            continue
        # 여유는 개수만 — 단, 여유밖에 없으면 이름을 적는다(위 docstring).
        if level == "여유" and len(grouped) > 1:
            lines.append(f"여유 {len(names)}곳")
        else:
            lines.append(f"{level} {len(names)}곳: {', '.join(names)}")
    if result.unavailable_area_count:
        lines.append(f"{result.unavailable_area_count}곳은 지금 조회하지 못했어요.")

    observed_at = format_citydata_timestamp(result.observed_at)
    if observed_at:
        lines.append(f"{observed_at} 기준이에요.")
    return "\n".join(lines)


def compose_realtime_population_message(response: InfoContextResponse) -> str:
    """가까운 서울시 제공 지역의 현재 인구 혼잡도를 대체 사실로 고지한다.

    place(요청한 장소)와 area(실제 답한 서울시 제공 지역)가 같으면 대체가 아니라
    그 장소 자체의 값이다 — "자체 데이터는 아니지만"을 그대로 붙이면 자기 자신을
    대체값이라 말하는 자기참조 문장이 된다. 121곳 목록에 실제 장소(경복궁 등)가
    들어오기 전에는 place와 area가 항상 달라서 드러나지 않던 문제다(TP-141/D-084).
    """

    if response.status == "unavailable" or not isinstance(
        response.result, RealtimePopulationInfoResult
    ):
        return _TOOL_UNAVAILABLE_MESSAGE
    result = response.result
    if result.status == "no_data":
        return _CONCENTRATION_NO_DATA_MESSAGE

    place = result.resolved_place_name or result.requested_place_name or "해당 장소"
    area = result.area_name or "가까운 제공 지역"
    level = result.current_congestion_level or "확인할 수 없음"
    observed_at = format_citydata_timestamp(result.observed_at)
    observed = f" {observed_at} 기준이에요." if observed_at else ""

    # 공백만 다른 같은 장소("여의도 한강공원" 지오코딩 결과 vs "여의도한강공원" 목록
    # 표기)나 글자 하나 차이("성수카페거리" vs "성수동카페거리")를 다른 곳으로
    # 오판하지 않도록 resolve_location.py와 같은 편집거리 기준으로 비교한다.
    if names_match(area, place):
        return (
            f"{area} 기준으로는 현재 {level} 수준이에요. "
            f"향후 12시간 예상 변화는 아래 카드에서 확인해보세요.{observed}"
        )

    distance = (
        f"약 {result.proxy_distance_km:.1f}km 떨어진 "
        if result.proxy_distance_km is not None and result.proxy_distance_km >= 0.05
        else ""
    )
    return (
        f"{place} 자체의 실시간 인구 데이터는 아니지만, {distance}{area} 기준으로는 "
        f"현재 {level} 수준이에요. 향후 12시간 예상 변화는 아래 카드에서 확인해보세요.{observed}"
    )


def compose_realtime_city_info_message(response: InfoContextResponse) -> str:
    """주차·대중교통·행사·도로소통의 실시간 citydata 결과를 간결하게 안내한다."""

    if response.status == "unavailable" or not isinstance(response.result, RealtimeCityInfoResult):
        return _TOOL_UNAVAILABLE_MESSAGE
    result = response.result
    place = result.resolved_place_name or result.requested_place_name or "해당 지역"
    if result.question_type == "realtime_traffic":
        return _compose_realtime_traffic_message(place, result)
    if result.question_type == "public_toilet":
        return _compose_public_toilet_message(place, result)
    labels = {
        "realtime_parking": "실시간 주차장 정보",
        "realtime_public_parking": "공영주차장 실시간 현황",
        "realtime_subway": "지하철 도착 정보",
        "realtime_bus": "주변 버스정류장 정보",
        "realtime_event": "진행 중 행사",
    }
    label = labels[result.question_type]
    if result.status == "no_data" or not result.fields:
        return f"{place} 주변의 {label}는 현재 확인할 수 없어요."
    return f"{place} 주변의 {label}를 찾았어요. 아래 카드에서 확인해보세요."


# 근처 주차장(area 응답)과 공영주차장(구 전체)을 짝으로 보여줄 때 둘째 카드로
# 넘어간다는 것을 알리는 한 줄. 트리비 페르소나 톤을 유지하되 카드 형태를 바꾸는
# LLM 호출을 새로 걸지 않는다 — 문구 두 개뿐이라 고정 문장으로 충분하다.
_PAIRED_PARKING_FOLLOW_UP = {
    "realtime_parking": "조금 멀어도 지금 자리가 있는 공영주차장도 같이 보여드릴게요.",
    "realtime_public_parking": "더 가까운 주차장도 있는지 같이 찾아봤어요.",
}


def compose_paired_parking_message(message: str, *, question_type: object) -> str:
    """실시간 주차 답변 뒤에 짝 카드로 이어진다는 한 문장을 붙인다.

    ``question_type``은 ``app.schemas.QuestionType``이지만 이 파일은 그 계약을
    모르는 편이 낫다(순환 임포트) — ``.value``가 있으면 쓰고 없으면 문자열
    그대로 받는다.
    """

    key = getattr(question_type, "value", question_type)
    follow_up = _PAIRED_PARKING_FOLLOW_UP.get(key)
    if follow_up is None:
        return message
    return f"{message} {follow_up}"


def _compose_public_toilet_message(place: str, result: RealtimeCityInfoResult) -> str:
    """화장실은 카드를 보라고 미루지 않고 말풍선에서 바로 두 곳을 읽어준다.

    급해서 묻는 질문이라 "아래 카드에서 확인해보세요"는 한 단계를 더 요구하는
    셈이다. 이름과 거리를 문장에 담아 눈으로 바로 집을 수 있게 한다. 대신 길찾기는
    카드를 눌러야 하므로 그 안내만 마지막에 붙인다.
    """

    if result.status == "no_data" or not result.fields:
        return (
            f"{place} 주변 1km 안에서는 이용할 수 있는 공중화장실을 찾지 못했어요."
            " 조금 이동한 뒤 다시 물어봐주시면 다시 찾아볼게요."
        )

    # fields는 {화장실 이름: "도보 50m · 지금 이용 가능 · 24시간"} 형태다.
    lines = [f"· {name} — {summary}" for name, summary in result.fields.items()]
    intro = f"{place} 주변에서 가까운 공중화장실을 찾았어요."
    outro = "카드를 누르면 네이버지도 도보 길찾기로 바로 이어져요."
    return "\n".join([intro, *lines, outro])


def _compose_realtime_traffic_message(place: str, result: RealtimeCityInfoResult) -> str:
    """도로소통은 카드로 안내를 미루지 않고, 값 자체를 말풍선에 바로 담는다.

    항목 하나짜리 스냅샷이라(주차·지하철처럼 여러 곳을 나열할 필요가 없다) 카드
    확인을 유도하는 대신 바로 답한다. 24시간 추이는 원본 API에 없어 다루지 않는다.
    """

    if result.status == "no_data" or not result.fields:
        return f"{place} 주변 도로소통 정보는 현재 확인할 수 없어요."
    level = result.fields.get("도로소통 단계")
    speed = result.fields.get("평균 주행속도")
    if level is None:
        return f"{place} 주변 도로소통 정보를 찾았어요. 아래 카드에서 확인해보세요."
    speed_part = f" 평균 주행속도 {speed}예요." if speed else ""
    return f"{place} 주변 도로는 지금 {level} 수준이에요.{speed_part}"


def compose_place_info_message(
    response: InfoContextResponse,
    *,
    specific_question: str | None = None,
    walking_route: TravelRoute | None = None,
    walking_origin_available: bool = False,
) -> str:
    """INFO(question_type=concentration/event 제외 6종) 응답 문구를 조립한다.

    C가 fields에 값이 있는 키만 채워 보내므로(info-question-types-handoff.md),
    여기서 없는 값을 지어내지 않는다. 긴 원문(운영시간·요금·개요)은 말풍선에
    다시 싣지 않고 아래 장소 카드에서 보여준다. 사실값을 재서술하는 LLM 호출은
    추가하지 않고 질문 유형별 고정 템플릿으로 짧게 안내한다.
    """

    if response.status == "needs_clarification":
        code = response.clarification.code if response.clarification is not None else None
        return _CLARIFICATION_TEMPLATES.get(code, _CLARIFICATION_FALLBACK_MESSAGE)
    if response.status == "unsupported":
        return _unsupported_message(response.error.code if response.error else None)
    if response.status == "unavailable" or response.result is None:
        return _TOOL_UNAVAILABLE_MESSAGE

    result = response.result
    assert isinstance(result, PlaceInfoResult)
    if result.status == "unavailable":
        return _TOOL_UNAVAILABLE_MESSAGE

    place_label = result.resolved_place_name or result.requested_place_name or "그 장소"
    if result.question_type == "location_info" and _asks_walking_time(specific_question):
        return _compose_info_walking_time_message(
            place_label,
            result.fields.get("address"),
            walking_route=walking_route,
            origin_available=walking_origin_available,
        )
    if result.question_type == "review_opinion":
        # 이 경로의 답은 LLM이 후기를 요약해 만든다. 여기까지 온 것은 스트리밍을
        # 쓰지 않는 호출이거나 그 생성이 실패한 경우다 — 고정 문구로는 요약을
        # 대신할 수 없으므로, 근거를 찾았는지만 사실대로 말하고 출처로 넘긴다.
        if result.status == "no_data" or not result.review_evidence:
            # "정보가 없다"가 아니라 "후기에서 확인하지 못했다"로 말한다. 근거를
            # 못 찾은 것과 그 장소에 그런 점이 없는 것은 다르고, 실제로 근처 가게
            # 이야기만 걸려서 전부 걸러진 경우가 많다.
            return (
                f"{place_label}에 대해 물어보신 내용은 후기에서 확인하지 못했어요. "
                "다른 점이 궁금하시면 말씀해 주세요."
            )
        return (
            f"{place_label}에 대한 후기를 찾았는데 지금은 정리해 드리기 어려워요. "
            "아래 출처에서 직접 확인해 보실 수 있어요."
        )

    if result.status == "no_data":
        type_label = _INFO_QUESTION_TYPE_LABELS.get(result.question_type, "그 질문")
        return f"{place_label}의 {type_label} 정보는 확인할 수 없어요."

    return _compose_place_info_sentence(
        place_label,
        result.question_type,
        result.fields,
        specific_question=specific_question,
    )


def _asks_walking_time(specific_question: str | None) -> bool:
    normalized = (specific_question or "").replace(" ", "")
    markers = (
        "가는데얼마나걸",
        "걷는데얼마나걸",
        "걸어서얼마나",
        "도보로얼마나",
        "도보시간",
        "도보이동",
    )
    return any(marker in normalized for marker in markers)


def _compose_info_walking_time_message(
    place_label: str,
    address: str | None,
    *,
    walking_route: TravelRoute | None,
    origin_available: bool,
) -> str:
    """INFO 도보 시간은 LLM 추측 대신 카카오 경로 결과만으로 안내한다."""

    if walking_route is not None:
        seconds = walking_route.duration_seconds or 0
        distance_m = walking_route.distance_m or 0
        if seconds == 0:
            return f"현재 위치에서 {place_label}까지 바로 도착할 수 있어요."
        minutes = max(1, math.ceil(seconds / 60))
        return (
            f"현재 위치에서 {place_label}까지 도보 약 {minutes}분 걸려요. "
            f"이동 거리는 약 {distance_m:,}m예요."
        )
    if not origin_available:
        message = f"현재 위치 정보가 없어 {place_label}까지 도보 이동 시간을 확인할 수 없어요."
        return f"{message} {place_label} 주소는 {address}예요." if address else message
    message = f"현재 위치에서 {place_label}까지의 도보 경로를 확인하지 못했어요."
    return f"{message} {place_label} 주소는 {address}예요." if address else message


def _compose_place_info_sentence(
    place_label: str,
    question_type: str,
    fields: dict[str, str],
    *,
    specific_question: str | None,
) -> str:
    """질문 유형별 짧은 말풍선 안내를 만든다.

    세부 사실은 같은 응답의 ``info_place_card``가 담당한다. 단, 휴무일과 성인
    입장료처럼 사용자가 즉시 알고 싶어 하는 1차 결론만 원문에서 안전하게 꺼낸다.
    """

    if question_type == "operating_hours" and "operating_hours" in fields:
        rest_date = fields.get("rest_date")
        if rest_date and _asks_rest_date(specific_question):
            main, notices = _split_notices(rest_date)
            sentence = f"{place_label} 휴무일은 {main}입니다."
            if notices:
                sentence += "\n" + "\n".join(notices)
            return sentence + "\n\n아래에서 자세한 운영시간을 확인하세요."
        return f"{place_label} 운영시간을 확인했어요. 아래에서 월별 운영시간과 휴무일을 확인하세요."

    if question_type == "fee" and "fee" in fields:
        summary = _fee_summary(fields["fee"])
        if summary:
            return (
                f"{place_label} 입장료는 {summary}이에요. 아래에서 상세 요금 정보를 확인해보세요!"
            )
        return f"{place_label} 입장료 정보를 찾았어요. 아래에서 상세 요금 정보를 확인해보세요!"

    if question_type == "parking" and "parking" in fields:
        return (
            f"{place_label} 주차는 {_parking_status(fields['parking'])}해요. "
            "아래 주차 상세 내용을 확인해보세요."
        )

    if question_type == "facility":
        # 라벨 4개가 고정이라 은/는을 직접 붙인다(받침 유무로 자동 판정하지 않음).
        parts = [
            f"{_FACILITY_FIELD_PHRASES[key]} {fields[key]}"
            for key in ("baby_carriage", "pet", "credit_card", "restroom")
            if key in fields
        ]
        if parts:
            return f"{place_label}의 편의시설이에요. " + ", ".join(parts) + "예요."

    if question_type == "location_info" and "address" in fields:
        return f"{place_label} 주소는 {fields['address']}예요."

    if question_type == "general_info" and "overview" in fields:
        sentence = f"{place_label} 소개예요. {fields['overview']}"
        if "homepage" in fields:
            sentence += f" 홈페이지는 {fields['homepage']}예요."
        return sentence

    # 8종 외 새 question_type이 생기거나 예상 밖 필드 조합이면 라벨:값 나열로
    # 안전하게 낮아진다 — 크래시 대신 최소한의 정보는 전달한다.
    homepage = fields.get("homepage")
    parts = [
        f"{_INFO_FIELD_LABELS.get(key, key)}: {value}"
        for key, value in fields.items()
        if key != "homepage"
    ]
    sentence = f"{place_label} — " + ", ".join(parts) if parts else f"{place_label} 정보예요."
    if homepage:
        sentence += f" 홈페이지: {homepage}"
    return sentence


def _asks_rest_date(question: str | None) -> bool:
    """운영시간 유형 안에서 휴무일을 직접 물은 경우만 구분한다."""

    normalized = (question or "").replace(" ", "")
    return any(token in normalized for token in ("휴무", "쉬는날", "휴관"))


def _split_notices(value: str) -> tuple[str, list[str]]:
    """``※`` 뒤 안내를 별도 줄로 분리한다."""

    parts = [part.strip() for part in value.split("※")]
    main = parts[0]
    notices = [f"※ {part}" for part in parts[1:] if part]
    return main, notices


def _fee_summary(value: str) -> str | None:
    """요금 원문 첫 항목에서 성인 기준의 짧은 결론만 뽑는다."""

    before_notice = value.split("※", maxsplit=1)[0]
    items = [item.strip() for item in before_notice.split("-") if item.strip()]
    if not items:
        return None
    first = items[0]
    if first.startswith("성인 "):
        return "성인 기준 " + first.removeprefix("성인 ")
    return first


def _parking_status(value: str) -> str:
    """수용 대수보다 먼저 읽히는 주차 가능 여부만 말풍선에 쓴다."""

    status = value.split("(", maxsplit=1)[0].strip()
    return {"가능": "가능", "불가": "불가능"}.get(status, status or "확인 가능")


def compose_event_info_message(response: InfoContextResponse) -> str:
    """INFO(question_type=event) 응답 문구를 조립한다.

    D-055/info-question-types-handoff.md의 필수 규칙: is_direct_match=False인
    행사를 그 장소의 행사처럼 말하지 않는다 — TourAPI에 장소별 행사 조회가
    없어 지역 단위로 받아 좌표 거리로 근접 매칭하기 때문이다. 직접 매칭 행사와
    근처 행사를 문장에서 분리해 고지한다.
    """

    if response.status == "needs_clarification":
        code = response.clarification.code if response.clarification is not None else None
        return _CLARIFICATION_TEMPLATES.get(code, _CLARIFICATION_FALLBACK_MESSAGE)
    if response.status == "unsupported":
        return _unsupported_message(response.error.code if response.error else None)
    if response.status == "unavailable" or response.result is None:
        return _TOOL_UNAVAILABLE_MESSAGE

    result = response.result
    assert isinstance(result, EventInfoResult)
    if result.status == "unavailable":
        return _TOOL_UNAVAILABLE_MESSAGE

    place_label = result.resolved_place_name or result.requested_place_name or "그 장소"
    if result.status == "no_data" or not result.events:
        return f"{place_label} 근처에 지금 진행 중인 행사가 없어요."

    direct = [event for event in result.events if event.is_direct_match]
    nearby = [event for event in result.events if not event.is_direct_match]

    sentences: list[str] = []
    if direct:
        titles = ", ".join(event.title for event in direct)
        sentences.append(f"{place_label}에서 진행 중인 행사예요. {titles}.")
    if nearby:
        items = ", ".join(
            f"{event.title}({event.distance_km:.2f}km)"
            if event.distance_km is not None
            else event.title
            for event in nearby
        )
        sentences.append(f"{place_label} 근처에서 진행 중인 행사예요. {items}.")
    return " ".join(sentences)


async def compose_compare_message(comparison: ComparisonResult, llm: LLMProvider) -> str:
    """COMPARE 사실 데이터를 3~6줄 LLM 설명으로 바꾼다.

    C가 반환할 ComparisonResult는 이미 추천 시점 Feature 스냅샷을 기준으로 한
    검증된 데이터다. LLM은 문장을 다듬는 역할만 하며, 호출이 실패해도 비교 요청
    전체를 실패시키지 않고 고정 템플릿으로 안전하게 낮춘다.
    """

    try:
        return (await llm.generate_compare_summary(comparison)).data
    except AppError:
        logger.warning(
            "COMPARE 요약 LLM 생성 실패, 기본 템플릿으로 fallback: criteria=%s",
            comparison.criteria.value,
            exc_info=True,
        )
        return _compose_compare_fallback(comparison)


def _compose_compare_fallback(comparison: ComparisonResult) -> str:
    """LLM 장애 시 C의 사실 데이터만으로 만드는 사용자 표시용 비교 문구."""

    criterion_label = {
        CompareCriteria.TIME: "운영시간",
        CompareCriteria.TRAVEL_TIME: "실제 이동시간",
        CompareCriteria.OVERALL: "운영시간·환경·이동시간",
    }[comparison.criteria]
    recommended = _select_compare_recommendation(comparison)
    lines = [
        f"요청하신 {criterion_label} 기준으로 보면, "
        f"{recommended.place_name}{_object_particle(recommended.place_name)} 추천드려요."
    ]
    for item in comparison.items[:4]:
        details: list[str] = []
        travel_detail = _format_compare_travel_time(item)
        if travel_detail is not None:
            details.append(travel_detail)
        elif item.distance_km is not None:
            details.append(_format_compare_walking_time(item.distance_km))
        if item.remaining_minutes is not None:
            details.append(_format_compare_remaining_time(item.remaining_minutes))
        if item.environment_type is not None:
            details.append(f"{item.environment_type} 환경")
        value = ", ".join(details) if details else "비교 정보 확인 필요"
        lines.append(f"{item.rank}번 {item.place_name}은 {value}이에요.")
    while len(lines) < 3:
        lines.append("확인된 정보를 바탕으로 방문 목적에 맞는 곳을 선택해보세요.")
    return "\n".join(lines[:6])


def _fastest_travel_minutes(item: ComparisonItem) -> int | None:
    """세 수단 중 가장 짧은 소요시간. 하나도 없으면 None."""

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


def _select_compare_recommendation(comparison: ComparisonResult) -> ComparisonItem:
    """LLM fallback에서도 질문 기준과 맞는 한 곳을 분명히 고른다."""

    if comparison.criteria is CompareCriteria.TIME:
        with_time = [item for item in comparison.items if item.remaining_minutes is not None]
        if with_time:
            return max(with_time, key=lambda item: item.remaining_minutes or 0)
    elif comparison.criteria is CompareCriteria.TRAVEL_TIME:
        # 수단 상관없이 "어떻게든 가장 빨리 갈 수 있는 곳"을 기준으로 고른다.
        with_travel_time = [
            item for item in comparison.items if _fastest_travel_minutes(item) is not None
        ]
        if with_travel_time:
            return min(with_travel_time, key=lambda item: _fastest_travel_minutes(item) or 0)
    # overall은 D가 직전에 정렬해 노출한 1번을 기준으로, 추가 점수 계산 없이 고른다.
    return comparison.items[0]


def _object_particle(value: str) -> str:
    """한글 장소명 뒤에 자연스러운 목적격 조사(을/를)를 붙인다."""

    last = value[-1] if value else ""
    is_hangul = "가" <= last <= "힣"
    return "을" if is_hangul and (ord(last) - ord("가")) % 28 else "를"


def _format_compare_walking_time(distance_km: float) -> str:
    """추천 카드와 같은 보수적 보행 속도로 거리 스냅샷을 표시한다."""

    minutes = max(1, math.ceil(distance_km * 60 / 3.6))
    return f"도보 약 {minutes}분"


def _format_compare_remaining_time(remaining_minutes: int) -> str:
    """분 단위 스냅샷을 카드와 같은 시간 단위 표시로 바꾼다."""

    # JavaScript 카드의 Math.round()와 동일하게 .5는 올림 처리한다.
    hours = max(1, math.floor(remaining_minutes / 60 + 0.5))
    return f"약 {hours}시간 남음"


# (조사까지 붙인 라벨, ComparisonItem 필드명) 순서 — compare/summary_instruction.md의
# 나열 순서(도보·자동차·대중교통)와 맞춘다. "대중교통"은 받침이 있어 "으로"를 붙여야
# 하므로("대중교통로"는 어색함) 조사까지 라벨에 미리 넣어둔다. LLM fallback 경로에서
# 같은 형식을 미러링한다(2026-08-21, TRAVEL_TIME 실측 연결).
_TRAVEL_MODE_FIELDS: tuple[tuple[str, str], ...] = (
    ("도보로", "travel_walking_minutes"),
    ("자동차로", "travel_driving_minutes"),
    ("대중교통으로", "travel_transit_minutes"),
)


def _format_compare_travel_time(item: ComparisonItem) -> str | None:
    """실측 거리·수단별 소요시간(TRAVEL_TIME 전용)을 한 문장 조각으로 표시한다.

    값이 있는 수단만 나열한다 — 조회 실패·provider 미설정으로 없는 수단까지
    "확인 안 됨"으로 매번 나열하면 문장이 지저분해진다. 셋 다 없으면 None을
    반환해 호출부가 다른 안내로 대체하게 한다.
    """

    mode_parts = [
        f"{label} 약 {minutes}분"
        for label, field in _TRAVEL_MODE_FIELDS
        if (minutes := getattr(item, field)) is not None
    ]
    if not mode_parts:
        return None
    distance_part = (
        f"약 {item.travel_distance_km}km, " if item.travel_distance_km is not None else ""
    )
    return distance_part + ", ".join(mode_parts)


# 요청 시간과 실제 편성 시간의 차이가 이 값(분) 이내면 문구에 실제 계산값 대신
# 사용자가 요청한 시간을 그대로 보여준다 — "2시간 짜줘"에 "1시간 52분 코스를
# 짜봤어요"처럼 어색하게 어긋나 보이는 걸 막는다. 차이가 크면(후보 부족 등으로
# 실제 편성이 요청과 크게 벌어진 경우) 사용자를 오도하지 않도록 실제 계산값을
# 그대로 보여준다. 15분이었을 때 "3시간 짜줘"(180분)에 실제 163분(오차 17분)이
# 편성되는 경계 사례가 실제 계산값을 그대로 노출해 어색했다 — 되도록 요청값을
# 그대로 보여주는 쪽을 우선하기로 하고 30분으로 넓힘(실사용 피드백, 2026-08-14).


# 계산해서 얻은 소요시간을 말할 때 쓰는 표시 단위(분). (TP-244)
_DURATION_DISPLAY_ROUNDING_MIN = 10


def _format_duration_label(total_minutes: int) -> str:
    """분을 사람 말로 적는다. **값을 바꾸지 않는다.**

    사용자가 말한 시간을 그대로 되돌려줄 때 쓴다("3시간 짜줘" -> "3시간 코스").
    계산해서 얻은 값은 `_format_estimated_duration_label()`을 쓴다.
    """

    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


def _format_estimated_duration_label(total_minutes: int) -> str:
    """편성이 계산한 소요시간을 10분 단위로 **올려** 적는다. (TP-244)

    **올림인 이유.** 반올림하면 266분이 "4시간 20분"이 돼 실제보다 짧게 말한다.
    사용자가 시간을 지키려고 묻는 값이라 짧게 말하는 쪽이 더 나쁘다.

    **표시만 바꾼다.** `total_duration_min` 저장값은 정확값으로 남는다 — 올린
    값을 저장하면 항목 체류시간의 합과 총합이 어긋난다. 허용 오차 판정
    (`budget.classify_budget()`)도 정확값으로 내려진다. 표시용으로 올린 값이
    판정을 뒤집으면 경계에서 화면과 편성이 서로 다른 것을 근거로 말하게 된다.

    총합과 초과 분량을 같은 함수로 올리므로 화면에서 뺄셈이 맞는다 — 266분
    편성을 180분 요청에 맞춰 말하면 "4시간 30분"(270)과 "1시간 30분"(90)이 되고
    270 - 180 = 90으로 앞뒤가 맞는다. 한쪽만 올리면 여기가 어긋난다.
    """

    return _format_duration_label(_round_up_display_minutes(total_minutes))


def _round_up_display_minutes(total_minutes: int) -> int:
    """`_format_estimated_duration_label()`이 실제로 말하게 되는 분.

    부족분·초과분을 이 값에서 뺄 수 있게 따로 뺐다 — 원본값에서 빼면 화면에
    적힌 두 수의 뺄셈이 맞지 않는다(149분 편성을 180분 요청에 대고 말하면
    총합은 "2시간 30분"인데 부족분은 원본 31분을 올려 "40분"이 된다).
    """

    step = _DURATION_DISPLAY_ROUNDING_MIN
    return -(-total_minutes // step) * step


def _topic_particle(word: str) -> str:
    """마지막 한글 음절의 받침 유무에 따라 주제 조사(은/는)를 고른다. (TP-223)

    예전에는 `은`을 문자열에 그대로 박아둬서 받침 없는 이름이 오면
    "인사동 문화의거리은"이 나갔다. 장소 이름은 사용자가 담은 것이라 목록 끝에
    무엇이 올지 코드가 고를 수 없다.

    한글이 아닌 문자로 끝나면(영문 상호 등) `는`으로 둔다 — 발음 규칙을 흉내 내는
    것보다 한쪽으로 고정하는 편이 덜 틀린다. 목적격 조사(을/를)는 이 파일의
    `_object_particle()`이 같은 방식으로 판별한다.
    """

    codepoint = ord(word[-1]) if word else 0
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "은" if (codepoint - 0xAC00) % 28 else "는"
    return "는"


def _with_saved_place_notes(
    message: str, schedule: ScheduleResult
) -> str:
    """보관함과 편성 결과가 어긋난 부분을 사용자 말로 덧붙인다. (SCHEDULE-12, TP-223)

    **사유마다 사용자가 할 수 있는 일이 다르므로 문장을 따로 만든다.** 예전에는
    상한 초과와 LLM 누락이 한 필드에 합쳐져 "시간을 늘리거나 다른 곳을 빼고"라는
    한 문장으로 나갔고, 자기 경우가 어느 쪽인지 알 수 없었다.

    - `closed_saved_place_names`: 방문 시각에 문을 닫아 D가 걸러냈다. **시간대를
      바꾸면 실제로 들어간다** — 조건을 달지 않고 그렇게 말할 수 있는 경우다.
    - `absent_saved_place_names`: 그 밖의 이유로 후보 목록에 없었다(장소 상세를
      못 가져왔거나 좌표가 없다). **시간대를 권하지 않는다** — 바꿔도 결과가 같다.
      예전에는 이 둘이 한 필드였고 "문을 닫는 시간이거나 장소 정보를 못 찾은
      경우라, 시간대를 바꾸면 들어갈 수도 있어요"라는 한 문장이 나갔다. 사유를 단
      채로 제시한 것이었지만, 자기가 어느 쪽인지 알 수 없는 사용자는 결국 시간대를
      바꿔가며 같은 실패를 반복했다(TP-236). D-116이 이 필드를 만든 이유가 통하지
      않는 재시도 권유를 없애는 것이었으므로, 갈라서 말하는 편이 그 결정에 더 맞다.
    - `over_capacity_place_names`: 항목 수 상한을 넘겨 잘렸다. "보관함에서 다른
      곳을 빼면 들어간다"가 확정적으로 참인 유일한 경우다.
    - `omitted_saved_place_names`: 재시도 후에도 LLM이 빠뜨렸다. 확정적인 해결책이
      없으므로 재요청만 권한다.
    - `added_place_names`: 담지 않은 장소가 빈 자리를 채웠다. 설계된 동작이지만
      (prompts/schedule/plan.md) 말하지 않으면 끼어든 것으로 보인다(TP-223).
      **"자리가 남아"로 문장을 연다** — 그 자리에서 사용자가 떠올리는 질문이 "왜 안
      담은 곳이 있지"이고 답이 곧 그 한 마디다. planner가 밀려난 자리를 되돌리므로
      (`_restore_displaced_must_include()`) 이 문구가 나갈 때는 대기 중인 보관함
      장소가 하나도 없다 — "자리가 남았다"가 실제로 참이다.

    사유마다 첫 문장을 다르게 연다("문을 닫아요" / "넣지 못했어요" / "빠졌어요" /
    "자리를 못 잡았어요") — 넷이 함께 나갈 수 있어서 같은 말이 반복되면 사용자가
    무엇이 다른지 읽어내지 못한다.

    "후보"·"편성" 같은 내부 용어를 쓰지 않는다. 순서는 **못 넣은 것 먼저, 새로
    넣은 것 나중**이다 — 사용자가 먼저 찾는 것은 자기가 담은 곳이다.

    어긋난 것이 없으면 message를 그대로 돌려준다. 이게 정상 경로다.
    """

    parts = [message]
    # 문을 닫은 경우를 먼저 말한다 — 넷 중 유일하게 해결책이 확정적이라,
    # 여러 사유가 함께 나갈 때 사용자가 바로 할 수 있는 일이 맨 앞에 온다.
    if schedule.closed_saved_place_names:
        joined = ", ".join(schedule.closed_saved_place_names)
        parts.append(
            f"담아두신 {joined}{_topic_particle(joined)} 그 시간에 문을 닫아요. "
            "시간대를 바꾸면 일정에 넣어드릴 수 있어요."
        )
    if schedule.absent_saved_place_names:
        joined = ", ".join(schedule.absent_saved_place_names)
        parts.append(
            f"담아두신 {joined}{_topic_particle(joined)} 이번엔 넣지 못했어요. "
            "장소 정보를 못 찾은 경우라, 시간대를 바꿔도 결과는 같아요."
        )
    if schedule.over_capacity_place_names:
        joined = ", ".join(schedule.over_capacity_place_names)
        # 상한은 요청마다 다르다 — 활동 가능 시간뿐 아니라 후보의 분류(체류 최소값)와
        # 서로의 거리까지 보고 계산되기 때문이다(TP-239). 후보를 모르는 이쪽에서는
        # 다시 계산할 수 없어 편성이 실어 보낸 값을 읽는다. 값이 없으면(옛 스냅샷,
        # 부분 재편성) 수를 말하지 않는다 — 틀린 수를 말하는 것보다 낫다.
        if schedule.item_capacity is not None:
            limit_phrase = f"한 번에 {schedule.item_capacity}곳까지만 넣을 수 있어서 "
        else:
            limit_phrase = "한 번에 넣을 수 있는 곳 수를 넘어서 "
        parts.append(
            f"{limit_phrase}"
            f"담아두신 {joined}{_topic_particle(joined)} 이번엔 빠졌어요. "
            "보관함에서 다른 곳을 빼면 다음엔 넣어드릴게요."
        )
    if schedule.omitted_saved_place_names:
        joined = ", ".join(schedule.omitted_saved_place_names)
        parts.append(
            f"담아두신 {joined}{_topic_particle(joined)} 이번엔 자리를 못 잡았어요. "
            "다시 말씀해 주시면 넣어볼게요."
        )
    if schedule.added_place_names:
        joined = ", ".join(schedule.added_place_names)
        parts.append(
            f"자리가 남아 {joined}도 함께 넣어봤어요."
        )
    return " ".join(parts)


def _budget_status(
    schedule: ScheduleResult, time_available_min: int | None
) -> ScheduleBudgetStatus | None:
    """이번 편성의 시간 준수 판정. (TP-238)

    **planner가 실어 보낸 값을 그대로 쓴다.** 화면이 다시 판정하면 편성이 목표로
    삼은 것과 화면이 말하는 것이 갈릴 수 있다.

    필드가 비어 있는 경우는 둘이다 — 이 필드가 생기기 전에 저장된 스냅샷
    (`saved_schedules.payload`, `session_messages`)과, 값을 채우지 않는 호출부
    (테스트 더블). 그때만 같은 함수로 계산한다. **같은 함수를 부르는 것과 같은
    계산을 다시 적는 것은 다르다** — 상수도 규칙도 한 곳에 있다.
    """

    if schedule.time_budget_status is not None:
        return schedule.time_budget_status
    return classify_budget(schedule.total_duration_min, time_available_min)


def _with_over_budget_note(
    message: str, schedule: ScheduleResult, time_available_min: int | None
) -> str:
    """편성 결과가 요청한 활동 가능 시간을 넘으면 그 사실을 덧붙인다. (TP-216)

    **탈락시키지 않고 안내만 한다.** 총 소요시간은 구간 이동시간 위에서 계산되는데
    그 값이 실측일 때도 추정일 때도 있어(`ScheduleItem.travel_to_next_measured`),
    넘었다는 이유로 장소를 빼면 추정 오차 때문에 멀쩡한 일정이 깎인다. 사용자가
    보고 판단할 수 있게 수치만 밝힌다.

    **라벨과 이 안내는 같은 판정 하나를 본다**(TP-238). 허용 오차 안이면 위에서
    요청 시간을 그대로 라벨로 쓰므로("3시간 코스를 짜봤어요"), 거기에 초과 안내가
    붙으면 한 문장 안에서 3시간이라고 해놓고 3시간을 넘었다고 말하게 된다. 예전에는
    두 곳이 같은 뺄셈을 따로 해서 묶여 있었고, 지금은 planner가 내린 판정 하나를
    둘 다 읽는다.
    """

    if time_available_min is None:
        return message
    if _budget_status(schedule, time_available_min) is not ScheduleBudgetStatus.OVER:
        return message
    over_min = schedule.total_duration_min - time_available_min
    # 라벨은 항상 "시간"이나 "분"으로 끝나고 둘 다 받침이 있어 조사는 "으로"로
    # 고정해도 된다.
    #
    # **요청 시간은 그대로, 초과 분량은 올려 적는다.** (TP-244) 앞엣것은 사용자가
    # 말한 값이라 손대면 안 되고, 뒤엣것은 편성이 계산한 값이다.
    return (
        f"{message} {_format_duration_label(time_available_min)}으로 말씀하셨는데 "
        f"{_format_estimated_duration_label(over_min)}쯤 길어졌어요. "
        "빼고 싶은 곳이 있으면 알려주세요."
    )


def _with_under_budget_note(
    message: str, schedule: ScheduleResult, time_available_min: int | None
) -> str:
    """편성이 요청한 시간보다 허용 오차 이상 짧으면 그 사실을 덧붙인다.

    **왜 필요한가.** TP-238이 판정을 셋으로 갈랐는데(`within`·`over`·`under`)
    말풍선에는 `over` 문장만 있었다. 그래서 3시간을 요청했는데 1곳 120분이
    나오면 화면이 `"2시간 코스를 짜봤어요"`라고만 말하고 **왜 짧은지 한마디도
    하지 않는다.** 2026-09-07 브라우저 실측에서 첫 턴이 그 모양이었다.

    `over`와 대칭이지만 **사용자가 할 수 있는 일이 다르다.** 길면 뺄 곳을 고르면
    되지만, 짧은 것은 사용자가 손쓸 데가 없다 — 그래서 요청을 되묻지 않고 사실만
    말한다.

    **원인은 자리를 다 썼는지로 가른다.** 항목 수가 상한과 같으면 더 넣을 자리가
    없었다는 뜻이고(후보가 모자랐거나 예산이 그만큼밖에 허락하지 않았다), 그때만
    "더 찾지 못했다"고 말할 수 있다. 상한이 남아 있는데 짧으면 그건 LLM이 덜 고른
    것이라 원인을 단정하지 않는다 — 말할 것이 없어서 짧은 것과 말하지 않는 것은
    다르다.

    `item_capacity`가 없는 옛 스냅샷은 원인 문장을 붙이지 않는다.
    """

    if time_available_min is None:
        return message
    if _budget_status(schedule, time_available_min) is not ScheduleBudgetStatus.UNDER:
        return message
    # **표시한 총합에서 뺀다.** 원본값에서 빼면 화면의 두 수가 어긋난다 —
    # `_round_up_display_minutes()` docstring에 예가 있다.
    short_min = time_available_min - _round_up_display_minutes(schedule.total_duration_min)
    if short_min <= 0:
        return message
    note = (
        f"{message} {_format_duration_label(time_available_min)}으로 말씀하셨는데 "
        f"{_format_duration_label(short_min)}쯤 짧아요."
    )
    capacity = schedule.item_capacity
    if capacity is not None and len(schedule.items) >= capacity:
        return f"{note} 근처에서 넣을 만한 곳을 더 찾지 못했어요."
    return note


def _with_cluster_note(message: str, schedule: ScheduleResult) -> str:
    """도보로 붙어 있는 자리들이 있으면 그 사실을 한 문장 덧붙인다. (TP-243)

    **왜 필요한가.** 묶인 자리는 체류시간 최소값이 45분까지 내려간다. 사용자
    입장에서는 아무 설명 없이 "60분씩"이 "45분씩"으로 바뀌는 것이라, 왜 짧게
    잡혔는지 알 방법이 없다.

    **말하는 것은 근거(붙어 있다)이지 결과(그래서 짧다)가 아니다.** 묶였다고
    모든 분류의 체류가 줄지는 않는다 — 문화시설과 식당은 최소값이 그대로다
    (`duration.policy_for()` 주석). 그런데 여기서는 항목의 분류를 알 수 없으므로
    ("`ScheduleItem`에 category가 없다") "짧게 잡았어요"라고 단정하면 줄지 않은
    편성에도 그 문장이 붙는다. 그건 상한이 줄어든 이유를 잘못 안내했던
    `over_capacity_place_names` 사고와 같은 모양이다. 붙어 있다는 사실은 묶음이
    있으면 언제나 참이고, 사용자가 화면에서 궁금해하는 것에 답한다.

    묶음이 여럿이면 가장 큰 것 하나만 말한다 — 말풍선은 요약이고, 두 묶음을 다
    설명하면 동선 요약보다 길어진다.
    """

    sizes: dict[int, int] = {}
    for item in schedule.items:
        if item.cluster_id is not None:
            sizes[item.cluster_id] = sizes.get(item.cluster_id, 0) + 1
    if not sizes:
        return message
    largest = max(sizes.values())
    if largest < 2:
        return message
    subject = (
        f"{largest}곳 모두"
        if largest == len(schedule.items)
        else f"그중 {largest}곳은"
    )
    return (
        f"{message} {subject} 걸어서 {SCHEDULE_CLUSTER_WALK_MINUTES}분 안쪽이라 "
        "이어서 둘러보도록 붙여 놨어요."
    )


def compose_schedule_message(
    schedule: ScheduleResult, *, time_available_min: int | None = None
) -> str:
    """SCHEDULE 응답 말풍선 텍스트를 조립한다.

    장소별 도착 시각·이유 같은 상세는 여기서 다시 풀어쓰지 않는다 —
    AgentResponse.schedule이 이미 그 정보를 갖고 있다(문서 docstring 원칙과
    동일하게 message는 요약만 맡는다).

    items가 비어있으면(후보 부족 등으로 일정을 못 짠 경우, app/schedule/
    planner.py가 route_summary를 안내 문구로 정규화해서 넘겨준다) "0분 코스를
    짜봤어요" 같은 어색한 접두사 없이 route_summary만 그대로 반환한다.

    time_available_min(사용자가 요청한 시간, 분)이 주어지고 편성이 허용 오차
    안에 들어왔으면 요청 시간을 그대로 보여준다("2시간 짜줘" → "2시간 코스를
    짜봤어요"). 벗어났으면 실제 편성 결과가 요청과 동떨어졌다는 뜻이므로 실제
    계산값을 보여준다. 그 판정은 planner가 내려 ScheduleResult에 싣는다(TP-238).

    총 소요시간이 요청한 활동 가능 시간을 허용 오차 이상으로 넘으면 한 문장을
    덧붙인다(TP-216) — 넘었다는 이유로 장소를 빼지는 않는다. 반대로 허용 오차
    이상으로 짧으면 그 사실도 말한다(`_with_under_budget_note()`) — 판정이
    셋인데 문장이 하나뿐이면 `under`인 턴이 조용히 짧게 나간다.

    도보로 붙어 있는 자리가 있으면 그 사실을 한 문장 덧붙인다
    (TP-243, `_with_cluster_note()`) — 체류시간이 짧게 잡힌 근거다.

    보관함과 편성 결과가 어긋난 부분은 사유별로 문장을 덧붙인다(SCHEDULE-12,
    TP-223, `_with_saved_place_notes()`) — 담아둔 장소를 조용히 빠뜨리거나 담지
    않은 장소를 말없이 끼워 넣으면 사용자는 이유를 알 수 없다. items가 비어 있는
    경우에도 붙인다: 일정을 아예 못 짠 이유가 담아둔 장소와 무관하지 않을 수 있다.
    """

    if not schedule.items:
        return _with_saved_place_notes(schedule.route_summary, schedule)

    if (
        time_available_min is not None
        and _budget_status(schedule, time_available_min) is ScheduleBudgetStatus.WITHIN
    ):
        duration_label = _format_duration_label(time_available_min)
    else:
        duration_label = _format_estimated_duration_label(schedule.total_duration_min)
    headline = f"{duration_label} 코스를 짜봤어요. {schedule.route_summary}"
    # 판정은 셋 중 하나라 두 함수가 동시에 붙지 않는다 — 각자 자기 판정만 본다.
    with_budget_note = _with_under_budget_note(
        _with_over_budget_note(headline, schedule, time_available_min),
        schedule,
        time_available_min,
    )
    # 묶음 설명은 예산 판정 뒤, 보관함 사유 앞에 둔다 — 시간에 대한 말을 먼저
    # 끝내고 동선 이야기를 한 다음, 담아둔 곳 이야기로 넘어가는 순서다.
    return _with_saved_place_notes(_with_cluster_note(with_budget_note, schedule), schedule)


async def compose_chat_message(
    llm_output: LLMOutput,
    *,
    recommendations: RecommendationResponse | None = None,
    schedule: ScheduleResult | None = None,
    schedule_time_available_min: int | None = None,
    tool_status: str | None = None,
    tool_clarification: Clarification | None = None,
    tool_error_code: str | None = None,
    info_response: InfoContextResponse | None = None,
    info_walking_route: TravelRoute | None = None,
    info_walking_origin_available: bool = False,
    llm: LLMProvider,
    on_message_delta: MessageDeltaCallback | None = None,
    rejected_offer_actions: Sequence[str] = (),
    conditions: UserConditions | None = None,
    history: Sequence[ConversationTurnView] = (),
) -> str:
    """AgentResponse.message(챗봇 말풍선 텍스트)를 조립한다.

    docs/design/agent-response-generation.md의 결정을 구현한다. GENERAL·INFO·COMPARE는
    필요할 때 LLM으로 답변 본문을 생성하고, RECOMMEND/MODIFY 성공 경로는 추천 카드
    아래에 붙일 선택 팁을 LLM으로 생성한다. 추천 카드의 상세 내용은 여기서 길게 다시
    풀어쓰지 않는다.

    schedule_time_available_min은 SCHEDULE 경로에서만 쓰인다(사용자가 요청한
    활동 가능 시간, 분) — compose_schedule_message에 그대로 전달해 "요청한
    시간대로" 문구를 만드는 데 쓴다.
    """

    # **이 턴의 intent를 trace 태그로 남기는 자리.** 태그는 그 범위 안에서 생기는
    # observation에 실려 올라가는데, 답변 생성 LLM 호출이 아래에서 생긴다.
    #
    # 그래프 진입(`graph/__init__.py::_intent_tag`)에도 같은 태그를 여는데,
    # **INFO는 두 그래프 중 어느 쪽도 안 거친다** — 자체 경로로 답변을 만든다.
    # 2026-08-25 실측에서 INFO 턴만 `intent:` 태그가 빠져 있는 걸 확인했다.
    # 태그는 집합으로 합쳐지므로 두 곳에서 같은 값을 열어도 중복되지 않는다.
    with trace_attributes(tags=[f"intent:{llm_output.intent.value}"]):
        return await _compose_chat_message(
            llm_output,
            recommendations=recommendations,
            schedule=schedule,
            schedule_time_available_min=schedule_time_available_min,
            tool_status=tool_status,
            tool_clarification=tool_clarification,
            tool_error_code=tool_error_code,
            info_response=info_response,
            info_walking_route=info_walking_route,
            info_walking_origin_available=info_walking_origin_available,
            llm=llm,
            on_message_delta=on_message_delta,
            rejected_offer_actions=rejected_offer_actions,
            conditions=conditions,
            history=history,
        )


async def _compose_chat_message(
    llm_output: LLMOutput,
    *,
    recommendations: RecommendationResponse | None = None,
    schedule: ScheduleResult | None = None,
    schedule_time_available_min: int | None = None,
    tool_status: str | None = None,
    tool_clarification: Clarification | None = None,
    tool_error_code: str | None = None,
    info_response: InfoContextResponse | None = None,
    info_walking_route: TravelRoute | None = None,
    info_walking_origin_available: bool = False,
    llm: LLMProvider,
    on_message_delta: MessageDeltaCallback | None = None,
    rejected_offer_actions: Sequence[str] = (),
    conditions: UserConditions | None = None,
    history: Sequence[ConversationTurnView] = (),
) -> str:
    """`compose_chat_message()`의 본체. 태그 범위 안에서 돈다."""

    if llm_output.status is OutputStatus.NEEDS_CLARIFICATION:
        # LLM 단계 needs_clarification은 추출 단계에서 이미 자연어 메시지가 나온다.
        assert llm_output.clarification is not None
        return llm_output.clarification.message

    if llm_output.intent is Intent.OUT_OF_SCOPE:
        assert llm_output.out_of_scope is not None
        return _OUT_OF_SCOPE_TEMPLATES[llm_output.out_of_scope.category.value]

    if llm_output.intent is Intent.GENERAL:
        assert llm_output.general is not None
        if llm_output.general.topic is GeneralTopic.SERVICE_IDENTITY:
            if on_message_delta is not None:
                await on_message_delta(_SERVICE_IDENTITY_MESSAGE)
            return _SERVICE_IDENTITY_MESSAGE
        # 상황에 맞는 제안이 있으면(대화층 3단계) 답변 끝에 자연스러운 질문으로
        # 붙인다. 무엇을 제안할지는 여기서 정하지 않는다 — situational_offers가
        # 코드로 이미 정해 둔 것을 문장으로 바꾸는 것만 LLM에 맡긴다.
        offer = offer_for(llm_output.general.situation)
        offer_content = (
            offer.content
            if offer is not None and offer.action_id not in rejected_offer_actions
            else None
        )
        if on_message_delta is not None:
            try:
                message = await _collect_message_stream(
                    llm.stream_general_answer(
                        llm_output.general.topic,
                        llm_output.general.original_question,
                        offer_content=offer_content,
                        history=history or None,
                    ),
                    on_message_delta,
                )
                if message:
                    return message
            except AppError:
                logger.warning("GENERAL 답변 스트리밍 실패, 단발 호출로 fallback", exc_info=True)
        result = await llm.generate_general_answer(
            llm_output.general.topic,
            llm_output.general.original_question,
            offer_content=offer_content,
            history=history or None,
        )
        if on_message_delta is not None:
            await on_message_delta(result.data)
        return result.data

    if llm_output.intent is Intent.INFO and info_response is not None:
        if isinstance(info_response.result, RealtimeCommercialInfoResult):
            return compose_realtime_commercial_message(info_response)
        if isinstance(info_response.result, RealtimePopulationInfoResult):
            return compose_realtime_population_message(info_response)
        if isinstance(info_response.result, DistrictPopulationInfoResult):
            return compose_district_population_message(info_response)
        if isinstance(info_response.result, RealtimeCityInfoResult):
            return compose_realtime_city_info_message(info_response)
        if isinstance(info_response.result, EventInfoResult):
            return compose_event_info_message(info_response)
        if isinstance(info_response.result, PlaceInfoResult):
            fallback_message = compose_place_info_message(
                info_response,
                specific_question=(
                    llm_output.info.specific_question if llm_output.info is not None else None
                ),
                walking_route=info_walking_route,
                walking_origin_available=info_walking_origin_available,
            )
            result = info_response.result
            if (
                on_message_delta is not None
                and result.status == "success"
                and result.review_evidence
            ):
                place_name = result.resolved_place_name or result.requested_place_name or "그 장소"
                try:
                    message = await _collect_message_stream(
                        llm.stream_review_answer(
                            place_name=place_name,
                            specific_question=(
                                llm_output.info.specific_question
                                if llm_output.info is not None
                                else None
                            ),
                            evidence=[item.text for item in result.review_evidence],
                            history=history or None,
                        ),
                        on_message_delta,
                    )
                    if message:
                        return message
                except AppError:
                    logger.warning(
                        "후기 답변 스트리밍 실패, 고정 안내문으로 fallback", exc_info=True
                    )
                await on_message_delta(fallback_message)
                return fallback_message
            if on_message_delta is not None and result.status == "success" and bool(result.fields):
                place_name = result.resolved_place_name or result.requested_place_name or "그 장소"
                # 말풍선도 카드의 질문 답변과 같은 정제 규칙을 따른다. 예를 들어
                # 주차 원문에는 버스 수용 대수가 있어도 일반 사용자용 화면은 승용차
                # 정보만 노출한다.
                display_fields = {
                    key: format_parking_for_display(value) if key == "parking" else value
                    for key, value in result.fields.items()
                }
                try:
                    message = await _collect_message_stream(
                        llm.stream_info_answer(
                            place_name=place_name,
                            question_type=result.question_type,
                            specific_question=(
                                llm_output.info.specific_question
                                if llm_output.info is not None
                                else None
                            ),
                            fields=display_fields,
                            history=history or None,
                        ),
                        on_message_delta,
                    )
                    if message:
                        return message
                except AppError:
                    logger.warning(
                        "INFO 답변 스트리밍 실패, 고정 안내문으로 fallback", exc_info=True
                    )
                await on_message_delta(fallback_message)
            return fallback_message
        return compose_info_concentration_message(info_response)

    if llm_output.intent in (Intent.RECOMMEND, Intent.MODIFY, Intent.SCHEDULE):
        if tool_status in _TOOL_TERMINAL_STATUSES:
            if tool_status == "needs_clarification":
                code = tool_clarification.code if tool_clarification is not None else None
                return _CLARIFICATION_TEMPLATES.get(code, _CLARIFICATION_FALLBACK_MESSAGE)
            if tool_status == "unsupported":
                return _unsupported_message(tool_error_code)
            # no_data는 장애가 아니라 "조건에 맞는 후보가 없음"이다. 명시하지 않으면
            # 아래 unavailable 문구로 새어 사용자에게 오류처럼 보인다.
            if tool_status == "no_data":
                return _NO_DATA_MESSAGE
            return _TOOL_UNAVAILABLE_MESSAGE

        if llm_output.intent is Intent.SCHEDULE:
            # schedule이 아직 None이면(예: 이번 호출부가 배선 전이거나 방어적 호출)
            # 안내 문구로 안전하게 낮춘다 — 정상 경로는 agent_runtime.py가 항상
            # schedule을 채워서 넘긴다.
            if schedule is None:
                return _SCHEDULE_NOT_YET_SUPPORTED_MESSAGE
            return compose_schedule_message(
                schedule, time_available_min=schedule_time_available_min
            )

        shown = (
            [*recommendations.recommendations, *recommendations.unverified_recommendations]
            if recommendations is not None
            else []
        )
        if not shown:
            return _NO_DATA_MESSAGE
        message = await compose_recommendation_summary(
            intent=llm_output.intent,
            recommendations=recommendations,
            llm=llm,
            conditions=conditions,
            history=history,
            on_message_delta=on_message_delta,
        )
        return message or recommendation_wrapper_message()

    # INFO/COMPARE — 별도 트랙(agent-response-generation.md §3/§6 3차), 지금은 안내만.
    return _NOT_YET_SUPPORTED_MESSAGE


__all__ = [
    "compose_recommendation_message",
    "compose_recommendation_summary",
    "compose_chat_message",
    "compose_info_concentration_message",
    "compose_realtime_population_message",
    "compose_realtime_commercial_message",
    "compose_realtime_city_info_message",
    "compose_paired_parking_message",
    "compose_place_info_message",
    "compose_event_info_message",
    "compose_compare_message",
    "compose_schedule_message",
    "recommendation_wrapper_message",
    "unsupported_region_footnote",
]

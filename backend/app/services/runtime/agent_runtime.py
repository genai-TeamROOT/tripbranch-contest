"""Agent Runtime — A가 B/C/D 호출 순서를 조정하는 상위 오케스트레이션 계층.

역할: 사용자 발화 하나를 받아 LLMOutput 생성 → B(State) 병합 → C(Tool)/D(Recommendation)
호출(부가 흐름에서만) → B(State)에 노출 결과 기록까지 전체 흐름을 조정한다. C/D는 서로
직접 부르지 않고 항상 A(이 모듈)를 거쳐서만 결과를 주고받는다.
입력: AgentRequest(user_input + session_id + device_location).
출력: AgentResponse(LLMOutput + 병합된 State + 추천 결과).
호출 시점: 아직 전용 HTTP 라우트는 없다. A–C 스키마, A–D RecommendationProvider
계약([TECH-02]) 모두 확정되어 run_agent()가 Real Provider를 주입한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import timedelta
from typing import Any, Literal, TypeAlias, TypeVar, get_type_hints

from app.agent_context.schemas import (
    Coordinates,
    PlaceCandidate,
    RecommendationContext,
)
from app.auth.principal import Principal
from app.config import settings
from app.domain import saved_preference
from app.domain.ranking_origin import resolve_ranking_origin
from app.domain.schedule_travel import (
    ModeJudgmentContext,
    ScheduleTravelCandidate,
    SegmentModeInput,
    SegmentWeather,
)
from app.domain.scoring import SCORING_VERSION
from app.domain.travel_route import (
    MEASURED_ROUTE_SOURCES,
    GeoCoordinate,
    RouteDestination,
    RouteStatus,
    TravelMode,
    TravelRoute,
)
from app.errors import AppError
from app.geo import haversine_km
from app.observability.api_usage import create_external_client
from app.observability.langfuse_tracing import (
    current_trace_id,
    observe_step,
    record_score,
    trace_attributes,
)
from app.place_search_policy import (
    MAX_PLACE_SEARCH_RADIUS_KM,
    WALKING_SPEED_KM_PER_MINUTE,
    transit_switch_straight_line_km,
)
from app.prompts.registry import turn_prompt_version
from app.providers.protocols import LLMProvider
from app.repositories.protocols import PlaceDetailsReadRepository
from app.schedule.associations import fetch_co_visited_hints
from app.schedule.budget import walkable_cluster_size
from app.schedule.metrics import (
    SCHEDULE_QUALITY_STEP,
    WALKABLE_THRESHOLD_MIN,
    schedule_quality_metrics,
)
from app.schedule.planner import plan_partial_schedule, plan_schedule
from app.schedule.schemas import SchedulePartialFillRequest, SchedulePlanningRequest
from app.schemas import (
    AgentRequest,
    AgentResponse,
    ClarificationOption,
    ClarificationPayload,
    CompareCriteria,
    ComparisonItem,
    ComparisonResult,
    ConcentrationIntent,
    ConversationTurnView,
    GeneralPayload,
    GeneralTopic,
    ImageAttribution,
    InfoPayload,
    Intent,
    InterpretRequest,
    LLMOutput,
    ModifyPayload,
    ModifyType,
    OutputStatus,
    PlaceContext,
    PlaceType,
    QuestionType,
    RecommendationItem,
    RecommendationResponse,
    RecommendPayload,
    ScheduleItem,
    SituationKind,
    ToolExecutionDebug,
    TravelOrigin,
    UserConditions,
)
from app.service_area_landmarks import (
    DISTRICT_LANDMARKS,
    find_district_by_gps,
    find_district_by_text,
)
from app.services.interpret.orchestrator import build_interpretation
from app.services.interpret.session_orchestrator import ensure_current_context
from app.services.interpret.situational_offers import offer_for
from app.services.interpret.state_transform import to_user_conditions, transform
from app.services.recommendation_pipeline import PreparedRecommendationResult
from app.services.runtime.compare_transform import (
    to_compare_context_request,
    to_comparison_result,
)
from app.services.runtime.context_transform import to_agent_context_request
from app.services.runtime.enrichment_transform import to_candidate_enrichment_request
from app.services.runtime.follow_up_suggester import suggest_follow_ups
from app.services.runtime.graph import (
    PipelineDeps,
    concentration_source_rows,
    run_early_return_graph,
    run_recommend_pipeline_graph,
)
from app.services.runtime.info_context_schemas import (
    InfoContextRequest,
    InfoContextResponse,
    PlaceInfoResult,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
)
from app.services.runtime.info_context_transform import to_info_context_request
from app.services.runtime.info_response_transform import to_answer_info_place_card
from app.services.runtime.llm_execution import (
    condition_extraction_was_retried,
    consumed_tokens,
    get_llm_execution_metadata,
    reset_llm_execution_metadata,
)
from app.services.runtime.protocols import (
    EnrichmentProvider,
    RecommendationProvider,
    StagedRecommendationProvider,
    ToolProvider,
    TravelRouteToolProvider,
)
from app.services.runtime.recommendation_transform import (
    modes_for_judged_choice,
    to_measured_travel_modes,
)
from app.services.runtime.response_composer import (
    compose_chat_message,
    compose_compare_message,
    compose_paired_parking_message,
    compose_recommendation_summary,
    recommendation_wrapper_message,
    tool_clarification_message,
    unsupported_region_footnote,
)
from app.services.runtime.stream_events import (
    INTERPRET_HEARTBEAT_INTERVAL_SECONDS,
    INTERPRET_HEARTBEAT_MESSAGES,
    SCHEDULING_HEARTBEAT_INTERVAL_SECONDS,
    SCHEDULING_HEARTBEAT_MESSAGES,
    StreamEventSink,
    await_with_heartbeat,
    begin_streamed_message,
    emit_progress,
    emit_stream_event,
)
from app.services.runtime.tool_debug import (
    build_candidate_enrichment_execution_debug,
    build_compare_execution_debug,
    build_info_concentration_execution_debug,
    build_tool_execution_debug,
)
from app.state import preferences as state_preferences
from app.state.schema import (
    ConversationTurn,
    PendingInfoContext,
    SavedPlaceItem,
    SituationState,
    now_kst,
)
from app.state.service import (
    ApiContextView,
    AppendConversationTurnRequest,
    RecommendedPlace,
    RecordClosedExclusionsRequest,
    RecordRecommendationRequest,
    RecordTraceRequest,
    SessionContextResponse,
    SetIgnoreOperatingHoursRequest,
    SetLastIntentRequest,
    SetPendingClarificationRequest,
    SetPendingInfoContextRequest,
    SetSituationStateRequest,
    StateApplyResponse,
    append_conversation_turn,
    apply,
    record_closed_exclusions,
    record_recommendation,
    record_trace,
    set_ignore_operating_hours_until,
    set_last_intent,
    set_pending_clarification,
    set_pending_info_context,
    set_situation_state,
)
from app.state.session import new_trace_id
from app.state.store import StateStore, get_store
from app.tools.mode_judge import LlmModeJudge, narrow_accessibility_needs
from app.tools.recommendation_cards import RecommendationCard, RecommendationCardTool
from app.tools.schedule_travel import (
    JUDGE_SKIPPED_TRANSPORTS,
    select_modes_for_segments,
)
from app.tools.travel_route import TravelRouteQuery

logger = logging.getLogger(__name__)

# SSE 발신 헬퍼는 stream_events.py로 옮겼다 — 라우팅 그래프(graph/)도 같은 sink를
# 써야 하는데, 이 모듈이 그래프를 import하므로 반대 방향은 순환이 되기 때문이다.
# 기존 호출부를 그대로 두려고 옮긴 이름을 여기서 비공개 별칭으로 받는다.
_emit_stream_event = emit_stream_event
_emit_progress = emit_progress
_begin_streamed_message = begin_streamed_message
_await_with_heartbeat = await_with_heartbeat
_SCHEDULING_HEARTBEAT_MESSAGES = SCHEDULING_HEARTBEAT_MESSAGES
_SCHEDULING_HEARTBEAT_INTERVAL_SECONDS = SCHEDULING_HEARTBEAT_INTERVAL_SECONDS

T = TypeVar("T")

_INFO_WALKING_TIME_MARKERS = (
    "가는데얼마나걸",
    "걷는데얼마나걸",
    "걸어서얼마나",
    "도보로얼마나",
    "도보시간",
    "도보이동",
)


def _llm_clarification_code(llm_output: LLMOutput) -> str | None:
    """LLM 단계 되묻기를 단일 코드로 정규화한다.

    LLM은 missing_fields/ambiguous_fields 목록으로, C는 location_required 같은 단일
    코드로 되묻는다. B에는 한 가지 표현만 저장하므로 여기서 맞춘다 — 값 자체는
    "무엇을 되물었는지" 기록용이고, 다음 턴의 판단은 "값이 있는지"만 본다.
    """
    clarification = llm_output.clarification
    if clarification is None:
        return "clarification_required"
    # 오케스트레이터가 분류 이전에 선제 차단으로 만든 되묻기(케이스 4/5)는
    # missing_fields/ambiguous_fields가 비어 있어 아래 유도로는 코드를 못 만든다 —
    # 이 값이 있으면 최우선으로 쓴다.
    if clarification.code is not None:
        return clarification.code
    if clarification.missing_fields:
        return f"missing:{clarification.missing_fields[0].field}"
    if clarification.ambiguous_fields:
        return f"ambiguous:{clarification.ambiguous_fields[0].field}"
    return "clarification_required"


# 조건을 발화에서 뽑아 오는 턴. 이 둘만 `llm_output.recommend`로 조건을 나른다 —
# MODIFY는 `modify` 페이로드를 쓰고 INFO·GENERAL·COMPARE는 조건을 안 나른다.
# 근거는 state_transform.py가 병합 대상을 이 둘로 좁힌 것이다.
_CONDITION_BEARING_INTENTS = (Intent.RECOMMEND, Intent.SCHEDULE)

# llm_interpret 단계의 error_type 값. 조건을 나르는 턴인데 페이로드가 통째로
# 없을 때만 쓴다.
CONDITION_PAYLOAD_MISSING = "condition_payload_missing"

# 같은 자리의 짝 값(TP-266). 첫 호출이 빈손이라 다시 뽑았고 그 재시도가 조건을
# 실어 온 턴이다. **성공이 아니라 "한 번 실패하고 복구된" 턴이라 error_type에
# 남긴다** — 여기를 None으로 두면 재시도가 얼마나 자주 걸리는지 아무도 못 세고,
# 그러면 폴백 모델을 유지할지 1순위를 올릴지 정할 근거가 사라진다(함정 44).
CONDITION_PAYLOAD_RECOVERED = "condition_payload_missing_recovered"


def _condition_intake_error(llm_output: LLMOutput) -> str | None:
    """이번 턴 해석이 조건을 실어 오지 못했으면 그 사유 코드를 돌려준다.

    **왜 필요한가.** `llm_output.recommend`가 None이면 state_transform이 조건 병합을
    통째로 건너뛴다(services/interpret/state_transform.py의 병합 조건). 오류도
    로그도 없이 지나가므로 조건이 하나도 없는 채로 추천·편성이 돌아가고, 사용자에게는
    자기가 말한 조건이 무시된 결과가 나간다.

    **실제로 일어나고 있었다.** 2026-08-18(`89b5bdf`)부터 주 추출 모델이
    `gemini-3.5-flash-lite`인데, 일정 발화에서 이 모델이 `recommend`를 통째로 비워
    보내는 회차가 3분의 1이었다(2026-09-08 실측: 흔들림 6/18 · 기대 불일치 9/18).
    같은 프롬프트를 `gemini-3.5-flash`로 돌리면 18/18 전부 고정·전부 일치다.
    증상이 "엔드포인트마다 결과가 다르다"로 보여 열흘 넘게 원인 미확정으로 남았던
    이유가 이 경로의 침묵이다 — 세는 곳이 있었으면 지표에서 바로 보였다
    (`scripts/verify_schedule_condition_extraction.py`로 확정).

    **`metrics`가 아니라 `error_type`을 쓴다.** TP-242가 도메인 지표를 기존 단계에
    얹지 않기로 정했고 그 분리를 잠그는 테스트가 있다(schedule/metrics.py 머리말).
    이건 지표가 아니라 **이 단계가 제 일을 못 했다는 사실**이라 계약 2절의
    `error_type`("실패 시 오류 분류")이 맞는 자리다 — 단계 이름이 `llm_interpret`,
    즉 전송이 아니라 해석이라는 점이 근거다. HTTP 호출은 성공했지만 해석은 실패했다.

    **조건이 전부 null인 것은 실패로 세지 않는다.** "일정 짜줘"처럼 조건을 하나도
    말하지 않은 발화에서는 그게 옳은 결과다. 페이로드 자체가 없는 것과 뜻이 다르다.
    """

    if llm_output.intent not in _CONDITION_BEARING_INTENTS:
        return None
    if llm_output.recommend is None:
        return CONDITION_PAYLOAD_MISSING
    if condition_extraction_was_retried():
        return CONDITION_PAYLOAD_RECOVERED
    return None


def _record_trace_safely(
    *,
    session_id: str,
    run_id: str,
    step: str,
    latency_ms: int,
    error_type: str | None = None,
    prompt_version: str | None = None,
    scoring_version: str | None = None,
    token_usage: int | None = None,
    metrics: dict[str, Any] | None = None,
    store: StateStore | None,
) -> None:
    """실행 단계 1건을 B에 기록한다. (llmops-trace-contract-v1.md AF-12, B-07)

    variant_id는 아직 값을 안 줘서 None으로 둔다. 기록 실패가 사용자 응답까지
    막으면 안 되므로 예외를 여기서 흡수한다.

    token_usage는 계약상 LLM 단계에만 해당하는 값이라 호출부가 그 단계에서만
    넘긴다 — 모든 단계에 누적 합계를 넣으면 단계별 토큰인 것처럼 읽힌다.
    """
    try:
        record_trace(
            RecordTraceRequest(
                session_id=session_id,
                run_id=run_id,
                step=step,
                latency_ms=latency_ms,
                error_type=error_type,
                prompt_version=prompt_version,
                scoring_version=scoring_version,
                token_usage=token_usage,
                metrics=metrics,
            ),
            store=store,
        )
    except Exception:
        logger.warning(
            "Trace 기록 실패(응답 흐름에는 영향 없음): step=%s session_id=%s run_id=%s",
            step,
            session_id,
            run_id,
            exc_info=True,
        )


def _remember_clarification(session_id: str, code: str | None, store: StateStore | None) -> None:
    """이번 턴이 되묻기로 끝났음을 B에 남긴다(추천까지 갔으면 호출하지 않는다).

    다음 턴의 state_transform이 이 값을 보고 조건 초기화를 건너뛴다.
    """
    set_pending_clarification(
        SetPendingClarificationRequest(session_id=session_id, code=code), store=store
    )


# no_data_closed 되묻기의 "운영 중이 아닌 곳도 볼게요"를 한 번 선택하면 이 시간
# 동안은 매 턴 다시 묻지 않는다(실사용 피드백, 2026-08-13).
_IGNORE_OPERATING_HOURS_TTL = timedelta(hours=1)


def _remember_ignore_operating_hours(session_id: str, store: StateStore | None) -> None:
    """"운영 중이 아닌 곳도 볼게요" 선택을 TTL 동안 B에 남긴다."""
    set_ignore_operating_hours_until(
        SetIgnoreOperatingHoursRequest(
            session_id=session_id, until=now_kst() + _IGNORE_OPERATING_HOURS_TTL
        ),
        store=store,
    )


def _next_situation_state(
    *,
    llm_output: LLMOutput,
    prior: SituationState | None,
    reject_offer_action: str | None,
) -> SituationState:
    """이번 턴이 끝난 뒤 상황 상태가 어떻게 바뀌어야 하는지 계산한다. (대화층 4단계)

    순수 함수다 — 어느 반환 경로가 실행되든 apply() 직후 한 곳에서만 부르면 되게
    하려고 llm_output 하나만으로 판단한다(_remember_clarification처럼 반환 경로마다
    따로 부르면 하나는 반드시 빠뜨린다).

    - 이번 턴이 직전 제안을 거절했으면(reject_offer_action) rejected_actions에
      더하고 pending_offer는 지운다.
    - 이번 턴이 GENERAL 상황 턴이고 아직 안 거절된 실행 가능한 제안을 냈으면 그
      제안을 pending_offer로 남긴다.
    - 그 외(수락/다른 요청/평범한 대화)는 pending_offer만 지운다 — 제안은 바로
      다음 턴에만 유효하다. rejected_actions는 세션 내내(TTL까지) 유지한다.
    """
    rejected = list(prior.rejected_actions) if prior is not None else []
    if reject_offer_action is not None and reject_offer_action not in rejected:
        rejected.append(reject_offer_action)

    pending_offer: str | None = None
    if (
        reject_offer_action is None
        and llm_output.intent is Intent.GENERAL
        and llm_output.general is not None
    ):
        offer = offer_for(llm_output.general.situation)
        if offer is not None and offer.action_id not in rejected:
            pending_offer = llm_output.general.situation.value

    return SituationState(
        current_situation=pending_offer,
        rejected_actions=rejected,
        pending_offer=pending_offer,
    )


def _sync_situation_state(
    *,
    session_id: str,
    llm_output: LLMOutput,
    session_context: SessionContextResponse,
    reject_offer_action: str | None,
    store: StateStore | None,
) -> None:
    """_next_situation_state()가 계산한 값을 필요할 때만 B에 쓴다.

    대부분의 턴은 상황과 전혀 무관하다 — 매 턴 무조건 쓰면 관계없는 세션에도
    Supabase 쓰기가 하나씩 더 생긴다. 바뀔 게 없으면(비어 있던 상태가 그대로
    비어 있음) 건너뛴다.
    """
    prior = session_context.situation_state
    next_state = _next_situation_state(
        llm_output=llm_output, prior=prior, reject_offer_action=reject_offer_action
    )
    if (prior or SituationState()) == next_state:
        return
    set_situation_state(
        SetSituationStateRequest(session_id=session_id, state=next_state), store=store
    )


# SCHEDULE 완료 직후 CHANGE_CONDITION MODIFY가 "일정 재조정"인지 "그냥 추천"인지 글자로는
# 구분 안 되는 경우를 감지한다(docs/design/clarification-options.md 5절). 일정 키워드가
# 전혀 없는데 추천체 어미가 있으면 재조정이라 단정할 근거가 없다.
_SCHEDULE_CONTINUATION_MARKERS = ("일정", "코스", "루트", "편성", "순서", "스케줄")
_RECOMMEND_STYLE_MARKERS = ("추천", "보여줘", "알려줘", "찾아줘", "찾아봐")


def _is_ambiguous_schedule_or_recommend(user_input: str) -> bool:
    return not any(marker in user_input for marker in _SCHEDULE_CONTINUATION_MARKERS) and any(
        marker in user_input for marker in _RECOMMEND_STYLE_MARKERS
    )


# PlaceTag는 값 자체가 한국어라 그대로 쓰면 되지만, PlaceType은 영문 키라 되묻기
# 문구용 한국어 라벨이 따로 필요하다.
_PLACE_TYPE_LABELS: dict[PlaceType, str] = {
    PlaceType.ATTRACTION: "관광지",
    PlaceType.CULTURAL_FACILITY: "문화시설",
    PlaceType.FESTIVAL: "축제",
    PlaceType.LEISURE: "레저",
    PlaceType.SHOPPING: "쇼핑",
    PlaceType.RESTAURANT: "음식점",
}


def _extracted_category_label(modify: ModifyPayload) -> str | None:
    """되묻기 문구/버튼에 넣을 카테고리 라벨. place_tags가 있으면 그대로 쓰고(이미
    한국어 값), 없으면 place_types 라벨, 둘 다 없으면 None(범용 "장소"로 대체)."""
    changes = modify.condition_changes
    if changes is None:
        return None
    if changes.place_tags:
        return changes.place_tags[0].value
    if changes.place_types:
        return _PLACE_TYPE_LABELS.get(changes.place_types[0])
    return None


# 되묻기 버튼 클릭(clarification_choice) 해소 테이블(케이스 1). pending_clarification
# 코드별로 choice_id → 강제할 Intent를 매핑한다. LLM 호출이 없는 순수 dict 매핑이라
# 단위 테스트하기 쉽고, 신규 고정-선택지 코드는 케이스 추가 시 여기에만 등록하면 된다.
_SCHEDULE06_RESOLUTIONS: dict[str, Intent] = {
    "schedule_continue": Intent.SCHEDULE,
    "recommend_only": Intent.RECOMMEND,
}

# location_required 되묻기 버튼(A2)의 최종 폴백값. 발화·GPS 둘 다 위치 신호를 못 주면
# 이 종로구 대표 스팟으로 대신한다 — docs/design/clarification-options.md 7절, D-044.
_LOCATION_REQUIRED_QUICK_PICKS = ("경복궁", "인사동", "광화문", "북촌")

# 클릭 시점 검증용(_resolve_clarification_choice). 어떤 구 버튼이 나갔는지 세션에
# 안 남기므로(TP-160), 나올 수 있는 모든 랜드마크 이름을 허용해야 한다.
_ALL_LOCATION_QUICK_PICK_NAMES = frozenset(_LOCATION_REQUIRED_QUICK_PICKS) | {
    landmark.name for landmarks in DISTRICT_LANDMARKS.values() for landmark in landmarks
}


def _parse_gps(context_gps: str | None) -> tuple[float, float] | None:
    """'위도,경도' 문자열을 (위도, 경도)로. 형식이 아니면 None(_valid_location과 같은 관례)."""
    if not context_gps:
        return None
    parts = context_gps.split(",")
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _location_required_quick_picks(context_gps: str | None) -> tuple[str, ...]:
    """위치 신호가 전혀 없을 때(location_required) 보여줄 대표 스팟 이름들.

    GPS로 가장 가까운 지원 구를 짐작할 수 있으면 그 구 대표 스팟을, 아니면 종로구
    고정 스팟을 반환한다(TP-160).
    """
    coordinate = _parse_gps(context_gps)
    if coordinate is not None:
        district = find_district_by_gps(*coordinate)
        if district is not None:
            return tuple(landmark.name for landmark in DISTRICT_LANDMARKS[district.name])
    return _LOCATION_REQUIRED_QUICK_PICKS


def _location_ambiguous_quick_picks(
    agent_conditions: UserConditions, context_gps: str | None
) -> tuple[str, ...]:
    """위치는 언급했지만(location_ambiguous) 후보를 못 찾았을 때 보여줄 대표 스팟 이름들.

    사용자가 실제로 말한 지역명("용산")을 GPS보다 우선한다 — 방금 직접 말한 지역명이
    현재 위치보다 신뢰도 높은 신호라서다(강남에서 "용산 카페" 검색하는 경우 등).
    발화에서 못 찾으면 GPS로, 그마저 없으면 종로구 고정 스팟으로 대신한다(TP-160).
    """
    location_text = agent_conditions.search_center or agent_conditions.current_location
    if location_text:
        district = find_district_by_text(location_text)
        if district is not None:
            return tuple(landmark.name for landmark in DISTRICT_LANDMARKS[district.name])
    return _location_required_quick_picks(context_gps)

# location_required는 last_intent를 그대로 복원해 재사용한다. RECOMMEND/SCHEDULE는
# 둘 다 llm_output.recommend(RecommendPayload)로 조건을 나르므로 이 지름길로 풀 수
# 있다. MODIFY는 llm_output.modify(ModifyPayload)가 필요해 이 지름길에서 뺐다 — 그런
# 경우는 None을 반환해 평소 경로로 폴백하면, "지명 단독 답변 → MODIFY" 규칙(D-053)이
# 이미 같은 결과를 만들어준다.
_LOCATION_REQUIRED_RESOLVABLE_INTENTS = frozenset({Intent.RECOMMEND.value, Intent.SCHEDULE.value})
# no_data_closed는 SCHEDULE 이전(1차 Scoring 직후)에만 발생하므로 SCHEDULE은 대상이
# 아니다 — RECOMMEND/MODIFY만 재조회 대상이 될 수 있다.
_NO_DATA_CLOSED_RESOLVABLE_INTENTS = frozenset(
    {Intent.RECOMMEND.value, Intent.MODIFY.value, Intent.SCHEDULE.value}
)

# compare_single_shown 되묻기 버튼(PR 3, 케이스 3). "다른 곳도 보여주세요"는 REJECT_ALL로
# 재조회하는 이미 검증된 경로를 그대로 탄다.
_COMPARE_SINGLE_SHOWN_SHOW_MORE = "show_more"
_COMPARE_SINGLE_SHOWN_KEEP_CURRENT = "keep_current"
_COMPARE_SINGLE_SHOWN_KEEP_MESSAGE = "네, 좋은 여행 되세요!"

# 케이스 5(양쪽 변형)의 "새로 시작할게요" 공통 문구. 조건은 비웠지만 Tool을 바로
# 부르지 않는다 — GPS가 있으면 그것만으로 추천이 조용히 나가버려(실사용 재현,
# 2026-08-13), "새로 시작"이라는 사용자 의도(새 조건을 직접 말하고 싶다)와 어긋난다.
_FULL_RESET_TERMINAL_MESSAGE = "새로운 목적지를 입력하거나 원하시는 조건을 알려주세요!"

# no_data_closed 되묻기(실사용 피드백, 2026-08-13: "운영시간 때문이면... 운영중이
# 아닌 곳도 확인하시겠어요?"). D가 결과 0건의 유일한 이유로 폐점 후보 제외를
# 지목했을 때만(RecommendationResponse.excluded_all_closed) 이 문구를 쓴다 —
# 카테고리/거리 등 다른 이유면 기존 _NO_DATA_MESSAGE(response_composer.py)를 그대로 쓴다.
_NO_DATA_CLOSED_MESSAGE = (
    "지금 운영 중인 곳이 없어 검색이 어려워요. 운영 중이 아닌 곳도 확인하시겠어요?"
)
_NO_DATA_CLOSED_SHOW_CLOSED = "show_closed"


async def _respond_no_data_closed(
    llm_output: LLMOutput,
    state_response: StateApplyResponse,
    *,
    store: StateStore | None,
    llm: LLMProvider,
    tool_execution: object,
    tool_executions: object,
) -> AgentResponse:
    """"운영 중이 아닌 곳도 볼게요" 되묻기 응답을 조립한다.

    RECOMMEND/MODIFY 경로와 SCHEDULE 경로 둘 다에서 쓴다 — SCHEDULE도 원인이
    "폐점 후보뿐"이면 "후보가 부족하니 지역/카테고리를 바꿔달라"는 일반 되묻기
    대신 이 되묻기를 먼저 띄워야 한다. 그렇지 않으면 실제 원인(운영시간)과
    무관한 지역/카테고리 변경만 계속 유도하게 되어 무한 되묻기로 이어진다
    (실사용 재현, 2026-08-13 — "경복궁 반나절 코스" 심야 요청).
    """
    clarified = llm_output.model_copy(
        update={
            "status": OutputStatus.NEEDS_CLARIFICATION,
            "clarification": ClarificationPayload(
                code="no_data_closed",
                message=_NO_DATA_CLOSED_MESSAGE,
                options=[
                    ClarificationOption(
                        id=_NO_DATA_CLOSED_SHOW_CLOSED,
                        label="운영 중이 아닌 곳도 볼게요",
                        resolved_intent=llm_output.intent,
                    ),
                ],
            ),
        }
    )
    _remember_clarification(state_response.session_id, "no_data_closed", store)
    message = await compose_chat_message(clarified, llm=llm)
    return AgentResponse(
        llm_output=clarified,
        state=state_response,
        recommendations=None,
        message=message,
        llm_execution=get_llm_execution_metadata(),
        tool_execution=tool_execution,
        tool_executions=tool_executions,
    )


# no_data_empty/no_data_exhausted 되묻기(원인1+3/원인2, 실사용 피드백 후속 조사,
# 2026-08-13). C가 장소 검색 자체에서 0건(status="no_data")을 돌려줄 때, 그 원인이
# "카테고리에 맞는 곳이 없음"(원인1)과 "반경이 좁음"(원인3)은 신호가 동일해
# 구분할 수 없지만(nearby_place_details.py의 `if not selected:`가 raw candidates
# 자체가 없을 때도 NO_DATA를 반환), "이전 노출/거절로 다 소진됨"(원인2)은
# 명시 경고 `candidate_pool_exhausted`로만 구분한다. Provider 호출 성공은 단순히
# 외부 호출이 성공했다는 뜻일 뿐, 후보가 이전 노출/거절로 소진됐다는 근거가 아니다.
_NO_DATA_RESOLVABLE_INTENTS = _LOCATION_REQUIRED_RESOLVABLE_INTENTS | frozenset(
    {Intent.MODIFY.value}
)

# 두 no_data 되묻기가 공유하는 선택지 id. "widen_radius"/"widen_category"는
# 원인이 뭐든 "후보 풀을 넓힌다"는 같은 처방이라 두 코드에서 동일한 id로 쓴다.
_WIDEN_RADIUS = "widen_radius"
_WIDEN_CATEGORY = "widen_category"
_DIFFERENT_AREA = "different_area"
_IGNORE_WEATHER = "ignore_weather"
_CUSTOM_CONDITIONS = "custom_conditions"

# max_travel_time을 이 값으로 올리면(도보 기준) 검색 반경이 상한(MAX_PLACE_SEARCH_
# RADIUS_KM)까지 커진다(recommendation_transform.to_search_radius_km). 도보가 가장
# 느려 상한에 가장 늦게 닿으므로, 이 값이면 어떤 교통수단이든 상한에 닿는다.
_WIDEN_RADIUS_MAX_TRAVEL_TIME = math.ceil(MAX_PLACE_SEARCH_RADIUS_KM / WALKING_SPEED_KM_PER_MINUTE)

_NO_DATA_EMPTY_MESSAGE = (
    "말씀하신 조건에 맞는 곳을 서비스 지역 안에서 찾지 못했어요. 찾으시는 종류가 없거나 "
    "검색 반경이 좁아서일 수 있어요. 검색 범위를 넓혀볼까요, 아니면 다른 종류의 장소도 "
    "함께 볼까요?"
)
_NO_DATA_EMPTY_OPTIONS: tuple[tuple[str, str], ...] = (
    (_WIDEN_RADIUS, "검색 범위 넓히기"),
    (_WIDEN_CATEGORY, "다른 종류도 보기"),
)

_NO_DATA_EXHAUSTED_MESSAGE = (
    "지금까지 보여드린 곳 말고는 조건에 맞는 곳을 다 보여드렸어요. 조건을 좀 바꿔볼까요?"
)
_NO_DATA_EXHAUSTED_OPTIONS: tuple[tuple[str, str], ...] = (
    (_WIDEN_CATEGORY, "다른 종류의 장소도 보기"),
    (_WIDEN_RADIUS, "검색 범위 넓혀서 보기"),
    (_DIFFERENT_AREA, "다른 지역에서 찾기"),
    (_IGNORE_WEATHER, "날씨 상관없이 보기"),
    (_CUSTOM_CONDITIONS, "새로운 조건 직접 말할게요"),
)
_NO_DATA_EXHAUSTED_CUSTOM_MESSAGE = "새로운 조건을 알려주세요!"


@dataclass(frozen=True)
class _ClarificationResolution:
    """되묻기 버튼 클릭의 해소 결과.

    llm_output은 항상 채워지며(감사 표시·상태 병합용), terminal_message가 있으면
    Tool/D 호출 없이 이 문구로 바로 응답을 끝낸다 — 조회할 것이 없는 확인성 선택지
    (예: "지금 장소가 마음에 들어요")에 쓴다.
    """

    llm_output: LLMOutput
    terminal_message: str | None = None
    # True면 D 재조회 시 폐점 후보도 제외하지 않는다 — no_data_closed 되묻기의
    # "운영중이 아닌 곳도 볼게요" 선택지에서만 켠다.
    ignore_operating_hours: bool = False
    # True면 조건 병합(MODIFY/CHANGE_CONDITION)이 끝난 뒤 intent 라벨을 SCHEDULE로
    # 바꿔 아래 6)~8) 단계가 일정 편성 분기를 타게 한다 — schedule_no_candidates
    # 되묻기의 "다른 지역/종류로 찾기" 선택지에서만 켠다. 조건을 실제로 지우려면
    # MODIFY/CHANGE_CONDITION 경로(_clear_conditions_llm_output)가 필요한데(RECOMMEND/
    # SCHEDULE 경로는 빈 값을 "언급 안 함"으로 봐서 안 지워진다), SCHEDULE-06의
    # pending_clarification is None 게이트는 되묻기 해소 turn엔 안 맞아 자동으로
    # relabel되지 않는다 — 그래서 여기서 명시적으로 신호를 준다.
    force_schedule: bool = False
    # 대화층 4단계 — 직전 GENERAL 상황 턴이 낸 제안을 이번 턴이 거절했으면 그
    # action_id. 채워지면 호출부가 situation_state.rejected_actions에 추가해
    # 같은 세션에서 다시 제안하지 않는다.
    reject_offer_action: str | None = None


# no_data_empty/no_data_exhausted의 "다른 종류도 보기"/"다른 지역에서 찾기"/
# "날씨 상관없이 보기"가 공유하는 값 — 조건을 명시적으로 지울 때 각 필드에 넣을
# "없음"에 해당하는 값이다.
_CLEARED_CONDITION_VALUES: dict[str, object] = {
    "place_types": [],
    "place_tags": [],
    "search_center": None,
    "current_location": None,
    "weather_intent": None,
}

# SCHEDULE 실패(후보 부족) 시 되묻기 버튼 ID 및 텍스트.
_SCHEDULE_RELAX_AREA = "schedule_relax_area"
_SCHEDULE_RELAX_CATEGORY = "schedule_relax_category"
# 아래 버튼이 함께 나가므로 "다른 지역이나 다른 종류로" 같은 방법 안내를 문장에
# 다시 적지 않는다 — 버튼이 이미 그 두 가지를 말한다.
_SCHEDULE_NO_CANDIDATES_MESSAGE = (
    "일정을 짤 만한 곳을 충분히 찾지 못했어요. 범위를 넓혀서 다시 찾아볼까요?"
)
_SCHEDULE_NO_CANDIDATES_OPTIONS = (
    (_SCHEDULE_RELAX_AREA, "다른 지역에서 찾기"),
    (_SCHEDULE_RELAX_CATEGORY, "다른 종류의 장소도 포함해서 찾기"),
)


def _clear_conditions_llm_output(fields: tuple[str, ...]) -> LLMOutput:
    """지정한 필드만 명시적으로 지우는 MODIFY/CHANGE_CONDITION을 만든다.

    RECOMMEND 경로(RecommendPayload)는 값이 없는 필드를 "언급 안 함"으로 보고
    기존 값을 그대로 유지한다(state_transform._full_replace_operations) — 그래서
    필드를 실제로 비우려면 changed_fields로 Remove를 명시하는 MODIFY 경로가
    필요하다(state_transform._changed_field_operations).
    """
    return LLMOutput(
        intent=Intent.MODIFY,
        status=OutputStatus.COMPLETE,
        modify=ModifyPayload(
            modify_type=ModifyType.CHANGE_CONDITION,
            condition_changes=UserConditions(
                **{field: _CLEARED_CONDITION_VALUES[field] for field in fields}
            ),
            changed_fields=list(fields),
        ),
    )


# 대화층 4단계 — 직전 GENERAL 상황 턴이 낸 제안에 말로 답할 때만 쓰는 정확 일치
# 화이트리스트. _is_bare_restart_phrase()와 같은 방식(전체 문자열 정확 일치)이다 —
# 도구를 실행하는 자리라 부분일치로 오탐하면 안 된다. "응 근데 다른 데로"는 아래
# 어느 목록과도 안 맞아 정상 분류(build_interpretation)로 폴백한다 — 그게 옳은
# 안전 실패다.
_OFFER_ACCEPT_MARKERS = ("응", "네", "그래", "좋아", "그렇게 해줘", "찾아줘", "찾아봐 줘")
_OFFER_REJECT_MARKERS = ("아니", "괜찮아", "됐어", "필요없어", "필요 없어")
_OFFER_DECLINED_MESSAGE = "네, 필요하시면 언제든 말씀해주세요."


def _matches_offer_marker(user_input: str, markers: tuple[str, ...]) -> bool:
    stripped = user_input.strip().rstrip("!?.~ ")
    return any(stripped == marker or stripped == f"{marker}요" for marker in markers)


def _resolve_offer_utterance(
    *, user_input: str, session_context: SessionContextResponse
) -> _ClarificationResolution | None:
    """직전 GENERAL 상황 턴이 낸 제안에 대한 짧은 응답을 결정적으로 해석한다.

    situation_state.pending_offer가 있을 때만 의미가 있다(_run_agent_flow가 그 조건을
    확인하고서만 이 함수를 부른다). 수락/거절 어느 쪽에도 안 맞으면 None을 돌려 평소
    build_interpretation() 경로로 폴백한다 — 새 요청("그건 말고 다른 걸로")이나 조건
    변경은 그 경로가 정상적으로 처리한다.

    수락은 LLM을 부르지 않고 세션에 이미 병합된 조건에 제안의 조건만 덮어써
    RECOMMEND로 만든다 — situational_offers.SITUATION_OFFERS가 "실행 가능한 것만"을
    보장하므로 이 경로가 못 하는 걸 약속할 위험이 없다.
    """

    situation_state = session_context.situation_state
    if situation_state is None or situation_state.pending_offer is None:
        return None
    try:
        situation = SituationKind(situation_state.pending_offer)
    except ValueError:
        return None
    offer = offer_for(situation)
    if offer is None:
        return None

    if _matches_offer_marker(user_input, _OFFER_REJECT_MARKERS):
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent.GENERAL,
                status=OutputStatus.COMPLETE,
                general=GeneralPayload(
                    topic=GeneralTopic.TRAVEL_TIP, original_question=user_input
                ),
            ),
            terminal_message=_OFFER_DECLINED_MESSAGE,
            reject_offer_action=offer.action_id,
        )

    if _matches_offer_marker(user_input, _OFFER_ACCEPT_MARKERS):
        conditions = to_user_conditions(session_context.user_conditions).model_copy(
            update=dict(offer.condition_overrides)
        )
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent.RECOMMEND,
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(conditions=conditions),
            )
        )

    return None


def _resolve_clarification_choice(
    *, choice_id: str, session_context: SessionContextResponse
) -> _ClarificationResolution | None:
    """되묻기 버튼 클릭을 결정적으로 해소한다.

    이미 세션에 병합된 조건(session_context.user_conditions)을 그대로 재사용해
    classify_intent()/extract_*_conditions() 호출을 건너뛴다. pending_clarification
    코드/choice_id가 등록된 조합과 안 맞으면(새로고침 후 오래된 버튼 클릭 등) None을
    반환해 평소 build_interpretation() 경로로 폴백하게 한다 — 절대 죽지 않는다.
    """
    code = session_context.pending_clarification
    if code is None:
        return None
    conditions = to_user_conditions(session_context.user_conditions)

    if code == "schedule06_ambiguous_recommend":
        resolved_intent = _SCHEDULE06_RESOLUTIONS.get(choice_id)
        if resolved_intent is None:
            return None
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=resolved_intent,
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(conditions=conditions),
            )
        )

    if code == "location_required":
        if choice_id not in _ALL_LOCATION_QUICK_PICK_NAMES:
            return None
        if session_context.last_intent not in _LOCATION_REQUIRED_RESOLVABLE_INTENTS:
            return None
        conditions = conditions.model_copy(update={"search_center": choice_id})
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent(session_context.last_intent),
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(conditions=conditions),
            )
        )

    if code == "location_ambiguous":
        # location_required와 달리 choice_id가 고정 목록이 아니라 Tool이 실제로
        # 찾아낸 후보 이름이다(resolve_location.py) — 값 자체를 검증할 기준이 없어
        # 비어있지만 않으면 그대로 search_center로 쓴다. MODIFY는 location_required와
        # 같은 이유로 이 지름길에서 빠진다.
        if (
            not choice_id
            or session_context.last_intent not in _LOCATION_REQUIRED_RESOLVABLE_INTENTS
        ):
            return None
        conditions = conditions.model_copy(update={"search_center": choice_id})
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent(session_context.last_intent),
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(conditions=conditions),
            )
        )

    if code == "place_ambiguous":
        # location_ambiguous와 같은 이유로 choice_id는 Tool이 실제로 찾아낸 후보
        # 이름이다(resolve_location.py). INFO는 RECOMMEND와 달리 조건이 아니라
        # question_type/specific_question 등 원래 질문 자체를 이어받아야 하므로
        # session_context.user_conditions가 아니라 pending_info_context(agent_runtime의
        # place_ambiguous 되묻기 생성 지점이 저장해둠)를 쓴다. 세션이 만료됐거나
        # 새로고침 후 오래된 버튼을 누르면(pending_info_context 없음) 평소
        # build_interpretation() 경로로 안전하게 폴백한다.
        pending_info = session_context.pending_info_context
        if (
            not choice_id
            or pending_info is None
            or session_context.last_intent != Intent.INFO.value
        ):
            return None
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent.INFO,
                status=OutputStatus.COMPLETE,
                info=InfoPayload(
                    place_name=choice_id,
                    place_context=PlaceContext(pending_info.place_context),
                    question_type=QuestionType(pending_info.question_type),
                    specific_question=pending_info.specific_question,
                    visit_time=pending_info.visit_time,
                ),
            )
        )

    if code == "no_data_closed":
        # 폐점 후보뿐이라 결과가 0건이었던 턴(no_data_closed, D-062류)의 "운영중이
        # 아닌 곳도 볼게요" 선택지. 조건은 그대로 재사용하고 ignore_operating_hours만
        # 켜서 같은 검색을 다시 돌린다 — D가 이번엔 폐점 후보도 채점에 포함한다.
        if (
            choice_id != _NO_DATA_CLOSED_SHOW_CLOSED
            or session_context.last_intent not in _NO_DATA_CLOSED_RESOLVABLE_INTENTS
        ):
            return None
        return _ClarificationResolution(
            llm_output=LLMOutput(
                intent=Intent(session_context.last_intent),
                status=OutputStatus.COMPLETE,
                recommend=RecommendPayload(conditions=conditions),
            ),
            ignore_operating_hours=True,
        )

    if code == "no_data_empty":
        # 원인1+3(TourAPI 자체가 0건). 두 선택지 다 조건을 넓혀 같은 검색을
        # 재조회한다 — 원인을 구분 못 하므로 "후보 풀을 넓힌다"는 같은 처방을 쓴다.
        if session_context.last_intent not in _NO_DATA_RESOLVABLE_INTENTS:
            return None
        if choice_id == _WIDEN_RADIUS:
            updated = conditions.model_copy(
                update={"max_travel_time": _WIDEN_RADIUS_MAX_TRAVEL_TIME}
            )
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent(session_context.last_intent),
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=updated),
                )
            )
        if choice_id == _WIDEN_CATEGORY:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("place_types", "place_tags"))
            )
        return None

    if code == "no_data_exhausted":
        # 원인2(이전 노출/거절로 소진). "제외 이력을 다시 보여달라"는 선택지는
        # B(세션 상태) 리셋이 필요해 빼고, 조건을 바꿔 재조회하는 선택지들과
        # Tool 호출 없이 자유 입력을 유도하는 선택지만 둔다.
        if session_context.last_intent not in _NO_DATA_RESOLVABLE_INTENTS:
            return None
        if choice_id == _CUSTOM_CONDITIONS:
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.GENERAL,
                    status=OutputStatus.COMPLETE,
                    general=GeneralPayload(topic=GeneralTopic.TRAVEL_TIP, original_question=""),
                ),
                terminal_message=_NO_DATA_EXHAUSTED_CUSTOM_MESSAGE,
            )
        if choice_id == _WIDEN_CATEGORY:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("place_types", "place_tags"))
            )
        if choice_id == _IGNORE_WEATHER:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("weather_intent",))
            )
        if choice_id == _DIFFERENT_AREA:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("search_center", "current_location"))
            )
        if choice_id == _WIDEN_RADIUS:
            updated = conditions.model_copy(
                update={"max_travel_time": _WIDEN_RADIUS_MAX_TRAVEL_TIME}
            )
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent(session_context.last_intent),
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=updated),
                )
            )
        return None

    if code == "schedule_no_candidates":
        # SCHEDULE 실패(후보 부족) 시 조건을 완화해 다시 시도한다.
        if session_context.last_intent not in _NO_DATA_RESOLVABLE_INTENTS:
            return None
        if choice_id == _SCHEDULE_RELAX_AREA:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("search_center", "current_location")),
                force_schedule=True,
            )
        if choice_id == _SCHEDULE_RELAX_CATEGORY:
            return _ClarificationResolution(
                llm_output=_clear_conditions_llm_output(("place_types", "place_tags")),
                force_schedule=True,
            )
        return None

    if code == "compare_single_shown":
        if choice_id == _COMPARE_SINGLE_SHOWN_SHOW_MORE:
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.MODIFY,
                    status=OutputStatus.COMPLETE,
                    modify=ModifyPayload(modify_type=ModifyType.REJECT_ALL),
                )
            )
        if choice_id == _COMPARE_SINGLE_SHOWN_KEEP_CURRENT:
            # Tool/D를 부를 것이 없는 확인성 선택지 — 고정 문구로 바로 끝낸다.
            # GeneralPayload는 감사 표시용으로만 채우고(compose_chat_message는 부르지
            # 않으므로 LLM 호출 없음), topic 값 자체는 의미가 없다.
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.GENERAL,
                    status=OutputStatus.COMPLETE,
                    general=GeneralPayload(topic=GeneralTopic.TRAVEL_TIP, original_question=""),
                ),
                terminal_message=_COMPARE_SINGLE_SHOWN_KEEP_MESSAGE,
            )
        return None

    if code == "schedule_bare_restart":
        # 케이스 4(PR 4). 두 옵션 다 intent=SCHEDULE로 다시 들어간다 — "restart"는
        # 버튼 label("네, 처음부터 다시 잡을게요")이 _RESET_SCOPE_PHRASES와 일치해
        # state_transform.transform()이 조건을 soft reset으로 비우고, 그 결과
        # search_center가 다시 비어 location_required가 자연스럽게 재발생한다(PR2
        # 종로구 대표 스팟 버튼으로 이어짐). "keep_asking"은 병합된 조건을 그대로
        # 재사용해 같은 location_required를 다시 띄운다 — 새 상태 조작 코드가
        # 필요 없다.
        if choice_id == "restart":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.SCHEDULE,
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=UserConditions()),
                )
            )
        if choice_id == "keep_asking":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.SCHEDULE,
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=conditions),
                )
            )
        return None

    if code == "bare_restart_active":
        # 케이스 5(PR 4). "keep_context"는 REJECT_ALL로 조건은 그대로 두고 다시
        # 조회한다(버튼 label에 재시작 문구가 없어 reset이 안 걸린다). "full_reset"은
        # 빈 조건 + label("새로 시작할게요")이 _RESET_SCOPE_PHRASES와 일치해 soft
        # reset으로 조건이 전부 비워진다 — 조회 자체는 터미널 문구로 대신한다(아래
        # _FULL_RESET_TERMINAL_MESSAGE 참고, GPS만으로 조용히 추천이 나가는 걸 막음).
        if choice_id == "keep_context":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.MODIFY,
                    status=OutputStatus.COMPLETE,
                    modify=ModifyPayload(modify_type=ModifyType.REJECT_ALL),
                )
            )
        if choice_id == "full_reset":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.RECOMMEND,
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=UserConditions()),
                ),
                terminal_message=_FULL_RESET_TERMINAL_MESSAGE,
            )
        return None

    if code == "schedule_bare_restart_completed":
        # 케이스 5의 SCHEDULE 버전. "retry_schedule"은 조건을 그대로 두고 SCHEDULE로
        # 재편성한다(REJECT_ALL이 아니다 — SCHEDULE 결과에는 안 맞는 동작이라서다).
        # "full_reset"은 케이스 5와 동일하게 빈 조건 + 터미널 문구로 끝낸다.
        if choice_id == "retry_schedule":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.SCHEDULE,
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=conditions),
                )
            )
        if choice_id == "full_reset":
            return _ClarificationResolution(
                llm_output=LLMOutput(
                    intent=Intent.RECOMMEND,
                    status=OutputStatus.COMPLETE,
                    recommend=RecommendPayload(conditions=UserConditions()),
                ),
                terminal_message=_FULL_RESET_TERMINAL_MESSAGE,
            )
        return None

    return None


# "OO 기준으로 다시 보기" 비차단형 전환(D-071, TravelOriginToggle)이 결정적으로 재실행할
# 수 있는 Intent. 직전 답변이 RecommendPayload로 조건을 나르는 두 Intent만 대상이다 —
# _resolve_clarification_choice의 location_required와 같은 이유.
_TRAVEL_ORIGIN_OVERRIDE_RESOLVABLE_INTENTS = frozenset(
    {Intent.RECOMMEND.value, Intent.SCHEDULE.value}
)


def _apply_selected_locations(llm_output: LLMOutput, request: AgentRequest) -> LLMOutput:
    """위치 설정 화면에서 정한 출발지·검색 기준을 이번 턴 조건에 채운다.

    두 값은 서로 다른 질문의 답이다 — `current_location`은 "사용자가 어디 있는가"
    (이동시간의 출발점), `search_center`는 "어디 주변을 찾을까"(후보를 모으는 중심)다.
    D-067이 둘을 분리한 이유가 여기 있다.

    **발화가 이긴다.** 화면에 안국역이 설정돼 있어도 "성수동 카페 알려줘"라고
    말했으면 성수동이다 — 그 턴에 사용자가 직접 말한 쪽이 더 명확한 의사이기
    때문이다. 그래서 추출된 값이 이미 있으면 손대지 않는다.

    RECOMMEND에만 적용한다. UserConditions를 직접 들고 있는 payload가 그것뿐이고,
    이어지는 MODIFY/SCHEDULE 턴은 B가 병합해 둔 세션 조건에서 이 값을 물려받는다.

    검색 기준을 채우면 위치 되묻기(location_required)도 함께 사라진다 — 사용자가
    이미 화면에서 정했으니 다시 물을 이유가 없다.
    """
    if llm_output.recommend is None:
        return llm_output

    conditions = llm_output.recommend.conditions
    filled: dict[str, str] = {}
    for field, selected in (
        ("current_location", request.selected_current_location),
        ("search_center", request.selected_search_center),
    ):
        if selected is None or getattr(conditions, field) is not None:
            continue
        normalized = selected.strip()
        if normalized:
            filled[field] = normalized
    if not filled:
        return llm_output

    return llm_output.model_copy(
        update={
            "recommend": llm_output.recommend.model_copy(
                update={"conditions": conditions.model_copy(update=filled)}
            )
        }
    )


def _override_locations_from_request(
    state_response: StateApplyResponse, request: AgentRequest
) -> StateApplyResponse:
    """조건 병합이 끝난 뒤, 화면에서 정한 위치를 이번 턴 조건에 반영한다.

    **`_apply_selected_locations()`가 닿지 못하는 턴을 받는다.** 저쪽은
    `llm_output.recommend`를 들고 있는 턴(RECOMMEND·SCHEDULE)에만 적용되고, 조건을
    직접 나르지 않는 MODIFY("그거 말고 다른 곳")·COMPARE·INFO는 그냥 지나간다.
    그런 턴은 병합된 세션 조건에서 위치를 물려받으므로, 사용자가 위치 설정 화면에서
    출발지를 바꿔도 **이번 요청에 그 값이 실려 왔는데도 무시된다.**

    2026-09-07 로컬 실측(POST /api/chat 2턴):

        턴1  "카페 추천해줘"           + 출발지 안국역 / 검색지 광화문역
        턴2  "그거 말고 다른 곳 보여줘"  + 출발지 성수동 / 검색지 성수동
        → 턴2 조건 current_location='안국역', search_center='광화문역'

    성수동을 보냈는데 안국역에서 찾는다. 화면에는 성수동이 떠 있으므로 사용자가
    보는 것과 실제 검색이 어긋난다.

    **발화가 화면 설정을 이긴다** — `_apply_selected_locations()`와 같은 규칙이다.
    이번 턴에 그 필드의 연산이 적용됐다면 발화가 값을 정했다는 뜻이므로 손대지
    않는다. 연산이 없을 때만 덮는다. RECOMMEND 턴은 저 함수가 이미 요청값을
    채워 연산을 만들었으므로 여기서는 아무 일도 하지 않는다.

    **세션에 다시 쓰지는 않는다.** 병합이 끝난 뒤라 이 덮어쓰기는 이번 턴에만
    산다. 요청은 매 턴 같은 값을 다시 실어 오므로 다음 턴도 같은 결과가 되고,
    위치를 서버에 저장하지 않게 되면(docs/location-storage-removal.local.md) 이
    함수는 그대로 둔 채 저장만 빠진다.

    **되묻기 버튼으로 해소된 턴은 여기서 안 고쳐진다.** 그 경로는 세션 조건을
    베껴 payload를 만들고, 그 값이 연산으로 적용돼 위 규칙에 걸린다. 저장을
    없애면 세션 조건이 비어 `_apply_selected_locations()`가 요청값을 채우므로
    자연히 풀린다 — 이 변경에서 따로 손대지 않는다.
    """

    applied_fields = {op.field for op in state_response.applied_operations if op.field}
    updates: dict[str, str] = {}
    for field, selected in (
        ("current_location", request.selected_current_location),
        ("search_center", request.selected_search_center),
    ):
        if field in applied_fields or selected is None:
            continue
        normalized = selected.strip()
        if normalized and getattr(state_response.user_conditions, field) != normalized:
            updates[field] = normalized
    if not updates:
        return state_response

    return state_response.model_copy(
        update={"user_conditions": state_response.user_conditions.model_copy(update=updates)}
    )


def _resolve_travel_origin_override(
    *, override: TravelOrigin, session_context: SessionContextResponse
) -> _ClarificationResolution | None:
    """"OO 기준으로 다시 보기" 버튼 클릭을 결정적으로 해소한다.

    되묻기(_resolve_clarification_choice)와 달리 pending_clarification을 요구하지
    않는다 — 이 버튼은 완결된 답변 아래에 조건부로 붙는 비차단형 제안이라(D-071,
    TravelOriginToggle) 직전 턴이 되묻기로 끝났을 필요가 없다. 세션에 이미 병합된
    조건(session_context.user_conditions)을 그대로 재사용해 travel_origin만
    override로 덮어쓴다 — classify_intent()/extract_recommend_conditions() 호출
    없이 즉시 재실행한다.

    직전 턴이 RECOMMEND/SCHEDULE가 아니거나 아직 추천 결과가 없으면(새로고침 뒤
    오래된 버튼 클릭 등) None을 반환해 평소 build_interpretation() 경로로
    폴백한다 — 절대 죽지 않는다.
    """
    if session_context.last_intent not in _TRAVEL_ORIGIN_OVERRIDE_RESOLVABLE_INTENTS:
        return None
    if not session_context.has_recommendation:
        return None
    conditions = to_user_conditions(session_context.user_conditions).model_copy(
        update={"travel_origin": override}
    )
    return _ClarificationResolution(
        llm_output=LLMOutput(
            intent=Intent(session_context.last_intent),
            status=OutputStatus.COMPLETE,
            recommend=RecommendPayload(conditions=conditions),
        )
    )


def _resolve_schedule_from_saved(
    *, session_context: SessionContextResponse
) -> _ClarificationResolution | None:
    """보관함 하단 바의 "이 장소들로 일정 짜기" 클릭을 결정적으로 해소한다.

    _resolve_travel_origin_override와 같은 비차단형 버튼이라 pending_clarification을
    요구하지 않는다 — 완결된 추천 답변 아래에 붙는 상시 CTA다. 세션에 이미 병합된
    조건(session_context.user_conditions)을 그대로 재사용해 intent만 SCHEDULE로
    확정한다. classify_intent()/extract_*_conditions() 호출을 통째로 건너뛰므로
    체감 지연에 직접 영향을 준다.

    must_include_place_ids를 여기서 넘기지 않는다 — 보관함 반영(후보 복귀·배치
    보장, D-114)은 이미 모든 SCHEDULE 턴에 적용되므로 이 버튼만 특별 취급하면
    자유입력 "담은 곳으로 일정 짜줘"와 동작이 갈린다.

    보관함이 비어 있으면 None을 반환해 평소 build_interpretation() 경로로
    폴백한다 — 새로고침 뒤 남은 화면에서 눌렀거나, 마지막 항목을 빼는 요청과
    클릭이 겹친 경우다. 빈 보관함으로 편성에 들어가면 "담은 곳"이 하나도 없는
    일정이 나가 사용자에게는 버튼이 오작동한 것처럼 보인다.
    """
    if not session_context.saved_places:
        return None
    return _ClarificationResolution(
        llm_output=LLMOutput(
            intent=Intent.SCHEDULE,
            status=OutputStatus.COMPLETE,
            recommend=RecommendPayload(
                conditions=to_user_conditions(session_context.user_conditions)
            ),
        )
    )


# C 단계에서 Recommendation으로 못 넘어가는 status. needs_clarification은 조건 재질문(사용자
# 응답 필요), unsupported/unavailable은 그 자체로 안내만 하고 끝나는 상태다(계약 문서 §5.4).
# no_data도 후보가 없어 D에 넘길 것이 없다 — 빈 후보로 Scoring을 돌려도 결과는 같으므로
# 호출하지 않고, 대신 조건을 바꿔볼지 사용자에게 되묻는다(int-03-modify.md §11).
_TOOL_TERMINAL_STATUSES = frozenset(
    {"needs_clarification", "no_data", "unsupported", "unavailable"}
)

# concentration_intent가 AVOID/SEEK일 때만 혼잡도 보강 조회 대상이 되는 값.
_CONCENTRATION_RANK_INTENTS = frozenset({ConcentrationIntent.AVOID, ConcentrationIntent.SEEK})

# 2차 Scoring(재순위)이 실제로 실행됐을 때만 적용하는 최종 노출 개수 — RECOMMEND/MODIFY
# 기본값. (기획 확정, 2026-08-02 — concentration-conditions.md §2.2.3 9단계.
# 재순위가 안 일어나면(D 미구현 등) 1차 결과를 그대로 쓰고 이 상수는 적용하지 않는다 —
# 기능이 실제로 동작하지 않는데 결과 개수만 줄이는 걸 피하기 위함이다.)
#
# (호출부가 final_limit을 항상 명시적으로 넘긴다. 생략했을 때의 기본값은
# settings에서 호출 시점에 읽는다 — 모듈 로드 시점에 굳히면 환경 설정 변경이나
# 테스트의 monkeypatch가 반영되지 않는다.)

# 하드 필터 통과 후보가 목표 개수보다 적을 때 C에 추가 후보를 요청하는 최대 횟수.
# 최초 조회는 포함하지 않으므로 전체 C 호출은 최대 3회다. 무한 반복과 외부 API
# 호출 폭증을 막기 위해 상수로 명시한다.
_MAX_CANDIDATE_REFILL_ATTEMPTS = 2

# SCHEDULE이 D에게 받는 후보 수. 일정 편성 모듈이 이 목록에서 코스를 고른다.
#
# **후보 수집 상한(RECOMMENDATION_CANDIDATE_LIMIT)에서 떼어낸 상수다.** 예전에는
# 그 설정을 그대로 썼는데, 두 값은 뜻이 다른데 우연히 같았을 뿐이다 — 하나는
# "C에서 몇 곳을 모아올까"이고 이건 "일정 편성에 몇 곳을 넘길까"다. 후보 상한을
# 올렸더니 이 값까지 따라 올라가면서 드러났다.
#
# 10인 근거는 D와의 협의다(int-07-schedule.md 135행 "D 반환 수: 상위 10개 전부",
# 5절). 혼잡도 2차 재순위도 SCHEDULE에서는 이 개수 전부에 걸리므로(같은 문서
# 201~206행) 늘리면 혼잡도 외부 조회가 비례해서 늘어난다. **바꾸려면 D와 다시
# 협의해야 한다.**
SCHEDULE_RECOMMENDATION_LIMIT = 10
_CANDIDATE_POOL_TRUNCATED_WARNING = "candidate_pool_truncated"

# 보강 응답 전체가 이 상태면 2차 Scoring을 시도할 실익이 없다(재조회할 데이터가 없음).
_ENRICHMENT_TERMINAL_STATUSES = frozenset({"no_data", "unavailable"})


def _context_place_ids(context: RecommendationContext) -> list[str]:
    places = context.places
    return [place.place_id for place in (places.data or [])] if places is not None else []


# 실측 이동시간을 조회할 상위 후보 수. 1차 채점(직선거리)에서 이만큼만 추린다.
#
# **후보 전량에 실측을 붙이지 않는 이유는 호출이 후보 수에 정비례하기 때문이다.**
# `_fetch_travel_routes()`가 하드 필터 통과 후보 전부를 목적지로 만들고 Provider가
# 목적지마다 요청을 쏜다(walking_route.py의 asyncio.gather). 후보 상한을 30으로
# 올리자 카카오 호출이 7~13건에서 25~35건이 됐다. 결과에 나가는 것은 5곳뿐인데
# 나머지 몫까지 치르고 있었다.
#
# **노출 개수(5)로 맞추지 않는 이유가 이 상수의 핵심이다.** 5로 두면 1차가 고른
# 5곳이 그대로 최종이 되어, 실측은 표시 시간과 그 5곳 내부 순서만 바꾼다 — 누구를
# 보여줄지에는 관여하지 못한다. 10이면 직선 기준 6~10위가 실측으로 5위 안에 들어올
# 수 있다.
#
# 그 일이 실제로 일어나는지 재봤다(2026-08-31, 안국역·경복궁·홍대입구 x 14시·19시,
# 반경 2km). **6개 조합 중 3개에서 최종 5곳의 집합이 바뀌었다.** 안국역 14시는 5곳
# 중 3곳이 갈렸다(인사동·개성만두 궁·모인화랑이 들어오고 인사동 옥정·꽃,밥에피다·
# [백년가게] 선천집이 빠졌다).
#
# 직선거리와 실거리의 비율이 일정하지 않아서다 — 도심 우회 계수가 평균 1.31배,
# 범위 1.07~1.71이다(2026-08-20, 종로 6개 지점). 직선 350m인 두 곳이 실제로는
# 375m와 600m일 수 있다. 노출 5곳의 2배면 그 범위를 덮는다.
#
# 이 값이 노출 개수보다 작아지면 안 된다 — 2차 채점 대상이 노출 대상보다 적어진다.
_MEASURED_ROUTE_CANDIDATE_LIMIT = 10


def _missing_place_ids(
    recommendations: RecommendationResponse, place_ids: Sequence[str]
) -> list[str]:
    """채점 결과에 들어가지 못한 place_id만 순서대로 돌려준다."""

    if not place_ids:
        return []
    present = {
        item.place_id
        for item in (
            *recommendations.recommendations,
            *recommendations.unverified_recommendations,
        )
    }
    return [place_id for place_id in place_ids if place_id not in present]


def _with_pinned_recommendations(
    recommendations: RecommendationResponse,
    pinned: RecommendationResponse,
    keep_place_ids: Sequence[str],
) -> RecommendationResponse:
    """점수순 자르기에서 빠진 보관함 장소를 결과 뒤에 덧붙인다.

    **왜 뒤에 붙이나.** 순위를 왜곡하지 않기 위해서다. 이 목록의 순서는 화면
    노출 순서이자 편성 후보 순서이고, 보관함 장소는 검색 반경 밖이라 점수가
    낮은 것이 정상이다. 배치는 `must_include_place_ids`가 보장하므로 여기서
    순위를 올려줄 이유가 없다 — 필요한 것은 "후보 목록에 있기"뿐이다.

    `excluded_all_closed`·`excluded_closed_place_ids`는 본 채점 결과를 그대로
    둔다. 그 둘은 이번 회차 하드 필터 판정의 기록이라 덧붙이기와 무관하다.
    """

    keep = set(keep_place_ids)
    added = [
        item
        for item in (*pinned.recommendations, *pinned.unverified_recommendations)
        if item.place_id in keep
    ]
    if not added:
        return recommendations
    return recommendations.model_copy(
        update={
            "recommendations": [*recommendations.recommendations, *added],
        }
    )


def _narrow_prepared(
    prepared: PreparedRecommendationResult, place_ids: Sequence[str]
) -> PreparedRecommendationResult:
    """채점 대상을 주어진 후보로 좁힌다. 하드 필터 결과는 그대로 둔다.

    `excluded_candidates`를 손대지 않는 이유는 그것이 "왜 떨어졌나"의 기록이기
    때문이다 — 좁히는 것은 이번 채점에 넣을 대상이지 필터 판정이 아니다. A가
    `excluded_all_closed` 같은 신호를 그 기록으로 읽는다.
    """
    keep = set(place_ids)
    narrowed = tuple(
        candidate
        for candidate in prepared.preparation.eligible_candidates
        if candidate.candidate.place_id in keep
    )
    return replace(
        prepared,
        preparation=replace(prepared.preparation, eligible_candidates=narrowed),
    )


def _saved_taste_query(
    conditions: UserConditions,
    principal: Principal | None,
    store: StateStore | None,
) -> str | None:
    """계정에 저장해 둔 취향으로 근거 검색 질의를 만든다. 쓸 것이 없으면 None.

    **무엇을 질의에 넣을지는 D가 정한다**(`domain/saved_preference.py`) — 혼잡도가
    부딪히는 칩을 빼는 것, 발화에 동행이 있으면 동행 칩을 통째로 빼는 것이 거기 있다. 여기는
    읽어서 넘기는 배선이다. 돌려주는 값은 **발화를 뺀 저장 칩만**이라, 발화 질의와
    잇는 것은 provider가 한다(`real_recommendation_provider::_taste_matches_for`).

    발화에 취향이 있어도 조회한다. 발화가 정본이고 저장값은 뒤에 덧붙는 값이라,
    발화가 정하지 않은 축의 칩은 그대로 살아남는다.

    신원이 없으면(게스트) 저장할 자리가 없어 항상 None이다 — 취향은 세션이 아니라
    사람에게 붙는 값이라 `user_preferences`가 user_id를 필수로 잡는다
    (`state/preferences.py`).

    조회 실패는 추천을 막지 않는다. 취향은 순위를 다듬는 축이지 후보를 만드는
    축이 아니라서, 못 읽으면 저장값 없이 채점하는 편이 낫다 — 취향 근거 검색
    실패를 삼키는 것과 같은 이유다(`real_recommendation_provider`).

    **store가 없으면 전역 저장소로 대신한다**(2026-09-07). `/api/chat`·
    `/api/chat/stream`이 `run_agent()`를 부를 때 store를 안 넘겨 여기까지
    쭉 None으로 흘러왔다 — `get_session_context()`(state/service.py)와 같은
    `store or get_store()` 패턴이 없어서, 세션·GPS는 멀쩡한데 저장된 취향만
    프로덕션에서 한 번도 채점에 실리지 못했다(실사용 재현, 2026-09-07).

    **취향 스위치(`taste_evidence_enabled`)가 꺼져 있으면 읽지 않는다.** 이 값을
    쓰는 곳은 임베딩 검색뿐이라 꺼진 동안은 어차피 버려지는데, 읽기만 하는
    DB 왕복을 매 턴 치를 이유가 없다. 저장값 자체는 건드리지 않는다 — 켜면
    그대로 다시 실린다.
    """
    if principal is None or not settings.taste_evidence_enabled:
        return None
    store = store or get_store()
    try:
        chips = state_preferences.get_items(store, principal.user_id)
    except Exception:  # noqa: BLE001
        logger.warning("저장된 취향 조회 실패 — 저장값 없이 채점합니다.", exc_info=True)
        return None
    return saved_preference.to_taste_query(
        chips,
        concentration_intent=conditions.concentration_intent,
        companion=conditions.companion,
    )


async def _score_with_measured_routes(
    recommendation_provider: StagedRecommendationProvider,
    conditions: UserConditions,
    prepared: PreparedRecommendationResult,
    *,
    tool_context: RecommendationContext,
    travel_route_tool: TravelRouteToolProvider | None,
    recommendation_limit: int,
    llm: LLMProvider | None = None,
    # 계정에 저장해 둔 취향으로 만든 근거 검색 질의. 1차·2차 채점에 모두 넘긴다 —
    # 한쪽만 주면 취향으로 후보를 좁혀 놓고 최종 순위에서는 취향을 빼게 된다
    # (2026-08-20에 그 계열 사고가 있었다, SCORING_VERSION 1.4.0).
    saved_taste_query: str | None = None,
) -> RecommendationResponse:
    """직선거리로 한 번 줄 세운 뒤, 상위 후보에만 실측을 붙여 다시 채점한다.

    ``1차 채점(직선거리) → 상위 N곳 실측 조회 → 2차 채점(실측 반영)``

    집합이 바뀌는 게 아니라 순서가 바뀐다. 최종 정렬은 두 번 다 가중합 점수순이고,
    실측은 거리 Feature의 입력만 바꾼다.

    **2차 대상을 실측을 받은 후보로 한정하는 것이 핵심이다.** scoring의
    `_consistent_routes()`가 "후보 중 하나라도 실측이 없으면 전부 직선거리로
    채점한다"고 정해 두었기 때문에, 전체를 채점하면서 일부만 실측을 붙이면 실측이
    통째로 버려진다. 좁혀 두면 그 안에서는 전원이 실측을 가져 규칙을 만족한다.

    실측을 못 받으면(경로 Tool 없음·조회 실패) 1차 결과를 그대로 쓴다 — 같은
    후보를 실측 없이 두 번 채점할 이유가 없다.

    **이동수단은 요청 단위가 아니라 후보 단위로 정해진다(D-118).** 도보권 후보는
    도보로, 임계를 넘은 후보는 도보·대중교통을 둘 다 조회해 빠른 쪽으로 잰다
    (`_fetch_travel_routes()`). 한 순위표에 수단이 섞이지만 거리 점수의 시간
    예산이 측정 수단을 보지 않으므로 자는 하나로 유지된다.
    """
    if travel_route_tool is None:
        return await recommendation_provider.score_prepared(
            conditions,
            prepared,
            limit=recommendation_limit,
            saved_taste_query=saved_taste_query,
        )

    # 1차 — 실측 없이 직선거리로 줄을 세워 실측할 후보를 고른다.
    shortlist_limit = max(recommendation_limit, _MEASURED_ROUTE_CANDIDATE_LIMIT)
    first_pass = await recommendation_provider.score_prepared(
        conditions, prepared, limit=shortlist_limit, saved_taste_query=saved_taste_query
    )
    shortlist_ids = [
        item.place_id
        for item in (
            *first_pass.recommendations,
            *first_pass.unverified_recommendations,
        )
    ]
    if not shortlist_ids:
        return first_pass

    narrowed = _narrow_prepared(prepared, shortlist_ids)
    travel_routes = await _fetch_travel_routes(
        travel_route_tool,
        tool_context,
        narrowed,
        conditions,
        # 후보별 실측 수단을 일정과 같은 판정으로 정한다(TP-227). 안 넘기면 기존
        # 거리 규칙으로 돌아간다.
        llm=llm,
    )
    if not travel_routes:
        return await recommendation_provider.score_prepared(
            conditions,
            narrowed,
            limit=recommendation_limit,
            saved_taste_query=saved_taste_query,
        )

    # 2차 — 실측을 받은 후보끼리 다시 줄을 세운다.
    return await recommendation_provider.score_prepared(
        conditions,
        narrowed,
        travel_routes=travel_routes,
        limit=recommendation_limit,
        saved_taste_query=saved_taste_query,
    )


def _search_center_of(context: RecommendationContext) -> Coordinates | None:
    """첫 조회가 확정한 검색 기준점 좌표. 보충 조회에 그대로 넘긴다.

    없으면 None을 돌려주고, 그때 C는 예전처럼 위치를 다시 해석한다 — 보충이 아예
    못 돌게 만드는 것보다 한 번 더 해석하는 편이 낫다.
    """
    location = context.location
    if location is None or location.data is None:
        return None
    return location.data.location


async def _saved_places_context(
    tool_context: RecommendationContext,
    *,
    saved_places: Sequence[SavedPlaceItem],
    place_details_repository: PlaceDetailsReadRepository | None,
) -> RecommendationContext | None:
    """보관함 장소 중 이번 턴 후보에 없는 것만 담은 Context를 만든다. (SCHEDULE-12 후속)

    **왜 필요한가.** 한 턴의 후보 풀은 C가 이번 반경에서 모아온 것이 전부다
    (`recommendation_candidate_limit`개). 보관함 장소를 여기에 넣어주는 단계가
    없어서, 이전 턴에 담은 장소는 이번 수집분에 안 들어오면 후보가 되지 못하고
    `planner._resolve_must_include()`가 조용히 버린다 — D-114의 배치 보장이
    통째로 무력해진다. 같은 지역이어도 POI가 후보 상한보다 많으면 매 턴 다른
    후보가 뽑히므로 흔하게 재현된다(2026-09-01, 인사동 4곳 중 3곳 누락).

    **왜 후보 목록이 아니라 Context인가.** `schedule_candidates`에 직접 꽂으려면
    `RecommendationItem`의 score·feature_scores·recommendation_reason을 만들어
    내야 하는데, 그 값들이 편성 프롬프트에 그대로 들어가 LLM 판단을 왜곡한다.
    한 단계 앞에 넣으면 D가 정상 채점하므로 지어낼 값이 없다.

    좌표는 상세를 우선하고, 없으면 담을 때 찍어둔 스냅샷(`SavedPlaceItem`)으로
    메운다. 둘 다 없으면 거리 계산을 할 수 없어 넣지 않는다 — 호출부가
    `absent_saved_place_names`로 안내한다.

    상세 조회가 실패해도 편성을 막지 않는다. 주입을 포기하고 기존 후보로 진행하며,
    빠진 장소는 역시 안내로 나간다.
    """

    if not saved_places or place_details_repository is None:
        return None
    places = tool_context.places
    if places is None or places.data is None:
        # C가 이번 턴 후보를 아예 못 준 경우다. 주입해도 채점을 태울 자리가 없다.
        return None

    present = {place.place_id for place in places.data}
    missing = [item for item in saved_places if item.place_id not in present]
    if not missing:
        return None

    try:
        details = await place_details_repository.get_active_place_details(
            [item.place_id for item in missing]
        )
    except Exception:  # noqa: BLE001 - 주입 실패가 편성을 막으면 안 된다
        logger.warning("보관함 장소 상세 조회 실패 — 후보 주입을 건너뛴다", exc_info=True)
        return None

    injected: list[PlaceCandidate] = []
    for item in missing:
        detail = details.get(item.place_id)
        if detail is None:
            continue
        latitude = detail.latitude if detail.latitude is not None else item.latitude
        longitude = detail.longitude if detail.longitude is not None else item.longitude
        if latitude is None or longitude is None:
            continue
        injected.append(
            PlaceCandidate(
                place_id=item.place_id,
                name=detail.title or item.name,
                category=detail.content_type_id,
                lcls_systm1=detail.lcls_systm1,
                lcls_systm2=detail.lcls_systm2,
                lcls_systm3=detail.lcls_systm3,
                location=Coordinates(latitude=latitude, longitude=longitude),
                operating_hours_raw=detail.operating_hours_raw,
                rest_date_raw=detail.rest_date_raw,
            )
        )

    if not injected:
        return None
    return tool_context.model_copy(
        update={"places": places.model_copy(update={"data": injected})}
    )


def _merge_recommendation_context_places(
    first: RecommendationContext,
    additional: RecommendationContext,
) -> RecommendationContext:
    """후속 혼잡도·일정 계산이 쓸 수 있도록 C 후보 좌표를 ID 기준으로 합친다."""
    first_places = first.places
    additional_places = additional.places
    if first_places is None or additional_places is None:
        return first

    places_by_id = {place.place_id: place for place in (first_places.data or [])}
    for place in additional_places.data or []:
        places_by_id.setdefault(place.place_id, place)

    merged_places = first_places.model_copy(update={"data": list(places_by_id.values())})
    return first.model_copy(update={"places": merged_places})


def _narrow_recommendation_context_places(
    context: RecommendationContext, place_ids: Sequence[str]
) -> RecommendationContext | None:
    """후보 Context를 주어진 place_id만 남기고 좁힌다. 남는 게 없으면 None.

    자르기에서 밀린 보관함 장소만 다시 채점할 때 쓴다(TP-223). 주입 Context
    (`_saved_places_context()`)를 그대로 쓰면 **이번 턴 후보에 원래 들어 있던**
    보관함 장소는 거기 없어서 되붙일 수 없다 — 주입 대상이 "후보에 없는 것"뿐이기
    때문이다. 병합이 끝난 `tool_context`를 좁히면 주입분과 원래 후보를 함께 담는다.
    """

    places = context.places
    if places is None or not places.data:
        return None
    keep = set(place_ids)
    narrowed = [place for place in places.data if place.place_id in keep]
    if not narrowed:
        return None
    return context.model_copy(
        update={"places": places.model_copy(update={"data": narrowed})}
    )


def _candidate_pool_exhausted(context: RecommendationContext) -> bool:
    """이 반경에서 C가 더 줄 후보가 없는지 판정한다 — 참이면 보충 조회를 멈춘다.

    두 가지 신호를 본다.

    1. `candidate_pool_truncated` 경고 — C가 행 상한(100행)까지 받고도 요청한
       개수를 못 채웠다는 뜻이다(nearby_place_details.py).
    2. 반환 후보 수가 `recommendation_candidate_limit`보다 적음 — C는
       min(가용 후보, limit)을 반환하므로, limit보다 적게 왔다면 반경 안을 이미
       다 긁은 것이다. 1번 경고는 행 상한에 걸렸을 때만 서기 때문에, 반경에
       애초에 후보가 몇 개 없는 흔한 경우는 이 조건으로만 걸린다.

    **2번이 성립하려면 C가 실제로 limit을 채워줘야 한다.** 예전에는 C가 TourAPI에
    필요분만 요청하고 미지원 분류(숙박·여행코스)를 받은 뒤에 걸러내서, 반경에
    후보가 수백 곳 남아 있어도 늘 limit보다 적게 돌려줬다. 그래서 이 판정이 항상
    참이 되어 **보충 조회가 한 번도 돌지 않았다**(안국역 반경 2km 실측: TourAPI
    totalCount 364곳인데 10 요청에 9곳 반환 → 소진 판정). 지금은 C가
    `CANDIDATE_OVERFETCH_FACTOR`만큼 넉넉히 받아 채우므로 이 전제가 성립한다.
    C 쪽 과요청을 되돌리면 이 판정도 함께 무너진다.
    """
    places = context.places
    if places is None:
        return True
    if any(warning.code == _CANDIDATE_POOL_TRUNCATED_WARNING for warning in places.warnings):
        return True
    return len(places.data or []) < settings.recommendation_candidate_limit


Coordinate: TypeAlias = tuple[float, float]


def _place_coordinates(places: Sequence[PlaceCandidate]) -> dict[str, Coordinate]:
    """C가 준 후보의 위경도를 place_id로 찾을 수 있게 펼친다."""

    return {
        place.place_id: (place.location.latitude, place.location.longitude)
        for place in places
    }


def _snapshot_coordinates(session_context: SessionContextResponse) -> dict[str, Coordinate]:
    """B에 남은 추천 시점 좌표 스냅샷을 place_id로 찾을 수 있게 펼친다 (SCHEDULE-12).

    보관함에 담긴 장소는 여러 턴 전에 노출된 것일 수 있어, 이번 턴 C 응답에 아예
    없을 수 있다(검색 반경 밖). 그때 후보 간 거리를 계산할 유일한 근거가 이 값이다.

    보관함(`saved_places`)을 마지막 노출분(`shown_recommendations`)보다 나중에 넣어
    덮어쓰게 한다 — 둘의 값은 같은 스냅샷에서 나오지만, 보관함 쪽이 "사용자가
    명시적으로 고른 것"이라 우선순위를 명확히 해 둔다. 좌표가 없는 항목(이 필드
    도입 이전 세션, C 컨텍스트를 안 거친 기록)은 건너뛴다.
    """

    coordinates: dict[str, Coordinate] = {}
    for item in session_context.shown_recommendations:
        if item.latitude is not None and item.longitude is not None:
            coordinates[item.place_id] = (item.latitude, item.longitude)
    for saved in session_context.saved_places:
        if saved.latitude is not None and saved.longitude is not None:
            coordinates[saved.place_id] = (saved.latitude, saved.longitude)
    return coordinates


def _coordinate_of(
    place_id: str,
    primary: Mapping[str, Coordinate],
    fallback: Mapping[str, Coordinate],
    axis: int,
) -> float | None:
    """place_id의 위도(axis=0) 또는 경도(axis=1). 어디에도 없으면 None. (SCHEDULE-12)

    `_build_pairwise_distances_km()`과 같은 우선순위를 쓴다 — 이번 턴 C 응답이
    있으면 그쪽, 없으면 B에 남은 이전 스냅샷. 이력에 기록할 때 fallback을 함께
    보는 이유는, 한 번 확보한 좌표를 그 장소가 C 응답에서 빠진 턴에 잃지 않게
    하려는 것이다.
    """

    coordinate = primary.get(place_id) or fallback.get(place_id)
    return None if coordinate is None else coordinate[axis]


def _build_pairwise_distances_km(
    candidates: list[RecommendationItem],
    places: list[PlaceCandidate],
    *,
    fallback_coordinates: Mapping[str, Coordinate] | None = None,
) -> dict[tuple[str, str], float]:
    """SCHEDULE 전용 — 후보 쌍 사이의 직선거리(km)를 계산한다.

    RecommendationItem에는 위경도가 없다(distance_km는 검색 중심 기준 거리라
    후보 간 거리를 못 구한다) — C가 준 PlaceCandidate(위경도 보유)를 place_id로
    매칭해 haversine_km()로 계산한다(docs/design/int-07-schedule.md 6.1절).

    `fallback_coordinates`는 C 응답에 없는 place_id를 위한 B의 추천 시점 스냅샷이다
    (SCHEDULE-12, `_snapshot_coordinates()`). 보관함에 담긴 장소는 이번 턴 검색 반경
    밖일 수 있어 C 응답에 아예 없는데, 그대로 건너뛰면 LLM이 그 장소의 거리 근거
    없이 동선을 짠다 — 강남 장소가 종로 일정의 2번째에 꽂혀도 막을 수가 없다.
    C 응답이 있으면 그쪽을 우선한다: 최신값이고, 같은 턴 후보끼리 같은 출처를 쓰는
    편이 일관된다.

    양쪽 어디에도 없는 place_id는 여전히 조용히 건너뛴다 — pairwise_distances_km는
    LLM에 참고 근거로만 쓰이므로 일부 누락되어도 편성 자체가 막히지 않는다.
    """

    coordinates_by_place_id = dict(fallback_coordinates or {})
    coordinates_by_place_id.update(_place_coordinates(places))
    distances: dict[tuple[str, str], float] = {}
    for index, first in enumerate(candidates):
        first_location = coordinates_by_place_id.get(first.place_id)
        if first_location is None:
            continue
        for second in candidates[index + 1 :]:
            second_location = coordinates_by_place_id.get(second.place_id)
            if second_location is None:
                continue
            distances[(first.place_id, second.place_id)] = haversine_km(
                first_location[0],
                first_location[1],
                second_location[0],
                second_location[1],
            )
    return distances


def _segment_weather(tool_context: RecommendationContext) -> SegmentWeather | None:
    """C가 조회한 예보를 일정 구간 판정이 쓰는 사실로 옮긴다 (TP-226).

    `conditions.weather`(사용자가 발화에서 말한 값)가 아니라 조회한 예보를 쓴다 —
    비 오는 날 20분을 걷게 할지는 말한 적 없는 사용자에게도 판단해야 한다.

    판정(좋다/나쁘다)은 옮기지 않는다. D-051대로 사실만 넘기고, 그 사실을 어떻게
    읽을지는 판정하는 쪽이 정한다. `resolve_weather_condition()`이 만드는
    WeatherCondition을 쓰지 않는 것도 같은 이유다 — 그건 "이 날씨가 이 사용자
    목적에 맞는가"라는 다른 질문의 답이다.
    """

    weather = tool_context.weather
    if weather is None or weather.status not in {"success", "partial"}:
        return None
    data = weather.data
    if data is None:
        return None
    return SegmentWeather(
        precipitation=data.precipitation,
        sky=data.sky,
        temperature_celsius=data.temperature_celsius,
    )


def _build_travel_candidates(
    candidates: list[RecommendationItem],
    places: list[PlaceCandidate],
    *,
    fallback_coordinates: Mapping[str, Coordinate] | None = None,
) -> list[ScheduleTravelCandidate]:
    """SCHEDULE 전용 — 구간 이동정보 계산에 넘길 후보 좌표. (TP-216)

    좌표 우선순위는 `_build_pairwise_distances_km()`과 같다(이번 턴 C 응답 →
    B의 추천 시점 스냅샷). 두 함수가 같은 좌표를 봐야 LLM에 준 거리 근거와
    엔진이 계산한 이동시간이 어긋나지 않는다.

    좌표가 없는 place_id는 건너뛴다 — 그 구간은 Edge가 안 만들어지고 시간표가
    폴백값으로 메운다. 값을 지어내지 않는다.
    """

    coordinates_by_place_id = dict(fallback_coordinates or {})
    coordinates_by_place_id.update(_place_coordinates(places))
    result: list[ScheduleTravelCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.place_id in seen:
            # 같은 place_id가 두 번 오면 _candidate_index()가 ValueError를 낸다.
            continue
        location = coordinates_by_place_id.get(candidate.place_id)
        if location is None:
            continue
        seen.add(candidate.place_id)
        result.append(
            ScheduleTravelCandidate(
                place_id=candidate.place_id,
                coordinate=GeoCoordinate(latitude=location[0], longitude=location[1]),
            )
        )
    return result


def _valid_location(device_location: str | None) -> str | None:
    """'위도,경도' 형식이 아니면 None으로 낮춘다.

    잘못된 GPS 문자열이 파싱 예외로 대화를 중단시키지 않도록 한다.
    (interpret.py의 동일 함수와 중복 — interpret.py가 run_agent()로 교체되면 정리한다.)
    """
    if not device_location:
        return None
    parts = device_location.split(",")
    if len(parts) != 2:
        return None
    try:
        latitude = float(parts[0])
        longitude = float(parts[1])
    except ValueError:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return device_location


async def _apply_concentration_rerank(
    agent_conditions: UserConditions,
    tool_context: RecommendationContext,
    first_pass: RecommendationResponse,
    *,
    recommendation_provider: RecommendationProvider,
    enrichment_provider: EnrichmentProvider,
    final_limit: int | None = None,
    execution_collector: list[ToolExecutionDebug] | None = None,
) -> RecommendationResponse:
    """concentration_intent가 AVOID/SEEK일 때만 1차 결과를 혼잡도로 보강·재순위한다
    (D-040 확정 — concentration-conditions.md §2.2.3, agent-runtime-contract.md
    §6.5.2). 그 외에는 first_pass를 그대로 반환한다.

    final_limit: 재순위 후 최종 노출 개수. 호출부가 1차 Scoring에 넘긴 limit과
    일치시켜야 한다. RECOMMEND/MODIFY는 recommendation_result_limit,
    SCHEDULE은 SCHEDULE_RECOMMENDATION_LIMIT을 사용한다. None이면
    recommendation_result_limit을 이 시점에 읽는다.

    C 보강 조회(EnrichmentProvider.enrich())와 D의 2차 Scoring
    (rerank_with_concentration())은 모두 실제로 연결·구현 완료됐다(D-040). hasattr
    가드는 이제 "D가 아직 없을 수 있어서"가 아니라, 테스트 더블 등 이 메서드를
    갖추지 않은 구현체가 주입됐을 때도 안전하게 낮아지도록 남겨둔 방어 코드다.

    (2026-08-05, B-06 완료 — PR #78) B의 StateUserConditions에 concentration_intent
    필드가 등록되어, 이제 run_agent_flow() 전체 통합 테스트로도 이 분기가 실제
    트리거된다(test_concentration_intent_persisted_by_b_triggers_rerank 참고).
    run_agent_flow()에서 이 로직을 분리해둔 건 그 갭을 우회하기 위함이었지만,
    agent_conditions(A의 enum 타입 UserConditions)만으로 독립 단위 테스트가
    가능하다는 이점은 여전히 유효해 구조는 그대로 유지한다.
    """

    if agent_conditions.concentration_intent not in _CONCENTRATION_RANK_INTENTS:
        return first_pass

    has_places = tool_context.places and tool_context.places.data
    places = tool_context.places.data if has_places else []
    enrichment_request = to_candidate_enrichment_request(new_trace_id(), first_pass, places)
    if enrichment_request is None:
        return first_pass

    enrichment_started_at = time.monotonic()
    # **혼잡도 보강을 자기 span으로 뺀다.** 이 조회는 `scoring` 노드 안에서 일어나서,
    # 여기서 터져도 화면에는 "scoring이 죽었다"까지만 보였다. 2026-08-25에 SCHEDULE
    # 턴이 `보강 후보는 최대 5개` ValueError로 죽고 있던 걸 그렇게 놓쳤다 — 요청한
    # 후보 수가 span에 있었으면 원인이 바로 읽혔다.
    with observe_step("concentration_enrichment") as enrichment_step:
        enrichment_step.record(
            output={
                "requested": len(enrichment_request.candidates),
                "features": list(enrichment_request.features),
            }
        )
        enrichment_response = await enrichment_provider.enrich(enrichment_request)
        # **Audit용 요약을 span 안에서 만든다.** 밖에서 만들면 span이 이미 닫혀 있어
        # 후보별 출처를 붙일 자리가 없다. 지연은 위에서 재둔 시각으로 계산하므로
        # 자리를 옮겨도 값이 달라지지 않는다.
        enrichment_execution = build_candidate_enrichment_execution_debug(
            enrichment_response,
            latency_ms=int((time.monotonic() - enrichment_started_at) * 1000),
        )
        try:
            enrichment_step.record(
                output={
                    "requested": len(enrichment_request.candidates),
                    "features": list(enrichment_request.features),
                    "status": str(getattr(enrichment_response, "status", None)),
                    "enriched": len(getattr(enrichment_response, "candidates", None) or []),
                    # 성공 건수만으로는 직접 조회한 값과 인근에서 빌려온 값이
                    # 구분되지 않는다 — 후보별 출처를 그대로 남긴다.
                    "candidates": (
                        concentration_source_rows(enrichment_execution)
                        if enrichment_execution is not None
                        else []
                    ),
                },
                status_message=(
                    f"보강 {len(enrichment_request.candidates)}건 요청 · "
                    f"{getattr(enrichment_response, 'status', '?')}"
                ),
            )
        except Exception:
            logger.warning("보강 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
    if execution_collector is not None and enrichment_execution is not None:
        execution_collector.append(enrichment_execution)
    if enrichment_response.status in _ENRICHMENT_TERMINAL_STATUSES or not hasattr(
        recommendation_provider, "rerank_with_concentration"
    ):
        logger.info(
            "혼잡도 보강 조회는 성공했지만 D의 2차 Scoring이 아직 없어 1차 결과를 "
            "그대로 씀: request_id=%s enrichment_status=%s",
            enrichment_request.request_id,
            enrichment_response.status,
        )
        return first_pass

    reranked = await recommendation_provider.rerank_with_concentration(
        agent_conditions,
        tool_context,
        first_pass,
        enrichment_response,
    )
    shown = [*reranked.recommendations, *reranked.unverified_recommendations]
    resolved_final_limit = (
        final_limit if final_limit is not None else settings.recommendation_result_limit
    )
    return reranked.model_copy(
        update={
            "recommendations": shown[:resolved_final_limit],
            "unverified_recommendations": [],
        }
    )


async def _apply_co_visited_rerank(
    agent_conditions: UserConditions,
    tool_context: RecommendationContext,
    recommendations: RecommendationResponse,
    *,
    recommendation_provider: RecommendationProvider,
) -> RecommendationResponse:
    """(D-092) place_associations(B-owned, D-088) 기반 "함께 방문된 이력"으로
    2차 Scoring(재순위)을 한다. `_apply_concentration_rerank()`와 달리
    concentration_intent 게이트가 없다 — co-visit은 방향(seek/avoid) 개념이
    없는 사실 신호라 항상 켜져 있어도 무해하다(쌍이 없으면 co_visited feature
    점수가 0.0이라 순위에 영향이 없다, scoring.co_visited_score() 참고).

    `_apply_concentration_rerank()` 뒤에 이어 호출한다 — 혼잡도 2차를 이미
    탄 응답이면 그 결과 위에 co_visited 축만 추가로 얹는다(OPTIONAL_FEATURES가
    최대 3개까지 동시 활성을 지원하도록 설계돼 있다, scoring.py 참고).

    place_associations 조회는 B가 SCHEDULE에서 쓰는
    `app.schedule.associations.fetch_co_visited_hints()`를 그대로 재사용한다 —
    "이번 응답 후보 집합 안에서 서로 함께 방문된 쌍"을 구하는 로직이 SCHEDULE과
    동일하기 때문이다. 조회 실패는 여기서 삼키고 `recommendations`를 그대로
    반환한다 — 이 신호가 없어도 기존 추천 흐름은 그대로 동작해야 한다(SCHEDULE
    통합 때와 같은 opt-in 원칙, planner.py::_with_co_visited_hints 참고).
    """
    items = [*recommendations.recommendations, *recommendations.unverified_recommendations]
    place_ids = [item.place_id for item in items]
    if len(place_ids) < 2 or not hasattr(recommendation_provider, "rerank_with_co_visited"):
        return recommendations

    try:
        hints = await fetch_co_visited_hints(place_ids, settings)
    except Exception:  # noqa: BLE001
        logger.warning("co_visited 힌트 조회 실패 — 힌트 없이 계속합니다.", exc_info=True)
        return recommendations
    if not hints:
        return recommendations

    co_visited_pairs = [(hint.from_place_id, hint.to_place_id) for hint in hints]
    return await recommendation_provider.rerank_with_co_visited(
        agent_conditions,
        tool_context,
        recommendations,
        co_visited_pairs,
    )


@dataclass(frozen=True)
class _RecommendationModePlan:
    """후보별로 무엇을 재고, 판정이 무엇을 골랐는지. (TP-227)

    둘을 따로 담는 이유는 **규칙 경로와 판정 경로에서 뒤 단계가 달라야** 하기
    때문이다. 규칙으로 정한 `(도보, 대중교통)`은 D-118대로 빠른 쪽을 쓰지만,
    판정이 대중교통을 고른 경우는 도보가 더 빨라도 그 값을 쓴다. 재는 집합만
    보면 둘을 구분할 수 없다 — 모양이 같기 때문이다.
    """

    measure: dict[str, tuple[TravelMode, ...]]
    # 판정이 고른 수단. 규칙으로 정한 회차는 비어 있다.
    judged: dict[str, TravelMode]


async def _judge_recommendation_modes(
    candidates: Sequence[tuple[RouteDestination, float]],
    *,
    conditions: UserConditions | None,
    context: RecommendationContext,
    switch_threshold_km: float,
    llm: LLMProvider | None,
) -> _RecommendationModePlan:
    """후보별로 어떤 이동수단을 실측할지 정한다. (TP-227)

    일정과 **같은 판정**을 쓴다. 두 임계값이 환산 관계라(D-118) 한쪽만 다른 판정을
    쓰면 같은 거리를 두고 일정은 "타세요" 추천은 "걸으세요"라고 하게 된다.

    다만 후보는 서로 대안이라 `sequential=False`로 넘긴다 — 사용자는 그중 한 곳만
    가므로, 앞 후보를 근거로 삼으면 목록에서 몇 번째냐에 따라 같은 거리가 다르게
    판정된다.

    `llm`이 없거나 판정이 실패하면 기존 거리 규칙(`to_measured_travel_modes()`)으로
    되돌아간다. 되돌아간 사실은 관측에 남긴다 — 안 남기면 판정이 한 번도 안 도는
    채로 정상처럼 보인다.
    """

    def _by_rule() -> _RecommendationModePlan:
        # judged를 비워 둔다 — 뒤 단계가 D-118대로 빠른 쪽을 고르게 하려는 것이다.
        return _RecommendationModePlan(
            measure={
                destination.place_id: to_measured_travel_modes(
                    conditions,
                    straight_line_km=straight_line_km,
                    switch_threshold_km=switch_threshold_km,
                )
                for destination, straight_line_km in candidates
            },
            judged={},
        )

    transport = conditions.transport if conditions is not None else None
    if llm is None or transport in JUDGE_SKIPPED_TRANSPORTS:
        return _by_rule()

    segments = tuple(
        SegmentModeInput(
            # 출발지는 랭킹 기준점 하나뿐이라 후보 id로 구분한다.
            from_place_id="origin",
            to_place_id=destination.place_id,
            order=index,
            distance_m=round(straight_line_km * 1000),
            walk_minutes=round(
                straight_line_km / WALKING_SPEED_KM_PER_MINUTE, 1
            ),
        )
        for index, (destination, straight_line_km) in enumerate(candidates, start=1)
    )
    judgment_context = ModeJudgmentContext(
        transport=transport,
        companion=conditions.companion if conditions is not None else None,
        accessibility_needs=narrow_accessibility_needs(
            conditions.accessibility_needs if conditions is not None else ()
        ),
        weather=_segment_weather(context),
        sequential=False,
    )
    try:
        chosen = await select_modes_for_segments(
            segments,
            judgment_context,
            judge=LlmModeJudge(llm),
            walking_speed_mps=WALKING_SPEED_KM_PER_MINUTE * 1000 / 60,
            walk_transfer_threshold_min=settings.schedule_walk_transfer_threshold_min,
        )
    except (AppError, ValueError):
        # 일정 쪽(`schedule/travel.py::_resolve_edges`)과 같은 이유로 좁게 잡는다 —
        # 없는 메서드(AttributeError)는 배선이 끊긴 것이라 시끄럽게 터져야 한다.
        logger.warning("recommend_travel.mode_judge_failed", exc_info=True)
        record_score("recommend_mode_judge_fallback", 1.0)
        return _by_rule()

    return _RecommendationModePlan(
        measure={
            segment.to_place_id: modes_for_judged_choice(conditions, chosen[segment.key])
            for segment in segments
        },
        judged={segment.to_place_id: chosen[segment.key] for segment in segments},
    )


async def _fetch_travel_routes(
    route_tool: TravelRouteToolProvider | None,
    context: RecommendationContext,
    prepared: PreparedRecommendationResult,
    conditions: UserConditions | None = None,
    llm: LLMProvider | None = None,
) -> tuple[TravelRoute, ...]:
    """하드 필터 통과 후보를 후보별 이동수단으로 실측하고 D에 넘길 결과를 만든다.

    수단은 `to_measured_travel_modes()`가 후보의 직선거리로 고른다(D-118). 임계를
    넘은 후보는 도보와 대중교통을 **둘 다** 조회하고 `_fastest_routes()`가 빠른
    쪽을 남긴다 — 카카오 대중교통이 근거리에서 도보보다 느린 값을 주는 경우가
    있어서(2026-09-02 실측), 전환했다는 이유만으로 느린 값을 쓰지 않게 한다.

    이동수단별로 Tool을 한 번씩 부른다. `TravelRouteQuery`가 수단 하나를 받기
    때문이고, 세 수단을 팬아웃하는 `_fetch_compare_travel_routes()`와 같은 방식이다.
    두 수단을 동시에 쏘는 것이 안전한 이유는 카카오 Provider들이 세마포어를
    공유하기 때문이다(`factory.get_travel_route_tool()`) — 공유하지 않으면 같은
    키로 동시 10건이 나가 대부분 거절당한다.
    """
    if route_tool is None or context.location is None or context.places is None:
        return ()
    resolved_location = context.location.data
    places = context.places.data
    if resolved_location is None or not places:
        return ()

    eligible_ids = {item.candidate.place_id for item in prepared.preparation.eligible_candidates}

    # 실측 경로도 거리 계산과 같은 기준점에서 잰다 — 한쪽만 사용자 기준이면
    # 실측이 있는 후보와 없는 후보가 서로 다른 자로 채점된다(TP-112).
    origin = (resolve_ranking_origin(context, conditions) or resolved_location).location
    switch_threshold_km = transit_switch_straight_line_km(
        settings.schedule_walk_transfer_threshold_min
    )

    # 후보별 직선거리를 먼저 모은다. 판정을 후보 루프 안에서 부르면 후보 수만큼
    # LLM을 부르게 되고, 그건 일정 쪽에서 구간별 호출을 피한 이유와 같다(TP-227).
    candidates: list[tuple[RouteDestination, float]] = []
    for place in places:
        if place.place_id not in eligible_ids:
            continue
        candidates.append(
            (
                RouteDestination(
                    place_id=place.place_id,
                    coordinate=GeoCoordinate(
                        latitude=place.location.latitude,
                        longitude=place.location.longitude,
                    ),
                ),
                haversine_km(
                    origin.latitude,
                    origin.longitude,
                    place.location.latitude,
                    place.location.longitude,
                ),
            )
        )
    if not candidates:
        return ()

    plan = await _judge_recommendation_modes(
        candidates,
        conditions=conditions,
        context=context,
        switch_threshold_km=switch_threshold_km,
        llm=llm,
    )

    destinations_by_mode: dict[TravelMode, list[RouteDestination]] = {}
    for destination, _ in candidates:
        for mode in plan.measure[destination.place_id]:
            destinations_by_mode.setdefault(mode, []).append(destination)
    if not destinations_by_mode:
        return ()

    origin_coordinate = GeoCoordinate(latitude=origin.latitude, longitude=origin.longitude)
    results = await asyncio.gather(
        *(
            route_tool.execute(
                TravelRouteQuery(
                    origin=origin_coordinate,
                    destinations=tuple(destinations),
                    mode=mode,
                )
            )
            for mode, destinations in destinations_by_mode.items()
        )
    )
    return _fastest_routes(
        (route for result in results for route in result.routes), plan.judged
    )


def _fastest_routes(
    routes: Iterable[TravelRoute],
    judged_modes: Mapping[str, TravelMode] | None = None,
) -> tuple[TravelRoute, ...]:
    """한 후보를 두 수단으로 조회했을 때 어느 값을 채점에 넘길지 고른다.

    **실측이 추정을 이긴다. 소요시간 비교는 그 다음이다.** 도보 조회에는 직선거리
    추정 fallback이 붙어 있어(`factory.get_travel_route_tool()`) 실패해도 SUCCESS로
    돌아오는데, 그 추정값은 실제 대중교통 실측보다 짧게 나오기 쉽다. 시간만 보고
    고르면 추정이 이기고, 채점은 추정을 버리므로(`scoring._applied_travel_route()`)
    후보 하나가 실측을 잃고 그 때문에 `_consistent_routes()`가 회차 전체를
    직선거리로 내린다 — 대중교통을 부른 값을 그대로 버리는 셈이다.

    실측이 하나도 없으면 성공한 것 중 하나를, 그것도 없으면 처음 것을 남긴다.
    소비 측이 실패를 볼 수 있어야 하므로 후보를 통째로 빼지는 않는다.
    """

    best: dict[str, TravelRoute] = {}
    judged = judged_modes or {}
    for route in routes:
        current = best.get(route.place_id)
        preferred = judged.get(route.place_id)
        if current is None or _route_priority(route, preferred) < _route_priority(
            current, preferred
        ):
            best[route.place_id] = route
    return tuple(best.values())


def _route_priority(
    route: TravelRoute, preferred_mode: TravelMode | None = None
) -> tuple[int, int, int]:
    """작을수록 채점에 쓰기 좋은 경로. (실측 등급, 판정 일치, 소요시간)으로 비교한다.

    **실측 등급이 여전히 맨 앞이다.** 판정이 대중교통을 골랐어도 추정 도보가 실측
    대중교통을 이기면 안 된다 — 채점이 추정을 버리므로(`_applied_travel_route()`)
    후보 하나가 실측을 잃고, `_consistent_routes()`가 회차 전체를 직선거리로 내린다.

    판정 일치는 그 다음이다(TP-227). 판정이 "이 사람에겐 대중교통"이라고 봤으면
    도보가 더 빨라도 그 값을 쓴다 — 유모차 사용자에게 "걸어서 10분"은 사실 10분이
    아니고, 그 숫자를 보여주면 일정이 "대중교통 20분"이라고 말하는 것과 어긋난다.

    **판정이 없으면(규칙 경로) 이 자리가 0으로 같아져 예전처럼 소요시간이 정한다** —
    D-118의 "양쪽 조회 후 빠른 쪽"이 그대로 유지된다.
    """

    matches_judgment = 0 if preferred_mode is None or route.mode is preferred_mode else 1
    measured = (
        route.status is RouteStatus.SUCCESS
        and route.source in MEASURED_ROUTE_SOURCES
        and route.duration_seconds is not None
    )
    if measured:
        assert route.duration_seconds is not None
        return (0, matches_judgment, route.duration_seconds)
    if route.status is RouteStatus.SUCCESS and route.duration_seconds is not None:
        return (1, matches_judgment, route.duration_seconds)
    return (2, matches_judgment, 0)


def _is_info_walking_time_request(llm_output: LLMOutput) -> bool:
    """INFO location_info 중 실제 도보 소요 시간을 물은 경우만 경로를 조회한다."""

    info = llm_output.info
    if info is None or info.question_type is not QuestionType.LOCATION_INFO:
        return False
    normalized_question = (info.specific_question or "").replace(" ", "")
    return any(marker in normalized_question for marker in _INFO_WALKING_TIME_MARKERS)


# 근처 주차장(area 응답, 목록이 짧다)과 공영주차장(구 전체, 목록이 길지만 멀 수도
# 있다)은 서로 짝이다. 둘 다 실시간 주차 질문의 다른 절반이라, 한쪽을 물으면
# 다른 쪽 InfoContextRequest.question_type을 여기서 얻어 이어서 조회한다.
_PARKING_QUESTION_TYPE_PAIRS: dict[QuestionType, str] = {
    QuestionType.REALTIME_PARKING: QuestionType.REALTIME_PUBLIC_PARKING.value,
    QuestionType.REALTIME_PUBLIC_PARKING: QuestionType.REALTIME_PARKING.value,
}


def _paired_parking_question_type(info: InfoPayload | None) -> str | None:
    if info is None:
        return None
    return _PARKING_QUESTION_TYPE_PAIRS.get(info.question_type)


# 서울시 실시간 도시데이터(인구 121목록 공용)와 실시간 상권(82목록)이 각각 폐쇄된
# 지원 지역 목록을 쓴다(seoul_realtime_areas.py) — 두 목록 다 좌표 최근접 1곳만
# 보고 그 밖이면 no_data로 끝나, "그 지역엔 없음 → 다른 지역은?"을 되묻기 없이
# 스스로 해볼 경로가 없었다(로드맵 24번). 대상은 이 6종 — 상권은
# agent_context.service._REALTIME_CITYDATA_QUESTION_TYPES에 없지만 같은 문제라
# 여기 합쳐 다룬다.
_AGENTIC_REALTIME_QUESTION_TYPES = frozenset(
    {
        QuestionType.REALTIME_PARKING.value,
        QuestionType.REALTIME_PUBLIC_PARKING.value,
        QuestionType.REALTIME_SUBWAY.value,
        QuestionType.REALTIME_BUS.value,
        QuestionType.REALTIME_EVENT.value,
        QuestionType.REALTIME_TRAFFIC.value,
        QuestionType.REALTIME_COMMERCIAL.value,
    }
)

# LLM이 다른 지역명으로 재시도할 수 있는 최대 횟수(자동 함수 호출 상한). 90강
# 04절의 반복 한계와 같은 안전장치 — 무한정 넓히면 엉뚱한 지역까지 뒤진다.
_AGENTIC_REALTIME_MAX_TOOL_CALLS = 3


def _describe_realtime_attempt(response: InfoContextResponse, area_name: str) -> str:
    """C 응답 하나를 LLM이 읽고 판단할 수 있는 한국어 문장으로 편다.

    LLM이 이 문장을 보고 "이 지역엔 있다/없다"를 판단해 다음 행동(다른 지역
    재시도 또는 최종 답변 작성)을 정하므로, 성공 시엔 실제 값을 담고 실패 시엔
    사유를 담는다 — 24강 04절의 "예외 대신 안내 문자열" 원칙과 같다.
    """

    if response.status == "needs_clarification":
        # place_ambiguous — "강남"처럼 넓은 지명이라 후보가 여럿이다. 여기서 곧장
        # 사용자에게 되묻지 않는다 — 후보를 그대로 LLM에게 보여줘서 그중 하나로
        # 스스로 재시도하게 한다(agent_runtime.py의 기존 place_ambiguous 되묻기는
        # LLM이 끝내 못 고를 때만 최후 수단으로 살아 있다).
        candidates = response.clarification.candidates if response.clarification else []
        if candidates:
            names = ", ".join(candidates[:5])
            return (
                f"'{area_name}'은(는) 범위가 넓어 여러 곳으로 해석돼요. 후보: {names}. "
                "이 중 사용자 질문과 가장 관련 있어 보이는 곳으로 다시 조회해보세요."
            )
        return f"'{area_name}'가 정확히 어디인지 확인하지 못했어요."
    result = response.result
    if response.status != "success" or result is None:
        return f"'{area_name}'에서는 관련 정보를 찾지 못했어요."
    if isinstance(result, RealtimeCityInfoResult):
        if not result.fields:
            return f"'{area_name}'에서는 관련 정보를 찾지 못했어요."
        details = "; ".join(f"{key}: {value}" for key, value in result.fields.items())
        return f"'{area_name}'({result.area_name}) 기준 정보: {details}"
    if isinstance(result, RealtimeCommercialInfoResult):
        parts = [
            part
            for part in (
                f"업종: {result.category_label}" if result.category_label else None,
                f"상권 활동 수준: {result.commercial_level}" if result.commercial_level else None,
                (
                    f"인구 혼잡도: {result.population_current_level}"
                    if result.population_current_level
                    else None
                ),
            )
            if part is not None
        ]
        if not parts:
            return f"'{area_name}'에서는 관련 정보를 찾지 못했어요."
        return f"'{area_name}'({result.area_name}) 기준 상권 정보: " + "; ".join(parts)
    return f"'{area_name}'에서는 관련 정보를 찾지 못했어요."


async def _filter_review_evidence(
    info_response: InfoContextResponse,
    *,
    info_request: InfoContextRequest,
    llm: LLMProvider,
) -> InfoContextResponse:
    """후기 후보 중 그 장소 이야기이면서 질문에 답이 되는 것만 남긴다.

    **C가 아니라 여기서 한다.** C는 Tool·저장소 계층이라 생성 모델을 부르지 않고,
    선별 결과가 답변과 화면의 출처 둘 다를 정하므로 두 소비자보다 앞에 있어야 한다.

    **유사도로는 대신할 수 없다.** 검색이 찾아온 문장에는 근처 가게 후기가 섞여
    있는데(`docs/근거-장소연결-오염-점검-20260914.md`: 경복궁은 근거의 약 2/3),
    실측에서 답할 수 있는 질문과 없는 질문의 유사도가 겹쳐 컷으로 갈리지 않았다.

    선별이 실패하면 근거를 버린다. 못 찾았다고 말하는 편이, 검토되지 않은 남
    이야기로 답을 만드는 것보다 낫다.
    """
    result = info_response.result
    if (
        not isinstance(result, PlaceInfoResult)
        or result.question_type != "review_opinion"
        or not result.review_evidence
    ):
        return info_response

    place_name = result.resolved_place_name or result.requested_place_name or ""
    try:
        selection = await llm.filter_review_evidence(
            place_name=place_name,
            specific_question=info_request.specific_question or "",
            snippets=[item.text for item in result.review_evidence],
        )
        kept_indexes = selection.data
    except Exception:
        logger.exception("후기 근거 선별 실패 — 근거 없이 답한다")
        kept_indexes = ()

    kept = tuple(
        result.review_evidence[index - 1]
        for index in kept_indexes
        if 1 <= index <= len(result.review_evidence)
    )
    status: Literal["success", "no_data"] = "success" if kept else "no_data"
    logger.info(
        "후기 근거 선별: 장소=%s 후보=%d건 → 채택=%d건",
        place_name,
        len(result.review_evidence),
        len(kept),
    )
    return info_response.model_copy(
        update={
            "status": status,
            "result": result.model_copy(
                update={"review_evidence": kept, "status": status}
            ),
        }
    )


async def _fetch_realtime_info_agentic(
    info_request: InfoContextRequest,
    *,
    llm: LLMProvider,
    tool_provider: ToolProvider,
) -> tuple[InfoContextResponse, str]:
    """로드맵 24번: no_data를 곧장 되묻는 대신, LLM이 스스로 다른 지역명으로
    재조회해보고 최종 답변 문장까지 직접 쓰게 한다(강의교재 90강 자기 교정 에이전트).

    C(`fetch_info_context`)는 손대지 않는다 — A가 place_name만 바꿔가며 여러 번
    부른다. 지역 해석·서울시 폐쇄목록 매칭·카드 포맷팅은 기존 로직을 그대로
    재사용한다. 최대 호출 수는 `answer_with_tools`의 SDK 자동 함수 호출이 막는다
    (`_AGENTIC_REALTIME_MAX_TOOL_CALLS`) — 전부 실패하면 마지막 시도의 no_data
    응답을 그대로 최종 응답으로 쓴다.
    """

    attempts: list[InfoContextResponse] = []

    async def _try_area(area_name: str, *, question_type: str) -> InfoContextResponse:
        # request_id도 새로 발급한다 — 원래 요청 것을 그대로 재사용하면 두 번째
        # 시도부터 트레이스 기록이 같은 id로 충돌한다(실측: 재시도가 매번 "시스템
        # 오류"로 실패).
        candidate_request = info_request.model_copy(
            update={
                "request_id": new_trace_id(),
                "place_name": area_name,
                "place_context": "explicit",
                "question_type": question_type,
            }
        )
        response = await tool_provider.fetch_info_context(candidate_request)
        attempts.append(response)
        return response

    async def get_realtime_population_data(area_name: str) -> str:
        """서울시 실시간 도시데이터(주차·지하철·버스·행사·도로소통) 지원 지역
        한 곳을 조회한다.

        area_name: 정확한 지역명(예: '강남역', '홍대입구역'). 지원 지역이 아니면
        그 사실만 알려주니, 원래 질문과 관련된 다른 근처 지역명으로 다시
        시도해본다."""

        response = await _try_area(area_name, question_type=info_request.question_type)
        return _describe_realtime_attempt(response, area_name)

    async def get_realtime_commercial_data(area_name: str) -> str:
        """서울시 실시간 상권현황(업종별 소비 활동 수준) 지원 지역 한 곳을
        조회한다.

        area_name: 정확한 지역명. 지원 지역이 아니면 그 사실만 알려준다."""

        response = await _try_area(area_name, question_type="realtime_commercial")
        return _describe_realtime_attempt(response, area_name)

    # google-genai의 automatic function calling이 파이썬 함수 하나하나를 스키마로
    # 바꿀 때, 파일 전체에 걸린 `from __future__ import annotations` 때문에
    # `__annotations__`가 실제 타입이 아니라 문자열("str")로 남아 있으면
    # `isinstance(값, "str")`을 그대로 시도해 TypeError로 죽는다(실측: 두 도구
    # 호출 모두 "isinstance() arg 2 must be a type..."로 실패하고, LLM은 그 실패를
    # "시스템 오류"로 뭉뚱그려 답했다). `get_type_hints()`로 실제 타입 객체를
    # 되돌려 넣어야 한다.
    get_realtime_population_data.__annotations__ = get_type_hints(get_realtime_population_data)
    get_realtime_commercial_data.__annotations__ = get_type_hints(get_realtime_commercial_data)

    original_place_name = info_request.place_name or "요청하신 지역"
    # TODO(팀 리뷰 후): 다른 답변 생성 프롬프트처럼 Markdown 파일 + meta.yaml로 정식
    # 프롬프트 자산화한다. 지금은 실험 단계(기본 off, 아직 develop에 안 올림)라
    # 인라인 문자열로 둔다. 페르소나·엄격한 문장 수·마크다운 전면 금지를 한 번
    # 넣어봤는데 답이 고객센터 템플릿처럼 딱딱해져서(실사용 확인, 2026-09-03) 뺐다 —
    # 자유롭게 서술하게 두는 편이 이 에이전트다운 답을 만든다.
    instruction = (
        "너는 서울 실시간 정보 안내 비서다. 사용자가 실시간 정보를 물었지만 "
        f"'{original_place_name}' 지역엔 데이터가 없거나, 범위가 넓어 여러 후보로 "
        "해석될 수 있다. 가진 도구로 조회해보고, 없으면 근처의 잘 알려진 다른 "
        "지역명으로 다시 시도해봐라. 도구가 '여러 곳으로 해석된다'며 후보 목록을 "
        "주면, 사용자에게 되묻지 말고 그중 질문과 가장 관련 있어 보이는 곳(보통 "
        "대표 지하철역이나 랜드마크)을 스스로 골라 다시 조회해봐라.\n"
        "도구 결과로 확인되지 않은 사실은 지어내지 말고, 찾은 지역이 원래 물어본 "
        "곳과 다르면 그 사실을 답변에 자연스럽게 밝혀라. 도구 결과에 있는 구체적인 "
        "수치·이름·거리·시간은 최대한 활용해서 친절하게 설명하고, 강조하고 싶은 "
        "부분엔 **굵게**나 목록처럼 마크다운을 적절히 써도 좋다. 목록은 중첩하지 "
        "말고 각 항목을 '이름: 값' 형태로 한 줄에 담아라. 아무 데서도 못 찾았으면 "
        "그렇다고 솔직히 답하되, 빈 답변으로 끝내지는 마라.\n"
        f"사용자 질문: {info_request.specific_question or '해당 지역의 실시간 정보를 알려줘'}"
    )
    result = await llm.answer_with_tools(
        instruction,
        tools=[get_realtime_population_data, get_realtime_commercial_data],
        max_tool_calls=_AGENTIC_REALTIME_MAX_TOOL_CALLS,
    )
    successful = next(
        (response for response in reversed(attempts) if response.status == "success"), None
    )
    if successful is not None:
        final_response = successful
    elif attempts:
        final_response = attempts[-1]
    else:
        # 도구가 한 번도 호출되지 않았다(LLM이 곧장 "모른다"고 답한 경우) — 원래
        # 지역으로라도 조회해 기존 no_data 응답 형태를 유지한다.
        final_response = await tool_provider.fetch_info_context(info_request)
    agent_message = result.data.strip()
    if not agent_message:
        # 실측: 도구를 한 번 불러보고 no_data를 받은 뒤 최종 문장 없이 빈 텍스트로
        # 끝내는 경우가 있다(자동 함수 호출 + 빈 마무리 턴). 빈 말풍선을 보내는
        # 대신 사람이 읽기 좋은 형태로 다시 포맷한 안내 문장을 채운다.
        fallback_area_name = getattr(final_response.result, "area_name", None)
        agent_message = _format_realtime_result_for_user(
            final_response, fallback_area_name or original_place_name
        )
    return final_response, agent_message


def _format_realtime_result_for_user(response: InfoContextResponse, area_name: str) -> str:
    """LLM이 최종 문장을 안 써줬을 때 쓰는 안전장치 문구를 사람이 읽기 좋게 만든다.

    `_describe_realtime_attempt()`는 LLM이 다음 행동을 판단하는 내부용이라 대괄호·
    가운뎃점 같은 축약 표기를 쓴다 — 그걸 사용자에게 그대로 보내면 다듬어지지 않은
    느낌을 준다(실사용 확인, 2026-09-03). 이 함수는 같은 데이터를 문장·목록으로
    다시 풀어 쓴다.
    """

    result = response.result
    if response.status != "success" or result is None:
        return (
            f"{area_name} 근처에서는 관련 정보를 찾지 못했어요. 다른 지역이나 "
            "표현으로 다시 물어봐 주시면 다시 찾아볼게요."
        )
    if isinstance(result, RealtimeCityInfoResult) and result.fields:
        items = "\n".join(f"- {name}: {detail}" for name, detail in result.fields.items())
        return f"{area_name} 근처 정보를 확인했어요.\n\n{items}"
    if isinstance(result, RealtimeCommercialInfoResult):
        parts = [
            part
            for part in (
                f"업종은 {result.category_label}" if result.category_label else None,
                f"상권 활동 수준은 {result.commercial_level}" if result.commercial_level else None,
                (
                    f"인구 혼잡도는 {result.population_current_level}"
                    if result.population_current_level
                    else None
                ),
            )
            if part is not None
        ]
        if parts:
            return f"{area_name} 근처 상권 정보를 확인했어요. " + ", ".join(parts) + "예요."
    return (
        f"{area_name} 근처에서는 관련 정보를 찾지 못했어요. 다른 지역이나 표현으로 "
        "다시 물어봐 주시면 다시 찾아볼게요."
    )


def _to_geo_coordinate(location: str | None) -> GeoCoordinate | None:
    """B에 저장된 ``위도,경도`` 문자열을 도보 경로 도메인 값으로 변환한다."""

    if location is None:
        return None
    parts = location.split(",")
    if len(parts) != 2:
        return None
    try:
        return GeoCoordinate(latitude=float(parts[0]), longitude=float(parts[1]))
    except (TypeError, ValueError):
        return None


async def _fetch_info_walking_route(
    route_tool: TravelRouteToolProvider | None,
    *,
    origin_location: str | None,
    info_response: InfoContextResponse,
) -> TravelRoute | None:
    """INFO가 해석한 한 장소까지의 실제 도보 경로를 안전하게 조회한다.

    경로 장애가 주소/상세 정보 응답 전체를 실패시키면 안 되므로, 실패·누락은 None으로
    낮춘다. `TravelRouteTool` 내부의 Real→직선거리 추정 fallback은 그대로 사용한다.
    """

    if route_tool is None:
        return None
    result = info_response.result
    if not isinstance(result, PlaceInfoResult):
        return None
    if result.place_id is None or result.destination_coordinates is None:
        return None
    origin = _to_geo_coordinate(origin_location)
    if origin is None:
        return None

    try:
        route_result = await route_tool.execute(
            TravelRouteQuery(
                origin=origin,
                destinations=(
                    RouteDestination(
                        place_id=result.place_id,
                        coordinate=GeoCoordinate(
                            latitude=result.destination_coordinates.latitude,
                            longitude=result.destination_coordinates.longitude,
                        ),
                    ),
                ),
                mode=TravelMode.WALKING,
            )
        )
    except AppError:
        logger.warning("INFO 도보 경로 조회 실패", exc_info=True)
        return None

    return next(
        (
            route
            for route in route_result.routes
            if route.place_id == result.place_id and route.status is RouteStatus.SUCCESS
        ),
        None,
    )


# TravelMode → ComparisonItem의 어느 필드에 채울지. "덜 막힐까" 등 실시간 정체는
# 아직 반영하지 못하므로 이 세 값은 정체 미반영 실측이다(criteria_rules.md에 안내).
_COMPARE_TRAVEL_TIME_FIELDS: dict[TravelMode, str] = {
    TravelMode.WALKING: "travel_walking_minutes",
    TravelMode.DRIVING: "travel_driving_minutes",
    TravelMode.TRANSIT: "travel_transit_minutes",
}
# 대표 거리로 쓸 우선순위 — 자동차 경로가 도로 기준이라 "실제로 얼마나 떨어져
# 있는지"에 가장 가깝다고 보고, 조회 실패 시 도보·대중교통 순으로 대체한다.
_COMPARE_DISTANCE_MODE_PRIORITY = (TravelMode.DRIVING, TravelMode.WALKING, TravelMode.TRANSIT)


async def _fetch_compare_travel_routes(
    route_tool: TravelRouteToolProvider | None,
    *,
    origin_location: str | None,
    comparison: ComparisonResult,
) -> ComparisonResult:
    """COMPARE의 TRAVEL_TIME 기준일 때 도보·자동차·대중교통 세 경로를 모두 실측한다.

    C는 좌표(item.latitude/longitude)만 사실대로 전달했을 뿐 우열을 매기지
    않는다(agent_context/service.py 참고) — 여기서 A가 실측을 붙인다. 대상은
    보통 2~3곳뿐이라 세 수단을 병렬로 조회해도 부담이 적다(_fetch_travel_routes와
    달리 "하드 필터 통과 후보 전체"가 아니라 "이미 비교 대상으로 확정된 소수").

    사용자 조건(transport)으로 한 수단만 고르지 않는다 — "도보/자차/대중교통으로
    얼마나 걸리는지"를 한 번에 보여줘야 사용자가 자기 상황에 맞는 수단을 고를 수
    있다. 수단 하나가 provider 미설정·경로 장애로 실패해도 나머지 수단·item은
    영향받지 않는다(response_composer가 None인 수단은 안내에서 뺀다).
    """

    if comparison.criteria is not CompareCriteria.TRAVEL_TIME or route_tool is None:
        return comparison

    origin = _to_geo_coordinate(origin_location)
    if origin is None:
        return comparison

    destinations = tuple(
        RouteDestination(
            place_id=item.place_id,
            coordinate=GeoCoordinate(latitude=item.latitude, longitude=item.longitude),
        )
        for item in comparison.items
        if item.latitude is not None and item.longitude is not None
    )
    if not destinations:
        return comparison

    async def _fetch_one_mode(mode: TravelMode) -> tuple[TravelMode, tuple[TravelRoute, ...]]:
        try:
            result = await route_tool.execute(
                TravelRouteQuery(origin=origin, destinations=destinations, mode=mode)
            )
        except AppError:
            logger.warning("COMPARE 이동시간 실측 조회 실패: mode=%s", mode.value, exc_info=True)
            return mode, ()
        return mode, result.routes

    mode_results = await asyncio.gather(
        *(_fetch_one_mode(mode) for mode in _COMPARE_TRAVEL_TIME_FIELDS)
    )
    routes_by_mode: dict[TravelMode, dict[str, TravelRoute]] = {
        mode: {route.place_id: route for route in routes if route.status is RouteStatus.SUCCESS}
        for mode, routes in mode_results
    }
    if not any(routes_by_mode.values()):
        return comparison

    def _update_item(item: ComparisonItem) -> ComparisonItem:
        updates: dict[str, object] = {}
        for mode, field in _COMPARE_TRAVEL_TIME_FIELDS.items():
            route = routes_by_mode.get(mode, {}).get(item.place_id)
            if route is not None and route.duration_seconds is not None:
                updates[field] = round(route.duration_seconds / 60)
        for mode in _COMPARE_DISTANCE_MODE_PRIORITY:
            route = routes_by_mode.get(mode, {}).get(item.place_id)
            if route is not None and route.distance_m is not None:
                updates["travel_distance_km"] = round(route.distance_m / 1000, 2)
                break
        return item.model_copy(update=updates) if updates else item

    updated_items = [_update_item(item) for item in comparison.items]
    return comparison.model_copy(update={"items": updated_items})


def _failure_attributes(error: BaseException) -> dict[str, object]:
    """실패한 턴을 `agent_turn` span에 적을 모양으로 편다.

    **`level`·`status_message`는 mask를 타지 않는다.** 오류 코드가 `capture_content`
    스위치에 걸리면 원문 수집을 끈 배포에서 "무엇이 터졌나"를 화면에서 못 읽는데,
    그건 이 관측이 있는 이유 자체다. 그래서 코드는 두 자리 모두에 적는다.

    `AppError`는 우리가 의도해서 만든 오류라 코드·재시도 가능 여부가 계약으로
    정해져 있다(`errors.py`). 그 밖의 예외는 **클래스 이름만** 적는다 — 메시지는
    싣지 않는다. 어디서 터졌느냐에 따라 발화나 좌표가 섞여 들어올 수 있는데
    `status_message`는 스위치와 무관하게 나가는 자리다.
    """

    if isinstance(error, AppError):
        return {
            "level": "ERROR",
            "status_message": f"{error.code} · retryable={error.retryable}",
            "output": {
                "error_code": error.code,
                "retryable": error.retryable,
                "status_code": error.status_code,
                "provider": error.provider,
            },
        }
    name = type(error).__name__
    return {
        "level": "ERROR",
        "status_message": name,
        "output": {"error_code": name, "retryable": False},
    }


def summarize_state_merge(response: StateApplyResponse) -> dict[str, object]:
    """`merge_conditions` span에 실을 값을 고른다 — Audit "B 상태" 탭과 같은 값이다.

    **여기는 span 자체가 없던 자리다.** 조건 병합은 A가 B를 부르는 단계인데 관측을
    안 걸어서, trace만 보면 "이번 턴이 어떤 조건으로 돌았나"를 알 수 없었다.
    `classify_intent`의 출력(= 이번 발화에서 **새로** 추출한 것)은 보여도 **이전
    턴에서 유지된 값까지 합친 최종 조건**은 어디에도 없었다. 2026-08-26에 B와
    협의해 열었다.

    **좌표는 안 싣는다.** `api_context.gps_location`은 "위도,경도" 문자열이라
    유무만 남긴다(`_api_context_summary`). `user_conditions`의
    `current_location`·`search_center`는 좌표가 아니라 발화에서 온 지명이라
    (`"홍대"`·`"경복궁"`) 그대로 싣는다 — 조건 병합 결과를 읽으려면 그 값이 있어야 한다.

    조건 목록 전체를 싣는다. B가 돌려주는 값이 곧 이 턴의 입력이라 일부만 고르면
    "왜 이 조건으로 돌았나"에 답이 안 된다 — 그게 이 span을 여는 이유다.
    """

    return {
        "session_created": response.session_created,
        "condition_version": response.condition_version,
        "condition_changed": response.condition_changed,
        "reset_applied": response.reset_applied,
        "user_conditions": response.user_conditions.model_dump(mode="json"),
        "api_context": _api_context_summary(response.api_context),
        "applied_operations": [
            operation.model_dump(mode="json") for operation in response.applied_operations
        ],
        # 무효 처리된 연산이 왜 무시됐는지(reason)가 여기 있다. 적용된 것만 보면
        # "왜 내 말이 반영이 안 됐지"에 답할 수 없다.
        "ignored_operations": [
            operation.model_dump(mode="json") for operation in response.ignored_operations
        ],
        "excluded_place_count": len(response.excluded_place_ids),
    }


def _api_context_summary(api_context: ApiContextView) -> dict[str, object]:
    """`api_context`에서 **좌표만 빼고** 편다.

    `gps_location`은 "위도,경도" 문자열이다 — 지금 이 앱을 돌리는 사람의 실제
    위치라, 스위치 하나에 맡길 값이 아니다. 값 대신 **있었는지만** 남긴다:
    "GPS가 없어서 못 했다"와 "있었는데 다른 이유"는 구분돼야 하지만 그건 유무로
    갈리지 좌표 값으로 갈리지 않는다.

    `gps_location_confirmed_at`은 시각이라 그대로 둔다 — "몇 분 전 위치로 계속"
    분기를 읽는 데 필요하고, 위치를 드러내지 않는다.
    """

    summary = api_context.model_dump(mode="json")
    summary["has_gps_location"] = summary.pop("gps_location") is not None
    return summary


def _state_merge_headline(response: StateApplyResponse) -> str:
    """`merge_conditions`의 `status_message`. **mask를 타지 않는 자리다.**

    원문 수집을 꺼도 "이번 턴에 조건이 바뀌었나"는 목록에서 읽혀야 한다. 좌표나
    조건 값은 넣지 않는다 — 여기 넣으면 스위치와 무관하게 나간다.
    """

    parts = [
        f"조건 v{response.condition_version}",
        "변경" if response.condition_changed else "유지",
        f"적용 {len(response.applied_operations)}",
        f"무시 {len(response.ignored_operations)}",
    ]
    if response.reset_applied:
        parts.append(f"reset:{response.reset_applied}")
    return " · ".join(parts)


def summarize_turn(response: AgentResponse) -> dict[str, object]:
    """루트 span(`agent_turn`)에 실을 값을 고른다.

    **여기가 비어 있으면 목록 화면이 안 읽힌다.** 루트는 SPAN이라 토큰·비용·모델이
    원래 없고(그건 자식 GENERATION의 것), 입출력까지 비어 있으면 행에 이름과 지연만
    남는다 — "이 턴이 무슨 요청이었나"를 알려면 눌러서 `classify_intent`의 출력을
    봐야 했다. 턴이 쌓이면 못 쓴다.

    **발화도 답변 원문도 싣지 않는다.** intent와 결과 모양만으로 목록이 읽힌다.
    원문이 필요하면 자식 generation에 이미 있다(`capture_content`가 켜져 있을 때).

    `headline`은 `status_message`로 나간다 — 그 자리는 mask를 타지 않아
    원문 수집을 꺼도 남는다(`langfuse_tracing` 모듈 docstring, 검증 기준 g).
    """
    recommendations = response.recommendations
    shown = list(getattr(recommendations, "recommendations", None) or [])
    unverified = list(getattr(recommendations, "unverified_recommendations", None) or [])
    cards = len(shown) + len(unverified)
    intent = response.llm_output.intent.value
    status = response.llm_output.status.value

    detail = f"카드 {cards}" if cards else None
    if response.schedule is not None:
        detail = "일정"
    elif response.comparison is not None:
        detail = "비교"
    elif response.info_place_card is not None:
        detail = "장소 정보"

    return {
        "intent": intent,
        "status": status,
        "card_count": cards,
        "unverified_count": len(unverified),
        "has_schedule": response.schedule is not None,
        "has_comparison": response.comparison is not None,
        "has_info_card": response.info_place_card is not None,
        # 답변이 나갔는지만 본다. 원문은 자식 generation에 있다.
        "message_length": len(response.message) if response.message else 0,
        "headline": " · ".join(part for part in (intent, status, detail) if part),
    }


def _conversation_place_names(response: AgentResponse) -> list[str]:
    """이번 턴에 실제로 노출한 장소명을 대화 기억용으로 짧게 모은다."""

    names: list[str] = []
    if response.recommendations is not None:
        names.extend(
            item.name
            for item in [
                *response.recommendations.recommendations,
                *response.recommendations.unverified_recommendations,
            ]
        )
    if response.schedule is not None:
        names.extend(item.place_name for item in response.schedule.items)
    if response.comparison is not None:
        names.extend(item.place_name for item in response.comparison.items)
    if response.info_place_card is not None and response.info_place_card.place_name:
        names.append(response.info_place_card.place_name)

    unique: list[str] = []
    for name in names:
        cleaned = name.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return unique[:5]


def _assistant_summary(turn: ConversationTurn) -> str | None:
    """저장된 답변 문장과 구조화 재료를 다음 Gemini 호출의 model 메시지로 바꾼다.

    답변 문장을 앞에 두고 재료를 괄호 꼬리로 붙인다 — 둘의 역할이 다르다. 문장은
    "내가 방금 사용자에게 뭐라고 말했나"(답변 연속성)를, 재료는 "그 턴을 무엇으로
    처리했나"(분류 정확도)를 담는다. 재료가 앞에 오면 모델이 그 내부 문구를 답변에
    그대로 흘릴 위험이 커서 순서를 이렇게 둔다(_shared/rules/conversation_history.md가
    "처리 기록은 인용하지 말라"고 함께 지시한다).

    과거 세션에는 assistant_message가 없으므로 그때는 재료만으로 조립한다.
    """

    parts: list[str] = []
    if turn.intent:
        parts.append(f"처리 의도: {turn.intent}")
    if turn.question_type:
        parts.append(f"질문 유형: {turn.question_type}")
    if turn.place_names:
        parts.append(f"안내한 장소: {', '.join(turn.place_names)}")
    if turn.offered_action:
        parts.append(f"제안한 기능: {turn.offered_action}")
    trace = "; ".join(parts)

    message = (turn.assistant_message or "").strip()
    if message and trace:
        return f"{message}\n(처리 기록 — {trace})"
    return message or trace or None


def _conversation_history(session_context: SessionContextResponse) -> list[ConversationTurnView]:
    """B가 보관한 최근 턴을 역할이 분리된 Gemini 대화 이력으로 변환한다."""

    return [
        ConversationTurnView(
            user_input=turn.user_input,
            assistant_summary=_assistant_summary(turn),
        )
        for turn in session_context.recent_turns
    ]


def _conversation_turn(request: AgentRequest, response: AgentResponse) -> ConversationTurn:
    """완성된 응답에서 다음 턴에 필요한 것(답변 문장 + 처리 재료)을 뽑는다.

    답변 문장은 `response.message`를 그대로 옮긴다 — 추가 LLM 호출 없이 이미 만들어진
    값이다. 길이 상한은 append_conversation_turn()이 적용한다(자르는 책임을 한 곳에).
    """

    llm_output = response.llm_output
    question_type = (
        llm_output.info.question_type.value
        if llm_output.info is not None
        else None
    )
    offered_action: str | None = None
    if llm_output.general is not None:
        offer = offer_for(llm_output.general.situation)
        offered_action = offer.action_id if offer is not None else None
    return ConversationTurn(
        user_input=request.user_input,
        assistant_message=response.message or None,
        intent=llm_output.intent.value,
        question_type=question_type,
        place_names=_conversation_place_names(response),
        offered_action=offered_action,
    )


async def run_agent_flow(
    request: AgentRequest,
    *,
    llm: LLMProvider,
    tool_provider: ToolProvider,
    recommendation_provider: RecommendationProvider,
    enrichment_provider: EnrichmentProvider,
    travel_route_tool: TravelRouteToolProvider | None = None,
    # 보관함 장소를 편성 후보에 주입할 때만 쓴다(SCHEDULE-12 후속). 없으면 주입을
    # 건너뛰고 기존 동작 그대로다 — Supabase가 없는 환경이나 테스트 더블에서
    # 흐름이 깨지지 않도록 optional로 둔다.
    place_details_repository: PlaceDetailsReadRepository | None = None,
    store: StateStore | None = None,
    principal: Principal | None = None,
    stream_event_sink: StreamEventSink | None = None,
    stream_recommendation_summary: bool = False,
    generate_follow_ups: bool = True,
) -> AgentResponse:
    """한 턴 전체를 하나의 관측 trace로 묶고 본체(`_run_agent_flow`)에 넘긴다.

    `generate_follow_ups=False`는 후속 질문(`suggested_follow_ups`)을 만들지 않고
    빈 목록으로 둔다. **SSE 라우트만 쓴다.** 그 호출은 답변이 이미 화면에 다 뜬 뒤에
    도는데, 여기서 응답을 붙잡고 있으면 `done`이 그만큼 늦어져 화면에는 답변과 카드
    아래에 로딩 말풍선이 한 번 더 뜬 것처럼 보인다. 라우트가 `done`을 먼저 내보내
    턴을 끝내고, 후속 질문은 뒤이어 별도 이벤트로 붙인다(D-102).

    **루트 span이 있어야 한 턴이 trace 하나가 된다.** 속성만 전파하고
    (`trace_attributes`) 루트를 안 만들면, 부모가 없는 observation이 저마다
    자기가 trace 루트가 되어 한 턴이 여러 조각으로 흩어진다 — 2026-08-25 첫
    실측에서 `classify_intent`와 `extract_recommend_conditions`가 별도 trace로
    올라와 확인했다. 여기서 연 span이 그 부모 자리다.

    이 블록 안에서 생기는 모든 span이 같은 session_id와 태그도 함께 물려받는다.
    LangGraph 노드는 별도 asyncio 태스크에서 돌지만, 태스크 생성 시점에 문맥을
    복사해 가므로 여기서 연 범위가 그 안까지 따라간다(llm_execution.py의
    ContextVar 설명과 같은 이유).

    **첫 턴은 session_id 없이 기록된다.** 세션은 아래 apply()에서 발급되는데
    그때는 이미 LLM 단계가 지나가서, 나중에 붙여도 앞 span에 소급되지 않는다
    (v4에는 trace 속성을 나중에 갱신하는 API가 없다). 두 번째 턴부터는 묶인다.

    관측이 꺼져 있으면(기본값) 이 래퍼는 아무 일도 하지 않는다.
    """

    with (
        trace_attributes(
            session_id=request.session_id,
            user_id=_observed_user_id(principal),
            tags=[f"scoring:{SCORING_VERSION}", f"env:{settings.app_env}"],
            # 목록에서 턴을 알아보게 이름을 발화로 쓴다. 원문 수집이 꺼져 있으면
            # 관측 모듈이 쓰지 않으므로 여기서 스위치를 보지 않는다.
            user_input=request.user_input,
        ),
        observe_step("agent_turn") as turn,
    ):
        # **본문 전에 기록한다.** 뒤에 적으면 터진 턴에는 입력이 안 남아서, 화면에서
        # "무슨 발화가 이 예외를 냈나"를 서버 로그로 따로 봐야 한다.
        turn.record(input=_turn_input(request))
        try:
            response = await _run_agent_flow(
                request,
                llm=llm,
                tool_provider=tool_provider,
                recommendation_provider=recommendation_provider,
                enrichment_provider=enrichment_provider,
                travel_route_tool=travel_route_tool,
                place_details_repository=place_details_repository,
                store=store,
                principal=principal,
                stream_event_sink=stream_event_sink,
                stream_recommendation_summary=stream_recommendation_summary,
            )
        except BaseException as error:
            # **오류 코드를 span에 적는다.** 그 전까지 실패한 턴은 `turn_success=0`
            # 하나로만 남아서, 화면에서 "터졌다"까지는 알아도 "무엇이 터졌나"는
            # 서버 로그를 따로 봐야 했다.
            turn.record(**_failure_attributes(error))
            # **실패한 턴에도 점수를 남긴다.** 여기서 안 남기면 실패는 Score 집계에서
            # 통째로 빠져 성공률이 항상 1.0으로 보인다 — 2026-08-07부터 SCHEDULE +
            # 혼잡도 조합이 ValueError로 죽고 있었는데 18일간 아무 지표도 안 움직인
            # 것이 그 모양이다. 예외는 그대로 올린다.
            record_score("turn_success", False)
            raise
        # **trace id를 응답에 실어 보낸다.** 평가가 골드셋 케이스와 trace를 잇는
        # 통로다. 위 docstring대로 첫 턴에는 session_id가 안 붙어서, session_id
        # 역조회로는 1턴짜리 케이스(dev 35건 중 20건)의 trace를 못 찾는다.
        # 요약(아래 try)과 분리해 둔다 — 요약이 실패해도 연결은 살아야 한다.
        response.langfuse_trace_id = current_trace_id()
        # **후속 질문은 여기 한 곳에서만 붙인다.** 응답을 만드는 자리
        # (`AgentResponse(...)`)는 인텐트·실패 경로별로 열다섯 군데인데, 버튼은 그
        # 전부에 똑같이 필요하다. 본체가 무엇을 돌려주든 반드시 지나는 이 지점에
        # 두면 새 경로가 생겨도 따로 배선하지 않아도 된다.
        #
        # SSE 경로는 이걸 끄고(`generate_follow_ups=False`) 라우트가 done을 먼저
        # 내보낸 뒤에 직접 만든다 — 아래 설명 참고.
        if generate_follow_ups:
            response.suggested_follow_ups = await suggest_follow_ups(request, response, llm=llm)
        # 모든 정상 응답이 이 중앙 래퍼를 지나므로 여기서 한 번만 기록한다. 본체의
        # 여러 조기 반환 지점마다 기록하면 새 경로가 생길 때 턴 하나가 빠지기 쉽다.
        try:
            append_conversation_turn(
                AppendConversationTurnRequest(
                    session_id=response.state.session_id,
                    turn=_conversation_turn(request, response),
                ),
                store=store,
            )
        except Exception:
            # 대화 기억은 응답 자체보다 부가 기능이다. 저장 장애가 이미 완성된 답변을
            # 사용자에게 못 보내게 해서는 안 된다.
            logger.warning("최근 대화 저장 실패(응답 흐름에는 영향 없음)", exc_info=True)
        # 화면 기록(TP-222 후속)은 **여기서 남기지 않는다.** 이 지점의 response는
        # 아직 사용자가 볼 모양이 아니다 — 후속 질문은 SSE 경로에서 done 뒤에
        # 붙고(routes/chat.py), 영어 화면의 번역도 이 함수가 끝난 뒤에 일어난다.
        # 여기서 저장하면 후속 질문이 빠지고 영어 대화가 한국어로 복원된다
        # (실측: 저장된 턴 8건 전부 후속 질문 없음).
        #
        # 그래서 기록은 화면용 응답을 완성하는 라우트 두 곳이 남긴다. /agent-debug
        # 같은 개발용 호출이 사용자 대화 기록에 섞이지 않는 효과도 함께 얻는다.
        try:
            summary = summarize_turn(response)
            turn.record(
                output=summary,
                # 목록 화면에서 필터를 걸 자리. capture_content가 꺼지면 가려진다.
                metadata={"intent": summary["intent"], "status": summary["status"]},
                # 마스킹을 타지 않는 자리. 원문 수집을 꺼도 목록에서 턴이 읽힌다.
                status_message=summary["headline"],
            )
            record_turn_scores(summary)
        except Exception:
            logger.warning("턴 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
        return response


def _turn_input(request: AgentRequest) -> dict[str, Any]:
    """루트 span에 실을 이 턴의 입력.

    **`input`은 mask를 탄다**(`observability/langfuse_tracing` 모듈 docstring) —
    `capture_content`가 꺼지면 SDK가 통째로 치환한다. 그래서 발화를 여기 실어도
    스위치를 우회하지 않는다. trace 이름과 다른 점이다(그쪽은 mask를 안 타서
    호출부가 직접 판단해야 한다).

    **좌표는 싣지 않는다.** `device_location`은 팀원이 테스트하는 자리의 실좌표라
    있고 없음만 남긴다(2026-08-26 결정, `graph/__init__.py::_location`과 같은 규칙).

    `clarification_choice`·`travel_origin_override`·`schedule_from_saved`는
    **값이 있을 때만** 넣는다.
    이 둘이 채워진 턴은 LLM 분류를 건너뛰므로, 없는데 `classify_intent` span이
    안 보이면 그게 이상한 것이고 있으면 정상이다 — 그 구분이 여기서 된다.
    """
    payload: dict[str, Any] = {
        "user_input": request.user_input,
        "language": request.language,
        "has_device_location": request.device_location is not None,
    }
    if request.selected_search_center:
        # 좌표가 아니라 사용자가 고른 장소 이름이고, 어차피 조건(search_center)으로
        # 관측에 남는 값이라 여기서도 그대로 싣는다. 출발지도 같다.
        payload["selected_search_center"] = request.selected_search_center
    if request.selected_current_location:
        payload["selected_current_location"] = request.selected_current_location
    if request.conversation_place_name:
        payload["conversation_place_name"] = request.conversation_place_name
    if request.clarification_choice:
        payload["clarification_choice"] = request.clarification_choice
    if request.travel_origin_override is not None:
        payload["travel_origin_override"] = request.travel_origin_override.value
    if request.schedule_from_saved:
        payload["schedule_from_saved"] = True
    return payload


def _observed_user_id(principal: Principal | None) -> str | None:
    """관측에 실을 사용자 식별자. 스위치가 꺼져 있으면 `None`.

    켜는 것은 팀 결정이라 기본값이 꺼짐이다(`config.py`). 게스트도 `user_id`를
    갖지만(D-062 2절) 그것 역시 외부로 나가는 식별자라 똑같이 스위치를 탄다.
    """

    if not settings.langfuse_capture_user_id or principal is None:
        return None
    return principal.user_id


def record_turn_scores(summary: Mapping[str, object]) -> None:
    """턴 요약에서 **집계할 값**만 골라 Score로 올린다.

    `turn.record(output=summary)`와 중복이 아니다 — output은 그 턴을 열어봤을 때
    읽는 값이고 Score는 여러 턴에 걸쳐 곡선이 되는 값이다. 그래서 여기 올리는 것은
    "추세가 의미 있는 수치"로 좁힌다. `intent`·`status`는 태그와 metadata로 이미
    필터가 되므로 Score로 또 올리지 않는다.

    `unverified_ratio`는 카드가 있을 때만 올린다 — 0/0을 0.0으로 적으면 "미검증이
    하나도 없는 좋은 턴"과 "카드 자체가 없는 턴"이 같은 값이 되어 평균이 거짓말을 한다.
    """

    record_score("turn_success", True)
    cards = int(summary.get("card_count") or 0)
    record_score("card_count", cards)
    if cards:
        record_score("unverified_ratio", int(summary.get("unverified_count") or 0) / cards)


async def _run_agent_flow(
    request: AgentRequest,
    *,
    llm: LLMProvider,
    tool_provider: ToolProvider,
    recommendation_provider: RecommendationProvider,
    enrichment_provider: EnrichmentProvider,
    travel_route_tool: TravelRouteToolProvider | None = None,
    place_details_repository: PlaceDetailsReadRepository | None = None,
    store: StateStore | None = None,
    principal: Principal | None = None,
    stream_event_sink: StreamEventSink | None = None,
    stream_recommendation_summary: bool = False,
) -> AgentResponse:
    """Provider를 인자로 받는 테스트 가능한 본체.

    호출 순서(A가 전체를 조정, B/C/D는 각자 내부 판단만 담당):
      A→B(세션 컨텍스트) → A(Intent+조건 추출) → A→B(조건 병합) →
      [A→C(Tool) → A→D(Recommendation) → A→B(결과 기록)] → A(최종 응답)
    대괄호 구간은 status가 complete이고 intent가 RECOMMEND/MODIFY일 때만 실행된다.
    이 구간 안에서도 C 응답 status가 needs_clarification/unsupported/unavailable이면
    D를 건너뛴다 — LLM 단계의 needs_clarification과는 별개 레이어다(계약 문서 §5.4).
    """

    # 1) A → B: GPS 세션 컨텍스트 최신화. GPS 형식이 잘못되면 이번 턴만 건너뛴다 —
    #    잘못된 GPS 문자열이 파싱 예외로 대화를 중단시키지 않아야 한다.
    reset_llm_execution_metadata()
    await _emit_progress(
        stream_event_sink,
        "interpreting",
        "요청 의도와 조건을 파악하고 있어요.",
    )
    valid_gps = _valid_location(request.device_location)
    session_context = await ensure_current_context(
        request.session_id, valid_gps, store=store, principal=principal
    )
    # 이 턴 전체가 쓰는 대화 이력. 해석 단계(InterpretRequest)와 답변 생성 단계가
    # **같은 값**을 봐야 한다 — 한쪽만 보면 "무엇을 물었는지"와 "무엇이라 답할지"가
    # 어긋난다(2026-08-31 실사용에서 답변 단계만 이력을 못 받아 생긴 문제).
    turn_history = _conversation_history(session_context)

    # 2) A: LLMOutput 생성 (Intent 분류 + Intent별 조건 추출). B가 준 현재 조건(순수 문자열)을
    #    A 쪽 enum 타입으로 변환해서 넘긴다 — MODIFY 추출이 이 타입을 요구한다.
    # 위치 되묻기 직후에는 아직 추천 결과가 없을 수 있어도, 첫 턴에서 저장된 조건을
    # MODIFY 추출에 제공해야 한다. 그렇지 않으면 "경복궁"이 MODIFY로 올바르게
    # 분류돼도 current_conditions 없음 되묻기로 다시 빠진다.
    # 되묻기 버튼 클릭이면 classify_intent()/extract_*_conditions()를 건너뛰고
    # 결정적으로 해소한다(docs/design/clarification-options.md 3절). code/choice_id가
    # 안 맞으면 None이 와서 아래 평소 경로로 자연스럽게 폴백한다.
    # "OO 기준으로 다시 보기" 버튼(travel_origin_override, D-071)도 같은 이유로
    # 결정적으로 해소한다 — 둘 다 세션에 온 요청이면 클라리피케이션 쪽을 우선한다
    # (두 필드가 같은 턴에 함께 오는 경우는 없다).
    # 보관함 CTA(schedule_from_saved, 카드 3)도 같은 비차단형이다. 우선순위는
    # "사용자가 얼마나 명확히 고른 것인가" 순이다 — 되묻기 답변(질문에 대한 직접
    # 응답) > 보관함 CTA(자기 payload를 가진 새 행동) > 기준 전환(직전 턴 재실행)
    # > 제안 수락 발화(자유 텍스트 휴리스틱).
    if request.clarification_choice is not None:
        clarification_resolution = _resolve_clarification_choice(
            choice_id=request.clarification_choice,
            session_context=session_context,
        )
        if clarification_resolution is None:
            # TP-182: 버튼 클릭인데도 결정적 해소를 못 타면 발화가 다시 LLM으로
            # 흘러가 행정구역이 잘리는 등 재해석 손실이 생긴다(예: "종로구 익선동"
            # → "익선동"). pending_clarification/last_intent 둘 중 어느 조건이
            # 안 맞았는지가 원인 특정의 핵심인데 재현이 안 돼 실측으로만 확인
            # 가능하므로, 폴백이 실제로 일어나는 순간을 여기서 반드시 남긴다.
            logger.warning(
                "되묻기 버튼 클릭이 결정적 해소를 못 타 재해석 경로로 폴백함: "
                "session_id=%s choice_id=%r pending_clarification=%r last_intent=%r",
                session_context.session_id,
                request.clarification_choice,
                session_context.pending_clarification,
                session_context.last_intent,
            )
    elif request.schedule_from_saved:
        clarification_resolution = _resolve_schedule_from_saved(
            session_context=session_context,
        )
    elif request.travel_origin_override is not None:
        clarification_resolution = _resolve_travel_origin_override(
            override=request.travel_origin_override,
            session_context=session_context,
        )
    elif (
        session_context.situation_state is not None
        and session_context.situation_state.pending_offer is not None
    ):
        # 대화층 4단계 — 직전 GENERAL 상황 턴이 낸 제안에 대한 "응"/"아니" 같은
        # 짧은 응답을 결정적으로 해석한다. 정확 일치 화이트리스트에 안 맞으면
        # None이 와서 평소 경로로 폴백한다(위 두 분기와 같은 안전 실패 패턴).
        clarification_resolution = _resolve_offer_utterance(
            user_input=request.user_input,
            session_context=session_context,
        )
    else:
        clarification_resolution = None
    # 이번 턴에 막 선택했거나(clarification_resolution), 직전에 선택해서 아직
    # TTL 안이거나(session_context), 개발자 채팅의 일회성 디버그 스위치가
    # 켜졌으면 폐점 후보도 계속 포함한다.
    clicked_show_closed = (
        clarification_resolution is not None and clarification_resolution.ignore_operating_hours
    )
    effective_ignore_operating_hours = bool(
        request.debug_ignore_operating_hours
        or clicked_show_closed
        or (
            session_context.ignore_operating_hours_until is not None
            and session_context.ignore_operating_hours_until > now_kst()
        )
    )

    llm_started_at = time.monotonic()
    if clarification_resolution is not None:
        llm_output = clarification_resolution.llm_output
    else:
        location_clarification_pending = session_context.pending_clarification in {
            "location_required",
            "location_ambiguous",
        }
        # 게이트는 "추천 결과가 있었나"가 아니라 **"바꿀 조건이 세션에 있나"**다.
        # has_recommendation만 보면 추천을 여러 번 시도했지만 결과가 0건이었던
        # 세션이 조건을 들고도 빈손으로 해석 단계에 들어간다 — 2026-09-20 실사용
        # 사례에서 condition_version=7 · search_center="망원동"인 세션이
        # has_recommendation=False라 current_conditions=None으로 내려갔고,
        # 라우터는 대화 이력을 보고 MODIFY를 냈다. 두 판단이 어긋나 "아직 추천한
        # 결과가 없어요" 되묻기로 끝났다(orchestrator._extract_for_intent의
        # MODIFY 분기). 조건이 있으면 넘긴다 — 그러면 MODIFY 추출이 정상 동작해
        # 검색 중심점만 바꾸는 발화가 제대로 처리된다.
        # 기존 두 조건은 그대로 두고 조건 보유 여부를 OR로 더한다 — 좁히는
        # 변경이 아니라 넓히는 변경이라 기존에 통과하던 턴은 그대로 통과한다.
        #
        # **condition_version이 아니라 실제 값으로 본다.** 버전만 보면 안 되는
        # 세션이 실재한다 — 2026-09-20 Supabase `agent_states` 확인에서 최근 30개
        # 중 둘이 `condition_version=7`인데 `user_conditions`가 전부 null이었다
        # (문제 세션 sess_...b49ab37f 포함). 버전으로 게이트하면 그런 세션이 빈
        # UserConditions를 넘겨, MODIFY 추출이 바꿀 것도 없는 상태로 돌고
        # orchestrator의 RECOMMEND 구제도 못 타게 된다.
        session_has_conditions = any(
            value not in (None, [], "")
            for value in session_context.user_conditions.model_dump().values()
        )
        current_conditions = (
            to_user_conditions(session_context.user_conditions)
            if session_context.has_recommendation
            or session_has_conditions
            or location_clarification_pending
            else None
        )
        # 직전 턴이 INFO 되묻기(장소명 없음/장소 후보 모호)로 끝났을 때만 이전 질문
        # 정보를 다음 턴 추출기에 건넨다 — 관련 없는 턴에 잘못 섞이지 않게 last_intent/
        # pending_clarification/pending_info_context 세 조건을 모두 확인한다.
        info_clarification_pending = (
            session_context.last_intent == Intent.INFO.value
            and session_context.pending_clarification is not None
            and session_context.pending_info_context is not None
        )
        pending_info = session_context.pending_info_context if info_clarification_pending else None
        interpret_request = InterpretRequest(
            user_input=request.user_input,
            has_previous_recommendation=session_context.has_recommendation,
            shown_place_count=len(session_context.shown_place_ids),
            current_conditions=current_conditions,
            pending_clarification=session_context.pending_clarification,
            last_intent=session_context.last_intent,
            # rank 순으로 채운다 — shown_recommendations는 이미 rank 정렬되어
            # 있으므로(history.get_last_recommended_items) 그대로 옮기면 된다.
            # 이름이 없는 항목(name 저장 이전의 과거 세션 등)은 빈 문자열로 채운다.
            shown_place_names=[item.name or "" for item in session_context.shown_recommendations],
            conversation_place_name=request.conversation_place_name,
            recent_turns=turn_history,
            pending_info_question_type=pending_info.question_type if pending_info else None,
            pending_info_specific_question=pending_info.specific_question if pending_info else None,
            pending_info_visit_time=pending_info.visit_time if pending_info else None,
        )
        # classify_intent()에 이어 intent별 extract_*()까지 순차 LLM 호출 최대
        # 두 번이 이 안에서 일어난다. 평소엔 1~2초 안에 끝나 heartbeat가 거의 안
        # 뜨지만, 꼬리 지연(P95/P99)이 걸리면 위 "interpreting" progress 이벤트
        # 하나 이후로 무응답 공백이 생긴다 — SCHEDULE과 같은 방식으로 채운다.
        llm_output = await _await_with_heartbeat(
            build_interpretation(interpret_request, llm),
            sink=stream_event_sink,
            stage="interpreting",
            messages=INTERPRET_HEARTBEAT_MESSAGES,
            interval_seconds=INTERPRET_HEARTBEAT_INTERVAL_SECONDS,
        )
    llm_latency_ms = int((time.monotonic() - llm_started_at) * 1000)

    # 화면에서 고른 검색 위치는 여기서 채운다 — 조건 병합(B)보다 앞이라 이번 턴의
    # 위치 판정과 다음 턴이 물려받을 세션 조건에 모두 반영된다. 되묻기 버튼으로
    # 해소된 턴(clarification_resolution)도 같이 지나가는데, 그때는 세션 조건을
    # 재사용하므로 search_center가 이미 차 있어 이 함수가 손대지 않는다.
    llm_output = _apply_selected_locations(llm_output, request)

    # 3) A → B: 조건 병합. confirmed=False(= status가 complete가 아님)면 B가 State를
    #    바꾸지 않고 현재 상태만 돌려주도록 이미 구현되어 있다(계약 2.6절) — 따로 걸러서
    #    apply()를 건너뛸 필요가 없다. 그래야 needs_clarification 응답에도 병합된(=변화
    #    없는) state가 항상 채워진다.
    await _emit_progress(
        stream_event_sink,
        "merging_conditions",
        "이전 대화 조건을 반영하고 있어요.",
    )
    apply_request = transform(llm_output, session_context, request.user_input)
    with observe_step("merge_conditions") as merge_step:
        state_response = apply(apply_request, store=store, principal=principal)
        try:
            merge_step.record(
                output=summarize_state_merge(state_response),
                status_message=_state_merge_headline(state_response),
            )
        except Exception:
            logger.warning("조건 병합 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)

    # 조건을 직접 나르지 않는 턴(MODIFY·COMPARE·INFO)이 화면에서 정한 위치를 놓치는
    # 것을 여기서 받는다. 병합 바로 뒤에 두는 이유는 아래 location_resolved부터
    # 도구 조회·채점까지 이 값을 읽는 자리가 전부 이 아래이기 때문이다 — 한 곳만
    # 고치면 나머지가 따라온다.
    state_response = _override_locations_from_request(state_response, request)

    # 라우트의 "발화 수신"(routes/chat.py)과 짝을 이루는 줄이다. 저쪽은 브라우저가
    # **보낸** 값이고 이쪽은 서버가 **쓰기로 정한** 값이다. 둘이 다를 수 있다 —
    # "쌍문동에 갈만한곳"이라고 말하면 화면에 설정된 서대문역을 발화가 이긴다.
    # 한쪽만 보면 정상 동작을 버그로 읽게 돼 실제로 그렇게 한 번 헤맸다(2026-09-08).
    # 좌표는 여기서도 남기지 않는다 — 이유는 routes/chat.py의 _log_incoming_location.
    logger.info(
        "위치 확정 | 출발지=%s | 검색지=%s | 분류=%s | 세션=%s",
        state_response.user_conditions.current_location or "-",
        state_response.user_conditions.search_center or "-",
        llm_output.intent.value if hasattr(llm_output.intent, "value") else llm_output.intent,
        state_response.session_id,
    )

    # 이번 턴이 쓸 위치가 여기서 확정된다 — 화면 우상단 위치 칩이 이 값을 보여준다.
    # done까지 기다리면 도구 조회(fetching_context)와 채점(scoring), 답변 스트리밍이
    # 전부 끝난 뒤라, 사용자는 "광화문역 근처"라고 말해 놓고 결과가 다 나올 때까지
    # 이전 위치를 보게 된다. 그 사이가 이 턴에서 제일 긴 구간이다.
    #
    # 모든 Intent가 지나가는 공통 경로다. 단발 POST /api/chat은 sink가 없어 그냥
    # 통과하고, 화면은 done의 state로 같은 값을 다시 받는다.
    await _emit_stream_event(
        stream_event_sink,
        "location_resolved",
        {
            "current_location": state_response.user_conditions.current_location,
            "search_center": state_response.user_conditions.search_center,
        },
    )

    if clarification_resolution is not None and clarification_resolution.ignore_operating_hours:
        _remember_ignore_operating_hours(state_response.session_id, store)

    # 대화층 4단계 — 이번 턴이 상황 상태를 어떻게 바꾸는지는 어느 반환 경로로
    # 끝나든 똑같으므로, 아래에서 갈라지는 여러 return보다 앞선 여기 한 곳에서만
    # 계산·저장한다.
    _sync_situation_state(
        session_id=state_response.session_id,
        llm_output=llm_output,
        session_context=session_context,
        reject_offer_action=(
            clarification_resolution.reject_offer_action
            if clarification_resolution is not None
            else None
        ),
        store=store,
    )

    # 2단계(LLM 호출) trace는 여기서 기록한다 — run_id/session_id가 apply() 안에서
    # 발급되므로 2단계 시점엔 아직 없다. latency만 미리 재뒀다가 여기서 기록.
    condition_intake_error = _condition_intake_error(llm_output)
    _record_trace_safely(
        session_id=state_response.session_id,
        run_id=state_response.run_id,
        step="llm_interpret",
        latency_ms=llm_latency_ms,
        # 조건을 나르는 턴인데 페이로드가 통째로 비어 왔으면 이 단계의 실패로
        # 남긴다 — 이유는 _condition_intake_error() 참고.
        error_type=condition_intake_error,
        # 이번 턴이 실제로 사용한 슬롯의 버전을 남긴다
        # (예: router.classify@2.0.0+info.extract@3.0.0).
        # 예전의 단일 고정 문자열로는 어느 인텐트의 프롬프트가 이 응답을 만들었는지
        # 되짚을 수 없었다.
        prompt_version=turn_prompt_version(llm_output.intent),
        # 계약 2절의 token_usage. 2026-08-25까지 이 값은 항상 None이었다 —
        # 필드는 있었지만 gemini.py가 응답의 usage_metadata를 안 읽었다.
        # 이 시점까지 이 턴이 쓴 총 토큰을 넘긴다(분류 + 조건 추출).
        token_usage=consumed_tokens(),
        store=store,
    )

    # 조건이 비어 온 턴은 로그에도 남긴다. trace의 error_type이 집계용이고 이
    # 줄은 지금 무슨 일이 일어났는지 그 자리에서 보이게 하려는 것이다 — 이
    # 경로가 조용했던 것이 원인 규명을 열흘 넘게 막았다.
    if condition_intake_error is not None:
        logger.warning(
            "조건 페이로드가 비어 왔다 — 이번 턴은 조건 없이 진행된다: "
            "intent=%s status=%s session_id=%s run_id=%s",
            llm_output.intent.value,
            llm_output.status.value,
            state_response.session_id,
            state_response.run_id,
        )

    # 되묻기 버튼이 "조회할 것 없는 확인성 선택지"로 해소된 경우(예: "지금 장소가
    # 마음에 들어요", "새로 시작할게요") — 조건 병합(soft reset 등)은 이미 위에서
    # 끝났지만 Tool/D 호출 없이 고정 문구로 여기서 바로 끝낸다. intent가
    # RECOMMEND/MODIFY/SCHEDULE이어도(즉 아래 "4) 게이트"를 안 거치는 경우에도)
    # 똑같이 터미널로 끝나야 해서 게이트보다 앞에 둔다 — "새로 시작할게요"가 GPS만
    # 있어도 자동으로 추천을 내버리는 걸 막는다(실사용 재현, 2026-08-13:
    # 사용자가 새 목적지를 직접 말하길 기다려야 하는데 조건이 비어있다는 이유로
    # 현재 위치 기준 추천이 조용히 나가버렸다).
    if clarification_resolution is not None and clarification_resolution.terminal_message:
        _remember_clarification(state_response.session_id, None, store)
        return AgentResponse(
            llm_output=llm_output,
            state=state_response,
            recommendations=None,
            message=clarification_resolution.terminal_message,
            llm_execution=get_llm_execution_metadata(),
        )

    # 3-1) 최초 턴에 GPS를 심던 자리였다. 서버가 사용자 위치를 저장하지 않게 되면서
    #      (state/store.py::for_persistence) 심어도 남지 않아 없앴다. 이번 턴의
    #      좌표는 valid_gps로 그대로 쓰이고, 다음 턴은 화면이 다시 실어 보낸다.

    # 3-2) 되묻기 플래그 소비. 조건을 건드리는 턴(RECOMMEND/MODIFY/SCHEDULE)만 지운다 —
    #      transform()이 이미 session_context의 값을 읽어 병합 방식을 정했으므로,
    #      여기서 지워도 이번 턴 판단에는 영향이 없다. 이번 턴이 또 되묻기로 끝나면
    #      아래 4)/5-1)에서 새 값을 다시 심는다. INFO/GENERAL 같은 곁가지 대화는
    #      조건을 바꾸지 않으므로 이전 되묻기를 그대로 살려둔다. SCHEDULE도 RECOMMEND와
    #      동일하게 조건을 건드리는 턴이라 목록에 포함한다(D-059) — 빠뜨리면 SCHEDULE
    #      되묻기가 옳게 이어져도 플래그가 계속 남아 다음 턴 판단에 잘못 영향을 준다.
    if session_context.pending_clarification is not None and llm_output.intent in (
        Intent.RECOMMEND,
        Intent.MODIFY,
        Intent.SCHEDULE,
    ):
        _remember_clarification(state_response.session_id, None, store)

    # 3-3) SCHEDULE 재조정 감지(SCHEDULE-06). 직전 턴이 SCHEDULE로 완료됐는데
    #      (last_intent="SCHEDULE", 되묻기 없이 끝남 — pending_clarification=None)
    #      이번 턴이 조건을 바꾸는 MODIFY로 분류됐다면 "일정 재조정" 요청으로
    #      본다. session_context는 이번 턴 처리 전에 조회한 값이라 직전 턴
    #      정보를 그대로 담고 있다(1번 참고). 조건 병합(3번)은 이미 원래
    #      MODIFY 페이로드(llm_output.modify)로 정상적으로 끝났으므로 그 결과는
    #      손대지 않고, intent 라벨만 SCHEDULE로 바꿔 아래 6)~8) 단계가 기존
    #      SCHEDULE 분기(D 10개 호출·편성 모듈 호출)를 그대로 타게 한다.
    #      classify_intent 프롬프트나 extract_modify_conditions는 건드리지
    #      않는다 — last_intent는 B가 이미 매 턴 저장해온 값을 여기서 처음
    #      읽는 것뿐이다(docs/design/int-07-schedule.md 3절 참고, A 공유 완료).
    if (
        llm_output.intent is Intent.MODIFY
        and llm_output.status is OutputStatus.COMPLETE
        and session_context.last_intent == Intent.SCHEDULE.value
        and session_context.pending_clarification is None
    ):
        modify = llm_output.modify
        if (
            modify is not None
            and modify.modify_type is ModifyType.CHANGE_CONDITION
            and _is_ambiguous_schedule_or_recommend(request.user_input)
        ):
            # "카페 추천해줘"류는 "일정 재조정"인지 "그냥 추천"인지 글자로 구분이 안
            # 되는 진짜 모호 케이스다 — 추측 대신 되묻는다(버튼 2개,
            # docs/design/clarification-options.md 5절). 조건 병합은 이미 원래 MODIFY
            # 페이로드로 끝났으므로 손대지 않고 라벨만 되묻기로 바꾼다. 이번 턴에서
            # 추출된 카테고리(예: "카페")가 있으면 "장소" 대신 그 카테고리명을 그대로
            # 문구/버튼에 넣는다 — 범용 문구보다 사용자가 방금 말한 걸 그대로
            # 되비춰주는 쪽이 더 명확하다.
            category_label = _extracted_category_label(modify)
            recommend_label = (
                f"{category_label}만 추천받기" if category_label else "장소만 추천받기"
            )
            clarification_message = (
                f"이어서 일정을 다시 짜드릴까요, 아니면 {category_label}만 추천해드릴까요?"
                if category_label
                else "이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"
            )
            clarification_llm_output = llm_output.model_copy(
                update={
                    "status": OutputStatus.NEEDS_CLARIFICATION,
                    "clarification": ClarificationPayload(
                        message=clarification_message,
                        options=[
                            ClarificationOption(
                                id="schedule_continue",
                                label="일정 다시 짜기",
                                resolved_intent=Intent.SCHEDULE,
                            ),
                            ClarificationOption(
                                id="recommend_only",
                                label=recommend_label,
                                resolved_intent=Intent.RECOMMEND,
                            ),
                        ],
                    ),
                }
            )
            _remember_clarification(
                state_response.session_id, "schedule06_ambiguous_recommend", store
            )
            # apply()(3번)는 이번 턴의 원본 intent(MODIFY)로 last_intent를 이미
            # 저장했다 — 그대로 두면 다음 턴 classify_intent가 "직전 SCHEDULE
            # 되묻기"라는 신호를 받지 못해 자유 텍스트 답변("추천만 해줘" 등)이
            # 아무 맥락 없이 새로 분류된다(아래 8)의 SCHEDULE relabel과 같은 이유,
            # D-061). 이 되묻기는 SCHEDULE 흐름에서 나온 것이므로 여기서도 바로잡는다.
            set_last_intent(
                SetLastIntentRequest(
                    session_id=state_response.session_id, intent=Intent.SCHEDULE.value
                ),
                store=store,
            )
            message = await compose_chat_message(clarification_llm_output, llm=llm)
            return AgentResponse(
                llm_output=clarification_llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                llm_execution=get_llm_execution_metadata(),
            )
        llm_output = llm_output.model_copy(update={"intent": Intent.SCHEDULE})
        # apply()(3번)는 이미 이 턴의 원본 intent(MODIFY)로 last_intent를 저장했다
        # — 그 호출 시점엔 아직 이 relabel이 일어나기 전이었기 때문이다. 그대로
        # 두면 다음 턴이 이번 턴을 last_intent="MODIFY"로 보게 되어, SCHEDULE →
        # REJECT_SPECIFIC → REJECT_SPECIFIC처럼 재조정이 연속될 때 두 번째부터
        # 이 감지 자체가 실패한다(2026-08-11 실사용 재현, D-061). 화면상 라벨과
        # 저장된 last_intent를 다시 맞춘다.
        set_last_intent(
            SetLastIntentRequest(
                session_id=state_response.session_id, intent=Intent.SCHEDULE.value
            ),
            store=store,
        )

    # 3-4) schedule_no_candidates 되묻기 해소(force_schedule). 되묻기 해소 turn은
    #      session_context.pending_clarification이 "schedule_no_candidates"라서
    #      바로 위 3-3)의 게이트(pending_clarification is None)를 못 타 자동
    #      relabel이 안 된다 — 여기서 명시적으로 같은 relabel을 반복한다.
    if (
        clarification_resolution is not None
        and clarification_resolution.force_schedule
        and llm_output.intent is not Intent.SCHEDULE
    ):
        llm_output = llm_output.model_copy(update={"intent": Intent.SCHEDULE})
        set_last_intent(
            SetLastIntentRequest(
                session_id=state_response.session_id, intent=Intent.SCHEDULE.value
            ),
            store=store,
        )

    # 4-0) INFO는 question_type 8종 모두 RECOMMEND/MODIFY와 별개로 C를 거친다
    #      (D-054/D-055, backend/docs/package-a/info-question-types-handoff.md)
    #      COMPARE/GENERAL은 그대로 4)의 일반 게이트로 빠진다 — Tool을 직접 호출하지
    #      않는다는 기존 원칙(ToolProvider Protocol)을 그대로 따른다.
    #      hasattr 체크: Fake 등 fetch_info_context()를 구현하지 않은 ToolProvider에도
    #      AttributeError로 요청 전체가 죽지 않고 기존 "준비 중" 문구로 안전하게
    #      낮아지게 한다.
    if (
        llm_output.status is OutputStatus.COMPLETE
        and llm_output.intent is Intent.INFO
        and llm_output.info is not None
        and hasattr(tool_provider, "fetch_info_context")
    ):
        info_request = to_info_context_request(
            new_trace_id(), llm_output.info, device_location=valid_gps
        )
        await _emit_progress(
            stream_event_sink,
            "fetching_context",
            "장소 상세 정보를 찾고 있어요.",
        )
        info_started_at = time.monotonic()
        # 로드맵 24번(A-1/A-2 후속): no_data를 곧장 되묻는 대신 LLM이 스스로 다른
        # 지역명으로 재시도하게 하는 경로. 기본 off — settings.agentic_realtime_info
        # 참고.
        agentic_realtime_message: str | None = None
        if (
            settings.agentic_realtime_info
            and info_request.question_type in _AGENTIC_REALTIME_QUESTION_TYPES
        ):
            info_response, agentic_realtime_message = await _fetch_realtime_info_agentic(
                info_request, llm=llm, tool_provider=tool_provider
            )
        else:
            info_response = await tool_provider.fetch_info_context(info_request)
        info_response = await _filter_review_evidence(
            info_response, info_request=info_request, llm=llm
        )
        info_execution = build_info_concentration_execution_debug(
            info_response,
            latency_ms=int((time.monotonic() - info_started_at) * 1000),
        )
        # place_ambiguous면 되묻기 버튼으로 끝낸다 — RECOMMEND/SCHEDULE의
        # location_ambiguous와 같은 패턴(status를 NEEDS_CLARIFICATION으로 바꾸고
        # ClarificationPayload를 채움). candidates가 비어 있으면(예: 지오코딩
        # 경로라 이름 자체를 모르는 경우) 버튼 없는 되묻기를 만들지 않고 기존
        # 평문 경로로 그대로 흘려보낸다.
        info_clarification = info_response.clarification
        if (
            info_response.status == "needs_clarification"
            and info_clarification is not None
            and info_clarification.code == "place_ambiguous"
            and info_clarification.candidates
        ):
            _remember_clarification(state_response.session_id, "place_ambiguous", store)
            # 버튼 클릭 시 원래 질문(question_type 등)을 그대로 이어받을 수
            # 있도록 저장해둔다 — 세션에 없으면 장소명만으로 처음부터
            # 재분류돼 "주차장 질문이었다"는 사실이 사라진다.
            set_pending_info_context(
                SetPendingInfoContextRequest(
                    session_id=state_response.session_id,
                    context=PendingInfoContext(
                        question_type=llm_output.info.question_type.value,
                        place_context=llm_output.info.place_context.value,
                        specific_question=llm_output.info.specific_question,
                        visit_time=llm_output.info.visit_time,
                    ),
                ),
                store=store,
            )
            llm_output = llm_output.model_copy(
                update={
                    "status": OutputStatus.NEEDS_CLARIFICATION,
                    "clarification": ClarificationPayload(
                        message=tool_clarification_message("place_ambiguous"),
                        options=[
                            ClarificationOption(
                                id=name, label=name, resolved_intent=llm_output.intent
                            )
                            for name in info_clarification.candidates
                        ],
                    ),
                }
            )
            message = await compose_chat_message(llm_output, llm=llm)
            return AgentResponse(
                llm_output=llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                llm_execution=get_llm_execution_metadata(),
                tool_execution=info_execution,
                tool_executions=[info_execution] if info_execution is not None else [],
            )

        requests_walking_time = _is_info_walking_time_request(llm_output)
        info_origin_location = valid_gps
        if info_origin_location is None and not state_response.api_context.gps_expired:
            info_origin_location = state_response.api_context.gps_location
        info_walking_route = None
        if requests_walking_time:
            await _emit_progress(
                stream_event_sink,
                "fetching_context",
                "현재 위치에서 도보 이동 시간을 확인하고 있어요.",
            )
            info_walking_route = await _fetch_info_walking_route(
                travel_route_tool,
                origin_location=info_origin_location,
                info_response=info_response,
            )
        stream_info_message = (
            stream_recommendation_summary
            and isinstance(info_response.result, PlaceInfoResult)
            and info_response.result.status == "success"
            # 후기 답변은 fields가 비어 있고 근거만 있다 — 여기 빠뜨리면 SSE가
            # 델타 채널을 열지 않아 고정 문구만 나간다.
            and (
                bool(info_response.result.fields)
                or bool(info_response.result.review_evidence)
            )
            and not requests_walking_time
        )
        if stream_info_message:
            await _begin_streamed_message(
                stream_event_sink,
                intent=Intent.INFO,
                progress_message="정보를 정리하고 있어요.",
            )

        async def emit_info_message_delta(text: str) -> None:
            await _emit_stream_event(stream_event_sink, "message_delta", {"text": text})

        if session_context.pending_clarification == "place_ambiguous":
            # 이번 턴은(되묻기 해소든 아니든) 되묻지 않고 끝났다 — 직전 턴이 남긴
            # place_ambiguous를 여기서 지운다. INFO/GENERAL 곁가지 대화가 RECOMMEND의
            # pending_clarification까지 지우지는 않는(위 3-2) 원칙과 같은 결로,
            # place_ambiguous는 INFO 자신의 되묻기라 INFO가 정리할 책임이 있다.
            _remember_clarification(state_response.session_id, None, store)

        if agentic_realtime_message is not None:
            # 에이전트가 이미 도구 결과를 종합해 최종 문장을 직접 썼다 — 별도
            # compose_chat_message() 합성을 다시 거치지 않는다(사용자 결정).
            message = agentic_realtime_message
        else:
            message = await compose_chat_message(
                llm_output,
                info_response=info_response,
                llm=llm,
                info_walking_route=info_walking_route,
                info_walking_origin_available=info_origin_location is not None,
                on_message_delta=(emit_info_message_delta if stream_info_message else None),
                history=turn_history,
            )

        secondary_info_place_card = None
        paired_question_type = _paired_parking_question_type(llm_output.info)
        if paired_question_type is not None and info_response.status == "success":
            # 근처 주차장(area 응답)과 공영주차장(구 전체)은 서로의 약점을 메운다 —
            # 근처는 가깝지만 목록이 짧고, 공영은 목록이 길지만 멀 수 있다. 하나를
            # 물으면 다른 쪽도 이어서 보여준다(TP-115 실사용 지적).
            #
            # 이 조회는 부가 기능이다 — 실패해도 이미 확정된 1차 답변을 물릴 이유가
            # 없다. 위치 재해석부터 다시 하는 두 번째 fetch_info_context() 호출이라
            # 실제 서울시 API(GetParkingInfo/도시데이터) 쪽 타임아웃·장애를 그대로
            # 물려받는데, 감싸지 않으면 부가 카드 하나 때문에 턴 전체가 죽는다
            # (2026-09-02 실사용 — follow_up_suggester.py와 같은 원칙).
            try:
                paired_response = await tool_provider.fetch_info_context(
                    info_request.model_copy(update={"question_type": paired_question_type})
                )
            except AppError:
                logger.warning(
                    "짝 주차 정보 조회 실패(1차 답변에는 영향 없음): %s → %s",
                    llm_output.info.question_type.value,
                    paired_question_type,
                    exc_info=True,
                )
                paired_response = None
            if paired_response is not None and paired_response.status == "success":
                secondary_info_place_card = to_answer_info_place_card(paired_response)
                if secondary_info_place_card is not None:
                    message = compose_paired_parking_message(
                        message, question_type=llm_output.info.question_type
                    )
            elif paired_response is not None:
                logger.info(
                    "짝 주차 정보 없음(%s): %s → %s",
                    paired_response.status,
                    llm_output.info.question_type.value,
                    paired_question_type,
                )

        return AgentResponse(
            llm_output=llm_output,
            state=state_response,
            recommendations=None,
            info_place_card=to_answer_info_place_card(info_response),
            secondary_info_place_card=secondary_info_place_card,
            message=message,
            message_footnote=unsupported_region_footnote(
                info_response.error.code if info_response.error else None
            ),
            llm_execution=get_llm_execution_metadata(),
            tool_execution=info_execution,
            tool_executions=[info_execution] if info_execution is not None else [],
        )

    # 4-1) COMPARE는 마지막 추천 이력의 Feature 스냅샷을 A가 targets로 해석해
    #      C에 넘기고, C가 place_id를 장소명으로 보강한 사실만 LLM 요약에 사용한다.
    #      새 추천 후보 검색·D 재점수화는 하지 않는다(D-050, int-04-compare.md §13).
    if (
        llm_output.status is OutputStatus.COMPLETE
        and llm_output.intent is Intent.COMPARE
        and llm_output.compare is not None
    ):
        if len(session_context.shown_place_ids) <= 1:
            # COMPARE 전제조건(노출 2개 이상)을 구조적으로 위반한다 — LLM이 그렇게
            # 분류했어도 비교 대상 자체가 성립하지 않는다(2026-08-11 68건 테스트에서
            # thinking 예산에 따라 COMPARE/RECOMMEND가 갈리는 걸로 확인, 케이스 3).
            # to_compare_context_request()의 기존 "비교할 장소가 더 필요해요" 안내
            # 대신 다음 행동을 바로 고를 수 있는 되묻기 버튼을 준다.
            clarification_llm_output = llm_output.model_copy(
                update={
                    "status": OutputStatus.NEEDS_CLARIFICATION,
                    "clarification": ClarificationPayload(
                        message="지금 보여드린 곳이 마음에 드시나요, 다른 곳도 보여드릴까요?",
                        options=[
                            ClarificationOption(
                                id=_COMPARE_SINGLE_SHOWN_KEEP_CURRENT,
                                label="지금 장소가 마음에 들어요",
                                resolved_intent=Intent.GENERAL,
                            ),
                            ClarificationOption(
                                id=_COMPARE_SINGLE_SHOWN_SHOW_MORE,
                                label="다른 곳도 보여주세요",
                                resolved_intent=Intent.MODIFY,
                            ),
                        ],
                    ),
                }
            )
            _remember_clarification(state_response.session_id, "compare_single_shown", store)
            message = await compose_chat_message(clarification_llm_output, llm=llm)
            return AgentResponse(
                llm_output=clarification_llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                llm_execution=get_llm_execution_metadata(),
            )
        resolution = to_compare_context_request(
            new_trace_id(), llm_output.compare, session_context.shown_recommendations
        )
        if resolution.request is None:
            return AgentResponse(
                llm_output=llm_output,
                state=state_response,
                recommendations=None,
                message=resolution.message or "비교할 장소를 확인할 수 없어요.",
                llm_execution=get_llm_execution_metadata(),
            )

        compare_started_at = time.monotonic()
        await _emit_progress(
            stream_event_sink,
            "fetching_context",
            "비교할 장소 정보를 확인하고 있어요.",
        )
        compare_response = await tool_provider.fetch_compare_context(resolution.request)
        compare_latency_ms = int((time.monotonic() - compare_started_at) * 1000)
        compare_execution = build_compare_execution_debug(
            compare_response, latency_ms=compare_latency_ms
        )
        _record_trace_safely(
            session_id=state_response.session_id,
            run_id=state_response.run_id,
            step="tool_fetch",
            latency_ms=compare_latency_ms,
            error_type=(
                compare_response.status
                if compare_response.status in {"no_data", "unavailable"}
                else None
            ),
            store=store,
        )
        comparison: ComparisonResult | None = to_comparison_result(compare_response)
        if comparison is None:
            message = (
                "비교에 필요한 장소 정보가 부족해요. 다른 추천을 볼까요?"
                if compare_response.status == "no_data"
                else "일시적으로 비교 정보를 확인하지 못했어요. 잠시 후 다시 시도해주세요."
            )
            return AgentResponse(
                llm_output=llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                llm_execution=get_llm_execution_metadata(),
                tool_execution=compare_execution,
                tool_executions=[compare_execution] if compare_execution is not None else [],
            )

        if comparison.criteria is CompareCriteria.TRAVEL_TIME:
            await _emit_progress(
                stream_event_sink,
                "fetching_context",
                "실제 이동시간을 확인하고 있어요.",
            )
            compare_origin_location = valid_gps or state_response.api_context.gps_location
            comparison = await _fetch_compare_travel_routes(
                travel_route_tool,
                origin_location=compare_origin_location,
                comparison=comparison,
            )

        await _emit_progress(
            stream_event_sink,
            "composing_message",
            "비교 결과를 정리하고 있어요.",
        )
        message = await compose_compare_message(comparison, llm)
        return AgentResponse(
            llm_output=llm_output,
            state=state_response,
            recommendations=None,
            comparison=comparison,
            message=message,
            llm_execution=get_llm_execution_metadata(),
            tool_execution=compare_execution,
            tool_executions=[compare_execution] if compare_execution is not None else [],
        )

    # 4) 확인이 더 필요하거나(needs_clarification), RECOMMEND/MODIFY/SCHEDULE이 아니면
    #    (INFO/COMPARE/GENERAL/OUT_OF_SCOPE) 여기서 끝난다 — Tool/Recommendation은
    #    부가 흐름이라 스킵한다. SCHEDULE도 D 호출까지 이어져야 하므로 포함한다
    #    (docs/design/int-07-schedule.md 4절).
    if llm_output.status is not OutputStatus.COMPLETE or llm_output.intent not in (
        Intent.RECOMMEND,
        Intent.MODIFY,
        Intent.SCHEDULE,
    ):
        # LLM이 되물은 경우만 기록한다. INFO/GENERAL 같은 다른 Intent는 조건을 건드리지
        # 않으므로, 이전 되묻기가 있었다면 그대로 살려둔다(곁가지 대화로 취급).
        if llm_output.status is not OutputStatus.COMPLETE:
            _remember_clarification(
                state_response.session_id, _llm_clarification_code(llm_output), store
            )
            # INFO가 장소명이 없어 되묻는 경우(place_ambiguous와 달리 여기는 버튼이
            # 없어 자유 텍스트가 유일한 응답 경로다) question_type 등 이미 파악한
            # 정보를 저장해둔다 — 안 남기면 다음 턴이 장소명만으로 처음부터
            # 재분류되어 "혼잡도 질문이었다"는 사실이 사라진다(2026-08-31 실사용
            # 재현). info/extract.md가 "반드시 info 필드를 채우고"라고 지시하므로
            # (place_name이 없어도) question_type/specific_question/visit_time은
            # 채워져 있다. place_ambiguous는 이 지점 이전에 이미 별도로 저장하고
            # 반환하므로 여기서 다시 저장되지 않는다.
            if llm_output.intent is Intent.INFO and llm_output.info is not None:
                set_pending_info_context(
                    SetPendingInfoContextRequest(
                        session_id=state_response.session_id,
                        context=PendingInfoContext(
                            question_type=llm_output.info.question_type.value,
                            place_context=llm_output.info.place_context.value,
                            specific_question=llm_output.info.specific_question,
                            visit_time=llm_output.info.visit_time,
                        ),
                    ),
                    store=store,
                )
        # terminal_message가 있는 경우는 위(3단계 직후)에서 이미 처리하고
        # 반환했으므로 여기서는 다시 안 본다.
        is_streaming_general = stream_recommendation_summary and llm_output.intent is Intent.GENERAL
        # 대화층 3·4단계 — 이번 턴 이전에 이미 거절된 제안이면 답변 문구도, 버튼도
        # 다시 권하지 않는다. session_context는 이번 턴 이전 상태이므로 이번 턴
        # 자체가 방금 거절한 제안은 여기 안 잡히지만, 그 경로(터미널 메시지)는 이
        # 분기에 도달하기 전에 이미 반환한다.
        rejected_offer_actions = (
            session_context.situation_state.rejected_actions
            if session_context.situation_state is not None
            else []
        )
        if settings.use_langgraph_early_return:
            # 2단계: 조기 반환 경로(Tool/Scoring 없이 끝나는 턴) 전체를 라우팅
            # 그래프가 맡는다(langgraph-adoption.md §6.1). RECOMMEND/MODIFY/
            # SCHEDULE은 아래로 내려가 기존 경로 그대로다 — 병행 운영이라 문제가
            # 보이면 USE_LANGGRAPH_EARLY_RETURN=false 하나로 즉시 되돌아간다.
            message = await run_early_return_graph(
                llm_output,
                llm=llm,
                stream_event_sink=stream_event_sink,
                stream_general=is_streaming_general,
                rejected_offer_actions=rejected_offer_actions,
                history=turn_history,
            )
        else:
            if is_streaming_general:
                await _begin_streamed_message(
                    stream_event_sink,
                    intent=Intent.GENERAL,
                    progress_message="답변을 정리하고 있어요.",
                )

            async def emit_general_message_delta(text: str) -> None:
                await _emit_stream_event(stream_event_sink, "message_delta", {"text": text})

            message = await compose_chat_message(
                llm_output,
                llm=llm,
                on_message_delta=emit_general_message_delta if is_streaming_general else None,
                rejected_offer_actions=rejected_offer_actions,
            )
        # 상황 제안의 후속 버튼(대화층 4단계). LLM을 다시 부르지 않는다 — 무엇을
        # 제안할지는 이미 situational_offers가 코드로 정했다. 버튼을 누르면 이
        # 문구가 그대로 사용자 발화로 재전송되어(suggested_follow_ups의 기존 계약)
        # 자연스러운 RECOMMEND 요청이 된다. follow_up_suggester.suggest_follow_ups()가
        # 이 값이 이미 채워져 있으면 자기 LLM 호출을 건너뛰고 그대로 둔다.
        offer_follow_up: list[str] = []
        if llm_output.intent is Intent.GENERAL and llm_output.general is not None:
            offer = offer_for(llm_output.general.situation)
            if offer is not None and offer.action_id not in rejected_offer_actions:
                offer_follow_up = [offer.button_label]
        return AgentResponse(
            llm_output=llm_output,
            state=state_response,
            recommendations=None,
            message=message,
            suggested_follow_ups=offer_follow_up,
            llm_execution=get_llm_execution_metadata(),
        )

    if settings.use_langgraph_pipeline:
        # 3단계: Tool 조회부터 응답 조립까지를 라우팅 그래프가 맡는다
        # (langgraph-adoption.md §6.1). 노드는 아래에서 떼어낸 단계 함수를 호출만
        # 하므로 동작은 아래 기존 경로와 같다 — 문제가 보이면
        # USE_LANGGRAPH_PIPELINE=false 하나로 되돌아간다.
        return await run_recommend_pipeline_graph(
            {
                "request": request,
                "llm_output": llm_output,
                "state_response": state_response,
                "valid_gps": valid_gps,
                "effective_ignore_operating_hours": effective_ignore_operating_hours,
                "stream_recommendation_summary": stream_recommendation_summary,
                "session_context": session_context,
                "tool_executions": [],
                "response": None,
            },
            deps=PipelineDeps(
                llm=llm,
                tool_provider=tool_provider,
                recommendation_provider=recommendation_provider,
                enrichment_provider=enrichment_provider,
                travel_route_tool=travel_route_tool,
                store=store,
                principal=principal,
                place_details_repository=place_details_repository,
            ),
            stream_event_sink=stream_event_sink,
        )

    tool_outcome = await _fetch_tool_context(
        request,
        llm_output,
        state_response,
        valid_gps=valid_gps,
        effective_ignore_operating_hours=effective_ignore_operating_hours,
        llm=llm,
        tool_provider=tool_provider,
        travel_route_tool=travel_route_tool,
        store=store,
        shown_place_ids=_revivable_place_ids(llm_output, session_context),
        stream_event_sink=stream_event_sink,
    )
    if tool_outcome.terminal is not None:
        return tool_outcome.terminal
    assert tool_outcome.tool_context is not None
    assert tool_outcome.agent_conditions is not None
    tool_context = tool_outcome.tool_context
    agent_conditions = tool_outcome.agent_conditions
    context_gps = tool_outcome.context_gps
    tool_execution = tool_outcome.tool_execution
    tool_executions = tool_outcome.tool_executions

    is_schedule = llm_output.intent is Intent.SCHEDULE

    scoring_outcome = await _score_recommendations(
        state_response,
        tool_context=tool_context,
        agent_conditions=agent_conditions,
        context_gps=context_gps,
        is_schedule=is_schedule,
        shown_place_ids=_revivable_place_ids(llm_output, session_context),
        saved_places=session_context.saved_places,
        place_details_repository=place_details_repository,
        tool_provider=tool_provider,
        recommendation_provider=recommendation_provider,
        enrichment_provider=enrichment_provider,
        travel_route_tool=travel_route_tool,
        store=store,
        principal=principal,
        tool_executions=tool_executions,
        effective_ignore_operating_hours=effective_ignore_operating_hours,
        stream_event_sink=stream_event_sink,
    )
    recommendations = scoring_outcome.recommendations
    # 보충 조회·보관함 주입으로 좌표가 합쳐진 컨텍스트. 원본을 넘기면 그렇게 들어온
    # 후보의 좌표를 아래 단계가 못 찾는다(TP-198).
    tool_context = scoring_outcome.tool_context

    if is_schedule:
        return await _run_schedule_branch(
            llm_output,
            state_response,
            recommendations,
            tool_context=tool_context,
            agent_conditions=agent_conditions,
            session_context=session_context,
            llm=llm,
            store=store,
            principal=principal,
            tool_execution=tool_execution,
            tool_executions=tool_executions,
            effective_ignore_operating_hours=effective_ignore_operating_hours,
            stream_event_sink=stream_event_sink,
            travel_route_tool=travel_route_tool,
            place_details_repository=place_details_repository,
        )

    return await _finalize_recommendation_response(
        llm_output,
        state_response,
        recommendations,
        llm=llm,
        store=store,
        principal=principal,
        tool_context=tool_context,
        tool_execution=tool_execution,
        tool_executions=tool_executions,
        effective_ignore_operating_hours=effective_ignore_operating_hours,
        stream_recommendation_summary=stream_recommendation_summary,
        stream_event_sink=stream_event_sink,
    )


@dataclass(frozen=True)
class _ToolFetchOutcome:
    """Tool 조회 결과. 여기서 끝날 수도, 다음 단계로 넘어갈 수도 있다.

    ``terminal``이 채워져 있으면 그 응답으로 이번 턴을 끝낸다(C가 되묻기·no_data·
    unsupported를 돌려준 경우). 비어 있으면 나머지 칸이 다음 단계 입력이 된다.
    """

    terminal: AgentResponse | None = None
    tool_context: RecommendationContext | None = None
    agent_conditions: UserConditions | None = None
    context_gps: str | None = None
    tool_execution: ToolExecutionDebug | None = None
    tool_executions: list[ToolExecutionDebug] = dataclass_field(default_factory=list)


@dataclass(frozen=True)
class _ScoringOutcome:
    """Scoring 결과와, 그 과정에서 좌표가 합쳐진 후보 컨텍스트.

    `tool_context`를 함께 돌려주는 것이 요점이다. `_score_recommendations()`는
    보충 조회(D-112)와 보관함 주입(D-114)으로 받은 후보를 `tool_context`에 합치는데,
    예전에는 그 결과가 함수 안 지역 변수로만 남아 밖으로 나가지 못했다. 그래서
    **후보는 합친 목록에서 뽑고 좌표는 합치기 전 목록에서 찾는** 상태였고,
    보충·주입으로 들어온 장소는 `_build_pairwise_distances_km()`에서 조용히
    건너뛰어져 거리 근거 없이 일정에 배치됐다(TP-198).

    호출부는 원본이 아니라 이 `tool_context`를 다음 단계로 넘겨야 한다.
    """

    recommendations: RecommendationResponse
    tool_context: RecommendationContext


async def _fetch_tool_context(
    request: AgentRequest,
    llm_output: LLMOutput,
    state_response: StateApplyResponse,
    *,
    valid_gps: str | None,
    effective_ignore_operating_hours: bool,
    llm: LLMProvider,
    tool_provider: ToolProvider,
    travel_route_tool: TravelRouteToolProvider | None,
    store: StateStore | None,
    # 마지막 run에서 보여준 place_id. 새 SCHEDULE 턴에서 제외 목록을 되살리는 데만
    # 쓴다(TP-180). 조회 단계에서 이미 걸러지면 채점 단계에는 후보 자체가 없다.
    shown_place_ids: Sequence[str] = (),
    # A-1(자기 교정 루프): no_data_empty 1차 재시도에서 그래프가 넘겨준다. 값이 있으면
    # C에 보내기 전에 max_travel_time을 이 값으로 덮어써 반경을 넓힌다 — 수동
    # "검색 범위 넓히기" 버튼(_WIDEN_RADIUS)과 같은 처방을 사람 개입 없이 먼저 한 번
    # 시도하는 것뿐이라, 값 자체는 기존 _WIDEN_RADIUS_MAX_TRAVEL_TIME을 그대로 쓴다.
    radius_override_max_travel_time: int | None = None,
    stream_event_sink: StreamEventSink | None,
) -> _ToolFetchOutcome:
    """A → C Tool 조회와 종료 상태 판정(5단계).

    `run_agent_flow()`의 5단계 블록을 그대로 옮긴 것이다 — 라우팅 그래프가 이 단계를
    노드로 감쌀 수 있게 먼저 함수로 떼어냈다(langgraph-adoption.md §6.1 3단계).
    떼어낼 당시에는 본문을 한 줄도 바꾸지 않고 중간 반환만 `_ToolFetchOutcome`으로
    포장했다. 이후 TP-180으로 C에 넘기는 제외 목록을 고르는 한 줄과, A-1의 반경
    확대 재시도(radius_override_max_travel_time)가 붙었다.
    """

    # 5) A → C: Tool 결과 확보 (Protocol을 통해서만 — C의 구체 클래스는 여기서 모른다).
    #    B가 준 조건(순수 문자열)을 A의 enum 타입으로 바꾼 뒤 C 계약 형태로 변환한다.
    #    conditions.weather(5단계 rain/snow/hot/cold/good)만 넘기고, api_context.api_weather
    #    (3단계 good/neutral/bad, Provider 정규화 값)는 여기 관여하지 않는다. GPS는
    #    사용자 조건과 별도 인자로 전달되어 Coordinates로 변환된다(계약 §5.2).
    agent_conditions = to_user_conditions(state_response.user_conditions)
    if radius_override_max_travel_time is not None:
        agent_conditions = agent_conditions.model_copy(
            update={"max_travel_time": radius_override_max_travel_time}
        )
    # 이번 요청의 유효한 GPS를 우선하고, 없으면 B에 저장된 신선한 GPS를 재사용한다.
    # 문자열은 A→C 변환 경계에서 Coordinates로 바뀌며 C는 원본 문자열을 알지 않는다.
    context_gps = valid_gps
    if context_gps is None and not state_response.api_context.gps_expired:
        context_gps = state_response.api_context.gps_location
    context_request = to_agent_context_request(
        request_id=new_trace_id(),
        conditions=agent_conditions,
        gps_location=context_gps,
        # D에 넘기는 것과 같은 소진분을 C에도 넘긴다. C는 이걸로 판정하지 않고
        # 수집 범위를 그만큼 넓히는 데만 쓴다 — 안 넘기면 "다른 곳 보여줘"에
        # 같은 후보가 다시 와서 D가 전부 걸러내고 0건이 된다.
        excluded_place_ids=_effective_excluded_place_ids(
            state_response.excluded_place_ids,
            shown_place_ids=shown_place_ids,
            is_schedule=llm_output.intent is Intent.SCHEDULE,
        ),
    )
    await _emit_progress(
        stream_event_sink,
        "fetching_context",
        "장소·운영시간·날씨 정보를 찾고 있어요.",
    )
    tool_started_at = time.monotonic()
    tool_response = await tool_provider.fetch_context(context_request)
    tool_latency_ms = int((time.monotonic() - tool_started_at) * 1000)
    # 개발자용 Audit 표시 정보. 아래 어느 경로로 응답이 끝나든 C를 호출한 사실은
    # 남아야 하므로 여기서 한 번만 만들어 모든 return에 함께 싣는다.
    tool_execution = build_tool_execution_debug(
        tool_response, latency_ms=tool_latency_ms, conditions=agent_conditions
    )
    tool_executions = [tool_execution] if tool_execution is not None else []
    _record_trace_safely(
        session_id=state_response.session_id,
        run_id=state_response.run_id,
        step="tool_fetch",
        latency_ms=tool_latency_ms,
        error_type=(
            tool_response.status if tool_response.status in _TOOL_TERMINAL_STATUSES else None
        ),
        store=store,
    )

    # 5-1) C 단계 자체의 needs_clarification/unsupported/unavailable — LLM 단계
    #      needs_clarification(4번)과 같은 방식으로 여기서 바로 응답을 끝낸다.
    if tool_response.status in _TOOL_TERMINAL_STATUSES:
        if tool_response.status == "needs_clarification" and tool_response.error is not None:
            # 계약(§5.5)상 needs_clarification이면 error는 항상 null이어야 한다. 위반이면
            # 흐름을 막지 않고 로그만 남긴다 — A가 사용자에게 재질문하는 데는 지장이 없다.
            logger.warning(
                "C 응답이 needs_clarification인데 error도 채워짐(계약 위반 의심): "
                "request_id=%s clarification=%s error=%s",
                tool_response.request_id,
                tool_response.clarification,
                tool_response.error,
            )
        if tool_response.status == "needs_clarification":
            code = (
                tool_response.clarification.code
                if tool_response.clarification is not None
                else "clarification_required"
            )
            _remember_clarification(state_response.session_id, code, store)
            if code == "location_required":
                # 위치 신호가 전혀 없다. GPS로 가장 가까운 지원 구를 짐작할 수 있으면
                # 그 구 대표 스팟을, 그마저 없으면 종로구 고정 스팟으로 대신한다
                # (TP-160, docs/design/clarification-options.md 7절 A2). resolved_intent는
                # 이번 턴의 intent를 그대로 표시용으로 담는다 — 실제 해소는 다음 턴에
                # last_intent로 복원한다(_resolve_clarification_choice).
                llm_output = llm_output.model_copy(
                    update={
                        "status": OutputStatus.NEEDS_CLARIFICATION,
                        "clarification": ClarificationPayload(
                            message=tool_clarification_message(code),
                            options=[
                                ClarificationOption(
                                    id=name,
                                    label=f"{name} 근처",
                                    resolved_intent=llm_output.intent,
                                )
                                for name in _location_required_quick_picks(context_gps)
                            ],
                        ),
                    }
                )
            elif code == "location_ambiguous" and tool_response.clarification is not None:
                # 동명이인 장소 후보(Tool이 실제로 찾아낸 지하철역·명소 이름)를
                # 버튼으로 준다. resolve_location.py가 이미 식당·상점류는 걸러내고
                # 넘긴다 — 여기서 candidates가 비어 있으면(전부 식당·상점뿐이었거나
                # 지오코딩 경로라 이름 자체를 모르면) 식당을 보여주는 대신, 사용자가
                # 말한 지역명("용산")이나 GPS로 짐작한 구의 대표 스팟으로 대신한다
                # (TP-160 — 예전엔 종로구 고정 스팟이었는데, 서비스 지역이 16개 구로
                # 늘어난 뒤에도 안 바뀌어 "용산"에도 종로구 버튼이 뜨는 버그였다).
                found_candidates = tool_response.clarification.candidates
                if found_candidates:
                    message = tool_clarification_message(code)
                    options = [
                        ClarificationOption(id=name, label=name, resolved_intent=llm_output.intent)
                        for name in found_candidates
                    ]
                else:
                    message = (
                        "말씀하신 곳이 넓은 지역이라 어디인지 콕 짚기 어려워요. "
                        "아래 후보 중에 골라주세요. 다른 곳이면 더 자세히 알려주셔도 좋아요."
                    )
                    options = [
                        ClarificationOption(
                            id=name, label=f"{name} 근처", resolved_intent=llm_output.intent
                        )
                        for name in _location_ambiguous_quick_picks(agent_conditions, context_gps)
                    ]
                llm_output = llm_output.model_copy(
                    update={
                        "status": OutputStatus.NEEDS_CLARIFICATION,
                        "clarification": ClarificationPayload(
                            message=message,
                            options=options,
                        ),
                    }
                )
        elif tool_response.status == "no_data":
            # 원인2(이전 노출/거절로 소진)는 C가 실제 후보와 excluded_place_ids를
            # 비교해 남긴 명시 경고로만 구분한다. Provider 성공만으로는 원인1+3과
            # 구분할 수 없어 "다 보여드렸어요"라는 오안내를 만들 수 있다.
            places_value = (
                tool_response.context.places if tool_response.context is not None else None
            )
            warning_codes = {item.code for item in (places_value.warnings if places_value else [])}
            if "candidate_pool_exhausted" in warning_codes:
                no_data_code = "no_data_exhausted"
                no_data_message = _NO_DATA_EXHAUSTED_MESSAGE
                no_data_options = _NO_DATA_EXHAUSTED_OPTIONS
            else:
                no_data_code = "no_data_empty"
                no_data_message = _NO_DATA_EMPTY_MESSAGE
                no_data_options = _NO_DATA_EMPTY_OPTIONS
            # "검색 범위를 넓혀볼까요?"에 대한 답변은 새 요청이 아니라 이번 요청을
            # 이어가는 발화다. 표시해두지 않으면 다음 턴이 RECOMMEND로 분류되면서
            # soft reset이 걸려 앞 턴 조건(장소·태그)이 사라진다(D-039와 같은 이유).
            _remember_clarification(state_response.session_id, no_data_code, store)
            llm_output = llm_output.model_copy(
                update={
                    "status": OutputStatus.NEEDS_CLARIFICATION,
                    "clarification": ClarificationPayload(
                        code=no_data_code,
                        message=no_data_message,
                        options=[
                            ClarificationOption(
                                id=option_id, label=label, resolved_intent=llm_output.intent
                            )
                            for option_id, label in no_data_options
                        ],
                    ),
                }
            )
        tool_error_code = tool_response.error.code if tool_response.error else None
        message = await compose_chat_message(
            llm_output,
            tool_status=tool_response.status,
            tool_clarification=tool_response.clarification,
            tool_error_code=tool_error_code,
            llm=llm,
        )
        return _ToolFetchOutcome(
            terminal=AgentResponse(
                llm_output=llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                message_footnote=unsupported_region_footnote(tool_error_code),
                llm_execution=get_llm_execution_metadata(),
                tool_execution=tool_execution,
                tool_executions=tool_executions,
            )
        )

    # success/partial은 Recommendation 단계로 진행한다(경고가 있어도 가능한 데이터로
    # 계속 — 계약 문서 §5.4). 위에서 종료 상태를 걸렀으므로 context는 항상 있다.
    # AgentContextResponse.warnings(최상위)만 지금은 보고 넘어간다.
    # TODO(자연어 응답 생성 단계): RecommendationContext의 항목별 ContextValue.warnings
    # (예: weather.warnings)까지 합쳐서 사용자에게 보여줄지 다시 검토한다.
    tool_context = tool_response.context
    if tool_context is None:
        # success/partial은 Schema가 Context를 강제하지만 no_data는 아직 None을 허용한다.
        # 잘못되거나 불완전한 C 응답을 D에 전달하지 않고 이번 실행을 안전하게 끝낸다.
        logger.warning(
            "C 응답에 RecommendationContext가 없음: request_id=%s status=%s",
            tool_response.request_id,
            tool_response.status,
        )
        message = await compose_chat_message(llm_output, tool_status=tool_response.status, llm=llm)
        return _ToolFetchOutcome(
            terminal=AgentResponse(
                llm_output=llm_output,
                state=state_response,
                recommendations=None,
                message=message,
                llm_execution=get_llm_execution_metadata(),
                tool_execution=tool_execution,
                tool_executions=tool_executions,
            )
        )

    return _ToolFetchOutcome(
        tool_context=tool_context,
        agent_conditions=agent_conditions,
        context_gps=context_gps,
        tool_execution=tool_execution,
        tool_executions=tool_executions,
    )


def _revivable_place_ids(
    llm_output: LLMOutput, session_context: SessionContextResponse
) -> Sequence[str]:
    """이번 턴에 후보로 되살려도 되는 place_id를 고른다 (TP-180 → SCHEDULE-12).

    두 출처를 합친다.

    **직전 노출분(`shown_place_ids`)** 은 **새 SCHEDULE 턴**에만 되살린다. 재조정
    턴(REJECT_ALL "다른 곳 보여줘", REJECT_SPECIFIC "두 번째는 별로야")은 MODIFY로
    분류된 뒤 SCHEDULE로 relabel되며, 사용자가 방금 그 장소들을 거절한 턴이다.
    거절 대상은 `rejected`로 제외 목록에 들어가지만 `shown_place_ids`에도 그대로
    남아 있어, 구분 없이 되살리면 REJECT 이력이 통째로 무력화된다(재조정해도 같은
    장소가 다시 나온다). relabel된 재조정 턴은 `llm_output.modify`가 채워져 있다 —
    `_run_schedule_branch()`가 pinned_items를 쓸지 판단하는 것과 같은 신호다.

    **보관함(`saved_places`)** 은 재조정 턴에도 되살린다(SCHEDULE-12). 사용자가
    명시적으로 담아둔 것이라, "두 번째는 별로야"가 담아둔 나머지까지 후보에서
    빼야 할 이유가 없다. 거절과 겹칠 걱정도 없다 — `record_rejected()`가 거절
    시점에 같은 place_id를 보관함에서 빼므로 `saved ∩ rejected = ∅`이 구조적으로
    보장된다(state/history.py). 그래서 여기서 타임스탬프를 비교하지 않는다.

    보관함이 `shown_place_ids`와 별도로 필요한 이유는 후자가 **마지막 run만**
    담기 때문이다(`history.get_shown_place_ids()`) — 3턴 전에 담은 장소는 거기
    없어서 제외 목록에 그대로 남고 후보에서 빠진다.
    """

    saved_place_ids = [item.place_id for item in session_context.saved_places]
    if llm_output.modify is not None:
        return saved_place_ids
    return [*session_context.shown_place_ids, *saved_place_ids]


def _effective_excluded_place_ids(
    excluded_place_ids: Sequence[str],
    *,
    shown_place_ids: Sequence[str],
    is_schedule: bool,
) -> list[str]:
    """SCHEDULE 턴에 한해 직전 턴에 보여준 장소를 제외 목록에서 뺀다 (TP-180).

    B의 제외 목록은 `recommended ∪ rejected ∪ closed_excluded`라(state/history.py)
    직전 턴에 추천한 장소가 이미 들어 있다. SCHEDULE은 직전 노출분을 그대로 쓰지
    않고 후보를 새로 채점하는데(`_run_schedule_branch`의 `schedule_candidates`),
    그 채점에 이 목록이 그대로 적용되면 "이 장소들로 일정 짜줘"가 "이 장소들만 빼고
    짜줘"로 동작한다 — 사용자가 방금 본 장소가 한 곳도 일정에 들어갈 수 없다.

    그래서 SCHEDULE 턴에서만 마지막 run의 노출분을 후보 풀로 되살린다. 제외 목록의
    의미(`get_exclusion_place_ids()`의 계약)는 바꾸지 않는다 — 이 턴에 무엇을
    넘길지만 조정한다. 무엇을 되살릴지는 `_revivable_place_ids()`가 정한다 —
    거절된 장소도 shown에 남아 있어 그대로 되살리면 REJECT 이력이 무력화된다.
    RECOMMEND를 연속 요청하는 흐름은 `is_schedule=False`라 영향을 받지 않는다.
    """

    if not is_schedule or not shown_place_ids:
        return list(excluded_place_ids)
    revived = set(shown_place_ids)
    return [place_id for place_id in excluded_place_ids if place_id not in revived]


async def _score_recommendations(
    state_response: StateApplyResponse,
    *,
    tool_context: RecommendationContext,
    agent_conditions: UserConditions,
    context_gps: str | None,
    is_schedule: bool,
    # 마지막 run에서 사용자에게 보여준 place_id(rank 순). SCHEDULE 턴에서
    # 제외 목록을 되살리는 데만 쓴다(TP-180).
    shown_place_ids: Sequence[str] = (),
    # 사용자가 담아둔 장소(담은 순서). SCHEDULE 턴에서 이번 후보에 없는 것을
    # 후보 컨텍스트에 주입하는 데만 쓴다 — 안 넣으면 D-114의 배치 보장이
    # `planner._resolve_must_include()`에서 조용히 무력해진다.
    saved_places: Sequence[SavedPlaceItem] = (),
    place_details_repository: PlaceDetailsReadRepository | None = None,
    tool_provider: ToolProvider,
    recommendation_provider: RecommendationProvider,
    enrichment_provider: EnrichmentProvider,
    travel_route_tool: TravelRouteToolProvider | None,
    store: StateStore | None,
    principal: Principal | None,
    tool_executions: list[ToolExecutionDebug],
    effective_ignore_operating_hours: bool,
    stream_event_sink: StreamEventSink | None,
    # 후보별 실측 이동수단 판정에 쓴다(TP-227). 없으면 기존 거리 규칙으로 돈다.
    llm: LLMProvider | None = None,
) -> _ScoringOutcome:
    """1차 Scoring과 후보 보충·혼잡도 재정렬까지 끝난 추천 결과를 돌려준다(6단계).

    `run_agent_flow()`의 6단계 블록을 그대로 옮긴 것이다 — 라우팅 그래프가 이 단계를
    노드로 감쌀 수 있게 먼저 함수로 떼어냈다(langgraph-adoption.md §6.1 3단계).
    이 구간에는 중간 반환이 없어 결과 하나만 돌려주면 되는, 경계가 가장 깨끗한
    단계다. 떼어낼 당시에는 본문을 한 줄도 바꾸지 않았고, 이후 TP-180으로 제외
    목록을 고르는 한 줄(`_effective_excluded_place_ids()`)만 앞에 붙었다.

    **추천 결과만이 아니라 `tool_context`도 함께 돌려준다**(TP-198). 이 함수는
    보충 조회·보관함 주입으로 받은 후보 좌표를 `tool_context`에 합치는데, 그 값을
    안 돌려주면 일정 편성과 노출 이력 기록이 합치기 전 원본을 받는다. 자세한 사정은
    `_ScoringOutcome` docstring에 있다.
    """

    excluded_place_ids = _effective_excluded_place_ids(
        state_response.excluded_place_ids,
        shown_place_ids=shown_place_ids,
        is_schedule=is_schedule,
    )

    # 6) A → D: 1차 Scoring (Protocol을 통해서만 — D의 구체 클래스는 여기서 모른다).
    #    최종 반환은 RECOMMEND/MODIFY가 recommendation_result_limit,
    #    SCHEDULE이 SCHEDULE_RECOMMENDATION_LIMIT을 쓴다
    #    (docs/design/int-07-schedule.md 2절/5절).
    #
    #    보충 조회 목표(candidate_target)는 recommendation_candidate_limit이다 —
    #    하드 필터를 통과한 후보를 설정된 후보 상한만큼 모아두고 그 안에서 고른다.
    #    (읽는 사람이 다시 파지 않도록 짚어둔다: C가 한 번에 반환하는 최대 후보 수도
    #    같은 설정값이라, 첫 조회에서 한 곳이라도 걸러지면 이 목표에는 도달할 수 없다.
    #    그래서 실제 동작은 "목표를 채운다"가 아니라 "_MAX_CANDIDATE_REFILL_ATTEMPTS
    #    회까지 더 긁어 모은다"에 가깝고, 후보가 넉넉한 지역에서는 C 호출이 최대 3회로
    #    늘어난다. 반경에 후보가 적으면 _candidate_pool_exhausted()가 첫 조회에서
    #    잡아내 보충하지 않는다.)
    candidate_target = settings.recommendation_candidate_limit
    recommendation_limit = (
        SCHEDULE_RECOMMENDATION_LIMIT
        if is_schedule
        else settings.recommendation_result_limit
    )
    await _emit_progress(
        stream_event_sink,
        "scoring",
        "조건에 맞게 장소 순위를 계산하고 있어요.",
    )
    scoring_started_at = time.monotonic()
    # 주입한 보관함 장소 id. 채점이 끝난 뒤 상위 N 자르기에서 빠졌는지 확인해
    # 다시 붙이는 데 쓴다(아래 두 분기).
    #
    # 처음에는 상한을 주입 개수만큼 올리는 것으로 막으려 했는데 방어가 되지
    # 않는다 — 후보 풀이 상한보다 크면 주입분은 그냥 하위권에 깔린다. 하필
    # 보관함의 주력 유스케이스가 구 간 이동(= 검색 반경 밖)이라 거리 점수가
    # 0으로 깔려, 가장 확실하게 잘리는 것이 보관함 장소다. 게다가 상한을 올리면
    # _score_with_measured_routes()의 shortlist_limit이 같이 커져 도보 실측
    # 조회까지 늘어난다(D-113이 줄여놓은 것을 되돌린다).
    # **주입한 것만 지키면 절반만 지키는 것이다**(TP-223). `_saved_places_context()`는
    # "이번 턴 후보에 없는" 보관함 장소만 주입하므로, 후보에 이미 들어 있던 보관함
    # 장소는 주입 대상도 복구 대상도 아니었다 — 자르기에 밀리면 아무도 되붙이지
    # 않았고 사용자에게는 "이번에 찾은 후보에 없어서"로 나갔다. 담은 것은 후보에
    # 있었든 주입됐든 똑같이 지킨다.
    protected_saved_ids = (
        [item.place_id for item in saved_places] if is_schedule else []
    )
    if isinstance(recommendation_provider, StagedRecommendationProvider):
        # 같은 실행의 모든 prepare가 동일한 운영시간 기준을 사용해야 한다. B 세션에는
        # 저장하지 않고 이 실행 동안만 고정한다.
        visit_at = now_kst()
        prepared_batches = [
            await recommendation_provider.prepare(
                agent_conditions,
                tool_context,
                excluded_place_ids,
                visit_at=visit_at,
                ignore_operating_hours=effective_ignore_operating_hours,
            )
        ]
        run_seen_ids = _context_place_ids(tool_context)
        run_seen_id_set = set(run_seen_ids)
        candidate_pool_exhausted = _candidate_pool_exhausted(tool_context)

        for refill_attempt in range(1, _MAX_CANDIDATE_REFILL_ATTEMPTS + 1):
            merged_prepared = recommendation_provider.merge_prepared(prepared_batches)
            if (
                merged_prepared.preparation.eligible_count >= candidate_target
                or candidate_pool_exhausted
            ):
                break

            refill_request = to_agent_context_request(
                request_id=new_trace_id(),
                conditions=agent_conditions,
                gps_location=context_gps,
                excluded_place_ids=[
                    *excluded_place_ids,
                    *(
                        place_id
                        for place_id in run_seen_ids
                        if place_id not in excluded_place_ids
                    ),
                ],
                # 첫 조회가 확정한 기준점을 넘겨 C가 장소만 다시 주게 한다. 보충
                # 배치의 위치·날씨·공휴일은 아래 병합에서 버려지므로(첫 배치 값을
                # 그대로 쓴다) 계산할 이유가 없다 — 보충 1회가 외부 호출 7건에서
                # 2건으로 준다.
                resolved_search_center=_search_center_of(tool_context),
            )
            # stage는 "scoring"을 유지한다 — 프론트가 stage로 진행 순서를 그리므로
            # 여기서 fetching_context로 되돌리면 진행 표시가 뒤로 간다. 문구만 바꾼다
            # (AgentProgressMessage.tsx가 detail을 서버 message로 덮어쓴다).
            await _emit_progress(
                stream_event_sink,
                "scoring",
                "조건에 맞는 장소를 조금 더 찾고 있어요.",
            )
            refill_started_at = time.monotonic()
            refill_response = await tool_provider.fetch_context(refill_request)
            refill_latency_ms = int((time.monotonic() - refill_started_at) * 1000)
            refill_execution = build_tool_execution_debug(
                refill_response,
                latency_ms=refill_latency_ms,
                conditions=agent_conditions,
            )
            if refill_execution is not None:
                tool_executions.append(refill_execution)
            _record_trace_safely(
                session_id=state_response.session_id,
                run_id=state_response.run_id,
                step=f"tool_refill_{refill_attempt}",
                latency_ms=refill_latency_ms,
                error_type=(
                    refill_response.status
                    if refill_response.status in _TOOL_TERMINAL_STATUSES
                    else None
                ),
                store=store,
            )

            # 최초 조회에서 이미 사용할 후보가 있으므로, 보충 조회 실패는 전체 요청을
            # 실패시키지 않고 확보된 후보로 진행한다.
            if refill_response.status in _TOOL_TERMINAL_STATUSES:
                break
            refill_context = refill_response.context
            if refill_context is None:
                break

            refill_place_ids = _context_place_ids(refill_context)
            new_place_ids = [
                place_id for place_id in refill_place_ids if place_id not in run_seen_id_set
            ]
            if not new_place_ids:
                break
            run_seen_ids.extend(new_place_ids)
            run_seen_id_set.update(new_place_ids)

            try:
                refill_prepared = await recommendation_provider.prepare(
                    agent_conditions,
                    refill_context,
                    excluded_place_ids,
                    visit_at=visit_at,
                    ignore_operating_hours=effective_ignore_operating_hours,
                )
            except AppError:
                # 보충 Context가 장소는 실었지만 location이 없거나 places가
                # unavailable인 경우다. 위와 같은 이유로 확보분으로 진행한다.
                break

            # 보충 조회가 날씨를 다시 조회해 값이 달라져도 여기서 배치를 버리지
            # 않는다 — merge_prepared()가 첫 배치의 판정 기준을 그대로 재사용한다.
            # 모든 prepare에 같은 visit_at/ignore_operating_hours를 넘기는 것만
            # 지키면 된다(그 둘이 어긋나면 merge_prepared()가 ValueError를 던진다).
            prepared_batches.append(refill_prepared)
            tool_context = _merge_recommendation_context_places(
                tool_context,
                refill_context,
            )
            candidate_pool_exhausted = _candidate_pool_exhausted(refill_context)

        # 보충 루프가 끝난 뒤에 붙인다. 루프 안에서 넣으면 C가 뒤늦게 같은 장소를
        # 돌려줬을 때 배치가 겹치고, 소진 판정(_candidate_pool_exhausted)이 보는
        # 후보 수도 실제 C 응답과 어긋난다.
        saved_context = await _saved_places_context(
            tool_context,
            saved_places=saved_places if is_schedule else (),
            place_details_repository=place_details_repository,
        )
        if saved_context is not None:
            # 제외 목록을 넘기지 않는다 — 보관함은 사용자가 직접 고른 것이고,
            # record_rejected()가 거절과의 교집합을 구조적으로 비워둔다.
            prepared_batches.append(
                await recommendation_provider.prepare(
                    agent_conditions,
                    saved_context,
                    [],
                    visit_at=visit_at,
                    ignore_operating_hours=effective_ignore_operating_hours,
                )
            )
            tool_context = _merge_recommendation_context_places(tool_context, saved_context)

        merged_prepared = recommendation_provider.merge_prepared(prepared_batches)
        # 계정에 저장해 둔 취향에서 발화와 부딪히지 않는 칩을 골라 온다. 발화에
        # 취향이 있어도 값이 있고, 호출부가 발화 질의 뒤에 이어 붙인다.
        #
        # 한 번만 읽어 1차·2차 채점과 보관함 덧붙이기가 같은 값을 쓰게 한다 —
        # 회차 중간에 갈리면 취향으로 후보를 좁혀 놓고 최종 순위에서 다른 자를 쓴다.
        saved_taste_query = _saved_taste_query(agent_conditions, principal, store)
        recommendations = await _score_with_measured_routes(
            recommendation_provider,
            agent_conditions,
            merged_prepared,
            tool_context=tool_context,
            travel_route_tool=travel_route_tool,
            recommendation_limit=recommendation_limit,
            llm=llm,
            saved_taste_query=saved_taste_query,
        )
        # 자르기에서 빠진 보관함 장소만 좁혀서 한 번 더 채점해 붙인다. 점수를
        # 지어내지 않는 것이 핵심이다 — D가 같은 공식으로 실제로 매긴다.
        cut_saved_ids = _missing_place_ids(recommendations, protected_saved_ids)
        if cut_saved_ids:
            # merged_prepared에는 원래 후보 배치와 주입 배치가 함께 들어 있어
            # 좁히는 것만으로 두 출처를 모두 덮는다.
            pinned = await recommendation_provider.score_prepared(
                agent_conditions,
                _narrow_prepared(merged_prepared, cut_saved_ids),
                limit=len(cut_saved_ids),
                saved_taste_query=saved_taste_query,
            )
            recommendations = _with_pinned_recommendations(
                recommendations, pinned, cut_saved_ids
            )
    else:
        saved_context = await _saved_places_context(
            tool_context,
            saved_places=saved_places if is_schedule else (),
            place_details_repository=place_details_repository,
        )
        if saved_context is not None:
            tool_context = _merge_recommendation_context_places(tool_context, saved_context)
        recommendations = await recommendation_provider.recommend(
            agent_conditions,
            tool_context,
            excluded_place_ids,
            limit=recommendation_limit,
            ignore_operating_hours=effective_ignore_operating_hours,
        )
        # staged 분기와 같은 이유로 자르기에서 빠진 것만 다시 붙인다. 이쪽은
        # prepare 결과가 없어 좁힐 대상이 Context뿐이라, **병합이 끝난
        # tool_context**를 그 id로 좁혀 다시 채점한다 — 주입 Context만 쓰면 후보에
        # 원래 있던 보관함 장소를 되붙일 수 없다(TP-223).
        cut_saved_ids = _missing_place_ids(recommendations, protected_saved_ids)
        narrowed_context = (
            _narrow_recommendation_context_places(tool_context, cut_saved_ids)
            if cut_saved_ids
            else None
        )
        if narrowed_context is not None:
            pinned = await recommendation_provider.recommend(
                agent_conditions,
                narrowed_context,
                [],
                limit=len(cut_saved_ids),
                ignore_operating_hours=effective_ignore_operating_hours,
            )
            recommendations = _with_pinned_recommendations(
                recommendations, pinned, cut_saved_ids
            )
    _record_trace_safely(
        session_id=state_response.session_id,
        run_id=state_response.run_id,
        step="scoring",
        latency_ms=int((time.monotonic() - scoring_started_at) * 1000),
        scoring_version=SCORING_VERSION,
        store=store,
    )

    # 6-1) concentration_intent가 AVOID/SEEK일 때만: 1차 상위 후보의 혼잡도를 C에
    #      보강 조회하고, D의 2차 Scoring(재순위)으로 그 결과를 교체한다(D-040 확정 —
    #      concentration-conditions.md §2.2.3, agent-runtime-contract.md §6.5.2).
    #      분기 로직은 _apply_concentration_rerank()로 분리했다 — B의
    #      concentration_intent 필드 등록 완료(2026-08-05, B-06, PR #78) 이후로는
    #      run_agent_flow() 전체 통합 테스트로도 exercise되지만(§7 참고), agent_
    #      conditions만으로 독립 단위 테스트할 수 있는 이점이 있어 구조는 유지한다.
    #      final_limit을 recommendation_limit과 맞춰야 SCHEDULE의 10개가 재순위 후
    #      5개로 조용히 잘리지 않는다.
    recommendations = await _apply_concentration_rerank(
        agent_conditions,
        tool_context,
        recommendations,
        recommendation_provider=recommendation_provider,
        enrichment_provider=enrichment_provider,
        final_limit=recommendation_limit,
        execution_collector=tool_executions,
    )

    # 6-1-1) D-092: place_associations 기반 co-visit 2차 Scoring. concentration과
    #        달리 조건 게이트가 없다 — _apply_co_visited_rerank() 참고.
    recommendations = await _apply_co_visited_rerank(
        agent_conditions,
        tool_context,
        recommendations,
        recommendation_provider=recommendation_provider,
    )

    # 6-1) A → B: D의 하드 필터(_is_closed)가 폐점이라 걸러낸 후보 id를 기록한다
    #      (TP-82). 이 후보들은 recommendations/unverified_recommendations
    #      어디에도 담기지 않아 아래 record_recommendation()의 노출 이력 경로를
    #      탈 수 없다 — 그래서 기록하지 않으면 다음 회차 후보 수집에서 매번
    #      다시 뽑혀, 밤 시간대처럼 폐점 비율이 높을 때 "다른 곳 보여줘"를
    #      반복하면 카드 수가 점점 줄어드는 문제로 이어진다. SCHEDULE/RECOMMEND/
    #      MODIFY 어느 경로든 D 응답은 여기서 이미 확정됐으므로, 분기 전에 한
    #      번만 기록해 두 경로에 중복하지 않는다.
    if recommendations.excluded_closed_place_ids:
        record_closed_exclusions(
            RecordClosedExclusionsRequest(
                session_id=state_response.session_id,
                run_id=state_response.run_id,
                place_ids=recommendations.excluded_closed_place_ids,
            ),
            store=store,
            principal=principal,
        )
    return _ScoringOutcome(recommendations=recommendations, tool_context=tool_context)


async def _with_pinned_images(
    pinned_items: list[ScheduleItem],
    place_details_repository: PlaceDetailsReadRepository | None,
) -> list[ScheduleItem]:
    """부분 재편성에서 유지한 장소에 사진 주소를 채운다.

    유지한 장소는 B에 저장된 직전 일정으로 다시 만드는데, 거기에는 사진 주소가
    없다. 편성 단계는 이번 턴 후보에서만 사진을 찾고 유지한 장소는 중복 선택을
    막으려고 후보에서 뺀다 — 그래서 여기서 채우지 않으면 새로 고른 자리만 사진이
    나오고 나머지는 전부 자리표시로 바뀐다.

    B에 사진 주소를 함께 저장하는 대신 다시 조회한다. 계약 필드를 늘리지 않아도
    되고, 이미 저장된 세션에도 그대로 통한다. 추천 카드와 같은 Tool을 써서 대표
    주소와 대체 주소를 고르는 규칙도 같다. DB 조회 1회이고 외부 호출은 없다.

    조회가 실패해도 일정은 그대로 짠다 — 사진은 편성 판단에 쓰이지 않는다.
    """

    if not pinned_items or place_details_repository is None:
        return pinned_items
    try:
        result = await RecommendationCardTool(place_details_repository).get_cards(
            [item.place_id for item in pinned_items]
        )
    except Exception:
        logger.exception("유지한 장소 사진 조회 실패 — 사진 없이 편성한다")
        return pinned_items
    cards = {card.content_id: card for card in result.cards}
    return [
        item.model_copy(
            update={
                "image_url": cards[item.place_id].thumbnail_url,
                "image_url_fallback": cards[item.place_id].fallback_thumbnail_url,
                # 사진을 덮어쓰면 출처도 함께 덮어야 한다. 이 Tool은 Google
                # 보강 없이 조립되므로 결과는 늘 None인데, 그래도 명시해야
                # 직전 턴에 붙었던 Google 출처가 새 관광공사 사진에 남지 않는다.
                "image_attribution": _attribution_of(cards[item.place_id]),
            }
        )
        if item.place_id in cards
        else item
        for item in pinned_items
    ]


def _attribution_of(card: RecommendationCard) -> ImageAttribution | None:
    if card.photo_attribution is None:
        return None
    return ImageAttribution(
        author_name=card.photo_attribution.author_name,
        author_uri=card.photo_attribution.author_uri,
        source_uri=card.photo_attribution.source_uri,
    )


async def _run_schedule_branch(
    llm_output: LLMOutput,
    state_response: StateApplyResponse,
    recommendations: RecommendationResponse,
    *,
    tool_context: RecommendationContext,
    agent_conditions: UserConditions,
    session_context: SessionContextResponse,
    llm: LLMProvider,
    store: StateStore | None,
    principal: Principal | None,
    tool_execution: ToolExecutionDebug | None,
    tool_executions: list[ToolExecutionDebug],
    effective_ignore_operating_hours: bool,
    stream_event_sink: StreamEventSink | None,
    travel_route_tool: TravelRouteToolProvider | None = None,
    # 부분 재편성에서 유지한 장소의 사진을 다시 조회할 때만 쓴다. 없으면 그
    # 장소들은 사진 없이 나간다.
    place_details_repository: PlaceDetailsReadRepository | None = None,
) -> AgentResponse:
    """SCHEDULE 편성 분기(6-2단계)를 처리한다.

    `run_agent_flow()`의 `if is_schedule:` 블록을 그대로 옮긴 것이다 — 라우팅 그래프가
    이 단계를 노드로 감쌀 수 있게 먼저 함수로 떼어냈다(langgraph-adoption.md §6.1
    3단계). **본문은 한 줄도 바꾸지 않았다**(들여쓰기만 한 단계 내어썼다).
    """

    # 6-2) A: C의 AgentContextResponse.places(위경도)를 place_id로 매칭해
    #      pairwise_distances_km 계산 → 일정 편성 모듈 호출(docs/design/
    #      int-07-schedule.md 4절/6절). 상태 저장소 비접근 — D를 부르는 것과
    #      동일한 방식.
    schedule_candidates = [
        *recommendations.recommendations,
        *recommendations.unverified_recommendations,
    ]
    # 후보가 전부 폐점 때문에 제외됐으면(D가 excluded_all_closed로 표시) 일정을
    # 못 짠 진짜 원인이 "지역/카테고리 부족"이 아니라 "운영시간"이다 — 그런데도
    # 아래 schedule_no_candidates로 넘어가면 지역/카테고리를 아무리 바꿔도 같은
    # 이유로 계속 실패해 무한 되묻기가 된다(실사용 재현, 2026-08-13). RECOMMEND/
    # MODIFY 경로와 동일하게 "운영 중이 아닌 곳도 볼게요"를 먼저 제안한다.
    if (
        not schedule_candidates
        and recommendations.excluded_all_closed
        and not effective_ignore_operating_hours
    ):
        return await _respond_no_data_closed(
            llm_output,
            state_response,
            store=store,
            llm=llm,
            tool_execution=tool_execution,
            tool_executions=tool_executions,
        )
    places = tool_context.places.data if tool_context.places and tool_context.places.data else []
    # 이번 턴 C 응답에 없는 후보(보관함에 담긴 지 여러 턴 지난 장소 등)의 좌표는
    # B에 남은 추천 시점 스냅샷으로 메운다(SCHEDULE-12).
    snapshot_coordinates = _snapshot_coordinates(session_context)
    place_coordinates = _place_coordinates(places)
    # 보관함에 담긴 장소는 이번 일정에 반드시 들어가야 한다(SCHEDULE-12). 담은
    # 순서를 그대로 넘긴다 — 항목 수 상한을 넘으면 planner가 이 순서로 앞에서부터
    # 자르므로 정렬을 바꾸면 "왜 그 곳이 빠졌는지" 설명이 달라진다.
    saved_place_ids = [item.place_id for item in session_context.saved_places]
    # 후보 목록에 아예 없는 보관함 장소. planner는 이름을 알 방법이 없으므로
    # 여기서 보관함에 저장된 이름으로 채운다.
    #
    # **사유를 둘로 가른다**(TP-236). D가 방문 시각 영업시간으로 걸러낸 것은
    # 시간대를 바꾸면 실제로 들어가고, 그 밖의 이유(장소 상세 없음·좌표 없음)는
    # 시간대를 어떻게 바꿔도 결과가 같다. 한 리스트에 두면 화면이 두 경우에 같은
    # 안내를 하게 되고, 뒤쪽 사용자는 통하지 않는 재시도를 반복한다.
    #
    # 판정은 D가 이미 해 둔 것을 그대로 쓴다 — 바로 위 6-1에서 노출 이력에
    # 기록하는 `recommendations.excluded_closed_place_ids`와 같은 값이다. 여기서
    # 영업시간을 다시 해석하면 D의 제외 판정과 화면 안내가 갈릴 수 있다.
    #
    # `effective_ignore_operating_hours`가 켜진 턴에는 D가 폐점 필터를 돌리지
    # 않아 이 집합이 비고, 전부 absent 쪽으로 간다 — 그 턴에는 영업시간이 빠진
    # 이유가 아니므로 그것이 맞다.
    candidate_place_ids = {c.place_id for c in schedule_candidates}
    closed_place_ids = set(recommendations.excluded_closed_place_ids)
    absent_saved_place_names: list[str] = []
    closed_saved_place_names: list[str] = []
    for item in session_context.saved_places:
        if item.place_id in candidate_place_ids:
            continue
        if item.place_id in closed_place_ids:
            closed_saved_place_names.append(item.name)
        else:
            absent_saved_place_names.append(item.name)

    # 6-2-1) SCHEDULE-09 2단계: REJECT_SPECIFIC으로 재라우팅된 턴이면 통째로
    #        새로 짜지 않고, target_indices가 가리키는 자리만 새로 채운다.
    #        session_context는 이번 턴 처리 전에 조회한 값이라 직전 SCHEDULE
    #        턴의 shown_recommendations(순서·도착시각 등 포함)를 그대로 담고
    #        있다(3-3절과 동일한 전제). pinned 대상의 place_name은 이번 턴
    #        C 응답에서 다시 매칭하지 않고 B에 저장된 값을 그대로 쓴다 —
    #        원래는 재매칭하도록 짰다가, "경복궁"류 지명 검색이 호출마다
    #        다른 좌표로 resolve돼 이번 턴 주변 후보가 매번 통째로 달라지는
    #        사례가 실사용 테스트에서 확인됐다(2026-08-11). 그러면 이전
    #        place_id가 이번 후보에 전혀 안 잡혀 pinned 유지가 매번 실패하고
    #        REJECT_ALL처럼 조용히 전체 재편성으로 폴백됐다. B가 추천 시점에
    #        이름도 함께 저장해두면(schema.RecommendedItem.name, SCHEDULE-09
    #        2단계 예외) 이 재검색에 의존하지 않아 안정적이다.
    pinned_items: list[ScheduleItem] = []
    if (
        llm_output.modify is not None
        and llm_output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    ):
        target_orders = set(llm_output.modify.target_indices)
        for prev in session_context.shown_recommendations:
            if prev.rank in target_orders:
                continue
            if (
                prev.name is None
                or prev.estimated_arrival is None
                or prev.estimated_duration_min is None
            ):
                # 방어적 폴백 — SCHEDULE-09 2단계 도입 이전에 기록된 세션처럼
                # 이 필드들이 없는 과거 데이터일 때만 해당하며, 이 항목만 새
                # 후보로 채워지고 나머지는 정상적으로 유지된다. 4개 필드
                # (estimated_arrival~reason)는 SCHEDULE-06에서 한꺼번에
                # 추가돼 따로 없을 일은 거의 없지만, name/estimated_arrival만
                # 체크하고 estimated_duration_min은 빠져 있으면 아래에서
                # `or 0`으로 조용히 체류시간 0분짜리 pinned 항목이 만들어질
                # 수 있었다 — 가드를 맞춰 방지한다(실사용 리뷰로 발견,
                # 2026-08-13). travel_to_next_min은 원래 마지막 항목이면
                # None이 정상이라 이 가드에 넣지 않는다.
                continue
            pinned_items.append(
                ScheduleItem(
                    order=prev.rank,
                    place_id=prev.place_id,
                    place_name=prev.name,
                    estimated_arrival=prev.estimated_arrival,
                    estimated_duration_min=prev.estimated_duration_min,
                    travel_to_next_min=prev.travel_to_next_min,
                    reason=prev.reason or "",
                )
            )
        pinned_items = await _with_pinned_images(pinned_items, place_details_repository)

    # 거리 행렬을 한 번만 만든다(TP-242). 예전에는 부분 재편성·전체 편성 두 분기가
    # 각각 만들었는데, 여기에 지표까지 따로 만들면 같은 요청을 세 번 계산하고
    # "지표가 본 거리"와 "편성이 쓴 거리"가 갈릴 수 있다.
    schedule_pairwise_km = _build_pairwise_distances_km(
        schedule_candidates, places, fallback_coordinates=snapshot_coordinates
    )

    if pinned_items and llm_output.modify is not None:
        partial_request = SchedulePartialFillRequest(
            pinned_items=pinned_items,
            target_orders=sorted(set(llm_output.modify.target_indices)),
            candidates=schedule_candidates,
            conditions=agent_conditions,
            # 항상 None이다 — 이유는 SchedulePlanningRequest.visit_datetime 주석.
            visit_datetime=None,
            pairwise_distances_km=schedule_pairwise_km,
            travel_candidates=_build_travel_candidates(
                schedule_candidates, places, fallback_coordinates=snapshot_coordinates
            ),
            weather=_segment_weather(tool_context),
        )
        await _emit_progress(
            stream_event_sink,
            "scheduling",
            "기존 일정은 유지하고 바꿀 장소를 다시 편성하고 있어요.",
        )
        schedule_result = await _await_with_heartbeat(
            plan_partial_schedule(
                partial_request,
                llm,
                co_visited_fetcher=fetch_co_visited_hints,
                travel_route_tool=travel_route_tool,
            ),
            sink=stream_event_sink,
            stage="scheduling",
        )
    else:
        schedule_request = SchedulePlanningRequest(
            candidates=schedule_candidates,
            # 부분 재편성(위 if 분기)에는 넘기지 않는다 — 그쪽은 pinned_items가
            # 이미 자리를 붙들고 있고, 사용자가 지목한 자리만 새로 채우는
            # 턴이라 담아둔 장소를 그 자리에 밀어넣을 이유가 없다.
            must_include_place_ids=saved_place_ids,
            conditions=agent_conditions,
            # 항상 None이다 — 이유는 SchedulePlanningRequest.visit_datetime 주석.
            visit_datetime=None,
            pairwise_distances_km=schedule_pairwise_km,
            travel_candidates=_build_travel_candidates(
                schedule_candidates, places, fallback_coordinates=snapshot_coordinates
            ),
            weather=_segment_weather(tool_context),
        )
        await _emit_progress(
            stream_event_sink,
            "scheduling",
            "장소 순서와 머무는 시간을 구성하고 있어요.",
        )
        schedule_result = await _await_with_heartbeat(
            plan_schedule(
                schedule_request,
                llm,
                co_visited_fetcher=fetch_co_visited_hints,
                travel_route_tool=travel_route_tool,
            ),
            sink=stream_event_sink,
            stage="scheduling",
        )

    if absent_saved_place_names or closed_saved_place_names:
        # planner가 채운 목록과 합치지 않는다 — 사유가 정반대라, 섞으면 화면이
        # 두 경우에 같은 해결책("시간을 늘려보라")을 안내하게 된다. 후보에 아예
        # 없었던 장소는 시간을 늘려도 들어가지 않는다.
        #
        # 두 필드를 한 번에 덮는다(TP-236). 한쪽만 비어 있어도 빈 리스트를 함께
        # 써서, 이 두 필드의 값이 planner가 아니라 여기서만 정해진다는 것을
        # 호출부에서 읽을 수 있게 둔다.
        schedule_result = schedule_result.model_copy(
            update={
                "absent_saved_place_names": absent_saved_place_names,
                "closed_saved_place_names": closed_saved_place_names,
            }
        )

    # 품질 지표를 한 줄 남긴다(TP-242). schedule_result의 모든 필드가 확정된 뒤라야
    # 누락 사유 건수가 맞는다 — 바로 위에서 absent/closed를 덮어쓴다.
    #
    # **기록 실패가 응답을 막지 않는다.** _record_trace_safely()가 예외를 흡수한다.
    # latency_ms는 편성 파이프라인이 이미 잰 값을 그대로 쓴다 — 여기서 다시 재면
    # 지표를 만드는 시간까지 섞인다.
    _record_trace_safely(
        session_id=state_response.session_id,
        run_id=state_response.run_id,
        step=SCHEDULE_QUALITY_STEP,
        latency_ms=int(schedule_result.elapsed_ms),
        metrics=schedule_quality_metrics(
            schedule_result,
            time_available_min=agent_conditions.time_available,
            saved_place_count=len(saved_place_ids),
            walkable_cluster_size=walkable_cluster_size(
                SchedulePlanningRequest(
                    candidates=schedule_candidates,
                    conditions=agent_conditions,
                    # 항상 None이다 — 이유는 SchedulePlanningRequest 주석.
                    visit_datetime=None,
                    pairwise_distances_km=schedule_pairwise_km,
                ),
                within_min=WALKABLE_THRESHOLD_MIN,
            ),
        ),
        store=store,
    )

    await _emit_progress(
        stream_event_sink,
        "composing_message",
        "일정 결과를 정리하고 있어요.",
    )

    # 7) A → B: 일정에 실제로 포함된 장소만 기록한다(6.3절) — LLM이 제외한
    #    후보는 기록하지 않아 이후 RECOMMEND 요청에서 재노출될 수 있다.
    if schedule_result.items:
        record_recommendation(
            RecordRecommendationRequest(
                session_id=state_response.session_id,
                run_id=state_response.run_id,
                recommended=[
                    RecommendedPlace(
                        place_id=item.place_id,
                        rank=item.order,
                        name=item.place_name,
                        # 다음 턴이 이 장소를 보관함에서 되살릴 때 쓸 좌표
                        # 스냅샷(SCHEDULE-12). C 응답에 없으면 이전 스냅샷을
                        # 그대로 이어 적어, 한 번 확보한 좌표를 잃지 않는다.
                        latitude=_coordinate_of(
                            item.place_id, place_coordinates, snapshot_coordinates, 0
                        ),
                        longitude=_coordinate_of(
                            item.place_id, place_coordinates, snapshot_coordinates, 1
                        ),
                        estimated_arrival=item.estimated_arrival,
                        estimated_duration_min=item.estimated_duration_min,
                        travel_to_next_min=item.travel_to_next_min,
                        reason=item.reason,
                    )
                    for item in schedule_result.items
                ],
            ),
            store=store,
            principal=principal,
        )
    else:
        # 후보가 부족해서 일정을 못 짠 경우, route_summary 메시지만 반환하지 말고
        # 명시적 되묻기로 사용자에게 선택지를 준다(실사용 피드백, 2026-08-13).
        # 버튼 클릭이 SCHEDULE intent로 올바르게 라우팅되어야 다시 SCHEDULE을 시도하지,
        # 프론트가 텍스트 파싱으로 버튼을 만들면 LLM 분류가 틀린다.
        llm_output = llm_output.model_copy(
            update={
                "status": OutputStatus.NEEDS_CLARIFICATION,
                "clarification": ClarificationPayload(
                    code="schedule_no_candidates",
                    message=_SCHEDULE_NO_CANDIDATES_MESSAGE,
                    options=[
                        ClarificationOption(
                            id=option_id,
                            label=label,
                            resolved_intent=Intent.SCHEDULE,
                        )
                        for option_id, label in _SCHEDULE_NO_CANDIDATES_OPTIONS
                    ],
                ),
            }
        )
        _remember_clarification(state_response.session_id, "schedule_no_candidates", store)
        message = await compose_chat_message(llm_output, llm=llm)
        return AgentResponse(
            llm_output=llm_output,
            state=state_response,
            recommendations=None,
            schedule=None,
            message=message,
            llm_execution=get_llm_execution_metadata(),
            tool_execution=tool_execution,
            tool_executions=tool_executions,
        )

    # 8) A: 최종 응답 조립. recommendations는 채우지 않는다(AgentResponse
    #    docstring — schedule과 동시에 채워지지 않음).
    message = await compose_chat_message(
        llm_output,
        schedule=schedule_result,
        schedule_time_available_min=agent_conditions.time_available,
        llm=llm,
    )
    return AgentResponse(
        llm_output=llm_output,
        state=state_response,
        recommendations=None,
        schedule=schedule_result,
        message=message,
        llm_execution=get_llm_execution_metadata(),
        tool_execution=tool_execution,
        tool_executions=tool_executions,
    )


async def _finalize_recommendation_response(
    llm_output: LLMOutput,
    state_response: StateApplyResponse,
    recommendations: RecommendationResponse,
    *,
    llm: LLMProvider,
    store: StateStore | None,
    principal: Principal | None,
    # 이번 턴 C 응답. 노출 이력에 추천 시점 좌표를 함께 남기는 데만 쓴다
    # (SCHEDULE-12). 좌표를 못 구하는 경로(테스트 더블 등)는 None으로 둘 수 있고,
    # 그때는 좌표만 비고 나머지 동작은 이전과 같다.
    tool_context: RecommendationContext | None = None,
    tool_execution: ToolExecutionDebug | None,
    tool_executions: list[ToolExecutionDebug],
    effective_ignore_operating_hours: bool,
    stream_recommendation_summary: bool,
    stream_event_sink: StreamEventSink | None,
) -> AgentResponse:
    """RECOMMEND/MODIFY 결과를 이력에 남기고 카드·요약을 방출한다(7·8단계).

    `run_agent_flow()`의 꼬리를 그대로 옮긴 것이다 — 라우팅 그래프가 이 단계를 노드로
    감쌀 수 있게 먼저 함수로 떼어냈다(docs/design/langgraph-adoption.md §6.1 3단계).
    이관 당시에는 본문을 한 줄도 바꾸지 않았고, 이후 SCHEDULE-12로 노출 이력에 좌표를
    싣는 인자(`tool_context`)가 붙었다 — `_run_schedule_branch()`가 같은 값을 받는
    것과 같은 모양이다.
    """

    # 7) A → B: 실제로 화면에 노출된 결과만 기록한다. recommendations와
    #    unverified_recommendations 둘 다 프론트에 렌더링되므로(운영시간 미검증 섹션으로
    #    구분되어 보일 뿐 노출 자체는 됨) 함께 기록한다 — 계산만 하고 안 보여준 건 넣지
    #    않아야 "다른 곳 보여줘"의 제외 목록이 정확해진다.
    #    distance_km/remaining_minutes/environment_type도 함께 기록한다 —
    #    COMPARE가 "추천 시 이미 계산된 데이터"를 그대로 쓸 수 있게 하는
    #    Feature 스냅샷이다(COMPARE 데이터 출처 A안, 2026-08-11).
    shown = [*recommendations.recommendations, *recommendations.unverified_recommendations]
    # 결과 0건이 전부 폐점 후보 제외 때문이면(D가 excluded_all_closed로 표시)
    # 일반 _NO_DATA_MESSAGE 대신 "운영중이 아닌 곳도 볼래요" 되묻기를 띄운다.
    # 이미 그 선택지로 재조회했거나 TTL 안이라 계속 무시 중인데도 여전히
    # 0건이면(ignore_operating_hours=True인데 excluded_all_closed) 무한
    # 되묻기를 피하려고 다시 띄우지 않고 그대로 진행한다.
    if not shown and recommendations.excluded_all_closed and not effective_ignore_operating_hours:
        return await _respond_no_data_closed(
            llm_output,
            state_response,
            store=store,
            llm=llm,
            tool_execution=tool_execution,
            tool_executions=tool_executions,
        )
    if shown:
        shown_coordinates = _place_coordinates(
            tool_context.places.data
            if tool_context is not None and tool_context.places and tool_context.places.data
            else []
        )
        record_recommendation(
            RecordRecommendationRequest(
                session_id=state_response.session_id,
                run_id=state_response.run_id,
                recommended=[
                    RecommendedPlace(
                        place_id=item.place_id,
                        rank=index + 1,
                        name=item.name,
                        # 다음 SCHEDULE 턴이 이 장소를 보관함에서 되살릴 때 쓸
                        # 좌표 스냅샷(SCHEDULE-12).
                        latitude=_coordinate_of(item.place_id, shown_coordinates, {}, 0),
                        longitude=_coordinate_of(item.place_id, shown_coordinates, {}, 1),
                        distance_km=item.distance_km,
                        remaining_minutes=item.remaining_minutes,
                        environment_type=item.environment_type,
                    )
                    for index, item in enumerate(shown)
                ],
            ),
            store=store,
            principal=principal,
        )

    # 8) A: 카드는 전송 순서상 즉시 내보내고, 선택 팁만 LLM으로 스트리밍한다.
    #    카드의 순위·근거는 D 결과 그대로라 LLM 생성 대기 때문에 사용자가 추천
    #    결과를 늦게 보지 않는다 — 이 전송 순서는 그대로다. 화면에서 팁이 카드
    #    위로 보이는 건 프론트가 스트리밍 말풍선을 카드 앞자리에 끼워 넣기
    #    때문이다(frontend/src/state/streamingMessage.ts의
    #    findStreamInsertionIndex, 2026-09-09).
    result_payload = {
        "llm_output": llm_output.model_dump(mode="json"),
        "state": state_response.model_dump(mode="json"),
        "recommendations": recommendations.model_dump(mode="json"),
        "message": recommendation_wrapper_message(),
    }
    result_emitted = False

    async def emit_recommendation_result() -> None:
        nonlocal result_emitted
        if result_emitted:
            return
        await _emit_stream_event(stream_event_sink, "result", result_payload)
        result_emitted = True

    should_stream_summary = stream_recommendation_summary and bool(shown)
    if should_stream_summary:
        # 카드부터 즉시 전송한다(지연 없이). 그 다음 message_start가 "추천 팁"
        # 로딩 말풍선을 여는데, 프론트가 이걸 카드 앞자리에 끼워 넣어 화면
        # 순서는 캡션 → 팁(로딩→실시간 채워짐) → 카드가 된다.
        await emit_recommendation_result()
        await _begin_streamed_message(
            stream_event_sink,
            intent=llm_output.intent,
            progress_message="추천 팁을 정리하고 있어요.",
        )
    else:
        # 스트리밍을 사용하지 않는 호출자는 기존처럼 결과를 즉시 관측한다.
        await emit_recommendation_result()

    async def emit_message_delta(text: str) -> None:
        await _emit_stream_event(stream_event_sink, "message_delta", {"text": text})

    # 말풍선은 지금까지 카드 데이터만 받아서, 동행을 friend로 정확히 뽑아 놓고도
    # "혼자서도 가기 좋고"로 답하는 일이 있었다(2026-08-31 실사용). 누적 조건을 함께
    # 넘긴다 — 사실 근거는 여전히 카드에서만 오고, 이 값은 강조점·말투만 고른다
    # (recommend/summary_instruction.md). 새 파라미터를 만들지 않고 state_response에서
    # 뽑으므로 두 호출부(직접 경로·그래프 노드)가 함께 혜택을 본다.
    stated_conditions = to_user_conditions(state_response.user_conditions)
    if should_stream_summary:
        message = await compose_recommendation_summary(
            intent=llm_output.intent,
            recommendations=recommendations,
            llm=llm,
            conditions=stated_conditions,
            on_message_delta=emit_message_delta,
        )
        # 부가 설명 생성 실패는 추천 자체의 실패가 아니다. 카드 앞에 이미 나온
        # 고정 안내문을 최종 message에도 남겨 단발 JSON 호출과의 계약을 유지한다.
        message = message or recommendation_wrapper_message()
    else:
        message = await compose_chat_message(
            llm_output,
            recommendations=recommendations,
            llm=llm,
            conditions=stated_conditions,
        )
    # 빈 스트림·부가 설명 실패여도 result는 이미 보냈거나 여기서 한 번 보장한다.
    await emit_recommendation_result()
    return AgentResponse(
        llm_output=llm_output,
        state=state_response,
        recommendations=recommendations,
        message=message,
        llm_execution=get_llm_execution_metadata(),
        tool_execution=tool_execution,
        tool_executions=tool_executions,
    )


async def run_agent(
    request: AgentRequest,
    *,
    principal: Principal | None = None,
    stream_event_sink: StreamEventSink | None = None,
    stream_recommendation_summary: bool = False,
    generate_follow_ups: bool = True,
) -> AgentResponse:
    """호출자가 쓰는 Fake/Real 공통 진입점.

    A는 조건 기반 ContextProvider 계약만 알고, C 내부 Tool·Provider 조립은
    app.agent_context.factory에 위임한다. D 계약이 확정되어([TECH-02])
    RealRecommendationProvider를 기본으로 주입한다.
    """

    from app.agent_context.factory import get_candidate_enrichment_service, get_context_provider
    from app.providers.factory import (
        get_llm_provider,
        get_place_details_repository,
        get_place_evidence_provider,
        get_recommendation_card_tool,
        get_travel_route_tool,
    )
    from app.services.runtime.real_recommendation_provider import RealRecommendationProvider

    async with create_external_client() as client:
        # D의 채점기와 보관함 주입이 같은 저장소를 쓴다 — 두 번 만들 이유가 없다.
        place_details_repository = get_place_details_repository(client)
        return await run_agent_flow(
            request,
            llm=get_llm_provider(),
            tool_provider=get_context_provider(client),
            recommendation_provider=RealRecommendationProvider(
                get_place_evidence_provider(client),
                place_details_repository,
                # 원래 COMPARE 전용 Tool(app/tools/recommendation_cards.py)이지만
                # 썸네일 조회 로직은 그대로 재사용한다(TECH-02).
                get_recommendation_card_tool(client),
            ),
            place_details_repository=place_details_repository,
            enrichment_provider=get_candidate_enrichment_service(client),
            travel_route_tool=get_travel_route_tool(client),
            principal=principal,
            stream_event_sink=stream_event_sink,
            stream_recommendation_summary=stream_recommendation_summary,
            generate_follow_ups=generate_follow_ups,
        )


__all__ = ["StreamEventSink", "run_agent", "run_agent_flow", "summarize_turn"]

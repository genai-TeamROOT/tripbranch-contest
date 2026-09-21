"""Agent Runtime(run_agent_flow)의 A→B→A→C→A→D→A→B 흐름 통합 테스트.

FakeLLMProvider/FakeToolProvider/FakeRecommendationProvider와 B의
실제 apply()/get_session_context()를 조합해서 검증한다(팩토리는 거치지 않음 —
test_state_integration.py와 같은 스타일).
FakeToolProvider는 A-C Context Contract v0(docs/design/a-c-context-contract-draft.md)를
그대로 흉내 낸다 — C 단계 자체의 needs_clarification은 LLM 단계 needs_clarification과
별개 레이어라 따로 테스트한다.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
)
from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    Clarification,
    ContextError,
    ContextValue,
    ContextWarning,
    Coordinates,
    PlaceCandidate,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
    ResponseMetadata,
    WeatherForecast,
)
from app.auth.principal import Principal
from app.config import settings
from app.domain.models import StoredPlaceDetail
from app.domain.scoring import SCORING_VERSION
from app.domain.travel_route import TravelMode, TravelRoute
from app.prompts.registry import turn_prompt_version
from app.providers.contracts import ProviderSource, provider_result
from app.providers.driving_route import FakeDrivingRouteProvider
from app.providers.kakao_transit_route import FakeTransitRouteProvider
from app.providers.stub import FakeLLMProvider
from app.providers.walking_route import FakeWalkingRouteProvider
from app.schedule.schemas import SchedulePlanningRequest
from app.schemas import (
    AgentRequest,
    CompareCriteria,
    ComparisonItem,
    ComparisonResult,
    ConcentrationIntent,
    GeneralPayload,
    GeneralTopic,
    Intent,
    IntentClassificationResult,
    InteractionMode,
    LLMOutput,
    OutputStatus,
    RecommendationItem,
    RecommendationResponse,
    RecommendPayload,
    SituationKind,
    Transport,
    TravelOrigin,
    UserConditions,
)
from app.services.runtime import agent_runtime as agent_runtime_module
from app.services.runtime.agent_runtime import (
    _MEASURED_ROUTE_CANDIDATE_LIMIT,
    _WIDEN_RADIUS_MAX_TRAVEL_TIME,
    _apply_concentration_rerank,
    _build_pairwise_distances_km,
    _effective_excluded_place_ids,
    _fetch_compare_travel_routes,
    _fetch_realtime_info_agentic,
    _narrow_recommendation_context_places,
    _revivable_place_ids,
    _snapshot_coordinates,
    run_agent_flow,
    summarize_turn,
)
from app.services.runtime.compare_context_schemas import (
    CompareContextRequest,
    CompareContextResponse,
)
from app.services.runtime.follow_up_suggester import MAX_LABEL_LENGTH, MAX_SUGGESTIONS
from app.services.runtime.info_context_schemas import (
    InfoContextRequest,
    InfoContextResponse,
    RealtimeCityInfoResult,
)
from app.services.runtime.llm_execution import (
    CONDITION_EXTRACTION_RETRY_OPERATION,
    record_llm_call,
)
from app.services.runtime.real_recommendation_provider import RealRecommendationProvider
from app.services.runtime.stubs import (
    FakeEnrichmentProvider,
    FakeRecommendationProvider,
    FakeToolProvider,
)
from app.state import preferences as state_preferences
from app.state import service as state_service
from app.state.schema import UserPreference, now_kst
from app.state.service import (
    SetPendingClarificationRequest,
    StateApplyResponse,
    get_session_context,
    set_pending_clarification,
)
from app.state.store import InMemoryStateStore
from app.tools.contracts import ToolStatus
from app.tools.travel_route import (
    TravelRouteProviders,
    TravelRouteTool,
    TravelRouteToolResult,
)

DEVICE_LOCATION = "37.5788,126.9770"


class _LLMProviderWithGeneralAnswer(FakeLLMProvider):
    """FakeLLMProvider + generate_general_answer()만 로컬로 보강한 테스트 전용 더블.

    app/providers/stub.py의 FakeLLMProvider는 건드리지 않는다(Fake 유지보수는
    이번 작업 범위 밖) — compose_chat_message()의 GENERAL 분기만 테스트하기 위한
    최소 보강이다.
    """

    async def generate_general_answer(
        self, topic, original_question, *, offer_content=None, history=None
    ):
        answer = "(테스트용 고정 답변)"
        if offer_content:
            answer = f"{answer} {offer_content}을(를) 찾아드릴까요?"
        return provider_result(answer, source=ProviderSource.FAKE_LLM)


class _LLMProviderWithSituationalOffer(_LLMProviderWithGeneralAnswer):
    """대화층 3·4단계 회귀 테스트용 — classify_intent를 GENERAL+situational로,
    extract_general_request를 지정한 situation으로 고정한다.

    실제 분류·추출 정확도는 scripts/test_situational_utterances.py(실 Gemini)가
    맡는다 — 이 더블은 그 결과가 나왔다는 전제 아래 orchestrator 이후
    (조건 병합 → 되묻기/제안 상태 → 응답 조립) 배선만 결정적으로 검증한다.
    """

    def __init__(self, situation: SituationKind = SituationKind.FATIGUE) -> None:
        self._situation = situation
        self.classify_histories: list[object] = []
        self.extract_histories: list[object] = []

    async def classify_intent(self, user_input: str, **kwargs: object):
        self.classify_histories.append(kwargs.get("history"))
        return provider_result(
            IntentClassificationResult(
                intent=Intent.GENERAL, interaction_mode=InteractionMode.SITUATIONAL
            ),
            source=ProviderSource.FAKE_LLM,
        )

    async def extract_general_request(self, user_input: str, **kwargs: object):
        self.extract_histories.append(kwargs.get("history"))
        return provider_result(
            LLMOutput(
                intent=Intent.GENERAL,
                status=OutputStatus.COMPLETE,
                general=GeneralPayload(
                    topic=GeneralTopic.TRAVEL_TIP,
                    original_question=user_input,
                    situation=self._situation,
                ),
            ),
            source=ProviderSource.FAKE_LLM,
        )


class _LLMProviderForcingSearchCenter(_LLMProviderWithGeneralAnswer):
    """FakeLLMProvider의 _KNOWN_PLACE_NAMES는 종로구 랜드마크만 알아서 "용산" 같은
    지명은 search_center로 못 넘긴다 — TP-160의 발화-매치 경로(구 이름 부분 일치)를
    검증하려면 실제 사용자 발화처럼 임의 지명을 그대로 넘겨야 한다.
    """

    def __init__(self, search_center: str) -> None:
        self._search_center = search_center

    async def extract_recommend_conditions(self, user_input, **kwargs):
        result = await super().extract_recommend_conditions(user_input, **kwargs)
        result.data.recommend.conditions.search_center = self._search_center
        return result


class _LLMProviderForcingCompareWithFewShown(_LLMProviderWithGeneralAnswer):
    """FakeLLMProvider는 shown_place_count>=2일 때만 COMPARE를 낸다(실제 규칙과 일치).

    thinking 예산에 따라 Real Gemini가 이 전제조건을 무시하고 COMPARE로 분류하는
    불안정성(케이스 3, 2026-08-11 68건 테스트 결과)을 재현하려고, 트리거 문구
    "억지비교"에 한해 그 가드만 우회한다 — 나머지 발화는 평소 Fake 규칙 그대로다.
    """

    async def classify_intent(self, user_input, **kwargs):
        if "억지비교" in user_input:
            return provider_result(
                IntentClassificationResult(intent=Intent.COMPARE),
                source=ProviderSource.FAKE_LLM,
            )
        return await super().classify_intent(user_input, **kwargs)


class _LLMProviderDroppingConditionPayload(_LLMProviderWithGeneralAnswer):
    """조건 추출이 `recommend`를 통째로 비워 보내는 상황을 만든다.

    운영에서 실제로 일어나는 일이다 — 주 추출 모델이 `gemini-3.5-flash-lite`인
    동안 일정 발화 3분의 1이 이렇게 왔다(2026-09-08 실측). Fake 추출기는 늘
    페이로드를 채우므로 평범한 시드로는 이 경로를 지나갈 수 없다.
    """

    async def extract_recommend_conditions(self, user_input, **kwargs):
        result = await super().extract_recommend_conditions(user_input, **kwargs)
        result.data.recommend = None
        return result


class _LLMProviderRecoveringOnRetry(_LLMProviderWithGeneralAnswer):
    """첫 조건 추출은 빈손, 재시도에서 채워 오는 provider (TP-266).

    **재시도 사실을 호출 이력에 남기는 것까지 흉내 낸다.** RealGeminiProvider가
    재시도 호출을 `extract_recommend_conditions_retry`라는 operation으로 남기고,
    `condition_extraction_was_retried()`가 그 이름으로 센다 — 그 계약을 여기서
    함께 잠근다. 이름만 바꾸고 세는 쪽을 안 고치면 이 테스트가 깨진다.
    """

    def __init__(self) -> None:
        self.extract_calls = 0

    async def extract_recommend_conditions(self, user_input, **kwargs):
        self.extract_calls += 1
        result = await super().extract_recommend_conditions(user_input, **kwargs)
        if self.extract_calls == 1:
            result.data.recommend = None
            return result
        record_llm_call(
            operation=CONDITION_EXTRACTION_RETRY_OPERATION,
            attempted_models=["gemini-3.5-flash"],
            served_model="gemini-3.5-flash",
        )
        return result


class _CountingToolProvider:
    """실제 FakeToolProvider를 감싸서 호출 횟수를 세고, 마지막 요청을 검사용으로 보관한다."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_request: AgentContextRequest | None = None
        self.info_call_count = 0
        self.last_info_request: InfoContextRequest | None = None
        self.compare_call_count = 0
        self.last_compare_request: CompareContextRequest | None = None
        self._inner = FakeToolProvider()

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        self.call_count += 1
        self.last_request = request
        return await self._inner.fetch_context(request)

    async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
        self.info_call_count += 1
        self.last_info_request = request
        return await self._inner.fetch_info_context(request)

    async def fetch_compare_context(self, request: CompareContextRequest) -> CompareContextResponse:
        self.compare_call_count += 1
        self.last_compare_request = request
        return await self._inner.fetch_compare_context(request)


class _CountingRecommendationProvider:
    """rerank_with_concentration()을 일부러 갖지 않는다 — Real D가 아직 2차
    Scoring을 구현하지 않은 상태를 재현한다(hasattr 가드 확인용, 기본 fixture)."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_limit: int | None = None
        # 호출마다의 limit. 보관함 주입이 상한을 부풀리지 않는지 보려면 마지막
        # 값만으로는 부족하다 — 자르기에서 빠진 것을 덧붙이는 호출이 뒤에 한 번
        # 더 붙기 때문이다.
        self.limits: list[int] = []
        self._inner = FakeRecommendationProvider()

    async def recommend(
        self, conditions, context, excluded_place_ids, limit=5, ignore_operating_hours=False
    ):
        self.call_count += 1
        self.last_limit = limit
        self.limits.append(limit)
        return await self._inner.recommend(
            conditions, context, excluded_place_ids, limit, ignore_operating_hours
        )


class _CountingRecommendationProviderWithRerank(_CountingRecommendationProvider):
    """rerank_with_concentration()을 갖춘 버전 — D가 2차 Scoring을 구현한 상태를
    재현한다."""

    def __init__(self) -> None:
        super().__init__()
        self.rerank_call_count = 0

    async def rerank_with_concentration(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        concentration: CandidateEnrichmentResponse,
    ) -> RecommendationResponse:
        self.rerank_call_count += 1
        return await self._inner.rerank_with_concentration(
            conditions, context, first_pass, concentration
        )


class _CountingEnrichmentProvider:
    """실제 FakeEnrichmentProvider를 감싸서 호출 횟수를 세고, 마지막 요청을 보관한다."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_request: CandidateEnrichmentRequest | None = None
        self._inner = FakeEnrichmentProvider()

    async def enrich(self, request: CandidateEnrichmentRequest) -> CandidateEnrichmentResponse:
        self.call_count += 1
        self.last_request = request
        return await self._inner.enrich(request)


def _providers():
    return {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": _CountingToolProvider(),
        "recommendation_provider": _CountingRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }


@pytest.mark.asyncio
async def test_recommend_flow_reaches_recommendations() -> None:
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "RECOMMEND"
    assert response.llm_output.status is OutputStatus.COMPLETE
    assert response.state.user_conditions.search_center == "경복궁"
    assert response.recommendations is not None
    assert len(response.recommendations.recommendations) > 0
    assert providers["tool_provider"].call_count == 1
    assert providers["recommendation_provider"].call_count == 1
    assert providers["tool_provider"].last_request.gps_location == Coordinates(
        latitude=37.5788, longitude=126.9770
    )


@pytest.mark.asyncio
async def test_turn_carries_follow_up_suggestions() -> None:
    """응답 조립 지점이 열다섯 군데라도 후속 질문은 한 곳에서 붙는다.

    `run_agent_flow`가 본체의 응답을 받은 직후 한 번만 부르므로, 어느 경로로 만들어진
    응답이든 같은 자리에서 채워진다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.recommendations is not None
    assert 0 < len(response.suggested_follow_ups) <= MAX_SUGGESTIONS
    assert all(len(label) <= MAX_LABEL_LENGTH for label in response.suggested_follow_ups)


@pytest.mark.asyncio
async def test_recommend_stream_shows_template_and_cards_before_llm_tip() -> None:
    """SSE 추천은 고정 안내·카드를 먼저, LLM 선택 팁을 그 아래에 보낸다."""

    store = InMemoryStateStore()
    providers = _providers()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        stream_event_sink=sink,
        stream_recommendation_summary=True,
        **providers,
    )

    names = [event for event, _ in events]
    # 조건 병합 직후의 location_resolved가 두 번째 progress 뒤에 끼어든다.
    assert names[:4] == ["progress", "progress", "location_resolved", "progress"]
    assert names.index("result") < names.index("message_start") < names.index("message_delta")
    result_payload = next(payload for event, payload in events if event == "result")
    assert result_payload["message"] == "이런 곳들을 찾아봤어요:"
    assert [payload["stage"] for event, payload in events if event == "progress"] == [
        "interpreting",
        "merging_conditions",
        "fetching_context",
        "scoring",
        "composing_message",
    ]
    assert (
        "".join(payload["text"] for event, payload in events if event == "message_delta")
        == response.message
    )


@pytest.mark.asyncio
async def test_stream_reports_the_resolved_location_before_fetching_context() -> None:
    """위치는 조건 병합에서 확정된다 — 도구 조회를 기다릴 이유가 없다.

    **순서가 이 이벤트의 전부다.** done까지 미루면 도구 조회(fetching_context)와
    채점(scoring), 답변 스트리밍이 모두 끝난 뒤라 — 그 사이가 턴에서 제일 긴
    구간이다 — 사용자는 "광화문역 근처"라고 말해 놓고 결과가 다 나올 때까지
    화면 우상단에서 이전 위치를 보게 된다.
    """

    store = InMemoryStateStore()
    providers = _providers()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        stream_event_sink=sink,
        stream_recommendation_summary=True,
        **providers,
    )

    names = [event for event, _ in events]
    stages = [payload["stage"] for event, payload in events if event == "progress"]
    resolved = next(payload for event, payload in events if event == "location_resolved")

    assert names.index("location_resolved") < names.index("result")
    assert stages.index("merging_conditions") < stages.index("fetching_context")
    # progress는 이벤트 목록에서 stage와 함께 세야 위치를 집을 수 있다.
    fetching_at = next(
        index
        for index, (event, payload) in enumerate(events)
        if event == "progress" and payload["stage"] == "fetching_context"
    )
    assert names.index("location_resolved") < fetching_at

    # 이 턴이 실제로 쓴 값과 같아야 한다 — 다른 값을 보내면 화면이 서버와 다른
    # 위치를 말하게 된다.
    assert resolved["search_center"] == response.state.user_conditions.search_center
    assert resolved["current_location"] == response.state.user_conditions.current_location


@pytest.mark.asyncio
async def test_stream_reports_the_location_picked_on_the_settings_screen() -> None:
    """발화가 위치를 말하지 않으면 위치 설정 화면에서 고른 값이 그대로 실린다.

    _apply_selected_locations()가 조건 병합보다 앞에서 채우므로, 이 이벤트는 그
    결과를 본다. 화면은 이 값으로 자기 저장소를 서버 기준에 맞춘다.
    """

    store = InMemoryStateStore()
    providers = _providers()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    await run_agent_flow(
        AgentRequest(
            user_input="조용한 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_search_center="안국역",
        ),
        store=store,
        stream_event_sink=sink,
        stream_recommendation_summary=True,
        **providers,
    )

    resolved = next(payload for event, payload in events if event == "location_resolved")
    assert resolved["search_center"] == "안국역"


@pytest.mark.asyncio
async def test_general_stream_opens_message_before_text_deltas() -> None:
    """GENERAL은 카드가 없으므로 message_start가 움직이는 로딩 말풍선을 연다."""

    store = InMemoryStateStore()
    providers = _providers()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(user_input="트리비는 뭐 할 수 있어?", session_id=None),
        store=store,
        stream_event_sink=sink,
        stream_recommendation_summary=True,
        **providers,
    )

    names = [event for event, _ in events]
    assert response.llm_output.intent is Intent.GENERAL
    assert names.index("message_start") < names.index("message_delta")
    assert [payload["stage"] for event, payload in events if event == "progress"][-1] == (
        "composing_message"
    )
    streamed_message = "".join(
        payload["text"] for event, payload in events if event == "message_delta"
    )
    assert streamed_message == response.message


@pytest.mark.asyncio
async def test_recommend_flow_records_traces_for_llm_tool_and_scoring() -> None:
    """B-07: LLM/Tool/Scoring 3단계가 같은 run_id로 기록되는지 확인한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    traces = store.get_traces(response.state.session_id)
    steps = [trace.step for trace in traces]
    assert steps == ["llm_interpret", "tool_fetch", "scoring"]
    assert all(trace.run_id == response.state.run_id for trace in traces)
    assert all(trace.latency_ms is not None and trace.latency_ms >= 0 for trace in traces)

    by_step = {trace.step: trace for trace in traces}
    assert by_step["llm_interpret"].prompt_version == turn_prompt_version(Intent.RECOMMEND)
    assert by_step["llm_interpret"].scoring_version is None
    assert by_step["tool_fetch"].prompt_version is None
    assert by_step["tool_fetch"].scoring_version is None
    assert by_step["scoring"].prompt_version is None
    assert by_step["scoring"].scoring_version == SCORING_VERSION


@pytest.mark.asyncio
async def test_needs_clarification_records_only_llm_trace() -> None:
    """LLM이 되물으면 Tool/Scoring은 호출 자체가 안 되니 trace도 llm_interpret만 남는다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="눈 오는데 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status is OutputStatus.NEEDS_CLARIFICATION
    traces = store.get_traces(response.state.session_id)
    assert [trace.step for trace in traces] == ["llm_interpret"]


@pytest.mark.asyncio
async def test_empty_condition_payload_is_recorded_as_interpret_failure() -> None:
    """조건 페이로드가 비어 오면 llm_interpret 단계의 실패로 남는다. (함정 12 후속)

    **이 경로가 조용했던 것이 문제였다.** `llm_output.recommend`가 None이면
    state_transform이 조건 병합을 통째로 건너뛰는데, 오류도 로그도 없어서 조건이
    하나도 없는 채로 추천이 돌아간다. 2026-08-18부터 실제로 일어나고 있었고
    열흘 넘게 원인 미확정으로 남았던 이유가 그 침묵이다.

    `metrics`가 아니라 `error_type`에 남기는 이유는 TP-242가 도메인 지표를 기존
    단계에 얹지 않기로 정했기 때문이다 — 아래 테스트가 그 분리를 함께 잠근다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderDroppingConditionPayload()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.recommend is None

    by_step = {trace.step: trace for trace in store.get_traces(response.state.session_id)}
    assert by_step["llm_interpret"].error_type == "condition_payload_missing"
    # 지표 쪽은 건드리지 않는다 — TP-242의 분리를 여기서도 지킨다.
    assert by_step["llm_interpret"].metrics is None


@pytest.mark.asyncio
async def test_condition_payload_recovered_by_retry_is_recorded_separately() -> None:
    """다시 뽑아서 살아난 턴은 성공이 아니라 "한 번 실패하고 복구된" 턴으로 남는다.

    **None으로 두면 안 되는 이유가 있다.** 재시도가 얼마나 자주 걸리는지 세는 자리가
    여기뿐이라, 조용히 넘기면 폴백 모델을 유지할지 1순위를 올릴지 정할 근거가
    사라진다(함정 44 — 없는 것을 세는 자리를 만든다).

    `condition_payload_missing`과 값을 나눠야 한다. 둘을 합치면 "재시도로 살아난
    턴"과 "두 번 다 실패한 턴"이 한 숫자에 묻혀 재시도의 효과를 못 잰다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    llm = _LLMProviderRecoveringOnRetry()
    providers["llm"] = llm

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert llm.extract_calls == 2
    assert response.llm_output.recommend is not None
    # 카드 완료조건 4 — 다시 뽑은 조건이 llm_output에만 실리고 끝나면 의미가 없다.
    # state까지 병합돼야 추천·편성이 그 조건으로 돈다. 재시도 결과를 버리고 첫
    # 결과를 돌려주면 여기가 빈 UserConditions와 condition_version 0으로 깨진다.
    assert response.state.user_conditions.search_center == "경복궁"
    assert response.state.condition_version == 1

    by_step = {trace.step: trace for trace in store.get_traces(response.state.session_id)}
    assert by_step["llm_interpret"].error_type == "condition_payload_missing_recovered"
    # 지표 쪽은 여기서도 건드리지 않는다 — TP-242의 분리.
    assert by_step["llm_interpret"].metrics is None


@pytest.mark.asyncio
async def test_conditions_that_arrive_empty_are_not_an_interpret_failure() -> None:
    """조건을 하나도 말하지 않은 발화는 실패가 아니다.

    대조군이다. 페이로드가 **있는데 안이 빈 것**과 페이로드 **자체가 없는 것**은
    뜻이 다르다 — "일정 짜줘"처럼 조건을 안 말한 발화에서는 전자가 옳은 결과다.
    이 구분을 지우면 위 판정이 정상 발화까지 실패로 세기 시작한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.recommend is not None

    by_step = {trace.step: trace for trace in store.get_traces(response.state.session_id)}
    assert by_step["llm_interpret"].error_type is None


@pytest.mark.asyncio
async def test_condition_free_intents_are_not_interpret_failures() -> None:
    """조건을 안 나르는 턴은 페이로드가 없어도 실패가 아니다.

    GENERAL·INFO·COMPARE는 `recommend`가 없는 것이 **정상**이다 — 조건을
    `recommend`로 나르는 것은 RECOMMEND·SCHEDULE 둘뿐이다(state_transform이 병합
    대상을 그 둘로 좁힌 것과 같은 기준).

    이 대조군이 없으면 인텐트 제한을 지워도 아무 테스트가 안 깨진다(실제로
    돌연변이로 확인했다 — 4,191건 전부 통과했다). 그러면 모든 잡담 턴이
    "조건 유실"로 집계되면서 지표가 통째로 무의미해진다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(user_input="트리비는 뭐 할 수 있어?", session_id=None),
        store=store,
        **providers,
    )

    assert response.llm_output.intent is Intent.GENERAL
    assert response.llm_output.recommend is None

    by_step = {trace.step: trace for trace in store.get_traces(response.state.session_id)}
    assert by_step["llm_interpret"].error_type is None


@pytest.mark.asyncio
async def test_keep_flow_conditions_persist_and_reject_all_excludes_shown() -> None:
    """1턴 RECOMMEND → 2턴 REJECT_ALL: 조건은 KEEP, 1턴에서 노출된 장소는 제외된다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    session_id = first.state.session_id
    first_shown = {item.place_id for item in first.recommendations.recommendations}
    assert first_shown  # FakeRecommendationProvider가 뭔가는 반환했어야 한다

    second = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘", session_id=session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.llm_output.modify.modify_type == "REJECT_ALL"
    # KEEP: 조건은 1턴 값이 그대로 유지된다.
    assert second.state.user_conditions.search_center == "경복궁"
    # 1턴에서 노출된 장소가 제외 목록에 들어갔다.
    assert first_shown.issubset(set(second.state.excluded_place_ids))
    # FakeRecommendationProvider가 excluded_place_ids를 반영해 후보에서 뺐으므로
    # (C는 더 이상 필터링하지 않는다 — 계약 §2) 2턴 추천은 비어 있다.
    assert second.recommendations is not None
    second_shown = {item.place_id for item in second.recommendations.recommendations}
    assert not (second_shown & first_shown)


@pytest.mark.asyncio
async def test_modify_change_condition_flow_reaches_recommendations() -> None:
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    session_id = first.state.session_id

    second = await run_agent_flow(
        AgentRequest(
            user_input="무료인 곳으로", session_id=session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.llm_output.modify.modify_type == "CHANGE_CONDITION"
    assert second.state.user_conditions.budget == "free"
    assert second.recommendations is not None


@pytest.mark.asyncio
async def test_location_only_turn_after_recommend_is_modify_and_keeps_prior_conditions() -> None:
    """TP-67: 이전 추천 뒤 위치만 바꾸는 발화는 soft reset 없이 기존 조건을 유지한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="비 오는데 경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.state.user_conditions.weather == "rain"
    assert first.state.user_conditions.weather_intent == "AVOID"
    assert first.state.user_conditions.environment == "indoor"

    second = await run_agent_flow(
        AgentRequest(
            user_input="광화문 근처에서",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.state.user_conditions.search_center == "광화문"
    assert second.state.user_conditions.weather == "rain"
    assert second.state.user_conditions.weather_intent == "AVOID"
    assert second.state.user_conditions.environment == "indoor"


@pytest.mark.asyncio
async def test_needs_clarification_skips_tool_and_recommendation() -> None:
    """LLM 단계 needs_clarification(눈/weather_intent 모호) — C 호출 자체를 안 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="눈 오는데 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status is OutputStatus.NEEDS_CLARIFICATION
    assert response.recommendations is None
    assert providers["tool_provider"].call_count == 0
    assert providers["recommendation_provider"].call_count == 0
    # needs_clarification이어도 state는 채워진다(변화 없는 현재 상태).
    assert response.state.session_id


@pytest.mark.asyncio
async def test_tool_needs_clarification_skips_recommendation() -> None:
    """C 단계 자체의 needs_clarification(위치 정보 전무) — LLM 단계와 별개 레이어.

    "카페 추천해줘"는 장소명이 전혀 없어 LLM 단계는 COMPLETE로 끝나지만(current_location/
    search_center 둘 다 None), device_location(GPS)은 UserConditions.current_location이
    아니므로 C 계약상 needs_clarification 대상이다 — Tool은 호출되지만 Recommendation은
    호출되지 않아야 한다.

    (2026-08-12, PR 2/A2) location_required는 이제 종로구 대표 스팟 되묻기 버튼을
    붙인다(docs/design/clarification-options.md 7절) — agent_runtime이 llm_output을
    NEEDS_CLARIFICATION + options로 덮어써서 프론트가 버튼을 렌더링할 수 있게 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(user_input="카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "RECOMMEND"
    assert response.llm_output.status is OutputStatus.NEEDS_CLARIFICATION
    assert response.state.user_conditions.current_location is None
    assert response.state.user_conditions.search_center is None
    assert response.recommendations is None
    assert providers["tool_provider"].call_count == 1
    assert providers["recommendation_provider"].call_count == 0

    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.message == (
        "어디 근처에서 찾아드릴까요? 원하시는 지역을 알려주세요."
    )
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"경복궁", "인사동", "광화문", "북촌"}
    assert all(option.resolved_intent == "RECOMMEND" for option in clarification.options)

    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "location_required"


@pytest.mark.asyncio
async def test_agent_context_request_weather_not_mixed_with_provider_weather() -> None:
    """conditions.weather(5단계 사용자-명시)가 api_weather(B의 옛 3단계 Provider
    필드)와 섞이지 않는지 검증한다(A-C Context Contract v0 §5.2).

    사용자가 "비"를 언급하면 5단계 UserConditions.weather는 "rain"이 돼야 한다.
    (2026-08-05, D-038) api_weather를 채우던 session_orchestrator.py의 날씨 조회
    경로는 제거했다 — 이 값을 읽는 소비자가 없어서다(decision-log.md D-038).
    그래서 api_weather는 이제 영구히 None이다 — 이 테스트는 "혹시 conditions.weather
    계산에 api_weather 같은 다른 소스가 섞여 들어가지 않는지"를 여전히 지킨다.

    (2026-08-06, D-053 후속) 2턴째는 이제 MODIFY 경로를 탄다 — 이전 추천이 있는 상태의
    "지명 + 근처 + 다른 조건" 발화를 Fake도 실 Gemini처럼 MODIFY로 분류하게 맞췄기
    때문이다. 같은 검증의 RECOMMEND 경로 판은
    `test_agent_recommend_path_weather_not_mixed_with_provider_weather`가 맡는다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    session_id = first.state.session_id

    second = await run_agent_flow(
        AgentRequest(
            user_input="비 오는데 경복궁 근처 카페 추천해줘",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    tool_provider = providers["tool_provider"]
    assert tool_provider.last_request is not None
    assert second.llm_output.intent == "MODIFY"
    # C에 보낸 요청의 conditions.weather는 사용자가 말한 5단계 값이다.
    assert tool_provider.last_request.conditions.weather == "rain"
    # api_weather는 더 이상 채워지지 않는다(제거됨) — conditions.weather와 섞이지 않는다.
    assert second.state.api_context.api_weather is None
    assert second.state.user_conditions.weather == "rain"


@pytest.mark.asyncio
async def test_agent_recommend_path_weather_not_mixed_with_provider_weather() -> None:
    """위 테스트의 RECOMMEND 경로 판.

    이전 추천이 없으면 같은 발화가 RECOMMEND로 분류된다(D-053에서 맞춘 Fake 분류의
    반대 방향 회귀). 이 경로에서도 conditions.weather는 사용자가 말한 값이어야 하고
    api_weather가 섞여 들어오지 않아야 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="비 오는데 경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    tool_provider = providers["tool_provider"]
    assert response.llm_output.intent == "RECOMMEND"
    assert tool_provider.last_request is not None
    assert tool_provider.last_request.conditions.weather == "rain"
    assert response.state.api_context.api_weather is None
    assert response.state.user_conditions.weather == "rain"


@pytest.mark.parametrize(
    "user_input",
    ["경복궁 오늘 열어?", "경복궁은 언제 지어졌어?", "주식 추천해줘"],
    ids=["info", "general", "out_of_scope"],
)
@pytest.mark.asyncio
async def test_non_recommend_modify_intents_skip_tool_and_recommendation(user_input: str) -> None:
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(user_input=user_input, session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    assert response.llm_output.intent not in ("RECOMMEND", "MODIFY")
    assert response.recommendations is None
    assert providers["tool_provider"].call_count == 0
    assert providers["recommendation_provider"].call_count == 0


@pytest.mark.asyncio
async def test_schedule_intent_reaches_planner_and_returns_schedule() -> None:
    """SCHEDULE-04: 조건 추출 → C/D 호출(D는 limit=10) → 일정 편성 모듈까지 실제로
    이어진다(docs/design/int-07-schedule.md 4절)."""
    store = InMemoryStateStore()
    providers = _providers()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        stream_event_sink=sink,
        **providers,
    )

    assert response.llm_output.intent == "SCHEDULE"
    assert response.llm_output.status is OutputStatus.COMPLETE
    assert response.state.user_conditions.search_center == "경복궁"

    assert providers["tool_provider"].call_count == 1
    assert providers["recommendation_provider"].call_count == 1
    assert providers["recommendation_provider"].last_limit == 10

    assert response.recommendations is None
    assert response.schedule is not None
    assert 1 <= len(response.schedule.items) <= 5
    assert response.schedule.basis_note
    assert "코스를 짜봤어요" in response.message

    progress_stages = [payload["stage"] for event, payload in events if event == "progress"]
    assert "scheduling" in progress_stages
    assert progress_stages.index("scoring") < progress_stages.index("scheduling")
    assert progress_stages.index("scheduling") < progress_stages.index("composing_message")

    context = get_session_context(response.state.session_id, store=store)
    schedule_ids = {item.place_id for item in response.schedule.items}
    assert schedule_ids
    assert set(context.shown_place_ids) == schedule_ids


@pytest.mark.asyncio
async def test_schedule_then_reject_all_modify_reroutes_to_new_schedule() -> None:
    """SCHEDULE-06: SCHEDULE 다음 턴 "다른 곳 보여줘"는 classify_intent()에서
    여전히 MODIFY로 분류되지만(docs/design/int-07-schedule.md 3.1절), 직전
    턴이 SCHEDULE로 완료됐다면 agent_runtime이 B의 last_intent를 보고 일정
    재편성으로 재라우팅한다. classify_intent/extract_modify_conditions는
    수정하지 않았다 — REJECT_ALL로 직전 일정 장소가 rejected에 들어가 새
    일정에서 자동 제외되는지까지 함께 확인한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    first_ids = {item.place_id for item in first.schedule.items}
    assert first_ids

    second = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    # A가 실제로 분류했을 raw intent는 MODIFY다 — agent_runtime이 결과 라벨만
    # SCHEDULE로 바꿔치기했다는 걸 응답으로 간접 확인한다(recommendations가
    # 아니라 schedule이 채워짐).
    assert second.llm_output.intent == "SCHEDULE"
    assert second.recommendations is None
    assert second.schedule is not None
    second_ids = {item.place_id for item in second.schedule.items}
    assert second_ids
    assert second_ids.isdisjoint(first_ids)

    context = get_session_context(second.state.session_id, store=store)
    assert set(context.shown_place_ids) == second_ids


@pytest.mark.asyncio
async def test_schedule_then_change_condition_modify_merges_before_rerouting() -> None:
    """SCHEDULE-06 PR 리뷰에서 A가 요청한 시나리오: REJECT_ALL이 아니라
    CHANGE_CONDITION("실내 위주로 바꿔줘")도 조건이 먼저 B에 병합된 뒤에만
    일정 재편성으로 재라우팅돼야 한다 — llm_output.intent를 조건 병합 전에
    SCHEDULE로 바꿔치기하면 modify.condition_changes(environment=INDOOR)가
    반영되지 않을 수 있다는 우려였다. agent_runtime.py의 override는 이미
    apply()/transform() 뒤에 위치해 이 순서를 지키고 있음을 확인한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"

    second = await run_agent_flow(
        AgentRequest(
            user_input="실내로 바꿔줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    # raw 분류는 MODIFY(CHANGE_CONDITION)였다는 걸 간접 확인 — REJECT_ALL과
    # 달리 이번엔 이전 일정 장소를 배제하는 게 아니라 조건만 바뀐다.
    assert second.llm_output.intent == "SCHEDULE"
    assert second.schedule is not None
    # 조건 병합이 relabel보다 먼저 일어났다는 증거: 병합된 State에 반영됨
    assert second.state.user_conditions.environment == "indoor"


@pytest.mark.asyncio
async def test_schedule_then_ambiguous_recommend_triggers_clarification() -> None:
    """docs/design/clarification-options.md 5절(PR 1, 케이스 1): SCHEDULE 완료 직후
    "카페 추천해줘"류는 classify_intent()에서 MODIFY(CHANGE_CONDITION)로 나오지만
    "일정 재조정"인지 "그냥 추천"인지 글자로 구분이 안 된다 — SCHEDULE로 강제
    라벨링하지 않고 되묻기 버튼 2개로 끝나야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"

    second = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert second.recommendations is None
    assert second.schedule is None
    assert second.llm_output.clarification is not None
    clarification = second.llm_output.clarification
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"schedule_continue", "recommend_only"}

    # 이번 턴에서 추출된 카테고리(카페)가 범용 "장소" 대신 문구/버튼에 그대로 들어간다.
    assert clarification.message == "이어서 일정을 다시 짜드릴까요, 아니면 카페만 추천해드릴까요?"
    recommend_only = next(o for o in clarification.options if o.id == "recommend_only")
    assert recommend_only.label == "카페만 추천받기"

    context = get_session_context(second.state.session_id, store=store)
    assert context.pending_clarification == "schedule06_ambiguous_recommend"
    # apply()가 이 턴의 원본 intent(MODIFY)로 last_intent를 이미 저장했으므로, 여기서
    # SCHEDULE로 바로잡지 않으면 다음 턴 classify_intent가 "직전 SCHEDULE 되묻기"라는
    # 신호를 못 받는다(2026-08-31 실사용 재현, D-061과 같은 이유의 누락).
    assert context.last_intent == "SCHEDULE"


@pytest.mark.asyncio
async def test_schedule06_ambiguous_free_text_recommend_only_switches_to_recommend() -> None:
    """schedule06_ambiguous_recommend 되묻기에 "추천만 해줘"류 자유 텍스트로 답하면
    RECOMMEND로 전환되어야 한다 — 두 선택지가 서로 다른 인텐트라 위 SCHEDULE 되묻기의
    "SCHEDULE 유지" 규칙을 그대로 적용하면 틀린다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    answered = await run_agent_flow(
        AgentRequest(
            user_input="추천만 해줘",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert answered.llm_output.intent == "RECOMMEND"


@pytest.mark.asyncio
async def test_schedule06_ambiguous_free_text_continue_keeps_schedule() -> None:
    """같은 되묻기에 "이어서 계속"류로 답하면 SCHEDULE을 유지해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    answered = await run_agent_flow(
        AgentRequest(
            user_input="이어서 계속 진행해줘",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert answered.llm_output.intent == "SCHEDULE"


@pytest.mark.asyncio
async def test_schedule_then_ambiguous_recommend_without_category_uses_generic_label() -> None:
    """카테고리를 언급하지 않은 모호 발화("경복궁 근처 알려줘")는 추출된 카테고리가
    없으므로 범용 "장소만 추천받기" 문구로 폴백해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"

    second = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 알려줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = second.llm_output.clarification
    assert clarification is not None
    assert clarification.message == "이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"
    recommend_only = next(o for o in clarification.options if o.id == "recommend_only")
    assert recommend_only.label == "장소만 추천받기"


@pytest.mark.asyncio
async def test_clarification_choice_location_quick_pick_resolves_to_recommend() -> None:
    """docs/design/clarification-options.md 7절(PR 2, A2): location_required 되묻기
    버튼("경복궁 근처") 클릭 시 classify_intent() 재호출 없이 원래 intent(RECOMMEND)로
    바로 해소되고, 고른 지명이 search_center에 반영돼야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="경복궁",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is not None
    assert resolved.state.user_conditions.search_center == "경복궁"
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_location_quick_pick_resolves_to_schedule() -> None:
    """location_required 되묻기가 SCHEDULE 턴에서 발생한 경우, 버튼 클릭이
    last_intent(SCHEDULE)를 복원해 일정 편성까지 이어져야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="반나절 코스 짜줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.intent == "SCHEDULE"
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="경복궁",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.schedule is not None
    assert resolved.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_stale_location_clarification_choice_falls_back_to_normal_classification() -> None:
    """세션에 location_required 되묻기가 없는 상태에서 온 clarification_choice는
    죽지 않고 평소 build_interpretation() 경로로 폴백해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            clarification_choice="경복궁",  # 이 세션엔 되묻기가 없었다
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.llm_output.intent == "RECOMMEND"
    assert response.recommendations is not None


@pytest.mark.asyncio
async def test_selected_search_center_fills_location_the_utterance_omitted() -> None:
    """위치 설정 화면에서 고른 검색 위치는 발화에 위치가 없을 때 조건을 채운다.

    "카페 추천해줘"는 위치가 전혀 없어 평소라면 location_required 되묻기로 끝난다
    (test_tool_needs_clarification_skips_recommendation). 화면에서 이미 위치를
    골랐다면 다시 물을 이유가 없으므로 그대로 추천까지 가야 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_search_center="경복궁",
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.state.user_conditions.search_center == "경복궁"
    assert response.recommendations is not None


@pytest.mark.asyncio
async def test_selected_current_location_fills_the_travel_origin() -> None:
    """화면에서 정한 출발지는 검색 기준과 다른 자리에 들어간다.

    "어디 있는가"(current_location)와 "어디를 찾을까"(search_center)는 다른 질문이라
    (D-067), 화면이 안국역을 출발지로 정했으면 검색 기준까지 안국역이 되면 안 된다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_current_location="안국역",
        ),
        store=store,
        **providers,
    )

    assert response.state.user_conditions.current_location == "안국역"
    assert response.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_spoken_origin_beats_selected_current_location() -> None:
    """"나 지금 OO인데"처럼 발화가 출발지를 말하면 그쪽이 이긴다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="나 지금 경복궁인데 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_current_location="안국역",
        ),
        store=store,
        **providers,
    )

    assert response.state.user_conditions.current_location == "경복궁"


@pytest.mark.asyncio
async def test_spoken_location_beats_selected_search_center() -> None:
    """그 턴에 말한 위치가 화면 설정을 이긴다 — 더 명확한 의사이기 때문이다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_search_center="인사동",
        ),
        store=store,
        **providers,
    )

    assert response.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_modify_turn_uses_locations_sent_with_this_request() -> None:
    """조건을 직접 나르지 않는 턴도 이번 요청에 실려 온 위치를 쓴다.

    화면에서 출발지를 바꾸고 "다른 곳 보여줘"라고 하면 MODIFY로 분류되는데, 이
    경로는 `_apply_selected_locations()`가 손대지 않아 병합된 세션 조건(=1턴의
    옛 위치)을 그대로 물려받았다. 요청에는 새 위치가 실려 오는데도 무시돼,
    화면에는 성수동이 떠 있고 실제 검색은 경복궁에서 도는 상태가 됐다
    (2026-09-07 로컬 실측으로 재현).
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_current_location="안국역",
        ),
        store=store,
        **providers,
    )
    assert first.state.user_conditions.current_location == "안국역"
    assert first.state.user_conditions.search_center == "경복궁"

    second = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            selected_current_location="성수동",
            selected_search_center="성수동",
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.state.user_conditions.current_location == "성수동"
    assert second.state.user_conditions.search_center == "성수동"


@pytest.mark.asyncio
async def test_modify_turn_keeps_session_locations_when_request_sends_none() -> None:
    """요청이 위치를 안 실어 보내면 세션에 쌓인 조건을 그대로 쓴다.

    위 테스트의 반대편이다 — "이번 요청 값이 있으면 쓴다"를 "없어도 덮어써서
    조건을 날린다"로 잘못 구현하면 여기서 걸린다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_current_location="안국역",
        ),
        store=store,
        **providers,
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.state.user_conditions.current_location == "안국역"
    assert second.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_spoken_location_beats_request_locations_on_modify_turn() -> None:
    """그 턴에 말한 위치는 화면 설정을 이긴다 — MODIFY 턴에서도 같다.

    `_apply_selected_locations()`가 RECOMMEND에서 지키는 규칙
    (test_spoken_location_beats_selected_search_center)을 새 경로도 똑같이
    지켜야 한다. 아니면 "창덕궁 근처"라고 말한 턴이 화면에 남아 있던 인사동으로
    검색된다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 근처로 바꿔줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            selected_search_center="인사동",
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.state.user_conditions.search_center == "창덕궁"


@pytest.mark.asyncio
async def test_blank_selected_search_center_is_ignored() -> None:
    """공백만 온 값은 위치를 고른 것으로 치지 않는다 — 평소 되묻기로 끝나야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            selected_search_center="   ",
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status is OutputStatus.NEEDS_CLARIFICATION
    assert response.state.user_conditions.search_center is None


@pytest.mark.asyncio
async def test_travel_origin_override_resolves_without_classification() -> None:
    """"OO 기준으로 다시 보기" 버튼(travel_origin_override, D-071)은
    classify_intent()를 건너뛰고 직전 조건에 travel_origin만 덮어써 재실행해야
    한다. 이를 증명하기 위해 이번 턴 user_input에 OUT_OF_SCOPE 마커("주식")를
    넣는다 — classify_intent()가 실제로 호출됐다면 OUT_OF_SCOPE로 분류될
    문장인데, 그대로 RECOMMEND/COMPLETE로 나오면 건너뛴 것이 증명된다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.status == OutputStatus.COMPLETE
    assert first.recommendations is not None

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="주식 얘기처럼 보이지만 버튼 클릭이라 실제로는 해석되지 않는다",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            travel_origin_override=TravelOrigin.SEARCH_CENTER,
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is not None
    assert resolved.state.user_conditions.search_center == "경복궁"
    assert resolved.state.user_conditions.travel_origin == "search_center"


@pytest.mark.asyncio
async def test_travel_origin_override_falls_back_without_prior_recommendation() -> None:
    """추천 결과가 아직 없는 세션에서 온 override는 평소 경로로 폴백해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            travel_origin_override=TravelOrigin.SEARCH_CENTER,  # 이 세션엔 아직 추천이 없다
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.llm_output.intent == "RECOMMEND"
    assert response.recommendations is not None


# --- SCHEDULE-12 카드 3: 보관함 CTA(schedule_from_saved) ------------------------
# 하단 바의 "이 장소들로 일정 짜기"는 되묻기·기준 전환 버튼과 같은 결정적 요청이다.
# classify_intent()를 건너뛰고 바로 SCHEDULE로 들어가되, 보관함이 비어 있으면
# 평소 경로로 조용히 폴백해야 한다.


@pytest.mark.asyncio
async def test_schedule_from_saved_resolves_without_classification() -> None:
    """보관함 CTA는 classify_intent()를 건너뛰고 SCHEDULE로 확정해야 한다.

    travel_origin_override 테스트와 같은 방법으로 증명한다 — user_input에
    OUT_OF_SCOPE 마커("주식")를 넣는다. 분류가 실제로 돌았다면 OUT_OF_SCOPE로
    떨어질 문장인데 SCHEDULE로 나오면 건너뛴 것이다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.recommendations is not None
    assert first.recommendations.recommendations

    session_id = first.state.session_id
    state_service.save_place(
        session_id,
        state_service.SavePlaceRequest(
            place_id=first.recommendations.recommendations[0].place_id
        ),
        store=store,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="주식 얘기처럼 보이지만 버튼 클릭이라 실제로는 해석되지 않는다",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.COMPLETE


@pytest.mark.asyncio
async def test_schedule_from_saved_falls_back_when_nothing_is_saved() -> None:
    """보관함이 비어 있으면 평소 경로로 폴백한다.

    새로고침 뒤 남은 화면에서 눌렀거나 마지막 항목을 빼는 요청과 클릭이 겹친
    경우다. 빈 보관함으로 편성에 들어가면 "담은 곳"이 하나도 없는 일정이 나가
    버튼이 오작동한 것처럼 보인다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,  # 이 세션엔 담아둔 장소가 없다
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.llm_output.intent == "RECOMMEND"
    assert response.recommendations is not None


# --- 보관함 장소를 편성 후보에 주입 (SCHEDULE-12 후속) -------------------------
# 한 턴의 후보 풀은 C가 이번 반경에서 모아온 것이 전부다. 보관함 장소를 거기
# 넣어주는 단계가 없으면 이전 턴에 담은 장소는 후보가 되지 못하고
# planner._resolve_must_include()가 조용히 버려, D-114의 배치 보장이 무력해진다.


class _DroppingToolProvider(_CountingToolProvider):
    """두 번째 호출부터 지정한 place_id를 후보에서 뺀다.

    담아둔 뒤 다음 턴 검색이 그 장소를 다시 못 물어오는 상황을 재현한다 —
    지역 POI가 후보 상한보다 많으면 매 턴 다른 후보가 뽑혀 흔하게 일어난다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.drop_place_id: str | None = None
        # 호출마다 실제로 돌려준 후보 id. 픽스처가 의도한 상황을 정말 만들었는지
        # 테스트가 직접 확인하기 위한 것이다.
        self.returned_ids: list[list[str]] = []

    def _record(self, response):
        places = response.context.places if response.context is not None else None
        data = places.data if places is not None and places.data is not None else []
        self.returned_ids.append([place.place_id for place in data])
        return response

    async def fetch_context(self, request):
        response = await super().fetch_context(request)
        if self.drop_place_id is None or response.context is None:
            return self._record(response)
        places = response.context.places
        if places is None or places.data is None:
            return self._record(response)
        kept = [place for place in places.data if place.place_id != self.drop_place_id]
        return self._record(response.model_copy(
            update={
                "context": response.context.model_copy(
                    update={"places": places.model_copy(update={"data": kept})}
                )
            }
        ))


class _FakePlaceDetailsRepository:
    """content_id로 상세를 돌려주는 최소 저장소."""

    def __init__(self, details: dict[str, StoredPlaceDetail]) -> None:
        self._details = details
        self.requested_ids: list[str] = []

    async def get_active_place_details(self, content_ids, *, include_barrier_free=False):
        ids = list(content_ids)
        self.requested_ids.extend(ids)
        return {cid: self._details[cid] for cid in ids if cid in self._details}


def _stored_detail(
    place_id: str, *, latitude: float = 37.5796, longitude: float = 126.9770
) -> StoredPlaceDetail:
    return StoredPlaceDetail(
        content_id=place_id,
        content_type_id="12",
        title=f"담아둔 {place_id}",
        address="서울 종로구",
        operating_hours_raw=None,
        rest_date_raw=None,
        detail_fetch_status="success",
        detail_fetched_at=None,
        source_modified_at=None,
        latitude=latitude,
        longitude=longitude,
    )


async def _recommend_then_save(store, providers) -> tuple[str, str]:
    """추천 한 번 받고 첫 장소를 보관함에 담는다. (session_id, place_id) 반환."""

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.recommendations is not None
    assert first.recommendations.recommendations
    session_id = first.state.session_id
    place_id = first.recommendations.recommendations[0].place_id
    state_service.save_place(
        session_id,
        state_service.SavePlaceRequest(place_id=place_id),
        store=store,
    )
    return session_id, place_id


@pytest.mark.asyncio
async def test_saved_place_missing_from_candidates_is_reported_without_repository() -> None:
    """회귀 재현 — 상세 저장소가 없으면 담아둔 장소가 후보에서 빠진 채로 편성된다.

    아래 주입 테스트가 실제로 무언가를 고쳤음을 보이려면, 같은 시나리오가
    고치기 전에는 실패했다는 것이 함께 잠겨 있어야 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _DroppingToolProvider()
    providers["tool_provider"] = tool_provider

    session_id, place_id = await _recommend_then_save(store, providers)
    tool_provider.drop_place_id = place_id

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        **providers,  # place_details_repository 없음
    )

    assert response.schedule is not None
    assert response.schedule.absent_saved_place_names != []


@pytest.mark.asyncio
async def test_saved_place_missing_from_candidates_is_injected() -> None:
    """상세 저장소가 있으면 후보에 없던 보관함 장소를 채워 넣어 편성 대상이 된다."""

    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _DroppingToolProvider()
    providers["tool_provider"] = tool_provider

    session_id, place_id = await _recommend_then_save(store, providers)
    tool_provider.drop_place_id = place_id
    repository = _FakePlaceDetailsRepository({place_id: _stored_detail(place_id)})

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert response.schedule is not None
    # 진단 순서: 조회를 시도했는가 → 상한을 올렸는가 → 후보에 들어갔는가.
    # 앞에서 끊기면 뒤를 볼 필요가 없다.
    assert tool_provider.returned_ids, "두 번째 턴에 C 조회가 일어나지 않았다"
    assert place_id not in tool_provider.returned_ids[-1], (
        "픽스처가 의도한 상황을 못 만들었다 — 마지막 C 응답에 그 장소가 그대로 있다"
    )
    assert place_id in repository.requested_ids, "상세 조회 자체가 시도되지 않았다"
    # 후보에 들어갔으므로 "후보에 아예 없었다"는 안내는 나가지 않는다.
    assert response.schedule.absent_saved_place_names == []


@pytest.mark.asyncio
async def test_injected_saved_place_does_not_inflate_the_scoring_limit() -> None:
    """주입했다고 채점 상한을 올리지 않는다.

    한때 상한을 주입 개수만큼 올려서 자르기를 피하려 했는데 두 가지로 틀렸다.
    후보 풀이 상한보다 크면 방어가 안 되고(그때는 하위권에 깔릴 뿐이다),
    `_score_with_measured_routes()`의 `shortlist_limit`이 상한을 따라가므로
    도보 실측 조회까지 함께 늘어난다 — D-113이 줄여놓은 호출 수가 되돌아간다.

    지금은 상한을 그대로 두고, 자르기에서 빠진 것만 좁혀 다시 채점해 붙인다.
    """

    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _DroppingToolProvider()
    providers["tool_provider"] = tool_provider

    session_id, place_id = await _recommend_then_save(store, providers)
    tool_provider.drop_place_id = place_id
    repository = _FakePlaceDetailsRepository({place_id: _stored_detail(place_id)})

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    limits = providers["recommendation_provider"].limits
    assert agent_runtime_module.SCHEDULE_RECOMMENDATION_LIMIT in limits, (
        "SCHEDULE 채점이 일어나지 않았다"
    )
    assert agent_runtime_module.SCHEDULE_RECOMMENDATION_LIMIT + 1 not in limits, (
        "상한을 주입 개수만큼 올리면 실측 조회 대상까지 함께 커진다"
    )
    assert response.schedule is not None
    assert response.schedule.absent_saved_place_names == []


@pytest.mark.asyncio
async def test_saved_place_injection_skipped_when_details_are_unknown() -> None:
    """상세 저장소가 그 장소를 모르면 주입하지 않고 안내로 넘긴다.

    테이블에서 지워진 장소 등이다. 조용히 빠뜨리지 않는 것이 핵심이다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _DroppingToolProvider()
    providers["tool_provider"] = tool_provider

    session_id, place_id = await _recommend_then_save(store, providers)
    tool_provider.drop_place_id = place_id
    repository = _FakePlaceDetailsRepository({})  # 아무것도 모른다

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert response.schedule is not None
    assert response.schedule.absent_saved_place_names != []


@pytest.mark.asyncio
async def test_schedule_bare_restart_during_location_ask_triggers_clarification() -> None:
    """docs/design/clarification-options.md 케이스 4(PR 4): SCHEDULE이 위치를 못
    찾아 되묻는 중(location_required) 목적어 없는 "처음부터 다시"는 classify_intent()
    호출 없이 결정적으로 되물어야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="카페 위주로 일정 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    assert first.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    context = get_session_context(first.state.session_id, store=store)
    assert context.pending_clarification == "location_required"

    second = await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "SCHEDULE"
    assert second.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = second.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"restart", "keep_asking"}
    context = get_session_context(second.state.session_id, store=store)
    assert context.pending_clarification == "schedule_bare_restart"


@pytest.mark.asyncio
async def test_clarification_choice_schedule_restart_wipes_conditions() -> None:
    """"네, 처음부터 다시 잡을게요" 클릭은 조건을 비우고 다시 location_required로
    이어져야 한다(PR 2의 종로구 대표 스팟 버튼으로 자연스럽게 연결)."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="카페 위주로 일정 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert "카페" in first.state.user_conditions.place_tags
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="네, 처음부터 다시 잡을게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="restart",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert resolved.state.user_conditions.place_tags == []
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification == "location_required"


@pytest.mark.asyncio
async def test_clarification_choice_schedule_keep_asking_preserves_conditions() -> None:
    """"아니요, 위치만 알려드릴게요" 클릭은 조건을 그대로 두고 같은
    location_required를 다시 띄워야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="카페 위주로 일정 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="아니요, 위치만 알려드릴게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="keep_asking",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert "카페" in resolved.state.user_conditions.place_tags
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification == "location_required"


@pytest.mark.asyncio
async def test_bare_restart_during_active_recommend_triggers_clarification() -> None:
    """docs/design/clarification-options.md 케이스 5(PR 4): 되묻기 중이 아닌 활성
    RECOMMEND 흐름에서 목적어 없는 "처음부터 다시"는 결정적으로 되물어야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "RECOMMEND"
    assert first.recommendations is not None

    second = await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = second.llm_output.clarification
    assert clarification is not None
    # 장소(경복궁 근처) + 카테고리(카페) 둘 다 채워졌으므로 우선순위 규칙대로 둘 다
    # 들어간다(장소 → 날씨 → 카테고리, 최대 2개).
    assert clarification.message == (
        "경복궁 근처 카페로 다시 알아볼까요, 아니면 새로운 목적지로 찾아볼까요?"
    )
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"keep_context", "full_reset"}
    context = get_session_context(second.state.session_id, store=store)
    assert context.pending_clarification == "bare_restart_active"


@pytest.mark.asyncio
async def test_clarification_choice_keep_context_resolves_to_modify_reject_all() -> None:
    """"경복궁 근처로 다시 찾아주세요" 클릭은 조건은 유지한 채 REJECT_ALL로
    재조회해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처로 다시 찾아주세요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="keep_context",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "MODIFY"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is not None
    assert resolved.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_clarification_choice_full_reset_wipes_conditions_without_auto_searching() -> None:
    """"새로 시작할게요" 클릭은 조건을 전부 비우되, Tool을 바로 부르지 않고 새
    목적지/조건을 직접 말해달라는 터미널 문구로 끝나야 한다.

    (2026-08-13 실사용 재현) 예전엔 조건을 비운 뒤 그대로 Tool까지 이어져서,
    GPS만 있으면 그걸로 조용히 추천이 나가버렸다("현재 계신 곳에서 가까운
    두가헌 레스토랑을...") — "새로 시작"이라는 사용자 의도(새 조건을 직접
    말하고 싶다)와 어긋난다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    tool_calls_before_resolution = providers["tool_provider"].call_count
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="새로 시작할게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="full_reset",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is None
    assert resolved.schedule is None
    assert resolved.message == "새로운 목적지를 입력하거나 원하시는 조건을 알려주세요!"
    assert resolved.state.user_conditions.search_center is None
    assert resolved.state.user_conditions.place_tags == []
    # 이 해소 턴에서는 Tool이 추가로 불리지 않아야 한다 — GPS만으로 조용히
    # 추천이 나가는 걸 막는 게 이번 수정의 핵심이다.
    assert providers["tool_provider"].call_count == tool_calls_before_resolution
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_bare_restart_after_schedule_completed_triggers_clarification() -> None:
    """실사용 재현(2026-08-13): SCHEDULE이 되묻기 없이 성공적으로 완료된 뒤 목적어
    없는 "처음부터 다시"는 케이스 4/5 어디에도 안 걸려서 SCHEDULE-06이 무조건 같은
    조건으로 재편성을 시도했고, 후보가 부족하면 "일정을 만들지 못했어요" 실패
    문구로 샜다. SCHEDULE 전용 되묻기로 잡아야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    assert first.llm_output.status == OutputStatus.COMPLETE

    second = await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "SCHEDULE"
    assert second.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = second.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"retry_schedule", "full_reset"}
    context = get_session_context(second.state.session_id, store=store)
    assert context.pending_clarification == "schedule_bare_restart_completed"


@pytest.mark.asyncio
async def test_clarification_choice_retry_schedule_keeps_conditions_and_replans() -> None:
    """"{조건}로 다시 짜주세요" 클릭은 같은 조건으로 SCHEDULE 재편성해야 한다
    (REJECT_ALL이 아니다 — MODIFY 결과 모양은 SCHEDULE에 안 맞는다)."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처로 다시 짜주세요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="retry_schedule",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.schedule is not None
    assert resolved.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_clarification_choice_schedule_full_reset_wipes_conditions() -> None:
    """SCHEDULE 완료 후 되묻기의 "새로 시작할게요"는 RECOMMEND로 전환하고 조건을
    비운다 — 케이스 5의 full_reset과 동일하게 Tool 호출 없이 터미널 문구로 끝난다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="새로 시작할게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="full_reset",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is None
    assert resolved.schedule is None
    assert resolved.message == "새로운 목적지를 입력하거나 원하시는 조건을 알려주세요!"
    assert resolved.state.user_conditions.search_center is None


@pytest.mark.asyncio
async def test_clarification_choice_schedule_continue_resolves_to_schedule() -> None:
    """되묻기 버튼 "일정 다시 짜기" 클릭 시 classify_intent() 재호출 없이 바로
    SCHEDULE로 해소돼야 한다(docs/design/clarification-options.md 3절)."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="일정 다시 짜기",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="schedule_continue",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.schedule is not None
    assert resolved.recommendations is None
    # 되묻기 답변 턴을 소비했으므로 다음 턴 판단에 영향을 주지 않는다.
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_recommend_only_resolves_to_recommend() -> None:
    """되묻기 버튼 "장소만 추천받기" 클릭 시 RECOMMEND로 해소되고, 되묻기 턴에서
    이미 병합된 조건(search_center=경복궁, place_tags=카페)이 그대로 쓰인다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.state.user_conditions.search_center == "경복궁"

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="장소만 추천받기",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="recommend_only",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.schedule is None
    assert resolved.recommendations is not None
    assert resolved.state.user_conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_stale_clarification_choice_falls_back_to_normal_classification() -> None:
    """세션에 남은 pending_clarification과 안 맞는(또는 없는) clarification_choice는
    죽지 않고 평소 build_interpretation() 경로로 폴백해야 한다 — 새로고침 후 오래된
    버튼 클릭 같은 상황을 흉내 낸다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            clarification_choice="schedule_continue",  # 이 세션엔 되묻기가 없었다
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.llm_output.intent == "RECOMMEND"
    assert response.recommendations is not None


@pytest.mark.asyncio
async def test_schedule_then_reject_specific_modify_keeps_other_items() -> None:
    """SCHEDULE-09 2단계: "두 번째는 별로야"는 REJECT_ALL과 달리 지목한 자리만
    갈아끼운다. classify_intent()가 순번+거절 신호 조합을 MODIFY로 분류하고
    (D-059 갭 수정분), extract_modify_conditions()가 target_indices=[2]를
    뽑아내면, agent_runtime이 이전 shown_recommendations에서 1·3번은 그대로
    pinned_items로 옮기고 plan_partial_schedule()이 2번 자리만 D의 새 후보로
    채운다 — 통째로 새 일정을 짜는 REJECT_ALL 경로(위 테스트)와 대비된다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    assert first.schedule is not None
    assert len(first.schedule.items) == 3
    first_by_order = {item.order: item.place_id for item in first.schedule.items}

    second = await run_agent_flow(
        AgentRequest(
            user_input="두 번째는 별로야",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    # raw 분류는 MODIFY(REJECT_SPECIFIC)였다는 걸 간접 확인 — relabel로
    # intent만 SCHEDULE로 바뀌었을 뿐, recommendations가 아니라 schedule이
    # 채워진다.
    assert second.llm_output.intent == "SCHEDULE"
    assert second.recommendations is None
    assert second.schedule is not None
    second_by_order = {item.order: item.place_id for item in second.schedule.items}
    assert set(second_by_order) == {1, 2, 3}

    # 1번·3번은 그대로 유지, 2번만 새 장소로 교체.
    assert second_by_order[1] == first_by_order[1]
    assert second_by_order[3] == first_by_order[3]
    assert second_by_order[2] != first_by_order[2]
    assert second_by_order[2] not in first_by_order.values()

    context = get_session_context(second.state.session_id, store=store)
    assert set(context.shown_place_ids) == set(second_by_order.values())

    # B의 rejected 이력에는 지목된 2번 장소만 들어가야 한다 — 1번·3번은
    # REJECT_ALL과 달리 거절 처리되지 않는다.
    history = store.get_history(second.state.session_id)
    assert history is not None
    rejected_ids = {item.place_id for item in history.rejected}
    assert rejected_ids == {first_by_order[2]}


@pytest.mark.asyncio
async def test_schedule_reject_specific_fills_images_of_kept_items() -> None:
    """"두 번째는 별로야" 뒤에도 유지한 1번·3번 자리에 사진이 남는다.

    유지한 장소는 B에 저장된 직전 일정으로 다시 만드는데 거기에는 사진 주소가
    없고, 편성은 이번 턴 후보에서만 사진을 찾는다. 장소 DB에서 다시 조회하지
    않으면 새로 고른 자리만 사진이 나오고 나머지는 자리표시로 바뀐다(실사용 재현).
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.schedule is not None
    first_by_order = {item.order: item.place_id for item in first.schedule.items}
    kept_ids = [first_by_order[1], first_by_order[3]]
    repository = _FakePlaceDetailsRepository(
        {
            place_id: replace(
                _stored_detail(place_id),
                thumbnail_url=f"https://tong.visitkorea.or.kr/{place_id}.jpg",
                first_image_url=f"https://tong.visitkorea.or.kr/{place_id}-big.jpg",
            )
            for place_id in kept_ids
        }
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="두 번째는 별로야",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert second.schedule is not None
    by_order = {item.order: item for item in second.schedule.items}
    # 부분 재편성 경로를 탔는지부터 본다 — 전체 재편성으로 빠지면 아래 검사는 의미가 없다.
    assert [by_order[1].place_id, by_order[3].place_id] == kept_ids
    for place_id in kept_ids:
        assert place_id in repository.requested_ids
    for order in (1, 3):
        place_id = by_order[order].place_id
        assert by_order[order].image_url == f"https://tong.visitkorea.or.kr/{place_id}.jpg"
        assert (
            by_order[order].image_url_fallback
            == f"https://tong.visitkorea.or.kr/{place_id}-big.jpg"
        )


@pytest.mark.asyncio
async def test_schedule_reject_specific_survives_image_lookup_failure() -> None:
    """유지한 장소의 사진 조회가 실패해도 일정은 그대로 나간다. 사진만 빠진다."""

    class _FailingRepository:
        async def get_active_place_details(self, content_ids, *, include_barrier_free=False):
            raise RuntimeError("조회 실패")

    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.schedule is not None
    first_by_order = {item.order: item.place_id for item in first.schedule.items}

    second = await run_agent_flow(
        AgentRequest(
            user_input="두 번째는 별로야",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        place_details_repository=_FailingRepository(),
        **providers,
    )

    assert second.schedule is not None
    by_order = {item.order: item for item in second.schedule.items}
    assert by_order[1].place_id == first_by_order[1]
    assert by_order[3].place_id == first_by_order[3]
    assert by_order[1].image_url is None


@pytest.mark.asyncio
async def test_schedule_reject_specific_chains_across_consecutive_turns() -> None:
    """SCHEDULE-09 후속(D-061): REJECT_SPECIFIC 재조정이 연속으로 이어질 때도
    매번 부분 재편성이 걸려야 한다. apply()는 매 턴 relabel 이전의 원본
    intent(MODIFY)로 last_intent를 저장하는데, 3-3절 relabel 직후 그 값을
    다시 SCHEDULE로 맞춰주지 않으면(set_last_intent) 두 번째 REJECT_SPECIFIC
    턴이 직전 턴을 last_intent="MODIFY"로 보게 되어 재조정 감지 자체가
    실패한다 — 실사용 테스트에서 "두 번째는 별로야" 다음에 "세 번째 장소
    별로야"를 보내면 전체가 새로 짜이는 것으로 재현됐다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    first_by_order = {item.order: item.place_id for item in first.schedule.items}

    second = await run_agent_flow(
        AgentRequest(
            user_input="두 번째는 별로야",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert second.llm_output.intent == "SCHEDULE"
    assert second.schedule is not None
    second_by_order = {item.order: item.place_id for item in second.schedule.items}
    assert second_by_order[1] == first_by_order[1]
    assert second_by_order[3] == first_by_order[3]

    # 두 번째 재조정 턴: 이번엔 3번을 지목한다. 직전 턴(SCHEDULE로 relabel된
    # MODIFY)이 last_intent="SCHEDULE"로 올바르게 저장돼 있어야 재조정
    # 감지가 걸린다 — 실패하면 intent가 MODIFY로 남고 recommendations가
    # 채워진다(위 다른 테스트들과 동일한 증거 패턴).
    third = await run_agent_flow(
        AgentRequest(
            user_input="세 번째 장소 별로야",
            session_id=second.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert third.llm_output.intent == "SCHEDULE"
    assert third.recommendations is None
    assert third.schedule is not None
    third_by_order = {item.order: item.place_id for item in third.schedule.items}
    assert set(third_by_order) == {1, 2, 3}

    # 1번(첫 턴부터 유지)·2번(직전 턴에서 새로 채워짐)은 그대로, 3번만 교체.
    assert third_by_order[1] == first_by_order[1]
    assert third_by_order[2] == second_by_order[2]
    assert third_by_order[3] != second_by_order[3]
    assert third_by_order[3] not in second_by_order.values()

    history = store.get_history(third.state.session_id)
    assert history is not None
    rejected_ids = {item.place_id for item in history.rejected}
    assert rejected_ids == {first_by_order[2], second_by_order[3]}


@pytest.mark.asyncio
async def test_schedule_then_reject_specific_exclusion_pattern_keeps_only_mentioned() -> None:
    """SCHEDULE-09 후속: "두 번째 말고는 다 마음에 안 들어"는 지목한 자리만
    직접 거부하는 것과 정반대다 — 2번만 남기고 1·3번을 새 장소로 채운다.
    표면상 REJECT_ALL 예문("다 마음에 안 들어")과 겹치지만, "말고는"으로
    특정 순번을 예외 처리했으므로 REJECT_SPECIFIC(여집합)이어야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    first_by_order = {item.order: item.place_id for item in first.schedule.items}

    second = await run_agent_flow(
        AgentRequest(
            user_input="두 번째 말고는 다 마음에 안 들어",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "SCHEDULE"
    assert second.recommendations is None
    assert second.schedule is not None
    second_by_order = {item.order: item.place_id for item in second.schedule.items}
    assert set(second_by_order) == {1, 2, 3}

    # 2번만 유지, 1번·3번은 새 장소로 교체.
    assert second_by_order[2] == first_by_order[2]
    assert second_by_order[1] != first_by_order[1]
    assert second_by_order[3] != first_by_order[3]
    assert second_by_order[1] not in first_by_order.values()
    assert second_by_order[3] not in first_by_order.values()

    history = store.get_history(second.state.session_id)
    assert history is not None
    rejected_ids = {item.place_id for item in history.rejected}
    assert rejected_ids == {first_by_order[1], first_by_order[3]}


@pytest.mark.asyncio
async def test_schedule_then_reject_by_name_keeps_other_items() -> None:
    """SCHEDULE-09 후속(이름 지목): 순번이 아니라 장소 이름으로 "OO는 빼줘"라고
    해도 그 자리만 교체돼야 한다 — B가 이제 이름도 저장하므로(SCHEDULE-09 후속),
    agent_runtime이 InterpretRequest.shown_place_names로 이름 목록을 전달하고
    FakeLLMProvider가 이름→순번 매칭까지 해낸다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    first_by_order = {item.order: item.place_id for item in first.schedule.items}
    target_name = first.schedule.items[1].place_name  # order=2

    second = await run_agent_flow(
        AgentRequest(
            user_input=f"{target_name}은 빼줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "SCHEDULE"
    assert second.recommendations is None
    assert second.schedule is not None
    second_by_order = {item.order: item.place_id for item in second.schedule.items}

    # 1번·3번은 그대로, 이름으로 지목한 2번만 새 장소로 교체.
    assert second_by_order[1] == first_by_order[1]
    assert second_by_order[3] == first_by_order[3]
    assert second_by_order[2] != first_by_order[2]


@pytest.mark.asyncio
async def test_schedule_modify_reroute_skipped_when_pending_clarification() -> None:
    """SCHEDULE-06 안전장치: 직전 턴이 SCHEDULE였어도(last_intent="SCHEDULE")
    되묻기가 아직 안 끝났다면(pending_clarification이 남아있음) 재조정
    오버라이드가 걸리지 않는다 — 완료되지 않은 SCHEDULE을 재편성 대상으로
    오인하면 안 된다.

    develop 머지로 들어온 D-059(app/providers/stub.py의 FakeLLMProvider.
    classify_intent)는 last_intent="SCHEDULE" + pending_clarification 존재 시
    단순 후속 발화를 곧바로 SCHEDULE로 분류해 그 되묻기를 이어간다 — 이건 그
    자체로 올바른 동작이라 이 시나리오에서는 이 테스트가 검증하려는 override
    분기(llm_output.intent is Intent.MODIFY 조건)를 아예 타지 않는다. 그래서
    명시적 재시작 문구("처음부터 다시")로 D-059 분기를 우회하고 REJECT_ALL
    문구("다른 곳")로 MODIFY 분류를 유도해, override가 실제로 검사되는 경로를
    직접 태운다."""
    from app.state import session as session_module
    from app.state.history import record_recommended
    from app.state.schema import RecommendedItemInput

    store = InMemoryStateStore()
    providers = _providers()

    state, _ = session_module.get_or_create_session(store, None)
    state.last_intent = "SCHEDULE"
    state.pending_clarification = "ambiguous:weather_intent"
    store.save_state(state)
    record_recommended(
        store,
        state.session_id,
        "run_seed",
        [RecommendedItemInput(place_id="runtime-stub-museum-1", rank=1)],
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="처음부터 다시 다른 곳 보여줘",
            session_id=state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "MODIFY"
    assert response.schedule is None


@pytest.mark.asyncio
async def test_schedule_continuation_during_pending_clarification_does_not_build_schedule() -> None:
    """D-059 분기(직전 SCHEDULE 되묻기 중 후속 발화를 SCHEDULE로 이어 분류)를 탄
    경우에도, 되묻기가 안 끝났으므로 실제 일정 편성은 이번 턴에 실행되지
    않는다 — intent 라벨과 무관하게 지켜져야 하는 안전 속성이다."""
    from app.state import session as session_module
    from app.state.history import record_recommended
    from app.state.schema import RecommendedItemInput

    store = InMemoryStateStore()
    providers = _providers()

    state, _ = session_module.get_or_create_session(store, None)
    state.last_intent = "SCHEDULE"
    state.pending_clarification = "ambiguous:weather_intent"
    store.save_state(state)
    record_recommended(
        store,
        state.session_id,
        "run_seed",
        [RecommendedItemInput(place_id="runtime-stub-museum-1", rank=1)],
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "SCHEDULE"
    assert response.schedule is None


@pytest.mark.asyncio
async def test_device_location_is_used_this_turn_but_not_kept_for_the_next() -> None:
    """이번 턴의 좌표는 도구까지 그대로 가고, 다음 턴에는 남지 않는다.

    예전에는 최초 턴 직후 세션에 GPS를 심어 다음 턴이 재사용했다. 서버가 사용자
    좌표를 저장하지 않게 되면서(state/store.py::for_persistence) 심는 자리를 없앴다.

    **다음 턴이 좌표를 잃는 것이 이 변경의 내용이다.** 화면은 매 턴 좌표를 실어
    보내므로 실제 사용에서는 빈 채로 가는 일이 드물고, 비면 백엔드가 어디서 찾을지
    되묻는다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    assert first.state.api_context.gps_expired is True  # 최초 턴엔 아직 반영 전
    assert providers["tool_provider"].last_request.gps_location == Coordinates(
        latitude=37.5788, longitude=126.9770
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="무료인 곳으로", session_id=first.state.session_id, device_location=None
        ),
        store=store,
        **providers,
    )

    # 2턴은 좌표를 안 실어 보냈고 세션에도 남아 있지 않다.
    assert second.state.api_context.gps_expired is True
    assert second.state.api_context.gps_location is None
    assert providers["tool_provider"].last_request.gps_location is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_gps", ["not-a-gps-string", "91.0,126.9770"])
async def test_invalid_gps_format_skips_turn_without_error(invalid_gps: str) -> None:
    """형식 또는 좌표 범위가 잘못된 GPS는 예외 없이 이번 턴만 건너뛴다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=invalid_gps,
        ),
        store=store,
        **providers,
    )

    assert response.state.api_context.gps_location is None
    assert response.state.api_context.gps_expired is True


@pytest.mark.asyncio
async def test_record_recommendation_reflected_in_session_context() -> None:
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    context = get_session_context(response.state.session_id, store=store)
    shown_ids = {item.place_id for item in response.recommendations.recommendations}
    assert shown_ids
    assert set(context.shown_place_ids) == shown_ids


@pytest.mark.asyncio
async def test_record_recommendation_carries_compare_feature_snapshot() -> None:
    """COMPARE 데이터 출처 A안(2026-08-11): agent_runtime이 record_recommendation을
    호출할 때 distance_km/remaining_minutes/environment_type을 함께 넘겨,
    B의 이력에 그대로 저장되는지 확인한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    context = get_session_context(response.state.session_id, store=store)
    assert context.shown_recommendations
    by_id = {item.place_id: item for item in context.shown_recommendations}
    for item in response.recommendations.recommendations:
        stored = by_id[item.place_id]
        assert stored.distance_km == item.distance_km
        assert stored.remaining_minutes == item.remaining_minutes
        assert stored.environment_type == item.environment_type


@pytest.mark.asyncio
async def test_compare_flow_uses_last_recommendation_snapshots_and_returns_summary() -> None:
    """COMPARE는 새 후보 검색 없이 B의 마지막 추천 스냅샷만 C에 전달한다."""

    store = InMemoryStateStore()
    providers = _providers()
    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    compared = await run_agent_flow(
        AgentRequest(
            user_input="어디가 더 가까워?",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    tool_provider = providers["tool_provider"]
    assert compared.llm_output.intent == "COMPARE"
    assert compared.comparison is not None
    assert compared.comparison.criteria == "travel_time"
    assert tool_provider.call_count == 1  # 첫 RECOMMEND의 일반 Context 조회만 수행
    assert tool_provider.compare_call_count == 1
    assert tool_provider.last_compare_request is not None
    assert [item.rank for item in tool_provider.last_compare_request.candidates] == [1, 2, 3, 4, 5]
    assert "런타임 스텁" in compared.message
    assert compared.tool_execution is not None
    assert compared.tool_execution.operation == "compare_fetch"


def _providers_with_forced_compare():
    return {
        "llm": _LLMProviderForcingCompareWithFewShown(),
        "tool_provider": _CountingToolProvider(),
        "recommendation_provider": _CountingRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }


@pytest.mark.asyncio
async def test_compare_with_single_shown_triggers_clarification() -> None:
    """docs/design/clarification-options.md 케이스 3(PR 3): COMPARE 전제조건(노출
    2개 이상)을 위반한 채로 분류돼도(thinking 예산에 따라 실제로 발생, 2026-08-11
    68건 테스트) 그대로 비교를 시도하지 않고 되묻기 버튼 2개로 끝나야 한다."""
    store = InMemoryStateStore()
    providers = _providers_with_forced_compare()

    response = await run_agent_flow(
        AgentRequest(
            user_input="억지비교 어디가 좋아?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "COMPARE"
    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert response.comparison is None
    assert response.recommendations is None
    assert providers["tool_provider"].compare_call_count == 0

    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.message == "지금 보여드린 곳이 마음에 드시나요, 다른 곳도 보여드릴까요?"
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"keep_current", "show_more"}

    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "compare_single_shown"


@pytest.mark.asyncio
async def test_clarification_choice_compare_show_more_resolves_to_recommend() -> None:
    """"다른 곳도 보여주세요" 클릭은 REJECT_ALL로 재조회하는 기존 검증된 경로를
    그대로 탄다.

    검색 중심점이 있어야 REJECT_ALL 재조회가 location_required로 새지 않으므로,
    먼저 정상 RECOMMEND 턴으로 조건을 만든 뒤 compare_single_shown 되묻기 상태만
    직접 심는다(실제 COMPARE 오분류 재현은 위 detection 테스트가 이미 검증했다)."""
    store = InMemoryStateStore()
    providers = _providers_with_forced_compare()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    set_pending_clarification(
        SetPendingClarificationRequest(
            session_id=first.state.session_id, code="compare_single_shown"
        ),
        store=store,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳도 보여주세요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="show_more",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "MODIFY"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is not None
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_compare_keep_current_returns_canned_message() -> None:
    """"지금 장소가 마음에 들어요" 클릭은 조회할 것이 없으므로 Tool/LLM 호출 없이
    고정 문구로 바로 끝나야 한다."""
    store = InMemoryStateStore()
    providers = _providers_with_forced_compare()

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="억지비교 어디가 좋아?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="지금 장소가 마음에 들어요",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="keep_current",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "GENERAL"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.message == "네, 좋은 여행 되세요!"
    assert resolved.recommendations is None
    assert providers["tool_provider"].call_count == 0
    assert providers["tool_provider"].compare_call_count == 0
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_stale_compare_clarification_choice_falls_back_to_normal_classification() -> None:
    """세션에 compare_single_shown 되묻기가 없는 상태에서 온 clarification_choice는
    죽지 않고 평소 build_interpretation() 경로로 폴백해야 한다."""
    store = InMemoryStateStore()
    providers = _providers_with_forced_compare()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
            clarification_choice="keep_current",  # 이 세션엔 되묻기가 없었다
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.COMPLETE
    assert response.llm_output.intent == "RECOMMEND"
    assert response.recommendations is not None


@pytest.mark.asyncio
async def test_second_turn_sends_consumed_place_ids_to_context_provider() -> None:
    """ "다른 곳 보여줘"의 2회차에는 1회차 노출분이 C 요청에 실려야 한다.

    D에만 넘기고 C에는 안 넘기면, C가 같은 앞쪽 후보를 다시 가져오고 D가 그걸
    전부 걸러내 추천이 0건이 된다. 계약 필드가 배선에서 빠지는 걸 여기서 잡는다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = providers["tool_provider"]

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert tool_provider.last_request is not None
    assert tool_provider.last_request.excluded_place_ids == []
    shown_ids = {item.place_id for item in first.recommendations.recommendations}
    assert shown_ids

    await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert tool_provider.last_request is not None
    assert set(tool_provider.last_request.excluded_place_ids) == shown_ids


@pytest.mark.asyncio
async def test_info_concentration_flow_calls_tool_provider_once() -> None:
    """question_type=concentration만 C(fetch_info_context)를 거치고, D는 호출하지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 사람 많아?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.info.question_type == "concentration"
    assert response.recommendations is None
    assert providers["tool_provider"].info_call_count == 1
    assert providers["tool_provider"].call_count == 0  # fetch_context(RECOMMEND용)는 안 씀
    assert [execution.operation for execution in response.tool_executions] == ["info_concentration"]
    assert providers["recommendation_provider"].call_count == 0
    assert "창덕궁" in response.message
    assert "보통" in response.message  # FakeToolProvider 고정 데이터


class _ToolProviderWithoutInfoContext:
    """fetch_info_context()가 아직 없는 C Real 구현체를 흉내 낸다(과도기 상태)."""

    def __init__(self) -> None:
        self._inner = FakeToolProvider()

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        return await self._inner.fetch_context(request)


@pytest.mark.asyncio
async def test_info_concentration_falls_back_gracefully_without_fetch_info_context() -> None:
    """C가 fetch_info_context()를 아직 구현하지 않아도 AttributeError로 죽지 않고
    기존 '준비 중' 문구로 안전하게 낮아진다(실제 ContextService로 재현된 회귀)."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _ToolProviderWithoutInfoContext()

    response = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 사람 많아?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.info.question_type == "concentration"
    assert "준비 중" in response.message


@pytest.mark.asyncio
async def test_info_missing_place_free_text_answer_stays_info() -> None:
    """장소명 없이 되묻는 INFO(버튼이 없는 유일한 케이스)에 자유 텍스트로 답해도
    INFO가 이어져야 한다 — 이전엔 MODIFY로 새고 원래 질문(혼잡도)이 사라졌다
    (2026-08-31 실사용 재현: "사람많아?" → "여의도 한강공원"이 엉뚱한 식당 추천으로
    이어짐)."""
    store = InMemoryStateStore()
    providers = _providers()

    # 이전 추천 이력을 만들어 재현 사례의 전제조건("이전 추천 있음")을 맞춘다 —
    # 그래야 "지명 단독 → MODIFY" 규칙과 실제로 경합한다.
    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    session_id = first.state.session_id

    asked = await run_agent_flow(
        AgentRequest(
            user_input="사람 많아?", session_id=session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    assert asked.llm_output.intent == "INFO"
    assert asked.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    context = get_session_context(session_id, store=store)
    assert context.pending_clarification == "missing:place_name"
    assert context.pending_info_context is not None
    assert context.pending_info_context.question_type == "concentration"

    answered = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁", session_id=session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert answered.llm_output.intent == "INFO"
    assert answered.llm_output.info.question_type == "concentration"
    assert answered.llm_output.info.place_name == "창덕궁"
    assert providers["tool_provider"].info_call_count == 1
    assert "창덕궁" in answered.message


@pytest.mark.asyncio
async def test_info_place_ambiguous_free_text_answer_resolves_without_button() -> None:
    """place_ambiguous도 버튼 없이 후보 이름을 그대로 타이핑하면 이어져야 한다 —
    이전엔 clarification_choice가 없으면 pending_info_context를 읽는 경로 자체가
    없어 장소명만으로 처음부터 재분류됐다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _InfoPlaceAmbiguousToolProvider(
        ["창덕궁 제1주차장", "창덕궁 제2주차장"]
    )

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 주차장 정보 알려줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 제1주차장",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            # clarification_choice를 일부러 보내지 않는다 — 자유 텍스트 경로를 검증한다.
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "INFO"
    assert resolved.llm_output.info.question_type == "parking"


@pytest.mark.asyncio
async def test_fake_tool_provider_proxy_fallback_discloses_source() -> None:
    """알려진 관광지가 아닌 장소는 FakeToolProvider의 근접치 fallback 시뮬레이션을 탄다.

    stub.py의 place-name 사전이 FakeToolProvider의 관광지 목록과 겹쳐서(둘 다
    같은 6개 이름), 전체 파이프라인으로는 이 케이스를 자연스럽게 재현할 수
    없다 — FakeToolProvider.fetch_info_context()를 직접 호출해 검증한다.
    """
    provider = FakeToolProvider()
    request = InfoContextRequest(
        request_id="r1", place_name="용리단길카페", place_context="explicit"
    )

    response = await provider.fetch_info_context(request)

    assert response.status == "success"
    assert response.result.is_proxy is True
    assert response.result.requested_place_name == "용리단길카페"
    assert response.result.resolved_place_name == "경복궁"


@pytest.mark.asyncio
async def test_info_operating_hours_question_type_calls_tool_provider() -> None:
    """D-054/D-059: concentration 외 question_type도 이제 C를 거쳐 실제 응답을 받는다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 오늘 열어?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.info.question_type == "operating_hours"
    assert providers["tool_provider"].info_call_count == 1
    assert "준비 중" not in response.message
    # 운영시간 원문은 말풍선이 아니라 아래 info_place_card가 싣는다.
    assert "운영시간을 확인했어요" in response.message
    assert response.info_place_card is not None
    assert response.info_place_card.answer_fields["operating_hours"] == "09:00~18:00"
    assert response.info_place_card.overview == "조선 왕조의 법궁으로 1395년에 창건된 궁궐이다."


@pytest.mark.asyncio
async def test_info_realtime_parking_pairs_with_public_parking_card() -> None:
    """근처 주차장을 물으면 공영주차장도 이어서 조회해 둘째 카드로 붙인다(TP-115).

    근처(area 응답)는 목록이 짧고 실시간 대수가 잘 안 보이는 반면, 공영(구 단위)은
    목록이 길지만 멀 수 있다 — 하나만 보여주면 사용자는 다른 절반을 다시 물어야
    했다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 주차할 곳 있어?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.info.question_type == "realtime_parking"
    # 근처(1회) + 공영(짝, 1회) = 2번 C를 거친다.
    assert providers["tool_provider"].info_call_count == 2
    assert response.info_place_card is not None
    assert response.secondary_info_place_card is not None
    assert "공영주차장" in response.message


class _ScriptedAreaToolProvider:
    """place_name별로 미리 정해둔 InfoContextResponse를 돌려주는 대역(로드맵 24번
    자기 교정 재시도 검증용) — 실제 서울시 폐쇄목록 조회는 흉내 내지 않는다."""

    def __init__(self, responses_by_place_name: dict[str, InfoContextResponse]) -> None:
        self._responses = responses_by_place_name

    async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
        return self._responses[request.place_name]


class _ScriptedToolCallingLLMProvider(FakeLLMProvider):
    """실제 LLM 판단 대신, 정해진 순서(원래 지역 실패 → 다른 지역 성공)로 도구를
    불러보는 대역."""

    def __init__(self, *, original_area_name: str, retry_area_name: str) -> None:
        self._original_area_name = original_area_name
        self._retry_area_name = retry_area_name

    async def answer_with_tools(
        self,
        instruction: str,
        *,
        tools,
        max_tool_calls: int = 3,
    ):
        del instruction, max_tool_calls
        population_tool = tools[0]
        first_attempt = await population_tool(self._original_area_name)
        assert "찾지 못했" in first_attempt
        second_attempt = await population_tool(self._retry_area_name)
        return provider_result(
            f"{self._original_area_name}엔 없었지만 {self._retry_area_name}엔 있어요: "
            f"{second_attempt}",
            source=ProviderSource.FAKE_LLM,
        )


@pytest.mark.asyncio
async def test_agentic_realtime_info_retries_with_different_area() -> None:
    """no_data_empty처럼 곧장 되묻지 않고, LLM이 스스로 다른 지역으로 재조회한
    결과를 최종 응답·문장으로 쓴다(로드맵 24번, 강의교재 90강 자기 교정)."""

    no_data_response = InfoContextResponse(
        request_id="r1",
        status="no_data",
        result=RealtimeCityInfoResult(
            status="no_data",
            question_type="realtime_event",
            requested_place_name="교대",
            resolved_place_name="교대",
        ),
    )
    success_response = InfoContextResponse(
        request_id="r2",
        status="success",
        result=RealtimeCityInfoResult(
            status="success",
            question_type="realtime_event",
            requested_place_name="교대",
            resolved_place_name="강남역",
            area_name="강남역",
            fields={"강남 페스티벌": "9/1~9/10 · 강남역 광장"},
        ),
    )
    tool_provider = _ScriptedAreaToolProvider(
        {"교대": no_data_response, "강남역": success_response}
    )
    info_request = InfoContextRequest(
        request_id="req-1",
        place_name="교대",
        place_context="explicit",
        question_type="realtime_event",
        specific_question="근처에 행사 있어?",
    )

    final_response, message = await _fetch_realtime_info_agentic(
        info_request,
        llm=_ScriptedToolCallingLLMProvider(original_area_name="교대", retry_area_name="강남역"),
        tool_provider=tool_provider,
    )

    assert final_response.status == "success"
    assert final_response.result.area_name == "강남역"
    assert "강남역" in message


@pytest.mark.asyncio
async def test_agentic_realtime_info_flag_off_uses_single_call() -> None:
    """settings.agentic_realtime_info가 기본값(off)이면 재시도 경로를 아예 안 탄다."""

    assert settings.agentic_realtime_info is False

    class _SingleCallToolProvider:
        def __init__(self) -> None:
            self.call_count = 0

        async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
            self.call_count += 1
            return InfoContextResponse(
                request_id="r1",
                status="no_data",
                result=RealtimeCityInfoResult(
                    status="no_data",
                    question_type="realtime_event",
                    requested_place_name=request.place_name,
                    resolved_place_name=request.place_name,
                ),
            )

    tool_provider = _SingleCallToolProvider()
    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에 지금 행사 있어?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert tool_provider.call_count == 1
    assert response.llm_output.info.question_type == "realtime_event"


@pytest.mark.asyncio
async def test_agentic_realtime_info_flag_on_end_to_end() -> None:
    """플래그를 켜면 run_agent_flow() 전체 경로에서도 자기 교정 재시도가 실제로
    발동해 최종 메시지가 에이전트 문장으로 대체된다."""

    original = settings.agentic_realtime_info
    settings.agentic_realtime_info = True
    try:
        no_data_response = InfoContextResponse(
            request_id="r1",
            status="no_data",
            result=RealtimeCityInfoResult(
                status="no_data",
                question_type="realtime_event",
                requested_place_name="경복궁",
                resolved_place_name="경복궁",
            ),
        )
        success_response = InfoContextResponse(
            request_id="r2",
            status="success",
            result=RealtimeCityInfoResult(
                status="success",
                question_type="realtime_event",
                requested_place_name="경복궁",
                resolved_place_name="강남역",
                area_name="강남역",
                fields={"강남 페스티벌": "9/1~9/10 · 강남역 광장"},
            ),
        )
        tool_provider = _ScriptedAreaToolProvider(
            {"경복궁": no_data_response, "강남역": success_response}
        )

        response = await run_agent_flow(
            AgentRequest(
                user_input="경복궁 근처에 지금 행사 있어?",
                session_id=None,
                device_location=DEVICE_LOCATION,
            ),
            llm=_ScriptedToolCallingLLMProvider(
                original_area_name="경복궁", retry_area_name="강남역"
            ),
            tool_provider=tool_provider,
            recommendation_provider=_CountingRecommendationProvider(),
            enrichment_provider=_CountingEnrichmentProvider(),
            store=InMemoryStateStore(),
        )
    finally:
        settings.agentic_realtime_info = original

    assert response.info_place_card is not None
    assert "강남역" in response.message


@pytest.mark.asyncio
async def test_info_walking_time_uses_current_gps_and_route_tool() -> None:
    """INFO location_info도 현재 GPS가 있으면 카카오 도보 경로 계약을 재사용한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 가는데 얼마나 걸려?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        travel_route_tool=TravelRouteTool(
            {
                TravelMode.WALKING: TravelRouteProviders(
                    primary=FakeWalkingRouteProvider(walking_speed_mps=1.2)
                )
            }
        ),
        **providers,
    )

    assert response.llm_output.intent is Intent.INFO
    assert response.llm_output.info is not None
    assert response.llm_output.info.question_type.value == "location_info"
    assert "현재 위치에서 경복궁까지 도보 약" in response.message
    assert "이동 거리는 약" in response.message


@pytest.mark.asyncio
async def test_info_general_info_question_type_shows_overview_raw() -> None:
    """general_info는 LLM 요약 없이 overview 원문을 그대로 보여준다(사용자 결정)."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 개요 알려줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.info.question_type == "general_info"
    assert providers["tool_provider"].info_call_count == 1
    assert "조선 왕조의 법궁" in response.message


@pytest.mark.asyncio
async def test_info_event_question_type_distinguishes_direct_and_nearby() -> None:
    """D-055: is_direct_match=False인 행사를 그 장소의 행사로 말하지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 행사 있어?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.info.question_type == "event"
    assert providers["tool_provider"].info_call_count == 1
    assert "경복궁에서 진행 중인 행사예요. 경복궁 별빛야행" in response.message
    assert "경복궁 근처에서 진행 중인 행사예요. 종로구 전통문화행사" in response.message


def _place(place_id: str, *, latitude: float = 37.5, longitude: float = 127.0) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        name=f"장소-{place_id}",
        category="cafe",
        location=Coordinates(latitude=latitude, longitude=longitude),
    )


def _item(place_id: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=f"장소-{place_id}",
        category="cafe",
        distance_km=0.3,
        remaining_minutes=60,
        environment_type="indoor",
        recommendation_reason="테스트용",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


class TestApplyConcentrationRerank:
    """_apply_concentration_rerank()를 run_agent_flow() 전체를 거치지 않고 직접
    단위 테스트한다 — B가 concentration_intent 필드를 아직 안 가지고 있어도
    (agent_conditions를 직접 만들어 주입하므로) 6-1 분기 로직 자체는 검증할 수 있다.
    """

    def _context(self, place_ids: list[str]) -> RecommendationContext:
        return RecommendationContext(
            places=ContextValue(status="success", data=[_place(pid) for pid in place_ids])
        )

    def _first_pass(self, place_ids: list[str]) -> RecommendationResponse:
        return RecommendationResponse(
            recommendations=[_item(pid) for pid in place_ids],
            unverified_recommendations=[],
            elapsed_ms=0,
        )

    @pytest.mark.asyncio
    async def test_ignore_intent_skips_enrichment_entirely(self) -> None:
        conditions = UserConditions(concentration_intent=ConcentrationIntent.IGNORE)
        enrichment_provider = _CountingEnrichmentProvider()

        result = await _apply_concentration_rerank(
            conditions,
            self._context(["a"]),
            self._first_pass(["a"]),
            recommendation_provider=_CountingRecommendationProvider(),
            enrichment_provider=enrichment_provider,
        )

        assert enrichment_provider.call_count == 0
        assert [item.place_id for item in result.recommendations] == ["a"]

    @pytest.mark.asyncio
    async def test_seek_with_rerank_capable_provider_reorders_and_caps_to_five(self) -> None:
        conditions = UserConditions(concentration_intent=ConcentrationIntent.SEEK)
        enrichment_provider = _CountingEnrichmentProvider()
        recommendation_provider = _CountingRecommendationProviderWithRerank()
        # 실제 1차 Scoring은 최대 5개까지만 넘기지만(_RECOMMENDATION_LIMIT), 이
        # 슬라이싱 자체가 5개 초과 입력에서도 정확히 잘리는지 확인하려고 6개를 준다.
        place_ids = ["a", "b", "c", "d", "e", "f"]

        result = await _apply_concentration_rerank(
            conditions,
            self._context(place_ids),
            self._first_pass(place_ids),
            recommendation_provider=recommendation_provider,
            enrichment_provider=enrichment_provider,
        )

        assert enrichment_provider.call_count == 1
        assert recommendation_provider.rerank_call_count == 1
        # FakeRecommendationProvider.rerank_with_concentration()은 1차 결과를 역순으로
        # 반환한다 — 실제로 2차 결과로 교체됐는지, 그리고 5개로 잘렸는지 확인한다.
        assert [item.place_id for item in result.recommendations] == list(reversed(place_ids))[:5]
        assert result.unverified_recommendations == []

    @pytest.mark.asyncio
    async def test_final_limit_defaults_to_five_but_schedule_can_request_ten(self) -> None:
        """SCHEDULE-04 회귀: final_limit을 안 넘기면 기존과 동일하게 5개로 잘리지만,
        SCHEDULE처럼 10을 명시하면 재순위 후에도 10개가 그대로 유지돼야 한다 —
        예전에는 _CONCENTRATION_FINAL_LIMIT=5가 무조건 적용돼 SCHEDULE의 10개가
        조용히 5개로 잘리는 버그가 있었다."""
        conditions = UserConditions(concentration_intent=ConcentrationIntent.SEEK)
        place_ids = [f"p{i}" for i in range(10)]

        default_result = await _apply_concentration_rerank(
            conditions,
            self._context(place_ids),
            self._first_pass(place_ids),
            recommendation_provider=_CountingRecommendationProviderWithRerank(),
            enrichment_provider=_CountingEnrichmentProvider(),
        )
        assert len(default_result.recommendations) == 5

        schedule_result = await _apply_concentration_rerank(
            conditions,
            self._context(place_ids),
            self._first_pass(place_ids),
            recommendation_provider=_CountingRecommendationProviderWithRerank(),
            enrichment_provider=_CountingEnrichmentProvider(),
            final_limit=10,
        )
        assert len(schedule_result.recommendations) == 10

    @pytest.mark.asyncio
    async def test_avoid_without_rerank_capable_provider_falls_back_to_first_pass(self) -> None:
        """D(rerank_with_concentration 없음)에서도 C 보강 조회는 크래시 없이
        호출되지만, 결과는 1차 그대로(개수 제한도 없이) 반환된다."""
        conditions = UserConditions(concentration_intent=ConcentrationIntent.AVOID)
        enrichment_provider = _CountingEnrichmentProvider()
        place_ids = ["a", "b", "c", "d"]

        result = await _apply_concentration_rerank(
            conditions,
            self._context(place_ids),
            self._first_pass(place_ids),
            recommendation_provider=_CountingRecommendationProvider(),
            enrichment_provider=enrichment_provider,
        )

        assert enrichment_provider.call_count == 1
        assert [item.place_id for item in result.recommendations] == place_ids

    @pytest.mark.asyncio
    async def test_seek_with_no_matching_places_skips_enrichment(self) -> None:
        """1차 결과의 place_id가 context.places에 하나도 없으면(재조인 실패) 보강
        조회 자체를 건너뛰고 1차 결과를 그대로 쓴다."""
        conditions = UserConditions(concentration_intent=ConcentrationIntent.SEEK)
        enrichment_provider = _CountingEnrichmentProvider()

        result = await _apply_concentration_rerank(
            conditions,
            self._context(["other"]),
            self._first_pass(["a"]),
            recommendation_provider=_CountingRecommendationProviderWithRerank(),
            enrichment_provider=enrichment_provider,
        )

        assert enrichment_provider.call_count == 0
        assert [item.place_id for item in result.recommendations] == ["a"]


@pytest.mark.asyncio
async def test_concentration_intent_persisted_by_b_triggers_rerank() -> None:
    """B-06으로 concentration_intent 필드가 추가된 뒤: LLM이 추출한 SEEK/AVOID가
    B의 State까지 정상적으로 저장되고, 그 결과 6-1 분기(_apply_concentration_rerank)가
    실제 run_agent_flow() 흐름에서 트리거된다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 핫한 곳 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.recommend.conditions.concentration_intent == "SEEK"
    assert response.state.user_conditions.concentration_intent == "SEEK"
    assert providers["enrichment_provider"].call_count == 1
    assert [execution.operation for execution in response.tool_executions] == [
        "context_fetch",
        "candidate_enrichment",
    ]
    # RECOMMEND 기본 limit=5. _FAKE_CANDIDATES가 SCHEDULE-07에서 6개로 늘어(재조정
    # 테스트가 3개 미만 가드에 걸리지 않도록) 5개로 잘려 enrichment 대상이 된다.
    assert response.tool_executions[1].candidate_status_counts == {"success": 5}


@pytest.mark.asyncio
async def test_concentration_ignore_skips_enrichment_call() -> None:
    """concentration_intent가 IGNORE/null이면 혼잡도 보강 조회 자체가 없다(회귀 확인)."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.recommend.conditions.concentration_intent in (None, "IGNORE")
    assert providers["enrichment_provider"].call_count == 0


@pytest.mark.asyncio
async def test_clarification_answer_keeps_conditions_from_previous_turn() -> None:
    """되묻기 답변은 새 요청이 아니므로 앞 턴 조건이 유지되어야 한다.

    1턴 "카페 추천해줘" → 위치가 없어 C가 needs_clarification.
    2턴 "경복궁 근처 카페 추천해줘" → 위치가 채워지고 place_tags도 살아 있어야 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(user_input="카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )
    session_id = first.state.session_id
    assert first.recommendations is None
    # 되묻기로 끝났으므로 B에 사유가 남는다.
    assert get_session_context(session_id, store=store).pending_clarification is not None

    second = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.state.user_conditions.search_center == "경복궁"
    assert "카페" in second.state.user_conditions.place_tags
    # 소비되어 지워진다.
    assert get_session_context(session_id, store=store).pending_clarification is None


@pytest.mark.asyncio
async def test_bare_place_after_location_clarification_becomes_modify_and_sets_center() -> None:
    """TP-67: 위치 되묻기 다음 '경복궁'은 INFO가 아닌 MODIFY로 이어져야 한다.

    아직 추천 결과가 없는 첫 요청에서도 B에 저장된 앞 턴 조건을 MODIFY 추출기에
    전달해, search_center만 추가한 뒤 추천을 이어간다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(user_input="근처 갈곳 추천해줘", session_id=None, device_location=None),
        store=store,
        **providers,
    )
    session_id = first.state.session_id
    assert first.llm_output.intent == "RECOMMEND"
    assert get_session_context(session_id, store=store).pending_clarification == "location_required"

    second = await run_agent_flow(
        AgentRequest(user_input="경복궁", session_id=session_id, device_location=None),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "MODIFY"
    assert second.llm_output.modify.changed_fields == ["search_center"]
    assert second.state.user_conditions.search_center == "경복궁"
    assert second.recommendations is not None
    assert get_session_context(session_id, store=store).pending_clarification is None


@pytest.mark.asyncio
async def test_new_recommendation_without_location_keeps_previous_search_center() -> None:
    """TP-67: 목적지 뒤 새 RECOMMEND가 와도 목적지를 다시 묻지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="박물관 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "RECOMMEND"
    assert second.state.user_conditions.search_center == "경복궁"
    assert "박물관" in second.state.user_conditions.place_tags
    assert second.recommendations is not None


@pytest.mark.asyncio
async def test_schedule_clarification_answer_stays_schedule() -> None:
    """D-059: SCHEDULE 되묻기에 지명만 답하면 MODIFY가 아니라 SCHEDULE을 유지해야 한다.

    1턴 "일정 짜줘"(위치 없음) → C가 needs_clarification(location_required).
    2턴 "광화문 근처로" → 되묻기 답변인데도 MODIFY로 오분류되면(수정 전 버그) 바꿀
    이전 추천 결과가 없어 흐름이 깨진다. SCHEDULE로 이어지고 pending_clarification도
    소비되어 사라져야 한다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(user_input="일정 짜줘", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )
    session_id = first.state.session_id
    assert first.llm_output.intent == "SCHEDULE"
    assert first.recommendations is None
    session_context = get_session_context(session_id, store=store)
    assert session_context.pending_clarification is not None
    assert session_context.last_intent == "SCHEDULE"

    second = await run_agent_flow(
        AgentRequest(
            user_input="광화문 근처로",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.llm_output.intent == "SCHEDULE"
    assert second.state.user_conditions.search_center == "광화문"
    # 소비되어 지워진다(수정 전에는 SCHEDULE이 되묻기 소비 화이트리스트에 없어 안 지워졌다).
    assert get_session_context(session_id, store=store).pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_answer_keeps_weather_and_environment() -> None:
    """되묻기 답변에 위치만 담겨도 앞 턴의 비 회피·실내 조건이 살아 있어야 한다.

    1턴 "비 오는데 카페 추천해줘"(위치 없음) → 되묻기.
    2턴 "경복궁 근처에서" → search_center만 채워지고 weather/environment는 유지.
    이 턴은 RECOMMEND로 분류되지만 pending_clarification 덕에 soft reset을 건너뛴다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    first = await run_agent_flow(
        AgentRequest(
            user_input="비 오는데 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    session_id = first.state.session_id
    assert first.state.user_conditions.weather == "rain"
    assert first.state.user_conditions.environment == "indoor"
    assert get_session_context(session_id, store=store).pending_clarification is not None

    second = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert second.state.user_conditions.search_center == "경복궁"
    assert second.state.user_conditions.weather == "rain"
    assert second.state.user_conditions.weather_intent == "AVOID"
    assert second.state.user_conditions.environment == "indoor"


@pytest.mark.asyncio
async def test_successful_recommendation_leaves_no_pending_clarification() -> None:
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.recommendations is not None
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_추천_응답에_C_실행_정보가_실린다() -> None:
    """AgentResponse.tool_execution은 감사 표시 전용이지만, 비어 있으면 /dev-chat의
    C Tool 탭이 다시 추측만 하게 된다. 실제 값이 실리는지 확인한다."""

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=InMemoryStateStore(),
        **_providers(),
    )

    assert response.tool_execution is not None
    assert response.tool_execution.status == "success"
    assert response.tool_execution.latency_ms is not None
    assert [execution.operation for execution in response.tool_executions] == ["context_fetch"]
    assert [item.key for item in response.tool_execution.context_items] == [
        "location",
        "weather",
        "places",
        "holidays",
    ]


@pytest.mark.asyncio
async def test_modify_change_condition_calls_context_again_with_merged_conditions() -> None:
    """MODIFY로 조건이 바뀌면 C를 다시 호출하고, 그 요청에 병합된 조건이 실려야 한다.

    재호출하지 않으면 조건만 바뀌고 후보는 1턴 그대로 남는다. 횟수만 세면 "부르긴
    했는데 옛 조건으로 불렀다"를 놓치므로 요청 내용까지 확인한다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = providers["tool_provider"]

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    assert tool_provider.call_count == 1
    assert tool_provider.last_request.conditions.budget is None

    await run_agent_flow(
        AgentRequest(
            user_input="무료인 곳으로",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert tool_provider.call_count == 2
    # 2턴 요청에는 병합 결과가 실린다 — 바뀐 budget과 유지된 search_center가 함께.
    assert tool_provider.last_request.conditions.budget == "free"
    assert tool_provider.last_request.conditions.search_center == "경복궁"


@pytest.mark.asyncio
async def test_modify_reject_all_calls_context_again() -> None:
    """REJECT_ALL은 조건이 그대로라도 C를 다시 호출한다.

    조건이 같다고 이전 Context를 재사용하면 제외 목록이 반영되지 않아 같은 장소가
    다시 노출된다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = providers["tool_provider"]

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert tool_provider.call_count == 2
    assert tool_provider.last_request.conditions.search_center == "경복궁"


class _LocationAmbiguousToolProvider:
    """C 대역 — location_ambiguous를 후보 이름과 함께 돌려준다.

    resolve_location.py가 실제로 찾아낸 이름을 candidate_names로 흘려보내는
    것과 같은 모양을 흉내 낸다(docs/design/clarification-options.md 7절 확장).
    """

    def __init__(self, candidates: list[str]) -> None:
        self._candidates = candidates
        self.call_count = 0

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        self.call_count += 1
        return AgentContextResponse(
            request_id=request.request_id,
            intent="RECOMMEND",
            status="needs_clarification",
            clarification=Clarification(code="location_ambiguous", candidates=self._candidates),
            metadata=ResponseMetadata(),
        )


@pytest.mark.asyncio
async def test_location_ambiguous_with_candidates_shows_them_as_buttons() -> None:
    """docs/design/clarification-options.md 7절 확장: 동명이인 후보 이름을 Tool이
    실제로 찾아내면(예: "종각" → "종각역"/"종각 지하도상가") 텍스트 재질문 대신
    후보 이름 버튼으로 보여줘야 한다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _LocationAmbiguousToolProvider(["종각역", "종각 지하도상가"])

    response = await run_agent_flow(
        AgentRequest(
            user_input="종각 근처 카페 추천해",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"종각역", "종각 지하도상가"}
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "location_ambiguous"


@pytest.mark.asyncio
async def test_location_ambiguous_without_candidates_falls_back_to_quick_picks() -> None:
    """실사용 피드백(2026-08-13): resolve_location.py가 식당·상점을 이미 걸러내고
    남는 후보가 하나도 없으면(전부 식당·상점뿐이었던 경우), 빈 후보 대신 A2와
    같은 종로구 대표 스팟 고정 버튼을 보여줘야 한다 — "그냥 지하철역으로만 가자"."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _LocationAmbiguousToolProvider([])

    response = await run_agent_flow(
        AgentRequest(
            user_input="종로 근처 식당 추천",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"경복궁", "인사동", "광화문", "북촌"}
    labels = {option.label for option in clarification.options}
    assert labels == {"경복궁 근처", "인사동 근처", "광화문 근처", "북촌 근처"}


@pytest.mark.asyncio
async def test_location_ambiguous_without_candidates_matches_mentioned_district() -> None:
    """TP-160: "용산 카페 추천"처럼 지원 구 이름을 직접 말했는데 후보를 못 찾으면,
    무관한 종로구 스팟이 아니라 그 구(용산구)의 대표 스팟을 보여줘야 한다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderForcingSearchCenter("용산")
    providers["tool_provider"] = _LocationAmbiguousToolProvider([])

    response = await run_agent_flow(
        AgentRequest(user_input="용산 카페 추천해줘", session_id=None, device_location=None),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"이태원역", "용산역"}


@pytest.mark.asyncio
async def test_location_required_uses_gps_nearest_district_when_no_location_mentioned() -> None:
    """TP-160: 위치를 아예 언급 안 했어도 GPS가 종로구 밖(용산구)이면, 무관한
    종로구 스팟이 아니라 GPS로 짐작한 구의 대표 스팟을 보여줘야 한다."""
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="카페 추천해줘", session_id=None, device_location="37.5299,126.9648"
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"이태원역", "용산역"}


@pytest.mark.asyncio
async def test_clarification_choice_location_required_district_landmark_resolves() -> None:
    """TP-160: GPS로 짐작한 구(종로구가 아닌)의 대표 스팟 버튼도 정상적으로
    클릭 해소돼야 한다 — 클릭 검증이 종로구 4곳으로만 좁혀져 있으면 안 된다."""
    store = InMemoryStateStore()
    providers = _providers()

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="카페 추천해줘", session_id=None, device_location="37.5299,126.9648"
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="이태원역 근처",
            session_id=ambiguous.state.session_id,
            device_location="37.5299,126.9648",
            clarification_choice="이태원역",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.state.user_conditions.search_center == "이태원역"
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_location_ambiguous_candidate_resolves_search_center() -> None:
    """후보 버튼 클릭 시 classify_intent() 재호출 없이 그 이름으로 바로 검색해야
    한다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _LocationAmbiguousToolProvider(["종각역", "종각 지하도상가"])

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="종각 근처 카페 추천해",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    # 해소 턴은 위치가 확정됐다고 보고 정상 응답을 돌려줘야 하므로, 이 턴만 보통의
    # FakeToolProvider로 바꿔 끼운다(location_ambiguous를 계속 강제하면 안 풀린다).
    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="종각역",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="종각역",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.state.user_conditions.search_center == "종각역"
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_with_district_prefix_keeps_full_name() -> None:
    """TP-182 (2026-09-03 임기민 실측): "종로구 익선동"처럼 후보 이름에 상위
    행정구역이 붙어 있어도 버튼 클릭은 그 이름을 그대로 써야 한다.

    실사용에서는 GPS가 강서구인 채로 "익선동"을 물으면 지오코딩 후보로
    [종로구 익선동, 창원시 진해구 익선동]이 뜨는데, 버튼을 눌러도 결정적
    해소를 안 타고 발화("종로구 익선동")가 다시 classify_intent()로 흘러가면
    실 LLM이 "익선동"으로 줄여버려 같은 되묻기가 반복됐다. 여기서는 그 재해석
    손실을 흉내 내려고 두 번째 턴 LLM을 일부러 "익선동"만 돌려주게 강제한다 —
    결정적 해소가 제대로 타면 이 가짜 LLM은 아예 호출되지 않아야 하므로,
    search_center가 줄어들지 않고 "종로구 익선동" 그대로 남는 것으로 검증한다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _LocationAmbiguousToolProvider(
        ["종로구 익선동", "창원시 진해구 익선동"]
    )
    gangseo_gps = "37.5509,126.8495"

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="익선동에서 갈 만한 곳 추천해줘",
            session_id=None,
            device_location=gangseo_gps,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    option_ids = {option.id for option in ambiguous.llm_output.clarification.options}
    assert option_ids == {"종로구 익선동", "창원시 진해구 익선동"}

    providers["llm"] = _LLMProviderForcingSearchCenter("익선동")
    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="종로구 익선동",
            session_id=ambiguous.state.session_id,
            device_location=gangseo_gps,
            clarification_choice="종로구 익선동",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "RECOMMEND"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.state.user_conditions.search_center == "종로구 익선동"
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


class _InfoPlaceAmbiguousToolProvider:
    """C 대역 — INFO의 place_ambiguous를 후보 이름과 함께 돌려준다.

    agent_context/service.py의 실제 필터링 로직(question_type별 가용성)은
    tests/agent_context/test_service.py에서 검증한다. 여기서는 agent_runtime.py가
    그 결과를 버튼으로 바꾸고, 상태에 원래 질문을 저장하는지만 본다.
    """

    def __init__(self, candidates: list[str]) -> None:
        self._candidates = candidates
        self.info_call_count = 0

    async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
        self.info_call_count += 1
        return InfoContextResponse(
            request_id=request.request_id,
            status="needs_clarification",
            clarification=Clarification(code="place_ambiguous", candidates=self._candidates),
        )


@pytest.mark.asyncio
async def test_info_place_ambiguous_shows_candidates_as_buttons() -> None:
    """INFO도 RECOMMEND의 location_ambiguous와 같은 방식으로 후보를 버튼으로
    보여줘야 한다 — 예전엔 candidates가 항상 버려졌다(실측, 2026-08-27)."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _InfoPlaceAmbiguousToolProvider(
        ["창덕궁 제1주차장", "창덕궁 제2주차장"]
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 주차장 정보 알려줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "INFO"
    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"창덕궁 제1주차장", "창덕궁 제2주차장"}
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "place_ambiguous"
    assert context.pending_info_context is not None
    assert context.pending_info_context.question_type == "parking"


@pytest.mark.asyncio
async def test_clarification_choice_place_ambiguous_candidate_resolves_info_request() -> None:
    """되묻기 버튼 클릭 시 재분류 없이 저장해둔 question_type을 그대로 이어받는다
    — 클릭 전엔 이 상태가 없어 장소명만으로 처음부터 재분류됐다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _InfoPlaceAmbiguousToolProvider(
        ["창덕궁 제1주차장", "창덕궁 제2주차장"]
    )

    ambiguous = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 주차장 정보 알려줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert ambiguous.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="창덕궁 제1주차장",
            session_id=ambiguous.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="창덕궁 제1주차장",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.intent == "INFO"
    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.llm_output.info is not None
    assert resolved.llm_output.info.place_name == "창덕궁 제1주차장"
    # question_type이 재분류로 사라지지 않고 그대로 이어받아졌다.
    assert resolved.llm_output.info.question_type == "parking"
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None
    assert context.pending_info_context is None


class _FixedStatusToolProvider:
    """지정한 status만 돌려주는 C 대역. 상태 분기만 보기 위해 내용은 최소로 채운다."""

    def __init__(self, status: str) -> None:
        self._status = status
        self.call_count = 0

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        self.call_count += 1
        payload: dict = {
            "request_id": request.request_id,
            "intent": "RECOMMEND",
            "status": self._status,
            "metadata": ResponseMetadata(),
        }
        if self._status in {"success", "partial", "no_data"}:
            payload["context"] = RecommendationContext(
                location=ContextValue(
                    status="success",
                    data=ResolvedLocation(
                        requested_query="경복궁",
                        resolved_name="경복궁",
                        source="query",
                        location=Coordinates(latitude=37.5788, longitude=126.9770),
                    ),
                ),
                places=ContextValue(status=self._status, data=[]),
            )
        elif self._status == "needs_clarification":
            payload["clarification"] = Clarification(
                code="location_required", missing_fields=["current_location"]
            )
        else:
            payload["error"] = ContextError(
                code=self._status, message="테스트용 오류", retryable=False
            )
        return AgentContextResponse(**payload)


@pytest.mark.parametrize(
    ("tool_status", "reaches_recommendation", "expected_tool_calls"),
    [
        ("success", True, 1),
        # partial은 "가능한 데이터로 계속"이라 D까지 간다(계약 §5.4).
        ("partial", True, 1),
        # 아래 넷은 _TOOL_TERMINAL_STATUSES — 안내만 하고 끝난다.
        # no_data(원인 구분 신호 없음 → no_data_empty)는 넘길 후보가 없어 D를 부르지
        # 않지만, 되묻기 전에 A-1 자기 교정으로 반경을 넓혀 한 번 더 스스로
        # 조회한다 — 그래서 다른 종료 status와 달리 호출이 2번이다.
        ("no_data", False, 2),
        ("needs_clarification", False, 1),
        ("unsupported", False, 1),
        ("unavailable", False, 1),
    ],
)
@pytest.mark.asyncio
async def test_tool_status_decides_whether_recommendation_runs(
    tool_status: str, reaches_recommendation: bool, expected_tool_calls: int
) -> None:
    """C의 6개 status가 D 호출 여부를 어떻게 가르는지 한곳에 고정한다.

    개별 케이스는 다른 테스트에도 흩어져 있지만, 경계를 한 표로 모아두면 "partial은
    어떻게 되지?"를 한눈에 답할 수 있고 정책이 바뀔 때 이 표만 고치면 된다.
    """
    tool_provider = _FixedStatusToolProvider(tool_status)
    recommendation_provider = _CountingRecommendationProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert tool_provider.call_count == expected_tool_calls
    assert recommendation_provider.call_count == (1 if reaches_recommendation else 0)
    assert (response.recommendations is not None) is reaches_recommendation


class _UnsupportedRegionToolProvider:
    """서비스 지역 밖 판정만 재현하는 C 대역(D-085 회귀 확인용)."""

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        return AgentContextResponse(
            request_id=request.request_id,
            intent="RECOMMEND",
            status="unsupported",
            error=ContextError(
                code="unsupported_region", message="테스트용 오류", retryable=False
            ),
            metadata=ResponseMetadata(),
        )


@pytest.mark.asyncio
async def test_unsupported_region_message_is_short_and_footnote_names_the_area() -> None:
    """구 목록은 message가 아니라 message_footnote에 실린다(D-085).

    본문에 목록을 그대로 이어붙이던 옛 방식은 구가 늘 때마다 문장이 길어졌다 —
    지금은 본문을 짧게 고정하고, 목록은 화면이 작고 옅은 글씨로 따로 보여줄
    message_footnote 쪽으로만 늘어나게 한다.
    """
    response = await run_agent_flow(
        AgentRequest(
            user_input="홍대입구역 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_UnsupportedRegionToolProvider(),
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert response.message == "이 위치는 지금 서비스 지역이 아니에요. 다른 위치를 말씀해 주세요."
    assert response.message_footnote is not None
    assert "종로구" in response.message_footnote


@pytest.mark.asyncio
async def test_location_required_clarification_reaches_user_message() -> None:
    """C의 clarification.code가 A를 거쳐 되묻기 문장까지 이어지는지 확인한다."""
    tool_provider = _FixedStatusToolProvider("needs_clarification")

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert "어디 근처에서" in response.message


@pytest.mark.asyncio
async def test_no_data_asks_to_adjust_conditions_without_calling_recommendation() -> None:
    """후보가 없으면 D를 부르지 않고 조건을 바꿔볼지 되묻는다.

    빈 후보로 Scoring을 돌려도 결과가 같으므로 호출하지 않는다. TourAPI
    provider_metadata가 없으면(원인 구분 신호 자체가 없음) 원인1+3(no_data_empty)
    되묻기로 처리한다 — 조사 결과(2026-08-13) TourAPI가 애초에 0건일 때와 반경이
    좁아 0건일 때는 신호가 같아 구분할 수 없다.
    """
    tool_provider = _FixedStatusToolProvider("no_data")
    recommendation_provider = _CountingRecommendationProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert recommendation_provider.call_count == 0
    assert response.recommendations is None
    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert response.llm_output.clarification is not None
    assert response.llm_output.clarification.code == "no_data_empty"
    assert "서비스 지역 안에서 찾지 못했어요" in response.message
    option_ids = {option.id for option in response.llm_output.clarification.options}
    assert option_ids == {"widen_radius", "widen_category"}
    # 일시적 장애 문구로 새면 안 된다.
    assert "일시적으로" not in response.message


@pytest.mark.asyncio
async def test_no_data_marks_pending_clarification_so_next_turn_keeps_conditions() -> None:
    """ "범위를 넓혀볼까요?"에 대한 답변은 새 요청이 아니라 이번 요청의 연속이다.

    표시해두지 않으면 다음 턴이 RECOMMEND로 분류되며 soft reset이 걸려 앞 턴 조건이
    사라진다(D-039와 같은 이유).
    """
    store = InMemoryStateStore()
    tool_provider = _FixedStatusToolProvider("no_data")

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "no_data_empty"


@pytest.mark.asyncio
async def test_bare_place_after_no_data_restarts_search_around_that_place() -> None:
    """후보 없음 뒤 단순 지명은 INFO가 아니라 해당 장소 주변 재추천 요청이다."""
    store = InMemoryStateStore()

    first = await run_agent_flow(
        AgentRequest(user_input="카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_FixedStatusToolProvider("no_data"),
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )
    session_id = first.state.session_id
    assert get_session_context(session_id, store=store).pending_clarification == "no_data_empty"

    second = await run_agent_flow(
        AgentRequest(user_input="광화문", session_id=session_id, device_location=DEVICE_LOCATION),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=FakeToolProvider(),
        recommendation_provider=_CountingRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    assert second.llm_output.intent == "RECOMMEND"
    assert second.state.user_conditions.search_center == "광화문"
    assert "카페" in second.state.user_conditions.place_tags
    assert second.recommendations is not None


def test_terminal_status_sets_match_between_runtime_and_composer() -> None:
    """두 모듈이 같은 집합을 각자 들고 있다 — 어긋나면 메시지가 엉뚱한 분기로 샌다."""
    from app.services.runtime.agent_runtime import _TOOL_TERMINAL_STATUSES as runtime_set
    from app.services.runtime.response_composer import (
        _TOOL_TERMINAL_STATUSES as composer_set,
    )

    assert runtime_set == composer_set


# C가 내려주는 operating_schedule 직렬화 형태. 24시간 열려 있어 Scoring이 폐점으로
# 걸러내지 않는 값으로 둔다 — 여기서 보려는 건 운영시간 유무에 따른 분류다.
#
# **마감을 "23:59"가 아니라 하루의 끝으로 둔다.** Scoring의 폐점 판정은
# `open_time <= now < close_time`이라(scoring.py `_remaining_minutes`), "23:59"로
# 두면 23:59:00부터 자정까지 이 픽스처가 폐점으로 판정된다. 그 1분에 CI가 걸리면
# "영업 중"으로 깔아둔 후보가 전부 걸러져 이 파일 24건이 한꺼번에 깨진다 —
# 2026-09-05 14:59 UTC(23:59 KST) 실행에서 실제로 그렇게 됐다.
#
# time.max(23:59:59.999999)는 이 저장소가 이미 "하루의 끝"으로 쓰는 값이다
# (recommendation_pipeline.py의 "%H:%M" 표기 주석 참고).
_END_OF_DAY = "23:59:59.999999"

_OPEN_ALL_DAY_SCHEDULE = {
    "availability": "scheduled",
    "rules": [
        {
            "months": None,
            "weekdays": None,
            "time_ranges": [
                {"open_time": "00:00", "close_time": _END_OF_DAY, "crosses_midnight": False}
            ],
        }
    ],
    "time_ranges": [{"open_time": "00:00", "close_time": _END_OF_DAY, "crosses_midnight": False}],
    "closure_rules": [],
    "parse_status": "parsed",
    "assumption_reason": None,
    "warnings": [],
}


def _context_place(place_id: str, *, with_schedule: bool) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        name=f"장소-{place_id}",
        category="cafe",
        location=Coordinates(latitude=37.5790, longitude=126.9772),
        operating_hours_raw="09:00~22:00" if with_schedule else None,
        operating_schedule=_OPEN_ALL_DAY_SCHEDULE if with_schedule else None,
    )


class _PartialPlacesToolProvider:
    """운영정보가 일부만 채워진 partial Context를 돌려주는 C 대역."""

    def __init__(self, places: list[PlaceCandidate]) -> None:
        self._places = places

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        return AgentContextResponse(
            request_id=request.request_id,
            intent="RECOMMEND",
            status="partial",
            context=RecommendationContext(
                location=ContextValue(
                    status="success",
                    data=ResolvedLocation(
                        requested_query="경복궁",
                        resolved_name="경복궁",
                        source="query",
                        location=Coordinates(latitude=37.5788, longitude=126.9770),
                    ),
                ),
                places=ContextValue(status="partial", data=self._places),
            ),
            metadata=ResponseMetadata(),
        )


def test_open_all_day_fixture_stays_open_through_the_last_minute() -> None:
    """이 픽스처는 하루의 어느 순간에도 영업 중이어야 한다.

    **그러지 않으면 이 파일이 하루에 1분씩 깨진다.** 폐점 판정은
    `open_time <= now < close_time`이라(scoring.py `_remaining_minutes`), 마감을
    "23:59"로 두면 23:59:00부터 자정까지 "영업 중"으로 깔아둔 후보가 전부
    걸러진다. 후보가 없으니 되묻기로 끝나거나 경로 조회가 0건이 되어, 운영시간과
    무관한 테스트까지 24건이 한꺼번에 무너진다 — 2026-09-05 14:59 UTC(23:59 KST)
    CI 실행에서 실제로 그렇게 됐다.

    시각을 고정하는 대신 픽스처 자체를 검사한다. 이 파일의 테스트들은 실제 시각으로
    돌기 때문에, 고정해 봐야 여기 한 곳만 안전해지고 나머지 24건은 그대로다.
    """
    from datetime import time
    from zoneinfo import ZoneInfo

    from app.domain.models import OperatingHours
    from app.domain.scoring import _remaining_minutes

    kst = ZoneInfo("Asia/Seoul")
    hours = OperatingHours(
        open_time=time.fromisoformat(_OPEN_ALL_DAY_SCHEDULE["time_ranges"][0]["open_time"]),
        close_time=time.fromisoformat(_OPEN_ALL_DAY_SCHEDULE["time_ranges"][0]["close_time"]),
    )

    for hour, minute, second in ((0, 0, 0), (12, 0, 0), (23, 58, 30), (23, 59, 0), (23, 59, 59)):
        now = datetime(2026, 9, 5, hour, minute, second, tzinfo=kst)
        assert _remaining_minutes(now, hours) is not None, f"{hour:02d}:{minute:02d}:{second:02d}"


_CLOSED_ALL_WEEK_SCHEDULE = {
    "availability": "all_day",
    "rules": [],
    "closure_rules": [
        {
            "weekdays": [
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ]
        }
    ],
}


# _RefillPlacesToolProvider가 한 번에 돌려주는 후보 수.
_REFILL_PAGE_SIZE = 10


@pytest.fixture
def refill_page_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    """보충 조회 대역의 페이지 크기와 후보 상한을 맞춘다.

    `_RefillPlacesToolProvider`는 10곳 단위로 후보를 돌려주고 open_indexes도 그
    경계에 맞춰 잡혀 있다. A의 소진 판정이 "반환 수 < recommendation_candidate_limit"
    이라, 설정값이 페이지 크기보다 크면 첫 조회가 곧바로 소진으로 읽혀 보충이 아예
    돌지 않는다. 이 테스트들이 보려는 것은 보충 메커니즘이지 운영 기본값이 아니므로
    여기서 둘을 맞춘다 — 기본값을 10에서 30으로 올렸을 때 실제로 이렇게 깨졌다.
    """
    monkeypatch.setattr(settings, "recommendation_candidate_limit", _REFILL_PAGE_SIZE)
    return _REFILL_PAGE_SIZE


class _RefillPlacesToolProvider(FakeToolProvider):
    """제외 ID 다음의 후보를 페이지 단위로 반환하는 C 보충 조회 대역.

    실제 C처럼 excluded_place_ids만큼 뒤 후보를 채워 주고, 남은 후보가
    page_size보다 적으면 그만큼만 반환한다 — A가 "limit보다 적게 왔다"를 풀
    소진 신호로 쓰기 때문에 그 모양을 그대로 흉내 낸다.
    """

    def __init__(
        self,
        *,
        total: int = 25,
        page_size: int = _REFILL_PAGE_SIZE,
        open_indexes: set[int] | None = None,
    ) -> None:
        self.requests: list[AgentContextRequest] = []
        self._page_size = page_size
        is_open = (
            (lambda index: index in open_indexes)
            if open_indexes is not None
            else (lambda index: index in {0, 10} or index >= 20)
        )
        self._places = [
            PlaceCandidate(
                place_id=f"refill-{index}",
                name=f"보충 장소 {index}",
                category="cafe",
                location=Coordinates(
                    latitude=37.5790 + index * 0.0001,
                    longitude=126.9772 + index * 0.0001,
                ),
                operating_schedule=(
                    _OPEN_ALL_DAY_SCHEDULE if is_open(index) else _CLOSED_ALL_WEEK_SCHEDULE
                ),
            )
            for index in range(total)
        ]

    def _build_context(
        self,
        places: list[PlaceCandidate],
        call_index: int,
    ) -> RecommendationContext:
        return RecommendationContext(
            location=ContextValue(
                status="success",
                data=ResolvedLocation(
                    requested_query="경복궁",
                    resolved_name="경복궁",
                    source="query",
                    location=Coordinates(latitude=37.5788, longitude=126.9770),
                ),
            ),
            places=ContextValue(status="success", data=places),
        )

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        self.requests.append(request)
        call_index = len(self.requests) - 1
        excluded = set(request.excluded_place_ids)
        places = [place for place in self._places if place.place_id not in excluded]
        places = places[: self._page_size]
        return AgentContextResponse(
            request_id=request.request_id,
            intent="RECOMMEND",
            status="success",
            context=self._build_context(places, call_index),
            metadata=ResponseMetadata(),
        )


class _RecordingTravelRouteTool:
    def __init__(self) -> None:
        self.queries = []
        self._delegate = TravelRouteTool(
            {
                TravelMode.WALKING: TravelRouteProviders(
                    primary=FakeWalkingRouteProvider(walking_speed_mps=1.2)
                )
            }
        )

    async def execute(self, query):
        self.queries.append(query)
        return await self._delegate.execute(query)


class _UnavailableTravelRouteTool:
    async def execute(self, query):
        return TravelRouteToolResult(status=ToolStatus.UNAVAILABLE, routes=())


def _all_modes_travel_route_tool() -> TravelRouteTool:
    """도보·자동차·대중교통 세 provider를 모두 등록한 실측 도구.

    COMPARE의 _fetch_compare_travel_routes()가 세 수단을 병렬로 조회하므로,
    단위 테스트도 세 provider가 모두 필요하다.
    """
    return TravelRouteTool(
        {
            TravelMode.WALKING: TravelRouteProviders(
                primary=FakeWalkingRouteProvider(walking_speed_mps=1.2)
            ),
            TravelMode.DRIVING: TravelRouteProviders(
                primary=FakeDrivingRouteProvider(driving_speed_mps=8.0)
            ),
            TravelMode.TRANSIT: TravelRouteProviders(
                primary=FakeTransitRouteProvider(transit_speed_mps=5.0)
            ),
        }
    )


class TestFetchCompareTravelRoutes:
    """COMPARE의 TRAVEL_TIME 실측 연결(2026-08-21, TP-105/106) 전용 단위 테스트."""

    def _comparison(self, *, criteria=CompareCriteria.TRAVEL_TIME) -> ComparisonResult:
        return ComparisonResult(
            criteria=criteria,
            items=[
                ComparisonItem(
                    place_id="p1",
                    place_name="경복궁",
                    rank=1,
                    latitude=37.5796,
                    longitude=126.9770,
                ),
                ComparisonItem(
                    place_id="p2",
                    place_name="창덕궁",
                    rank=2,
                    latitude=37.5824,
                    longitude=126.9910,
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_returns_unchanged_when_criteria_is_not_travel_time(self) -> None:
        comparison = self._comparison(criteria=CompareCriteria.TIME)
        tool = _RecordingTravelRouteTool()

        result = await _fetch_compare_travel_routes(
            tool, origin_location="37.5760,126.9769", comparison=comparison
        )

        assert result is comparison
        assert tool.queries == []

    @pytest.mark.asyncio
    async def test_fetches_all_three_modes_and_fills_fields(self) -> None:
        comparison = self._comparison()
        tool = _all_modes_travel_route_tool()

        result = await _fetch_compare_travel_routes(
            tool, origin_location="37.5760,126.9769", comparison=comparison
        )

        for item in result.items:
            assert item.travel_walking_minutes is not None
            assert item.travel_driving_minutes is not None
            assert item.travel_transit_minutes is not None
            assert item.travel_distance_km is not None
            # 도보가 가장 느린 수단이라 소요시간이 가장 길어야 한다.
            assert item.travel_walking_minutes > item.travel_driving_minutes

    @pytest.mark.asyncio
    async def test_missing_provider_leaves_that_mode_none_others_filled(self) -> None:
        """대중교통 provider가 미설정이어도(TP-106 이전 상태 재현) 나머지 수단은 채워진다."""
        comparison = self._comparison()
        tool = TravelRouteTool(
            {
                TravelMode.WALKING: TravelRouteProviders(
                    primary=FakeWalkingRouteProvider(walking_speed_mps=1.2)
                ),
                TravelMode.DRIVING: TravelRouteProviders(
                    primary=FakeDrivingRouteProvider(driving_speed_mps=8.0)
                ),
            }
        )

        result = await _fetch_compare_travel_routes(
            tool, origin_location="37.5760,126.9769", comparison=comparison
        )

        for item in result.items:
            assert item.travel_walking_minutes is not None
            assert item.travel_driving_minutes is not None
            assert item.travel_transit_minutes is None

    @pytest.mark.asyncio
    async def test_items_without_coordinates_are_left_untouched(self) -> None:
        comparison = ComparisonResult(
            criteria=CompareCriteria.TRAVEL_TIME,
            items=[ComparisonItem(place_id="p1", place_name="좌표 없는 곳", rank=1)],
        )
        tool = _all_modes_travel_route_tool()

        result = await _fetch_compare_travel_routes(
            tool, origin_location="37.5760,126.9769", comparison=comparison
        )

        assert result is comparison

    @pytest.mark.asyncio
    async def test_no_origin_location_returns_unchanged(self) -> None:
        comparison = self._comparison()
        tool = _all_modes_travel_route_tool()

        result = await _fetch_compare_travel_routes(
            tool, origin_location=None, comparison=comparison
        )

        assert result is comparison


class _RecordingWalkingRoutesRecommendationProvider(RealRecommendationProvider):
    def __init__(self) -> None:
        self.travel_routes: tuple[TravelRoute, ...] = ()

    async def score_prepared(
        self,
        conditions,
        prepared,
        *,
        travel_routes=(),
        limit=5,
        saved_taste_query=None,
    ):
        self.travel_routes = travel_routes
        return await super().score_prepared(
            conditions,
            prepared,
            travel_routes=travel_routes,
            limit=limit,
            saved_taste_query=saved_taste_query,
        )


@pytest.mark.asyncio
async def test_staged_recommendation_refills_candidates_up_to_target(
    refill_page_limit: int,
) -> None:
    store = InMemoryStateStore()
    tool_provider = _RefillPlacesToolProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    assert response.recommendations is not None
    shown = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    assert len(shown) == 5
    assert len(tool_provider.requests) == 3
    assert len(tool_provider.requests[0].excluded_place_ids) == 0
    assert len(tool_provider.requests[1].excluded_place_ids) == 10
    assert len(tool_provider.requests[2].excluded_place_ids) == 20
    assert any(item.place_id.startswith("refill-2") for item in shown)

    session = get_session_context(response.state.session_id, store=store)
    # TP-82: 화면에 보여준 5개뿐 아니라, 리필 도중 폐점이라 걸러진 후보(3라운드에
    # 걸쳐 refill-1~9, 11~19 총 18개)도 B에 기록되어 다음 회차 제외 목록에
    # 들어간다 — 안 그러면 "다른 곳 보여줘"를 반복할 때마다 같은 폐점 후보를
    # 다시 리필해 뽑는 낭비가 반복된다.
    assert set(session.excluded_place_ids) == {item.place_id for item in shown} | set(
        response.recommendations.excluded_closed_place_ids
    )
    assert response.recommendations.excluded_closed_place_ids != []


@pytest.mark.asyncio
async def test_saved_place_closed_at_visit_time_is_reported_separately() -> None:
    """영업시간으로 빠진 보관함 장소는 absent가 아니라 closed로 간다. (TP-236)

    1턴에는 열려 있어 추천에 나가고 보관함에 담긴다. 그 사이 문을 닫은 것으로
    바꾼 뒤 "이 장소들로 일정 짜기"를 부르면, D의 폐점 하드 필터가 걸러내
    후보에 못 들어온다. 그때 화면은 "시간대를 바꾸면 넣어드릴 수 있어요"라고
    확정적으로 말할 수 있어야 하므로 사유가 갈려 있어야 한다.

    `place_details_repository`를 주지 않는다 — 주면 보관함 주입이 이 장소를
    후보로 되돌려 놓아 애초에 빠지지 않는다(SCHEDULE-12).
    """
    store = InMemoryStateStore()
    tool_provider = _RefillPlacesToolProvider(total=6, page_size=6)
    # 전부 열어 둔다 — 1턴에서 담을 수 있어야 하고, 닫는 것은 그 다음이다.
    tool_provider._places = [
        place.model_copy(update={"operating_schedule": _OPEN_ALL_DAY_SCHEDULE})
        for place in tool_provider._places
    ]
    providers = {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": tool_provider,
        "recommendation_provider": RealRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }

    session_id, place_id = await _recommend_then_save(store, providers)

    # 담은 뒤에 문을 닫았다. 다음 턴 D는 이 장소를 폐점으로 걸러낸다.
    tool_provider._places = [
        place.model_copy(update={"operating_schedule": _CLOSED_ALL_WEEK_SCHEDULE})
        if place.place_id == place_id
        else place
        for place in tool_provider._places
    ]

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        **providers,
    )

    assert response.schedule is not None
    # 대조군 — 이 장소가 실제로 **폐점 사유로** 걸러졌음을 같은 실행에서 확인한다.
    # 이게 없으면 D가 다른 이유로 후보를 못 준 경우에도 아래 단정이 통과할 수 있다.
    # SCHEDULE 턴 응답에는 recommendations가 실리지 않으므로, A가 6-1에서
    # 기록한 폐점 제외 이력(TP-82)을 저장소에서 읽어 확인한다.
    history = store.get_history(session_id)
    assert history is not None
    assert place_id in {item.place_id for item in history.closed_excluded}
    saved_name = next(
        item.name
        for item in state_service.get_session_context(session_id, store=store).saved_places
        if item.place_id == place_id
    )
    assert saved_name and saved_name != place_id
    assert response.schedule.closed_saved_place_names == [saved_name]
    # 사유가 갈렸으므로 absent 쪽은 비어야 한다 — 예전에는 여기로 갔다.
    assert response.schedule.absent_saved_place_names == []


@pytest.mark.asyncio
async def test_saved_place_absent_for_other_reasons_stays_in_absent() -> None:
    """폐점이 아닌 이유로 빠진 장소는 그대로 absent에 남는다. (TP-236)

    갈라내기가 한쪽으로 쏠리지 않았는지 잠근다. 위 테스트만 있으면 모든 장소를
    closed로 보내는 구현도 통과한다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _DroppingToolProvider()
    providers["tool_provider"] = tool_provider

    session_id, place_id = await _recommend_then_save(store, providers)
    # C가 이 장소를 아예 안 돌려준다 — 폐점이 아니라 장소 정보가 없는 경우다.
    tool_provider.drop_place_id = place_id

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        **providers,
    )

    assert response.schedule is not None
    assert response.schedule.absent_saved_place_names != []
    assert response.schedule.closed_saved_place_names == []


@pytest.mark.asyncio
async def test_repeated_reject_all_does_not_refetch_closed_candidates() -> None:
    """TP-82 완료 조건: 같은 세션에서 "다른 곳 보여줘"를 반복해도, 이전에
    폐점으로 판명된 후보는 다음 회차 C 조회에서 다시 뽑히지 않는다.

    후보 15개 중 5개(0~4)만 영업 중, 10개(5~14)는 폐점 — 1턴에서 5개가
    노출되고 10개가 폐점으로 걸러진다. 2턴("다른 곳 보여줘")에서 C가 받는
    excluded_place_ids에 그 10개가 이미 포함돼 있어야, 폐점 후보를 매번
    다시 조회해 낭비하지 않는다(밤 시간대 폐점 비율이 높을 때 카드 수가
    점점 줄어드는 원인이었다).
    """
    store = InMemoryStateStore()
    tool_provider = _RefillPlacesToolProvider(
        total=15, page_size=15, open_indexes={0, 1, 2, 3, 4}
    )
    providers = {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": tool_provider,
        "recommendation_provider": RealRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘", session_id=None, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )
    first_shown = {
        item.place_id
        for item in [
            *first.recommendations.recommendations,
            *first.recommendations.unverified_recommendations,
        ]
    }
    assert len(first_shown) == 5
    closed_ids = {f"refill-{i}" for i in range(5, 15)}
    assert set(first.recommendations.excluded_closed_place_ids) == closed_ids

    second = await run_agent_flow(
        AgentRequest(
            user_input="다른 곳 보여줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    # 2턴 C 조회의 excluded_place_ids가 1턴 폐점 후보 10개를 이미 포함해야
    # 한다 — 후보 풀에 남은 게 없으므로(전부 노출 or 폐점) 결과가 0건이어도
    # 정상이다. 여기서 확인하려는 건 "재조회 자체를 안 한다"는 것이다.
    assert len(second.state.excluded_place_ids) >= 15
    assert closed_ids.issubset(set(second.state.excluded_place_ids))
    second_request_excluded = set(tool_provider.requests[-1].excluded_place_ids)
    assert closed_ids.issubset(second_request_excluded)


@pytest.mark.asyncio
async def test_staged_recommendation_passes_only_eligible_routes_to_d_after_refill(
    refill_page_limit: int,
) -> None:
    context_provider = _RefillPlacesToolProvider()
    route_tool = _RecordingTravelRouteTool()
    recommendation_provider = _RecordingWalkingRoutesRecommendationProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=context_provider,
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert len(context_provider.requests) == 3
    assert len(route_tool.queries) == 1
    requested_ids = [destination.place_id for destination in route_tool.queries[0].destinations]
    assert requested_ids == ["refill-0", "refill-10", *[f"refill-{i}" for i in range(20, 25)]]
    assert [route.place_id for route in recommendation_provider.travel_routes] == requested_ids
    assert "refill-1" not in requested_ids


class _DroppingRefillToolProvider(_RefillPlacesToolProvider):
    """후보를 넉넉히 주면서 지정한 place_id만 응답에서 뺀다.

    `_DroppingToolProvider`와 목적은 같지만 후보 풀이 자르기 상한보다 커야 하는
    시나리오라 이쪽을 쓴다 — 주입한 장소가 상위 N에서 잘리는지 보려면 풀이 상한
    이하로는 안 된다.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.drop_place_id: str | None = None

    async def fetch_context(self, request):
        response = await super().fetch_context(request)
        if self.drop_place_id is None or response.context is None:
            return response
        places = response.context.places
        if places is None or places.data is None:
            return response
        kept = [place for place in places.data if place.place_id != self.drop_place_id]
        return response.model_copy(
            update={
                "context": response.context.model_copy(
                    update={"places": places.model_copy(update={"data": kept})}
                )
            }
        )


@pytest.mark.asyncio
async def test_injected_saved_place_survives_the_top_n_cut() -> None:
    """후보 풀이 자르기 상한보다 커도 주입한 보관함 장소는 살아남아야 한다.

    실사용 재현(2026-09-01): 인사동에서 담은 2곳으로 홍대 일정을 요청하니 둘 다
    "이번에 찾은 후보에 없어서"로 빠졌다. Supabase에 행이 있고 영업 중이었으므로
    주입 자체는 성공했고, 검색 반경(2km) 밖이라 거리 점수가 0이 되어 점수순
    자르기(`recommendation_pipeline.py`의 `ranked[:recommendation_limit]`)에서
    잘린 것이다.

    주입 개수만큼 상한을 올리는 방어는 후보 풀이 딱 그 크기일 때만 유효하다.
    하필 보관함의 주력 유스케이스가 구 간 이동(= 반경 밖)이라, 거리 점수가 0으로
    깔리는 보관함 장소가 가장 확실하게 잘린다.

    기존 주입 테스트들이 이걸 못 잡은 이유는 후보 더블이 몇 곳만 돌려줘서 풀이
    상한보다 작았기 때문이다 — 자르기 자체가 일어나지 않았다.
    """
    store = InMemoryStateStore()
    tool_provider = _DroppingRefillToolProvider(
        total=20, page_size=20, open_indexes=set(range(20))
    )
    providers = {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": tool_provider,
        "recommendation_provider": RealRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.recommendations is not None
    shown = [
        *first.recommendations.recommendations,
        *first.recommendations.unverified_recommendations,
    ]
    assert shown
    session_id = first.state.session_id
    place_id = shown[0].place_id
    state_service.save_place(
        session_id,
        state_service.SavePlaceRequest(place_id=place_id),
        store=store,
    )

    # 담은 다음 턴 검색이 그 장소를 다시 못 물어온다(반경 밖).
    tool_provider.drop_place_id = place_id
    # 상세는 남아 있고 좌표만 검색 중심점에서 약 5km 떨어져 있다 — 종로에서 담고
    # 홍대에서 일정을 짜는 상황 그대로다.
    repository = _FakePlaceDetailsRepository(
        {place_id: _stored_detail(place_id, latitude=37.5563, longitude=126.9236)}
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert response.schedule is not None
    # 진단 순서: 상황을 만들었는가 → 주입을 시도했는가 → 살아남았는가.
    assert place_id not in tool_provider.requests[-1].excluded_place_ids, (
        "보관함 장소가 제외 목록에 남아 있다 — _revivable_place_ids()가 안 돌았다"
    )
    assert place_id in repository.requested_ids, "상세 조회 자체가 시도되지 않았다"
    assert response.schedule.absent_saved_place_names == [], (
        "주입은 됐는데 상위 N 자르기에서 잘렸다 — 상한을 주입 개수만큼 올리는 것으로는 "
        "후보 풀이 상한보다 클 때 방어가 되지 않는다"
    )


class _FarPlaceRefillToolProvider(_RefillPlacesToolProvider):
    """후보를 넉넉히 주면서 지정한 place_id만 검색 중심에서 멀리 옮긴다.

    `_DroppingRefillToolProvider`와 정반대 상황이다 — 저쪽은 보관함 장소가 이번 턴
    후보에서 **빠져서** 주입 경로를 타지만, 이쪽은 후보에 **그대로 남아** 주입 대상이
    아니면서 점수순 자르기에서만 밀린다.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.far_place_id: str | None = None
        # 비-staged(Fake) 분기용. 저쪽은 거리로 채점하지 않고 Context 순서를 그대로
        # 잘라내므로(`stubs.FakeRecommendationProvider`), 뒤로 미는 것이 "자르기에
        # 밀린다"를 만드는 유일한 방법이다.
        self.tail_place_id: str | None = None

    async def fetch_context(self, request):
        response = await super().fetch_context(request)
        if response.context is None:
            return response
        places = response.context.places
        if places is None or places.data is None:
            return response
        if self.tail_place_id is not None:
            reordered = [
                place for place in places.data if place.place_id != self.tail_place_id
            ] + [
                place for place in places.data if place.place_id == self.tail_place_id
            ]
            return response.model_copy(
                update={
                    "context": response.context.model_copy(
                        update={"places": places.model_copy(update={"data": reordered})}
                    )
                }
            )
        if self.far_place_id is None:
            return response
        moved = [
            (
                place.model_copy(
                    update={
                        # 종로에서 담고 홍대에서 일정을 짜는 상황과 같은 거리(약 5km).
                        "location": Coordinates(latitude=37.5563, longitude=126.9236)
                    }
                )
                if place.place_id == self.far_place_id
                else place
            )
            for place in places.data
        ]
        return response.model_copy(
            update={
                "context": response.context.model_copy(
                    update={"places": places.model_copy(update={"data": moved})}
                )
            }
        )


@pytest.mark.asyncio
async def test_saved_place_already_in_candidates_survives_the_top_n_cut() -> None:
    """이번 턴 후보에 **이미 들어 있는** 보관함 장소도 자르기에서 살아남아야 한다. (TP-223)

    D-116 정정이 넣은 자르기 복구는 `injected_saved_ids`, 즉 `_saved_places_context()`가
    **주입한** 장소만 되붙인다. 그런데 그 함수는 "이번 턴 후보에 없는" 보관함 장소만
    주입 대상으로 삼는다(`agent_runtime.py`의 `missing = [...] not in present`).

    그래서 보관함 장소가 이번 턴 후보에 이미 들어 있으면 주입도 안 되고 복구 대상도
    아니다 — 점수순 상한(`SCHEDULE_RECOMMENDATION_LIMIT`)에서 잘리면 아무도 되붙이지
    않는다. 사용자에게는 "이번에 찾은 후보에 없어서"로 보인다.

    실사용 재현(TP-223, 2026-09-02): 6곳을 담았는데 세종문화회관·인사동 문화의 거리가
    빠졌다. 둘 다 Supabase에 행이 있고 좌표가 있었으며, 운영시간 원문이 파싱되지 않아
    (`매장 별로 상이함`) 폐점 필터에는 애초에 걸리지 않는다 — 같은 원문을 가진 남대문
    두 곳은 들어갔다. 남은 차이는 "이번 턴 후보에 있었느냐"뿐이다.

    기존 `test_injected_saved_place_survives_the_top_n_cut`이 이걸 못 잡은 이유는
    더블이 보관함 장소를 응답에서 **빼서** 항상 주입 경로만 태웠기 때문이다.
    """
    store = InMemoryStateStore()
    tool_provider = _FarPlaceRefillToolProvider(
        total=20, page_size=20, open_indexes=set(range(20))
    )
    providers = {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": tool_provider,
        "recommendation_provider": RealRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.recommendations is not None
    shown = [
        *first.recommendations.recommendations,
        *first.recommendations.unverified_recommendations,
    ]
    assert shown
    session_id = first.state.session_id
    place_id = shown[0].place_id
    state_service.save_place(
        session_id,
        state_service.SavePlaceRequest(place_id=place_id),
        store=store,
    )

    # 담은 다음 턴에도 그 장소는 후보에 그대로 있다 — 다만 거리 점수가 0으로 깔린다.
    tool_provider.far_place_id = place_id
    repository = _FakePlaceDetailsRepository(
        {place_id: _stored_detail(place_id, latitude=37.5563, longitude=126.9236)}
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert response.schedule is not None
    # 진단 순서: 상황을 만들었는가 → 주입 경로가 아닌가 → 그래도 살아남았는가.
    assert place_id not in tool_provider.requests[-1].excluded_place_ids, (
        "보관함 장소가 제외 목록에 남아 있다 — _revivable_place_ids()가 안 돌았다"
    )
    assert place_id not in repository.requested_ids, (
        "후보에 이미 있는데도 주입을 시도했다 — 이 테스트가 겨냥한 경로가 아니다"
    )
    assert response.schedule.absent_saved_place_names == [], (
        "후보에 이미 있던 보관함 장소가 상위 N 자르기에서 잘렸다 — 자르기 복구가 "
        "주입된 것(injected_saved_ids)만 보고 있어 이 경로를 보호하지 못한다"
    )


@pytest.mark.asyncio
async def test_saved_place_already_in_candidates_survives_the_cut_without_staging() -> None:
    """비-staged 분기(Fake D)에서도 후보에 있던 보관함 장소가 살아남아야 한다. (TP-223)

    staged 분기는 `merged_prepared`를 좁히면 원래 후보와 주입분을 함께 덮지만,
    이쪽은 prepare 결과가 없어 좁힐 대상이 Context뿐이다. 예전에는 **주입
    Context**만 다시 채점해서, 후보에 원래 있던 보관함 장소는 되붙일 방법이
    아예 없었다.
    """
    store = InMemoryStateStore()
    tool_provider = _FarPlaceRefillToolProvider(
        total=20, page_size=20, open_indexes=set(range(20))
    )
    providers = {
        "llm": _LLMProviderWithGeneralAnswer(),
        "tool_provider": tool_provider,
        "recommendation_provider": FakeRecommendationProvider(),
        "enrichment_provider": _CountingEnrichmentProvider(),
    }

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.recommendations is not None
    shown = [
        *first.recommendations.recommendations,
        *first.recommendations.unverified_recommendations,
    ]
    assert shown
    session_id = first.state.session_id
    place_id = shown[0].place_id
    state_service.save_place(
        session_id,
        state_service.SavePlaceRequest(place_id=place_id),
        store=store,
    )

    # 후보에는 그대로 있지만 맨 뒤로 밀려 상한 밖에 놓인다.
    tool_provider.tail_place_id = place_id
    repository = _FakePlaceDetailsRepository(
        {place_id: _stored_detail(place_id)}
    )

    response = await run_agent_flow(
        AgentRequest(
            user_input="이 장소들로 일정 짜기",
            session_id=session_id,
            device_location=DEVICE_LOCATION,
            schedule_from_saved=True,
        ),
        store=store,
        place_details_repository=repository,
        **providers,
    )

    assert response.schedule is not None
    assert place_id not in repository.requested_ids, (
        "후보에 이미 있는데도 주입을 시도했다 — 이 테스트가 겨냥한 경로가 아니다"
    )
    assert response.schedule.absent_saved_place_names == [], (
        "비-staged 분기에서 후보에 있던 보관함 장소가 잘렸다"
    )


class Test후보_Context_좁히기:
    """`_narrow_recommendation_context_places()` — 되붙일 후보만 남긴다. (TP-223)"""

    @staticmethod
    def _context(*place_ids: str) -> RecommendationContext:
        return RecommendationContext(
            location=ContextValue(
                status="success",
                data=ResolvedLocation(
                    requested_query="경복궁",
                    resolved_name="경복궁",
                    source="query",
                    location=Coordinates(latitude=37.5788, longitude=126.9770),
                ),
            ),
            places=ContextValue(
                status="success",
                data=[
                    PlaceCandidate(
                        place_id=place_id,
                        name=f"장소 {place_id}",
                        category="cafe",
                        location=Coordinates(latitude=37.5, longitude=127.0),
                    )
                    for place_id in place_ids
                ],
            ),
        )

    def test_지정한_id만_남긴다(self) -> None:
        narrowed = _narrow_recommendation_context_places(
            self._context("a", "b", "c"), ["b"]
        )

        assert narrowed is not None
        assert narrowed.places is not None
        assert [place.place_id for place in (narrowed.places.data or [])] == ["b"]

    def test_남는_것이_없으면_None이다(self) -> None:
        """빈 Context로 채점을 부르지 않게 호출부가 분기할 수 있어야 한다."""

        assert _narrow_recommendation_context_places(self._context("a"), ["z"]) is None

    def test_후보가_없으면_None이다(self) -> None:
        context = self._context("a").model_copy(
            update={"places": ContextValue(status="success", data=[])}
        )

        assert _narrow_recommendation_context_places(context, ["a"]) is None


class _LLMProviderWithTransport(_LLMProviderWithGeneralAnswer):
    """이동수단과 이동시간을 못 박는 더블 — FakeLLMProvider는 transport를 만들지 않는다."""

    def __init__(self, transport: Transport | None, max_travel_time: int = 30) -> None:
        super().__init__()
        self._transport = transport
        self._max_travel_time = max_travel_time

    async def extract_recommend_conditions(self, user_input, **kwargs):
        result = await super().extract_recommend_conditions(user_input, **kwargs)
        output = result.data
        assert output.recommend is not None
        conditions = output.recommend.conditions.model_copy(
            update={"transport": self._transport, "max_travel_time": self._max_travel_time}
        )
        return provider_result(
            output.model_copy(update={"recommend": RecommendPayload(conditions=conditions)}),
            source=ProviderSource.FAKE_LLM,
        )


@pytest.mark.asyncio
async def test_staged_recommendation_asks_driving_mode_and_gets_nothing_for_car_request() -> None:
    """자동차 요청은 자동차 mode로만 묻고, 등록된 Provider가 없어 값 없이 돌아온다.

    도보를 함께 묻지 않는 것이 핵심이다 — 자동차라고 말한 사용자에게 도보 시간을
    보여줄 이유가 없다. 거리가 임계를 넘어도 대중교통을 덧붙이지 않는다(D-118).
    카카오 도보 호출이 0건인지는 test_travel_route_tool.py가 Provider 호출 수로
    못 박는다.
    """
    route_tool = _RecordingTravelRouteTool()
    recommendation_provider = _RecordingWalkingRoutesRecommendationProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithTransport(Transport.CAR),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert [query.mode for query in route_tool.queries] == [TravelMode.DRIVING]
    assert recommendation_provider.travel_routes == ()


@pytest.mark.asyncio
async def test_staged_recommendation_measures_walking_when_transport_is_unstated() -> None:
    """이동수단 미언급 + 이동시간 언급도 실측한다 (D-118).

    예전에는 조회하지 않았다 — 반경이 20km/h 가정으로 커져 있는데 그게 대중교통인지
    자동차인지 발화에 없어서, 무엇으로 재도 예산과 단위가 안 맞았기 때문이다.
    예산이 측정 수단을 보지 않게 되면서 그 이유가 사라졌다.

    이 픽스처의 후보는 전부 기준점에서 0.3km 안이라 임계(0.85km) 아래다. 그래서
    대중교통은 묻지 않고 도보만 조회한다.
    """
    route_tool = _RecordingTravelRouteTool()
    recommendation_provider = _RecordingWalkingRoutesRecommendationProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithTransport(None),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert [query.mode for query in route_tool.queries] == [TravelMode.WALKING]


class _RecordingSavedTasteProvider(RealRecommendationProvider):
    """score_prepared가 받은 saved_taste_query를 전부 기록한다."""

    def __init__(self) -> None:
        self.saved_taste_queries: list[str | None] = []

    async def score_prepared(
        self,
        conditions,
        prepared,
        *,
        travel_routes=(),
        limit=5,
        saved_taste_query=None,
    ):
        self.saved_taste_queries.append(saved_taste_query)
        return await super().score_prepared(
            conditions,
            prepared,
            travel_routes=travel_routes,
            limit=limit,
            saved_taste_query=saved_taste_query,
        )


@pytest.mark.asyncio
async def test_saved_preferences_reach_scoring_when_nothing_was_spoken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """계정에 저장해 둔 취향이 실제로 채점까지 간다.

    `_saved_taste_query()` 단위 테스트만으로는 **호출부 한 줄이 지워져도 안 잡힌다** —
    되돌려서 확인했다. 1.9.0에서 provider 배선 2줄이 같은 구멍이었다.

    취향 스위치를 켠다 — conftest가 끄는데, 꺼져 있으면 저장값을 아예 읽지 않는다.
    """
    monkeypatch.setattr(settings, "taste_evidence_enabled", True)
    store = InMemoryStateStore()
    state_preferences.replace(
        store,
        "user-saved-taste",
        [
            UserPreference(label="아늑한 공간", source="preference", codes=["cozy"]),
            UserPreference(label="전망 좋은", source="preference", codes=["good_view"]),
            # 분류 칩도 질의에 들어간다 — 고른 것을 버리지 않는다.
            UserPreference(label="카페", source="place_tag", codes=["카페", "찻집"]),
        ],
    )
    provider = _RecordingSavedTasteProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
        principal=Principal(user_id="user-saved-taste", is_anonymous=False),
    )

    # 실측 경로 tool이 없어 1차만 도는 구성이라 호출이 1건이다. 2차(실측 반영)까지
    # 도는 구성은 travel_route_tool을 붙인 아래 테스트가 본다.
    assert provider.saved_taste_queries == ["아늑한 공간 전망 좋은 카페"]


@pytest.mark.asyncio
async def test_saved_preferences_reach_both_scoring_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """1차(실측 대상 고르기)와 2차(실측 반영)가 **같은 값**을 봐야 한다.

    한쪽만 주면 취향으로 후보를 좁혀 놓고 최종 순위에서는 취향을 빼게 된다 —
    2026-08-20에 그 계열의 사고가 있었다(`SCORING_VERSION` 1.4.0).
    """
    monkeypatch.setattr(settings, "taste_evidence_enabled", True)
    store = InMemoryStateStore()
    state_preferences.replace(
        store,
        "user-two-pass",
        [UserPreference(label="아늑한 공간", source="preference", codes=["cozy"])],
    )
    provider = _RecordingSavedTasteProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=_RecordingTravelRouteTool(),
        store=store,
        principal=Principal(user_id="user-two-pass", is_anonymous=False),
    )

    assert len(provider.saved_taste_queries) >= 2, provider.saved_taste_queries
    assert set(provider.saved_taste_queries) == {"아늑한 공간"}


@pytest.mark.asyncio
async def test_guest_has_no_saved_preferences_to_apply() -> None:
    """신원이 없으면 저장할 자리가 없다 — 지금까지와 같이 동작한다."""
    provider = _RecordingSavedTasteProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert provider.saved_taste_queries
    assert set(provider.saved_taste_queries) == {None}


@pytest.mark.asyncio
async def test_staged_recommendation_requests_walking_mode_for_walk_request() -> None:
    """도보 요청은 mode=WALKING으로 조회한다 — 반경도 도보 속도로 만들어진다."""
    route_tool = _RecordingTravelRouteTool()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=_RecordingWalkingRoutesRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert [query.mode for query in route_tool.queries] == [TravelMode.WALKING]


@pytest.mark.asyncio
async def test_staged_recommendation_passes_empty_routes_when_route_tool_is_unavailable() -> None:
    recommendation_provider = _RecordingWalkingRoutesRecommendationProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(total=6),
        recommendation_provider=recommendation_provider,
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=_UnavailableTravelRouteTool(),
        store=InMemoryStateStore(),
    )

    assert response.recommendations is not None
    assert recommendation_provider.travel_routes == ()


@pytest.mark.asyncio
async def test_staged_recommendation_stops_after_max_refill_attempts(
    refill_page_limit: int,
) -> None:
    store = InMemoryStateStore()
    tool_provider = _RefillPlacesToolProvider()
    tool_provider._places = [
        place.model_copy(update={"operating_schedule": _CLOSED_ALL_WEEK_SCHEDULE})
        for place in tool_provider._places
    ]

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    # 최초 1회 + 보충 최대 2회. 후보가 계속 부족해도 네 번째 호출은 하지 않는다.
    assert len(tool_provider.requests) == 3


async def _run_staged_recommend(
    tool_provider: FakeToolProvider,
    *,
    store: InMemoryStateStore | None = None,
    user_input: str = "경복궁 근처 카페 추천해줘",
    stream_event_sink=None,
):
    """실제 D(RealRecommendationProvider)를 태워 staged 경로만 돌리는 공통 실행부."""
    return await run_agent_flow(
        AgentRequest(
            user_input=user_input,
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store if store is not None else InMemoryStateStore(),
        stream_event_sink=stream_event_sink,
    )


@pytest.mark.parametrize(
    ("candidate_limit", "page_size", "expected_requests"),
    [
        # 첫 조회에서 6곳이 하드 필터를 통과한다. result_limit(5)은 이미 넘었지만
        # candidate_limit(10)에는 못 미치므로 보충이 돈다 — 목표가 result_limit이면
        # 여기서 1회로 끝나버린다.
        (10, 10, 3),
        # 같은 6곳이라도 candidate_limit이 6이면 목표를 채웠으니 보충하지 않는다.
        (6, 6, 1),
    ],
)
@pytest.mark.asyncio
async def test_staged_recommendation_refill_target_is_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
    candidate_limit: int,
    page_size: int,
    expected_requests: int,
) -> None:
    """보충 조회 목표는 recommendation_candidate_limit이다.

    하드 필터를 통과한 후보를 설정된 후보 상한만큼 모아두고 그 안에서 고른다 —
    최종 노출 개수(result_limit)를 채운 시점에 멈추지 않는다.
    """
    monkeypatch.setattr(
        "app.services.runtime.agent_runtime.settings.recommendation_candidate_limit",
        candidate_limit,
    )
    tool_provider = _RefillPlacesToolProvider(
        page_size=page_size,
        open_indexes={0, 1, 2, 3, 4, 5},
    )

    response = await _run_staged_recommend(tool_provider)

    assert len(tool_provider.requests) == expected_requests
    assert response.recommendations is not None


@pytest.mark.asyncio
async def test_staged_recommendation_skips_refill_when_pool_smaller_than_limit() -> None:
    """C가 candidate_limit보다 적게 반환했으면 반경을 다 긁은 것이라 보충하지 않는다.

    candidate_pool_truncated 경고는 C가 상한(100행)을 넘겨 요청했을 때만 서기
    때문에, 반경 안에 후보가 애초에 몇 개 없는 흔한 경우는 이 조건으로만 걸린다.
    """
    # 전체 6곳(열린 곳은 refill-0 하나) < candidate_limit(10).
    tool_provider = _RefillPlacesToolProvider(total=6)

    response = await _run_staged_recommend(tool_provider)

    assert len(tool_provider.requests) == 1
    assert response.recommendations is not None


class _WeatherDivergingRefillToolProvider(_RefillPlacesToolProvider):
    """최초 조회에만 날씨를 싣는 대역 — 보충 조회에서 기상 조회가 실패한 상황."""

    def _build_context(
        self,
        places: list[PlaceCandidate],
        call_index: int,
    ) -> RecommendationContext:
        context = super()._build_context(places, call_index)
        if call_index > 0:
            return context
        return context.model_copy(
            update={
                "weather": ContextValue(
                    status="success",
                    data=WeatherForecast(
                        forecast_for=now_kst(),
                        precipitation="rain",
                        sky="cloudy",
                        temperature_celsius=18.0,
                    ),
                )
            }
        )


@pytest.mark.asyncio
async def test_staged_recommendation_reuses_first_batch_weather_for_refill_batches(
    refill_page_limit: int,
) -> None:
    """보충 조회에서 날씨가 빠져도 배치를 버리지 않고 첫 배치 판정을 재사용한다.

    보충 조회는 같은 요청·같은 시각·같은 좌표를 다시 조회하는 것이라, 날씨가
    달라졌다면 그건 판정이 바뀐 게 아니라 그쪽 기상 조회가 실패한 것이다.
    하드 필터는 날씨를 입력으로 받지도 않으므로(prepare_candidates), 여기서
    배치를 거부하면 멀쩡한 보충 후보만 통째로 버리게 된다.
    """
    tool_provider = _WeatherDivergingRefillToolProvider()

    response = await _run_staged_recommend(tool_provider)

    # 날씨가 달라져도 보충이 중단되지 않는다.
    assert len(tool_provider.requests) == 3
    assert response.recommendations is not None
    shown = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    # 날씨가 없던 보충 배치의 후보도 추천에 남아 있다.
    from_refill_batches = [item for item in shown if item.place_id != "refill-0"]
    assert from_refill_batches
    # 그리고 첫 배치의 날씨 판정으로 채점됐다 — 재사용이 아니었다면 weather
    # Feature가 결측(None)이 되고 "날씨 확인 못 함" warning이 붙는다.
    for item in from_refill_batches:
        assert item.feature_scores.get("weather") is not None


class _BrokenLocationRefillToolProvider(_RefillPlacesToolProvider):
    """보충 조회 응답만 location을 잃은 대역 — D의 prepare()가 AppError를 던진다."""

    def _build_context(
        self,
        places: list[PlaceCandidate],
        call_index: int,
    ) -> RecommendationContext:
        context = super()._build_context(places, call_index)
        if call_index == 0:
            return context
        return context.model_copy(update={"location": None})


@pytest.mark.asyncio
async def test_staged_recommendation_drops_refill_batch_when_prepare_raises(
    refill_page_limit: int,
) -> None:
    """보충 Context가 장소는 실었지만 location이 없으면 prepare()가 AppError를 던진다.

    응답 status와 place_id 유무만 보는 가드로는 이 조합이 안 걸려서, 보충 실패가
    요청 전체를 죽였다.
    """
    tool_provider = _BrokenLocationRefillToolProvider()

    response = await _run_staged_recommend(tool_provider)

    assert len(tool_provider.requests) == 2
    assert response.recommendations is not None
    shown = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    assert [item.place_id for item in shown] == ["refill-0"]


@pytest.mark.asyncio
async def test_staged_recommendation_refill_progress_does_not_move_backwards(
    refill_page_limit: int,
) -> None:
    """보충 조회 중에도 progress stage는 scoring을 유지한다.

    프론트(AgentProgressMessage.tsx)는 stage로 진행 순서를 그리고 문구만 서버
    message로 덮어쓴다 — 여기서 fetching_context를 다시 보내면 완료 표시가 뒤로
    돌아간다.
    """
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    tool_provider = _RefillPlacesToolProvider()
    await _run_staged_recommend(tool_provider, stream_event_sink=sink)

    assert len(tool_provider.requests) > 1  # 보충이 실제로 돌았다
    stages = [payload["stage"] for event, payload in events if event == "progress"]
    assert "scoring" in stages
    # scoring 이후에는 그보다 앞 단계로 되돌아가지 않는다.
    assert "fetching_context" not in stages[stages.index("scoring") :]
    messages = [payload["message"] for event, payload in events if event == "progress"]
    assert "조건에 맞는 장소를 조금 더 찾고 있어요." in messages


@pytest.mark.asyncio
async def test_staged_recommendation_all_closed_triggers_no_data_closed() -> None:
    """보충까지 돌고도 전부 폐점이면 no_data_closed 되묻기로 이어져야 한다.

    excluded_all_closed는 병합된 제외 목록 전체가 CLOSED일 때만 참이다 —
    배치를 합치면서 제외 사유 집계가 어긋나면 이 되묻기가 조용히 사라진다.
    """
    tool_provider = _RefillPlacesToolProvider()
    tool_provider._places = [
        place.model_copy(update={"operating_schedule": _CLOSED_ALL_WEEK_SCHEDULE})
        for place in tool_provider._places
    ]
    store = InMemoryStateStore()

    response = await _run_staged_recommend(tool_provider, store=store)

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.code == "no_data_closed"
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "no_data_closed"


@pytest.mark.asyncio
async def test_staged_recommendation_merges_refill_places_into_tool_context(
    refill_page_limit: int,
) -> None:
    """보충으로 받은 장소도 tool_context에 합쳐져 후속 C 보강 조회로 넘어가야 한다.

    to_candidate_enrichment_request()는 원본 places에서 place_id를 못 찾은 후보를
    조용히 버린다 — 병합을 빠뜨리면 보충으로 추천된 장소만 혼잡도 보강에서
    사라지고, 그 사실이 아무 데도 안 드러난다.
    """
    tool_provider = _RefillPlacesToolProvider()
    enrichment_provider = _CountingEnrichmentProvider()

    response = await run_agent_flow(
        AgentRequest(
            # "조용" → FakeLLMProvider가 concentration_intent=AVOID를 세워
            # 6-1단계 혼잡도 보강 조회가 실제로 돈다.
            user_input="경복궁 근처 조용한 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=enrichment_provider,
        store=InMemoryStateStore(),
    )

    assert len(tool_provider.requests) > 1
    assert response.recommendations is not None
    shown = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    refill_ids = {item.place_id for item in shown if item.place_id != "refill-0"}
    assert refill_ids  # 보충으로 들어온 후보가 실제로 추천됐다
    assert enrichment_provider.last_request is not None
    enriched_ids = {target.place_id for target in enrichment_provider.last_request.candidates}
    assert refill_ids <= enriched_ids


async def _run_with_partial_places(places: list[PlaceCandidate]):
    """실제 D(RealRecommendationProvider)까지 태워 A→C→D 전파를 확인한다.

    _CountingRecommendationProvider는 호출 횟수만 세는 stub이라 분류 로직을 타지
    않는다. 통합 검증에는 실제 구현을 주입해야 한다.
    """
    return await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_PartialPlacesToolProvider(places),
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )


@pytest.mark.asyncio
async def test_partial_context_keeps_all_candidates_and_splits_unverified() -> None:
    """partial Context의 후보가 누락 없이 D까지 가고, 운영정보 유무로 나뉜다.

    Supabase 상세조회로 전환한 뒤 DB에 없는 장소는 운영정보가 비는데(detail no_data),
    그 후보가 중간에 사라지면 추천 수가 조용히 줄고, 잘못 분류되면 운영시간을 모르는
    곳을 확정 추천하게 된다.
    """
    response = await _run_with_partial_places(
        [
            _context_place("with-1", with_schedule=True),
            _context_place("without-1", with_schedule=False),
            _context_place("without-2", with_schedule=False),
        ]
    )

    assert response.recommendations is not None
    verified = [item.place_id for item in response.recommendations.recommendations]
    unverified = [item.place_id for item in response.recommendations.unverified_recommendations]

    # C가 준 3건이 그대로 유지된다.
    assert len(verified) + len(unverified) == 3
    assert verified == ["with-1"]
    assert sorted(unverified) == ["without-1", "without-2"]


@pytest.mark.asyncio
async def test_partial_context_with_no_operating_hours_returns_only_unverified() -> None:
    """전건 운영정보가 없으면 확정 추천은 비고 unverified만 남는다.

    현재는 이 경우에도 "이런 곳들을 찾아봤어요:"가 나간다 — 첫 문장에서 미확인임을
    알리는 편이 나을 수 있으나, 메시지 정책은 별도 판단 대상이라 현 동작을 고정한다.
    """
    response = await _run_with_partial_places(
        [
            _context_place("without-1", with_schedule=False),
            _context_place("without-2", with_schedule=False),
        ]
    )

    assert response.recommendations is not None
    assert response.recommendations.recommendations == []
    assert len(response.recommendations.unverified_recommendations) == 2


class _ClosedOnlyRecommendationProvider:
    """D 대역 — ignore_operating_hours 유무로 결과 유무가 바뀐다.

    실제로는 domain/scoring.py가 폐점 후보를 걸러내고 D의
    recommendation_pipeline.py가 excluded_all_closed를 계산하지만
    (test_scoring.py/test_recommendation_pipeline.py가 그 계산 자체를 검증한다),
    여기서는 그 결과 모양만 고정으로 흉내 내 no_data_closed 되묻기의 A 쪽 배선
    (agent_runtime.py)만 검증한다.
    """

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        self.calls.append(ignore_operating_hours)
        if not ignore_operating_hours:
            return RecommendationResponse(
                recommendations=[],
                unverified_recommendations=[],
                elapsed_ms=0,
                excluded_all_closed=True,
            )
        return RecommendationResponse(
            recommendations=[],
            unverified_recommendations=[_item("closed-1")],
            elapsed_ms=0,
        )


@pytest.mark.asyncio
async def test_no_data_closed_triggers_clarification_with_show_closed_button() -> None:
    """실사용 피드백(2026-08-13): "조건에 맞는 곳을 찾지 못했어요" 대신, 원인이
    전부 폐점이면 "운영 중이 아닌 곳도 확인하시겠어요?" 되묻기 버튼을 띄워야
    한다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["recommendation_provider"] = _ClosedOnlyRecommendationProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.code == "no_data_closed"
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {"show_closed"}
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "no_data_closed"


@pytest.mark.asyncio
async def test_clarification_choice_show_closed_reruns_ignoring_operating_hours() -> None:
    """"운영 중이 아닌 곳도 볼게요" 클릭은 classify_intent() 재호출 없이 같은
    조건으로 D를 다시 부르되, 이번엔 ignore_operating_hours=True로 폐점 후보도
    채점에 포함해야 한다."""
    store = InMemoryStateStore()
    providers = _providers()
    provider = _ClosedOnlyRecommendationProvider()
    providers["recommendation_provider"] = provider

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="운영 중이 아닌 곳도 볼게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="show_closed",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is not None
    assert len(resolved.recommendations.unverified_recommendations) == 1
    assert provider.calls == [False, True]
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_show_closed_choice_keeps_ignoring_operating_hours_on_later_turns() -> None:
    """실사용 피드백(2026-08-13): "운영 중이 아닌 곳도 볼게요"를 한 번 누르면,
    이후 버튼을 다시 누르지 않은 새 RECOMMEND 요청에서도 TTL 동안은 계속
    폐점 후보를 포함해야 한다 — 매 턴 다시 물으면 안 된다."""
    store = InMemoryStateStore()
    providers = _providers()
    provider = _ClosedOnlyRecommendationProvider()
    providers["recommendation_provider"] = provider

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="운영 중이 아닌 곳도 볼게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="show_closed",
        ),
        store=store,
        **providers,
    )
    assert resolved.llm_output.status == OutputStatus.COMPLETE

    later = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 박물관도 추천해줘",
            session_id=resolved.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert later.llm_output.status == OutputStatus.COMPLETE
    assert later.llm_output.clarification is None
    assert later.recommendations is not None
    assert len(later.recommendations.unverified_recommendations) == 1
    assert provider.calls == [False, True, True]
    context = get_session_context(later.state.session_id, store=store)
    assert context.ignore_operating_hours_until is not None


class _ExhaustedNoDataToolProvider:
    """C 대역 — TourAPI raw candidates는 있었지만 excluded_place_ids로 전부
    소진된 상황(원인2)을 흉내 낸다. C가 `candidate_pool_exhausted` 경고를
    명시적으로 남겼을 때만 A가 소진 안내를 해야 한다."""

    def __init__(self) -> None:
        self.call_count = 0

    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        self.call_count += 1
        return AgentContextResponse(
            request_id=request.request_id,
            intent="RECOMMEND",
            status="no_data",
            context=RecommendationContext(
                location=ContextValue(
                    status="success",
                    data=ResolvedLocation(
                        requested_query="경복궁",
                        resolved_name="경복궁",
                        source="query",
                        location=Coordinates(latitude=37.5788, longitude=126.9770),
                    ),
                ),
                places=ContextValue(
                    status="no_data",
                    data=[],
                    warnings=[
                        ContextWarning(
                            code="candidate_pool_exhausted",
                            message="이미 본 장소를 제외하면 새 후보가 남아 있지 않습니다.",
                        )
                    ],
                    provider_metadata=[
                        ProviderMetadata(
                            source="tourapi",
                            status="success",
                            retrieved_at=datetime.now(UTC),
                        )
                    ],
                ),
            ),
            metadata=ResponseMetadata(),
        )


@pytest.mark.asyncio
async def test_no_data_exhausted_triggers_clarification_with_five_buttons() -> None:
    """원인2(이전 노출/거절 소진)는 provider_metadata가 "success"로 남아 원인1+3과
    구분된다 — "제외했던 곳도 다시 보기" 대신 조건을 바꾸는 선택지들을 보여준다
    (실사용 피드백 후속 조사, 2026-08-13)."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _ExhaustedNoDataToolProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.code == "no_data_exhausted"
    option_ids = {option.id for option in clarification.options}
    assert option_ids == {
        "widen_category",
        "widen_radius",
        "different_area",
        "ignore_weather",
        "custom_conditions",
    }
    assert len(clarification.options) <= 5
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification == "no_data_exhausted"


@pytest.mark.asyncio
async def test_clarification_choice_no_data_exhausted_widens_category_and_reruns() -> None:
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _ExhaustedNoDataToolProvider()

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.status == OutputStatus.NEEDS_CLARIFICATION

    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="다른 종류의 장소도 보기",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="widen_category",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.state.user_conditions.place_types == []
    assert resolved.state.user_conditions.place_tags == []
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_no_data_exhausted_custom_conditions_is_terminal() -> None:
    """"새로운 조건 직접 말할게요"는 Tool을 다시 부르지 않고 바로 끝난다(케이스5의
    full_reset과 동일 패턴)."""
    store = InMemoryStateStore()
    providers = _providers()
    tool_provider = _ExhaustedNoDataToolProvider()
    providers["tool_provider"] = tool_provider

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="새로운 조건 직접 말할게요",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="custom_conditions",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.recommendations is None
    assert resolved.message == "새로운 조건을 알려주세요!"
    assert tool_provider.call_count == 1
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


@pytest.mark.asyncio
async def test_clarification_choice_no_data_empty_widen_radius_reruns_search() -> None:
    store = InMemoryStateStore()
    providers = _providers()
    providers["tool_provider"] = _FixedStatusToolProvider("no_data")

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.clarification is not None
    assert first.llm_output.clarification.code == "no_data_empty"

    providers["tool_provider"] = _CountingToolProvider()
    resolved = await run_agent_flow(
        AgentRequest(
            user_input="검색 범위 넓히기",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="widen_radius",
        ),
        store=store,
        **providers,
    )

    assert resolved.llm_output.status == OutputStatus.COMPLETE
    assert resolved.state.user_conditions.max_travel_time == _WIDEN_RADIUS_MAX_TRAVEL_TIME
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.pending_clarification is None


class _TwoCandidateRecommendationProvider:
    """D 대역 — SCHEDULE-10 최소 개수(3개, time_available>=210분)보다 적은 2개만
    돌려준다. planner.py의 `len(request.candidates) < min_items` 가드가 걸려
    plan_schedule()이 LLM 호출 없이 바로 빈 ScheduleResult를 반환한다."""

    def __init__(self) -> None:
        self.calls: list[UserConditions] = []

    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        self.calls.append(conditions)
        return RecommendationResponse(
            recommendations=[_item("p1"), _item("p2")],
            unverified_recommendations=[],
            elapsed_ms=0,
        )


@pytest.mark.asyncio
async def test_clarification_choice_schedule_no_candidates_stays_schedule_intent() -> None:
    """실사용 버그(2026-08-13): SCHEDULE 후보 부족 되묻기의 "다른 종류의 장소도
    포함해서 찾기"를 누르면, 조건 병합이 MODIFY/CHANGE_CONDITION 경로를 타더라도
    최종 라벨은 SCHEDULE로 유지돼야 한다 — 그래야 다시 일정 편성을 시도하지,
    RECOMMEND 결과로 새지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()
    provider = _TwoCandidateRecommendationProvider()
    providers["recommendation_provider"] = provider

    first = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 2시간 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert first.llm_output.intent == "SCHEDULE"
    assert first.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert first.llm_output.clarification is not None
    assert first.llm_output.clarification.code == "schedule_no_candidates"
    option_ids = {option.id for option in first.llm_output.clarification.options}
    assert option_ids == {"schedule_relax_area", "schedule_relax_category"}
    assert all(
        option.resolved_intent == "SCHEDULE" for option in first.llm_output.clarification.options
    )

    resolved = await run_agent_flow(
        AgentRequest(
            user_input="다른 종류의 장소도 포함해서 찾기",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
            clarification_choice="schedule_relax_category",
        ),
        store=store,
        **providers,
    )

    # 후보는 여전히 2개뿐이라 이번에도 편성엔 실패하지만, 핵심 회귀 포인트는
    # intent가 MODIFY로 새지 않고 SCHEDULE로 유지되는지다.
    assert resolved.llm_output.intent == "SCHEDULE"
    assert resolved.llm_output.status == OutputStatus.NEEDS_CLARIFICATION
    assert resolved.recommendations is None
    assert resolved.state.user_conditions.place_types == []
    assert resolved.state.user_conditions.place_tags == []
    context = get_session_context(resolved.state.session_id, store=store)
    assert context.last_intent == "SCHEDULE"


class _ClosedOnlyWithEnoughWhenIgnoredProvider(_ClosedOnlyRecommendationProvider):
    """폐점 필터를 끄면 일정을 짤 만큼 후보가 나오는 대역.

    부모는 무시해도 1곳만 돌려줘 편성 최소 개수에 못 미친다 — 자동 전환이
    일어났는지까지만 볼 수 있다. 이 대역은 그 다음(실제로 일정이 나가는지)을
    본다.
    """

    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        self.calls.append(ignore_operating_hours)
        if not ignore_operating_hours:
            return RecommendationResponse(
                recommendations=[],
                unverified_recommendations=[],
                elapsed_ms=0,
                excluded_all_closed=True,
            )
        return RecommendationResponse(
            recommendations=[_item("closed-1"), _item("closed-2"), _item("closed-3")],
            unverified_recommendations=[],
            elapsed_ms=0,
        )


@pytest.mark.asyncio
async def test_schedule_ignores_operating_hours_without_asking() -> None:
    """심야 SCHEDULE은 되묻지 않고 폐점 필터를 끄고 한 번 더 돈다(2026-09-20).

    예전에는 "운영 중이 아닌 곳도 확인하시겠어요?"를 먼저 띄웠다. 심야에는 이
    상황이 예외가 아니라 기본값이라, 밤마다 같은 버튼을 한 번씩 눌러야 일정이
    나왔다. 답이 사실상 정해진 질문은 묻지 않고 넘어가고, 무시했다는 사실은
    편성 쪽이 basis_note로 알린다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    provider = _ClosedOnlyRecommendationProvider()
    providers["recommendation_provider"] = provider

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "SCHEDULE"
    # 폐점 필터를 켠 채 한 번, 끄고 한 번 — 사용자에게 묻는 턴이 사이에 없다.
    assert provider.calls[:2] == [False, True]
    clarification = response.llm_output.clarification
    assert clarification is None or clarification.code != "no_data_closed"
    context = get_session_context(response.state.session_id, store=store)
    assert context.pending_clarification != "no_data_closed"


@pytest.mark.asyncio
async def test_schedule_auto_ignored_operating_hours_produces_schedule() -> None:
    """자동 전환으로 후보가 확보되면 되묻기 없이 일정까지 나간다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["recommendation_provider"] = _ClosedOnlyWithEnoughWhenIgnoredProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.status is OutputStatus.COMPLETE
    assert response.schedule is not None
    assert response.schedule.items != []


@pytest.mark.asyncio
async def test_recommend_still_asks_before_showing_closed_places() -> None:
    """RECOMMEND는 그대로 묻는다 — "추천해줘"에 닫힌 곳을 말없이 내놓지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["recommendation_provider"] = _ClosedOnlyRecommendationProvider()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "RECOMMEND"
    clarification = response.llm_output.clarification
    assert clarification is not None
    assert clarification.code == "no_data_closed"


class _SlowSchedulePlanLLM(_LLMProviderWithGeneralAnswer):
    """generate_schedule_plan()이 heartbeat 간격보다 오래 걸리는 상황을 흉내 낸다.

    실사용 피드백(2026-08-13): SCHEDULE 편성 호출이 수십 초씩 걸리는데 로딩
    화면이 그동안 "장소 순서와 머무는 시간을 구성하고 있어요." 문구 하나로
    멈춰 보인다 — _await_with_heartbeat()가 이 구간에도 progress 이벤트를
    주기적으로 흘려보내는지 검증한다.
    """

    async def generate_schedule_plan(self, request):
        await asyncio.sleep(0.05)
        return await super().generate_schedule_plan(request)


@pytest.mark.asyncio
async def test_schedule_heartbeat_emits_progress_during_slow_planning() -> None:
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _SlowSchedulePlanLLM()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        stream_event_sink=sink,
        **providers,
    )

    assert response.llm_output.intent == "SCHEDULE"
    scheduling_events = [
        payload
        for event, payload in events
        if event == "progress" and payload["stage"] == "scheduling"
    ]
    # 최초 1건("장소 순서와...")은 항상 있다. heartbeat 간격(6초)보다 훨씬 짧게 재웠으니
    # 추가 heartbeat는 안 왔어야 정상 — 이 테스트는 "느릴 때 최소 1건은 보장된다"만
    # 확인하고, 실제 heartbeat 반복은 아래 단위 테스트가 별도로 검증한다.
    assert len(scheduling_events) >= 1


class _SlowClassifyIntentLLM(_LLMProviderWithGeneralAnswer):
    """classify_intent()가 heartbeat 간격보다 오래 걸리는 상황을 흉내 낸다.

    classify_intent()·extract_*()는 SCHEDULE 편성과 달리 heartbeat 없이 그냥
    await 하나로 끝난다 — "요청 의도와 조건을 파악하고 있어요." 문구 하나로 멈춘
    것처럼 보이는 구간이다. 평소엔 1~2초 안에 끝나 체감되지 않지만 외부 API
    꼬리 지연이 걸리면 그대로 무응답 공백이 된다.
    """

    async def classify_intent(self, user_input, **kwargs):
        await asyncio.sleep(0.05)
        return await super().classify_intent(user_input, **kwargs)


@pytest.mark.asyncio
async def test_interpret_heartbeat_emits_progress_during_slow_classification() -> None:
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _SlowClassifyIntentLLM()
    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    response = await run_agent_flow(
        AgentRequest(
            user_input="넌 누구야?",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        stream_event_sink=sink,
        **providers,
    )

    assert response.llm_output.intent == "GENERAL"
    interpreting_events = [
        payload
        for event, payload in events
        if event == "progress" and payload["stage"] == "interpreting"
    ]
    # 최초 1건("요청 의도와...")은 항상 있다 — heartbeat 반복 자체는
    # test_await_with_heartbeat_emits_progress_until_task_completes()가 단위로
    # 검증하므로, 여기서는 "interpreting" 단계에도 실제로 걸려 있는지만 확인한다.
    assert len(interpreting_events) >= 1


@pytest.mark.asyncio
async def test_await_with_heartbeat_emits_progress_until_task_completes() -> None:
    from app.services.runtime.agent_runtime import _await_with_heartbeat

    events: list[tuple[str, dict[str, object]]] = []

    async def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))

    async def slow_task() -> str:
        await asyncio.sleep(0.05)
        return "done"

    result = await _await_with_heartbeat(
        slow_task(),
        sink=sink,
        stage="scheduling",
        messages=("계속 진행 중이에요.",),
        interval_seconds=0.01,
    )

    assert result == "done"
    scheduling_events = [p for e, p in events if e == "progress" and p["stage"] == "scheduling"]
    assert len(scheduling_events) >= 2
    assert all(p["message"] == "계속 진행 중이에요." for p in scheduling_events)


@pytest.mark.asyncio
async def test_turn_opens_a_root_observation_so_it_stays_one_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 턴은 관측에서 **trace 하나**여야 한다 — 루트 span이 그 부모 자리다.

    2026-08-25 첫 실측에서 실제로 깨져 있었다. 속성만 전파하고(`trace_attributes`)
    루트 span을 안 만들면, 부모가 없는 observation이 저마다 자기가 trace 루트가
    되어 `classify_intent`와 `extract_recommend_conditions`가 **별도 trace**로
    올라갔다. 화면에서 "이 턴이 무슨 일을 했나"를 볼 수 없다.

    실 서버까지 확인하는 건 `scripts/verify_langfuse_tracing.py`의 기준 (e)다.
    여기서는 네트워크 없이 루트가 열리는지, 그리고 **본체보다 먼저** 열리는지만
    잡는다 — 나중에 열면 앞선 LLM 호출이 이미 밖으로 나가버린다.
    """

    opened: list[str] = []
    real_observe_step = agent_runtime_module.observe_step

    @contextmanager
    def _spy(name: str, **kwargs: object):
        opened.append(name)
        with real_observe_step(name, **kwargs) as recorder:  # type: ignore[arg-type]
            yield recorder

    monkeypatch.setattr(agent_runtime_module, "observe_step", _spy)

    providers = _providers()
    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=InMemoryStateStore(),
        **providers,
    )

    assert opened, "턴을 감싸는 루트 관측이 열리지 않았다 — trace가 조각난다."
    assert opened[0] == "agent_turn"


# --- 루트 span 요약: 목록 화면이 읽히게 한다 ---------------------------------


@pytest.mark.asyncio
async def test_turn_summary_says_what_the_turn_was() -> None:
    """루트는 SPAN이라 토큰·비용이 없다. 그래서 요약이 없으면 행에 이름과 지연만 남는다."""
    providers = _providers()
    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        **providers,
    )

    summary = summarize_turn(response)

    # 같은 객체에서 뽑은 값끼리 비교하면 항진명제다 — 기대값을 직접 적는다.
    assert summary["intent"] == "RECOMMEND"
    assert summary["status"] == "complete"
    assert summary["card_count"] > 0
    assert summary["message_length"] == len(response.message)
    # 목록 행에 뜨는 한 줄. 마스킹을 타지 않는 자리로 나간다.
    assert summary["headline"].startswith("RECOMMEND · complete · 카드 ")


@pytest.mark.asyncio
async def test_turn_summary_carries_no_utterance_or_answer_text() -> None:
    """발화도 답변도 싣지 않는다 — intent와 결과 모양만으로 목록이 읽힌다."""
    providers = _providers()
    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        **providers,
    )

    blob = json.dumps(summarize_turn(response), ensure_ascii=False)

    assert "경복궁 근처 카페 추천해줘" not in blob
    if response.message:
        assert response.message not in blob


def test_turn_summary_names_the_payload_shape() -> None:
    """카드·일정·비교·장소정보 중 무엇이 나갔는지가 headline에 드러난다."""

    class _Resp:
        recommendations = None
        schedule = object()
        comparison = None
        info_place_card = None
        message = "일정을 만들었어요."

        class llm_output:  # noqa: N801
            class intent:
                value = "SCHEDULE"

            class status:
                value = "complete"

    summary = summarize_turn(_Resp())  # type: ignore[arg-type]

    assert summary["has_schedule"] is True
    assert summary["card_count"] == 0
    assert summary["headline"] == "SCHEDULE · complete · 일정"


# --- Score: 여러 턴에 걸쳐 곡선이 되는 값만 올린다 ------------------------------


def _captured_scores(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float | bool]]:
    scores: list[tuple[str, float | bool]] = []
    monkeypatch.setattr(
        agent_runtime_module,
        "record_score",
        lambda name, value: scores.append((name, value)),
    )
    return scores


def test_turn_scores_skip_unverified_ratio_when_there_are_no_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0/0을 0.0으로 적으면 평균이 거짓말을 한다.

    "미검증이 하나도 없는 좋은 턴"과 "카드 자체가 없는 턴"이 같은 값이 되기 때문이다.
    GENERAL·INFO는 카드가 원래 없으므로 이 경로가 대부분의 턴에 걸린다.
    """
    scores = _captured_scores(monkeypatch)

    agent_runtime_module.record_turn_scores(
        {"card_count": 0, "unverified_count": 0},
    )

    assert scores == [("turn_success", True), ("card_count", 0)]


def test_turn_scores_report_the_unverified_share(monkeypatch: pytest.MonkeyPatch) -> None:
    scores = _captured_scores(monkeypatch)

    agent_runtime_module.record_turn_scores({"card_count": 4, "unverified_count": 1})

    assert scores == [("turn_success", True), ("card_count", 4), ("unverified_ratio", 0.25)]


def test_user_id_stays_off_until_the_switch_is_turned_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """개인정보를 외부 SaaS에 올리는 것은 팀 합의가 먼저다 — 코드가 먼저 들어가도 꺼짐이다.

    `capture_content`와 별개 축이라는 것도 함께 잠근다. 원문을 가려도 user_id는
    trace 속성이라 mask를 타지 않으므로, 묶어두면 "발화는 가리고 신원만 쌓는" 상태가
    실수로 만들어진다.
    """
    principal = Principal(user_id="user-abc", is_anonymous=False)
    monkeypatch.setattr(settings, "langfuse_capture_content", True)

    monkeypatch.setattr(settings, "langfuse_capture_user_id", False)
    assert agent_runtime_module._observed_user_id(principal) is None

    monkeypatch.setattr(settings, "langfuse_capture_user_id", True)
    assert agent_runtime_module._observed_user_id(principal) == "user-abc"
    assert agent_runtime_module._observed_user_id(None) is None


# --- 조건 병합 span: Audit "B 상태" 탭과 같은 값을 싣는다 ----------------------


def _state_response(**overrides: object) -> StateApplyResponse:
    from app.state.schema import UserConditions as StateUserConditions
    from app.state.service import ApiContextView

    defaults: dict[str, object] = {
        "session_id": "s-1",
        "run_id": "r-1",
        "session_created": False,
        "user_conditions": StateUserConditions(),
        "api_context": ApiContextView(),
        "condition_version": 3,
        "condition_changed": True,
    }
    defaults.update(overrides)
    return StateApplyResponse(**defaults)  # type: ignore[arg-type]


def test_merge_conditions_span_carries_the_accumulated_conditions() -> None:
    """`classify_intent` 출력은 **이번 발화에서 새로 뽑은 것**뿐이다.

    이전 턴에서 유지된 값까지 합친 최종 조건은 그동안 trace 어디에도 없었다 —
    "이번 턴이 어떤 조건으로 돌았나"에 답할 수 없었다는 뜻이다.
    """
    from app.state.schema import UserConditions as StateUserConditions
    from app.state.service import ApiContextView, AppliedOperation

    response = _state_response(
        user_conditions=StateUserConditions(budget="low", place_types=["cafe"]),
        api_context=ApiContextView(
            api_weather="맑음", gps_expired=False, gps_location="37.5796,126.977"
        ),
        applied_operations=[
            AppliedOperation(op="set", field="budget", before_value=None, after_value="low")
        ],
        excluded_place_ids=["p1", "p2"],
    )

    summary = agent_runtime_module.summarize_state_merge(response)

    assert summary["condition_version"] == 3
    assert summary["condition_changed"] is True
    conditions = summary["user_conditions"]
    assert isinstance(conditions, dict)
    assert conditions["budget"] == "low"
    assert conditions["place_types"] == ["cafe"]
    # Audit "C Tool" 탭이 보여주던 날씨 캐시·만료 플래그도 여기 들어온다.
    # **좌표는 값 대신 유무만** 남는다 — "GPS가 없어서 못 했다"와 "있었는데 다른
    # 이유"는 구분돼야 하지만 그건 유무로 갈리지 좌표 값으로 갈리지 않는다.
    assert summary["api_context"] == {
        "has_gps_location": True,
        "api_weather": "맑음",
        "gps_expired": False,
        "weather_expired": True,
        "gps_location_confirmed_at": None,
    }
    assert "37.5796" not in json.dumps(summary, ensure_ascii=False)
    assert summary["applied_operations"][0]["field"] == "budget"
    assert summary["excluded_place_count"] == 2


def test_merge_conditions_span_keeps_the_place_names_that_are_not_coordinates() -> None:
    """`current_location`·`search_center`는 좌표가 아니라 발화에서 온 지명이다.

    이 둘까지 빼면 "무슨 조건으로 돌았나"에 답할 수 없어 span을 여는 이유가 없어진다.
    """
    from app.state.schema import UserConditions as StateUserConditions

    summary = agent_runtime_module.summarize_state_merge(
        _state_response(
            user_conditions=StateUserConditions(current_location="홍대", search_center="경복궁")
        )
    )

    conditions = summary["user_conditions"]
    assert isinstance(conditions, dict)
    assert conditions["current_location"] == "홍대"
    assert conditions["search_center"] == "경복궁"


def test_merge_conditions_span_says_why_an_operation_was_ignored() -> None:
    """적용된 것만 보면 "왜 내 말이 반영이 안 됐지"에 답할 수 없다."""
    from app.state.operations import IgnoredOperation

    response = _state_response(
        ignored_operations=[
            IgnoredOperation(
                operation={"op": "set", "field": "budget", "value": "무한대"},
                reason="invalid_value",
            )
        ]
    )

    summary = agent_runtime_module.summarize_state_merge(response)

    ignored = summary["ignored_operations"]
    assert isinstance(ignored, list)
    assert ignored[0]["reason"] == "invalid_value"


def test_merge_conditions_headline_survives_the_content_switch() -> None:
    """`status_message`는 mask를 타지 않는다 — 원문 수집을 꺼도 목록에서 읽혀야 한다.

    그래서 여기에는 좌표도 조건 값도 넣지 않는다. 넣으면 스위치와 무관하게 나간다.
    """
    from app.state.schema import UserConditions as StateUserConditions

    headline = agent_runtime_module._state_merge_headline(
        _state_response(
            user_conditions=StateUserConditions(budget="low"),
            condition_changed=False,
            reset_applied="soft",
        )
    )

    assert headline == "조건 v3 · 유지 · 적용 0 · 무시 0 · reset:soft"
    assert "low" not in headline


# --- 실패한 턴: 무엇이 터졌는지 span에 남긴다 ---------------------------------


def test_failure_attributes_keep_the_error_code_outside_the_mask() -> None:
    """오류 코드가 `capture_content`에 걸리면 원문 수집을 끈 배포에서 못 읽는다.

    그건 이 관측이 있는 이유 자체라, 코드는 mask를 안 타는 `status_message`에도 적는다.
    """
    from app.errors import ProviderTimeoutError

    attributes = agent_runtime_module._failure_attributes(
        ProviderTimeoutError("kakao_local"),
    )

    assert attributes["level"] == "ERROR"
    assert attributes["status_message"] == "provider_timeout · retryable=True"
    assert attributes["output"] == {
        "error_code": "provider_timeout",
        "retryable": True,
        "status_code": 504,
        "provider": "kakao_local",
    }


def test_failure_attributes_do_not_carry_an_unexpected_errors_message() -> None:
    """어디서 터졌느냐에 따라 발화나 좌표가 예외 메시지에 섞여 들어올 수 있다.

    `status_message`는 스위치와 무관하게 나가는 자리라 클래스 이름만 적는다.
    """
    attributes = agent_runtime_module._failure_attributes(
        ValueError("경복궁 근처 37.5796,126.977 처리 실패"),
    )

    assert attributes["status_message"] == "ValueError"
    assert attributes["output"] == {"error_code": "ValueError", "retryable": False}
    assert "경복궁" not in json.dumps(attributes, ensure_ascii=False)


@pytest.mark.asyncio
async def test_condition_merge_opens_its_own_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """B를 부르는 단계에 관측이 없어서 최종 조건이 trace 어디에도 없었다.

    루트(`agent_turn`)보다 뒤, Tool 단계보다 앞이어야 한다 — 이 순서가 뒤집히면
    "무슨 조건으로 조회했나"를 시간순으로 읽을 수 없다.
    """
    opened: list[str] = []
    real_observe_step = agent_runtime_module.observe_step

    @contextmanager
    def _spy(name: str, **kwargs: object):
        opened.append(name)
        with real_observe_step(name, **kwargs) as recorder:  # type: ignore[arg-type]
            yield recorder

    monkeypatch.setattr(agent_runtime_module, "observe_step", _spy)

    providers = _providers()
    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=InMemoryStateStore(),
        **providers,
    )

    assert "merge_conditions" in opened
    assert opened.index("agent_turn") < opened.index("merge_conditions")


# --- 루트 span의 입력: 무슨 발화였나 ------------------------------------------


def test_turn_input_carries_the_utterance_but_not_coordinates() -> None:
    """발화는 `input`에 싣는다 — 그 자리는 mask를 타서 스위치를 우회하지 않는다.

    좌표는 다르다. `device_location`은 팀원이 테스트하는 자리의 실좌표라, 켜고 끄는
    스위치 하나에 맡기지 않고 **있고 없음만** 남긴다(2026-08-26 결정).
    """
    payload = agent_runtime_module._turn_input(
        AgentRequest(user_input="경복궁 근처 카페 추천해줘", device_location="37.5796,126.9770")
    )

    assert payload["user_input"] == "경복궁 근처 카페 추천해줘"
    assert payload["language"] == "ko"
    assert payload["has_device_location"] is True

    blob = json.dumps(payload, ensure_ascii=False)
    assert "37.5" not in blob
    assert "126.9" not in blob
    assert "device_location" not in payload


def test_turn_input_omits_optional_fields_that_were_not_sent() -> None:
    """빈 값을 다 적으면 실제로 채워진 턴과 아닌 턴이 화면에서 구분되지 않는다."""
    payload = agent_runtime_module._turn_input(AgentRequest(user_input="안녕"))

    assert set(payload) == {"user_input", "language", "has_device_location"}
    assert payload["has_device_location"] is False


def test_turn_input_records_the_paths_that_skip_intent_classification() -> None:
    """이 둘이 채워진 턴은 `classify_intent`를 건너뛴다.

    그래서 span이 안 보이는 게 정상인 턴과 이상한 턴을 여기서 가른다.
    """
    payload = agent_runtime_module._turn_input(
        AgentRequest(
            user_input="운영 중이 아닌 곳도 볼게요",
            clarification_choice="ignore_operating_hours",
            travel_origin_override=TravelOrigin.USER_LOCATION,
        )
    )

    assert payload["clarification_choice"] == "ignore_operating_hours"
    assert payload["travel_origin_override"] == TravelOrigin.USER_LOCATION.value


def test_turn_input_records_schedule_from_saved() -> None:
    """보관함 CTA도 분류를 건너뛰는 경로라 감사 payload에 남아야 한다."""
    payload = agent_runtime_module._turn_input(
        AgentRequest(user_input="이 장소들로 일정 짜기", schedule_from_saved=True)
    )

    assert payload["schedule_from_saved"] is True

    # 값이 없으면 키 자체를 넣지 않는다 — 평소 턴의 payload를 넓히지 않기 위함이다.
    plain = agent_runtime_module._turn_input(AgentRequest(user_input="카페 추천해줘"))
    assert "schedule_from_saved" not in plain


# --- TP-180: SCHEDULE 턴의 제외 목록 되살리기 -----------------------------------
# "이 장소들로 일정 짜줘"가 방금 추천한 장소를 오히려 제외한 채 일정을 짜던 문제.
# 제외 목록(recommended ∪ rejected ∪ closed_excluded)에 직전 추천분이 들어 있고,
# SCHEDULE은 후보를 새로 채점하면서 그 목록을 그대로 적용해 사용자가 방금 본 장소가
# 후보에서 통째로 빠졌다.


def test_schedule_revives_shown_places_from_exclusion() -> None:
    """SCHEDULE 턴에서는 마지막 run의 노출분이 제외 목록에서 빠진다."""

    result = _effective_excluded_place_ids(
        ["p1", "p2", "p3"],
        shown_place_ids=["p1", "p2"],
        is_schedule=True,
    )

    assert result == ["p3"]


def test_schedule_keeps_rejected_places_excluded() -> None:
    """노출분이 아닌 제외 대상(거절·폐점)은 SCHEDULE 턴에서도 계속 제외된다.

    되살리는 것은 "방금 보여준 것"뿐이다 — 사용자가 명시적으로 거절한 장소까지
    후보로 돌아오면 이번 수정이 REJECT 이력을 무력화하게 된다.
    """

    result = _effective_excluded_place_ids(
        ["shown1", "rejected1", "closed1"],
        shown_place_ids=["shown1"],
        is_schedule=True,
    )

    assert result == ["rejected1", "closed1"]


def test_recommend_turn_keeps_exclusion_intact() -> None:
    """RECOMMEND 반복 흐름은 영향을 받지 않는다 — 중복 추천 방지가 그대로 산다."""

    result = _effective_excluded_place_ids(
        ["p1", "p2"],
        shown_place_ids=["p1", "p2"],
        is_schedule=False,
    )

    assert result == ["p1", "p2"]


def test_exclusion_unchanged_when_nothing_shown() -> None:
    """첫 턴처럼 노출 이력이 없으면 그대로 둔다."""

    assert _effective_excluded_place_ids(["p1"], shown_place_ids=[], is_schedule=True) == ["p1"]


def test_exclusion_order_is_preserved() -> None:
    """제외 목록의 순서를 뒤집지 않는다 — 호출부가 순서에 의미를 두지 않더라도
    diff와 로그를 읽을 때 원본 순서가 유지되는 편이 낫다."""

    result = _effective_excluded_place_ids(
        ["a", "b", "c", "d"],
        shown_place_ids=["c"],
        is_schedule=True,
    )

    assert result == ["a", "b", "d"]


def _llm_output(*, modify: object) -> object:
    """_revivable_place_ids()가 보는 필드(modify)만 가진 최소 스텁."""

    class _Stub:
        pass

    stub = _Stub()
    stub.modify = modify
    return stub


def _session_context_stub(shown: list[str], saved: list[str] | None = None) -> object:
    class _Stub:
        pass

    class _Saved:
        def __init__(self, place_id: str) -> None:
            self.place_id = place_id

    stub = _Stub()
    stub.shown_place_ids = shown
    stub.saved_places = [_Saved(place_id) for place_id in (saved or [])]
    return stub


def test_new_schedule_turn_revives_shown_places() -> None:
    """새 SCHEDULE 턴("이 장소들로 일정 짜줘")은 직전 노출분을 되살린다."""

    assert _revivable_place_ids(
        _llm_output(modify=None), _session_context_stub(["p1", "p2"])
    ) == ["p1", "p2"]


def test_replan_turn_does_not_revive_shown_places() -> None:
    """재조정 턴은 직전 노출분을 되살리지 않는다 — 방금 거절한 장소가 다시 나오면 안 된다.

    "다른 곳 보여줘"(REJECT_ALL)·"두 번째는 별로야"(REJECT_SPECIFIC)는 MODIFY로
    분류된 뒤 SCHEDULE로 relabel되므로 is_schedule만으로는 새 일정 요청과 구분되지
    않는다. 거절 대상이 shown_place_ids에도 남아 있어, 구분 없이 되살리면 REJECT
    이력이 무력화된다.
    """

    assert (
        _revivable_place_ids(
            _llm_output(modify=object()), _session_context_stub(["p1", "p2"])
        )
        == []
    )


def test_saved_places_are_revived_on_new_schedule_turn() -> None:
    """보관함은 shown과 합집합으로 되살아난다 (SCHEDULE-12).

    shown_place_ids는 마지막 run만 담아, 3턴 전에 담은 장소는 여기 없다.
    """

    assert _revivable_place_ids(
        _llm_output(modify=None), _session_context_stub(["p1"], saved=["p9"])
    ) == ["p1", "p9"]


def test_saved_places_are_revived_even_on_replan_turn() -> None:
    """재조정 턴에도 보관함은 되살린다 — 사용자가 명시적으로 담아둔 것이다.

    "두 번째는 별로야"가 담아둔 나머지까지 후보에서 뺄 이유가 없다. 거절과
    겹칠 걱정은 record_rejected()가 보관함에서 자동으로 빼므로 없다.
    """

    assert _revivable_place_ids(
        _llm_output(modify=object()), _session_context_stub(["p1", "p2"], saved=["p9"])
    ) == ["p9"]


def test_empty_saved_places_keeps_previous_behaviour() -> None:
    """보관함이 비어 있으면 TP-180 동작과 완전히 같다."""

    assert _revivable_place_ids(
        _llm_output(modify=None), _session_context_stub(["p1", "p2"], saved=[])
    ) == ["p1", "p2"]


# ---------------------------------------------------------------- 대화층 3·4단계


@pytest.mark.asyncio
async def test_situational_general_turn_offers_a_button_and_records_pending_offer() -> None:
    """상황 발화가 GENERAL로 끝나면 답변에 제안이 붙고, 버튼(suggested_follow_ups)이
    나가고, 세션에 pending_offer가 남는다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)

    response = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    assert response.llm_output.intent == "GENERAL"
    assert "이동이 짧고 쉬기 편한 곳" in response.message
    assert response.suggested_follow_ups == ["이동이 짧고 쉬기 편한 곳 찾아줘"]

    context = get_session_context(response.state.session_id, store=store)
    assert context.situation_state is not None
    assert context.situation_state.pending_offer == "fatigue"
    assert len(context.recent_turns) == 1
    assert context.recent_turns[0].user_input == "너무 지친다"
    assert context.recent_turns[0].intent == "GENERAL"
    assert context.recent_turns[0].offered_action == "recommend_nearby_rest_place"


@pytest.mark.asyncio
async def test_second_turn_passes_saved_history_to_classify_and_extract() -> None:
    """두 번째 자연어 턴은 첫 턴을 user/model 역할 이력으로 LLM에 전달한다."""
    store = InMemoryStateStore()
    providers = _providers()
    llm = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)
    providers["llm"] = llm

    first = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="그냥 잠깐 쉬고 싶어",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert llm.classify_histories[0] is None
    history = llm.classify_histories[1]
    assert history is not None
    assert len(history) == 1
    assert history[0].user_input == "너무 지친다"
    assert "제안한 기능: recommend_nearby_rest_place" in history[0].assistant_summary
    assert llm.extract_histories[1] == history

    context = get_session_context(first.state.session_id, store=store)
    assert [turn.user_input for turn in context.recent_turns] == [
        "너무 지친다",
        "그냥 잠깐 쉬고 싶어",
    ]


@pytest.mark.asyncio
async def test_history_model_turn_carries_the_answer_text_and_the_trace() -> None:
    """model 쪽 이력에 **화면에 나간 답변 문장**과 처리 기록이 함께 실려야 한다.

    처음에는 처리 기록만 담았는데, 그러면 모델이 "내가 방금 뭐라고 말했는지"를 알 수
    없어 답변이 앞 턴과 어긋났다(2026-08-31 실사용). 강의교재 36강도 model 답변을
    이력에 넣는 것을 멀티턴의 핵심으로 든다. 답변 문장이 먼저, 처리 기록이 꼬리다 —
    순서가 뒤집히면 모델이 내부 문구를 사용자에게 흘릴 위험이 커진다.
    """
    store = InMemoryStateStore()
    providers = _providers()
    llm = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)
    providers["llm"] = llm

    first = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )
    await run_agent_flow(
        AgentRequest(
            user_input="그냥 잠깐 쉬고 싶어",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    summary = llm.classify_histories[1][0].assistant_summary
    assert summary is not None
    # 화면에 나간 문장이 그대로 앞에 온다.
    assert summary.startswith(first.message)
    # 처리 기록은 뒤에 괄호로 붙어 분류 신호도 함께 유지된다.
    assert "(처리 기록 — " in summary
    assert "처리 의도: GENERAL" in summary

    # 세션에도 답변 문장이 남는다(다음 턴이 이 값을 읽는다).
    context = get_session_context(first.state.session_id, store=store)
    assert context.recent_turns[0].assistant_message == first.message


@pytest.mark.asyncio
async def test_bare_accept_resolves_to_recommend_without_reclassifying() -> None:
    """"응"은 LLM을 다시 부르지 않고 결정적으로 RECOMMEND + 제안 조건이 된다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)

    first = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    # 두 번째 턴은 GENERAL을 강제하는 더블을 쓰지 않는다 — accept 경로가 결정적
    # 해소로 classify_intent()를 건너뛴다는 것 자체를 이 double 교체로 증명한다.
    # (만약 정말로 재분류를 탄다면, FakeLLMProvider가 "응"을 GENERAL로 보내
    # RECOMMEND가 나오지 않는다.)
    second_providers = _providers()
    second = await run_agent_flow(
        AgentRequest(
            user_input="응", session_id=first.state.session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **second_providers,
    )

    assert second.llm_output.intent == "RECOMMEND"
    assert second.state.user_conditions.max_travel_time == 15

    context = get_session_context(second.state.session_id, store=store)
    assert context.situation_state is not None
    assert context.situation_state.pending_offer is None


@pytest.mark.asyncio
async def test_bare_reject_records_rejection_and_offer_is_not_repeated() -> None:
    """"아니"는 고정 문구로 끝나고, 같은 제안은 이 세션에서 다시 나오지 않는다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)

    first = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    second = await run_agent_flow(
        AgentRequest(
            user_input="아니", session_id=first.state.session_id, device_location=DEVICE_LOCATION
        ),
        store=store,
        **providers,
    )

    assert second.message == "네, 필요하시면 언제든 말씀해주세요."
    context = get_session_context(second.state.session_id, store=store)
    assert context.situation_state is not None
    assert "recommend_nearby_rest_place" in context.situation_state.rejected_actions
    assert context.situation_state.pending_offer is None

    # 같은 상황이 다시 감지돼도 이미 거절한 제안은 다시 권하지 않는다.
    third = await run_agent_flow(
        AgentRequest(
            user_input="또 지친다",
            session_id=second.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )
    assert "이동이 짧고 쉬기 편한 곳" not in third.message
    # 거절당한 그 제안 버튼만 다시 안 뜬다 — 후속 질문 자체를 아예 끄는 것은
    # 아니다(그건 별개의 일반 후속 질문 제안, follow_up_suggester가 맡는다).
    assert "이동이 짧고 쉬기 편한 곳 찾아줘" not in third.suggested_follow_ups


@pytest.mark.asyncio
async def test_unmatched_utterance_after_offer_falls_back_to_normal_classification() -> None:
    """제안 뒤 "응"·"아니" 어느 쪽도 아니면 정상 분류 경로로 안전하게 폴백한다."""
    store = InMemoryStateStore()
    providers = _providers()
    providers["llm"] = _LLMProviderWithSituationalOffer(SituationKind.FATIGUE)

    first = await run_agent_flow(
        AgentRequest(user_input="너무 지친다", session_id=None, device_location=DEVICE_LOCATION),
        store=store,
        **providers,
    )

    fallback_providers = _providers()
    second = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=first.state.session_id,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **fallback_providers,
    )

    assert second.llm_output.intent == "RECOMMEND"
    assert second.state.user_conditions.max_travel_time != 15


@pytest.mark.asyncio
async def test_staged_recommendation_refill_passes_resolved_search_center(
    refill_page_limit: int,
) -> None:
    """보충 조회는 첫 조회가 확정한 기준점을 넘긴다.

    그래야 C가 위치 해석·날씨·공휴일을 건너뛰고 장소만 다시 준다. 그 셋의 결과는
    보충 배치에서 어차피 버려지므로(_merge_recommendation_context_places가 첫 배치
    값을 그대로 쓴다) 계산하지 않는 것뿐이다. 실측으로 보충 1회의 외부 호출이
    7건에서 2건으로 준다.
    """

    store = InMemoryStateStore()
    tool_provider = _RefillPlacesToolProvider()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=tool_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    first, *refills = tool_provider.requests
    assert refills, "보충 조회가 돌아야 이 테스트가 뜻이 있다"
    # 첫 조회는 기준점을 모른다 — C가 해석해야 한다.
    assert first.resolved_search_center is None
    # 보충은 전부 첫 조회가 확정한 좌표를 그대로 넘긴다.
    for refill in refills:
        assert refill.resolved_search_center is not None


@pytest.mark.asyncio
async def test_measured_routes_are_requested_only_for_the_shortlist(
    refill_page_limit: int,
) -> None:
    """실측 도보는 1차 채점 상위 후보에만 조회한다.

    `_fetch_travel_routes()`가 목적지마다 요청을 쏘므로(walking_route.py) 후보 전량에
    붙이면 호출이 후보 수에 정비례한다. 결과에 나가는 것은 5곳뿐인데 나머지 몫까지
    치를 이유가 없다 — 후보 상한을 30으로 올렸을 때 카카오 호출이 7~13건에서
    25~35건이 됐다.
    """

    # 25곳 전부를 영업 중으로 둬서 하드 필터 통과 후보가 상위 목록보다 많게 만든다.
    context_provider = _RefillPlacesToolProvider(open_indexes=set(range(25)))
    route_tool = _RecordingTravelRouteTool()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=context_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert len(route_tool.queries) == 1
    requested = route_tool.queries[0].destinations
    assert len(requested) == _MEASURED_ROUTE_CANDIDATE_LIMIT


@pytest.mark.asyncio
async def test_measured_routes_cover_every_candidate_when_pool_is_small(
    refill_page_limit: int,
) -> None:
    """통과 후보가 상위 목록보다 적으면 전부 실측한다 — 좁히기가 손해가 아니다."""

    context_provider = _RefillPlacesToolProvider(open_indexes={0, 1, 2})
    route_tool = _RecordingTravelRouteTool()

    await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=context_provider,
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        travel_route_tool=route_tool,
        store=InMemoryStateStore(),
    )

    assert len(route_tool.queries) == 1
    requested = {item.place_id for item in route_tool.queries[0].destinations}
    assert requested == {"refill-0", "refill-1", "refill-2"}


# ---------------------------------------------- 좌표 스냅샷 폴백 (SCHEDULE-12)


class _SavedStub:
    def __init__(self, place_id: str, latitude: float | None, longitude: float | None) -> None:
        self.place_id = place_id
        self.latitude = latitude
        self.longitude = longitude


class _ShownStub(_SavedStub):
    pass


def _coordinate_context(shown: list[_ShownStub], saved: list[_SavedStub]) -> object:
    class _Stub:
        pass

    stub = _Stub()
    stub.shown_recommendations = shown
    stub.saved_places = saved
    return stub


def test_snapshot_coordinates_reads_both_sources() -> None:
    context = _coordinate_context(
        [_ShownStub("p1", 37.1, 127.1)],
        [_SavedStub("p9", 37.9, 127.9)],
    )

    assert _snapshot_coordinates(context) == {
        "p1": (37.1, 127.1),
        "p9": (37.9, 127.9),
    }


def test_snapshot_coordinates_skips_missing_values() -> None:
    """좌표 도입 이전 세션과 C 컨텍스트를 안 거친 기록은 None으로 남는다."""

    context = _coordinate_context(
        [_ShownStub("p1", None, None)],
        [_SavedStub("p9", 37.9, None)],
    )

    assert _snapshot_coordinates(context) == {}


def test_snapshot_coordinates_prefers_saved_over_shown() -> None:
    """같은 place_id면 보관함 쪽을 쓴다 — 사용자가 명시적으로 고른 것이다."""

    context = _coordinate_context(
        [_ShownStub("p1", 37.1, 127.1)],
        [_SavedStub("p1", 37.5, 127.5)],
    )

    assert _snapshot_coordinates(context) == {"p1": (37.5, 127.5)}


def _pairwise_candidate(place_id: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=f"장소 {place_id}",
        category="attraction",
        distance_km=0.3,
        remaining_minutes=120,
        environment_type="indoor",
        recommendation_reason="테스트용 고정 후보입니다.",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


def test_pairwise_distances_use_snapshot_when_context_lacks_place() -> None:
    """이번 턴 C 응답에 없는 보관함 장소도 B 스냅샷으로 거리를 잰다.

    폴백이 없으면 그 쌍이 조용히 빠져 LLM이 거리 근거 없이 동선을 짠다.
    """

    candidates = [_pairwise_candidate("p1"), _pairwise_candidate("p9")]

    without_fallback = _build_pairwise_distances_km(candidates, [])
    with_fallback = _build_pairwise_distances_km(
        candidates,
        [],
        fallback_coordinates={"p1": (37.5796, 126.9770), "p9": (37.4979, 127.0276)},
    )

    assert without_fallback == {}
    assert ("p1", "p9") in with_fallback
    assert with_fallback[("p1", "p9")] > 8.0


def test_pairwise_distances_prefer_context_over_snapshot() -> None:
    """C 응답이 있으면 그쪽을 쓴다 — 최신값이고 같은 턴 후보끼리 출처가 일관된다."""

    candidates = [_pairwise_candidate("p1"), _pairwise_candidate("p2")]
    places = [
        PlaceCandidate(
            place_id="p1",
            name="장소 p1",
            category="attraction",
            location=Coordinates(latitude=37.5796, longitude=126.9770),
        ),
        PlaceCandidate(
            place_id="p2",
            name="장소 p2",
            category="attraction",
            location=Coordinates(latitude=37.5800, longitude=126.9780),
        ),
    ]

    from_context = _build_pairwise_distances_km(candidates, places)
    with_bogus_fallback = _build_pairwise_distances_km(
        candidates,
        places,
        fallback_coordinates={"p1": (0.0, 0.0), "p2": (10.0, 10.0)},
    )

    assert from_context == with_bogus_fallback


@pytest.mark.parametrize("use_graph", [False, True])
@pytest.mark.asyncio
async def test_refilled_candidates_reach_schedule_with_coordinates(
    refill_page_limit: int,
    monkeypatch: pytest.MonkeyPatch,
    use_graph: bool,
) -> None:
    """TP-198: 보충 조회로 들어온 후보의 좌표가 일정 편성까지 간다.

    `_score_recommendations()`가 보충 후보를 `tool_context`에 합치고도 그 값을
    돌려주지 않던 동안, 일정 편성은 **합치기 전** 컨텍스트를 받았다. 후보는 합친
    목록에서 뽑고 좌표는 합치기 전 목록에서 찾는 상태라, 보충으로 들어온 장소만
    `_build_pairwise_distances_km()`에서 조용히 건너뛰어졌다.

    거리 근거가 빠져도 편성은 성공하므로 응답만 봐서는 드러나지 않는다. 그래서
    planner에 실제로 넘어간 `pairwise_distances_km`를 직접 본다.

    직접 호출과 그래프 두 경로를 다 돈다 — 합친 컨텍스트를 넘기는 자리가 경로마다
    달라서(호출부 인자 / 노드 반환 키), 한쪽만 고치면 나머지가 조용히 남는다.
    """
    monkeypatch.setattr(settings, "use_langgraph_pipeline", use_graph)
    captured: list[SchedulePlanningRequest] = []
    real_plan_schedule = agent_runtime_module.plan_schedule

    async def capturing_plan_schedule(request, llm, **kwargs):
        captured.append(request)
        return await real_plan_schedule(request, llm, **kwargs)

    monkeypatch.setattr(agent_runtime_module, "plan_schedule", capturing_plan_schedule)

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 반나절 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=InMemoryStateStore(),
    )

    assert response.schedule is not None
    [schedule_request] = captured

    candidate_ids = [item.place_id for item in schedule_request.candidates]
    # 첫 페이지는 refill-0~9다. 그 뒤 번호는 보충 조회로만 들어올 수 있다.
    refilled_ids = [
        place_id
        for place_id in candidate_ids
        if int(place_id.removeprefix("refill-")) >= _REFILL_PAGE_SIZE
    ]
    # 보충 후보가 하나도 안 뽑히면 이 테스트는 아무것도 검증하지 못한다.
    assert refilled_ids

    # 보충 후보가 낀 쌍이 거리 근거에 들어가 있어야 한다. 좌표를 못 찾으면 그
    # 장소가 낀 쌍이 통째로 사라진다.
    paired_ids = {
        place_id for pair in schedule_request.pairwise_distances_km for place_id in pair
    }
    assert set(refilled_ids) <= paired_ids

    # 좌표를 가진 후보끼리는 모든 쌍이 나온다 — 하나라도 빠지면 위 조건만으로는
    # 놓치는 부분 누락이 있다는 뜻이다.
    expected_pairs = len(candidate_ids) * (len(candidate_ids) - 1) // 2
    assert len(schedule_request.pairwise_distances_km) == expected_pairs


@pytest.mark.parametrize("use_graph", [False, True])
@pytest.mark.asyncio
async def test_refilled_candidates_are_recorded_with_coordinates(
    refill_page_limit: int,
    monkeypatch: pytest.MonkeyPatch,
    use_graph: bool,
) -> None:
    """TP-198: 보충 조회로 들어온 후보의 좌표가 노출 이력에도 남는다(D-114 후속).

    이 스냅샷은 다음 턴의 안전망이다 — 보관함에 담긴 장소가 그때 검색 반경 밖이면
    C 응답에 아예 없어서, `_snapshot_coordinates()`가 꺼내는 이 값이 후보 간 거리를
    구할 유일한 근거가 된다.

    좌표가 안 남아도 그 턴은 멀쩡하고 **다음 턴에** 그 장소만 거리 근거 없이
    등장하므로, 응답을 봐서는 드러나지 않는다.
    """
    monkeypatch.setattr(settings, "use_langgraph_pipeline", use_graph)
    store = InMemoryStateStore()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처 카페 추천해줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        llm=_LLMProviderWithGeneralAnswer(),
        tool_provider=_RefillPlacesToolProvider(),
        recommendation_provider=RealRecommendationProvider(),
        enrichment_provider=_CountingEnrichmentProvider(),
        store=store,
    )

    assert response.recommendations is not None
    shown_ids = [
        item.place_id
        for item in (
            *response.recommendations.recommendations,
            *response.recommendations.unverified_recommendations,
        )
    ]
    # 첫 페이지는 refill-0~9다. 그 뒤 번호는 보충 조회로만 들어올 수 있다.
    refilled_ids = {
        place_id
        for place_id in shown_ids
        if int(place_id.removeprefix("refill-")) >= _REFILL_PAGE_SIZE
    }
    # 보충 후보가 하나도 안 뽑히면 이 테스트는 아무것도 검증하지 못한다.
    assert refilled_ids

    session = get_session_context(response.state.session_id, store=store)
    assert refilled_ids <= set(_snapshot_coordinates(session))


@pytest.mark.asyncio
async def test_schedule_turn_records_quality_metrics() -> None:
    """SCHEDULE 턴 한 번에 지표 trace 행이 하나 남는다. (TP-242)

    **기존 단계에 얹지 않는다** — 단계별 지연시간을 보는 화면이 도메인 지표에
    오염된다. 그래서 step 이름이 따로 있고, 이 테스트가 그 분리를 잠근다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 3시간 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.schedule is not None

    traces = store.get_traces(response.state.session_id)
    quality = [trace for trace in traces if trace.step == "schedule_quality"]
    assert len(quality) == 1

    metrics = quality[0].metrics
    assert metrics is not None
    assert metrics["item_count"] == len(response.schedule.items)
    assert metrics["item_capacity"] == response.schedule.item_capacity
    assert metrics["total_duration_min"] == response.schedule.total_duration_min
    assert metrics["walkable_within_min"] == 5

    # 다른 단계는 지표를 싣지 않는다.
    assert all(trace.metrics is None for trace in traces if trace.step != "schedule_quality")


@pytest.mark.asyncio
async def test_schedule_quality_metrics_carry_no_user_text() -> None:
    """지표에 장소 이름이 들어가지 않는다. (TP-242)

    **trace_records를 대화 삭제 때 안 지우는 근거가 "사용자 텍스트가 없다"는
    것이다.** 이름을 실으면 그 근거가 무너지고 보관 규칙까지 다시 봐야 한다.
    단위 테스트는 고정 입력으로 확인하지만, 이 테스트는 실제 편성 결과의
    이름들이 새어나가지 않는지 본다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 3시간 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.schedule is not None
    quality = [
        trace
        for trace in store.get_traces(response.state.session_id)
        if trace.step == "schedule_quality"
    ]
    rendered = repr(quality[0].metrics)

    for item in response.schedule.items:
        assert item.place_name not in rendered
        assert item.place_id not in rendered
    assert "경복궁" not in rendered


@pytest.mark.asyncio
async def test_schedule_turn_survives_metrics_record_failure() -> None:
    """지표 기록이 실패해도 사용자 응답은 정상으로 나간다. (TP-242)

    기존 trace 기록이 예외를 흡수하는 것과 같은 규칙이다. 지표는 관측이고,
    관측이 기능을 막으면 안 된다.
    """
    store = InMemoryStateStore()
    providers = _providers()

    original = store.append_traces

    def _fail_on_quality(records):
        if any(record.step == "schedule_quality" for record in records):
            raise RuntimeError("지표 저장 실패(테스트)")
        original(records)

    store.append_traces = _fail_on_quality  # type: ignore[method-assign]

    response = await run_agent_flow(
        AgentRequest(
            user_input="경복궁 근처에서 3시간 코스 짜줘",
            session_id=None,
            device_location=DEVICE_LOCATION,
        ),
        store=store,
        **providers,
    )

    assert response.schedule is not None
    assert "코스를 짜봤어요" in response.message

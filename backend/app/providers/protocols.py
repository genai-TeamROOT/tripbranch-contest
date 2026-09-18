"""TripBranch provider 계층의 인터페이스 정의.

역할: 외부 시스템(LLM·지도·날씨·관광 API 등)을 다루는 provider가 구현해야 할
      메서드 계약을 표현한다.
입력: provider별 조회 조건(사용자 발화, 좌표, 지역 코드, 날짜 등).
출력: 도메인 모델 또는 ProviderResult로 감싼 조회 결과.
호출 시점: factory가 Fake/Real provider를 주입할 때 타입 계약으로 사용된다.
TODO: provider가 늘어나면 오류 타입, 비동기 계약, 메타데이터 계약을 분리한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from app.domain.models import (
    AccessibilityNeed,
    AccessibilityVerdict,
    ConcentrationResult,
    GeocodeResult,
    HolidayResult,
    LocalSearchPlace,
    MunicipalParkingStatus,
    PlaceBarrierFreeDetails,
    PlaceCategoryFilter,
    PlaceCommonDetails,
    PlaceDetails,
    PlaceOperatingDetails,
    PlacePhoto,
    PublicToilet,
    RealtimeCityDataResult,
    RealtimeCommercialResult,
    TourPlacePage,
    WeatherForecastResult,
)
from app.domain.schedule_travel import ModeJudgmentContext, SegmentModeInput
from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    TravelMode,
    TravelRouteBatch,
)
from app.place_search_policy import DEFAULT_PLACE_PROVIDER_RESULT_LIMIT
from app.providers.contracts import ProviderResult
from app.providers.festival import FestivalEvent
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
    PlaceCandidate,
    PlacePreferenceInsight,
    RecommendationResponse,
    UserConditions,
)


class LLMProvider(Protocol):
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
        """사용자 발화의 Intent를 1단계로 판정한다.

        pending_clarification/last_intent는 직전 턴이 되묻기로 끝났는지와 그 되묻기가
        어떤 Intent의 턴이었는지를 알려준다 — SCHEDULE 되묻기 답변이 새 MODIFY 요청으로
        오분류되는 걸 막는 데 쓰인다(D-059).
        shown_place_names는 SCHEDULE-09 후속(이름 지목)에서 추가됐다 — "두가헌
        레스토랑은 빼줘"처럼 순번 없이 노출된 항목 이름만으로 특정 대상을 지목해도
        MODIFY로 판정할 근거가 된다.
        conversation_place_name은 직전 INFO 상세 카드의 장소명이다. "여기 가는데
        얼마나 걸려?"처럼 대화 속 장소를 지시하는 정보 질문을 INFO로 판정하는
        근거로만 쓴다.
        """
        ...

    async def extract_recommend_conditions(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
        retry_models: list[str] | None = None,
    ) -> ProviderResult[LLMOutput]:
        """RECOMMEND 발화에서 UserConditions를 추출한다.

        retry_models를 주면 기본 fast 묶음 대신 그 목록으로 호출한다 — 조건
        페이로드가 빈손으로 온 턴을 다시 뽑을 때만 쓴다(TP-266). 구현체가
        모델 개념을 갖지 않으면(stub) 무시해도 된다.
        """
        ...

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
        """MODIFY 발화에서 modify_type과 condition_changes를 추출한다.

        위치 되묻기 답변이면 pending_clarification이 전달돼, 단순 지명을
        search_center 변경으로 해석한다.
        shown_place_count는 SCHEDULE-09(부분 수정)에서 추가됐다 —
        REJECT_SPECIFIC의 target_indices가 노출 범위를 벗어나는지 판별한다.
        shown_place_names는 SCHEDULE-09 후속(이름 지목)에서 추가됐다 — rank 순
        이름 목록으로, "두가헌 레스토랑은 빼줘"처럼 순번이 아니라 이름으로
        지목했을 때 target_indices를 이름→순번으로 매칭한다.
        """
        ...

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
        """INFO 발화에서 장소/질문 정보를 추출한다.

        reference_date: "오늘"/"내일"/"이번 주말" 등 concentration 질의의
        visit_time을 실제 날짜로 환산하는 기준일(KST). concentration-conditions.md §3.2.
        conversation_place_name: 직전 INFO 카드의 장소명. "여기/이곳" 같은
        from_conversation 지시어가 있을 때만 장소를 해소하는 후보다.
        pending_info_question_type/pending_info_specific_question/pending_info_visit_time:
        직전 턴이 장소명이 없어 되물은 INFO 되묻기였을 때 그때 이미 파악한 질문 정보.
        이번 발화가 그 답변(장소명만 던지는 짧은 응답)으로 보이면 question_type 등을
        유지하고 place_name만 채우는 데 쓴다. 없으면(직전이 INFO 되묻기가 아니었으면)
        모두 None이다.
        """
        ...

    async def answer_with_tools(
        self,
        instruction: str,
        *,
        tools: Sequence[Callable[..., Awaitable[str]]],
        max_tool_calls: int = 3,
    ) -> ProviderResult[str]:
        """도구 목록을 주고, LLM이 스스로 호출·판단해 최종 답변 문장을 쓰게 한다.

        다른 메서드와 달리 정해진 스키마로 구조화 추출을 하는 게 아니라, LLM이
        `tools`를 몇 번이든(최대 max_tool_calls회) 스스로 골라 호출하고 그 결과를
        보고 다음 행동을 정하는 자동 함수 호출(automatic function calling) 루프다
        (강의교재 90강 ReAct 패턴). 반복 자체는 SDK가 돌리므로 호출부는 반복문을
        짜지 않는다.

        각 tool은 실패해도 예외를 던지지 말고 사람이 읽을 안내 문자열(예: "이
        지역엔 없어요, 가까운 후보: ...")을 돌려줘야 한다 — 그래야 LLM이 그 문장을
        보고 스스로 다른 값으로 재시도한다(24강 04절의 입력 검증 원칙과 동일).
        max_tool_calls는 무한 반복을 막는 안전장치이지, 한도에 닿아도 예외를
        던지지 않고 그때까지의 정보로 최종 답변을 만든다.
        """
        ...

    async def extract_compare_request(
        self,
        user_input: str,
        *,
        shown_place_count: int,
        shown_place_names: list[str] | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        """COMPARE 발화에서 비교 대상과 기준을 추출한다.

        shown_place_names는 rank 순 이름 목록이다. 사용자가 순번 대신 장소
        이름으로 비교 대상을 지목했을 때 그 이름을 순번으로 옮기는 데 쓴다 —
        목록이 없으면 이름 지목을 해석할 근거가 없다.
        """
        ...

    async def extract_general_request(
        self,
        user_input: str,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[LLMOutput]:
        """GENERAL 발화의 주제를 분류한다."""
        ...

    async def generate_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        """GENERAL 발화에 실제로 답할 배경지식 문장을 생성한다.

        다른 메서드와 달리 구조화 조건 추출이 아니라 자유 텍스트 답변이다 —
        docs/design/agent-response-generation.md §3/§6의 유일한 LLM 신규 호출 지점.

        offer_content가 있으면(대화층 3단계) 답변 끝에 그 도움을 자연스러운 질문으로
        제안하며 마무리한다. 무엇을 제안할지는 이미 호출자(situational_offers)가
        정했고, 여기서는 문장으로 바꾸는 것만 한다.
        """
        ...

    async def generate_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        """추천 카드 목록을 감싸는 짧은 챗봇 말풍선 문장을 생성한다.

        카드에 없는 사실, 내부 점수/가중치/feature_scores/warnings는 말하지 않는다.
        실패해도 추천 카드 응답 자체는 유지되어야 하므로 호출부는 템플릿으로 fallback한다.
        """
        ...

    async def generate_place_reason(
        self,
        *,
        place_name: str,
        category_label: str | None,
        insights: Sequence[PlacePreferenceInsight],
        matched_preference_codes: Sequence[str] = (),
    ) -> ProviderResult[str]:
        """장소 상세 카드의 "AI가 추천하는 이유" 1~2문장을 만든다.

        근거는 그 장소의 취향 태그 집계와 후기 문장뿐이다 — 순위·점수·조건 축은
        넘기지 않는다(순위는 카드 제목이 이미 말한다). 태그가 없는 장소는 호출부가
        아예 부르지 않는다.

        `matched_preference_codes`는 이번 추천에서 **사용자 취향과 맞은** 태그
        코드다. 구현은 그 태그를 먼저 말하도록 입력을 정렬한다 — 없으면 언급 수
        순서다.

        실패해도 상세 카드 자체는 성립해야 하므로 호출부는 이 문장을 비운 채
        응답한다 — 화면은 이 절을 통째로 접는다.
        """
        ...

    def stream_recommendation_summary(
        self,
        intent: Intent,
        recommendations: RecommendationResponse,
        *,
        conditions: UserConditions | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """추천 요약 문장을 Gemini 조각 단위로 전달한다.

        SSE 경로에서만 사용한다. 카드 데이터는 이미 확정되어 있으므로 이 스트림이
        실패해도 호출자는 고정 템플릿으로 안전하게 마무리할 수 있어야 한다.
        """
        ...

    def stream_general_answer(
        self,
        topic: GeneralTopic,
        original_question: str,
        *,
        offer_content: str | None = None,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """GENERAL 답변을 텍스트 조각으로 전달한다.

        SSE 경로에서만 사용한다. 자유 답변은 추천 카드처럼 별도 결과가 없으므로,
        호출자는 첫 조각 전에 로딩 말풍선을 먼저 열어야 한다. offer_content는
        generate_general_answer()와 같다.
        """
        ...

    def stream_info_answer(
        self,
        *,
        place_name: str,
        question_type: str,
        specific_question: str | None,
        fields: dict[str, str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """검증된 INFO 필드만 근거로 한 안내 답변을 텍스트 조각으로 전달한다."""
        ...

    async def filter_review_evidence(
        self,
        *,
        place_name: str,
        specific_question: str,
        snippets: Sequence[str],
    ) -> ProviderResult[tuple[int, ...]]:
        """후기 문장 중 답변 근거로 쓸 수 있는 것의 번호(1부터)를 고른다.

        검색이 찾아온 문장에는 그 장소가 아니라 근처 가게 이야기가 섞여 있다
        (`docs/근거-장소연결-오염-점검-20260914.md`). 유사도로는 갈리지 않아
        판정을 여기서 한다. 빈 튜플이면 답변을 만들지 않는다는 뜻이다.
        """
        ...

    def stream_review_answer(
        self,
        *,
        place_name: str,
        specific_question: str | None,
        evidence: Sequence[str],
        history: Sequence[ConversationTurnView] | None = None,
    ) -> AsyncIterator[str]:
        """선별된 후기 문장만 근거로 한 답변을 텍스트 조각으로 전달한다."""
        ...

    async def generate_compare_summary(
        self,
        comparison: ComparisonResult,
        *,
        history: Sequence[ConversationTurnView] | None = None,
    ) -> ProviderResult[str]:
        """C가 반환한 비교 사실을 3~6줄의 사용자용 설명으로 바꾼다.

        comparison 밖의 사실·점수·순위를 만들지 않는다. 호출부는 LLM 장애 시
        고정 템플릿으로 fallback해 비교 데이터 응답 자체를 유지해야 한다.
        """
        ...

    async def judge_travel_modes(
        self,
        segments: Sequence[SegmentModeInput],
        context: ModeJudgmentContext,
    ) -> ProviderResult[tuple[str, ...]]:
        """구간별 이동수단을 전 구간 한 번에 정한다. (TP-227)

        Intent에 매이지 않는다 — SCHEDULE의 구간과 RECOMMEND의 후보가 같은 판정을
        쓴다. 두 임계값이 환산 관계라(D-118) 한쪽만 다른 판정을 쓰면 같은 거리를
        두고 서로 다른 이동수단을 말하게 된다.

        돌려주는 것은 **검증하지 않은 문자열 목록**이다. 개수와 어휘는 호출부가
        확인한다 — 어느 구간의 답인지 아는 쪽이 거기다.
        """
        ...

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
        """방금 끝난 턴 뒤에 버튼으로 보여줄 다음 발화 후보를 만든다.

        Intent에 매이지 않는 유일한 생성 메서드다 — 어떤 Intent로 끝난 턴이든 그 뒤에
        한 번 돈다. place_names는 이번 턴에 화면에 나간 장소 이름(추천 카드·일정·비교·
        INFO 카드)으로, 답변 본문이 고정 문구인 경로(RECOMMEND 카드 wrapper 등)에서
        모델이 근거로 삼을 유일한 재료다.

        search_place는 이번 대화가 잡고 있는 검색 장소다(B의 search_center →
        current_location 순). 추천 카드 이름과 별개로 필요하다 — "안국역 근처 카페
        추천해줘"의 "안국역"은 카드 이름 어디에도 없어서, 안 넘기면 모델이 지역을
        가리키는 후속 질문을 만들 근거가 없다.

        transport는 B가 누적한 이동수단 조건(walk/public/car, 없으면 None)이다 —
        도보·대중교통으로 움직이는 사용자에게 주차 질문을 권하지 않기 위해 넘긴다.

        already_suggested는 화면이 최근에 버튼으로 보여준 문구다. 같은 세 개가 턴마다
        되풀이돼 사용자가 이어물을 곳이 없어지는 것을 막으려고 넘긴다. **서버가 만들 수
        없는 값이다** — 세션에 남는 것은 사용자가 실제로 한 말뿐이라, 보여줬는데 누르지
        않은 문구는 화면 말고 아는 곳이 없다.

        개수·길이 상한은 호출부가 코드로 다시 검사한다. 실패해도 답변 자체는 이미
        확정돼 있으므로 호출부는 빈 목록으로 낮춰야 한다.
        """
        ...

    async def generate_schedule_plan(
        self, request: SchedulePlanningRequest
    ) -> ProviderResult[ScheduleLLMPlan]:
        """INT-07 SCHEDULE: 후보 중 3~5개를 선택해 방문 순서를 정한다.

        basis_note는 포함하지 않는다 — LLM이 생성하지 않고
        app.schedule.planner.plan_schedule()이 결정적으로 채운다
        (docs/design/int-07-schedule.md 6.2.1절).
        """
        ...

    async def generate_schedule_fill(
        self, request: SchedulePartialFillRequest
    ) -> ProviderResult[SchedulePartialLLMPlan]:
        """SCHEDULE-09(부분 수정) 2단계: 기존 일정 중 일부 자리만 새로 채운다.

        pinned_items는 결과에 echo하지 않는다 — target_orders 자리에 들어갈
        new_items만 반환한다. 개수·순번 일치 여부는 app.schedule.planner가
        검증한다(SchedulePartialLLMPlan 참고).
        """
        ...


class GeocodingProvider(Protocol):
    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        """장소 이름이나 주소를 정규화된 좌표 결과로 변환한다."""
        ...


class LocalSearchProvider(Protocol):
    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        """상호명·시설명으로 Naver 지역 검색 후보를 반환한다."""
        ...


class WeatherProvider(Protocol):
    async def get_forecast_slots(
        self, latitude: float, longitude: float
    ) -> ProviderResult[WeatherForecastResult]:
        """좌표의 시각별 초단기예보 목록을 반환한다."""
        ...


class TravelRouteProvider(Protocol):
    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        """출발지에서 여러 목적지까지의 경로를 목적지 순서대로 반환한다.

        구현체는 자신이 지원하지 않는 `mode`를 받으면 ValueError를 던진다 —
        지원 여부는 `TravelRouteTool`의 mode별 등록으로 가르고, 여기서는
        잘못 배선된 경우를 조용히 통과시키지 않는 것이 목적이다.
        """
        ...


class PlaceSearchProvider(Protocol):
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
        """주어진 좌표/조건으로 장소 후보 목록을 조회해 공통 모델로 반환한다."""
        ...


@dataclass(frozen=True)
class BarrierFreePlaceSearch:
    """무장애 검색이 돌려주는 것: 후보와, 각 후보가 왜 후보인지.

    후보만 돌려주면 `partial`과 `possible`이 같은 것이 된다. 둘 다 후보로 남기
    때문이다 — 들어갈 수는 있는데 못 가는 구역이 남는 장소를 추천에서 빼는 것은
    과하지만, 그 사실을 말하지 않고 추천하는 것도 옳지 않다.

    `verdicts`의 열쇠는 `PlaceCandidate.place_id`이고, 값은 **요구한 어휘만**
    담는다. 요구하지 않은 편의의 판정까지 올리면 사용자가 묻지 않은 것을 답변이
    말하게 된다.
    """

    candidates: list[PlaceCandidate]
    verdicts: dict[str, dict[AccessibilityNeed, AccessibilityVerdict]]


class DistrictPlaceSearchProvider(Protocol):
    """구 이름으로 들어온 요청의 후보를 모으는 검색 provider(D-119).

    `PlaceSearchProvider`와 계약을 나눈 이유는 무장애 경로와 같다 — 검색 인자가
    다르고, 인자를 더하면 무관한 경로의 서명까지 바뀐다.

    이쪽은 좌표도 반경도 받지 않는다. 구 단위 요청에는 기준점이 없기 때문이다.
    개수도 받지 않는다 — 몇 곳을 쓸지는 후보를 다 받은 뒤 선택 단계가 정한다.
    """

    async def search_places_in_district(
        self, *, district_code: str
    ) -> ProviderResult[list[PlaceCandidate]]:
        """그 구의 활성 장소 전량을 후보로 돌려준다. 순서에 의미는 없다."""
        ...


class BarrierFreePlaceSearchProvider(Protocol):
    """무장애 편의를 요구한 요청의 후보를 찾는 검색 provider.

    `PlaceSearchProvider`에 인자를 더하지 않고 계약을 나눈 이유는 두 가지다.

    첫째, 검색 인자가 다르다. 이쪽은 지역·구 코드를 쓰지 않고(저장소가 이미 서울
    적재분만 담는다) 대신 요구 편의 목록을 받는다.

    둘째, 인자를 더하면 `RealPlaceProvider`와 Fake까지 서명이 바뀐다. 무장애와
    무관한 경로가 무장애 때문에 흔들리지 않게 둔다.
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
        """요구 편의를 **전부** 만족하는 후보를 거리순으로 반환한다.

        `needs`가 비어 있으면 구현체는 ValueError를 던진다. 조건 없는 검색으로
        조용히 바뀌면 무장애를 요구한 요청이 조건 빠진 결과를 받고도 모른다.
        """
        ...


class PlaceDetailsProvider(Protocol):
    async def get_details(
        self, content_id: str, content_type_id: str
    ) -> ProviderResult[PlaceDetails]:
        """장소 ID와 유형 ID로 정규화된 상세정보를 반환한다."""
        ...


@runtime_checkable
class BatchPlaceDetailsProvider(Protocol):
    """여러 장소의 상세정보를 한 번의 조회로 반환할 수 있는 provider.

    미리 구축된 저장소를 읽는 provider만 구현한다. content_id별로 개별 호출이
    필요한 외부 API provider는 기존 PlaceDetailsProvider만 만족하면 되고,
    Tool이 런타임에 이 계약 지원 여부를 보고 조회 방식을 고른다.
    """

    async def get_details_batch(
        self,
        content_ids: list[str],
    ) -> ProviderResult[dict[str, PlaceDetails]]:
        """content_id 목록에 대한 상세정보를 content_id 기준 dict로 반환한다.

        조회되지 않은 content_id는 결과에서 빠진다(호출자가 누락으로 처리한다).
        """
        ...


class PlaceProvider(PlaceSearchProvider, PlaceDetailsProvider, Protocol):
    async def search_by_keyword(
        self,
        keyword: str,
        region_code: str | None = None,
        district_code: str | None = None,
        limit: int = DEFAULT_PLACE_PROVIDER_RESULT_LIMIT,
    ) -> ProviderResult[list[PlaceCandidate]]:
        """장소명·키워드로 후보와 TourAPI content ID를 조회한다."""
        ...

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        """장소명으로 정확히 일치하는 후보를 찾아 상세정보까지 반환한다."""
        ...


class PlaceCommonDetailsProvider(Protocol):
    async def get_common_details(self, content_id: str) -> ProviderResult[PlaceCommonDetails]:
        """detailCommon2만 호출해 overview·homepage·tel을 반환한다."""
        ...


class PlaceImageProvider(Protocol):
    """장소 사진 목록을 주는 최소 계약(TourAPI detailImage2).

    상세 조회 계약과 나눈다. 사진은 detailCommon2·detailIntro2와 다른
    오퍼레이션이고 일일 한도도 따로 걸리므로, 사진만 필요한 호출부가 상세
    provider 전체를 요구할 이유가 없다.
    """

    async def get_place_images(
        self, content_id: str, limit: int
    ) -> ProviderResult[tuple[PlacePhoto, ...]]: ...


class PlaceDetailByNameProvider(Protocol):
    """장소명으로 상세 1건을 찾는 최소 계약.

    GetPlaceDetailTool이 실제로 쓰는 메서드는 이것 하나뿐이다. 넓은 PlaceProvider를
    요구하면 저장소 기반 provider가 쓰지도 않는 검색 메서드를 구현해야 한다.
    """

    async def find_details_by_name(
        self,
        name: str,
        region_code: str | None = None,
        district_code: str | None = None,
    ) -> ProviderResult[PlaceDetails]:
        """장소명으로 정확히 일치하는 후보를 찾아 상세정보까지 반환한다."""
        ...


class TourAreaPlaceProvider(Protocol):
    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage:
        """행정구역에 속한 TourAPI 장소 목록 한 페이지를 반환한다."""
        ...

    async def get_operating_details(
        self,
        content_id: str,
        content_type_id: str,
    ) -> PlaceOperatingDetails:
        """소개 상세 조회만 사용해 운영시간과 휴무일 원문을 반환한다."""
        ...


class BarrierFreeProvider(Protocol):
    """무장애 여행 정보(KorWithService2) 조회 계약.

    TourAreaPlaceProvider와 나눈 이유는 서비스가 다르기 때문이다 — 같은 인증키를
    쓰지만 경로도 응답 필드도 다르고, 등록된 장소가 19%뿐이라 호출 대상을 목록으로
    먼저 좁힌다.
    """

    async def list_barrier_free_content_ids(
        self,
        area_code: str,
        district_code: str,
    ) -> dict[str, str]:
        """무장애 정보가 등록된 장소의 content_id → content_type_id."""
        ...

    async def get_barrier_free_details(
        self,
        content_id: str,
    ) -> PlaceBarrierFreeDetails | None:
        """무장애 상세 1건. 등록되지 않은 장소면 None이다."""
        ...


class FestivalProvider(Protocol):
    async def search_festivals(
        self,
        region_code: str,
        district_code: str | None,
        reference_date: date,
    ) -> ProviderResult[list[FestivalEvent]]:
        """법정동 코드 기준 지역의 행사 목록을 반환한다.

        district_code가 None이면 시도 전체를 받는다 — 지원 구가 여럿일 때 구마다
        호출하지 않기 위해서다. 지원 구 판정은 구현체가 응답으로 한다(D-025).

        진행 중 판정은 호출자가 reference_date로 다시 한다 — provider는 기간이
        해석 가능한 행사를 모아 돌려주기만 한다.
        """
        ...


class ConcentrationProvider(Protocol):
    async def get_forecast(
        self,
        area_code: str,
        district_code: str,
        place_name: str | None = None,
    ) -> ProviderResult[ConcentrationResult]:
        """지역과 선택적인 관광지명에 대한 향후 집중률을 반환한다."""
        ...


class RealtimeCommercialProvider(Protocol):
    async def get_area_commercial_status(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCommercialResult]:
        """서울시 주요 장소 한 곳의 실시간 상권현황을 반환한다."""
        ...


class RealtimeCityDataProvider(Protocol):
    async def get_area_citydata(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCityDataResult]:
        """서울시 주요 장소의 실시간 상권·인구 데이터를 한 번에 반환한다."""
        ...


class MunicipalParkingProvider(Protocol):
    async def get_district_parking(
        self, district: str
    ) -> ProviderResult[tuple[MunicipalParkingStatus, ...]]:
        """서울시 공영주차장 API에서 한 구의 최신 주차 현황을 가져온다."""
        ...


class PublicToiletProvider(Protocol):
    async def list_all_toilets(self) -> ProviderResult[tuple[PublicToilet, ...]]:
        """서울시 공중화장실 위치정보 전량을 가져온다.

        구·좌표 필터 파라미터가 없어(실측 확인: 추가 경로 세그먼트를 무시하고 항상
        전체를 준다) 부분 조회가 불가능하다. 그래서 이 Provider는 동기화 스크립트만
        쓰고, 사용자 조회는 적재된 ``public_toilets`` 표에서 한다.
        """
        ...


class HolidayProvider(Protocol):
    async def get_holidays(
        self, year: int, month: int | None = None
    ) -> ProviderResult[HolidayResult]:
        """공휴일 목록을 반환한다."""
        ...

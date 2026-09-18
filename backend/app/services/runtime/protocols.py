"""Agent Runtime이 C(Tool Intelligence)/D(Recommendation)에 의존하는 최소 계약.

역할: Runtime 코드(agent_runtime.py)가 C/D의 구체 클래스를 직접 import하지 않고 이
Protocol만 거치도록 강제한다. A–C 요청·응답은 app.agent_context.schemas를 단일 계약으로
사용하며, 실제 C Service(get_context_provider())가 stubs.py의 FakeToolProvider를 대체한다.
ToolProvider는 A-C Context Contract v0(docs/design/a-c-context-contract-draft.md)로
확정됐다 — excluded_place_ids는 여기 없다. C는 외부 데이터 조회·정규화만 담당하고, 이전
노출·거절 후보 제외는 D Recommendation의 책임이라고 계약서 §2가 명시한다.
RecommendationProvider(D)도 이 형태로 확정됐다([TECH-02]) — excluded_place_ids를 그대로
받아 D가 직접 필터링한다. RealRecommendationProvider(real_recommendation_provider.py)가
실제 구현체다. 후보 보충 조회에 필요한 단계 분할(prepare/score_prepared)은
StagedRecommendationProvider로 따로 두고, 호출부가 isinstance()로 지원 여부를 확인한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
)
from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    RecommendationContext,
)
from app.domain.travel_route import TravelRoute
from app.schemas import RecommendationResponse, UserConditions
from app.services.recommendation_pipeline import PreparedRecommendationResult
from app.services.runtime.compare_context_schemas import (
    CompareContextRequest,
    CompareContextResponse,
)
from app.services.runtime.info_context_schemas import InfoContextRequest, InfoContextResponse
from app.tools.travel_route import TravelRouteQuery, TravelRouteToolResult


class ToolProvider(Protocol):
    async def fetch_context(self, request: AgentContextRequest) -> AgentContextResponse:
        """조건에 맞는 위치·날씨·장소 후보 등을 공통 AgentContextResponse로 반환한다."""
        ...

    async def fetch_info_context(self, request: InfoContextRequest) -> InfoContextResponse:
        """INFO의 혼잡도 질의(question_type=concentration)를 처리한다.

        (A 제안, C 확인 필요 — info_context_schemas.py, concentration-conditions.md
        §2.4/§3.3) 장소 해석·get_concentration 조회·근접치 fallback 오케스트레이션은
        전부 C 내부 책임이다. A는 구조화된 결과만 받는다.
        """
        ...

    async def fetch_compare_context(
        self, request: CompareContextRequest
    ) -> CompareContextResponse:
        """COMPARE 비교 대상의 place_id를 장소명으로 해석해 비교 사실을 반환한다.

        (C 구현 — compare_context_schemas.py, int-04-compare.md) A는 targets
        ("all" / [1, 2] 같은 지시 표현)를 shown_place_ids로 이미 푼 뒤, B가 보관한
        추천 시점 Feature 스냅샷과 함께 넘긴다. C는 place_id → 장소명 해석만 하고
        우열을 판정하지 않는다 — 비교 문장 생성은 A의 LLM 요약 몫이다.

        수치(거리·남은 운영시간·실내외)는 재조회하지 않고 그대로 통과한다. 사용자가
        카드에서 본 값과 비교 답변의 값이 어긋나면 안 되기 때문이다(D-050).

        fetch_info_context와 달리 호출부에 hasattr 방어가 필요 없다 — 실제 C
        (ContextService)와 FakeToolProvider 양쪽에 구현이 있다.
        """
        ...


class TravelRouteToolProvider(Protocol):
    async def execute(self, query: TravelRouteQuery) -> TravelRouteToolResult:
        """통과 후보까지의 이동 정보를 실제 경로 또는 추정값으로 반환한다."""
        ...


class EnrichmentProvider(Protocol):
    async def enrich(self, request: CandidateEnrichmentRequest) -> CandidateEnrichmentResponse:
        """상위 추천 후보의 혼잡도를 후조회한다.

        (concentration-conditions.md §2.2.3 안 B, agent-runtime-contract.md
        §6.5.2 — C 협의 완료) C의 `CandidateEnrichmentService.enrich()`가 이미
        이 시그니처를 만족하므로 Fake 없이 바로 연결 가능하다.
        """
        ...


@runtime_checkable
class StagedRecommendationProvider(Protocol):
    """하드 필터와 채점을 나눠 실행할 수 있는 D 구현체의 추가 계약.

    `RecommendationProvider`와 분리해 둔 이유: A가 후보를 보충 조회하려면
    "필터만 먼저 돌리고 채점은 나중에" 나눌 수 있어야 하는데, 이건 추천 결과를
    돌려주는 최소 계약(`recommend()`)보다 넓은 능력이다. 테스트용 Fake처럼 이
    단계 분할이 필요 없는 구현체까지 세 메서드를 구현하게 만들면, Fake가 D의
    하드 필터 로직을 흉내 내야 해서 오히려 "조용한 fake"가 된다.

    `runtime_checkable`이라 호출부는 `isinstance()`로 능력을 확인하고, 그
    분기에서 타입도 함께 좁혀진다.
    """

    def merge_prepared(
        self,
        results: Sequence[PreparedRecommendationResult],
    ) -> PreparedRecommendationResult:
        """같은 요청의 여러 하드 필터 결과를 중복 없이 병합한다.

        배치들의 하드 필터 입력(`PreparedRecommendationResult.filter_context` —
        방문 시각과 운영시간 무시 여부)이 서로 다르면 `ValueError`를 던진다.
        호출부는 모든 `prepare()`에 같은 값을 넘기기만 하면 된다.

        날씨 판정 등 채점 조건이 배치마다 달라도 오류가 아니다 — 첫 배치 값을
        재사용한다. 보충 조회에서 기상 조회가 실패했다고 멀쩡한 후보까지 버리지
        않기 위해서다.
        """
        ...

    async def prepare(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        *,
        visit_at: datetime,
        ignore_operating_hours: bool = False,
    ) -> PreparedRecommendationResult:
        """후보 변환과 하드 필터까지만 실행한다.

        같은 사용자 요청 안에서 후보를 보충할 때는 모든 호출에 동일한
        ``visit_at``과 ``ignore_operating_hours``를 전달해야 한다 — 하드 필터
        입력이 달라지면 같은 장소가 조회 순서에 따라 다르게 걸러진다.
        `recommend()`와 같은 조건에서 `AppError`를 던진다(context/location/
        places 조회 실패) — 보충 호출이라면 호출부가 잡아서 이미 확보한
        후보로 진행해야 한다.
        """
        ...

    async def score_prepared(
        self,
        conditions: UserConditions,
        prepared: PreparedRecommendationResult,
        *,
        travel_routes: tuple[TravelRoute, ...] = (),
        limit: int = 5,
        # 계정에 저장해 둔 취향으로 만든 근거 검색 질의. **발화에 취향이 있어도
        # 값이 있다** — 발화와 부딪히는 칩만 빠진 뒤에 온다
        # (`domain/saved_preference.py`가 그 판단을 한다). 호출부는 발화 질의
        # **뒤에** 이 값을 이어 붙인다.
        #
        # `conditions.taste_query`에 합치지 않고 따로 받는 이유는, 그 필드가 LLM
        # 요약 프롬프트로도 가서(`gemini_prompts.py`) 말풍선이 "말씀하신 '아늑한
        # 공간'"이라고 이번 턴에 하지 않은 말을 하게 되기 때문이다.
        saved_taste_query: str | None = None,
    ) -> RecommendationResponse:
        """하드 필터 통과 후보와 A가 조회한 도보 정보를 받아 최종 추천한다."""
        ...


class RecommendationProvider(Protocol):
    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = 5,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        """조건과 Tool 결과를 바탕으로 최종 추천 결과를 반환한다.

        limit은 반환할 최대 개수다. 기본값 5는 RECOMMEND 흐름과 동일하게
        유지하고, SCHEDULE처럼 더 많은 후보가 필요한 흐름은 호출 시 지정한다.
        ignore_operating_hours=True면 폐점 후보도 제외하지 않고 채점한다 —
        "운영중이 아닌 곳도 볼래요" 되묻기 해소 턴에서만 켠다.
        """
        ...

    async def rerank_with_concentration(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        concentration: CandidateEnrichmentResponse,
    ) -> RecommendationResponse:
        """(D-040 확정, D-051 2차 Scoring 배선 통일) 1차 추천 결과와 혼잡도 보강
        데이터로 재순위를 계산한다.

        `context`/`conditions`는 1차 `recommend()`에 넘긴 것과 동일해야 한다 —
        구현체가 내부에서 `resolve_weather_condition(context, conditions)`으로
        날씨 판정을 다시 얻어야 1차와 2차의 판정이 갈라지지 않는다(사전에 계산한
        `WeatherCondition`을 그대로 받는 옛 방식은 폐기 — D-051 "남은 것" 해소).

        D의 Real 구현체가 아직 이 메서드를 갖고 있지 않을 수 있다 — 호출부
        (agent_runtime.py)는 `hasattr()`로 방어하고, 없으면 `first_pass`를
        그대로 최종 결과로 쓴다. D가 이 메서드를 구현하면 자동으로 새 경로를
        타기 시작한다.
        """
        ...

    async def rerank_with_co_visited(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        co_visited_pairs: Sequence[tuple[str, str]],
    ) -> RecommendationResponse:
        """(D-092) place_associations(B-owned, D-088) 기반 "함께 방문된 이력"
        쌍으로 `first_pass`(1차, 또는 이미 혼잡도 2차를 탄 결과)를 재순위한다.

        `co_visited_pairs`는 B의 `CoVisitedHint` 스키마를 그대로 받지 않고
        (place_id, place_id) 쌍만 받는다 — D가 B의 스키마를 몰라도 되게 하기
        위해서다. `conditions`/`context`는 1차 `recommend()`에 넘긴 것과 동일해야
        한다 — 구현체가 내부에서 `resolve_weather_condition(context, conditions)`로
        날씨 판정을 다시 얻어 `rerank_with_concentration()`과 같은 방식으로 근거
        문장을 재조립한다.

        D의 Real 구현체가 아직 이 메서드를 갖고 있지 않을 수 있다 — 호출부
        (agent_runtime.py)는 `hasattr()`로 방어하고, 없으면 `first_pass`를
        그대로 최종 결과로 쓴다.
        """
        ...

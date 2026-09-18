"""RecommendationContext를 Scoring→Evidence→Explanation으로 조립하는 추천 파이프라인.

D는 C Tool을 직접 호출하지 않는다([TECH-02]). Tool 조회는 호출자(A, 또는
`app/services/recommendations.py`처럼 아직 조건 스키마 통합 전인 레거시
호출자)가 맡고, D는 그 결과인 RecommendationContext만 입력으로 받는다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from time import perf_counter
from typing import TypeAlias

from app.agent_context.enrichment_schemas import CandidateEnrichmentResponse
from app.agent_context.schemas import RecommendationContext
from app.concentration_policy import ConcentrationLevel
from app.domain.candidate_mapper import map_context_to_scoring_candidates
from app.domain.evidence import build_evidence
from app.domain.explanation import build_explanations
from app.domain.models import (
    PlaceEvidenceMatch,
    PreferenceTagMatch,
    ScoringCandidate,
    WeatherCondition,
)
from app.domain.ranking_origin import (
    resolve_ranking_origin,
    resolve_travel_origin_toggle,
    resolve_user_to_target_km,
)
from app.domain.scoring import (
    ExclusionReason,
    PrepareResult,
    RankedCandidate,
    co_visited_score,
    concentration_score,
    equalize_weights,
    prepare_candidates,
    redistribute_weights,
    score_prepared_candidates,
    weights_for_feature_scores,
)
from app.domain.travel_route import TravelRoute
from app.domain.weather_judgment import (
    WeatherReason,
    judge_weather_condition_from_facts,
    judge_weather_condition_from_stated,
)
from app.errors import AppError
from app.place_search_policy import WALKING_SPEED_KM_PER_MINUTE
from app.recommendation_limits import DEFAULT_RECOMMENDATION_RESULT_LIMIT
from app.schemas import (
    ExcludedScoringCandidate,
    PreferenceTagScoreDetail,
    RecommendationItem,
    RecommendationResponse,
    TasteEvidenceQuote,
    TravelOrigin,
    TravelOriginToggle,
    UserConditions,
    WeatherIntent,
)

_OPERATING_HOURS_UNVERIFIED_WARNING = "방문 전에 운영 여부를 확인해주세요."
_DETAILS_MISSING_WARNING = "장소 상세정보 일부를 확인하지 못했습니다."
# 날씨가 Scoring에 빠지는 이유는 두 가지이고, 사용자에게 같은 말로 알리면 안 된다.
# (1) 조회 자체를 안 한 경우: weather_intent=IGNORE(= "날씨 상관없어" 명시)면 C가
#     Weather Tool을 실행하지 않는다(tool_rules.py). 정상 흐름이므로 오류처럼 알리지
#     않는다. int-01-recommend.md §8의 IGNORE 정의 참고.
# (2) 조회했으나 실패한 경우: 날씨 API 장애 등 — 이때만 "확인하지 못했다"가 사실이다.
_WEATHER_IGNORED_WARNING = "날씨 조건을 반영하지 않기로 하셔서 이번 추천에는 제외했어요."
_WEATHER_MISSING_WARNING = "현재 날씨 정보를 확인하지 못해 이 조건은 반영되지 않았어요."
_NO_NOTABLE_EXPLANATION_WARNING = (
    "이 장소는 특별히 강조할 만한 조건은 없지만, 조건에 맞아 추천했어요."
)
# 24시간 영업은 운영 구간이 time.min~time.max로 들어온다. 그대로 포맷하면
# "00:00~23:59"가 되어 마감이 임박한 것처럼 읽힌다.
_ALL_DAY_OPERATING_HOURS_DISPLAY = "24시간"
# 원문 "09:00~24:00"의 종료도 time.max로 들어온다(operating_hours.py). 원문이 24:00인데
# "23:59"로 보이면 1분 일찍 닫는 것처럼 읽히므로 원문 표기를 되살린다.
_MIDNIGHT_CLOSE_DISPLAY = "24:00"
# 다만 C의 직렬화(agent_context/mappers.py::_operating_schedule)가 close_time을
# "%H:%M"으로 자르는 탓에 time.max(23:59:59.999999)는 D까지 "23:59"로 도착한다 —
# 자정 마감이라는 표식이 도중에 지워진다. 그래서 23:59도 자정 마감으로 함께 본다.
# 종로구 데이터에 23:59 마감 원문은 0건이고(전부 "24:00" 표기) 설령 생기더라도
# 표기가 1분 어긋날 뿐이라, C의 직렬화를 바꾸는 것보다 이쪽이 비용이 작다.
_MIDNIGHT_CLOSE_TIMES = (time.max, time(hour=23, minute=59))

Timer: TypeAlias = Callable[[], float]


@dataclass(frozen=True)
class PreparedRecommendationResult:
    """Context 변환과 하드 필터를 마쳐 최종 채점을 기다리는 결과."""

    preparation: PrepareResult
    all_candidates: tuple[ScoringCandidate, ...]
    weather_condition: WeatherCondition | None
    weather_reason: WeatherReason
    requested_environment: str | None
    details_missing_place_ids: frozenset[str]
    visit_at: datetime
    weather_ignored: bool
    ignore_operating_hours: bool
    # 거리 기준점의 표시 이름(resolve_origin_name 참고). 하드 필터 입력이 아니라
    # 근거 문장 재료라 filter_context에는 넣지 않는다 — 날씨 판정과 같은 취급으로,
    # 병합 시 첫 배치 값을 그대로 쓴다.
    origin_name: str | None = None
    # 거리 점수 분모에 더할 값(km). 위와 같은 이유로 첫 배치 값을 쓴다.
    # 계산 근거는 _distance_denominator_offset_km() 참고.
    distance_denominator_offset_km: float = 0.0
    # "OO 기준으로 다시 보기" 비차단형 전환 제안(D-071). 위와 같은 이유로
    # 첫 배치 값을 쓴다 — resolve_travel_origin_toggle() 참고.
    travel_origin_toggle: TravelOriginToggle | None = None
    # 후보를 구 하나에서 통째로 모았는가(C의 district_scope, D-119). 거리 Feature를
    # 쓸지 정하는 값이라 채점까지 그대로 흘린다. 위와 같은 이유로 첫 배치 값을 쓴다 —
    # 한 요청의 배치들은 같은 방식으로 후보를 모았다.
    district_scoped: bool = False

    @property
    def filter_context(self) -> tuple[object, ...]:
        """하드 필터가 실제로 입력으로 받은 값 — 배치 간 이게 같아야 병합할 수 있다.

        `prepare_candidates()`에 들어가는 것만 담는다. 날씨·요청 환경은 여기
        없다 — 하드 필터는 그 값을 아예 받지 않기 때문에, 배치별로 달라도
        통과/제외 결과를 오염시킬 수 없다(`merge_prepared_recommendations()`
        docstring 참고).
        """
        return (self.visit_at, self.ignore_operating_hours)


def merge_prepared_recommendations(
    results: Sequence[PreparedRecommendationResult],
) -> PreparedRecommendationResult:
    """같은 요청에서 여러 번 준비한 후보를 중복 없이 하나로 합친다.

    하드 필터 입력(`filter_context` — 방문 시각과 운영시간 무시 여부)은 모든
    보충 조회에서 같아야 한다. 이게 다르면 같은 장소가 조회 순서에 따라 다르게
    걸러져 통과/제외 목록 자체가 오염되므로 오류로 처리한다.

    반면 채점 조건(날씨 판정·요청 환경·weather_ignored)은 **첫 배치 값을 그대로
    재사용한다.** 보충 조회는 같은 요청·같은 시각·같은 좌표를 다시 조회하는
    것이라 날씨가 달라질 이유가 없고, 실제로 달라졌다면 그건 보충 조회의 기상
    조회가 실패했다는 뜻이지 판정이 바뀌었다는 뜻이 아니다. 그리고 하드 필터
    (`prepare_candidates()`)는 날씨를 인자로 받지 않고, 후보 변환
    (`map_context_to_scoring_candidates()`)도 `context.weather`를 읽지 않는다 —
    채점은 병합이 끝난 뒤 이 단일 기준으로 한 번만 돌기 때문에, 배치별 날씨
    차이가 결과에 섞여 들어갈 경로가 없다. 여기서 배치를 거부하면 멀쩡한 보충
    후보만 통째로 버리게 된다.

    Provider가 같은 장소를 다시 반환하면 첫 분류만 유지한다.
    """
    if not results:
        raise ValueError("병합할 준비 결과가 없습니다.")

    first = results[0]
    for result in results[1:]:
        if result.filter_context != first.filter_context:
            raise ValueError("준비 결과의 방문 시각 또는 운영시간 무시 여부가 서로 다릅니다.")

    eligible_by_id = {}
    excluded_by_id = {}
    candidates_by_id = {}
    for result in results:
        for candidate in result.all_candidates:
            candidates_by_id.setdefault(candidate.place_id, candidate)
        for prepared in result.preparation.eligible_candidates:
            place_id = prepared.candidate.place_id
            if place_id not in excluded_by_id:
                eligible_by_id.setdefault(place_id, prepared)
        for excluded in result.preparation.excluded_candidates:
            place_id = excluded.place_id
            if place_id not in eligible_by_id:
                excluded_by_id.setdefault(place_id, excluded)

    return PreparedRecommendationResult(
        preparation=PrepareResult(
            eligible_candidates=tuple(eligible_by_id.values()),
            excluded_candidates=tuple(excluded_by_id.values()),
            input_count=len(candidates_by_id),
        ),
        all_candidates=tuple(candidates_by_id.values()),
        weather_condition=first.weather_condition,
        weather_reason=first.weather_reason,
        requested_environment=first.requested_environment,
        details_missing_place_ids=frozenset().union(
            *(result.details_missing_place_ids for result in results)
        ),
        visit_at=first.visit_at,
        weather_ignored=first.weather_ignored,
        ignore_operating_hours=first.ignore_operating_hours,
        origin_name=first.origin_name,
        distance_denominator_offset_km=first.distance_denominator_offset_km,
        travel_origin_toggle=first.travel_origin_toggle,
        district_scoped=first.district_scoped,
    )


async def prepare_recommendation_from_context(
    context: RecommendationContext | None,
    *,
    # A가 넘기는 사용자 발화 조건. weather_intent와 발화 날씨(conditions.weather)를
    # resolve_weather_condition()에 전달해 D-051 판정(사실+의도)에 쓴다.
    # AVOID/ENJOY면 C가 날씨를 조회하지 않을 수 있어 그때는 발화 값으로 대신 판정한다.
    conditions: UserConditions | None = None,
    visit_at: datetime,
    shown_place_ids: frozenset[str] = frozenset(),
    rejected_place_ids: frozenset[str] = frozenset(),
    # True면 폐점 후보도 채점에 포함한다 — "운영중이 아닌 곳도 볼래요"(no_data_closed
    # 되묻기) 해소 턴에서만 A가 켠다.
    ignore_operating_hours: bool = False,
) -> PreparedRecommendationResult:
    """Context를 후보로 변환하고 하드 필터까지만 적용한다.

    Context 상태 처리: `context` 자체가 `None`이거나(예: A의
    `AgentContextResponse.status`가 `needs_clarification`/`unsupported`/
    `unavailable`일 때), `location`/`places`가 없거나 `unavailable`(조회
    자체 실패)이면 `AppError`를 던진다. `no_data`(정상 조회했지만 결과
    없음)는 에러가 아니라 후보 0건으로 처리한다 — "확인 못 함"과 "확인했는데
    없음"은 다른 상황이라 구분한다.

    호출자 책임: 같은 사용자 요청 안에서 후보를 보충하려고 이 함수를 여러 번
    부를 때는 모든 호출에 같은 `visit_at`과 `ignore_operating_hours`를 넘겨야
    한다. 하드 필터 입력이 호출마다 달라지면 같은 장소가 조회 순서에 따라 다르게
    걸러진다. `merge_prepared_recommendations()`가 이걸 `filter_context`로
    검사하고, 다르면 `ValueError`를 던진다.
    """
    if context is None:
        raise AppError(
            code="context_unavailable",
            message="Context 정보가 없습니다.",
            status_code=502,
            retryable=True,
        )

    location = context.location
    if location is None or location.status not in {"success", "partial"} or location.data is None:
        raise AppError(
            code="location_unavailable",
            message="위치 정보를 확인할 수 없습니다.",
            status_code=502,
            retryable=True,
        )

    places = context.places
    if places is None or places.status == "unavailable":
        error = places.error if places is not None else None
        raise AppError(
            code=error.code if error else "unavailable",
            message=error.message if error else "주변 장소를 검색하지 못했습니다.",
            status_code=502,
            retryable=error.retryable if error else True,
        )

    candidates = map_context_to_scoring_candidates(
        context, visit_at=visit_at, conditions=conditions
    )
    resolved_weather_condition, weather_reason = resolve_weather_condition(context, conditions)
    preparation = prepare_candidates(
        candidates,
        now=visit_at,
        shown_place_ids=shown_place_ids,
        rejected_place_ids=rejected_place_ids,
        ignore_operating_hours=ignore_operating_hours,
    )

    return PreparedRecommendationResult(
        preparation=preparation,
        all_candidates=candidates,
        weather_condition=resolved_weather_condition,
        weather_reason=weather_reason,
        requested_environment=resolve_requested_environment(conditions),
        details_missing_place_ids=frozenset(
            place.place_id for place in (places.data or []) if place.operating_schedule is None
        ),
        visit_at=visit_at,
        weather_ignored=_is_weather_explicitly_ignored(context, conditions),
        ignore_operating_hours=ignore_operating_hours,
        # C가 "후보를 구 전체에서 모았다"는 **사실만** 실어 보낸다(D-119). 그것을
        # 점수에 어떻게 반영할지는 D가 정한다 — 거리 Feature를 쓰지 않는다.
        district_scoped=context.district_scope is not None,
        origin_name=resolve_origin_name(context, conditions),
        distance_denominator_offset_km=_distance_denominator_offset_km(context, conditions),
        travel_origin_toggle=resolve_travel_origin_toggle(context, conditions),
    )


async def score_prepared_recommendation(
    prepared: PreparedRecommendationResult,
    *,
    search_radius_km: float,
    recommendation_limit: int = DEFAULT_RECOMMENDATION_RESULT_LIMIT,
    # A가 조회한 실측 경로. 비어 있으면 거리 Feature가 직선거리로 계산된다.
    # 후보마다 이동수단이 다를 수 있다(D-118) — 도보권은 도보, 그 밖은 대중교통.
    travel_routes: Sequence[TravelRoute] = (),
    # `search_radius_km`을 만들 때 쓴 속도(km/분). 거리 점수의 시간 예산이 이
    # 값으로 반경을 소요시간으로 되돌린다. **측정한 이동수단의 속도가 아니다** —
    # 반경과 예산이 같은 속도를 써야 사용자가 말한 이동시간이 그대로 예산이 된다
    # (D-118, `scoring.py::_travel_minutes_budget`). 호출자는 A의
    # `to_search_radius_speed_km_per_min()` 값을 그대로 넘긴다.
    travel_budget_speed_km_per_min: float = WALKING_SPEED_KM_PER_MINUTE,
    # 취향 근거 검색 결과. None이면 사용자가 취향을 말하지 않은 것으로 보고
    # taste Feature를 아예 쓰지 않는다. 빈 dict는 "말했는데 근거를 못 찾았다"라
    # Feature는 켜지고 모든 후보가 0점이 된다 — 둘을 구분한다.
    taste_matches: Mapping[str, PlaceEvidenceMatch] | None = None,
    # 취향 태그 일치 결과. 발화에서 사전 코드(혼밥 → alone 등)를 뽑아낸 요청에만
    # 채워지고, 임베딩 점수의 가산 신호로 함께 taste 점수를 만든다.
    taste_tag_matches: Mapping[str, PreferenceTagMatch] | None = None,
    timer: Timer = perf_counter,
) -> RecommendationResponse:
    """준비된 후보를 채점하고 Evidence·Explanation 응답을 조립한다.

    호출자 책임: `search_radius_km`은 C가 `context.places`를 조회할 때 실제로
    사용한 검색 반경과 동일해야 한다 — Scoring의 거리 점수 정규화
    (`max_distance_km`)가 이 값을 그대로 재사용하기 때문이다
    (`docs/design/recommendation-scoring.md` 참고). 값이 어긋나면 거리 점수가
    실제 후보 풀 범위와 안 맞게 계산된다.
    """
    started_at = timer()
    scoring = score_prepared_candidates(
        prepared.preparation.eligible_candidates,
        weather_condition=prepared.weather_condition,
        weather_reason=prepared.weather_reason,
        # 분모의 원점을 분자와 맞춘다 — 거리는 사용자 기준으로 재는데 반경은
        # 타겟 기준이라, 이동시간을 말하지 않은 요청에서 둘이 갈린다(TP-112).
        max_distance_km=search_radius_km + prepared.distance_denominator_offset_km,
        requested_environment=prepared.requested_environment,
        travel_routes=travel_routes,
        travel_budget_speed_km_per_min=travel_budget_speed_km_per_min,
        district_scoped=prepared.district_scoped,
        taste_matches=taste_matches,
        taste_tag_matches=taste_tag_matches,
    )
    ranked = scoring.ranked[:recommendation_limit]
    # 결과가 0건이고, 그 이유가 전부 폐점 후보 제외였다면(다른 이유로 제외된 후보가
    # 없었다면) A가 "운영중이 아닌 곳도 볼래요" 되묻기를 띄울 수 있게 표시한다.
    excluded = prepared.preparation.excluded_candidates
    excluded_closed_place_ids = tuple(
        candidate.candidate.place_id
        for candidate in excluded
        if candidate.reason is ExclusionReason.CLOSED
    )
    excluded_closed_count = len(excluded_closed_place_ids)
    excluded_all_closed = (
        not ranked and excluded_closed_count > 0 and excluded_closed_count == len(excluded)
    )
    response = _build_response(
        ranked,
        prepared.all_candidates,
        prepared.details_missing_place_ids,
        prepared.visit_at,
        weather_ignored=prepared.weather_ignored,
        excluded_all_closed=excluded_all_closed,
        excluded_closed_place_ids=excluded_closed_place_ids,
        origin_name=prepared.origin_name,
        travel_origin_toggle=prepared.travel_origin_toggle,
    )
    # 노출 상위 N곳과 별도로, 실제 채점을 받은 전체 후보를 개발자 패널에 보낸다.
    # 같은 변환 함수를 재사용해 점수·근거 필드가 사용자 카드와 어긋나지 않게 한다.
    debug_response = _build_response(
        scoring.ranked,
        prepared.all_candidates,
        prepared.details_missing_place_ids,
        prepared.visit_at,
        weather_ignored=prepared.weather_ignored,
        origin_name=prepared.origin_name,
    )
    scoring_candidates = sorted(
        [*debug_response.recommendations, *debug_response.unverified_recommendations],
        key=lambda item: item.scoring_rank or 10_000,
    )
    scoring_excluded_candidates = [
        ExcludedScoringCandidate(
            place_id=item.candidate.place_id,
            name=item.candidate.name,
            category=item.candidate.category,
            distance_km=round(item.candidate.distance_km, 2),
            reason=item.reason.value,
        )
        for item in excluded
    ]
    return response.model_copy(
        update={
            "elapsed_ms": round((timer() - started_at) * 1000, 2),
            "scoring_candidates": scoring_candidates,
            "scoring_excluded_candidates": scoring_excluded_candidates,
        }
    )


async def run_recommendation_pipeline_from_context(
    context: RecommendationContext | None,
    *,
    conditions: UserConditions | None = None,
    visit_at: datetime,
    search_radius_km: float,
    shown_place_ids: frozenset[str] = frozenset(),
    rejected_place_ids: frozenset[str] = frozenset(),
    recommendation_limit: int = DEFAULT_RECOMMENDATION_RESULT_LIMIT,
    ignore_operating_hours: bool = False,
    timer: Timer = perf_counter,
) -> RecommendationResponse:
    """prepare와 score를 연속 실행하는 기존 호환 진입점.

    A가 C에서 받은 RecommendationContext를 그대로 넘기면 D 내부(후보 변환→
    하드 필터→Scoring→Evidence→Explanation 조립)를 전부 처리해
    RecommendationResponse만 반환한다. C Tool을 직접 호출하지 않는다([TECH-02]).
    후보를 보충하지 않는 호출자(HTTP 추천 라우트 등)는 이 진입점만 쓰면 된다.

    각 인자의 의미와 호출자 책임은 `prepare_recommendation_from_context()`와
    `score_prepared_recommendation()` docstring을 본다.
    """
    # 경과 시간은 prepare까지 포함한 전체 구간으로 다시 잰다 —
    # score_prepared_recommendation()이 채운 값은 채점 구간만이라 여기서 덮어쓴다.
    started_at = timer()
    prepared = await prepare_recommendation_from_context(
        context,
        conditions=conditions,
        visit_at=visit_at,
        shown_place_ids=shown_place_ids,
        rejected_place_ids=rejected_place_ids,
        ignore_operating_hours=ignore_operating_hours,
    )
    response = await score_prepared_recommendation(
        prepared,
        search_radius_km=search_radius_km,
        recommendation_limit=recommendation_limit,
        timer=timer,
    )
    return response.model_copy(update={"elapsed_ms": round((timer() - started_at) * 1000, 2)})


async def rerank_with_concentration(
    response: RecommendationResponse,
    weather_condition: WeatherCondition | None,
    concentration: CandidateEnrichmentResponse,
    *,
    seek: bool,
    weather_reason: WeatherReason = None,
    # 1차와 같은 값. 안 넘기면 2차가 거리 결측을 비례 재분배해 **1차와 다른 자를
    # 쓴다** — 구 단위 요청에서 취향이 1/3에서 0.17로 깎인다(1.9.0). 2026-08-20에
    # 같은 계열의 사고가 있었다(CONCENTRATION_WEIGHTS에 taste 키가 없어 취향으로
    # 후보를 골라 놓고 최종 순위에서 취향을 뺐다).
    district_scoped: bool = False,
    # 1차와 같은 기준점 이름. 안 넘기면 근거 문장이 "현재 위치"로 폴백해 1차와
    # 2차가 같은 요청에서 다른 문장을 말하게 되므로, 호출자가 반드시 1차에 쓴
    # 값을 그대로 넘긴다(real_recommendation_provider.py).
    origin_name: str | None = None,
    timer: Timer = perf_counter,
) -> RecommendationResponse:
    """D의 2차 Scoring 진입점(D-040, concentration_intent AVOID/SEEK 전용).

    `response`는 1차 `run_recommendation_pipeline_from_context()` 결과(이미
    호출자가 지정한 개수로 좁혀진 상태 — RECOMMEND는 5개, SCHEDULE은 10개)다.
    여기서 새 Candidate를 다시 만들지 않는다 —
    `RecommendationItem.feature_scores`(weather/remaining_operating_time/distance)를
    그대로 재사용한다. concentration과 무관하게 이 값들은 변하지 않기 때문이다.
    `weather_condition`/`weather_reason`은 1차 호출과 같은 입력(`context`,
    `conditions`)으로 `resolve_weather_condition()`을 호출해 얻은 값이어야 한다 —
    근거 문장을 다시 조립하는 데만 쓰고, 점수 자체를 다시 계산하지는 않는다.
    호출자가 직접 판정 로직을 다시 구현하면(예: 옛 `to_weather_condition()`을
    계속 쓰면) 1차와 2차의 판정이 갈라질 수 있다 — 반드시
    `resolve_weather_condition()`을 통해 같은 값을 재사용해야 한다.
    `weather_reason`은 기본값 `None`이라, 호출자가 아직 안 넘겨도(현재
    `agent_runtime.py`가 그렇다) 동작은 그대로 유지되고 근거 문장만
    `weather_condition` 기반 라벨로 폴백한다.

    concentration 결측(C가 해당 후보에 no_data/unavailable을 반환) 처리는
    weather/remaining_operating_time과 동일한 패턴이다 — 그 후보만
    `redistribute_weights()`로 재분배한다.
    """
    started_at = timer()

    concentration_by_place_id = {result.place_id: result for result in concentration.candidates}
    unverified_place_ids = frozenset(item.place_id for item in response.unverified_recommendations)

    items = [*response.recommendations, *response.unverified_recommendations]

    order_key: list[tuple[float, float, str]] = []
    rescoring_context: dict[
        str,
        tuple[
            RecommendationItem,
            dict[str, float | None],
            dict[str, float],
            ConcentrationLevel | None,
        ],
    ] = {}

    for item in items:
        result = concentration_by_place_id.get(item.place_id)
        concentration_rate: float | None = None
        concentration_level: ConcentrationLevel | None = None
        if result is not None and result.status == "success" and result.concentration:
            forecast = result.concentration[0]
            concentration_rate = forecast.concentration_rate
            concentration_level = forecast.concentration_level

        feature_scores: dict[str, float | None] = dict(item.feature_scores)
        feature_scores["concentration"] = (
            None
            if concentration_rate is None
            else concentration_score(concentration_rate, seek=seek)
        )

        # 2차 가중치는 상수에서 고르지 않고 **1차가 실제로 채점한 키**로 조립한다.
        # 1차가 날씨 대신 요청 환경을 썼는지, 취향이 켜져 있었는지가 모두 여기서
        # 갈린다. 예전에는 CONCENTRATION_WEIGHTS를 그대로 썼는데, 그 상수에
        # taste 키가 없어서 **취향으로 후보를 골라 놓고 최종 순위에서는 취향을
        # 빼고 있었다**(2026-08-20). 합이 1.0이라 결측 재분배도 안 걸렸다.
        base_weights = weights_for_feature_scores(feature_scores)
        missing = [feature for feature in base_weights if feature_scores.get(feature) is None]
        weights_used = (
            equalize_weights(base_weights, missing)
            if district_scoped
            else redistribute_weights(base_weights, missing)
            if missing
            else dict(base_weights)
        )
        score = round(
            sum(
                feature_scores[feature] * weight  # type: ignore[operator]
                for feature, weight in weights_used.items()
            ),
            4,
        )

        order_key.append((score, item.distance_km, item.place_id))
        # concentration_level은 근거 문장 조립에만 쓰고 정렬 키에는 관여하지 않는다.
        rescoring_context[item.place_id] = (
            item,
            feature_scores,
            weights_used,
            concentration_level,
        )

    order_key.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    verified: list[RecommendationItem] = []
    unverified: list[RecommendationItem] = []

    for rank, (score, _distance_km, place_id) in enumerate(order_key, start=1):
        item, feature_scores, weights_used, concentration_level = rescoring_context[place_id]
        is_unverified = place_id in unverified_place_ids
        candidate = RankedCandidate(
            place_id=item.place_id,
            name=item.name,
            category=item.category,
            rank=rank,
            score=score,
            feature_scores=feature_scores,
            weights_used=weights_used,
            is_unverified=is_unverified,
            warnings=tuple(w for w in item.warnings if w != _NO_NOTABLE_EXPLANATION_WARNING),
            distance_km=item.distance_km,
            remaining_minutes=item.remaining_minutes,
            weather_condition=weather_condition,
            weather_reason=weather_reason,
            environment_type=item.environment_type,
            # 근거 문장도 1차와 같은 거리 기준으로 말해야 한다. 응답 필드로는
            # 아래에서 이월하고 있었는데 이 candidate에는 안 넘겨서, 같은 카드가
            # 필드로는 "400m/300초/도보", 문장으로는 "직선거리 약 110m"라고
            # 서로 다른 숫자를 말했다(2026-08-20). explanation._distance_sentence()가
            # 이 세 값으로 "걸어서 약 5분"인지 직선거리인지를 고른다.
            travel_distance_m=item.travel_distance_m,
            travel_duration_seconds=item.travel_duration_seconds,
            travel_mode=item.travel_mode,
            # 취향 근거 원문도 같은 이유로 이월한다. 위 도보 3필드와 **똑같은
            # 사고를 한 번 더 냈다**(2026-08-24 발견) — 응답 필드
            # `taste_evidence`는 아래에서 이월하고 있었는데 이 candidate에는 안
            # 넘겨서, 혼잡도 재순위를 탄 요청만 근거 문장이 인용문 대신 폴백
            # 문구("말씀하신 분위기와 잘 맞는 곳이에요.")로 떨어졌다.
            # 1차는 유사도 1위 조각을 쓰므로(scoring._taste_evidence_text),
            # 유사도 내림차순으로 실려 온 첫 인용문이 같은 값이다.
            taste_evidence_text=(item.taste_evidence[0].text if item.taste_evidence else None),
            taste_tag_label=item.taste_tag_label,
            taste_tag_documents=item.taste_tag_documents,
            taste_tag_score=item.taste_tag_score,
            taste_embedding_similarity=item.taste_embedding_similarity,
            taste_embedding_score=item.taste_embedding_score,
            taste_combined_score=item.taste_combined_score,
            concentration_level=concentration_level,
        )
        # feature_order를 넘기지 않는다 — build_evidence()가 feature_scores의 키로
        # concentration 포함 여부와 날씨/환경 중 어느 쪽인지를 함께 판단한다.
        evidence = build_evidence(candidate, origin_name=origin_name)
        explanations = build_explanations(evidence)
        warnings = list(candidate.warnings)
        if not explanations:
            warnings.append(_NO_NOTABLE_EXPLANATION_WARNING)

        new_item = RecommendationItem(
            place_id=item.place_id,
            name=item.name,
            category=item.category,
            distance_km=item.distance_km,
            remaining_minutes=item.remaining_minutes,
            # 2차는 후보를 다시 만들지 않고 1차 값을 재사용한다 — 운영 구간도
            # 혼잡도와 무관하게 변하지 않으므로 그대로 가져온다. 여기서 빠뜨리면
            # 혼잡도 재순위를 탄 요청만 이 필드가 조용히 사라진다.
            operating_hours_display=item.operating_hours_display,
            # 혼잡도 재순위는 후보를 다시 만들지 않으므로 실측 이동 정보도
            # 1차 값을 그대로 가져온다 — 여기서 빠뜨리면 2차를 탄
            # 요청만 이 필드가 조용히 사라진다.
            travel_distance_m=item.travel_distance_m,
            travel_duration_seconds=item.travel_duration_seconds,
            travel_mode=item.travel_mode,
            environment_type=item.environment_type,
            recommendation_reason=_recommendation_reason(candidate),
            explanations=list(explanations),
            warnings=warnings,
            score=evidence.score,
            feature_scores={
                contribution.feature: contribution.score for contribution in evidence.contributions
            },
            weights_used={
                contribution.feature: contribution.weight
                for contribution in evidence.contributions
                if contribution.weight is not None
            },
            # 2차는 후보를 다시 만들지 않으므로 취향 근거도 1차 값을 그대로
            # 가져온다 — 여기서 빠뜨리면 혼잡도 재순위를 탄 요청만 이 필드가
            # 조용히 사라진다(travel_distance_m과 같은 이유, 위 주석 참고).
            taste_evidence=item.taste_evidence,
            taste_tag_score=item.taste_tag_score,
            taste_tag_label=item.taste_tag_label,
            taste_tag_documents=item.taste_tag_documents,
            taste_tag_details=item.taste_tag_details,
            taste_embedding_similarity=item.taste_embedding_similarity,
            taste_embedding_score=item.taste_embedding_score,
            taste_combined_score=item.taste_combined_score,
            scoring_rank=rank,
            preference_tags=item.preference_tags,
            # 썸네일도 1차 값을 그대로 가져온다. 2차는 후보를 다시 만들지 않고
            # A가 붙여둔 값을 옮기기만 하면 된다 — 재조회 경로가 여기엔 없다.
            # 빠뜨리면 혼잡도 재순위를 탄 요청만 추천 카드 전체가 사진을 잃는다
            # (travel_distance_m·taste_evidence와 같은 유형, 위 주석 참고).
            image_url=item.image_url,
            image_url_fallback=item.image_url_fallback,
            # 분류 라벨도 같은 이유로 이월한다. 2차가 후보를 다시 만들지 않으므로
            # 여기 안 적으면 재순위를 탄 요청에서만 칩이 대분류로 되돌아간다.
            category_label=item.category_label,
        )
        (unverified if is_unverified else verified).append(new_item)

    return RecommendationResponse(
        recommendations=verified,
        unverified_recommendations=unverified,
        elapsed_ms=round((timer() - started_at) * 1000, 2),
        # 2차(혼잡도 재순위)는 1차가 이미 채점을 마친 후보만 다시 정렬할 뿐,
        # 하드 필터를 다시 태우지 않는다 — 1차가 걸러낸 폐점 후보 id는 그대로다.
        excluded_all_closed=response.excluded_all_closed,
        excluded_closed_place_ids=response.excluded_closed_place_ids,
        # 전환 제안도 1차 값을 그대로 이월한다. 재순위는 순위만 바꾸고 기준점
        # 판정(resolve_travel_origin_toggle)의 입력은 건드리지 않으므로 1차와
        # 같은 결론이다. 안 넘기면 혼잡도 재순위를 탄 요청만 "OO 기준으로 다시
        # 보기" 버튼을 잃는다(2026-08-24 발견, 위 taste_evidence_text와 같은 유형).
        travel_origin_toggle=response.travel_origin_toggle,
        scoring_candidates=response.scoring_candidates,
        scoring_excluded_candidates=response.scoring_excluded_candidates,
    )


async def rerank_with_co_visited(
    response: RecommendationResponse,
    co_visited_pairs: Sequence[tuple[str, str]],
    weather_condition: WeatherCondition | None,
    *,
    weather_reason: WeatherReason = None,
    origin_name: str | None = None,
    # `rerank_with_concentration()`과 같은 이유로 받는다 — 1차와 자를 맞춘다.
    district_scoped: bool = False,
    timer: Timer = perf_counter,
) -> RecommendationResponse:
    """D-092: RECOMMEND의 2차 Scoring 진입점. place_associations(B-owned, D-088)
    기반 "함께 방문된 이력"으로 `response`(1차, 또는 이미 혼잡도 2차를 탄 결과)를
    재순위한다. `rerank_with_concentration()`(D-040)과 같은 패턴이다 — 새
    Candidate를 다시 만들지 않고 `RecommendationItem.feature_scores`를 그대로
    재사용한다.

    `co_visited_pairs`는 A(agent_runtime.py)가 이번 응답의 candidate place_id
    집합으로 조회해 넘긴 (place_id, place_id) 쌍이다. B의 place_associations
    스키마(`CoVisitedHint`)를 여기서 직접 받지 않고 순수 튜플만 받는다 — D가
    B의 스키마를 몰라도 되게 하기 위해서다(B-01 "판단하지 않는 기억 장치"
    경계 원칙과 같은 이유를 D→B 방향에도 적용한다). 두 값 모두 이번 응답 안의
    후보라는 보장은 호출부(`app.schedule.associations.fetch_co_visited_hints`의
    dual `in.()` 필터)가 이미 하지만, 방어적으로 여기서도 한 번 더 걸러낸다 —
    이 함수가 항상 그 호출부만 거쳐 오리라는 보장은 계약이 아니라 관례다.

    `weather_condition`/`weather_reason`/`origin_name`은 1차 호출과 동일한
    입력으로 얻은 값이어야 한다 — 근거 문장을 다시 조립하는 데만 쓰고, 이
    함수가 날씨를 다시 판정하지는 않는다(`rerank_with_concentration()`과 동일).

    쌍이 하나도 없는 후보는 co_visited feature_scores가 0.0이다(결측이 아니다,
    `scoring.co_visited_score()` 참고) — `weights_for_feature_scores()`가 모든
    후보에 co_visited 축을 균일하게 반영해, 이 재순위를 탄 요청은 기존 3~5축
    가중치가 그만큼 양보한다(`build_weights()`).
    """
    started_at = timer()

    items = [*response.recommendations, *response.unverified_recommendations]
    unverified_place_ids = frozenset(item.place_id for item in response.unverified_recommendations)
    valid_ids = frozenset(item.place_id for item in items)
    name_by_place_id = {item.place_id: item.name for item in items}

    partners_by_place_id: dict[str, list[str]] = {place_id: [] for place_id in valid_ids}
    for left, right in co_visited_pairs:
        if left == right or left not in valid_ids or right not in valid_ids:
            continue
        partners_by_place_id[left].append(right)
        partners_by_place_id[right].append(left)

    hit_counts = {place_id: len(partners) for place_id, partners in partners_by_place_id.items()}
    max_hit_count = max(hit_counts.values(), default=0)

    order_key: list[tuple[float, float, str]] = []
    rescoring_context: dict[
        str,
        tuple[RecommendationItem, dict[str, float | None], dict[str, float], tuple[str, ...]],
    ] = {}

    for item in items:
        partner_ids = partners_by_place_id.get(item.place_id, [])
        hit_count = hit_counts.get(item.place_id, 0)

        feature_scores: dict[str, float | None] = dict(item.feature_scores)
        feature_scores["co_visited"] = co_visited_score(hit_count, max_hit_count)

        # 2차 가중치는 상수에서 고르지 않고 1차(혹은 혼잡도 2차)가 실제로 채점한
        # 키로 조립한다 — rerank_with_concentration()과 같은 이유(2026-08-20 사고
        # 재발 방지).
        base_weights = weights_for_feature_scores(feature_scores)
        missing = [feature for feature in base_weights if feature_scores.get(feature) is None]
        weights_used = (
            equalize_weights(base_weights, missing)
            if district_scoped
            else redistribute_weights(base_weights, missing)
            if missing
            else dict(base_weights)
        )
        score = round(
            sum(
                feature_scores[feature] * weight  # type: ignore[operator]
                for feature, weight in weights_used.items()
            ),
            4,
        )

        # 근거 문장에 쓸 이름 — 원래 등장 순서로 중복 제거, 최대 2개만.
        seen: set[str] = set()
        partner_names: list[str] = []
        for partner_id in partner_ids:
            name = name_by_place_id.get(partner_id)
            if name is None or name in seen:
                continue
            seen.add(name)
            partner_names.append(name)
            if len(partner_names) >= 2:
                break

        order_key.append((score, item.distance_km, item.place_id))
        rescoring_context[item.place_id] = (
            item,
            feature_scores,
            weights_used,
            tuple(partner_names),
        )

    order_key.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    verified: list[RecommendationItem] = []
    unverified: list[RecommendationItem] = []

    for rank, (score, _distance_km, place_id) in enumerate(order_key, start=1):
        item, feature_scores, weights_used, partner_names = rescoring_context[place_id]
        is_unverified = place_id in unverified_place_ids
        candidate = RankedCandidate(
            place_id=item.place_id,
            name=item.name,
            category=item.category,
            rank=rank,
            score=score,
            feature_scores=feature_scores,
            weights_used=weights_used,
            is_unverified=is_unverified,
            warnings=tuple(w for w in item.warnings if w != _NO_NOTABLE_EXPLANATION_WARNING),
            distance_km=item.distance_km,
            remaining_minutes=item.remaining_minutes,
            weather_condition=weather_condition,
            weather_reason=weather_reason,
            environment_type=item.environment_type,
            travel_distance_m=item.travel_distance_m,
            travel_duration_seconds=item.travel_duration_seconds,
            travel_mode=item.travel_mode,
            taste_evidence_text=(item.taste_evidence[0].text if item.taste_evidence else None),
            taste_tag_label=item.taste_tag_label,
            taste_tag_documents=item.taste_tag_documents,
            taste_tag_score=item.taste_tag_score,
            taste_embedding_similarity=item.taste_embedding_similarity,
            taste_embedding_score=item.taste_embedding_score,
            taste_combined_score=item.taste_combined_score,
            co_visited_place_names=partner_names,
        )
        evidence = build_evidence(candidate, origin_name=origin_name)
        explanations = build_explanations(evidence)
        warnings = list(candidate.warnings)
        if not explanations:
            warnings.append(_NO_NOTABLE_EXPLANATION_WARNING)

        new_item = RecommendationItem(
            place_id=item.place_id,
            name=item.name,
            category=item.category,
            distance_km=item.distance_km,
            remaining_minutes=item.remaining_minutes,
            operating_hours_display=item.operating_hours_display,
            travel_distance_m=item.travel_distance_m,
            travel_duration_seconds=item.travel_duration_seconds,
            travel_mode=item.travel_mode,
            environment_type=item.environment_type,
            recommendation_reason=_recommendation_reason(candidate),
            explanations=list(explanations),
            warnings=warnings,
            score=score,
            feature_scores={
                contribution.feature: contribution.score for contribution in evidence.contributions
            },
            weights_used={
                contribution.feature: contribution.weight
                for contribution in evidence.contributions
                if contribution.weight is not None
            },
            taste_evidence=item.taste_evidence,
            taste_tag_score=item.taste_tag_score,
            taste_tag_label=item.taste_tag_label,
            taste_tag_documents=item.taste_tag_documents,
            taste_tag_details=item.taste_tag_details,
            taste_embedding_similarity=item.taste_embedding_similarity,
            taste_embedding_score=item.taste_embedding_score,
            taste_combined_score=item.taste_combined_score,
            scoring_rank=rank,
            preference_tags=item.preference_tags,
            # 혼잡도 재순위와 같은 이유로 썸네일도 1차 값을 그대로 가져온다.
            image_url=item.image_url,
            image_url_fallback=item.image_url_fallback,
            # 분류 라벨도 같은 이유로 이월한다. 2차가 후보를 다시 만들지 않으므로
            # 여기 안 적으면 재순위를 탄 요청에서만 칩이 대분류로 되돌아간다.
            category_label=item.category_label,
        )
        (unverified if is_unverified else verified).append(new_item)

    return RecommendationResponse(
        recommendations=verified,
        unverified_recommendations=unverified,
        elapsed_ms=round((timer() - started_at) * 1000, 2),
        excluded_all_closed=response.excluded_all_closed,
        excluded_closed_place_ids=response.excluded_closed_place_ids,
        travel_origin_toggle=response.travel_origin_toggle,
        scoring_candidates=response.scoring_candidates,
        scoring_excluded_candidates=response.scoring_excluded_candidates,
    )


def resolve_weather_condition(
    context: RecommendationContext,
    conditions: UserConditions | None,
) -> tuple[WeatherCondition | None, WeatherReason]:
    """D-051: 사실(C 조회) 우선, 없으면 발화 값으로 판정한다.

    `context.weather`가 있으면 그게 C가 실제로 조회한 사실이므로 우선 쓴다 —
    NO_MENTION 경로뿐 아니라, AVOID/ENJOY인데 발화에서 5단계 값을 못 뽑아 C가
    대신 조회한 경우(PR #102)도 여기 해당한다. `weather_intent`를 그대로 넘겨서
    ENJOY의 강수 반전 등 의도 재해석이 두 경로 모두에 적용되게 한다.

    `context.weather`가 없으면(AVOID/ENJOY라 조회를 생략하고 발화 값을 뽑은 경우)
    `conditions.weather`로 대신 판정한다.

    반환하는 두 번째 값(`WeatherReason`)은 판정 원인(비/눈/폭염/한파)이다 —
    `WeatherCondition`만으로는 explanation.py가 "왜"를 알 수 없어서 근거 문장
    조립에 따로 필요하다(scoring.py::RankedCandidate.weather_reason 참고).

    `run_recommendation_pipeline_from_context()`(1차)가 내부에서 쓰는 것과 동일한
    함수를 `rerank_with_concentration()`(2차) 호출자도 그대로 써야 한다 — 같은
    `context`/`conditions`에 대해 두 판정이 서로 다른 로직으로 갈라지면 1차 설명과
    2차 설명이 어긋난다. public으로 둔 이유가 이것이다(D-051 "남은 것" 참고).
    """
    weather_intent = conditions.weather_intent if conditions is not None else None

    weather = context.weather
    if weather is not None and weather.status in {"success", "partial"}:
        data = weather.data
        if data is not None:
            return judge_weather_condition_from_facts(
                data.precipitation, data.sky, data.temperature_celsius, weather_intent
            )

    if (
        conditions is not None
        and conditions.weather_intent in (WeatherIntent.AVOID, WeatherIntent.ENJOY)
        and conditions.weather is not None
    ):
        return judge_weather_condition_from_stated(conditions.weather, conditions.weather_intent)

    return None, None


def _distance_denominator_offset_km(
    context: RecommendationContext | None,
    conditions: UserConditions | None,
) -> float:
    """거리 점수 분모(`max_distance_km`)에 더할 사용자 → 검색 기준점 거리(km).

    거리를 사용자 기준으로 재기 시작하면서(TP-112) 분자와 분모의 원점을 맞춰야
    한다. 두 경우를 가른다.

    **사용자가 이동시간을 말한 요청은 0.0이다.** 그때 분모는 `max_travel_time ×
    속도`이고, 실측 분기에서 같은 속도로 다시 나뉘어 "사용자가 말한 30분"이 그대로
    예산이 된다(`scoring.py::_travel_minutes_budget`). 시간 약속은 어디서 재든 같은
    값이라 애초에 원점이 없다 — 여기 거리를 더하면 "30분"이 사실상 30분+α가 된다.
    그 요청에서 전 후보가 0점이 나온다면 그건 "이 조건으로는 아무데도 30분 안에
    못 간다"는 사실이고, 분모로 감출 것이 아니다.

    **말하지 않은 요청은 사용자 → 기준점 거리를 더한다.** 그때 분모는
    `DEFAULT_PLACE_SEARCH_RADIUS_KM`인데, 이 값은 "타겟 주변 얼마를 뒤지는가"라는
    수집 정책에서 빌려온 거리라 원점이 타겟에 묶여 있다. 분자만 사용자 기준으로
    바꾸면 사용자가 타겟에서 멀 때 모든 후보가 분모를 넘겨 거리 Feature(가중치
    0.20)가 통째로 죽고, 순위가 날씨·운영시간만으로 정해진다.

    후보는 전부 타겟 중심 수집 반경 안에 있으므로 삼각부등식에 따라 사용자 기준
    거리는 `이 값 + 수집 반경`을 넘을 수 없다. 그래서 이 값을 더하면 어떤 후보도
    0으로 잘리지 않는다. 사용자가 기준점에 서 있으면 0.0이라 기존 분모로 되돌아간다.

    사용자 위치를 모르면(발화도 GPS도 없음) 0.0이다 — 그때는 거리 자체가 타겟
    기준으로 계산되므로 분모도 그대로 두어야 짝이 맞는다.

    **출발점이 검색 기준점으로 확정된 요청도 0.0이다**("안국역에서 10분",
    `conditions.travel_origin == TravelOrigin.SEARCH_CENTER`). 그때는
    `resolve_ranking_origin()`이 분자도 검색 기준점 기준으로 재므로(D-071)
    원점이 이미 타겟과 같다 — 이 오프셋을 더하면 사용자 위치와 타겟 사이의
    거리를 엉뚱하게 얹히게 된다.
    """
    if conditions is not None and conditions.travel_origin is TravelOrigin.SEARCH_CENTER:
        return 0.0
    if conditions is not None and conditions.max_travel_time is not None:
        return 0.0
    if context is None:
        return 0.0
    return resolve_user_to_target_km(context) or 0.0


def resolve_origin_name(
    context: RecommendationContext,
    conditions: UserConditions | None = None,
) -> str | None:
    """거리·경로의 기준점을 사용자에게 뭐라고 부를지 정한다.

    부르는 대상은 **랭킹 기준점**이다(`ranking_origin.resolve_ranking_origin`).
    거리와 경로를 사용자 위치에서 재므로 문장도 거기서 재야 한다 — 기준점만
    검색 중심 이름으로 부르면 "안국역에서 걸어서 12분"이라고 말해놓고 실제로는
    혜화역에서 잰 값을 싣게 된다(TP-112).

    C는 사실만 싣는다(D-051) — 좌표의 출처(`source`)와 사용자가 말한 문자열
    (`requested_query`)이 전부고, 그걸 문구로 옮기는 판정은 D가 한다. 기기 GPS가
    기준점이면 부를 이름이 없으므로 None을 돌려주고, 문장 쪽이 "현재 위치"로
    옮긴다(`explanation.py::_distance_sentence()`).

    `resolved_name`을 쓰지 않는다. 기준점이 지오코딩으로 풀리면 그 값이 도로명
    주소라(`providers/geocoding.py`) "서울특별시 종로구 사직로 161에서 걸어서 41분"이
    된다. `requested_query`는 수식어를 뗀 사용자 발화라 언제나 부를 수 있는
    이름이다(`tools/resolve_location.py::strip_location_modifiers()`).
    """
    origin = resolve_ranking_origin(context, conditions)
    if origin is None or origin.source == "device_gps":
        return None
    return origin.requested_query


def resolve_requested_environment(conditions: UserConditions | None) -> str | None:
    """날씨 언급이 없을 때만 사용자가 명시한 실내/실외를 Scoring에 넘긴다.

    `weather_intent`가 AVOID/ENJOY면 발화에 날씨가 있는 것이고(D-049), 그 경로는
    이미 날씨 판정이 실내/실외를 의도대로 반영한다("비 오는데 실내로" →
    BAD/indoor=1.00). 여기서 환경으로 갈아타면 같은 조건을 두 번 세는 셈이라
    기존 날씨 판정을 그대로 둔다.

    `any`(실내외 무관)는 `scoring.uses_environment_feature()`가 걸러낸다 —
    되묻기 기본값이 ANY라(D-053) 그 구분이 필요하다.
    """
    if conditions is None or conditions.environment is None:
        return None
    if conditions.weather_intent in (WeatherIntent.AVOID, WeatherIntent.ENJOY):
        return None
    return conditions.environment.value


def _is_weather_explicitly_ignored(
    context: RecommendationContext,
    conditions: UserConditions | None,
) -> bool:
    """weather Feature가 빠졌을 때 "제외했어요"와 "확인 못 했어요" 중 뭘 보여줄지 정한다.

    `conditions`가 있으면 `IGNORE`(상관없다고 명시)만 진짜 opt-out이다. 그 외에
    weather가 빠졌다면(AVOID/ENJOY인데 발화·조회 둘 다 실패 등) 사용자 선택이
    아니라 실패이므로 "확인 못 했어요" 쪽이 맞다. `conditions`가 없는 레거시
    호출자는 그런 구분을 할 수 없어 기존 동작(`context.weather is None`)을 그대로
    쓴다.
    """
    if conditions is not None:
        return conditions.weather_intent is WeatherIntent.IGNORE
    return context.weather is None


def _build_response(
    ranked: tuple[RankedCandidate, ...],
    candidates: tuple[ScoringCandidate, ...],
    details_missing_place_ids: frozenset[str],
    visit_at: datetime,
    *,
    weather_ignored: bool,
    excluded_all_closed: bool = False,
    excluded_closed_place_ids: Sequence[str] = (),
    origin_name: str | None = None,
    travel_origin_toggle: TravelOriginToggle | None = None,
) -> RecommendationResponse:
    candidate_by_id = {item.place_id: item for item in candidates}
    verified: list[RecommendationItem] = []
    unverified: list[RecommendationItem] = []

    for ranked_item in ranked:
        candidate = candidate_by_id[ranked_item.place_id]
        evidence = build_evidence(ranked_item, origin_name=origin_name)
        explanations = build_explanations(evidence)
        item = RecommendationItem(
            place_id=candidate.place_id,
            name=candidate.name,
            category=candidate.category,
            distance_km=round(candidate.distance_km, 2),
            remaining_minutes=_remaining_minutes(candidate, visit_at),
            operating_hours_display=_operating_hours_display(candidate),
            travel_distance_m=ranked_item.travel_distance_m,
            travel_duration_seconds=ranked_item.travel_duration_seconds,
            travel_mode=ranked_item.travel_mode,
            environment_type=candidate.environment_type,
            recommendation_reason=_recommendation_reason(ranked_item),
            explanations=list(explanations),
            warnings=list(ranked_item.warnings)
            + _extra_warnings(
                ranked_item,
                ranked_item.place_id in details_missing_place_ids,
                explanations,
                weather_ignored=weather_ignored,
            ),
            score=evidence.score,
            feature_scores={
                contribution.feature: contribution.score for contribution in evidence.contributions
            },
            weights_used={
                contribution.feature: contribution.weight
                for contribution in evidence.contributions
                if contribution.weight is not None
            },
            taste_evidence=[
                TasteEvidenceQuote(text=snippet.source_text, similarity=snippet.similarity)
                for snippet in ranked_item.taste_evidence
            ],
            taste_tag_score=ranked_item.taste_tag_score,
            taste_tag_label=ranked_item.taste_tag_label,
            taste_tag_documents=ranked_item.taste_tag_documents,
            taste_tag_details=[
                PreferenceTagScoreDetail(
                    code=detail.code,
                    label=detail.label,
                    positive_document_count=detail.positive_documents,
                    negative_document_count=detail.negative_documents,
                    candidate_max_positive_document_count=detail.candidate_max_positive_documents,
                    relative_score=detail.score,
                )
                for detail in ranked_item.taste_tag_details
            ],
            taste_embedding_similarity=ranked_item.taste_embedding_similarity,
            taste_embedding_score=ranked_item.taste_embedding_score,
            taste_combined_score=ranked_item.taste_combined_score,
            scoring_rank=ranked_item.rank,
        )
        (unverified if ranked_item.is_unverified else verified).append(item)

    return RecommendationResponse(
        recommendations=verified,
        unverified_recommendations=unverified,
        travel_origin_toggle=travel_origin_toggle,
        elapsed_ms=0,
        excluded_all_closed=excluded_all_closed,
        excluded_closed_place_ids=list(excluded_closed_place_ids),
    )


def _extra_warnings(
    ranked: RankedCandidate,
    details_missing: bool,
    explanations: tuple[str, ...],
    *,
    weather_ignored: bool,
) -> list[str]:
    """운영시간 결측 외에, 지금까지 조용히 생략되던 두 케이스를 warning으로 보충한다.

    (1) 날씨 결측으로 weather Feature 점수가 없는 경우 — 조회를 안 한 것(IGNORE)과
        조회에 실패한 것을 구분해 서로 다른 문구를 쓴다.
    (2) Feature 점수가 있어도 전부 임계값 미만이라 explanations가 비는 경우
    """
    extra: list[str] = []
    if details_missing and _OPERATING_HOURS_UNVERIFIED_WARNING not in ranked.warnings:
        extra.append(_DETAILS_MISSING_WARNING)
    # 요청 환경으로 채점한 실행에는 weather 키 자체가 없다. 그건 결측이 아니라
    # "이번 실행에 존재하지 않는 Feature"이므로 날씨 warning을 붙이지 않는다.
    if "weather" in ranked.feature_scores and ranked.feature_scores["weather"] is None:
        extra.append(_WEATHER_IGNORED_WARNING if weather_ignored else _WEATHER_MISSING_WARNING)
    if not explanations:
        extra.append(_NO_NOTABLE_EXPLANATION_WARNING)
    return extra


def _operating_hours_display(candidate: ScoringCandidate) -> str | None:
    """그 후보에 적용된 당일 운영 구간을 "09:00~18:00" 형태로 만든다.

    `candidate.operating_hours`는 `_operating_hours_from_context()`가 방문 시각에
    맞춰 이미 고른 값이라 여기서 요일·휴무를 다시 따지지 않는다. 영업 중이 아닌
    후보는 `_is_closed()`가 Scoring 단계에서 이미 걸러내므로, 여기 도달한 값은
    실제로 지금 적용 중인 구간이다.

    자정 마감은 두 가지 뜻으로 들어오므로 `open_time`까지 함께 봐야 한다
    (`operating_hours.py`, `candidate_mapper.py`):
    - `time.min ~ 자정`: 24시간 영업 → "00:00~23:59"로 쓰면 엉뚱하다.
    - `09:00 ~ 자정`: 원문의 "09:00~24:00"(당일 자정 종료). `strftime`을 그대로
      쓰면 "23:59"가 되어 실제보다 1분 일찍 닫는 것처럼 보인다.
    """
    hours = candidate.operating_hours
    if hours is None:
        return None
    if hours.open_time == hours.close_time:
        # 길이 0 구간은 표시할 시각이 없다는 뜻이다 — 유도한 정기 휴무처럼 그날
        # 구간이 원문에 없는 경우다(`candidate_mapper.py`). 그대로 포맷하면
        # "00:00~00:00"이 카드에 찍힌다.
        return None
    closes_at_midnight = hours.close_time in _MIDNIGHT_CLOSE_TIMES
    if hours.open_time == time.min and closes_at_midnight:
        return _ALL_DAY_OPERATING_HOURS_DISPLAY
    close_display = (
        _MIDNIGHT_CLOSE_DISPLAY if closes_at_midnight else hours.close_time.strftime("%H:%M")
    )
    return f"{hours.open_time.strftime('%H:%M')}~{close_display}"


def _remaining_minutes(
    candidate: ScoringCandidate,
    visit_at: datetime,
) -> int | None:
    hours = candidate.operating_hours
    if (
        hours is None
        or hours.is_regular_closure
        or not (hours.open_time <= visit_at.time() < hours.close_time)
    ):
        return None
    close_at = datetime.combine(
        visit_at.date(),
        hours.close_time,
        tzinfo=visit_at.tzinfo,
    )
    return max(0, int((close_at - visit_at).total_seconds() // 60))


# 응답 문구에 쓸 Feature 이름. 여기 없는 축은 "조건"으로 묶어 표현한다 —
# 이름을 못 찾아 문장이 깨지는 것보다 낫다.
_FEATURE_LABELS: Mapping[str, str] = {
    "weather": "날씨",
    "environment": "실내외",
    "remaining_operating_time": "운영시간",
    "distance": "거리",
    "taste": "취향",
    "concentration": "혼잡도",
    "co_visited": "동선",
}


def _recommendation_reason(ranked: RankedCandidate) -> str:
    """실제로 채점에 쓴 축을 그대로 말한다.

    전에는 "거리·날씨·운영시간"으로 고정돼 있었다. 취향 축이 붙은 뒤로는 그
    문구가 **실제 계산과 어긋난다** — 취향이 순위를 바꿔놓고 문장은 그 사실을
    숨기는 셈이라, 채점에 쓴 키에서 문구를 만든다.
    """
    labels = [
        _FEATURE_LABELS[feature] for feature in ranked.weights_used if feature in _FEATURE_LABELS
    ]
    axes = "·".join(labels) if labels else "여러"
    return f"{axes} 조건을 종합한 {ranked.rank}순위 추천이에요."

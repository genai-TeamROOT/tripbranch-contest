"""A → D / A → B 변환 함수 모음(부분): 검색 반경 계산, 혼잡도 항목 변환, 노출 기록 변환.

날씨 조건 변환(옛 `to_weather_condition()`)은 D-051로 D에 이관돼 제거됐다 — D가
`resolve_weather_condition()`으로 직접 판정한다(services/recommendation_pipeline.py).

역할: A가 D(Recommendation)에 넘길 값과 B(State)에 기록할 값을 만드는 변환
함수를 모아둔다. app.services.interpret.state_transform.to_user_conditions()
(B↔A)와 app.services.runtime.context_transform.to_agent_context_request()
(A↔C)와 같은 원칙으로, 이 파일의 각 함수도 정확히 명시된 두 구간만 담당한다 —
서로 다른 변환 지점을 섞지 않는다.
이 파일은 D 내부(app.domain.*, app.services.recommendation_pipeline)를 전혀
import하지 않는다 — D 호출은 app.services.runtime.real_recommendation_provider가
담당한다. 예외는 순수 도메인 계약인 app.domain.travel_route(TravelMode)뿐이다.

to_measured_travel_modes()는 A→C(경로 Tool 질의)에 쓰이지만 여기 둔다. 검색 반경·
시간 예산 속도·실측 이동수단이 한 요청의 세 얼굴이라, 흩어놓으면 한쪽만 바뀌었을
때 조용히 어긋나기 때문이다(D-042 성격의 불일치).
"""

from __future__ import annotations

from app.domain.travel_route import TravelMode
from app.place_search_policy import (
    DEFAULT_PLACE_SEARCH_RADIUS_KM,
    MAX_PLACE_SEARCH_RADIUS_KM,
    MIN_PLACE_SEARCH_RADIUS_KM,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.schemas import (
    RecommendationResponse,
    Transport,
    UserConditions,
)
from app.services.runtime.context_schemas import RecommendationContext
from app.state.service import RecommendedPlace, RecordRecommendationRequest

# A가 D에 넘기는 search_radius_km은 C가 실제 후보를 조회한 반경과 일관돼야
# 거리 점수 정규화가 어긋나지 않는다. 공통 기본값과 최소·최대 범위는
# place_search_policy에서 함께 관리한다.
_OTHER_KM_PER_MIN = 20 / 60  # 임시: 대중교통/자동차/미언급 공통 가정(20km/h)


def _radius_uses_walking_speed(conditions: UserConditions) -> bool:
    """검색 반경이 도보 속도로 만들어지는 요청인지 판정한다.

    반경 산정(to_search_radius_km)과 시간 예산 속도(to_search_radius_speed_km_per_min)가
    같은 조건을 봐야 한다 — 거리 점수의 분모가 반경을 이 속도로 되돌린 값이라
    (scoring.py::_travel_minutes_budget) 둘이 어긋나면 분자와 단위가 맞지 않는다.

    실측 이동수단 선택(to_measured_travel_modes)은 D-118부터 이 조건을 보지 않는다.
    예산이 측정 수단과 무관해졌으므로 수단은 후보의 거리로만 고르면 된다.
    """
    return conditions.transport is Transport.WALK or conditions.max_travel_time is None


def to_search_radius_speed_km_per_min(conditions: UserConditions) -> float:
    """이 요청이 검색 반경을 만들 때 쓴 속도(km/분)를 돌려준다 (D-118).

    거리 점수의 시간 예산이 이 값으로 반경을 소요시간으로 되돌린다
    (`domain/scoring.py::_travel_minutes_budget`). **예산을 측정한 이동수단이
    아니라 반경을 만든 속도로 나누는 것이 D-118의 핵심이다** — 한 순위표 안에서
    도보와 대중교통을 섞어 재면서 예산만 수단별로 고르면, 기본 반경 2.0km에서
    대중교통 예산이 6.0분이 되어 전환된 후보만 0점이 된다.

    `to_search_radius_km()`과 같은 분기(`_radius_uses_walking_speed()`)를 쓴다.
    두 함수가 다른 속도를 고르면 분자와 분모의 단위가 어긋난다.
    """
    return (
        WALKING_SPEED_KM_PER_MINUTE
        if _radius_uses_walking_speed(conditions)
        else _OTHER_KM_PER_MIN
    )


def to_measured_travel_modes(
    conditions: UserConditions | None,
    *,
    straight_line_km: float,
    switch_threshold_km: float,
) -> tuple[TravelMode, ...]:
    """후보 한 곳을 어떤 이동수단으로 실측할지 정한다 (D-118).

    **요청당 하나가 아니라 후보마다 고른다.** 같은 요청 안에서도 걸어갈 만한 곳과
    그렇지 않은 곳이 섞이기 때문이다. 판정 입력은 1차 채점이 이미 계산한
    직선거리이고, 호출부는 `_score_with_measured_routes()`가 상위 후보를 추린
    직후다.

    ```
    transport == WALK  → 도보 (거리 무관)
    transport == CAR   → 자동차 (거리 무관, 이동시간을 말하지 않았어도)
    그 외(PUBLIC·미지정):
        직선거리 ≤ 임계 → 도보
        직선거리 >  임계 → 도보·대중교통 둘 다 (호출부가 빠른 쪽을 고른다)
    ```

    `tools/schedule_travel.py::_select_mode()`와 같은 판정이다. 다른 점은 둘 다
    조회한다는 것뿐인데, 카카오 대중교통이 근거리에서 도보보다 느린 값을 주는
    경우가 실측으로 확인됐기 때문이다(2026-09-02, 아띠인력거 직선 0.42km에서
    대중교통 11.2분 대 도보 9.6분).

    **자동차 명시가 거리와 무관한 이유.** 예전에는 이동시간을 말하지 않은 요청을
    `transport`와 상관없이 도보로 쟀다 — 반경이 도보 기준이라는 이유였지만,
    자동차로 가겠다고 말한 사용자에게 도보 시간을 보여주고 있었다.

    빈 튜플은 돌려주지 않는다. 어떤 요청이든 최소한 도보 하나로는 잰다.
    """
    transport = conditions.transport if conditions is not None else None
    if transport is Transport.WALK:
        return (TravelMode.WALKING,)
    if transport is Transport.CAR:
        return (TravelMode.DRIVING,)
    if straight_line_km > switch_threshold_km:
        return (TravelMode.WALKING, TravelMode.TRANSIT)
    return (TravelMode.WALKING,)


def modes_for_judged_choice(
    conditions: UserConditions | None, choice: TravelMode
) -> tuple[TravelMode, ...]:
    """판정이 고른 수단을 **실측할 수단 집합**으로 옮긴다. (TP-227)

    추천에서 판정이 답하는 질문은 일정과 다르다. 일정은 "무엇으로 갈까"이지만
    추천은 **"대중교통도 재볼까"**다 — 임계를 넘은 후보는 도보와 대중교통을 둘 다
    조회하고 `_fastest_routes()`가 빠른 쪽을 남기기 때문이다(D-118). 카카오
    대중교통이 근거리에서 도보보다 느린 값을 주는 경우가 실측으로 확인돼 있어,
    판정이 대중교통을 골랐다고 그것만 재면 더 느린 값을 쓰게 된다.

    그래서 `TRANSIT` 판정은 `(WALKING, TRANSIT)`로 옮긴다. 양쪽 조회라는 D-118의
    설계를 그대로 두고, **어느 후보에 그 비용을 쓸지만** 판정이 정하는 것이다.

    도보·자동차 명시는 판정을 거치지 않으므로 여기 오지 않는다
    (`select_modes_for_segments()`가 그 두 경우에 판정을 부르지 않는다).
    """

    del conditions
    if choice is TravelMode.TRANSIT:
        return (TravelMode.WALKING, TravelMode.TRANSIT)
    return (choice,)


def to_search_radius_km(conditions: UserConditions) -> float:
    """A의 이동시간과 이동수단 조건을 검색 반경(km)으로 변환한다.

    max_travel_time이 없으면 공통 기본 반경을 사용한다. 도보는 70m/min,
    그 외 이동수단과 미언급은 임시로 20km/h를 적용하고, 결과는 공통
    최소·최대 검색 반경 구간으로 제한한다.
    """
    if conditions.max_travel_time is None:
        return DEFAULT_PLACE_SEARCH_RADIUS_KM

    speed_km_per_min = (
        WALKING_SPEED_KM_PER_MINUTE
        if _radius_uses_walking_speed(conditions)
        else _OTHER_KM_PER_MIN
    )
    radius = speed_km_per_min * conditions.max_travel_time
    return max(
        MIN_PLACE_SEARCH_RADIUS_KM,
        min(MAX_PLACE_SEARCH_RADIUS_KM, radius),
    )


def to_concentration_entries(context: RecommendationContext) -> list[object] | None:
    """C의 RecommendationContext.concentration을 D에 넘길 형태로 변환한다.

    (A 제안, D/C 확인 필요 — concentration-conditions.md §2.3/§4.2) 반환 타입은
    잠정이다: 원본 리스트를 그대로 넘길지, place_name으로 매핑한 dict로 바꿀지는
    D의 Scoring 입력 형태에 맞춰 확정한다.

    C가 아직 RecommendationContext에 concentration 필드를 추가하지 않은
    과도기에는(a-c-context-contract-draft.md §5.1 제안 상태) getattr 기본값으로
    안전하게 None을 반환한다 — 필드 자체가 없어도 AttributeError로 죽지 않는다.
    C가 필드를 추가하면 이 함수는 코드 변경 없이 정상 동작하기 시작한다.

    status가 "success"/"partial"일 때만 데이터를 반환한다. 그 외(no_data/
    unavailable, concentration 자체가 없음)는 None을 반환해, D가
    weather/remaining_operating_time 결측과 동일한 redistribute_weights()
    경로로 concentration 가중치를 재분배하게 한다.
    """
    concentration = getattr(context, "concentration", None)
    if concentration is None:
        return None
    if concentration.status not in ("success", "partial") or concentration.data is None:
        return None
    return list(concentration.data)


def to_record_recommendation_request(
    session_id: str,
    run_id: str,
    response: RecommendationResponse,
) -> RecordRecommendationRequest:
    """A→B 변환: D의 RecommendationResponse를 B의 RecordRecommendationRequest로 변환한다.

    recommendations + unverified_recommendations를 배열 순서 그대로 이어붙이고
    1부터 rank를 매긴다. response에 담긴 항목은 전부 이미 "실제로 노출된 것"
    이므로(계산만 하고 안 보여준 건 애초에 response에 없다) 별도 필터링 없이
    그대로 쓴다.
    """
    shown = [*response.recommendations, *response.unverified_recommendations]
    return RecordRecommendationRequest(
        session_id=session_id,
        run_id=run_id,
        recommended=[
            RecommendedPlace(
                place_id=item.place_id,
                rank=index + 1,
                distance_km=item.distance_km,
                remaining_minutes=item.remaining_minutes,
                environment_type=item.environment_type,
            )
            for index, item in enumerate(shown)
        ],
    )


__all__ = [
    "modes_for_judged_choice",
    "to_search_radius_km",
    "to_concentration_entries",
    "to_record_recommendation_request",
]

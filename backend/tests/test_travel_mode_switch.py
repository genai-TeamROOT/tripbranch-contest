"""후보별 실측 이동수단 선택과 그 결과 병합 (D-118).

여기서 확인하는 것은 세 가지다.

1. 임계(직선 0.85km) 위아래로 조회하는 이동수단이 갈리는가
2. 한 후보를 두 수단으로 조회했을 때 어느 값을 채점에 넘기는가
3. 그 값이 실제로 채점을 움직이는가

3번을 함께 두는 이유는 이 저장소에서 반복된 실패가 "조회는 바뀌었는데 소비 측
판정은 한 줄도 안 바뀌는" 모양이기 때문이다. 도보 Provider의 fallback은 성공
상태로 직선거리 추정을 돌려주므로(source=STRAIGHT_LINE_ESTIMATE), 그 값을 실측인
줄 알고 고르면 채점이 통째로 버린다.
"""

from datetime import UTC, datetime

import pytest

from app.agent_context.schemas import (
    ContextValue,
    Coordinates,
    PlaceCandidate,
    RecommendationContext,
    ResolvedLocation,
)
from app.domain.models import ScoringCandidate
from app.domain.scoring import prepare_candidates, score_prepared_candidates
from app.domain.travel_route import (
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
)
from app.place_search_policy import (
    WALKING_SPEED_KM_PER_MINUTE,
    transit_switch_straight_line_km,
)
from app.schemas import Transport, UserConditions
from app.services.recommendation_pipeline import prepare_recommendation_from_context
from app.services.runtime.agent_runtime import _fastest_routes, _fetch_travel_routes

_VISIT_AT = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)

# 경복궁. 위도만 움직여 거리를 검산 가능하게 둔다(위도 0.01도 ≈ 1.112km).
_ORIGIN = Coordinates(latitude=37.5796, longitude=126.9770)
# 기준점에서 북쪽 약 0.33km — 임계(0.85km) 아래.
_NEAR = Coordinates(latitude=37.5826, longitude=126.9770)
# 기준점에서 북쪽 약 1.11km — 임계 위.
_FAR = Coordinates(latitude=37.5896, longitude=126.9770)

_SWITCH_KM = transit_switch_straight_line_km(20)


def _place(place_id: str, coordinate: Coordinates) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        name=f"{place_id} 카페",
        category="cafe",
        location=coordinate,
        operating_schedule={"availability": "all_day", "rules": [], "closure_rules": []},
    )


def _context(places: list[PlaceCandidate]) -> RecommendationContext:
    return RecommendationContext(
        location=ContextValue(
            status="success",
            data=ResolvedLocation(
                requested_query="경복궁",
                resolved_name="경복궁",
                source="query",
                location=_ORIGIN,
            ),
        ),
        places=ContextValue(status="success", data=places),
    )


class _RecordingRouteTool:
    """어떤 이동수단으로 어느 후보를 물었는지만 기록하는 더블."""

    def __init__(self) -> None:
        self.queries: list[tuple[TravelMode, tuple[str, ...]]] = []

    async def execute(self, query):  # type: ignore[no-untyped-def]
        self.queries.append(
            (query.mode, tuple(destination.place_id for destination in query.destinations))
        )

        class _Result:
            routes = ()

        return _Result()


async def _asked(
    places: list[PlaceCandidate], conditions: UserConditions | None = None
) -> dict[TravelMode, tuple[str, ...]]:
    context = _context(places)
    prepared = await prepare_recommendation_from_context(
        context, conditions=conditions or UserConditions(), visit_at=_VISIT_AT
    )
    tool = _RecordingRouteTool()

    await _fetch_travel_routes(tool, context, prepared, conditions)

    return dict(tool.queries)


# --- 1. 임계로 갈리는가 ------------------------------------------------------


@pytest.mark.asyncio
async def test_near_candidate_is_asked_only_by_walking() -> None:
    asked = await _asked([_place("near", _NEAR)])

    assert asked == {TravelMode.WALKING: ("near",)}


@pytest.mark.asyncio
async def test_far_candidate_is_asked_by_both_modes() -> None:
    """임계를 넘으면 도보와 대중교통을 둘 다 묻고, 호출부가 빠른 쪽을 고른다."""
    asked = await _asked([_place("far", _FAR)])

    assert asked == {
        TravelMode.WALKING: ("far",),
        TravelMode.TRANSIT: ("far",),
    }


@pytest.mark.asyncio
async def test_mixed_candidates_split_by_distance() -> None:
    """한 요청 안에서 후보마다 갈린다 — 가까운 곳까지 대중교통에 묻지 않는다."""
    asked = await _asked([_place("near", _NEAR), _place("far", _FAR)])

    assert asked == {
        TravelMode.WALKING: ("near", "far"),
        TravelMode.TRANSIT: ("far",),
    }


@pytest.mark.asyncio
async def test_car_request_asks_driving_only_even_for_a_near_candidate() -> None:
    """자동차 명시는 거리와 무관하다. 이동시간을 말하지 않아도 도보로 재지 않는다."""
    asked = await _asked(
        [_place("near", _NEAR), _place("far", _FAR)],
        UserConditions(transport=Transport.CAR),
    )

    assert asked == {TravelMode.DRIVING: ("near", "far")}


@pytest.mark.asyncio
async def test_walk_request_never_asks_transit() -> None:
    asked = await _asked([_place("far", _FAR)], UserConditions(transport=Transport.WALK))

    assert asked == {TravelMode.WALKING: ("far",)}


# --- 2. 두 값 중 무엇을 채점에 넘기는가 --------------------------------------


def _route(
    mode: TravelMode,
    source: RouteSource,
    duration_seconds: int | None,
    status: RouteStatus = RouteStatus.SUCCESS,
) -> TravelRoute:
    return TravelRoute(
        place_id="far",
        mode=mode,
        status=status,
        source=source,
        distance_m=1200 if duration_seconds is not None else None,
        duration_seconds=duration_seconds,
    )


def test_fastest_route_wins_between_two_measurements() -> None:
    walking = _route(TravelMode.WALKING, RouteSource.KAKAO_WALKING, 1500)
    transit = _route(TravelMode.TRANSIT, RouteSource.KAKAO_TRANSIT, 900)

    assert _fastest_routes([walking, transit]) == (transit,)
    assert _fastest_routes([transit, walking]) == (transit,)


def test_slower_transit_does_not_replace_walking() -> None:
    """근거리에서 대중교통이 도보보다 느린 경우가 실제로 있다.

    2026-09-02 실측: 아띠인력거(직선 0.42km) 대중교통 11.2분 대 도보 9.6분.
    """
    walking = _route(TravelMode.WALKING, RouteSource.KAKAO_WALKING, 576)
    transit = _route(TravelMode.TRANSIT, RouteSource.KAKAO_TRANSIT, 672)

    assert _fastest_routes([walking, transit]) == (walking,)


def test_measured_route_beats_a_faster_estimate() -> None:
    """추정이 더 짧아도 실측을 남긴다 — 추정을 고르면 채점이 그 후보를 통째로 버린다.

    도보 조회가 실패하면 Tool이 직선거리 추정으로 메우는데(성공 상태로 돌아온다)
    그 값은 실제 대중교통 시간보다 짧게 나오기 쉽다. 시간만 보고 고르면
    `_applied_travel_route()`가 그 추정을 버리고, 후보 하나가 실측을 잃은 탓에
    `_consistent_routes()`가 회차 전체를 직선거리로 내린다.
    """
    estimate = _route(TravelMode.WALKING, RouteSource.STRAIGHT_LINE_ESTIMATE, 600)
    transit = _route(TravelMode.TRANSIT, RouteSource.KAKAO_TRANSIT, 900)

    assert _fastest_routes([estimate, transit]) == (transit,)


def test_failed_transit_falls_back_to_the_walking_value() -> None:
    """대중교통이 실패해도 그 후보를 통째로 빼지 않는다 — 도보 값이 남는다."""
    walking = _route(TravelMode.WALKING, RouteSource.KAKAO_WALKING, 1500)
    failed = _route(
        TravelMode.TRANSIT,
        RouteSource.KAKAO_TRANSIT,
        None,
        status=RouteStatus.UNAVAILABLE,
    )

    assert _fastest_routes([walking, failed]) == (walking,)


def test_only_failures_are_still_reported() -> None:
    """둘 다 실패하면 실패를 그대로 넘긴다 — 소비 측이 실패를 볼 수 있어야 한다."""
    failed = _route(
        TravelMode.TRANSIT,
        RouteSource.KAKAO_TRANSIT,
        None,
        status=RouteStatus.UNAVAILABLE,
    )

    assert _fastest_routes([failed]) == (failed,)


# --- 3. 그 값이 채점을 움직이는가 --------------------------------------------


def test_transit_route_actually_moves_the_distance_score() -> None:
    """전환된 후보의 대중교통 실측이 거리 점수에 그대로 반영된다.

    예산을 측정 수단으로 고르던 시절에는 이 점수가 0.0이었다 — 반경 2.0km를
    20km/h로 되돌린 6.0분을 14.28분이 넘겼기 때문이다. 그때는 대중교통을 부르고도
    전환된 후보만 거리 가중치 0.20을 통째로 잃었다.
    """
    candidate = ScoringCandidate(
        place_id="far",
        name="먼 카페",
        category="cafe",
        environment_type="indoor",
        distance_km=1.112,
        operating_hours=None,
    )
    prepared = prepare_candidates([candidate], now=datetime(2026, 7, 28, 15, 0))
    result = score_prepared_candidates(
        prepared.eligible_candidates,
        weather_condition=None,
        max_distance_km=2.0,
        travel_budget_speed_km_per_min=WALKING_SPEED_KM_PER_MINUTE,
        travel_routes=[_route(TravelMode.TRANSIT, RouteSource.KAKAO_TRANSIT, 857)],
    )

    ranked = result.ranked[0]
    # 예산 28.57분에 14.28분이면 절반이 남는다.
    assert ranked.feature_scores["distance"] == pytest.approx(0.5, abs=0.01)
    assert ranked.travel_mode is TravelMode.TRANSIT


def test_switch_threshold_matches_the_documented_value() -> None:
    """임계값이 D-118에 적은 0.85km 그대로인지 못 박는다.

    우회 계수를 나누는 것을 빠뜨리면 1.4km가 되어, "도보 20분 초과"라고 적어놓고
    실제로는 33분짜리부터 전환하게 된다.
    """
    assert _SWITCH_KM == pytest.approx(0.85, abs=0.005)

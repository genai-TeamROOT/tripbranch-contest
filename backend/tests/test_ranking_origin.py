"""랭킹 기준점이 검색 기준점이 아니라 사용자 위치라는 것을 못 박는다(TP-112).

후보를 **모으는** 중심(검색 기준점)과 후보를 **줄 세우는** 기준점(사용자 위치)이
다르다. 사용자 위치가 없으면 예전처럼 검색 기준점으로 돌아간다 — 기존 테스트가
전부 그 경로라, 여기서 사용자 위치를 채우지 않으면 새 코드가 한 줄도 실행되지
않는다.
"""

from datetime import UTC, datetime

import pytest

from app.agent_context.schemas import (
    ContextValue,
    Coordinates,
    PlaceCandidate,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
)
from app.domain.candidate_mapper import map_context_to_scoring_candidates
from app.domain.ranking_origin import (
    haversine_km,
    resolve_ranking_origin,
    resolve_travel_origin_toggle,
    resolve_user_to_target_km,
)
from app.schemas import TravelOrigin, UserConditions
from app.services.recommendation_pipeline import (
    prepare_recommendation_from_context,
    resolve_origin_name,
    run_recommendation_pipeline_from_context,
)
from app.services.runtime.agent_runtime import _fetch_travel_routes

_VISIT_AT = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)

# 경복궁. 이 파일의 모든 좌표는 위도만 움직여 거리 계산을 검산 가능하게 둔다
# (위도 0.01도 ≈ 1.112km).
_TARGET = Coordinates(latitude=37.5796, longitude=126.9770)
# 타겟에서 북쪽 1.112km. 사용자에게서는 가장 멀다.
_NORTH = Coordinates(latitude=37.5896, longitude=126.9770)
# 타겟에서 남쪽 1.112km. 타겟 기준으로는 북쪽 후보와 정확히 동점이다.
_SOUTH = Coordinates(latitude=37.5696, longitude=126.9770)
# 타겟에서 남쪽 5.560km. 남쪽 후보와 4.448km, 북쪽 후보와 6.672km 떨어져 있다.
_USER = Coordinates(latitude=37.5296, longitude=126.9770)


def _location_value(
    coordinate: Coordinates,
    *,
    requested_query: str,
    source: str = "query",
) -> ContextValue[ResolvedLocation]:
    return ContextValue(
        status="success",
        data=ResolvedLocation(
            requested_query=requested_query,
            resolved_name=requested_query,
            source=source,
            location=coordinate,
        ),
    )


def _place(place_id: str, coordinate: Coordinates) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        name=f"{place_id} 카페",
        category="cafe",
        location=coordinate,
        operating_schedule={"availability": "all_day", "rules": [], "closure_rules": []},
    )


def _context(
    *,
    user_location: ContextValue[ResolvedLocation] | None = None,
    places: list[PlaceCandidate] | None = None,
) -> RecommendationContext:
    return RecommendationContext(
        location=_location_value(_TARGET, requested_query="경복궁"),
        user_location=user_location,
        places=ContextValue(
            status="success",
            data=places if places is not None else [_place("place-a", _NORTH)],
            provider_metadata=[
                ProviderMetadata(
                    source="fake_place",
                    status="success",
                    retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
                )
            ],
        ),
    )


def _user_here() -> ContextValue[ResolvedLocation]:
    return _location_value(_USER, requested_query="사당역")


# --- 기준점 선택 ------------------------------------------------------------


def test_ranking_origin_prefers_user_location() -> None:
    origin = resolve_ranking_origin(_context(user_location=_user_here()))

    assert origin is not None
    assert origin.requested_query == "사당역"
    assert origin.location == _USER


def test_ranking_origin_falls_back_to_search_center() -> None:
    """발화도 GPS도 없는 요청은 예전처럼 검색 기준점으로 줄 세운다."""
    origin = resolve_ranking_origin(_context())

    assert origin is not None
    assert origin.requested_query == "경복궁"


def test_user_to_target_km_is_none_without_user_location() -> None:
    assert resolve_user_to_target_km(_context()) is None


def test_user_to_target_km_measures_user_to_search_center() -> None:
    distance = resolve_user_to_target_km(_context(user_location=_user_here()))

    assert distance == pytest.approx(5.56, abs=0.01)


def test_user_to_target_km_is_zero_when_user_stands_on_search_center() -> None:
    context = _context(user_location=_location_value(_TARGET, requested_query="경복궁"))

    assert resolve_user_to_target_km(context) == pytest.approx(0.0, abs=0.001)


# --- 거리 계산 --------------------------------------------------------------


def test_distance_is_measured_from_user_not_search_center() -> None:
    candidates = map_context_to_scoring_candidates(
        _context(user_location=_user_here(), places=[_place("place-a", _NORTH)]),
        visit_at=_VISIT_AT,
    )

    # 타겟 기준이면 1.112km다. 사용자 기준이라 6.672km가 나와야 한다.
    assert candidates[0].distance_km == pytest.approx(6.672, abs=0.01)


def test_distance_falls_back_to_search_center_without_user_location() -> None:
    candidates = map_context_to_scoring_candidates(
        _context(places=[_place("place-a", _NORTH)]),
        visit_at=_VISIT_AT,
    )

    assert candidates[0].distance_km == pytest.approx(1.112, abs=0.01)


# --- 근거 문장이 부르는 이름 -------------------------------------------------


def test_origin_name_calls_the_user_location() -> None:
    """거리를 사용자 기준으로 쟀으면 문장도 사용자 위치를 불러야 한다."""
    assert resolve_origin_name(_context(user_location=_user_here())) == "사당역"


def test_origin_name_is_none_when_user_location_came_from_gps() -> None:
    """기기 GPS는 부를 이름이 없다 — 문장 쪽이 "현재 위치"로 옮긴다."""
    context = _context(
        user_location=_location_value(_USER, requested_query="gps_location", source="device_gps")
    )

    assert resolve_origin_name(context) is None


# --- 거리 점수 분모 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_denominator_offset_added_when_travel_time_is_not_stated() -> None:
    """이동시간을 말하지 않으면 분모(수집 반경)의 원점이 타겟이라 보정이 필요하다."""
    prepared = await prepare_recommendation_from_context(
        _context(user_location=_user_here()),
        conditions=UserConditions(),
        visit_at=_VISIT_AT,
    )

    assert prepared.distance_denominator_offset_km == pytest.approx(5.56, abs=0.01)


@pytest.mark.asyncio
async def test_denominator_is_untouched_when_travel_time_is_stated() -> None:
    """사용자가 말한 시간 약속은 어디서 재든 같은 값이라 원점이 없다.

    여기에 사용자→타겟 거리를 더하면 "30분"이 사실상 30분+α가 된다.
    """
    prepared = await prepare_recommendation_from_context(
        _context(user_location=_user_here()),
        conditions=UserConditions(max_travel_time=30),
        visit_at=_VISIT_AT,
    )

    assert prepared.distance_denominator_offset_km == 0.0


@pytest.mark.asyncio
async def test_denominator_is_untouched_without_user_location() -> None:
    """거리를 타겟 기준으로 쟀으면 분모도 타겟 기준이어야 짝이 맞는다."""
    prepared = await prepare_recommendation_from_context(
        _context(),
        conditions=UserConditions(),
        visit_at=_VISIT_AT,
    )

    assert prepared.distance_denominator_offset_km == 0.0


# --- 순위가 실제로 바뀌는가 --------------------------------------------------


async def _ranked_place_ids(
    context: RecommendationContext,
    *,
    conditions: UserConditions | None = None,
) -> list[str]:
    response = await run_recommendation_pipeline_from_context(
        context,
        conditions=conditions,
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )
    items = [*response.recommendations, *response.unverified_recommendations]
    return [item.place_id for item in items]


@pytest.mark.asyncio
async def test_user_side_candidate_wins_a_target_side_tie() -> None:
    """이 카드의 핵심. 타겟 기준으로 동점인 두 후보를 사용자 기준이 가른다.

    두 후보는 검색 기준점에서 정확히 같은 거리(1.112km)에 있고 방향만 반대다.
    타겟 기준으로 줄을 세우면 영영 동점이라 place_id 순으로 밀리지만, 실제로
    이동하는 사용자에게는 남쪽 후보가 2.2km 더 가깝다.
    """
    places = [_place("place-a", _NORTH), _place("place-b", _SOUTH)]

    with_user = await _ranked_place_ids(
        _context(user_location=_user_here(), places=places),
        conditions=UserConditions(),
    )
    without_user = await _ranked_place_ids(
        _context(places=places),
        conditions=UserConditions(),
    )

    assert with_user[0] == "place-b", "사용자 쪽 후보가 앞서야 한다"
    # 사용자 위치가 없으면 타겟 기준 동점이라 place_id 오름차순으로 갈린다.
    assert without_user[0] == "place-a"


@pytest.mark.asyncio
async def test_distance_feature_survives_a_far_user() -> None:
    """분모를 보정하지 않으면 두 후보 모두 0점이 되어 순위가 거리와 무관해진다.

    사용자가 타겟에서 5.56km 떨어져 있어 후보들의 사용자 기준 거리(4.4~6.7km)가
    기본 반경 2.0km를 크게 넘는다. 보정이 없으면 clamp로 둘 다 정확히 0.0이 되고,
    위 테스트의 순위 역전도 일어나지 않는다.
    """
    places = [_place("place-a", _NORTH), _place("place-b", _SOUTH)]
    response = await run_recommendation_pipeline_from_context(
        _context(user_location=_user_here(), places=places),
        conditions=UserConditions(),
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )
    items = [*response.recommendations, *response.unverified_recommendations]
    scores = {item.place_id: item.score for item in items}

    assert scores["place-b"] > scores["place-a"], "거리가 순위에 실제로 기여해야 한다"


# --- 실측 경로도 같은 기준점에서 --------------------------------------------


class _RecordingRouteTool:
    """경로 조회가 어느 좌표에서 출발했는지만 기록하는 더블.

    호출 수는 보지 않는다 — 임계를 넘은 후보는 도보·대중교통으로 두 번 조회되므로
    (D-118) 여기서 확인할 것은 "모든 조회가 같은 기준점에서 출발했는가"뿐이다.
    """

    def __init__(self) -> None:
        self.origins: list[tuple[float, float]] = []

    async def execute(self, query):  # type: ignore[no-untyped-def]
        self.origins.append((query.origin.latitude, query.origin.longitude))

        class _Result:
            routes = ()

        return _Result()


@pytest.mark.asyncio
async def test_travel_routes_start_from_the_user() -> None:
    """거리와 경로가 다른 기준점을 쓰면 실측 유무에 따라 자가 갈린다."""
    context = _context(user_location=_user_here(), places=[_place("place-a", _NORTH)])
    prepared = await prepare_recommendation_from_context(
        context, conditions=UserConditions(), visit_at=_VISIT_AT
    )
    tool = _RecordingRouteTool()

    await _fetch_travel_routes(tool, context, prepared)

    assert set(tool.origins) == {(_USER.latitude, _USER.longitude)}


@pytest.mark.asyncio
async def test_travel_routes_fall_back_to_search_center() -> None:
    context = _context(places=[_place("place-a", _NORTH)])
    prepared = await prepare_recommendation_from_context(
        context, conditions=UserConditions(), visit_at=_VISIT_AT
    )
    tool = _RecordingRouteTool()

    await _fetch_travel_routes(tool, context, prepared)

    assert set(tool.origins) == {(_TARGET.latitude, _TARGET.longitude)}


def test_haversine_km_matches_latitude_degrees() -> None:
    """이 파일의 기대값이 기대는 환산(위도 0.01도 ≈ 1.112km)을 고정한다."""
    assert haversine_km(37.5796, 126.9770, 37.5896, 126.9770) == pytest.approx(1.112, abs=0.001)


# --- travel_origin: "안국역에서 10분" vs "안국역 근처에 10분" (D-071) --------


def test_ranking_origin_uses_search_center_when_travel_origin_says_so() -> None:
    """"안국역에서 10분"은 사용자 위치가 있어도 검색 기준점을 그대로 써야 한다."""
    origin = resolve_ranking_origin(
        _context(user_location=_user_here()),
        UserConditions(travel_origin=TravelOrigin.SEARCH_CENTER),
    )

    assert origin is not None
    assert origin.requested_query == "경복궁"


def test_ranking_origin_ignores_override_without_conditions() -> None:
    """conditions를 안 넘기면(기존 호출부) D-067 기본 동작 그대로다."""
    origin = resolve_ranking_origin(_context(user_location=_user_here()))

    assert origin is not None
    assert origin.requested_query == "사당역"


def test_ranking_origin_default_when_travel_origin_is_none() -> None:
    """"안국역 근처에 10분"(travel_origin=null)은 D-067 기본값(사용자 위치)을 그대로 쓴다."""
    origin = resolve_ranking_origin(
        _context(user_location=_user_here()),
        UserConditions(),
    )

    assert origin is not None
    assert origin.requested_query == "사당역"


@pytest.mark.asyncio
async def test_denominator_offset_is_zero_when_travel_origin_is_search_center() -> None:
    """분자가 검색 기준점 기준으로 재므로 사용자→기준점 거리를 분모에 얹지 않는다."""
    prepared = await prepare_recommendation_from_context(
        _context(user_location=_user_here()),
        conditions=UserConditions(travel_origin=TravelOrigin.SEARCH_CENTER),
        visit_at=_VISIT_AT,
    )

    assert prepared.distance_denominator_offset_km == 0.0


def test_origin_name_calls_the_search_center_when_travel_origin_overrides() -> None:
    """"안국역에서 10분"의 근거 문장은 사용자 위치가 아니라 안국역(검색 기준점)을 불러야 한다."""
    context = _context(user_location=_user_here())

    assert (
        resolve_origin_name(context, UserConditions(travel_origin=TravelOrigin.SEARCH_CENTER))
        == "경복궁"
    )


@pytest.mark.asyncio
async def test_travel_routes_start_from_search_center_when_travel_origin_overrides() -> None:
    """실측 경로도 거리·근거 문장과 같은 기준점(검색 기준점)에서 출발해야 한다."""
    context = _context(user_location=_user_here(), places=[_place("place-a", _NORTH)])
    conditions = UserConditions(travel_origin=TravelOrigin.SEARCH_CENTER)
    prepared = await prepare_recommendation_from_context(
        context, conditions=conditions, visit_at=_VISIT_AT
    )
    tool = _RecordingRouteTool()

    await _fetch_travel_routes(tool, context, prepared, conditions)

    assert set(tool.origins) == {(_TARGET.latitude, _TARGET.longitude)}


# --- 비차단형 전환 제안(TravelOriginToggle, D-071) ---------------------------


def test_toggle_offers_search_center_when_origin_undetermined() -> None:
    """"안국역 근처에 10분"류(travel_origin=None)는 두 기준점이 다르면 전환을 제안한다."""
    toggle = resolve_travel_origin_toggle(
        _context(user_location=_user_here()),
        UserConditions(max_travel_time=10),
    )

    assert toggle is not None
    assert toggle.alternative_origin == TravelOrigin.SEARCH_CENTER
    assert toggle.alternative_origin_name == "경복궁"


def test_toggle_is_none_when_travel_origin_already_determined() -> None:
    """"안국역에서 10분"처럼 이미 확정된 요청엔 되물을 이유가 없다."""
    toggle = resolve_travel_origin_toggle(
        _context(user_location=_user_here()),
        UserConditions(max_travel_time=10, travel_origin=TravelOrigin.SEARCH_CENTER),
    )

    assert toggle is None


def test_toggle_is_none_without_max_travel_time() -> None:
    """이동시간 제약이 없으면 출발점 논의 자체가 의미 없다."""
    toggle = resolve_travel_origin_toggle(
        _context(user_location=_user_here()),
        UserConditions(),
    )

    assert toggle is None


def test_toggle_is_none_without_user_location() -> None:
    """전환할 대상(사용자 위치)을 모르면 제안할 것이 없다."""
    toggle = resolve_travel_origin_toggle(
        _context(),
        UserConditions(max_travel_time=10),
    )

    assert toggle is None


def test_toggle_is_none_when_origins_are_the_same_point() -> None:
    """두 기준점이 같은 지점이면 전환해도 답이 똑같다."""
    same_point = _location_value(_TARGET, requested_query="경복궁")
    toggle = resolve_travel_origin_toggle(
        _context(user_location=same_point),
        UserConditions(max_travel_time=10),
    )

    assert toggle is None


def test_toggle_is_none_when_search_center_came_from_device_gps() -> None:
    """검색 기준점이 기기 GPS면 부를 이름이 없어 제안을 만들 수 없다."""
    context = _context(user_location=_user_here())
    gps_context = context.model_copy(
        update={
            "location": _location_value(
                _TARGET, requested_query="gps_location", source="device_gps"
            )
        }
    )

    toggle = resolve_travel_origin_toggle(gps_context, UserConditions(max_travel_time=10))

    assert toggle is None


@pytest.mark.asyncio
async def test_prepared_result_carries_toggle_end_to_end() -> None:
    """전환 제안이 RecommendationResponse까지 그대로 실린다."""
    context = _context(user_location=_user_here(), places=[_place("place-a", _NORTH)])
    response = await run_recommendation_pipeline_from_context(
        context,
        conditions=UserConditions(max_travel_time=10),
        visit_at=_VISIT_AT,
        search_radius_km=2.0,
    )

    assert response.travel_origin_toggle is not None
    assert response.travel_origin_toggle.alternative_origin == TravelOrigin.SEARCH_CENTER
    assert response.travel_origin_toggle.alternative_origin_name == "경복궁"

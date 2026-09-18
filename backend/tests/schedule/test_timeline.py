"""일정 시간표 계산기 (TP-215)."""

from __future__ import annotations

from datetime import datetime

from app.place_search_policy import (
    NON_WALKING_SPEED_KM_PER_MINUTE,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.schedule.timeline import (
    FALLBACK_TRAVEL_MINUTES,
    TimelineStop,
    build_timeline,
    estimated_travel_minutes,
    travel_speed_km_per_minute,
)
from app.schemas import Transport, UserConditions

START = datetime(2026, 9, 1, 13, 0)


def _fixed(minutes: int):
    return lambda _from, _to: minutes


def test_arrival_is_the_running_sum_of_visit_and_travel() -> None:
    """도착시각이 체류·이동의 누적과 항상 일치한다 — 이 카드의 핵심 불변식이다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=60),
            TimelineStop(place_id="b", visit_duration_min=90),
            TimelineStop(place_id="c", visit_duration_min=60),
        ],
        start_at=START,
        travel_minutes=_fixed(20),
    )

    arrivals = [stop.arrival_at.strftime("%H:%M") for stop in timeline.stops]
    # 13:00 도착 -> 60분 체류 -> 20분 이동 -> 14:20 -> 90분 체류 -> 20분 이동 -> 16:10
    assert arrivals == ["13:00", "14:20", "16:10"]
    assert [stop.order for stop in timeline.stops] == [1, 2, 3]


def test_total_duration_matches_first_arrival_to_last_departure() -> None:
    """총합이 항목들과 어긋나지 않는다. 지금까지는 LLM이 준 값을 그대로 실었다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=60),
            TimelineStop(place_id="b", visit_duration_min=90),
        ],
        start_at=START,
        travel_minutes=_fixed(20),
    )

    # 60 + 20 + 90
    assert timeline.total_duration_min == 170
    assert timeline.ends_at == datetime(2026, 9, 1, 15, 50)


def test_last_stop_has_no_travel_to_next() -> None:
    timeline = build_timeline(
        [TimelineStop(place_id="a", visit_duration_min=60)],
        start_at=START,
        travel_minutes=_fixed(20),
    )

    assert timeline.stops[-1].travel_to_next_min is None
    assert timeline.total_duration_min == 60


def test_waiting_is_inserted_when_arriving_before_opening() -> None:
    """운영 시작 전에 도착하면 대기시간이 잡히고 방문 시작이 그만큼 밀린다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=30),
            # 15:00 개장. 첫 장소가 13:30에 끝나고 이동 20분이면 13:50 도착이다.
            TimelineStop(place_id="b", visit_duration_min=60, opens_at_min=15 * 60),
        ],
        start_at=START,
        travel_minutes=_fixed(20),
    )

    second = timeline.stops[1]
    assert second.arrival_at.strftime("%H:%M") == "13:50"
    assert second.waiting_before_visit_min == 70
    assert second.visit_start_at.strftime("%H:%M") == "15:00"
    assert second.departure_at.strftime("%H:%M") == "16:00"
    # 대기도 총 소요시간에 들어간다 — 사용자가 실제로 쓰는 시간이다.
    assert timeline.total_duration_min == 180


def test_no_waiting_when_already_open_or_hours_unknown() -> None:
    """근거 없이 기다리게 만들지 않는다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=30, opens_at_min=9 * 60),
            TimelineStop(place_id="b", visit_duration_min=30, opens_at_min=None),
        ],
        start_at=START,
        travel_minutes=_fixed(10),
    )

    assert [stop.waiting_before_visit_min for stop in timeline.stops] == [0, 0]


def test_schedule_crossing_midnight_keeps_its_order() -> None:
    """자정을 넘겨도 순서와 시각이 깨지지 않는다.

    기존 후처리는 "HH:MM"을 자정 기준 분으로 파싱해서 01:30이 90분으로 읽혔고,
    23:00에 시작한 일정과 비교하면 순서가 뒤집혔다.
    """

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=90),
            TimelineStop(place_id="b", visit_duration_min=60),
        ],
        start_at=datetime(2026, 9, 1, 23, 0),
        travel_minutes=_fixed(30),
    )

    first, second = timeline.stops
    assert first.arrival_at == datetime(2026, 9, 1, 23, 0)
    assert second.arrival_at == datetime(2026, 9, 2, 1, 0)
    assert second.arrival_at > first.arrival_at
    assert timeline.total_duration_min == 180


def test_midnight_arrival_does_not_wait_for_that_days_opening() -> None:
    """자정을 넘겨 도착한 뒤 그날 개장까지 몇 시간을 기다리는 일정을 만들지 않는다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=120),
            TimelineStop(place_id="b", visit_duration_min=60, opens_at_min=9 * 60),
        ],
        start_at=datetime(2026, 9, 1, 23, 0),
        travel_minutes=_fixed(30),
    )

    assert timeline.stops[1].waiting_before_visit_min == 0


def test_missing_travel_information_uses_the_fallback() -> None:
    """구간 정보를 못 구해도 순간이동하는 일정을 만들지 않는다."""

    timeline = build_timeline(
        [
            TimelineStop(place_id="a", visit_duration_min=60),
            TimelineStop(place_id="b", visit_duration_min=60),
        ],
        start_at=START,
        travel_minutes=lambda _from, _to: None,
    )

    assert timeline.stops[0].travel_to_next_min == FALLBACK_TRAVEL_MINUTES
    assert timeline.stops[1].arrival_at.strftime("%H:%M") == "14:15"


def test_empty_input_returns_an_empty_timeline() -> None:
    timeline = build_timeline([], start_at=START, travel_minutes=_fixed(10))

    assert timeline.stops == ()
    assert timeline.total_duration_min == 0
    assert timeline.ends_at is None


def test_same_input_is_deterministic() -> None:
    stops = [
        TimelineStop(place_id="a", visit_duration_min=60),
        TimelineStop(place_id="b", visit_duration_min=45),
    ]
    first = build_timeline(stops, start_at=START, travel_minutes=_fixed(25))
    second = build_timeline(stops, start_at=START, travel_minutes=_fixed(25))

    assert first == second


def test_estimated_travel_minutes_reads_the_distance_matrix_both_ways() -> None:
    """거리 행렬은 방향이 없다(haversine) — 반대 방향 키도 찾아야 한다."""

    resolve = estimated_travel_minutes(
        {("a", "b"): 2.1}, speed_km_per_minute=WALKING_SPEED_KM_PER_MINUTE
    )

    assert resolve("a", "b") == 30
    assert resolve("b", "a") == 30
    assert resolve("a", "c") is None


def test_estimated_travel_minutes_never_returns_zero() -> None:
    """아주 가까운 두 곳도 0분이 아니다 — 같은 시각에 두 곳에 있는 일정이 된다."""

    resolve = estimated_travel_minutes(
        {("a", "b"): 0.01}, speed_km_per_minute=NON_WALKING_SPEED_KM_PER_MINUTE
    )

    assert resolve("a", "b") == 1


def test_speed_assumption_mirrors_the_search_radius_rule() -> None:
    """검색 반경을 만든 가정과 같은 속도를 써야 한다.

    다르면 "반경 안에서 모은 후보인데 시간 안에 못 돈다"가 정상이 된다
    (place_search_policy.NON_WALKING_SPEED_KM_PER_MINUTE 주석).
    """

    walking = UserConditions(transport=Transport.WALK, max_travel_time=30)
    unspecified = UserConditions()
    driving = UserConditions(transport=Transport.CAR, max_travel_time=30)

    assert travel_speed_km_per_minute(walking) == WALKING_SPEED_KM_PER_MINUTE
    assert travel_speed_km_per_minute(unspecified) == WALKING_SPEED_KM_PER_MINUTE
    assert travel_speed_km_per_minute(driving) == NON_WALKING_SPEED_KM_PER_MINUTE

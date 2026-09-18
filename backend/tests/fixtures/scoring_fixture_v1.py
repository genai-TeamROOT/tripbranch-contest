"""Scoring v1 평가 Fixture v1.

역할: `score_candidates()`를 반복 검증하기 위한 고정 시나리오 모음. 각
`ScoringFixtureCase`는 입력(후보, 기준 시각, 날씨, 검색 반경, 이전 노출·거절
ID)과 기대 결과(정렬 순서, 제외 ID, 미확인 ID)를 함께 담는다.
날씨·남은 운영시간 결측 조합(둘 다 있음 / 날씨만 없음 / 운영시간만 미확인 /
둘 다 없음)과 폐점·이전 노출·거절 제외, tie-break를 모두 포함한다.
설계 근거: `docs/design/recommendation-evidence-fixture.md` 참고.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from app.domain.models import OperatingHours, ScoringCandidate, WeatherCondition
from app.domain.travel_route import RouteSource, RouteStatus, TravelMode, TravelRoute

# 모든 케이스가 공유하는 기준 시각 (14:00).
NOW = datetime(2026, 7, 23, 14, 0, 0)

_MUSEUM_OPEN = ScoringCandidate(
    place_id="p1",
    name="박물관A",
    category="museum",
    environment_type="indoor",
    distance_km=0.5,
    operating_hours=OperatingHours(time(9, 0), time(18, 0)),  # 마감까지 240분
)
_CAFE_CLOSING_SOON = ScoringCandidate(
    place_id="p2",
    name="카페B",
    category="cafe",
    environment_type="indoor",
    distance_km=0.8,
    operating_hours=OperatingHours(time(9, 0), time(15, 0)),  # 마감까지 60분
)
_PARK_CLOSED = ScoringCandidate(
    place_id="p3",
    name="공원C",
    category="park",
    environment_type="outdoor",
    distance_km=0.3,
    operating_hours=OperatingHours(time(9, 0), time(13, 0)),  # 14:00엔 이미 마감
)
_GALLERY_UNKNOWN_HOURS = ScoringCandidate(
    place_id="p4",
    name="갤러리D",
    category="gallery",
    environment_type="indoor",
    distance_km=0.9,
    operating_hours=None,
)
_RESTAURANT_FAR = ScoringCandidate(
    place_id="p5",
    name="맛집E",
    category="restaurant",
    environment_type="indoor",
    distance_km=1.2,
    operating_hours=OperatingHours(time(11, 0), time(23, 0)),  # 마감까지 540분(캡)
)

_AMPLE_HOURS = OperatingHours(time(9, 0), time(18, 0))
_TIE_NEAR_A = ScoringCandidate(
    place_id="z-near",
    name="Z",
    category="museum",
    environment_type="indoor",
    distance_km=0.5,
    operating_hours=_AMPLE_HOURS,
)
_TIE_NEAR_B = ScoringCandidate(
    place_id="a-near",
    name="A",
    category="museum",
    environment_type="indoor",
    distance_km=0.5,
    operating_hours=_AMPLE_HOURS,
)
_TIE_FAR = ScoringCandidate(
    place_id="a-far",
    name="A-far",
    category="museum",
    environment_type="indoor",
    distance_km=1.0,
    operating_hours=_AMPLE_HOURS,
)


@dataclass(frozen=True)
class ScoringFixtureCase:
    """`score_candidates()` 시나리오 1건과 기대 결과."""

    name: str
    purpose: str
    candidates: tuple[ScoringCandidate, ...]
    now: datetime
    weather_condition: WeatherCondition | None
    max_distance_km: float
    shown_place_ids: tuple[str, ...] = ()
    rejected_place_ids: tuple[str, ...] = ()
    expected_ranked_place_ids: tuple[str, ...] = field(default_factory=tuple)
    expected_excluded_place_ids: frozenset[str] = field(default_factory=frozenset)
    expected_unverified_place_ids: frozenset[str] = field(default_factory=frozenset)
    # 실측 도보 경로. 후보 중 하나라도 빠지면 전부 직선거리로 채점된다.
    travel_routes: tuple[TravelRoute, ...] = field(default_factory=tuple)


def _walking_route(place_id: str, duration_seconds: int) -> TravelRoute:
    return TravelRoute(
        place_id=place_id,
        mode=TravelMode.WALKING,
        status=RouteStatus.SUCCESS,
        source=RouteSource.KAKAO_WALKING,
        distance_m=duration_seconds,
        duration_seconds=duration_seconds,
    )


SCORING_FIXTURE_V1: tuple[ScoringFixtureCase, ...] = (
    ScoringFixtureCase(
        name="baseline_all_features_present",
        purpose="날씨·남은 운영시간·거리 모두 결측 없이 정렬되는 기본 케이스",
        candidates=(_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR),
        now=NOW,
        weather_condition=WeatherCondition.BAD,
        max_distance_km=1.5,
        expected_ranked_place_ids=("p1", "p5", "p2"),
    ),
    ScoringFixtureCase(
        name="weather_missing",
        purpose="날씨만 결측일 때 남은 운영시간·거리로 재분배되는 케이스",
        candidates=(_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR),
        now=NOW,
        weather_condition=None,
        max_distance_km=1.5,
        expected_ranked_place_ids=("p1", "p5", "p2"),
    ),
    ScoringFixtureCase(
        name="operating_hours_unknown",
        purpose="운영시간 미확인 후보가 날씨·거리로만 재분배되어 계산되는 케이스",
        candidates=(_MUSEUM_OPEN, _GALLERY_UNKNOWN_HOURS),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        expected_ranked_place_ids=("p1", "p4"),
        expected_unverified_place_ids=frozenset({"p4"}),
    ),
    ScoringFixtureCase(
        name="weather_and_operating_hours_both_missing",
        purpose="날씨·남은 운영시간이 모두 결측이면 거리만 100% 가중치로 계산되는 케이스",
        candidates=(_GALLERY_UNKNOWN_HOURS,),
        now=NOW,
        weather_condition=None,
        max_distance_km=1.5,
        expected_ranked_place_ids=("p4",),
        expected_unverified_place_ids=frozenset({"p4"}),
    ),
    ScoringFixtureCase(
        name="closed_place_is_excluded",
        purpose="기준 시각에 마감된 후보가 하드 필터로 제외되는 케이스",
        candidates=(_MUSEUM_OPEN, _PARK_CLOSED),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        expected_ranked_place_ids=("p1",),
        expected_excluded_place_ids=frozenset({"p3"}),
    ),
    ScoringFixtureCase(
        name="shown_and_rejected_are_excluded",
        purpose="이전 노출·거절 ID가 결과에서 제외되는 케이스",
        candidates=(_MUSEUM_OPEN, _CAFE_CLOSING_SOON),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        shown_place_ids=("p1",),
        rejected_place_ids=("p2",),
        expected_ranked_place_ids=(),
        expected_excluded_place_ids=frozenset({"p1", "p2"}),
    ),
    ScoringFixtureCase(
        name="tie_break_uses_distance_then_place_id",
        purpose="점수가 동일할 때 거리 오름차순 → place_id 오름차순으로 정렬되는 케이스",
        candidates=(_TIE_FAR, _TIE_NEAR_A, _TIE_NEAR_B),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        expected_ranked_place_ids=("a-near", "z-near", "a-far"),
    ),
    ScoringFixtureCase(
        name="walking_routes_reorder_by_measured_time",
        purpose=(
            "전원 실측이면 직선거리가 아니라 도보 소요시간으로 순위가 정해진다 — "
            "직선으로는 p1이 더 가깝지만 실제로 걸으면 p5가 빠른 경우"
        ),
        candidates=(_MUSEUM_OPEN, _RESTAURANT_FAR),
        now=NOW,
        weather_condition=None,
        max_distance_km=2.0,
        travel_routes=(
            _walking_route("p1", 1500),  # 25분
            _walking_route("p5", 300),  # 5분
        ),
        expected_ranked_place_ids=("p5", "p1"),
    ),
    ScoringFixtureCase(
        name="walking_routes_partially_missing_fall_back_together",
        purpose=(
            "하나라도 실측이 없으면 전부 직선거리로 채점한다 — 낙관도가 다른 두 "
            "기준이 섞이면 실측이 있는 후보만 손해를 보기 때문"
        ),
        candidates=(_MUSEUM_OPEN, _RESTAURANT_FAR),
        now=NOW,
        weather_condition=None,
        max_distance_km=2.0,
        travel_routes=(_walking_route("p1", 1500),),
        expected_ranked_place_ids=("p1", "p5"),
    ),
)

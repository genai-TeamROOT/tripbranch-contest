"""D-07 추천 품질 평가 Fixture — C 정규화 Context Fixture 기준.

역할: `tests/fixtures/agent_context/*.json`(C 제공)을 `run_recommendation_pipeline_from_context()`
로 실행했을 때의 기대 순위·점수·가중치 재분배·제외 결과를 담는다.
`scoring_fixture_v1.py`와 달리 손으로 만든 `ScoringCandidate`가 아니라 C 계약
그대로의 Context JSON을 입력으로 써서, candidate_mapper.py(거리 계산·
environment_type 매핑·운영시간 파싱)까지 포함한 전체 D 파이프라인을 검증한다.

기대값은 1단계 Inspection 테스트(`test_recommendation_context_fixture_inspection.py`)
실제 산출값을 Scoring 공식(기본 가중치 weather=0.4/remaining_operating_time=0.4/
distance=0.2, 결측 시 남은 항목끼리 비례 재분배)으로 역산 검증한 뒤 확정했다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_VISIT_AT = datetime.fromisoformat("2026-08-15T11:00:00+09:00")
_SEARCH_RADIUS_KM = 2.0


@dataclass(frozen=True)
class ExpectedItem:
    """추천 결과 1건에 대한 기대값."""

    place_id: str
    score: float
    feature_scores: dict[str, float | None]
    weights_used: dict[str, float]


@dataclass(frozen=True)
class ContextFixtureCase:
    """C Context Fixture 1개를 D 파이프라인으로 돌렸을 때의 기대 결과."""

    name: str
    filename: str
    purpose: str
    visit_at: datetime = _VISIT_AT
    search_radius_km: float = _SEARCH_RADIUS_KM
    shown_place_ids: frozenset[str] = field(default_factory=frozenset)
    rejected_place_ids: frozenset[str] = field(default_factory=frozenset)
    expected_recommended: tuple[ExpectedItem, ...] = ()
    expected_unverified: tuple[ExpectedItem, ...] = ()
    expected_excluded_place_ids: frozenset[str] = field(default_factory=frozenset)
    note: str | None = None


_DEFAULT_WEIGHTS = {"weather": 0.4, "remaining_operating_time": 0.4, "distance": 0.2}
_NO_WEATHER_WEIGHTS = {"remaining_operating_time": 2 / 3, "distance": 1 / 3}
_NO_REMAINING_TIME_WEIGHTS = {"weather": 2 / 3, "distance": 1 / 3}

CONTEXT_FIXTURE_EXPECTATIONS: tuple[ContextFixtureCase, ...] = (
    ContextFixtureCase(
        name="success_basic_ranking",
        filename="success.json",
        purpose="결측 없이 날씨·운영시간·거리 3-Feature로 정렬되는 기본 케이스",
        expected_recommended=(
            ExpectedItem(
                place_id="126508",
                score=1,
                feature_scores={
                    "weather": 1.0,
                    "remaining_operating_time": 1.0,
                    "distance": 1.0,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
            # 국립민속박물관은 cultural_facility(실내)다. 이전에는 candidate_mapper가
            # 이 값을 몰라 environment_type=unknown으로 떨어져 날씨 점수가 0.85였다.
            # 실내로 정상 판정되면서 맑은 날 적합도가 0.7로 내려간다(순위는 그대로).
            ExpectedItem(
                place_id="130100",
                score=0.8515,
                feature_scores={
                    "weather": 0.7,
                    "remaining_operating_time": 1.0,
                    "distance": 0.8575,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
        ),
        note=(
            "130100(국립민속박물관, category=cultural_facility)은 candidate_mapper.py의 "
            "_environment_type()이 cultural_facility를 정상 인식해(2026-08-04, C의 "
            "bde29a3+4d37713로 해결) environment_type=indoor로 처리된다. 맑은 날 "
            "적합도는 GOOD+indoor=0.7이라 score=0.8515다. 순위(126508 우선)는 해결 "
            "전후 항상 동일했다. 해결 전에는 environment_type=unknown이라 weather=0.85/"
            "score=0.9115였다([D-07] 문서 기록 참고)."
        ),
    ),
    ContextFixtureCase(
        name="success_bad_weather_environment_fit",
        filename="success_bad_weather.json",
        purpose="나쁜 날씨에서 실내가 야외보다 우선되는지 확인",
        expected_recommended=(
            ExpectedItem(
                place_id="bad-weather-museum-1",
                score=0.9811,
                feature_scores={
                    "weather": 1.0,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9055,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
            ExpectedItem(
                place_id="bad-weather-park-1",
                score=0.6742,
                feature_scores={
                    "weather": 0.3,
                    "remaining_operating_time": 1.0,
                    "distance": 0.771,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="success_operating_schedule_excludes_closed",
        filename="success_operating_schedule.json",
        purpose="영업 중·24시간 후보는 남고 폐점 후보(schedule-closed-1)는 하드 필터로 제외",
        expected_recommended=(
            ExpectedItem(
                place_id="schedule-open-1",
                score=0.9133,
                feature_scores={
                    "weather": 0.8,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9665,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
            ExpectedItem(
                place_id="schedule-all-day-1",
                score=0.9000,
                feature_scores={
                    "weather": 0.8,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
        ),
        expected_excluded_place_ids=frozenset({"schedule-closed-1"}),
        note="schedule-closed-1은 06:00-10:00 운영, visit_at=11:00 기준 이미 마감이라 제외.",
    ),
    ContextFixtureCase(
        name="partial_weather_unavailable_redistribution",
        filename="partial_weather_unavailable.json",
        purpose="날씨 Provider 실패 시 운영시간·거리로 2/3·1/3 재분배되는지 확인",
        expected_recommended=(
            ExpectedItem(
                place_id="126508",
                score=1.0,
                feature_scores={
                    "weather": None,
                    "remaining_operating_time": 1.0,
                    "distance": 1.0,
                },
                weights_used=_NO_WEATHER_WEIGHTS,
            ),
            ExpectedItem(
                place_id="130100",
                score=0.9525,
                feature_scores={
                    "weather": None,
                    "remaining_operating_time": 1.0,
                    "distance": 0.8575,
                },
                weights_used=_NO_WEATHER_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="partial_place_details_unverified_separation",
        filename="partial_place_details.json",
        purpose="운영정보 있는 후보는 recommendations, 없는 후보는 unverified로 분리",
        expected_recommended=(
            ExpectedItem(
                place_id="partial-detail-known-1",
                score=0.8713,
                feature_scores={
                    "weather": 0.7,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9565,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
        ),
        expected_unverified=(
            ExpectedItem(
                place_id="partial-detail-missing-1",
                score=0.7667,
                feature_scores={
                    "weather": 0.7,
                    "remaining_operating_time": None,
                    "distance": 0.9,
                },
                weights_used=_NO_REMAINING_TIME_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="missing_weather_ignored_redistribution",
        filename="missing_weather.json",
        purpose="날씨 미언급(Tool 미실행)도 Provider 실패와 동일하게 재분배되지만 경고 문구는 다름",
        expected_recommended=(
            ExpectedItem(
                place_id="weather-ignored-cafe-1",
                score=0.9888,
                feature_scores={
                    "weather": None,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9665,
                },
                weights_used=_NO_WEATHER_WEIGHTS,
            ),
            ExpectedItem(
                place_id="weather-ignored-park-1",
                score=0.9473,
                feature_scores={
                    "weather": None,
                    "remaining_operating_time": 1.0,
                    "distance": 0.842,
                },
                weights_used=_NO_WEATHER_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="missing_operating_hours_all_unverified",
        filename="missing_operating_hours.json",
        purpose="전체 후보 운영시간 결측 시 전부 unverified로 분리되고 날씨·거리로 재분배",
        expected_unverified=(
            ExpectedItem(
                place_id="hours-missing-museum-1",
                score=0.7845,
                feature_scores={
                    "weather": 0.7,
                    "remaining_operating_time": None,
                    "distance": 0.9535,
                },
                weights_used=_NO_REMAINING_TIME_WEIGHTS,
            ),
            ExpectedItem(
                place_id="hours-missing-cafe-1",
                score=0.7667,
                feature_scores={
                    "weather": 0.7,
                    "remaining_operating_time": None,
                    "distance": 0.9,
                },
                weights_used=_NO_REMAINING_TIME_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="insufficient_candidates_single_result",
        filename="insufficient_candidates.json",
        purpose="후보가 1개뿐이어도 자동 반경 확장 없이 그 상태 그대로 반환",
        expected_recommended=(
            ExpectedItem(
                place_id="only-candidate-1",
                score=0.9107,
                feature_scores={
                    "weather": 0.8,
                    "remaining_operating_time": 1.0,
                    "distance": 0.9535,
                },
                weights_used=_DEFAULT_WEIGHTS,
            ),
        ),
    ),
    ContextFixtureCase(
        name="no_place_candidates_empty_result",
        filename="no_place_candidates.json",
        purpose="장소 검색 결과가 없으면 예외 없이 빈 추천 결과를 반환",
    ),
)

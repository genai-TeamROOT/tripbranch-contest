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


def _context(
    *,
    schedule: dict | None,
    # TourAPI contenttypeid에서 나올 수 있는 값만 쓴다("cafe"는 실제 경로에 없다).
    category: str = "restaurant",
    lcls_systm3: str | None = None,
) -> RecommendationContext:
    return RecommendationContext(
        location=ContextValue(
            status="success",
            data=ResolvedLocation(
                requested_query="경복궁",
                resolved_name="경복궁",
                source="query",
                location=Coordinates(latitude=37.5796, longitude=126.9770),
            ),
        ),
        places=ContextValue(
            status="success",
            data=[
                PlaceCandidate(
                    place_id="place-1",
                    name="후보 장소",
                    category=category,
                    lcls_systm3=lcls_systm3,
                    location=Coordinates(latitude=37.5806, longitude=126.9770),
                    operating_schedule=schedule,
                )
            ],
            provider_metadata=[
                ProviderMetadata(
                    source="fake_place",
                    status="success",
                    retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
                )
            ],
        ),
    )


def test_maps_public_context_to_scoring_candidate() -> None:
    context = _context(
        schedule={
            "availability": "scheduled",
            "rules": [
                {
                    "months": [7],
                    "weekdays": ["friday"],
                    "time_ranges": [
                        {
                            "open_time": "09:00",
                            "close_time": "18:00",
                            "crosses_midnight": False,
                        }
                    ],
                }
            ],
            "closure_rules": [],
        }
    )

    candidates = map_context_to_scoring_candidates(
        context,
        visit_at=datetime(2026, 7, 24, 12, 0),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.distance_km == pytest.approx(0.111, abs=0.001)
    assert candidate.environment_type == "indoor"
    assert candidate.operating_hours is not None
    assert candidate.operating_hours.open_time.isoformat() == "09:00:00"
    assert candidate.operating_hours.close_time.isoformat() == "18:00:00"
    assert candidate.raw_source == "fake_place"


def test_context_mapper_keeps_regular_closure_hours_for_card_display() -> None:
    context = _context(
        schedule={
            "availability": "scheduled",
            "rules": [],
            "time_ranges": [
                {
                    "open_time": "09:00",
                    "close_time": "18:00",
                    "crosses_midnight": False,
                }
            ],
            "closure_rules": [{"weekdays": ["monday"]}],
        }
    )

    candidate = map_context_to_scoring_candidates(
        context,
        visit_at=datetime(2026, 7, 27, 12, 0),
    )[0]

    assert candidate.operating_hours is not None
    # 폐점 여부는 Scoring이 remaining_minutes=None으로 판별한다. 여기서는 카드가
    # 실제 시간대를 보여 줄 수 있도록 00:00~00:00 표식으로 덮어쓰지 않는다.
    assert candidate.operating_hours.open_time.isoformat() == "09:00:00"
    assert candidate.operating_hours.close_time.isoformat() == "18:00:00"


def test_context_mapper_keeps_closed_hours_for_card_display() -> None:
    context = _context(
        schedule={
            "availability": "scheduled",
            "rules": [],
            "time_ranges": [
                {
                    "open_time": "11:00",
                    "close_time": "21:00",
                    "crosses_midnight": False,
                }
            ],
            "closure_rules": [],
        }
    )

    candidate = map_context_to_scoring_candidates(
        context,
        visit_at=datetime(2026, 7, 24, 22, 0),
    )[0]

    assert candidate.operating_hours is not None
    assert candidate.operating_hours.open_time.isoformat() == "11:00:00"
    assert candidate.operating_hours.close_time.isoformat() == "21:00:00"


def test_context_mapper_keeps_unknown_hours_unverified() -> None:
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None, category="unknown"),
        visit_at=datetime(2026, 7, 24, 12, 0),
    )[0]

    assert candidate.operating_hours is None
    assert candidate.environment_type == "unknown"


def test_environment_type_uses_middle_category_when_lcls_systm3_available() -> None:
    """대분류(category=shopping)만으로는 옛 매핑에 없어 unknown이었지만, 소분류
    (SH040100=면세점, 중분류 SH04)로 조회하면 indoor로 정확히 판정된다."""
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None, category="shopping", lcls_systm3="SH040100"),
        visit_at=datetime(2026, 7, 24, 12, 0),
    )[0]

    assert candidate.environment_type == "indoor"


def test_environment_type_prefers_middle_category_over_coarse_category() -> None:
    """category=attraction은 옛 매핑에서 outdoor였지만, 소분류가 종교성지(HS03,
    unknown 판정)를 가리키면 대분류로 새지 않고 unknown을 반환해야 한다."""
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None, category="attraction", lcls_systm3="HS030100"),
        visit_at=datetime(2026, 7, 24, 12, 0),
    )[0]

    assert candidate.environment_type == "unknown"


def test_environment_type_falls_back_to_category_when_lcls_systm3_missing() -> None:
    """lcls_systm3가 없으면(과거 fixture 등) 대분류 기준 최소 매핑으로 폴백한다."""
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None, category="restaurant", lcls_systm3=None),
        visit_at=datetime(2026, 7, 24, 12, 0),
    )[0]

    assert candidate.environment_type == "indoor"


def test_environment_type_falls_back_when_lcls_systm3_not_in_registry() -> None:
    """Registry에 없는(신규/오탈자) 소분류 코드는 대분류 기준으로 폴백한다."""
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None, category="restaurant", lcls_systm3="ZZ999999"),
        visit_at=datetime(2026, 7, 24, 12, 0),
    )[0]

    assert candidate.environment_type == "indoor"


def test_context_mapper_returns_empty_without_usable_location_or_places() -> None:
    context = RecommendationContext(
        location=ContextValue(status="no_data", data=None),
        places=ContextValue(status="no_data", data=[]),
    )

    assert (
        map_context_to_scoring_candidates(
            context,
            visit_at=datetime(2026, 7, 24, 12, 0),
        )
        == ()
    )


def test_무장애_판정을_후보로_옮긴다() -> None:
    """C가 올린 판정이 D 후보까지 와야 안내를 만들 수 있다.

    중간에서 떨어지면 오류는 나지 않고 안내만 사라진다 — 부분 가능인 장소가
    온전히 갈 수 있는 곳처럼 추천된다.
    """
    context = _context(schedule=None)
    context.places.data[0].accessibility_verdicts = {"wheelchair_access": "partial"}

    candidate = map_context_to_scoring_candidates(
        context, visit_at=datetime(2026, 7, 24, 12, 0)
    )[0]

    assert candidate.accessibility_verdicts == {"wheelchair_access": "partial"}


def test_무장애_조건이_없으면_판정도_비어_있다() -> None:
    """빈 dict가 아니라 None이다. "안 물어봤다"와 "물었는데 값이 없다"는 다르다."""
    candidate = map_context_to_scoring_candidates(
        _context(schedule=None), visit_at=datetime(2026, 7, 24, 12, 0)
    )[0]

    assert candidate.accessibility_verdicts is None

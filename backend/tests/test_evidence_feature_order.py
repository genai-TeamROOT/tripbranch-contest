"""Feature 순서 결정이 새 축을 빠뜨리지 않는지 검사한다.

예전에는 (날씨|환경) x (혼잡도 유무) 조합을 상수 4개로 열거했다. Feature가
하나 늘 때마다 조합이 배로 늘고, 새 키를 목록에 넣지 않으면 **점수에는
반영되는데 응답 feature_scores에서는 사라진다** — 2026-08-19에 taste가 실제로
그렇게 빠졌다(총점은 취향이 반영된 값인데 근거에는 taste가 없었다).
"""

from __future__ import annotations

import pytest

from app.domain.evidence import resolve_feature_order
from app.domain.scoring import (
    CONCENTRATION_WEIGHTS,
    DEFAULT_WEIGHTS,
    TASTE_WEIGHTS,
)


@pytest.mark.parametrize(
    "weights", [DEFAULT_WEIGHTS, CONCENTRATION_WEIGHTS, TASTE_WEIGHTS]
)
def test_every_scored_feature_appears_in_the_order(weights) -> None:
    """가중치 세트에 있는 축이 근거에서 빠지면 왜 그 순위인지 설명할 수 없다."""
    scores = dict.fromkeys(weights, 1.0)

    assert set(resolve_feature_order(scores)) == set(weights)


def test_unknown_feature_is_kept_at_the_end() -> None:
    """앞으로 늘 축도 조용히 사라지지 않아야 한다 — 이 규칙이 taste 누락의 재발을 막는다."""
    order = resolve_feature_order(
        {"weather": 1.0, "distance": 1.0, "future_feature": 1.0}
    )

    assert order == ("weather", "distance", "future_feature")


def test_taste_and_concentration_can_coexist() -> None:
    """2차 Scoring이 취향 위에 얹히는 조합도 순서가 있어야 한다."""
    order = resolve_feature_order(
        {
            "weather": 1.0,
            "remaining_operating_time": 1.0,
            "distance": 1.0,
            "taste": 1.0,
            "concentration": 1.0,
        }
    )

    assert order == (
        "weather",
        "remaining_operating_time",
        "distance",
        "taste",
        "concentration",
    )


def test_taste_concentration_co_visited_can_coexist() -> None:
    """D-092: co_visited가 마지막 순서로 taste/concentration과 함께 나타난다."""
    order = resolve_feature_order(
        {
            "weather": 1.0,
            "remaining_operating_time": 1.0,
            "distance": 1.0,
            "taste": 1.0,
            "concentration": 1.0,
            "co_visited": 1.0,
        }
    )

    assert order == (
        "weather",
        "remaining_operating_time",
        "distance",
        "taste",
        "concentration",
        "co_visited",
    )


def test_environment_replaces_weather_in_place() -> None:
    """날씨와 환경은 같은 자리를 나눠 쓴다 — 둘이 동시에 오지 않는다."""
    order = resolve_feature_order(
        {"environment": 1.0, "remaining_operating_time": 1.0, "distance": 1.0}
    )

    assert order[0] == "environment"
    assert "weather" not in order

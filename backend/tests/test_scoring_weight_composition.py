"""가중치 조립(build_weights) 회귀 검증.

조합별 가중치 상수를 열거하던 방식을 "켜진 선택 Feature로 조립"으로 바꾼 뒤,
**기존 세 세트가 숫자 하나까지 그대로인지**를 고정한다. 여기가 깨지면 과거
버전의 점수를 재현할 수 없다.

함께 검증하는 것: 2차 Scoring이 1차에서 켜졌던 taste를 계속 반영하는지
(2026-08-20 이전에는 CONCENTRATION_WEIGHTS에 taste 키가 없어 조용히 빠졌다).
"""

from __future__ import annotations

import pytest

from app.domain.scoring import (
    CONCENTRATION_WEIGHTS,
    DEFAULT_WEIGHTS,
    OPTIONAL_FEATURES,
    TASTE_WEIGHTS,
    build_weights,
    weights_for_feature_scores,
)

# 조립 방식으로 바꾸기 전에 코드에 하드코딩돼 있던 값 그대로. 상수를 import해서
# 비교하면 "자기 자신과 같다"가 되므로 여기 숫자를 직접 적는다.
_LEGACY_DEFAULT = {"weather": 0.4, "remaining_operating_time": 0.4, "distance": 0.2}
_LEGACY_CONCENTRATION = {
    "weather": 0.35,
    "remaining_operating_time": 0.35,
    "distance": 0.15,
    "concentration": 0.15,
}
_LEGACY_TASTE = {
    "weather": 0.35,
    "remaining_operating_time": 0.35,
    "distance": 0.15,
    "taste": 0.15,
}


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        ((), _LEGACY_DEFAULT),
        (("concentration",), _LEGACY_CONCENTRATION),
        (("taste",), _LEGACY_TASTE),
    ],
)
def test_build_weights_reproduces_legacy_sets(
    active: tuple[str, ...], expected: dict[str, float]
) -> None:
    assert build_weights(active) == expected


def test_exported_constants_match_legacy_sets() -> None:
    """import해서 쓰는 쪽(테스트 픽스처 포함)이 보는 값도 그대로여야 한다."""
    assert dict(DEFAULT_WEIGHTS) == _LEGACY_DEFAULT
    assert dict(CONCENTRATION_WEIGHTS) == _LEGACY_CONCENTRATION
    assert dict(TASTE_WEIGHTS) == _LEGACY_TASTE


def test_taste_and_concentration_together_sums_to_one() -> None:
    """조립 이전에는 존재하지 않던 조합. 기본 3축이 0.05씩 더 양보한다."""
    weights = build_weights(("taste", "concentration"))
    assert weights == {
        "weather": 0.3,
        "remaining_operating_time": 0.3,
        "distance": 0.1,
        "taste": 0.15,
        "concentration": 0.15,
    }


def test_taste_concentration_co_visited_together_fills_max_optional_slots() -> None:
    """D-092: co_visited가 OPTIONAL_FEATURES에 추가되며 정확히 3개(최대치)를
    동시에 채우는 조합이 처음 생겼다 — distance가 0.05까지 내려가는 경계값이다.
    """
    weights = build_weights(("taste", "concentration", "co_visited"))
    assert weights == {
        "weather": 0.25,
        "remaining_operating_time": 0.25,
        "distance": 0.05,
        "taste": 0.15,
        "concentration": 0.15,
        "co_visited": 0.15,
    }
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("count", range(len(OPTIONAL_FEATURES) + 1))
def test_every_combination_sums_to_one(count: int) -> None:
    weights = build_weights(OPTIONAL_FEATURES[:count])
    assert sum(weights.values()) == pytest.approx(1.0)
    assert all(weight > 0 for weight in weights.values())


def test_unknown_optional_feature_raises() -> None:
    """오타를 조용히 무시하면 그 Feature가 점수에서 빠진 채 정상처럼 돈다."""
    with pytest.raises(ValueError, match="알 수 없는 선택 Feature"):
        build_weights(("tast",))


def test_optional_feature_order_does_not_change_weights() -> None:
    assert build_weights(("concentration", "taste")) == build_weights(("taste", "concentration"))


# --- 2차 Scoring이 보는 가중치 ------------------------------------------------


def test_second_pass_keeps_taste_from_first_pass() -> None:
    """1차에서 taste로 후보를 골랐으면 2차 순위에도 taste가 남아야 한다."""
    feature_scores = {
        "weather": 0.7,
        "remaining_operating_time": 0.5,
        "distance": 0.9,
        "taste": 0.59,
        "concentration": 0.4,
    }
    weights = weights_for_feature_scores(feature_scores)
    assert weights["taste"] == 0.15
    assert sum(weights.values()) == pytest.approx(1.0)


def test_second_pass_without_taste_matches_legacy_concentration_set() -> None:
    feature_scores = {
        "weather": 0.7,
        "remaining_operating_time": 0.5,
        "distance": 0.9,
        "concentration": 0.4,
    }
    assert weights_for_feature_scores(feature_scores) == _LEGACY_CONCENTRATION


def test_second_pass_renames_weather_slot_to_environment() -> None:
    """1차가 요청 환경으로 채점했으면 2차도 같은 키를 써야 합산에서 안 빠진다."""
    feature_scores = {
        "environment": 0.7,
        "remaining_operating_time": 0.5,
        "distance": 0.9,
        "concentration": 0.4,
    }
    weights = weights_for_feature_scores(feature_scores)
    assert "weather" not in weights
    assert weights["environment"] == 0.35


def test_second_pass_keeps_base_axis_even_when_score_missing() -> None:
    """날씨 조회 실패(None)는 결측이지 Feature 부재가 아니다.

    키 유무로 축을 빼면 가중치 합이 1.0에 못 미쳐 2차를 탄 요청만 점수가
    통째로 낮아진다.
    """
    feature_scores: dict[str, float | None] = {
        "weather": None,
        "remaining_operating_time": None,
        "distance": 0.9,
        "concentration": 0.4,
    }
    weights = weights_for_feature_scores(feature_scores)
    assert set(weights) == set(_LEGACY_CONCENTRATION)
    assert sum(weights.values()) == pytest.approx(1.0)

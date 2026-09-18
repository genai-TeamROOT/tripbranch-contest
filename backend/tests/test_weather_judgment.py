"""weather_judgment.judge_weather_condition_from_facts/_from_stated() 판정 규칙 고정.

판정 근거: package_D/feature-weather-judgment-role-split_설계.md §4 (D-051).
의도(AVOID/ENJOY/IGNORE/NO_MENTION/None) × 원인(rain/snow/heat/cold/맑음/흐림/결측)
조합을 전수 검증한다. 핵심 회귀 케이스는 test_enjoy_precipitation_bad_flips_to_good다
— D-051 문제 1(ENJOY 반전)의 직접적인 재현·검증이다.

두 판정 함수 모두 `(WeatherCondition, WeatherReason)` 튜플을 반환한다 — reason은
explanation.py의 근거 문장(비/눈/폭염/한파 구분)에 쓰인다(2026-08-05 검수에서
weather_condition만으로는 "폭염인데 비 예보라고 말하는" 불일치가 발견되어 추가).
"""

from __future__ import annotations

import pytest

from app.domain.models import WeatherCondition
from app.domain.weather_judgment import (
    Precipitation,
    Sky,
    judge_weather_condition_from_facts,
    judge_weather_condition_from_stated,
)
from app.schemas import StatedWeather, WeatherIntent

_NON_FLIPPING_INTENTS = (WeatherIntent.AVOID, WeatherIntent.NO_MENTION, None)


# --- judge_weather_condition_from_facts() -----------------------------------


@pytest.mark.parametrize("weather_intent", _NON_FLIPPING_INTENTS)
def test_facts_rain_is_bad_regardless_of_intent_except_enjoy(
    weather_intent: WeatherIntent | None,
) -> None:
    condition, reason = judge_weather_condition_from_facts("rain", "overcast", 15.0, weather_intent)
    assert condition is WeatherCondition.BAD
    assert reason == "rain"


@pytest.mark.parametrize(
    ("precipitation", "expected_reason"),
    [("rain", "rain"), ("sleet", "rain"), ("shower", "rain"), ("snow", "snow")],
)
def test_enjoy_precipitation_bad_flips_to_good(
    precipitation: Precipitation, expected_reason: str
) -> None:
    """D-051 문제 1의 회귀 테스트: "비 오는 날 산책하고 싶어"(ENJOY)가
    outdoor를 우대해야 한다 — BAD가 아니라 GOOD으로 판정돼야 한다.

    reason은 GOOD으로 뒤집힌 뒤에도 원래 원인을 그대로 유지한다(근거 문장이
    "왜 원래 나빴는지"를 말해야 하므로) — snow만 rain과 다른 reason으로 남는다.
    """
    condition, reason = judge_weather_condition_from_facts(
        precipitation, "overcast", 15.0, WeatherIntent.ENJOY
    )
    assert condition is WeatherCondition.GOOD
    assert reason == expected_reason


def test_facts_precipitation_none_is_not_bad() -> None:
    condition, reason = judge_weather_condition_from_facts(
        "none", "clear", 15.0, WeatherIntent.AVOID
    )
    assert condition is WeatherCondition.GOOD
    assert reason is None


@pytest.mark.parametrize("weather_intent", [*_NON_FLIPPING_INTENTS, WeatherIntent.ENJOY])
def test_facts_extreme_heat_is_bad_and_enjoy_does_not_flip(
    weather_intent: WeatherIntent | None,
) -> None:
    """기온이 원인인 BAD는 ENJOY여도 뒤집지 않는다(1차 범위 제외)."""
    condition, reason = judge_weather_condition_from_facts("none", "clear", 35.0, weather_intent)
    assert condition is WeatherCondition.BAD
    assert reason == "heat"


@pytest.mark.parametrize("weather_intent", [*_NON_FLIPPING_INTENTS, WeatherIntent.ENJOY])
def test_facts_extreme_cold_is_bad_and_enjoy_does_not_flip(
    weather_intent: WeatherIntent | None,
) -> None:
    condition, reason = judge_weather_condition_from_facts("none", "clear", -15.0, weather_intent)
    assert condition is WeatherCondition.BAD
    assert reason == "cold"


@pytest.mark.parametrize("temperature_celsius", [28.0, 30.0, 33.0 - 0.01, -12.0 + 0.01, 0.0])
def test_facts_below_advisory_temperature_is_not_bad_or_neutral_from_temperature(
    temperature_celsius: float,
) -> None:
    """주의보 미만 기온은 그 자체로 BAD도 NEUTRAL도 만들지 않는다 — 하늘 상태로
    넘어간다(맑으면 GOOD)."""
    condition, reason = judge_weather_condition_from_facts(
        "none", "clear", temperature_celsius, WeatherIntent.AVOID
    )
    assert condition is WeatherCondition.GOOD
    assert reason is None


@pytest.mark.parametrize("temperature_celsius", [28.0, 30.0, -5.0, 0.0])
def test_facts_below_advisory_temperature_follows_sky(temperature_celsius: float) -> None:
    """주의보 기준에 못 미치면 기온은 판정에서 빠지고 하늘 상태로 결정된다."""
    clear_condition, clear_reason = judge_weather_condition_from_facts(
        "none", "clear", temperature_celsius, WeatherIntent.AVOID
    )
    assert clear_condition is WeatherCondition.GOOD
    assert clear_reason is None

    overcast_condition, overcast_reason = judge_weather_condition_from_facts(
        "none", "overcast", temperature_celsius, WeatherIntent.AVOID
    )
    assert overcast_condition is WeatherCondition.NEUTRAL
    assert overcast_reason is None


@pytest.mark.parametrize("temperature_celsius", [33.0, 34.0, 35.0 - 0.01])
def test_facts_heat_advisory_band_is_neutral(temperature_celsius: float) -> None:
    """폭염주의보(33°C) 이상 ~ 폭염경보(35°C) 미만은 NEUTRAL이다 — 맑아도(clear)
    기온이 하늘보다 우선한다."""
    condition, reason = judge_weather_condition_from_facts(
        "none", "clear", temperature_celsius, WeatherIntent.AVOID
    )
    assert condition is WeatherCondition.NEUTRAL
    assert reason == "heat"


@pytest.mark.parametrize("temperature_celsius", [-12.0, -13.5, -15.0 + 0.01])
def test_facts_cold_advisory_band_is_neutral(temperature_celsius: float) -> None:
    """한파주의보(-12°C) 이하 ~ 한파경보(-15°C) 초과는 NEUTRAL이다."""
    condition, reason = judge_weather_condition_from_facts(
        "none", "clear", temperature_celsius, WeatherIntent.AVOID
    )
    assert condition is WeatherCondition.NEUTRAL
    assert reason == "cold"


def test_facts_heat_warning_boundary_is_bad() -> None:
    """폭염경보(35°C) 이상만 BAD다 — 34.99°C는 아직 NEUTRAL(주의보 수준)."""
    below, below_reason = judge_weather_condition_from_facts(
        "none", "clear", 35.0 - 0.01, WeatherIntent.AVOID
    )
    assert below is WeatherCondition.NEUTRAL
    assert below_reason == "heat"

    at, at_reason = judge_weather_condition_from_facts("none", "clear", 35.0, WeatherIntent.AVOID)
    assert at is WeatherCondition.BAD
    assert at_reason == "heat"


def test_facts_cold_warning_boundary_is_bad() -> None:
    """한파경보(-15°C) 이하만 BAD다 — -14.99°C는 아직 NEUTRAL(주의보 수준)."""
    above, above_reason = judge_weather_condition_from_facts(
        "none", "clear", -15.0 + 0.01, WeatherIntent.AVOID
    )
    assert above is WeatherCondition.NEUTRAL
    assert above_reason == "cold"

    at, at_reason = judge_weather_condition_from_facts("none", "clear", -15.0, WeatherIntent.AVOID)
    assert at is WeatherCondition.BAD
    assert at_reason == "cold"


def test_facts_clear_sky_comfortable_temperature_is_good() -> None:
    condition, reason = judge_weather_condition_from_facts(
        "none", "clear", 15.0, WeatherIntent.AVOID
    )
    assert condition is WeatherCondition.GOOD
    assert reason is None


@pytest.mark.parametrize("sky", ["cloudy", "overcast"])
def test_facts_cloudy_or_overcast_comfortable_temperature_is_neutral(sky: Sky) -> None:
    condition, reason = judge_weather_condition_from_facts("none", sky, 15.0, WeatherIntent.AVOID)
    assert condition is WeatherCondition.NEUTRAL
    assert reason is None


def test_facts_all_missing_defaults_to_neutral() -> None:
    condition, reason = judge_weather_condition_from_facts(None, None, None, WeatherIntent.AVOID)
    assert condition is WeatherCondition.NEUTRAL
    assert reason is None


def test_facts_ignore_intent_does_not_change_baseline() -> None:
    """IGNORE는 실제로는 이 함수까지 안 온다 — C가 IGNORE면 애초에 날씨를 조회하지
    않아(tool_rules.py) resolve_weather_condition()이 호출 자체를 안 한다. 그래도
    이 함수 자체는 방어적으로 기본 판정을 그대로 반환해야 한다."""
    condition, reason = judge_weather_condition_from_facts(
        "rain", "overcast", 15.0, WeatherIntent.IGNORE
    )
    assert condition is WeatherCondition.BAD
    assert reason == "rain"


# --- judge_weather_condition_from_stated() -----------------------------------


@pytest.mark.parametrize(
    ("stated_weather", "expected_reason"),
    [(StatedWeather.RAIN, "rain"), (StatedWeather.SNOW, "snow")],
)
@pytest.mark.parametrize("weather_intent", _NON_FLIPPING_INTENTS)
def test_stated_precipitation_is_bad_when_not_enjoy(
    stated_weather: StatedWeather, expected_reason: str, weather_intent: WeatherIntent | None
) -> None:
    condition, reason = judge_weather_condition_from_stated(stated_weather, weather_intent)
    assert condition is WeatherCondition.BAD
    assert reason == expected_reason


@pytest.mark.parametrize(
    ("stated_weather", "expected_reason"),
    [(StatedWeather.RAIN, "rain"), (StatedWeather.SNOW, "snow")],
)
def test_stated_precipitation_flips_to_good_when_enjoy(
    stated_weather: StatedWeather, expected_reason: str
) -> None:
    condition, reason = judge_weather_condition_from_stated(stated_weather, WeatherIntent.ENJOY)
    assert condition is WeatherCondition.GOOD
    assert reason == expected_reason


@pytest.mark.parametrize(
    ("stated_weather", "expected_reason"),
    [(StatedWeather.HOT, "heat"), (StatedWeather.COLD, "cold")],
)
@pytest.mark.parametrize("weather_intent", [*_NON_FLIPPING_INTENTS, WeatherIntent.ENJOY])
def test_stated_temperature_is_bad_and_enjoy_does_not_flip(
    stated_weather: StatedWeather, expected_reason: str, weather_intent: WeatherIntent | None
) -> None:
    condition, reason = judge_weather_condition_from_stated(stated_weather, weather_intent)
    assert condition is WeatherCondition.BAD
    assert reason == expected_reason


@pytest.mark.parametrize("weather_intent", [*_NON_FLIPPING_INTENTS, WeatherIntent.ENJOY])
def test_stated_good_is_always_good(weather_intent: WeatherIntent | None) -> None:
    condition, reason = judge_weather_condition_from_stated(StatedWeather.GOOD, weather_intent)
    assert condition is WeatherCondition.GOOD
    assert reason is None

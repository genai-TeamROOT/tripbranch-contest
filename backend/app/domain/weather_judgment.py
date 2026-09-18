"""날씨 사실(precipitation/sky/temperature)과 사용자 의도로 WeatherCondition을 판정한다.

D-051: C는 기상청 코드를 도메인 용어로 번역한 사실만 전달하고(`WeatherForecast`의
precipitation/sky/temperature_celsius), 판정("이 날씨가 좋은가")은 D가 맡는다.
판정에는 weather_intent(AVOID/ENJOY)가 필요한데 그 값을 가진 쪽이 D이기 때문이다.

핵심 원칙: WeatherCondition(GOOD/NEUTRAL/BAD)은 "객관적으로 좋은 날씨"가 아니라
"이 사용자의 목적에 맞는 날씨"다. 그래서 같은 강수라도 AVOID면 BAD, ENJOY면
GOOD으로 판정될 수 있다. `_WEATHER_FIT_TABLE`(scoring.py)의 점수는 WeatherCondition
값만 소비하므로 그대로 재사용된다. 다만 explanation.py의 근거 문장은 WeatherCondition
만으로는 "왜"(비/눈/폭염/한파 중 무엇 때문인지)를 알 수 없어서 `WeatherReason`도 함께
반환한다 — GOOD/BAD 값 하나로 뭉뚱그리면 "폭염인데 '비 예보'라고 말하는" 식의
근거-사실 불일치가 생긴다(2026-08-05 검수에서 발견).
"""

from __future__ import annotations

from typing import Literal

from app.domain.models import WeatherCondition
from app.schemas import StatedWeather, WeatherIntent

# 기상청 특보 기준(2026-08-05 확인)을 그대로 차용했다 — 주의보/경보 두 단계 모두
# 공식 경계다. 공식 기준은 체감온도·2일 이상 지속을 요구하지만, 여기서는 단일
# 예보 시점의 기온만 본다 — 단순화라는 걸 인지하고 있어야 한다.
#   폭염주의보 33°C 이상 / 폭염경보 35°C 이상(중대경보 38°C+는 BAD에 흡수 —
#   WeatherCondition에 4번째 값이 없다)
#   한파주의보 -12°C 이하 / 한파경보 -15°C 이하(중대경보 단계 없음)
# 주의보~경보 사이는 NEUTRAL이다 — 이전엔 이 구간에 완충값을 안 두었는데
# (28°C/0°C가 근거 없는 임의값이라 뺐었다), 이번엔 두 경계 모두 공식 등급이라
# "근거 없는 완충"이 아니다.
_HEAT_ADVISORY_THRESHOLD_CELSIUS = 33.0
_HEAT_WARNING_THRESHOLD_CELSIUS = 35.0
_COLD_ADVISORY_THRESHOLD_CELSIUS = -12.0
_COLD_WARNING_THRESHOLD_CELSIUS = -15.0

# C가 기상청 PTY 코드를 옮긴 강수형태(WeatherForecast.precipitation)와 동일한 값.
# C의 스키마 클래스는 import하지 않는다 — 값 타입만 공유하고 코드 의존은 없다.
Precipitation = Literal["none", "rain", "snow", "sleet", "shower"]
Sky = Literal["clear", "cloudy", "overcast"]

# 판정이 왜 그렇게 나왔는지(원인)를 함께 들고 다닌다. 두 용도로 쓰인다.
#   1) ENJOY 재해석: 강수(rain/snow)가 원인인 BAD만 뒤집고, 기온(heat/cold)이
#      원인인 BAD는 그대로 둔다.
#   2) 근거 문장(explanation.py): "비 예보"/"눈 예보"/"폭염 예보"/"한파 예보"를
#      구분해서 말해야 사실과 어긋나지 않는다. sleet/shower는 rain으로 뭉뚱그린다 —
#      snow만 한국어로 명확히 다른 낱말(비/눈)이라 최소한으로 구분한다.
WeatherReason = Literal["rain", "snow", "heat", "cold"] | None

_STATED_WEATHER_BASELINE: dict[StatedWeather, tuple[WeatherCondition, WeatherReason]] = {
    StatedWeather.RAIN: (WeatherCondition.BAD, "rain"),
    StatedWeather.SNOW: (WeatherCondition.BAD, "snow"),
    StatedWeather.HOT: (WeatherCondition.BAD, "heat"),
    StatedWeather.COLD: (WeatherCondition.BAD, "cold"),
    StatedWeather.GOOD: (WeatherCondition.GOOD, None),
}


def _classify_from_facts(
    precipitation: Precipitation | None,
    sky: Sky | None,
    temperature_celsius: float | None,
) -> tuple[WeatherCondition, WeatherReason]:
    """의도와 무관한 사실 기반 판정 + 원인 태깅.

    강수 > 기온(폭염/한파) > 하늘 순으로 확인한다 — 비가 오면서 기온도
    극단적이어도 강수 쪽이 체감에 더 크게 영향을 준다고 보고 강수를 우선한다.
    기온은 경보 이상이면 BAD, 주의보 이상~경보 미만이면 NEUTRAL, 주의보 미만이면
    그 자체로는 판정하지 않고 하늘 상태로 넘어간다.
    """
    if precipitation is not None and precipitation != "none":
        return WeatherCondition.BAD, ("snow" if precipitation == "snow" else "rain")

    if temperature_celsius is not None:
        if temperature_celsius >= _HEAT_WARNING_THRESHOLD_CELSIUS:
            return WeatherCondition.BAD, "heat"
        if temperature_celsius >= _HEAT_ADVISORY_THRESHOLD_CELSIUS:
            return WeatherCondition.NEUTRAL, "heat"
        if temperature_celsius <= _COLD_WARNING_THRESHOLD_CELSIUS:
            return WeatherCondition.BAD, "cold"
        if temperature_celsius <= _COLD_ADVISORY_THRESHOLD_CELSIUS:
            return WeatherCondition.NEUTRAL, "cold"

    if sky == "clear":
        return WeatherCondition.GOOD, None
    if sky in ("cloudy", "overcast"):
        return WeatherCondition.NEUTRAL, None

    # precipitation/sky/temperature가 전부 결측이면 판단 근거가 없다 — 안전한
    # 중간값으로 취급한다(1차 Scoring의 _WEATHER_FIT_DEFAULT와 같은 태도).
    return WeatherCondition.NEUTRAL, None


def _classify_from_stated(stated: StatedWeather) -> tuple[WeatherCondition, WeatherReason]:
    return _STATED_WEATHER_BASELINE[stated]


def _apply_intent(
    condition: WeatherCondition,
    reason: WeatherReason,
    weather_intent: WeatherIntent | None,
) -> WeatherCondition:
    """AVOID/NO_MENTION/None은 기본 판정을 그대로 쓰고, ENJOY는 강수(비/눈)가
    원인인 BAD만 GOOD으로 뒤집는다. 기온(폭염/한파)이 원인인 BAD는 1차 범위에서
    뒤집지 않는다 — "더워서 즐기고 싶다"류 케이스는 실제 사례가 나오면 확장 검토한다.
    """
    if (
        weather_intent is WeatherIntent.ENJOY
        and reason in ("rain", "snow")
        and condition is WeatherCondition.BAD
    ):
        return WeatherCondition.GOOD
    return condition


def judge_weather_condition_from_facts(
    precipitation: Precipitation | None,
    sky: Sky | None,
    temperature_celsius: float | None,
    weather_intent: WeatherIntent | None,
) -> tuple[WeatherCondition, WeatherReason]:
    """C가 조회한 날씨 사실(`WeatherForecast.precipitation`/`sky`/`temperature_celsius`)로
    WeatherCondition을 판정한다. 반환하는 `WeatherReason`은 의도 재해석 후에도
    원래 원인(비/눈/폭염/한파)을 그대로 유지한다 — ENJOY로 GOOD이 됐어도 근거
    문장은 "왜 원래 나빴는지"를 말해야 하기 때문이다(예: "비 예보에 적합한 야외").

    weather_intent가 NO_MENTION/None일 때뿐 아니라, AVOID/ENJOY인데 발화에서 5단계
    값을 못 뽑아 C가 대신 조회한 경우(PR #102 폴백)에도 쓰인다 — 그때도 사실이
    존재하면 사실을 우선한다(`resolve_weather_condition()` 참고). AVOID/ENJOY가
    발화 값을 직접 말한 경우에만 `judge_weather_condition_from_stated()`를 대신
    쓴다(호출부 책임). weather_intent 값 자체는 두 진입점 모두 `_apply_intent()`로
    똑같이 재해석되므로, 어느 경로로 오든 ENJOY 반전 등은 동일하게 적용된다.
    """
    condition, reason = _classify_from_facts(precipitation, sky, temperature_celsius)
    return _apply_intent(condition, reason, weather_intent), reason


def judge_weather_condition_from_stated(
    stated_weather: StatedWeather,
    weather_intent: WeatherIntent | None,
) -> tuple[WeatherCondition, WeatherReason]:
    """사용자가 발화에서 직접 말한 날씨(`StatedWeather`)로 WeatherCondition을
    판정한다. weather_intent가 AVOID/ENJOY일 때 쓰인다. 반환하는 `WeatherReason`은
    `judge_weather_condition_from_facts()`와 같은 용도다.
    """
    condition, reason = _classify_from_stated(stated_weather)
    return _apply_intent(condition, reason, weather_intent), reason


__all__ = [
    "Precipitation",
    "Sky",
    "WeatherReason",
    "judge_weather_condition_from_facts",
    "judge_weather_condition_from_stated",
]

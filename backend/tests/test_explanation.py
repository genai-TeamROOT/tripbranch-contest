"""Explainability Layer v1(D-06)의 Rule 기반 문장 생성 검증.

D-02의 `scoring_fixture_v1.py` 시나리오를 그대로 재사용해, Feature 점수별로
어떤 문장이 몇 개·어떤 순서로 나오는지 손으로 계산한 값과 대조한다.
D-06 구체화 요청(`package_D/[D-06]explainability_detail.txt`) 이후에는 문장이
고정 텍스트가 아니라 거리(m/km)·남은 운영시간(시/분)·날씨-환경 조합에 따라
달라지므로, 단위 경계값과 조합 매트릭스도 함께 검증한다.
"""

from datetime import time

import pytest
from fixtures.scoring_fixture_v1 import (
    _CAFE_CLOSING_SOON,
    _GALLERY_UNKNOWN_HOURS,
    _MUSEUM_OPEN,
    _RESTAURANT_FAR,
    NOW,
)

from app.concentration_policy import ConcentrationLevel
from app.domain import explanation
from app.domain.evidence import (
    CONCENTRATION_FEATURE_ORDER,
    FeatureContribution,
    RecommendationEvidence,
    build_evidence,
)
from app.domain.explanation import build_explanations
from app.domain.models import OperatingHours, ScoringCandidate, WeatherCondition
from app.domain.scoring import (
    CONCENTRATION_WEIGHTS,
    RankedCandidate,
    build_weights,
    prepare_candidates,
    redistribute_weights,
    score_candidates,
    score_prepared_candidates,
)
from app.domain.travel_route import RouteSource, RouteStatus, TravelMode, TravelRoute


def _explanations_by_place_id(candidates, **kwargs) -> dict[str, tuple[str, ...]]:
    result = score_candidates(candidates, now=NOW, **kwargs)
    return {ranked.place_id: build_explanations(build_evidence(ranked)) for ranked in result.ranked}


def test_baseline_all_features_present_explanations() -> None:
    explanations = _explanations_by_place_id(
        (_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR),
        weather_condition=WeatherCondition.BAD,
        weather_reason="rain",
        max_distance_km=1.5,
    )

    # p1(박물관A): weather=1.0, remaining=1.0(둘 다 임계값 이상, 기여도 동률
    # → weather가 고정 순서상 먼저), distance=0.667(임계값 미만이라 생략).
    # remaining_minutes=240 → "4시간"(분 나머지 0이라 "0분" 생략).
    assert explanations["p1"] == (
        "비 예보에 적합한 실내 장소예요.",
        "마감까지 약 4시간 남았어요.",
    )
    # p5(맛집E): p1과 동일한 패턴. remaining_minutes=540(점수는 120분 캡으로
    # 1.0이지만, 문장에는 캡 걸리지 않은 실제 잔여시간을 그대로 표시) → "9시간".
    assert explanations["p5"] == (
        "비 예보에 적합한 실내 장소예요.",
        "마감까지 약 9시간 남았어요.",
    )
    # p2(카페B): weather=1.0(포함), remaining=0.5(임계값 미만, 생략),
    # distance=0.467(임계값 미만, 생략)
    assert explanations["p2"] == ("비 예보에 적합한 실내 장소예요.",)


def test_weather_missing_can_produce_empty_explanations() -> None:
    explanations = _explanations_by_place_id(
        (_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR),
        weather_condition=None,
        max_distance_km=1.5,
    )

    assert explanations["p1"] == ("마감까지 약 4시간 남았어요.",)
    assert explanations["p5"] == ("마감까지 약 9시간 남았어요.",)
    # p2: remaining=0.5, distance=0.467 모두 임계값 미만이고 weather는 결측
    # → 강조할 이유가 하나도 없어 빈 튜플이 될 수 있다.
    assert explanations["p2"] == ()


def test_operating_hours_unknown_orders_by_contribution() -> None:
    explanations = _explanations_by_place_id(
        (_MUSEUM_OPEN, _GALLERY_UNKNOWN_HOURS),
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
    )

    # p1: weather=0.70(기여도 0.28), remaining=1.0(기여도 0.4) → remaining이
    # 기여도가 더 커서 먼저 나온다.
    assert explanations["p1"] == (
        "마감까지 약 4시간 남았어요.",
        "맑은 날씨에 적합한 실내 장소예요.",
    )
    # p4(갤러리D, 운영시간 미확인): remaining_operating_time 결측 → 생략,
    # weather=0.70(임계값과 동일, >= 이므로 포함)만 포함.
    assert explanations["p4"] == ("맑은 날씨에 적합한 실내 장소예요.",)


def test_explanations_are_deterministic_for_identical_input() -> None:
    kwargs = dict(weather_condition=WeatherCondition.BAD, max_distance_km=1.5)
    first = _explanations_by_place_id((_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR), **kwargs)
    second = _explanations_by_place_id(
        (_MUSEUM_OPEN, _CAFE_CLOSING_SOON, _RESTAURANT_FAR), **kwargs
    )

    assert first == second


# --- 거리 단위 경계값 -------------------------------------------------------


def _single_distance_candidate(distance_km: float) -> ScoringCandidate:
    return ScoringCandidate(
        place_id="dist",
        name="거리테스트",
        category="test",
        environment_type="indoor",
        distance_km=distance_km,
        operating_hours=None,
    )


def test_distance_sentence_uses_meters_under_one_km_rounded_to_10m() -> None:
    # 0.437km → 437m를 10m 단위로 반올림해 440m로 표시한다.
    explanations = _explanations_by_place_id(
        (_single_distance_candidate(0.437),),
        weather_condition=None,
        max_distance_km=2.0,
    )

    assert explanations["dist"] == ("현재 위치에서 직선거리 약 440m예요.",)


def test_distance_sentence_uses_km_at_one_km_and_above() -> None:
    # 1.0km 경계: m 단위가 아니라 km 단위("1km")로 전환된다.
    explanations = _explanations_by_place_id(
        (_single_distance_candidate(1.0),),
        weather_condition=None,
        max_distance_km=5.0,
    )

    assert explanations["dist"] == ("현재 위치에서 직선거리 약 1km예요.",)


def test_distance_sentence_keeps_one_decimal_above_one_km() -> None:
    explanations = _explanations_by_place_id(
        (_single_distance_candidate(1.2),),
        weather_condition=None,
        max_distance_km=5.0,
    )

    assert explanations["dist"] == ("현재 위치에서 직선거리 약 1.2km예요.",)


# --- 운영시간(남은 시간) 경계값 ---------------------------------------------


def _single_remaining_time_candidate(close_hour: int, close_minute: int) -> ScoringCandidate:
    return ScoringCandidate(
        place_id="time",
        name="운영시간테스트",
        category="test",
        environment_type="indoor",
        distance_km=100.0,  # distance 점수를 0으로 눌러 remaining만 notable하게 만든다.
        operating_hours=OperatingHours(time(9, 0), time(close_hour, close_minute)),
    )


def test_remaining_time_sentence_omits_zero_minutes() -> None:
    # NOW=14:00, 마감 18:00 → 정확히 240분(4시간 0분) → "0분"은 생략한다.
    explanations = _explanations_by_place_id(
        (_single_remaining_time_candidate(18, 0),),
        weather_condition=None,
        max_distance_km=1.0,
    )

    assert explanations["time"] == ("마감까지 약 4시간 남았어요.",)


def test_remaining_time_sentence_shows_hours_and_minutes() -> None:
    # NOW=14:00, 마감 15:40 → 100분 → "1시간 40분".
    explanations = _explanations_by_place_id(
        (_single_remaining_time_candidate(15, 40),),
        weather_condition=None,
        max_distance_km=1.0,
    )

    assert explanations["time"] == ("마감까지 약 1시간 40분 남았어요.",)


def test_remaining_time_at_exact_threshold_is_included() -> None:
    # NOW=14:00, 마감 15:24 → 84분 → 점수 84/120=0.7 (임계값과 동일, >= 이므로
    # 포함). 84분은 "1시간 24분".
    explanations = _explanations_by_place_id(
        (_single_remaining_time_candidate(15, 24),),
        weather_condition=None,
        max_distance_km=1.0,
    )

    assert explanations["time"] == ("마감까지 약 1시간 24분 남았어요.",)


def test_remaining_time_just_below_threshold_is_excluded() -> None:
    # 83분 → 점수 0.6917 (< 0.7) → 문장 생략.
    explanations = _explanations_by_place_id(
        (_single_remaining_time_candidate(15, 23),),
        weather_condition=None,
        max_distance_km=1.0,
    )

    assert explanations["time"] == ()


# --- 날씨·환경 조합 매트릭스 -------------------------------------------------


def _single_weather_candidate(environment_type: str) -> ScoringCandidate:
    return ScoringCandidate(
        place_id="weather",
        name="날씨테스트",
        category="test",
        environment_type=environment_type,
        distance_km=100.0,  # distance 점수를 0으로 눌러 weather만 notable하게 만든다.
        operating_hours=None,  # remaining_operating_time은 결측 처리.
    )


def _weather_explanation(
    weather_condition: WeatherCondition,
    environment_type: str,
    weather_reason: str | None = None,
) -> tuple[str, ...]:
    explanations = _explanations_by_place_id(
        (_single_weather_candidate(environment_type),),
        weather_condition=weather_condition,
        weather_reason=weather_reason,
        max_distance_km=1.0,
    )
    return explanations["weather"]


def test_weather_environment_combinations_at_or_above_threshold() -> None:
    assert _weather_explanation(WeatherCondition.GOOD, "indoor") == (
        "맑은 날씨에 적합한 실내 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.GOOD, "outdoor") == (
        "맑은 날씨에 적합한 야외 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.GOOD, "unknown") == (
        "맑은 날씨에 적합한 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.NEUTRAL, "indoor") == (
        "무난한 날씨에 적합한 실내 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.NEUTRAL, "outdoor") == (
        "무난한 날씨에 적합한 야외 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.NEUTRAL, "unknown") == (
        "무난한 날씨에 적합한 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.BAD, "indoor", "rain") == (
        "비 예보에 적합한 실내 장소예요.",
    )


def test_weather_environment_combinations_below_threshold_are_omitted() -> None:
    # BAD+outdoor(0.30), BAD+unknown(0.60) 둘 다 임계값(0.7) 미만이라
    # 문장이 아예 생성되지 않는다.
    assert _weather_explanation(WeatherCondition.BAD, "outdoor", "rain") == ()
    assert _weather_explanation(WeatherCondition.BAD, "unknown", "rain") == ()


def test_weather_reason_picks_label_over_condition() -> None:
    """weather_reason이 있으면 weather_condition 라벨 대신 원인별 문구를 쓴다.

    2026-08-05 검수에서 발견한 문제의 회귀 테스트: weather_condition만 보면
    ENJOY로 GOOD이 된 비/눈도 "맑은 날씨"라고 말했고, 폭염/한파로 BAD가 된
    경우도 전부 "비 예보"라고 말했다 — 둘 다 사실과 어긋난다.
    """
    # ENJOY로 GOOD이 됐어도 원래 원인(비)을 그대로 말한다 — "맑은 날씨" 아님.
    assert _weather_explanation(WeatherCondition.GOOD, "outdoor", "rain") == (
        "비 예보에 적합한 야외 장소예요.",
    )
    # 눈은 비와 다른 낱말을 쓴다.
    assert _weather_explanation(WeatherCondition.BAD, "indoor", "snow") == (
        "눈 예보에 적합한 실내 장소예요.",
    )
    # 폭염/한파로 인한 BAD는 "비 예보"가 아니라 각각 다른 문구를 쓴다.
    assert _weather_explanation(WeatherCondition.BAD, "indoor", "heat") == (
        "폭염 예보에 적합한 실내 장소예요.",
    )
    assert _weather_explanation(WeatherCondition.BAD, "indoor", "cold") == (
        "한파 예보에 적합한 실내 장소예요.",
    )


# --- 혼잡도(concentration) 문장 (D-040, 2차 Scoring 전용) ----------------------
# score_candidates()는 concentration을 전혀 모르므로(1차엔 키 자체가 없음),
# 여기서는 RankedCandidate를 직접 구성해 build_evidence()에 CONCENTRATION_
# FEATURE_ORDER를 명시적으로 넘긴다 — rerank_with_concentration()이 실제로
# 하는 일과 동일한 경로다.


def _concentration_ranked_candidate(
    concentration_score_value: float | None,
    concentration_level: ConcentrationLevel | None,
) -> RankedCandidate:
    feature_scores: dict[str, float | None] = {
        "weather": None,
        "remaining_operating_time": None,
        "distance": 0.0,
        "concentration": concentration_score_value,
    }
    missing = [f for f in CONCENTRATION_WEIGHTS if feature_scores.get(f) is None]
    weights_used = redistribute_weights(CONCENTRATION_WEIGHTS, missing)
    return RankedCandidate(
        place_id="conc",
        name="혼잡도테스트",
        category="test",
        rank=1,
        score=0.0,
        feature_scores=feature_scores,
        weights_used=weights_used,
        is_unverified=False,
        warnings=(),
        distance_km=0.0,
        remaining_minutes=None,
        weather_condition=None,
        environment_type="unknown",
        concentration_level=concentration_level,
    )


def test_concentration_sentence_by_level() -> None:
    cases = [
        (ConcentrationLevel.QUIET, "지금 이 근처는 한적한 편이에요."),
        (ConcentrationLevel.NORMAL, "지금 이 근처는 보통 수준으로 붐벼요."),
        (ConcentrationLevel.SLIGHTLY_CROWDED, "지금 이 근처는 다소 혼잡한 편이에요."),
        (ConcentrationLevel.CROWDED, "지금 이 근처는 혼잡한 편이에요."),
    ]
    for level, expected_sentence in cases:
        candidate = _concentration_ranked_candidate(0.9, level)
        evidence = build_evidence(candidate, feature_order=CONCENTRATION_FEATURE_ORDER)
        assert build_explanations(evidence) == (expected_sentence,)


def test_concentration_below_threshold_is_omitted() -> None:
    candidate = _concentration_ranked_candidate(0.5, ConcentrationLevel.NORMAL)
    evidence = build_evidence(candidate, feature_order=CONCENTRATION_FEATURE_ORDER)
    assert build_explanations(evidence) == ()


def test_concentration_missing_is_omitted() -> None:
    candidate = _concentration_ranked_candidate(None, None)
    evidence = build_evidence(candidate, feature_order=CONCENTRATION_FEATURE_ORDER)
    assert build_explanations(evidence) == ()


# --- co_visited Feature(D-092, RECOMMEND 2차 Scoring 전용) 근거 문장 ---------


def _co_visited_ranked_candidate(
    co_visited_score_value: float,
    co_visited_place_names: tuple[str, ...],
) -> RankedCandidate:
    co_visited_weights = build_weights(("co_visited",))
    feature_scores: dict[str, float | None] = {
        "weather": None,
        "remaining_operating_time": None,
        "distance": 0.0,
        "co_visited": co_visited_score_value,
    }
    missing = [f for f in co_visited_weights if feature_scores.get(f) is None]
    weights_used = redistribute_weights(co_visited_weights, missing)
    return RankedCandidate(
        place_id="cov",
        name="동선테스트",
        category="test",
        rank=1,
        score=0.0,
        feature_scores=feature_scores,
        weights_used=weights_used,
        is_unverified=False,
        warnings=(),
        distance_km=0.0,
        remaining_minutes=None,
        weather_condition=None,
        environment_type="unknown",
        co_visited_place_names=co_visited_place_names,
    )


def test_co_visited_sentence_names_the_other_place() -> None:
    candidate = _co_visited_ranked_candidate(1.0, ("경복궁",))
    evidence = build_evidence(candidate)
    assert build_explanations(evidence) == ("경복궁와(과) 함께 방문객들이 자주 찾는 곳이에요.",)


def test_co_visited_sentence_lists_up_to_two_names() -> None:
    candidate = _co_visited_ranked_candidate(1.0, ("경복궁", "북촌한옥마을", "인사동"))
    evidence = build_evidence(candidate)
    assert build_explanations(evidence) == (
        "경복궁, 북촌한옥마을와(과) 함께 방문객들이 자주 찾는 곳이에요.",
    )


def test_co_visited_sentence_falls_back_without_names() -> None:
    """이름이 없는데도 notable(비정상 입력 방어) — 그래도 크래시 없이 일반 문구를 낸다."""
    candidate = _co_visited_ranked_candidate(1.0, ())
    evidence = build_evidence(candidate)
    assert build_explanations(evidence) == (
        "다른 추천 장소와 함께 방문객들이 자주 찾는 곳이에요.",
    )


def test_co_visited_below_threshold_is_omitted() -> None:
    candidate = _co_visited_ranked_candidate(0.3, ("경복궁",))
    evidence = build_evidence(candidate)
    assert build_explanations(evidence) == ()


def test_co_visited_zero_is_omitted() -> None:
    """쌍이 없는 후보(co_visited=0.0)는 결측이 아니라 낮은 점수다 — 문장 생략."""
    candidate = _co_visited_ranked_candidate(0.0, ())
    evidence = build_evidence(candidate)
    assert build_explanations(evidence) == ()


# --- 요청 환경(conditions.environment)으로 채점된 실행의 근거 문장 ------------


def test_requested_environment_sentence_does_not_mention_weather() -> None:
    """맑은 날 "실내로"를 요청한 실행에서 날씨를 근거로 말하면 사실과 어긋난다.

    이 실행에는 날씨가 점수에 들어가지 않았으므로 요청 자체를 근거로 말한다.
    """
    result = score_candidates(
        (_MUSEUM_OPEN,),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        requested_environment="indoor",
    )
    explanations = build_explanations(build_evidence(result.ranked[0]))

    assert "요청하신 실내 장소예요." in explanations
    assert not any("날씨" in sentence for sentence in explanations)


def test_mismatched_environment_is_below_threshold() -> None:
    """요청과 어긋난 환경(0.30)은 임계값 미만이라 근거 문장으로 나오지 않는다."""
    outdoor = ScoringCandidate(
        place_id="outdoor-1",
        name="야외공원",
        category="park",
        environment_type="outdoor",
        distance_km=0.5,
        operating_hours=OperatingHours(time(9, 0), time(18, 0)),
    )
    result = score_candidates(
        (outdoor,),
        now=NOW,
        weather_condition=WeatherCondition.GOOD,
        max_distance_km=1.5,
        requested_environment="indoor",
    )
    explanations = build_explanations(build_evidence(result.ranked[0]))

    assert not any("요청하신" in sentence for sentence in explanations)


# --- 실측 도보 시간 문구 (feat/walking-distance-scoring) --------------------


def _walking_explanations(
    duration_seconds: int, *, distance_km: float = 0.437, max_distance_km: float = 2.0
):
    candidate = _single_distance_candidate(distance_km)
    prepared = prepare_candidates([candidate], now=NOW)
    result = score_prepared_candidates(
        prepared.eligible_candidates,
        weather_condition=None,
        max_distance_km=max_distance_km,
        travel_routes=[
            TravelRoute(
                place_id=candidate.place_id,
                mode=TravelMode.WALKING,
                status=RouteStatus.SUCCESS,
                source=RouteSource.KAKAO_WALKING,
                distance_m=int(distance_km * 1000),
                duration_seconds=duration_seconds,
            )
        ],
    )
    return build_explanations(build_evidence(result.ranked[0]))


def test_distance_sentence_uses_measured_walking_time() -> None:
    """실측이 있으면 "직선거리"가 아니라 도보 시간으로 말한다.

    점수도 같은 값으로 계산되므로 근거와 점수가 어긋나지 않는다.
    """
    assert _walking_explanations(420) == ("현재 위치에서 걸어서 약 7분 거리예요.",)


def test_distance_sentence_formats_walking_time_over_an_hour() -> None:
    """반경 20km면 도보 예산이 약 286분이라 75분도 임계값 이상으로 남는다."""
    explanations = _walking_explanations(4500, max_distance_km=20.0)

    assert explanations == ("현재 위치에서 걸어서 약 1시간 15분 거리예요.",)


def test_distance_sentence_keeps_straight_line_wording_without_measurement() -> None:
    """실측이 없으면 기존 직선거리 문구를 그대로 쓴다."""
    explanations = _explanations_by_place_id(
        (_single_distance_candidate(0.437),),
        weather_condition=None,
        max_distance_km=2.0,
    )

    assert explanations["dist"] == ("현재 위치에서 직선거리 약 440m예요.",)


def _travel_evidence(mode: TravelMode) -> RecommendationEvidence:
    return RecommendationEvidence(
        place_id="p1",
        name="장소A",
        category="cafe",
        rank=1,
        score=0.5,
        contributions=(
            FeatureContribution(feature="distance", score=0.9, weight=0.2, contribution=0.18),
        ),
        is_unverified=False,
        warnings=(),
        distance_km=1.4,
        remaining_minutes=None,
        weather_condition=None,
        environment_type="indoor",
        travel_distance_m=1800,
        travel_duration_seconds=600,
        travel_mode=mode,
    )


def test_distance_sentence_says_by_car_for_a_driving_measurement() -> None:
    """자동차 실측은 "차로"라고 말한다 — "걸어서"로 새어 나가지 않는다."""
    sentences = build_explanations(_travel_evidence(TravelMode.DRIVING))

    assert sentences == ("현재 위치에서 차로 약 10분 거리예요.",)


def test_distance_sentence_speaks_transit() -> None:
    """대중교통 실측도 그 수단으로 말한다 (D-118).

    문구가 없던 시절에는 카드에 "대중교통 10분"이 적히고 문장은 직선거리로
    돌아가, 같은 후보를 두 숫자로 말했다.
    """
    sentences = build_explanations(_travel_evidence(TravelMode.TRANSIT))

    assert sentences == ("현재 위치에서 대중교통으로 약 10분 거리예요.",)


def test_distance_sentence_falls_back_when_the_mode_has_no_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """문구가 없는 이동수단은 실측 시간을 말하지 않고 직선거리로 답한다.

    지금은 세 수단 모두 문구가 있으므로 하나를 지워서 확인한다 — 새 수단이
    늘어도 "문구가 없으면 지어내지 않는다"는 성질은 남아야 한다.
    """
    monkeypatch.delitem(explanation._TRAVEL_MODE_PHRASES, TravelMode.TRANSIT)

    sentences = build_explanations(_travel_evidence(TravelMode.TRANSIT))

    assert sentences == ("현재 위치에서 직선거리 약 1.4km예요.",)

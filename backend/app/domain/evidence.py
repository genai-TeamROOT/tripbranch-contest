"""추천 Evidence: RankedCandidate를 설명 가능한 근거 구조로 변환한다.

역할: Scoring v1(`RankedCandidate`)의 feature_scores/weights_used를 그대로
버리지 않고, Feature별 기여도(score * weight)를 더해 "왜 이 순위인지" 확인할
수 있는 형태로 재구성한다. 자연어 문장(reason)은 만들지 않는다 — 이는
Response Generator(LLM) 영역이며 D-02 범위 밖이다.
입력: `ScoringResult`/`RankedCandidate` (모두 `backend/app/domain/scoring.py`).
출력: `RecommendationEvidence` (직렬화 가능한 순수 데이터).
설계 근거: `docs/design/recommendation-evidence-fixture.md` 참고.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.concentration_policy import ConcentrationLevel
from app.domain.models import WeatherCondition
from app.domain.scoring import RankedCandidate, ScoringResult
from app.domain.travel_route import TravelMode
from app.domain.weather_judgment import WeatherReason

# 표시 순서의 기준 축. 여기 없는 Feature는 뒤에 들어온 순서대로 붙는다 —
# 새 Feature가 목록에서 빠져도 응답에서 사라지지 않게 하기 위해서다.
_BASE_FEATURE_ORDER: tuple[str, ...] = (
    "weather",
    "environment",
    "remaining_operating_time",
    "distance",
    "taste",
    "concentration",
    "co_visited",
)

# 1차 Scoring 결과의 Feature 순서 (scoring.py DEFAULT_WEIGHTS와 동일).
_FEATURE_ORDER: tuple[str, ...] = ("weather", "remaining_operating_time", "distance")

# 요청 환경으로 채점된 실행(scoring.py::uses_environment_feature)의 순서. 날씨와
# 환경은 같은 자리를 나눠 쓰므로 Feature 개수는 그대로 3개다.
_ENVIRONMENT_FEATURE_ORDER: tuple[str, ...] = (
    "environment",
    "remaining_operating_time",
    "distance",
)

# 2차 Scoring(rerank_with_concentration(), D-040) 결과의 Feature 순서. concentration은
# 1차 결과의 feature_scores에는 키 자체가 없다(결측이 아니라 "존재하지 않음" —
# concentration-conditions.md §2.3) — 그래서 1차용 _FEATURE_ORDER에는 넣지 않고,
# 이 상수를 별도로 둔다.
CONCENTRATION_FEATURE_ORDER: tuple[str, ...] = (*_FEATURE_ORDER, "concentration")
ENVIRONMENT_CONCENTRATION_FEATURE_ORDER: tuple[str, ...] = (
    *_ENVIRONMENT_FEATURE_ORDER,
    "concentration",
)


def resolve_feature_order(feature_scores: Mapping[str, float | None]) -> tuple[str, ...]:
    """실제로 채점된 Feature 키를 보고 순서를 고른다.

    날씨/환경 중 어느 쪽으로 채점됐는지는 호출부가 다시 판단하지 않는다 —
    `feature_scores`에 들어 있는 키가 그대로 답이다.

    **조합을 상수로 두지 않는다.** 예전에는 (날씨|환경) x (혼잡도 유무)를
    상수 4개로 열거했는데, Feature가 하나 늘 때마다 조합이 배로 늘고 새 키를
    빠뜨리면 **응답에서 조용히 사라진다** — 2026-08-19에 taste가 실제로 그렇게
    빠졌다(점수에는 반영되는데 feature_scores에는 없었다). 알려진 축을 먼저
    정해진 순서로 놓고, 나머지는 들어온 순서를 그대로 이어 붙인다.
    """
    ordered = [feature for feature in _BASE_FEATURE_ORDER if feature in feature_scores]
    ordered.extend(feature for feature in feature_scores if feature not in _BASE_FEATURE_ORDER)
    return tuple(ordered)


@dataclass(frozen=True)
class FeatureContribution:
    """Feature 1개가 최종 점수에 기여한 정도."""

    feature: str
    score: float | None
    weight: float | None
    contribution: float | None  # score * weight; 둘 중 하나라도 결측이면 None


@dataclass(frozen=True)
class RecommendationEvidence:
    """추천 결과 1건의 점수 근거."""

    place_id: str
    name: str
    category: str
    rank: int
    score: float
    contributions: tuple[FeatureContribution, ...]
    is_unverified: bool
    warnings: tuple[str, ...]
    # 문장 조립(explanation.py)에 필요한 원본 값. contributions는 정렬용
    # 정규화 점수만 담으므로 별도로 보존한다.
    distance_km: float
    remaining_minutes: float | None
    weather_condition: WeatherCondition | None
    environment_type: str
    # 실측 경로로 거리 Feature를 채점했을 때만 채워진다. 직선거리로 폴백한
    # 후보는 None이라, 근거 문장이 "직선거리"와 실측을 구분해 쓸 수 있다.
    # travel_mode는 그 실측이 어떤 이동수단인지다 — 문장이 "걸어서"라고 말할 수
    # 있는지가 여기서 갈린다(explanation.py::_distance_sentence()).
    travel_distance_m: int | None = None
    travel_duration_seconds: int | None = None
    travel_mode: TravelMode | None = None
    taste_evidence_text: str | None = None
    # 태그 경로로 채점했을 때 맞은 태그의 라벨과 긍정 문서 수. 문장이 원문 인용
    # 대신 "후기 5건에서 '혼자 가기 좋은' 이야기가 나와요"로 말하는 재료다
    # (scoring.py::RankedCandidate.taste_tag_label 참고).
    taste_tag_label: str | None = None
    taste_tag_documents: int = 0
    # 거리·이동시간을 어디서부터 잰 것인지 사용자에게 부를 이름. 검색 기준점이
    # 기기 GPS면 부를 이름이 없어 None이고, 문장이 "현재 위치"로 옮긴다
    # (explanation.py::_distance_sentence()). distance_km의 기준점 자체는 바뀌지
    # 않는다 — 점수 분모(search_radius_km)와 같은 원점을 써야 하기 때문이다
    # (docs/design/recommendation-scoring.md).
    origin_name: str | None = None
    # D-040: 2차 Scoring에서만 채워진다(scoring.py::RankedCandidate.concentration_level
    # 참고) — concentration_score(direction 반영됨)만으로는 실제 붐빔 정도를 알 수
    # 없어서, 문장 조립에 원본 4단계 구간을 그대로 보존한다.
    concentration_level: ConcentrationLevel | None = None
    # weather_condition만으로는 "왜"(비/눈/폭염/한파)를 알 수 없어서 문장 조립에
    # 따로 필요하다(scoring.py::RankedCandidate.weather_reason 참고).
    weather_reason: WeatherReason = None
    # D-092: 2차 Scoring(rerank_with_co_visited())에서만 채워진다. 문장 조립이
    # "함께 방문된 이력"을 점수 대신 이름으로 말할 수 있게 원본을 그대로 이월한다
    # (scoring.py::RankedCandidate.co_visited_place_names 참고).
    co_visited_place_names: tuple[str, ...] = ()


def _build_contributions(
    candidate: RankedCandidate, feature_order: tuple[str, ...]
) -> tuple[FeatureContribution, ...]:
    contributions = []
    for feature in feature_order:
        score = candidate.feature_scores.get(feature)
        weight = candidate.weights_used.get(feature)
        contribution = score * weight if score is not None and weight is not None else None
        contributions.append(
            FeatureContribution(
                feature=feature,
                score=score,
                weight=weight,
                contribution=contribution,
            )
        )
    return tuple(contributions)


def build_evidence(
    candidate: RankedCandidate,
    *,
    feature_order: tuple[str, ...] | None = None,
    # 거리 기준점의 표시 이름. None이면 문장이 "현재 위치"로 폴백한다 — 기기 GPS
    # 기준일 때가 그렇고, 넘기지 않은 호출자도 종전과 같은 문구를 얻는다.
    origin_name: str | None = None,
) -> RecommendationEvidence:
    """`RankedCandidate` 1건을 `RecommendationEvidence`로 변환한다.

    `feature_order`를 생략하면 `candidate.feature_scores`에 실제로 들어 있는
    키로 순서를 정한다(`resolve_feature_order()`) — 2차 Scoring(D-040)의
    `"concentration"`과 요청 환경 채점의 `"environment"`가 모두 여기서 갈린다.
    """
    resolved_order = feature_order or resolve_feature_order(candidate.feature_scores)
    return RecommendationEvidence(
        place_id=candidate.place_id,
        name=candidate.name,
        category=candidate.category,
        rank=candidate.rank,
        score=candidate.score,
        contributions=_build_contributions(candidate, resolved_order),
        is_unverified=candidate.is_unverified,
        warnings=candidate.warnings,
        distance_km=candidate.distance_km,
        remaining_minutes=candidate.remaining_minutes,
        weather_condition=candidate.weather_condition,
        environment_type=candidate.environment_type,
        concentration_level=candidate.concentration_level,
        weather_reason=candidate.weather_reason,
        travel_distance_m=candidate.travel_distance_m,
        travel_duration_seconds=candidate.travel_duration_seconds,
        travel_mode=candidate.travel_mode,
        taste_evidence_text=candidate.taste_evidence_text,
        taste_tag_label=candidate.taste_tag_label,
        taste_tag_documents=candidate.taste_tag_documents,
        origin_name=origin_name,
        co_visited_place_names=candidate.co_visited_place_names,
    )


def build_evidence_list(
    result: ScoringResult, *, origin_name: str | None = None
) -> tuple[RecommendationEvidence, ...]:
    """`ScoringResult.ranked` 전체를 순서 그대로 `RecommendationEvidence` 목록으로 변환한다.

    `origin_name`은 요청당 하나뿐인 값이라 후보별로 나르지 않고 여기서 전 건에
    같은 값을 찍는다 — 후보마다 다른 기준점을 표현할 수 있는 모양으로 두면
    픽스처가 서로 다른 값을 넣어도 아무도 잡지 못한다.
    """
    return tuple(build_evidence(candidate, origin_name=origin_name) for candidate in result.ranked)

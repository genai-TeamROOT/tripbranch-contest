"""Scoring v1: Candidate 목록에 하드 필터와 가중치 점수를 적용해 정렬한다.

역할: `ScoringCandidate` 목록을 받아 이전 노출/거절 후보와 폐점 후보(운영 유무
최종 판정)를 제외하고, 날씨·거리·남은 운영시간 Feature로 가중치 점수를 계산해
정렬한다. 카테고리(place_type/place_tag) 하드 필터는 Scoring 이전 단계에서
이미 처리됐다고 전제한다.
입력: `ScoringCandidate` 목록과 실행 조건(기준 시각, 날씨, 검색 반경, 이전
노출·거절 ID).
출력: `ScoringResult` (정렬된 `RankedCandidate` 목록, 후보별 사용 가중치,
제외 ID).
호출 시점: 추천 파이프라인이 카테고리 필터를 마친 뒤 순위를 매길 때 호출한다.
설계 근거: `docs/design/recommendation-scoring.md` 참고.
TODO: 혼잡도 Feature, 실제 이동시간 기반 거리, 예산/동행 하드 필터는 v2 이후.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.concentration_policy import ConcentrationLevel
from app.domain.models import (
    OperatingHours,
    PlaceEvidenceMatch,
    PlaceEvidenceSnippet,
    PreferenceTag,
    PreferenceTagMatch,
    PreferenceTagScoreDetail,
    ScoringCandidate,
    WeatherCondition,
)
from app.domain.travel_route import (
    MEASURED_ROUTE_SOURCES,
    RouteStatus,
    TravelMode,
    TravelRoute,
)
from app.domain.weather_judgment import WeatherReason
from app.place_search_policy import WALKING_SPEED_KM_PER_MINUTE

# B의 LLMOps Trace(record_trace(scoring_version=...))에 넘길 값 —
# backend/docs/package-b/llmops-trace-contract-v1.md §7 Q2. B는 이 값의 의미를
# 해석하지 않고 문자열로만 저장한다(B-01 경계 원칙). operating_hours.py의
# OPERATING_PARSER_VERSION과 동일한 semver 패턴. 점수 산출에 영향을 주는 변경
# (가중치, Feature 추가/제거, environment_type 판정표 등) 시 버전을 올린다 —
# 사소한 리팩터링·주석 변경은 올리지 않는다.
#
# **OPTIONAL_FEATURES에 이름을 넣는 것만으로는 올릴 근거가 되지 않는다.** 조립부
# 두 곳이 전부 실재 여부로 거른다(build_weights는 `in set(requested)`,
# weights_for_feature_scores는 `in feature_scores`). 올릴 근거는 그 키를
# feature_scores에 실제로 채우는 **새 경로**다 — 1.7.0이 그 예다.
SCORING_VERSION = "recommendation-scoring-1.10.0"

WEATHER_FEATURE = "weather"
ENVIRONMENT_FEATURE = "environment"

# 어느 실행에서나 채점되는 기본 3축. 여기 없는 Feature는 전부 선택 Feature다.
_BASE_WEIGHTS: Mapping[str, float] = {
    WEATHER_FEATURE: 0.40,
    "remaining_operating_time": 0.40,
    "distance": 0.20,
}

# 선택 Feature 1개가 받는 가중치와, 그 자리를 만들려고 기본 3축이 각각 내놓는 몫.
#
# **이 두 숫자는 새로 만든 게 아니라 기존 세트에서 읽어낸 것이다.**
# DEFAULT_WEIGHTS(0.40/0.40/0.20)와 CONCENTRATION_WEIGHTS(0.35/0.35/0.15+0.15)를
# 나란히 놓으면 세 축이 정확히 0.05씩 줄어 있었다 — 0.05 x 3축 = 0.15가 새 Feature
# 자리다. 취향(1.3.0)도 같은 모양을 따랐다. 그 규칙을 상수로 꺼냈을 뿐이라
# 기존 두 세트를 그대로 재현한다(test_scoring_weight_composition.py).
_OPTIONAL_WEIGHT = 0.15
_BASE_CONCESSION = 0.05

# 요청에 따라 켜고 끄는 Feature. 순서는 가중치에 영향을 주지 않지만 표시 순서
# (evidence._BASE_FEATURE_ORDER)와 맞춰 둔다.
#
# 새 선택 Feature를 추가할 때 손댈 곳은 여기 하나다. 예전처럼 조합별 가중치
# 상수를 열거하면 조합이 배로 늘고, 빠뜨린 조합에서 그 Feature가 **점수에서
# 조용히 사라진다** — 2026-08-20에 실제로 그랬다. taste가 1차에서는 순위를
# 정하는데 2차(CONCENTRATION_WEIGHTS)에는 키가 없어서, 취향으로 후보를 골라
# 놓고 최종 순위에서는 취향을 빼고 있었다. 가중치 합이 1.0이라 결측 재분배도
# 안 걸리고 예외도 안 났다.
#
# D-092: co_visited(RECOMMEND 2차 Scoring, rerank_with_co_visited())를 추가해
# taste/concentration과 함께 정확히 3개를 채운다 — 아래 _MAX_OPTIONAL_FEATURES
# 주석이 예고한 그 자리다. 이 튜플에 넣는 것만으로는 어떤 요청의 점수도 바꾸지
# 않는다 — feature_scores에 "co_visited" 키가 실제로 있는 요청(rerank_with_co_visited()를
# 탄 요청)에서만 build_weights()/weights_for_feature_scores()가 이 이름을 활성으로 본다.
OPTIONAL_FEATURES: tuple[str, ...] = ("taste", "concentration", "co_visited")

# 기본 3축 중 distance(0.20)가 0.05씩 내놓으므로 4개째에서 0이 된다.
_MAX_OPTIONAL_FEATURES = 3


def build_weights(optional_features: Iterable[str] = ()) -> dict[str, float]:
    """켜진 선택 Feature에 맞춰 가중치를 조립한다.

    기본 3축은 켜진 선택 Feature 수만큼 `_BASE_CONCESSION`씩 양보하고, 켜진
    Feature마다 `_OPTIONAL_WEIGHT`를 준다. 합은 항상 1.0이다.

    켜지지 않은 Feature는 키 자체가 없다 — "결측"이 아니라 "존재하지 않는
    Feature"다(concentration-conditions.md §2.3). 결측 재분배
    (`redistribute_weights()`)와 섞이지 않게 이 구분을 유지한다.

    모르는 이름이 오면 멈춘다. 조용히 무시하면 오타 하나로 그 Feature가 점수에서
    빠진 채 정상처럼 돌아간다 — 이 함수가 막으려는 사고가 바로 그거다.
    """
    requested = list(optional_features)
    unknown = [feature for feature in requested if feature not in OPTIONAL_FEATURES]
    if unknown:
        raise ValueError(f"알 수 없는 선택 Feature: {unknown}")
    active = [feature for feature in OPTIONAL_FEATURES if feature in set(requested)]
    if len(active) > _MAX_OPTIONAL_FEATURES:
        raise ValueError(
            f"선택 Feature는 최대 {_MAX_OPTIONAL_FEATURES}개다"
            f"(기본 축 가중치가 0 이하가 된다): {active}"
        )
    concession = _BASE_CONCESSION * len(active)
    # 0.4 - 0.1 = 0.30000000000000004 같은 부동소수 찌꺼기를 여기서 끊는다.
    weights = {feature: round(weight - concession, 10) for feature, weight in _BASE_WEIGHTS.items()}
    weights.update({feature: _OPTIONAL_WEIGHT for feature in active})
    return weights


DEFAULT_WEIGHTS: Mapping[str, float] = build_weights()

# D-040: concentration_intent가 AVOID/SEEK일 때만 쓰는 2차 Scoring 기본 가중치.
# 1차 Scoring은 이 이름 자체를 모른다.
CONCENTRATION_WEIGHTS: Mapping[str, float] = build_weights(("concentration",))

# 사용자가 취향을 말한 요청에서만 쓰는 가중치. 취향을 말하지 않은 요청은
# DEFAULT_WEIGHTS로 남는다.
TASTE_WEIGHTS: Mapping[str, float] = build_weights(("taste",))

# 취향 유사도를 0~1 점수로 펴는 구간.
#
# 상한이 1.0이 아닌 이유: 실측 관측 최대가 0.813이고 중심점별 평균은
# 0.554~0.609다(2026-08-19, 종로 4개 중심점 x 발화 20개, 후보 150곳,
# backend/test_results/taste_score_distribution.csv). 1.0으로 나누면 1등
# 후보조차 평균 0.22점에 머문다 — 도보 예산 분모가 과대해 점수가 눌렸던 것과
# 같은 구조의 함정이다.
#
# 0.65로 잡은 근거: 최고 유사도 32건 중 26건이 0.65 미만이다. 0.813·0.732 같은
# 예외적 상위값에 맞추면 대부분의 질의가 저점에 몰린다. 대가는 강한 질의가
# 0.65에서 clipping돼 상위권 변별이 줄어드는 것이고, 의도한 선택이다.
#
# 하한 0.43은 검색 컷값과 같다(RAG 계획 문서 7.13절). 이 값 미만은 애초에
# 검색 결과로 오지 않는다.
_TASTE_CUT = 0.43
_TASTE_FULL_SCORE = 0.65

# 태그는 임베딩을 대체하지 않고 남은 점수 여백만 일부 채운다. 같은 리뷰·블로그에서
# 태그와 임베딩이 함께 만들어졌을 수 있어 단순 합산하면 근거를 두 번 세게 된다.
# 0.35는 태그만 있는 후보의 taste를 최대 0.35로 제한하면서, 임베딩이 이미 높은
# 후보에는 작은 확인 보너스만 주는 초기값이다. 개발자 패널에서 두 원점수와 결합식을
# 함께 확인한 뒤 실측으로 조정한다.
_TASTE_TAG_BONUS_FACTOR = 0.35

# 남은 운영시간이 이 값(분) 이상이면 만점(1.0)으로 취급한다.
_REMAINING_TIME_FULL_SCORE_MINUTES = 120.0

_WEATHER_FIT_TABLE: Mapping[tuple[WeatherCondition, str], float] = {
    (WeatherCondition.GOOD, "indoor"): 0.70,
    (WeatherCondition.GOOD, "outdoor"): 1.00,
    (WeatherCondition.GOOD, "unknown"): 0.85,
    (WeatherCondition.NEUTRAL, "indoor"): 0.80,
    (WeatherCondition.NEUTRAL, "outdoor"): 0.80,
    (WeatherCondition.NEUTRAL, "unknown"): 0.80,
    (WeatherCondition.BAD, "indoor"): 1.00,
    (WeatherCondition.BAD, "outdoor"): 0.30,
    (WeatherCondition.BAD, "unknown"): 0.60,
}
_WEATHER_FIT_DEFAULT = 0.80

# 사용자가 실내/실외를 명시했는데 날씨 언급이 없으면, 날씨 사실 대신 이 표로
# 같은 자리의 Feature 점수를 매긴다(D-051 문제 5의 후속). 값은
# _WEATHER_FIT_TABLE의 BAD 행과 같은 폭이다 — "명시적으로 피해야 할 환경"에
# 이미 쓰던 간격이라 근거 없는 숫자를 새로 만들지 않았다.
_ENVIRONMENT_FIT_TABLE: Mapping[tuple[str, str], float] = {
    ("indoor", "indoor"): 1.00,
    ("indoor", "outdoor"): 0.30,
    ("indoor", "unknown"): 0.60,
    ("outdoor", "outdoor"): 1.00,
    ("outdoor", "indoor"): 0.30,
    ("outdoor", "unknown"): 0.60,
}
_ENVIRONMENT_FIT_DEFAULT = 0.60

# 이 두 값일 때만 환경으로 채점한다. `any`는 "실내외 무관"이라 제외한다 —
# 되묻기 기본값이 ANY라(D-053) 여기 넣으면 되묻기 답변 경로 전체가 환경
# 판정으로 넘어간다.
_ENVIRONMENT_PREFERENCES = frozenset({"indoor", "outdoor"})

_UNVERIFIED_WARNING = "방문 전에 운영 여부를 확인해주세요."
# **한 문장으로 줄였다**(2026-09-08). 전에는 "지금은 운영시간이 아니에요. 방문
# 전에 다시 확인해주세요."였는데, 추천 카드가 이 경고를 한 줄로 자르므로
# (PlaceCard의 `truncate`) 화면에서 "…방문 " 에서 끊겼다 — 필요 폭 250px에 자리는
# 160px뿐이라 90px이 잘렸다(실측). 뒷문장은 카드에서 한 번도 읽힌 적이 없다.
#
# 잘린 부분을 살리는 대신 문장을 줄인 이유는, 카드가 이미 그 말을 하고 있기
# 때문이다 — 운영시간 자리에 "19:00~23:00 (현재 운영시간 아님)"이 찍히고 눌러
# 들어가면 상세가 열린다. 카드를 두 줄로 늘려 "다시 확인해주세요"를 더 보여주는
# 것보다 짧게 다 보이는 편이 낫다고 판단했다.
_CLOSED_NOW_WARNING = "지금은 운영시간이 아니에요."
# 주기 휴무인 것은 알지만 몇 번째인지 원문에 없는 후보에 붙인다(`월 1회 월요일`).
#
# 그날 닫을 수도 있지만 **확인할 수 없다.** 매주로 치면 안 쉬는 주에 멀쩡한 장소가
# 사라지고, 무시하면 휴무일에 추천이 나간다. 후보는 살리고 사실만 말한다 —
# 확인하지 못한 것을 확인한 척하지 않는다(D-042와 같은 원칙).
_UNCERTAIN_CLOSURE_WARNING = "이곳은 주기적으로 쉬는 날이 있어요. 방문 전에 확인해주세요."

# 무장애 판정이 `partial`인 후보에 붙이는 안내. 어휘마다 막히는 것이 달라
# 문구도 나눈다 — "일부 구역이 어렵다"만으로는 휠체어가 못 가는 것인지 점자
# 안내가 없는 것인지 알 수 없다.
#
# 판정이 붙는 어휘는 셋뿐이다. 나머지 여섯(화장실·주차장·유아·대여·좌석·저상버스)은
# 아직 원문 규칙으로 거르므로 후보에 있다는 것 말고는 할 말이 없다.
_ACCESSIBILITY_PARTIAL_WARNINGS: Mapping[str, str] = {
    "wheelchair_access": "일부 구역은 휠체어 접근이 어려워요.",
    "stroller_access": "일부 구역은 유모차로 다니기 어려워요.",
    "visual_guide": "점자·음성 안내가 일부 구역에만 있어요.",
}

# 이 판정만 안내한다. `possible`은 할 말이 없고, `impossible`은 저장소 조회가
# 이미 후보에서 뺐으므로 여기까지 오지 않는다.
_PARTIAL_VERDICT = "partial"


def _accessibility_warnings(candidate: ScoringCandidate) -> tuple[str, ...]:
    """무장애 판정이 `partial`인 어휘마다 안내 한 줄을 만든다.

    요구하지 않은 어휘는 애초에 판정이 오지 않으므로(저장소 조회가 요구한 것만
    올린다) 여기서 다시 거르지 않는다.

    문구가 없는 어휘는 건너뛴다. 판정표가 셋에만 있어 지금은 일어나지 않지만,
    나중에 어휘가 늘었을 때 빈 문자열이 경고 목록에 끼어 카드가 빈 줄을 띄우는
    것보다 낫다.
    """
    verdicts = candidate.accessibility_verdicts
    if not verdicts:
        return ()
    return tuple(
        warning
        for need, verdict in sorted(verdicts.items())
        if verdict == _PARTIAL_VERDICT and (warning := _ACCESSIBILITY_PARTIAL_WARNINGS.get(need))
    )


class ExclusionReason(StrEnum):
    """하드 필터에서 후보를 제외한 이유."""

    CLOSED = "closed"
    ALREADY_SHOWN = "already_shown"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PreparedCandidate:
    """하드 필터를 통과해 점수 계산을 기다리는 후보."""

    candidate: ScoringCandidate
    remaining_minutes: float | None
    is_unverified: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExcludedCandidate:
    """하드 필터에서 제외된 후보와 그 사유."""

    candidate: ScoringCandidate
    reason: ExclusionReason

    @property
    def place_id(self) -> str:
        return self.candidate.place_id


@dataclass(frozen=True)
class PrepareResult:
    """하드 필터가 입력 후보 전체를 통과/제외로 분류한 결과."""

    eligible_candidates: tuple[PreparedCandidate, ...]
    excluded_candidates: tuple[ExcludedCandidate, ...]
    input_count: int

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_candidates)

    @property
    def excluded_place_ids(self) -> tuple[str, ...]:
        return tuple(candidate.place_id for candidate in self.excluded_candidates)


@dataclass(frozen=True)
class RankedCandidate:
    """점수 계산 후 정렬된 후보 1건."""

    place_id: str
    name: str
    category: str
    rank: int
    score: float
    feature_scores: Mapping[str, float | None]
    weights_used: Mapping[str, float]
    is_unverified: bool
    warnings: tuple[str, ...]
    # 정규화 점수(feature_scores)만으로는 "직선거리 약 400m" 같은 구체적인
    # 문장을 만들 수 없어, Explainability Layer(D-06)가 쓸 원본 값을 보존한다.
    distance_km: float
    remaining_minutes: float | None
    weather_condition: WeatherCondition | None
    environment_type: str
    # 실측 경로가 있을 때만 채워진다. 거리 Feature 점수를 이 값으로 계산했다는
    # 표시이자, 근거 문장·응답 표기의 원본이다(`_proximity_score()`). travel_mode는
    # 어떤 이동수단으로 잰 값인지다 — 문장이 "걸어서"라고 말할 수 있는지가 여기서
    # 갈린다(explanation.py).
    travel_distance_m: int | None = None
    travel_duration_seconds: int | None = None
    travel_mode: TravelMode | None = None
    # 취향 근거로 쓴 문장 원문(유사도 1위). 근거 문장이 "왜 취향에 맞는지"를
    # 사람 말로 설명하는 데 쓴다 — 점수만으로는 납득이 안 된다.
    taste_evidence_text: str | None = None
    # 검색이 실제로 찾은 근거 문장 전부(장소당 최대 DEFAULT_MATCH_COUNT건, 유사도
    # 내림차순). taste_evidence_text는 이 중 1위만 문장 조립용으로 뽑은 것이고,
    # 이건 개발자 디버그 화면이 "taste=0인데 왜 0인지"를 원문으로 확인하는 데 쓴다.
    taste_evidence: tuple[PlaceEvidenceSnippet, ...] = ()
    # 태그 경로로 채점했을 때 맞은 태그의 라벨("혼자 가기 좋은")과 그 근거가 된
    # 긍정 문서 수. 임베딩 경로에서는 항상 비어 있다. 근거 문장이 유사도 1위
    # 원문(taste_evidence_text) 대신 이 값을 쓴다 — 태그로 뽑았는데 문장은
    # 임베딩에서 가져오면 "혼밥으로 뽑고 가족 외식 문장을 보여주는" 어긋남이 그대로다.
    taste_tag_label: str | None = None
    taste_tag_documents: int = 0
    # 태그 최종 점수와 코드별 상대 점수 계산식. 임베딩 근거와 분리해 개발자
    # 화면에서 "무엇이 순위에 반영됐는지"를 확인할 수 있게 보존한다.
    taste_tag_score: float | None = None
    taste_tag_details: tuple[PreferenceTagScoreDetail, ...] = ()
    # 임베딩과 태그를 결합하기 전·후 값을 개발자 화면까지 보존한다. similarity는
    # 검색 결과의 평균 코사인 유사도, embedding_score는 0.43~0.65를 0~1로 편 값,
    # combined_score는 실제 feature_scores["taste"]에 들어간 값이다.
    taste_embedding_similarity: float | None = None
    taste_embedding_score: float | None = None
    taste_combined_score: float | None = None
    # D-040: 2차 Scoring(rerank_with_concentration())에서만 채워진다. 1차 Scoring
    # 결과는 concentration 자체를 모르므로 항상 None이다 — explanation.py가 문장을
    # "한적함/보통/다소 혼잡/혼잡" 중 무엇으로 쓸지 고르는 데 필요하다(direction이
    # 이미 반영된 concentration_score만으로는 실제 붐빔 정도를 알 수 없다).
    concentration_level: ConcentrationLevel | None = None
    # WeatherCondition만으로는 "왜"(비/눈/폭염/한파 중 무엇 때문)를 알 수 없어서
    # 근거 문장(explanation.py) 조립에 따로 필요하다 — 점수 계산에는 안 쓰인다.
    weather_reason: WeatherReason = None
    # D-092: 2차 Scoring(rerank_with_co_visited())에서만 채워진다. 이 후보와
    # place_associations(B-owned, D-088) 상 "함께 방문된 이력"이 있는, 같은
    # 응답 안의 다른 후보 이름들(최대 2개, 중복 제거). concentration_level과
    # 같은 이유로 원본을 들고 간다 — co_visited_score만으로는 "누구와" 겹쳤는지
    # 근거 문장에 쓸 수 없다.
    co_visited_place_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoringResult:
    """Scoring v1의 최종 출력."""

    ranked: tuple[RankedCandidate, ...]
    excluded_place_ids: tuple[str, ...]
    # 폐점이라 제외된 후보만 별도로 센다(이전 노출/거절 제외와 구분) — 호출부가
    # "결과가 0건인 이유가 전부 폐점인가"를 판단하는 데 쓴다.
    excluded_closed_place_ids: tuple[str, ...] = ()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _remaining_minutes(now: datetime, hours: OperatingHours) -> float | None:
    """`now`가 영업시간 안이면 마감까지 남은 분을, 밖이면 `None`(폐점)을 반환한다.

    정기 휴무일은 구간 안이어도 `None`이다 — `hours`가 그날 실제로 여는 시간이
    아니라 평소 구간이기 때문이다. 이 한 곳에서 걸러 두면 폐점 판정·잔여시간
    Feature·"운영시간 무시" 경고가 모두 같은 기준을 따른다.
    """
    if hours.is_regular_closure:
        return None
    current_time = now.time()
    if not (hours.open_time <= current_time < hours.close_time):
        return None
    close_at = datetime.combine(
        now.date(),
        hours.close_time,
        tzinfo=now.tzinfo,
    )
    return (close_at - now).total_seconds() / 60.0


def _remaining_time_score(remaining_minutes: float) -> float:
    return _clamp(remaining_minutes / _REMAINING_TIME_FULL_SCORE_MINUTES, 0.0, 1.0)


def _weather_fit_score(candidate: ScoringCandidate, weather_condition: WeatherCondition) -> float:
    return _WEATHER_FIT_TABLE.get(
        (weather_condition, candidate.environment_type), _WEATHER_FIT_DEFAULT
    )


def _environment_fit_score(candidate: ScoringCandidate, requested_environment: str) -> float:
    return _ENVIRONMENT_FIT_TABLE.get(
        (requested_environment, candidate.environment_type), _ENVIRONMENT_FIT_DEFAULT
    )


def uses_environment_feature(requested_environment: str | None) -> bool:
    """요청 환경이 실내/실외로 명시됐으면 True — 날씨 대신 환경으로 채점한다."""
    return requested_environment in _ENVIRONMENT_PREFERENCES


def _rename_weather_weight(weights: Mapping[str, float]) -> dict[str, float]:
    """날씨 자리의 가중치를 값 그대로 환경 Feature로 옮긴다.

    숫자는 바꾸지 않는다 — 두 Feature는 한 실행에서 동시에 쓰이지 않아 합이
    그대로 1.0이고, 결측 재분배도 기존과 같은 조건에서만 일어난다.
    """
    return {
        (ENVIRONMENT_FEATURE if feature == WEATHER_FEATURE else feature): weight
        for feature, weight in weights.items()
    }


def weights_for_environment(
    weights: Mapping[str, float], requested_environment: str | None
) -> dict[str, float]:
    """요청 환경으로 채점하는 실행이면 날씨 가중치 키를 환경으로 바꿔 돌려준다."""
    if not uses_environment_feature(requested_environment):
        return dict(weights)
    return _rename_weather_weight(weights)


def weights_for_feature_scores(
    feature_scores: Mapping[str, float | None],
) -> dict[str, float]:
    """이미 채점된 feature_scores를 보고 가중치를 조립한다.

    2차 Scoring(`rerank_with_concentration()`)은 조건을 다시 받지 않고 1차
    결과만 재사용하므로, **어느 Feature로 채점됐는지를 키 존재로 판단한다.**
    날씨/환경 중 어느 쪽인지도, 취향이 켜져 있었는지도 여기서 갈린다.

    기본 3축은 키가 없어도 항상 넣는다 — 1차에서 날씨 조회에 실패하면
    `feature_scores["weather"]`가 `None`으로 들어오는데, 그건 결측이지 Feature가
    없는 게 아니다. 키 유무로 축을 빼면 가중치 합이 1.0에 못 미쳐 2차를 탄
    요청만 점수가 통째로 낮아진다. 결측 처리는 호출부가
    `redistribute_weights()`로 따로 한다.
    """
    active = [feature for feature in OPTIONAL_FEATURES if feature in feature_scores]
    weights = build_weights(active)
    if ENVIRONMENT_FEATURE not in feature_scores:
        return weights
    return _rename_weather_weight(weights)


def _distance_score(distance_km: float, max_distance_km: float) -> float:
    if max_distance_km <= 0:
        return 1.0 if distance_km <= 0 else 0.0
    return _clamp(1.0 - distance_km / max_distance_km, 0.0, 1.0)


def _taste_score(match: PlaceEvidenceMatch | None) -> float:
    """취향 근거의 평균 유사도를 0~1 점수로 편다.

    근거가 없는 후보는 **0.0이지 결측이 아니다.** "계산하지 못했다"(날씨 조회
    실패)와 "안 맞는다"는 다르다 — 후보마다 결측 여부가 갈리면 한 순위 안에서
    가중치 세트가 달라져 자를 두 개 쓰는 셈이 된다(도보 `_consistent_routes()`
    에서 실제로 순위가 뒤집혔다).

    후보 전체가 0.0인 경우도 순위에는 무해하다 — 모두 같은 만큼 낮아진다.
    실측에서 "아이랑 비 오는 날 실내"는 어느 중심점에서도 컷을 넘지 못했다.
    """
    if match is None:
        return 0.0
    span = _TASTE_FULL_SCORE - _TASTE_CUT
    if span <= 0:
        return 0.0
    return _clamp((match.avg_similarity - _TASTE_CUT) / span, 0.0, 1.0)


def _taste_similarity(match: PlaceEvidenceMatch | None) -> float | None:
    """임베딩 점수 환산 전 평균 코사인 유사도를 디버그용으로 보존한다."""
    return match.avg_similarity if match is not None else None


def combine_taste_scores(
    embedding_score: float | None,
    tag_score: float | None,
) -> float:
    """임베딩을 기본점수로 두고 태그가 남은 여백의 35%까지만 가산한다.

    ``embedding + 0.35 * tag * (1 - embedding)``이라 태그가 없어도 임베딩
    점수는 그대로 유지되고, 둘 다 강해도 1.0을 넘지 않는다. 태그만 있으면 최대
    0.35라서 동일 원문에서 파생된 두 신호를 단순 합산하는 이중 계상을 피한다.
    """
    embedding = _clamp(embedding_score or 0.0, 0.0, 1.0)
    if tag_score is None:
        return embedding
    tag = _clamp(tag_score, 0.0, 1.0)
    return _clamp(
        embedding + _TASTE_TAG_BONUS_FACTOR * tag * (1.0 - embedding),
        0.0,
        1.0,
    )


def _taste_evidence_text(match: PlaceEvidenceMatch | None) -> str | None:
    """근거 문장 중 유사도 1위 원문을 꺼낸다.

    RPC가 유사도 내림차순으로 돌려주므로 첫 조각이 가장 가까운 문장이다.
    점수만 보여주면 "왜 이게 내 취향이냐"에 답할 수 없어서, 사람이 읽을 수
    있는 근거를 하나 들고 간다.
    """
    if match is None or not match.snippets:
        return None
    return match.snippets[0].source_text


def _taste_evidence_snippets(
    match: PlaceEvidenceMatch | None,
) -> tuple[PlaceEvidenceSnippet, ...]:
    """검색이 찾은 근거 문장 전부를 유사도 내림차순으로 그대로 넘긴다."""
    if match is None:
        return ()
    return match.snippets


def match_preference_tags(
    content_id: str,
    requested_codes: Sequence[str],
    tags: Sequence[PreferenceTag],
    *,
    max_positive_documents_by_code: Mapping[str, int] | None = None,
) -> PreferenceTagMatch:
    """요청 취향 코드와 그 장소의 태그를 맞춰 taste 점수를 만든다.

    **맞으면 주고, 안 맞으면 0이다. 감점은 없다.** 3상태(일치 / 태그는 있는데
    불일치 / 태그 없음)로 나눠 가운데를 중립값으로 두는 안을 먼저 검토했는데,
    실측이 그 안을 기각했다 — 후보 30곳에서 태그 자체가 없는 곳이 평균 59.3%이고
    지역 편차가 23~90%로 컸다(2026-09-17, `test_results/preference_tag_coverage.csv`).
    "태그 없음"을 "태그는 있는데 불일치"보다 높게 두면 안국역처럼 후기가 많이
    수집된 지역에서 **자료가 있는 장소가 자료가 없는 장소보다 낮은 점수**를 받는다.
    그래서 불일치와 미수집을 같은 0으로 두고 일치에만 점수를 준다.

    전원이 0인 경우도 순위에는 무해하다 — 모두 같은 만큼 낮아진다(`_taste_score()`와
    같은 이유). 태그가 거의 없는 지역(실측에서 홍대입구 27/30)에서는 이 축이
    사실상 동점이 되어 기존 순위가 그대로 유지된다.
    """
    if not requested_codes:
        return PreferenceTagMatch(content_id=content_id, score=0.0)

    by_code = {tag.code: tag for tag in tags}
    scores: list[float] = []
    details: list[PreferenceTagScoreDetail] = []
    for code in requested_codes:
        tag = by_code.get(code)
        if tag is None:
            scores.append(0.0)
            details.append(
                PreferenceTagScoreDetail(
                    code=code,
                    label=None,
                    positive_documents=0,
                    negative_documents=0,
                    candidate_max_positive_documents=max(
                        0, int((max_positive_documents_by_code or {}).get(code, 0))
                    ),
                    score=0.0,
                )
            )
            continue
        candidate_max = max(
            0,
            int((max_positive_documents_by_code or {}).get(code, tag.positive_documents)),
        )
        # 부정이 긍정보다 많은 태그는 "그 취향에 맞다"는 근거가 아니다. 실측 64건
        # 에서 부정이 섞인 것은 3건뿐이고 우세한 경우는 없었지만, 태그 이름만
        # 보고 점수를 주면 "혼자 가긴 좀"이라는 후기가 만점이 된다.
        if tag.negative_documents > tag.positive_documents:
            scores.append(0.0)
            details.append(
                PreferenceTagScoreDetail(
                    code=code,
                    label=tag.label,
                    positive_documents=tag.positive_documents,
                    negative_documents=tag.negative_documents,
                    candidate_max_positive_documents=candidate_max,
                    score=0.0,
                )
            )
            continue
        # 장소마다 리뷰·블로그 수집량이 달라 절대 언급 수를 그대로 비교할 수 없다.
        # 같은 요청의 하드 필터 통과 후보 안에서 가장 많이 언급된 장소를 1.0으로
        # 두고 상대 비중만 가산한다. 태그가 없는 장소에는 벌점을 주지 않고 0점이다.
        relative_score = (
            _clamp(tag.positive_documents / candidate_max, 0.0, 1.0)
            if candidate_max > 0
            else 0.0
        )
        scores.append(relative_score)
        details.append(
            PreferenceTagScoreDetail(
                code=code,
                label=tag.label,
                positive_documents=tag.positive_documents,
                negative_documents=tag.negative_documents,
                candidate_max_positive_documents=candidate_max,
                score=relative_score,
            )
        )

    strongest = max(details, key=lambda detail: detail.score, default=None)
    has_strongest = strongest is not None and strongest.score > 0
    return PreferenceTagMatch(
        content_id=content_id,
        score=sum(scores) / len(scores),
        label=strongest.label if has_strongest else None,
        documents=strongest.positive_documents if has_strongest else 0,
        details=tuple(details),
    )


def _taste_score_from_tags(match: PreferenceTagMatch | None) -> float:
    """태그 일치 점수를 그대로 쓴다. 태그가 아예 없는 후보는 0.0이다."""
    if match is None:
        return 0.0
    return _clamp(match.score, 0.0, 1.0)


def _taste_tag_label(match: PreferenceTagMatch | None) -> str | None:
    return match.label if match is not None else None


def _taste_tag_documents(match: PreferenceTagMatch | None) -> int:
    return match.documents if match is not None else 0


def _travel_minutes_budget(max_distance_km: float, budget_speed_km_per_min: float) -> float:
    """검색 반경을 소요시간 예산(분)으로 되돌린다.

    호출부(`to_search_radius_km()`)가 `max_travel_time × 속도`로 반경을 만들었으므로,
    같은 속도로 나누면 **사용자가 말한 이동시간이 그대로** 나온다. 그래서 여기 쓰는
    속도는 반경을 만든 속도와 반드시 같아야 하고, 호출부가
    `to_search_radius_speed_km_per_min()`으로 그 값을 넘긴다.

    **측정한 이동수단의 속도로 나누지 않는다(D-118).** 예전에는 실측 결과에 적힌
    mode로 `TRAVEL_SPEED_KM_PER_MINUTE`를 찾아 나눴는데, 그러면 반경을 만든 속도와
    나누는 속도가 갈린다. 기본 반경 2.0km는 도보 속도로 만든 값이라 대중교통
    속도(20km/h)로 나누면 예산이 **6.0분**이 되고, 그건 사용자가 약속한 적 없는
    숫자다 — "20km/h로 2km를 가면 6분"이라는 계산일 뿐이다. 반경 안의 대중교통
    실측은 대부분 10~19분이라 `_travel_time_score()`의 clamp에 전부 0으로 잘리고,
    그 손해가 **다른 수단으로 전환된 후보에만** 간다(도보 후보는 28.6분 예산으로
    채점되므로). 한 순위표 안에서 후보마다 이동수단이 다를 수 있게 되면서
    (`to_measured_travel_modes()`), 자는 요청당 하나로 고정해야 한다.

    이동시간을 말한 요청은 이 변경으로 값이 바뀌지 않는다 — 반경이 `시간 × 속도`라
    같은 속도로 나누면 그 시간이 그대로 나온다. 말하지 않은 요청은 기본 반경
    2.0km에서 약 28.6분이 되고, 대중교통·자동차로 잰 후보도 같은 28.6분으로 잰다.

    속도가 0 이하면 ValueError로 멈춘다 — 조용히 도보 속도로 재는 것보다 낫다.

    분모가 사용자 약속 그 자체라는 게 이 방식의 핵심이다. 우회 계수를 추정해
    보정하는 안도 있었지만, 그 계수의 근거가 약해서 택하지 않았다.

    실측으로 확인한 한계(카카오 실 API, 종로 5개 지점, 2026-08-19):

    - 우회 계수 평균 **1.65배**(직선 대비 실제 보행 경로), 범위 1.37~2.13
    - 실보행 속도 평균 **3.70km/h**, 범위 3.32~3.94 (이 상수의 가정은 4.20km/h)
    - 두 오차가 곱해져, 실제 소요는 이 예산이 가정하는 값의 **평균 1.88배**다
      (범위 1.49~2.50). 직선거리 기준으로 환산하면 실효 속도가 평균 2.31km/h이고
      **사람이 그 속도로 걷는다는 뜻이 아니다** — 우회 때문에 직선거리가 그만큼
      느리게 좁혀진다는 뜻이다.
    - 결과적으로 **직선거리가 반경의 절반쯤을 넘으면 거리 점수가 0**이 된다
      (표본 평균 55%, 범위 40~67%)
    - 표본이 종로 5개 지점뿐이고 편차가 크다. 이 값으로 상수를 바꾸기 전에
      표본을 넓혀야 한다.

    이걸 알고도 보정하지 않는다. 사용자가 "30분 안에"라고 했는데 실제로 41분
    걸리는 곳이라면 0점이 사실에 맞다 — 예산을 늘리면 "30분"이 사실상 56분이
    된다. 거리 가중치가 0.2라 0점이어도 총점 손실은 0.2로 제한되고, 가까운
    후보들 사이의 변별력은 그대로 남는다.

    근본 원인은 이 함수가 아니라 `to_search_radius_km()`이 도보 속도를
    4.20km/h로 잡아 **검색 반경 자체가 도보 기준으로 과대**하다는 데 있다.
    반경 산정을 조정하려면 C·A와 협의가 필요하다.
    """
    if budget_speed_km_per_min <= 0:
        raise ValueError("시간 예산 속도는 0보다 커야 합니다.")
    return max_distance_km / budget_speed_km_per_min


def _travel_time_score(
    duration_seconds: int, max_distance_km: float, budget_speed_km_per_min: float
) -> float:
    budget_minutes = _travel_minutes_budget(max_distance_km, budget_speed_km_per_min)
    if budget_minutes <= 0:
        return 0.0
    return _clamp(1.0 - (duration_seconds / 60.0) / budget_minutes, 0.0, 1.0)


def _applied_travel_route(route: TravelRoute | None) -> TravelRoute | None:
    """실제로 채점에 쓸 수 있는 경로만 남긴다.

    세 가지를 함께 봐야 한다.

    - `status`: `NO_DATA`/`UNAVAILABLE`은 조회에 실패한 것이다.
    - `duration_seconds`: SUCCESS일 때만 값이 보장된다(`travel_route.py`
      `__post_init__`). 계약상 보장이 아니므로 상태와 따로 확인한다.
    - `source`: **`STRAIGHT_LINE_ESTIMATE`는 실측이 아니라 직선거리 추정이다.**
      `TravelRouteTool`이 도보 실패분을 이 값으로 채우고, 개발용 fake Provider도
      전부 이 값을 내보낸다. 그런데 둘 다 `status`는 `SUCCESS`라 상태만으로는
      실측과 구분되지 않는다(C 리뷰 지적, 2026-08-19).

      이동수단을 추가할 때 `MEASURED_ROUTE_SOURCES`에 그 벤더 source를 넣지
      않으면, 실측을 받아놓고도 전부 직선거리로 떨어진다.

    `source`를 빠뜨리면 두 가지가 깨진다. (1) 직선거리 추정이 실측인 척
    `travel_duration_seconds`에 실려 "걸어서 약 9분"이라는 거짓 문구가 나간다.
    (2) 실측과 추정은 낙관도가 달라 한 순위표에 섞이면 안 되는데
    (`_consistent_routes()`), 상태가 똑같이 SUCCESS라 그 검사를 그냥 통과한다.

    반환값이 `None`이면 거리 Feature는 직선거리로 계산되고, 응답에도 도보 값이
    실리지 않는다 — 추정을 실측으로 포장하지 않는다.
    """
    if (
        route is not None
        and route.status is RouteStatus.SUCCESS
        and route.source in MEASURED_ROUTE_SOURCES
        and route.duration_seconds is not None
    ):
        return route
    return None


def _travel_field(route: TravelRoute | None, field: str) -> int | None:
    """채점에 실제로 쓴 경로에서만 값을 꺼낸다 — 폴백 후보는 `None`이다."""
    applied = _applied_travel_route(route)
    return None if applied is None else getattr(applied, field)


def _travel_mode_of(route: TravelRoute | None) -> TravelMode | None:
    """거리·소요시간과 같은 조건으로 이동수단을 꺼낸다.

    세 값이 같은 경로에서 나와야 응답의 mode와 수치가 어긋나지 않는다.
    """
    applied = _applied_travel_route(route)
    return None if applied is None else applied.mode


def _proximity_score(
    candidate: ScoringCandidate,
    route: TravelRoute | None,
    max_distance_km: float,
    budget_speed_km_per_min: float,
) -> float:
    """거리 Feature 점수. 실측 도보 시간이 있으면 쓰고, 없으면 직선거리로 돌아간다.

    폴백을 결측(`missing_features`)으로 처리하지 않는 이유: 거리는 이미 알고
    있어서 결측이 아니고, 재분배를 태우면 도보 조회에 실패한 후보만 거리
    Feature가 빠져 오히려 유리해진다.
    """
    applied = _applied_travel_route(route)
    if applied is not None:
        assert applied.duration_seconds is not None
        return _travel_time_score(
            applied.duration_seconds, max_distance_km, budget_speed_km_per_min
        )
    return _distance_score(candidate.distance_km, max_distance_km)


def concentration_score(concentration_rate: float, *, seek: bool) -> float:
    """혼잡률(평시 대비 0~100대 상대 비율)을 0~1 점수로 선형 정규화한다.

    `seek`(concentration_intent=SEEK, 붐비는 곳 선호)이면 혼잡률이 높을수록
    점수가 높고, `seek=False`(AVOID, 한적한 곳 선호)면 그 반대다. distance/
    remaining_operating_time과 같은 연속값 스타일을 따른다 — 4단계 구간
    (quiet/normal/slightly_crowded/crowded) 매핑 대신 선형 정규화를 택해
    정보 손실을 피한다.
    """
    normalized = _clamp(concentration_rate / 100.0, 0.0, 1.0)
    return normalized if seek else 1.0 - normalized


def co_visited_score(hit_count: int, max_hit_count: int) -> float:
    """D-092: 이 후보가 이번 응답 내 다른 후보와 "함께 방문된 이력"(place_associations,
    B-owned) 쌍에 몇 번 등장했는지를, 이번 응답에서 관측된 최댓값 대비 0~1로
    정규화한다.

    concentration_rate(0~100 고정 스케일)와 달리 "몇 번 함께 갔는지"는 관광지마다
    표본 수가 달라 절대값으로 비교할 근거가 없다. 같은 응답 안에서 상대적으로 많이
    겹치는 후보가 더 높은 점수를 받으면 된다 — taste_score가 실측 분포 상한(0.65)에
    맞춰 상대적으로 클리핑한 것과 같은 이유로 상대 스케일을 택했다.

    쌍이 하나도 없는 후보(hit_count=0)도 0.0이지 결측이 아니다 — _taste_score와
    같은 이유다: 후보마다 결측 여부가 갈리면 한 순위 안에서 가중치 세트가 달라져
    자를 두 개 쓰는 셈이 된다.
    """
    if max_hit_count <= 0:
        return 0.0
    return _clamp(hit_count / max_hit_count, 0.0, 1.0)


def redistribute_weights(
    weights: Mapping[str, float], missing_features: Iterable[str]
) -> dict[str, float]:
    """결측 Feature들을 제외하고 나머지 가중치를 기존 비중에 비례해 재분배한다."""
    missing = set(missing_features)
    remaining = {feature: weight for feature, weight in weights.items() if feature not in missing}
    total_remaining = sum(remaining.values())
    if total_remaining <= 0:
        return remaining
    return {feature: weight / total_remaining for feature, weight in remaining.items()}


def equalize_weights(
    weights: Mapping[str, float], missing_features: Iterable[str]
) -> dict[str, float]:
    """결측을 뺀 나머지 축에 가중치를 **균등하게** 나눈다(구 단위 요청, D-119 후속).

    `redistribute_weights()`와 나누는 이유는 **축마다 값이 몇 갈래로 나오는지가
    다르기 때문이다.** 날씨는 판정표 조회라 한 날씨 조건에서 실내·실외·모름 3갈래
    밖에 안 나오고, 남은 운영시간은 120분만 넘으면 전부 1.0이다. 반면 취향은
    유사도 연속값이라 후보 30곳에서 14~16갈래로 갈린다.

    비례 재분배는 그 가장 성긴 축에 가장 큰 몫을 남긴다 — 거리가 빠진 구 단위에서
    날씨 0.41 / 영업시간 0.41 / 취향 0.18이 되어, 3갈래 축이 1차 정렬을 하고
    14갈래 축은 그 덩어리 안에서만 미세조정한다. 실측에서 종로구 "야경 보기 좋은
    곳" 질의의 2위가 서울특별시교육청이었다(실내라 비 오는 날 날씨 만점).
    균등(1/3씩)으로 옮기면 북악하늘길·창덕궁 달빛기행·인왕산이 올라온다.

    **반경 검색에는 쓰지 않는다.** 거리 축이 살아 있으면 기본 3축 0.35/0.35/0.15가
    그대로고, 그 비율은 `_OPTIONAL_WEIGHT` 주석대로 기존 세트에서 읽어낸 값이다.
    축이 2개만 남는 경우(취향이 없는 구 단위 요청)에는 비례 재분배도 0.5/0.5라
    이 함수와 결과가 같다 — 갈리는 것은 축이 3개 이상일 때뿐이다.

    딸린 대가: 영업시간을 모르는 후보(그 축이 결측)와 아는 후보의 점수 격차가
    0.1235에서 0.1667로 벌어진다. 방향은 맞지만(미확인은 문이 닫혀 있을 수도
    있다) 의도해서 고른 값이 아니라 균등 배분의 부산물이고, 영업시간 적재가
    끝나면 줄어든다.
    """
    missing = set(missing_features)
    remaining = [feature for feature in weights if feature not in missing]
    if not remaining:
        return {}
    share = 1.0 / len(remaining)
    return {feature: share for feature in remaining}


def _is_closed(candidate: ScoringCandidate, now: datetime) -> bool:
    if candidate.operating_hours is None:
        return False  # 운영시간 미확인은 폐점이 아니다.
    return _remaining_minutes(now, candidate.operating_hours) is None


def prepare_candidates(
    candidates: Sequence[ScoringCandidate],
    *,
    now: datetime,
    shown_place_ids: Iterable[str] = (),
    rejected_place_ids: Iterable[str] = (),
    ignore_operating_hours: bool = False,
) -> PrepareResult:
    """하드 필터를 적용하고 통과 후보의 운영시간 Feature를 준비한다.

    제외 사유는 사용자 이력을 폐점보다 우선한다. 이미 노출되거나 거절된 후보가
    현재 폐점이기도 하더라도 폐점 때문에 제외된 것으로 집계하지 않는 기존
    ``score_candidates()`` 동작을 보존하기 위해서다.
    """
    shown = frozenset(shown_place_ids)
    rejected = frozenset(rejected_place_ids)
    eligible: list[PreparedCandidate] = []
    excluded: list[ExcludedCandidate] = []

    for candidate in candidates:
        if candidate.place_id in shown:
            excluded.append(ExcludedCandidate(candidate, ExclusionReason.ALREADY_SHOWN))
            continue
        if candidate.place_id in rejected:
            excluded.append(ExcludedCandidate(candidate, ExclusionReason.REJECTED))
            continue

        remaining_minutes = (
            _remaining_minutes(now, candidate.operating_hours)
            if candidate.operating_hours is not None
            else None
        )
        is_closed = candidate.operating_hours is not None and remaining_minutes is None
        if is_closed and not ignore_operating_hours:
            excluded.append(ExcludedCandidate(candidate, ExclusionReason.CLOSED))
            continue

        is_unverified = candidate.operating_hours is None or is_closed
        operating_warnings = (
            ((_CLOSED_NOW_WARNING,) if is_closed else (_UNVERIFIED_WARNING,))
            if is_unverified
            else ()
        )
        # **무장애 안내를 앞에 둔다.** 표시 측은 첫 줄만 보여주는데(PlaceCard.tsx),
        # 운영시간은 "가서 닫혀 있을 수 있다"이고 무장애는 "가도 못 들어가는 데가
        # 있다"라 무게가 다르다. 뒤에 두면 운영시간 미확인 후보에서 무장애 안내가
        # 통째로 가려진다.
        # 주기 휴무 안내는 폐점·미확인 안내와 겹치지 않을 때만 붙인다. 이미
        # "방문 전에 확인해주세요"라고 말한 후보에 같은 말을 두 번 하지 않는다.
        uncertain_warnings = (
            (_UNCERTAIN_CLOSURE_WARNING,)
            if candidate.operating_hours is not None
            and candidate.operating_hours.has_uncertain_closure
            and not is_unverified
            else ()
        )
        warnings = _accessibility_warnings(candidate) + uncertain_warnings + operating_warnings
        eligible.append(
            PreparedCandidate(
                candidate=candidate,
                remaining_minutes=remaining_minutes,
                is_unverified=is_unverified,
                warnings=warnings,
            )
        )

    return PrepareResult(
        eligible_candidates=tuple(eligible),
        excluded_candidates=tuple(excluded),
        input_count=len(candidates),
    )


def _consistent_routes(
    candidates: Sequence[PreparedCandidate],
    travel_routes: Sequence[TravelRoute],
) -> dict[str, TravelRoute]:
    """한 순위 안에서는 모든 후보를 같은 자로 재도록 실측을 전부 쓰거나 전부 버린다.

    실측 도보 시간과 직선거리 점수는 낙관도가 다르다 — 실거리는 직선거리보다 항상
    크거나 같아서(우회·신호 대기), 두 기준이 한 순위표에 섞이면 **실측이 있는
    후보만 구조적으로 손해**를 본다. 실제로 더 가까운 장소가 실측이 있다는 이유로
    실측 없는 먼 장소보다 아래로 내려가는 역전이 확인됐다.

    그래서 후보 중 하나라도 실측이 없으면 전부 직선거리로 채점한다. 카카오 조회가
    일부 목적지만 실패한 경우(PARTIAL)가 여기 해당한다. 1건 실패로 전체가
    직선거리로 내려가는 손해는 있지만, 일관되게 덜 정확한 편이 기준이 뒤섞여
    순위가 뒤집히는 것보다 낫다.
    """
    routes_by_place_id = {route.place_id: route for route in travel_routes}
    applied = {
        prepared.candidate.place_id: _applied_travel_route(
            routes_by_place_id.get(prepared.candidate.place_id)
        )
        for prepared in candidates
    }
    if any(route is None for route in applied.values()):
        return {}
    return {place_id: route for place_id, route in applied.items() if route is not None}


def score_prepared_candidates(
    candidates: Sequence[PreparedCandidate],
    *,
    weather_condition: WeatherCondition | None,
    max_distance_km: float,
    # 안 넘기면 요청에서 켜진 선택 Feature로 조립한다(`build_weights`).
    #
    # **`district_scoped=True`와 함께 넘기면 여기 적은 비율은 버려진다** —
    # 그 모드는 남은 축을 균등하게 나누므로(`equalize_weights`) 키 목록만 쓰인다.
    # 지금 이 인자를 넘기는 프로덕션 호출부는 없고 테스트뿐이다.
    weights: Mapping[str, float] | None = None,
    weather_reason: WeatherReason = None,
    requested_environment: str | None = None,
    # A가 조회해 넘긴 실측 경로. 해당 후보가 없거나 조회에 실패했으면
    # 직선거리로 돌아간다(`_proximity_score()`).
    travel_routes: Sequence[TravelRoute] = (),
    # 거리 점수의 시간 예산을 만들 속도(km/분) — **이 요청이 검색 반경을 만들 때
    # 쓴 속도**여야 한다(`to_search_radius_speed_km_per_min()`). 측정한 이동수단의
    # 속도가 아니다(D-118, `_travel_minutes_budget()` 참고). 기본값은 도보로,
    # 넘기지 않는 호출부는 기본 반경(도보 기준) 요청과 같은 자를 쓴다.
    travel_budget_speed_km_per_min: float = WALKING_SPEED_KM_PER_MINUTE,
    # 후보를 구 하나에서 통째로 모은 요청인가(C의 RecommendationContext.district_scope,
    # D-119). True면 거리 Feature를 **결측으로 다룬다** — 아래 루프 참고.
    district_scoped: bool = False,
    # 취향 근거 검색 결과(place_id = content_id 기준). 비어 있으면 사용자가
    # 취향을 말하지 않았거나 검색이 실패한 것으로 보고 taste Feature를 아예
    # 쓰지 않는다 — 후보 단위가 아니라 **요청 단위** 판단이다.
    taste_matches: Mapping[str, PlaceEvidenceMatch] | None = None,
    # 취향 태그 일치 결과(place_id = content_id 기준). 임베딩을 대체하지 않고
    # `combine_taste_scores()`의 가산 신호로 함께 쓴다. 태그가 없는 후보도
    # 임베딩 근거가 있으면 그대로 점수를 받는다.
    taste_tag_matches: Mapping[str, PreferenceTagMatch] | None = None,
) -> ScoringResult:
    """하드 필터를 통과한 후보에 가중치 점수를 적용해 정렬한다.

    1. 후보별로 날씨·남은 운영시간 결측 여부를 확인해 기본 가중치 또는
       재분배 가중치를 적용 (두 Feature 모두 결측일 수도 있음)
    2. Feature별 점수 계산 후 가중합 (날씨 또는 요청 환경, 남은 운영시간, 거리)
    3. score 내림차순 → distance_km 오름차순 → place_id 오름차순으로 정렬

    `requested_environment`가 indoor/outdoor면 날씨 Feature 자리를 환경 적합도가
    대신한다 — 사용자가 실내/실외를 직접 말했는데 날씨 사실이 순위를 뒤집는
    문제(맑은 날 GOOD/indoor=0.70)를 막기 위해서다. 가중치 숫자는 그대로고 키만
    바뀐다. 날씨를 함께 언급한 경우(weather_intent=AVOID/ENJOY)는 호출부가 이
    값을 넘기지 않아 기존 날씨 판정을 그대로 쓴다.
    """
    environment_driven = uses_environment_feature(requested_environment)
    routes_by_place_id = _consistent_routes(candidates, travel_routes)
    # 취향 Feature는 요청 단위로 켜고 끈다. 켜지면 모든 후보가 이 Feature를
    # 가지므로 한 순위 안에서 가중치 세트가 갈리지 않는다.
    taste_by_place_id = dict(taste_matches or {})
    taste_tag_by_place_id = dict(taste_tag_matches or {})
    uses_taste_tags = taste_tag_matches is not None
    uses_embedding_taste = taste_matches is not None
    uses_taste = uses_taste_tags or uses_embedding_taste
    default_weights = build_weights(("taste",) if uses_taste else ())
    base_weights = weights_for_environment(
        dict(weights) if weights is not None else dict(default_weights),
        requested_environment,
    )
    scored: list[
        tuple[
            ScoringCandidate,
            float,
            dict[str, float | None],
            dict[str, float],
            bool,
            float | None,
            tuple[str, ...],
        ]
    ] = []

    for prepared in candidates:
        candidate = prepared.candidate
        missing_features: list[str] = []

        # 날씨와 요청 환경은 한 실행에서 같은 자리를 나눠 쓴다. 요청 환경은
        # 후보마다 항상 판정할 수 있어 결측이 없다(unknown도 표에 값이 있다).
        primary_feature = ENVIRONMENT_FEATURE if environment_driven else WEATHER_FEATURE
        primary_score: float | None
        if environment_driven:
            assert requested_environment is not None
            primary_score = _environment_fit_score(candidate, requested_environment)
        elif weather_condition is None:
            primary_score = None
            missing_features.append(primary_feature)
        else:
            primary_score = _weather_fit_score(candidate, weather_condition)

        remaining_minutes = prepared.remaining_minutes
        remaining_time_score: float | None
        if remaining_minutes is None:
            remaining_time_score = None
            missing_features.append("remaining_operating_time")
        else:
            remaining_time_score = _remaining_time_score(remaining_minutes)

        # 구 단위 요청은 거리 Feature를 쓰지 않는다(D-119 후속).
        #
        # **후보를 모은 방식이 반경 검색이 아니기 때문이다.** C가 구 전량에서
        # 격자로 흩어 뽑으므로 기준점이 없고, 그 요청의 `location`은 구 이름을 푼
        # 대표점이라 후보 수집에도 정렬에도 쓰이지 않았다 — 그 좌표와의 거리는
        # 사용자가 말한 것과 아무 관계가 없다.
        #
        # 분모도 맞지 않는다. 실측(2026-09-03, 좌표 정상인 행 기준)으로 구 중심에서
        # 기본 반경 2km를 넘는 장소가 강남 16.6% · 서초 26.8% · 송파 33.3% ·
        # 용산 34.2%다. 그 후보들은 전부 0점이 되어 2.1km와 8km가 구분되지 않는다.
        #
        # **"축이 없다"가 아니라 "값이 없다"로 다룬다.** 요청 전체가 함께 빠지므로
        # 한 순위 안에서 자를 두 개 쓰는 문제가 생기지 않는다.
        #
        # 다만 나누는 방식은 날씨 조회 실패와 다르다 — 남은 축에 **균등하게**
        # 나눈다(`equalize_weights`, 1.9.0). 비례 재분배가 가장 성긴 축(날씨,
        # 3갈래)에 가장 큰 몫을 남기는 문제를 그 함수 주석에 적었다.
        #
        # 사용자 위치를 아는 요청에서는 거리 축이 노이즈가 아니다 — 그래도
        # 구 단위 요청은 "이 구 전체에서 골라 달라"는 뜻이라 가까운 순을 우선하지
        # 않기로 팀에서 정했다. 카드의 거리 표시(`distance_km`)는 그대로 남는다 —
        # 점수에서 뺀 것이지 정보를 감춘 것이 아니다.
        if district_scoped:
            missing_features.append("distance")

        weights_used = (
            equalize_weights(base_weights, missing_features)
            if district_scoped
            else redistribute_weights(base_weights, missing_features)
            if missing_features
            else dict(base_weights)
        )

        feature_scores: dict[str, float | None] = {
            primary_feature: primary_score,
            "remaining_operating_time": remaining_time_score,
            # 실측 이동시간 우선, 없으면 직선거리. 구 단위 요청에서는 None이다 —
            # 점수를 안 쓰는데 값만 채우면 개발자 패널과 근거 문장이 "거리 때문에
            # 뽑혔다"고 말하게 된다(build_explanations가 score로 고른다).
            "distance": (
                None
                if district_scoped
                else _proximity_score(
                    candidate,
                    routes_by_place_id.get(candidate.place_id),
                    max_distance_km,
                    travel_budget_speed_km_per_min,
                )
            ),
        }
        taste_match = (
            taste_by_place_id.get(candidate.place_id) if uses_embedding_taste else None
        )
        taste_tag_match = (
            taste_tag_by_place_id.get(candidate.place_id) if uses_taste_tags else None
        )
        if uses_taste:
            # 둘 중 한쪽 근거가 없어도 다른 쪽은 그대로 살아 있다. 모두 없으면
            # 0.0이지 결측이 아니다 — 후보마다 가중치 세트가 달라지지 않게 한다.
            feature_scores["taste"] = combine_taste_scores(
                _taste_score(taste_match) if uses_embedding_taste else None,
                _taste_score_from_tags(taste_tag_match) if uses_taste_tags else None,
            )

        score = sum(
            feature_scores[feature] * weight  # type: ignore[operator]
            for feature, weight in weights_used.items()
        )

        scored.append(
            (
                candidate,
                score,
                feature_scores,
                weights_used,
                prepared.is_unverified,
                remaining_minutes,
                prepared.warnings,
            )
        )

    scored.sort(key=lambda entry: (-entry[1], entry[0].distance_km, entry[0].place_id))

    ranked = tuple(
        RankedCandidate(
            place_id=candidate.place_id,
            name=candidate.name,
            category=candidate.category,
            rank=index + 1,
            score=round(score, 4),
            feature_scores=feature_scores,
            weights_used=weights_used,
            is_unverified=is_unverified,
            warnings=warnings,
            distance_km=candidate.distance_km,
            remaining_minutes=remaining_minutes,
            weather_condition=weather_condition,
            weather_reason=weather_reason,
            environment_type=candidate.environment_type,
            travel_distance_m=_travel_field(
                routes_by_place_id.get(candidate.place_id), "distance_m"
            ),
            travel_duration_seconds=_travel_field(
                routes_by_place_id.get(candidate.place_id), "duration_seconds"
            ),
            travel_mode=_travel_mode_of(routes_by_place_id.get(candidate.place_id)),
            taste_evidence_text=_taste_evidence_text(taste_by_place_id.get(candidate.place_id)),
            taste_evidence=_taste_evidence_snippets(taste_by_place_id.get(candidate.place_id)),
            taste_tag_label=_taste_tag_label(taste_tag_by_place_id.get(candidate.place_id)),
            taste_tag_documents=_taste_tag_documents(taste_tag_by_place_id.get(candidate.place_id)),
            taste_tag_score=(
                _taste_score_from_tags(taste_tag_by_place_id.get(candidate.place_id))
                if uses_taste_tags
                else None
            ),
            taste_tag_details=(
                taste_tag_by_place_id.get(candidate.place_id).details
                if uses_taste_tags and taste_tag_by_place_id.get(candidate.place_id) is not None
                else ()
            ),
            taste_embedding_similarity=(
                _taste_similarity(taste_by_place_id.get(candidate.place_id))
                if uses_embedding_taste
                else None
            ),
            taste_embedding_score=(
                _taste_score(taste_by_place_id.get(candidate.place_id))
                if uses_embedding_taste
                else None
            ),
            taste_combined_score=(feature_scores.get("taste") if uses_taste else None),
        )
        for index, (
            candidate,
            score,
            feature_scores,
            weights_used,
            is_unverified,
            remaining_minutes,
            warnings,
        ) in enumerate(scored)
    )

    return ScoringResult(
        ranked=ranked,
        excluded_place_ids=(),
        excluded_closed_place_ids=(),
    )


def score_candidates(
    candidates: Sequence[ScoringCandidate],
    *,
    now: datetime,
    weather_condition: WeatherCondition | None,
    max_distance_km: float,
    shown_place_ids: Iterable[str] = (),
    rejected_place_ids: Iterable[str] = (),
    weights: Mapping[str, float] | None = None,
    weather_reason: WeatherReason = None,
    # True면 폐점 후보를 제외하지 않고 그대로 채점한다 — "운영중이 아닌 곳도
    # 볼래요"(no_data_closed 되묻기) 해소 시에만 호출부가 켠다. 기본은 False로,
    # 기존 하드 필터 동작을 그대로 유지한다.
    ignore_operating_hours: bool = False,
    # 사용자가 명시한 실내/실외. indoor/outdoor면 날씨 대신 이 조건으로 같은
    # 자리의 Feature를 채점한다(호출부가 날씨 언급이 없을 때만 넘긴다).
    requested_environment: str | None = None,
    # A가 조회한 실측 경로. 분리 진입점(score_prepared_candidates)과 같은 규칙을
    # 따른다 — 후보 중 하나라도 실측이 없으면 전부 직선거리로 채점한다.
    travel_routes: Sequence[TravelRoute] = (),
    # 거리 점수의 시간 예산 속도. 분리 진입점과 같은 의미다(D-118).
    travel_budget_speed_km_per_min: float = WALKING_SPEED_KM_PER_MINUTE,
) -> ScoringResult:
    """기존 하드 필터와 점수 계산을 연속 실행하는 호환 진입점."""
    prepared_result = prepare_candidates(
        candidates,
        now=now,
        shown_place_ids=shown_place_ids,
        rejected_place_ids=rejected_place_ids,
        ignore_operating_hours=ignore_operating_hours,
    )
    scoring_result = score_prepared_candidates(
        prepared_result.eligible_candidates,
        weather_condition=weather_condition,
        max_distance_km=max_distance_km,
        travel_routes=travel_routes,
        travel_budget_speed_km_per_min=travel_budget_speed_km_per_min,
        weights=weights,
        weather_reason=weather_reason,
        requested_environment=requested_environment,
    )
    return ScoringResult(
        ranked=scoring_result.ranked,
        excluded_place_ids=prepared_result.excluded_place_ids,
        excluded_closed_place_ids=tuple(
            candidate.place_id
            for candidate in prepared_result.excluded_candidates
            if candidate.reason is ExclusionReason.CLOSED
        ),
    )

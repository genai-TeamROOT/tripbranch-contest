# 추천 Evidence·평가 Fixture 설계 (D-02)

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-24 |
| 관련 코드 | `backend/app/domain/evidence.py`, `backend/tests/fixtures/scoring_fixture_v1.py`, `backend/tests/test_scoring_fixture.py` |

이 문서는 Scoring v1(D-008, [`recommendation-scoring.md`](./recommendation-scoring.md))의
출력(`RankedCandidate`)을 사용자가 이해할 수 있는 근거로 변환하고, 반복 검증
가능한 고정 평가 Fixture를 구축하는 D-02 작업을 정리한다.

---

## 1. 범위

D-02가 다루는 것:

- `RankedCandidate`의 `feature_scores`/`weights_used`를 Feature별 기여도로
  재구성하는 Evidence 모델 (`RecommendationEvidence`)
- 날씨·남은 운영시간 결측 조합, 폐점·이전 노출·거절 제외를 포함하는 고정
  평가 Fixture v1
- 동일 입력에서 동일 순서가 나오는지 확인하는 결정적 정렬 검증
- D-03에서 그대로 가져다 쓸 수 있는 Payload 형태 정리(문서화 수준)

D-02가 다루지 않는 것 (`범위 제외`, task.txt 원문 기준):

- `/api/recommendations` 라우트 연결
- Provider 운영시간 원문 정규화 (`backend/app/domain/operating_hours.py`가
  이미 별도로 구현됨 — D-02는 이를 `ScoringCandidate.operating_hours`로
  매핑하지 않고, 기존처럼 단순 `OperatingHours(open_time, close_time)`를
  그대로 사용한다. 두 모델을 잇는 매퍼는 이후 작업의 몫이다)
- 카테고리 하드 필터 구현
- 혼잡도·신뢰도 등 신규 Feature 추가
- 사용자용 자연어 설명을 LLM으로 생성하는 기능 (`reason` 문자열은 만들지 않음)

## 2. Evidence 모델

`backend/app/domain/evidence.py`

```python
@dataclass(frozen=True)
class FeatureContribution:
    feature: str
    score: float | None
    weight: float | None
    contribution: float | None  # score * weight; 하나라도 결측이면 None

@dataclass(frozen=True)
class RecommendationEvidence:
    place_id: str
    name: str
    category: str
    rank: int
    score: float
    contributions: tuple[FeatureContribution, ...]
    is_unverified: bool
    warnings: tuple[str, ...]

def build_evidence(candidate: RankedCandidate) -> RecommendationEvidence: ...
def build_evidence_list(result: ScoringResult) -> tuple[RecommendationEvidence, ...]: ...
```

- `contributions`는 `weather` → `remaining_operating_time` → `distance` 고정
  순서로 3개를 항상 포함한다 (결측이어도 `score=None`/`weight=None`인 항목으로
  남긴다 — 어떤 Feature가 왜 반영되지 않았는지 그대로 드러내기 위함).
- `contribution = score * weight`는 두 값이 모두 있을 때만 계산한다. 결측
  Feature는 애초에 `weights_used`에 키 자체가 없으므로(§5.2,
  [recommendation-scoring.md](./recommendation-scoring.md)) `weight=None`이
  되고, 자동으로 `contribution=None`이 된다.
- `RankedCandidate` → `RecommendationEvidence`는 순수 변환이며 부가 판단(예:
  "가장 중요한 이유가 무엇인지" 요약)을 하지 않는다. 자연어 문장 생성은
  Response Generator(LLM) 몫이며, 이 모델은 그 입력 재료만 정리한다.

## 3. Fixture v1

`backend/tests/fixtures/scoring_fixture_v1.py`

```python
@dataclass(frozen=True)
class ScoringFixtureCase:
    name: str
    purpose: str
    candidates: tuple[ScoringCandidate, ...]
    now: datetime
    weather_condition: WeatherCondition | None
    max_distance_km: float
    shown_place_ids: tuple[str, ...] = ()
    rejected_place_ids: tuple[str, ...] = ()
    expected_ranked_place_ids: tuple[str, ...] = ()
    expected_excluded_place_ids: frozenset[str] = frozenset()
    expected_unverified_place_ids: frozenset[str] = frozenset()
```

`SCORING_FIXTURE_V1`은 아래 7개 시나리오를 담는다.

| 이름 | 검증 목적 |
| --- | --- |
| `baseline_all_features_present` | 날씨·남은 운영시간·거리 모두 결측 없는 기본 정렬 |
| `weather_missing` | 날씨만 결측 → 남은 운영시간·거리로 재분배 |
| `operating_hours_unknown` | 운영시간 미확인 후보 → 날씨·거리로 재분배, `is_unverified` |
| `weather_and_operating_hours_both_missing` | 둘 다 결측 → 거리 100% 가중치 |
| `closed_place_is_excluded` | 마감 후보 하드 필터 제외 |
| `shown_and_rejected_are_excluded` | 이전 노출·거절 ID 제외 |
| `tie_break_uses_distance_then_place_id` | 동점 시 거리→place_id 정렬 |

Fixture는 Python 데이터로 두었다 (JSON 등 별도 포맷 대신). 이유:

1. `ScoringCandidate`/`OperatingHours`/`WeatherCondition`이 이미 타입이 있는
   dataclass/Enum이라, 직렬화 포맷을 새로 정의하면 왕복 변환 코드가 추가로
   필요해진다.
2. `test_scoring_fixture.py`와 향후 다른 평가 스크립트가 그대로 import해서
   재사용할 수 있다.

## 4. Fixture 기반 테스트

`backend/tests/test_scoring_fixture.py`가 `SCORING_FIXTURE_V1`을 순회하며
3가지를 검증한다 (case별로 parametrize, 총 21개 테스트).

1. **`test_fixture_ranking_and_exclusion`**: `expected_ranked_place_ids`,
   `expected_excluded_place_ids`, `expected_unverified_place_ids`(및 그에
   따른 `warnings` 유무)가 모두 일치하는지 확인
2. **`test_fixture_is_deterministic`**: 동일 케이스를 두 번 실행해 `(place_id,
   rank, score)` 목록과 `excluded_place_ids`가 완전히 같은지 확인 — "동일
   입력에서 동일 순서" 완료 기준을 직접 코드로 검증
3. **`test_fixture_evidence_matches_ranked_candidates`**: `build_evidence_list()`
   결과가 `RankedCandidate`와 place_id 순서·점수·경고가 일치하고,
   `contributions`의 `score`/`weight`/`contribution`이 원본
   `feature_scores`/`weights_used`와 정확히 대응하는지 확인

## 5. 평가 기준

D-02의 완료 기준(task.txt)을 아래처럼 기계적으로 확인한다.

| 완료 기준 | 확인 방법 |
| --- | --- |
| 추천 결과마다 주요 점수 근거를 확인할 수 있다 | `RecommendationEvidence.contributions`에 Feature별 score/weight/contribution 노출 |
| 후보별 weights_used와 결측 Feature 처리가 근거에 반영된다 | `test_fixture_evidence_matches_ranked_candidates`가 `weights_used`/`feature_scores` 대응을 직접 대조 |
| 운영시간 미확인 후보에 경고가 포함된다 | `operating_hours_unknown`/`weather_and_operating_hours_both_missing` 케이스에서 `warnings` 검증 |
| 폐점·노출·거절 장소가 Fixture에서 정상 제외된다 | `closed_place_is_excluded`/`shown_and_rejected_are_excluded` 케이스 |
| 동일 Fixture를 반복 실행했을 때 추천 순서가 동일하다 | `test_fixture_is_deterministic` |
| D-03에서 API 응답으로 연결할 수 있는 형태가 준비된다 | §6 Payload 매핑 |

## 6. D-03 연결용 Payload 매핑

`docs/api-contracts.md`에 이미 초안된 `RecommendationResult`와
`RecommendationEvidence`의 대응은 다음과 같다.

| `RecommendationResult` 필드 | 현재 준비 상태 |
| --- | --- |
| `place_id`, `name` | `RecommendationEvidence`에서 그대로 사용 가능 |
| `score`, `rank` | `RecommendationEvidence.score`/`rank`에서 그대로 사용 가능 |
| `warnings` | `RecommendationEvidence.warnings`에서 그대로 사용 가능 |
| `feature_scores` | `contributions`를 `{feature: score}` 형태로 평탄화하면 됨 |
| `reason` | 아직 준비 안 됨 — Response Generator(LLM) 영역, D-02 범위 밖 |
| `snapshot` | 아직 준비 안 됨 — Persistence 설계(D-012) 영역, D-02 범위 밖 |

`RecommendationEvidence`는 모든 필드가 dataclass/tuple/기본 타입이라 별도
직렬화 계층 없이 JSON으로 바로 변환 가능하다. D-03에서는 이 값을 그대로
받아 `reason`/`snapshot`만 채우면 된다.

## 7. 알려진 제한사항

- **자연어 근거 없음**: `contributions`는 숫자 근거만 제공하며, "왜 이
  장소를 추천했는지" 문장은 이 작업의 산출물이 아니다.
- **카테고리 하드 필터 미구현**: Fixture의 모든 후보는 이미 카테고리 필터를
  통과했다고 가정한다. 실제 카테고리 필터가 붙기 전까지 원치 않는 카테고리가
  섞여 나올 수 있다는 한계는 [recommendation-scoring.md](./recommendation-scoring.md)
  §8과 동일하다.
- **`OperatingSchedule` ↔ `ScoringCandidate.operating_hours` 매퍼 없음**:
  `backend/app/domain/operating_hours.py`의 `normalize_operating_schedule()`는
  요일별·월별 규칙까지 표현하는 `OperatingSchedule`을 만들지만, Scoring은
  여전히 단순 `OperatingHours(open_time, close_time)` 한 쌍만 받는다. 여러
  요일 규칙 중 어떤 것을 `open_time`/`close_time`으로 골라야 하는지는 이후
  작업에서 결정해야 한다.
- **자정을 넘기는 운영시간, 기준 시각(`now`) 출처**: `recommendation-scoring.md`
  §1과 동일한 제한이 그대로 적용된다.
- **Fixture 규모**: 7개 시나리오는 핵심 결측/제외/tie-break 조합만 다루며,
  실제 Tool 데이터의 다양성(복수 요일 규칙, 부분 파싱 등)을 전부 반영하지
  않는다.

## 8. 관련 문서

- [`docs/decision-log.md`](../decision-log.md) — D-008, 신규 결정 항목
- [추천 점수 설계](./recommendation-scoring.md) — Scoring v1 전체 설계
- [`docs/api-contracts.md`](../api-contracts.md) — `RecommendationResult` 목표 계약

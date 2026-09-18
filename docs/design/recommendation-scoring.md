# 추천 점수 설계 (Scoring v1)

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1 |
| 상태 | v1 확정, §4.4·§5.1·§5.2 혼잡도(D-040) 확정 반영. §3에 `ignore_operating_hours`/정기 휴무(`is_regular_closure`) 반영(2026-08-13) |
| 최종 수정 | 2026-08-13 |
| 관련 코드 | `backend/app/domain/models.py::ScoringCandidate`, `backend/app/domain/scoring.py` |

이 문서는 [`docs/decision-log.md`](../decision-log.md)의 D-008(하드 필터 + 가중치 점수
조합)을 구현 가능한 수준으로 구체화한다. C-01(Tool 계약)이 아직 확정되지 않았으므로
Tool의 실제 출력 대신 `ScoringCandidate` 모델에 맞춘 응답 샘플/Stub 데이터로 작업한다.

---

## 1. 범위

Scoring v1이 다루는 것:

- Provider/Tool 독립적인 `Candidate` 공통 모델 (`ScoringCandidate`)
- 날씨, 남은 운영시간, 거리 3개 Feature
- 운영종료(최종 하드 필터)/운영시간 미확인/이전 추천·거절 장소의 처리 기준
- 기본 가중치와 날씨·남은 운영시간 결측 시 재분배 가중치
- 점수 기준 정렬(Ranking) 출력 구조

Scoring v1이 다루지 않는 것 (`TBD`):

- 카테고리 매치 Feature — 1차 하드 필터(place_type/place_tag)가 이미 처리한다고
  보고 Scoring v1의 가중치 계산에서는 제외하기로 결정함 (§4 참고)
- 혼잡도(Concentration) Feature, 분위기/조용함 근거(Evidence confidence)
- 실제 이동시간 기반 거리 계산 (현재는 직선거리 전제)
- 예산·동행·필수 편의시설 하드 필터 (`INT-01`의 Conditions 필드 중 일부)
- 운영시간 원문("0900~1800" 등)을 `OperatingHours`로 정규화하는 파서 자체
  (`PLC-03`과 동일 범위, Tool/Provider 계층 소관)
- 자정을 넘기는 운영시간(예: 22:00~02:00)
- 기준 시각(`now`)의 실제 출처 — 즉시 방문/방문 예정 시각 선택은 D-022 연계 `TBD`
- 자연어 추천 이유 생성 (Response Generator 영역)

## 2. Candidate Model v1

`backend/app/domain/models.py::ScoringCandidate`

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `place_id` | `str` | 장소 식별자 (Provider의 `PlaceCandidate.place_id`와 동일 값 사용) |
| `name` | `str` | 장소명 |
| `category` | `str` | 내부 카테고리 (`PlaceCandidate.category`와 동일 체계); 표시용 메타데이터로만 사용하고 점수 계산에는 쓰지 않음 |
| `environment_type` | `str` | `"indoor"` \| `"outdoor"` \| `"unknown"` |
| `distance_km` | `float` | 랭킹 기준점(origin)으로부터의 거리 (v1은 직선거리 전제). 기준점은 사용자 위치이고, 사용자 위치를 모르는 요청에서만 검색 기준점으로 돌아간다(D-067) |
| `operating_hours` | `OperatingHours \| None` | 당일 개장~마감 시각. `None`이면 운영시간 미확인 |
| `raw_source` | `str` | 후보를 채운 Tool/Provider 식별값 (기본 `"unknown"`) |

`OperatingHours`는 `open_time`/`close_time`(`datetime.time`)에 더해
`is_regular_closure: bool = False` 필드를 가지며, `open_time <= close_time`인
당일 운영만 다룬다(§1 범위 참고). `is_regular_closure`는 "이 구간은 평소
운영시간이지만 방문일은 정기 휴무"라는 뜻으로, 시각을 `00:00~00:00`으로 지워
운영종료를 표시하던 이전 방식(그 시각 자체가 추천 카드에 그대로 노출되는
부작용이 있었다)을 대체한다 — 시각과 휴무 여부를 한 객체에 같이 둬서 실제
운영시간 표시와 운영종료 판정이 어긋나지 않게 한다. 운영 유무(운영종료 여부)
자체는 이 필드만으로 결정되지 않고, Scoring이 실행 시점에 받는 기준 시각
`now`와 `open_time`/`close_time`/`is_regular_closure`를 함께 비교해 직접
판정한다(§3).

`PlaceCandidate`(Provider 산출물)와 별도 모델로 둔 이유는 두 가지다.

1. `PlaceCandidate`는 아직 운영 상태·거리·실내외 구분을 갖지 않는다. 이 값들은
   여러 Tool(`getPlaceDetails`, `estimateTravelTime`, `getCurrentWeather` 등)의
   결과를 조합해야 채워지므로, Provider 하나의 출력 모델에 억지로 넣지 않는다.
2. C-01 Tool 계약이 확정되기 전까지 Scoring이 실제 Tool 구현에 결합되지 않도록,
   Scoring의 입력 경계를 이 모델 하나로 고정한다. Tool이 완성되면
   "Tool 출력 → `ScoringCandidate`" 매퍼만 추가하면 되고 Scoring 내부 로직은
   바뀌지 않는다.

### 김진형의 Tool 결과와 연결

C-01의 Tool 출력 초안이 아직 없으므로, 현재는 아래 대응을 임시 계약으로 가정한다.
실제 Tool 계약이 나오면 이 표를 갱신한다.

| `ScoringCandidate` 필드 | 예상 Tool 출처 |
| --- | --- |
| `place_id`, `name`, `category` | `searchNearbyPlaces` (Place Provider 기반) |
| `distance_km` | `estimateTravelTime` 또는 랭킹 기준점 좌표로 계산한 직선거리 |
| `operating_hours` | `getPlaceDetails`의 운영시간 원문을 `OperatingHours`(개장~마감)로 정규화한 결과 |
| `environment_type` | `category` → 실내외 매핑표 (§4.2) |

## 3. 제외 규칙 (하드 필터)

Scoring 실행 시점의 기준 시각 `now`를 입력받아, 다음 조건으로 후보를 제외한다.
제외된 항목은 점수를 계산하지 않는다.

| 조건 | 처리 | 이유 |
| --- | --- | --- |
| `operating_hours`가 있고 (`now`가 `[open_time, close_time)` 밖 또는 `is_regular_closure=True`) | 제외(운영종료) — `ignore_operating_hours=True`면 제외하지 않음 | 운영종료 장소는 방문 불가능하므로 원칙적으로 추천 후보가 아님 |
| `place_id in shown_place_ids` | 제외 | 이미 이번 세션에서 노출한 장소 |
| `place_id in rejected_place_ids` | 제외 | 사용자가 명시적으로 거절한 장소 |

운영 유무(운영종료 여부)는 별도 boolean 필드 하나로 저장되지 않고, Scoring이
`now`와 `operating_hours`(개장~마감 시각 + `is_regular_closure`)를 비교해
**최종 하드 필터로 직접 판정**한다(`_is_closed()`). 정기 휴무일은 `now`가
평소 개장~마감 구간 안에 있어도 `is_regular_closure=True`면 운영종료로
판정한다 — `hours`가 그날 실제로 여는 시간이 아니라 평소 구간이기 때문이다.
`operating_hours is None`(운영시간 미확인)은 **제외하지 않는다.** 운영종료와
미확인은 다른 상태이며, 미확인 후보는 점수를 계산하되 `is_unverified=True`와
경고 문구를 함께 반환한다. 이는 현재 `services/recommendations.py`가
`operating_hours` 유무로 `recommendations`/`unverified_recommendations`를
나누는 방식과 같은 원칙이다.

**`ignore_operating_hours`(2026-08-13 추가)**: `score_candidates()`의 키워드
인자로, 기본값은 `False`(기존 동작 그대로 운영종료 후보 제외)다. `True`면
운영종료 후보를 제외하지 않고 그대로 채점에 포함한다 — 검색 결과가 전부
운영종료 후보뿐이라 0건으로 응답한 턴에서 "운영 중이 아닌 곳도 볼게요"
되묻기(`no_data_closed`, [clarification-options.md](./clarification-options.md))를
사용자가 선택했을 때만 호출부(`agent_runtime.py`)가 켠다. `shown_place_ids`/
`rejected_place_ids` 제외는 이 플래그와 무관하게 항상 적용된다.

카테고리(place_type/place_tag) 일치 여부도 하드 필터의 영역이다. 예를 들어
"카페 아니면 공원"처럼 place_type이 여러 개 허용된 경우 그 안에서의 우선순위는
Scoring이 아니라 상위 Interpret/Filter 단계가 담당하고, Scoring v1은 카테고리
필터를 통과한 후보들 사이에서 날씨·남은 운영시간·거리로 점수를 매긴 뒤 운영
유무로 최종 필터링한다 (§9 참고).

## 4. Feature 정의

각 Feature는 0.0~1.0 범위로 정규화한다.

### 4.1 남은 운영시간 (remaining_operating_time_score)

```
remaining_minutes = (close_time - now)를 분 단위로 계산 (now가 영업시간 안일 때만)
remaining_operating_time_score = clamp(remaining_minutes / 120, 0.0, 1.0)
```

마감까지 120분 이상 남았으면 만점(1.0)으로 취급한다. `now`가 영업시간 밖이면
§3의 하드 필터에서 이미 제외되므로 이 함수에 도달하지 않는다.
`operating_hours is None`(운영시간 미확인)이면 이 Feature 자체를 계산하지 않고
§5의 재분배 규칙을 적용한다. 미확인을 0점으로 두지 않는 이유는, 미확인이
"곧 닫음"이나 "운영종료"를 의미하지 않기 때문이다(운영종료와 구분되는 이유와 동일).

`environment_type` 매핑은 TourAPI 3단계 분류(대·중·소분류) 기준으로 판정한다
(D-046, `app/domain/candidate_mapper.py::_environment_type()`). 대분류
(`category`)만으로는 실내외를 정확히 가릴 수 없다 — 관광지(대분류)에 고궁(실외)과
체험관(실내)이, 쇼핑(대분류)에 면세점(실내)과 시장(실외)이 함께 섞여 있다.

판정 순서:

1. 후보의 소분류 코드(`lcls_systm3`)가 있으면 `TourCategoryRegistry.get_by_small_code()`
   로 조회해 `(content_type_id, lcls_systm2)` 중분류 조합을 얻는다.
2. 이 조합을 D가 정한 중분류 판정표와 비교한다 — 근거와 전체 48개 중분류
   판정 내역은 `package_D/feature-environment-type-classification.md` 참고.
   요약: indoor 19개, outdoor 16개, unknown 13개(축제·공연 등 장소마다
   실내외가 갈리는 중분류는 `unknown` 유지).
3. 소분류 코드가 없거나(과거 데이터 등) Registry에 없는 코드면, 대분류
   (`category`) 기준 최소 매핑으로 폴백한다:

| environment_type | category 예시 |
| --- | --- |
| `indoor` | cultural_facility, restaurant |
| `outdoor` | attraction |
| `unknown` | 그 외 (festival, leisure, shopping 등 미확정) |

### 4.2 날씨 적합도 (weather_fit_score)

날씨 정보가 없으면(`weather_condition is None`) 이 Feature 자체를 계산하지 않고
§5의 재분배 규칙을 적용한다. 날씨 정보가 있으면 `environment_type`과 조합한다.

| `WeatherCondition` | indoor | outdoor | unknown |
| --- | --- | --- | --- |
| `GOOD` | 0.70 | 1.00 | 0.85 |
| `NEUTRAL` | 0.80 | 0.80 | 0.80 |
| `BAD` | 1.00 | 0.30 | 0.60 |

### 4.3 거리 (distance_score)

```
distance_score = clamp(1 - distance_km / max_distance_km, 0.0, 1.0)
```

`max_distance_km`는 해당 실행의 검색 반경(`search_radius_km`)을 사용한다. 반경이
0 이하인 방어적인 경우 `distance_km == 0`이면 1.0, 아니면 0.0으로 처리한다.

### 4.4 혼잡도 (concentration_score) — D-040 확정

> **✅ 2026-08-02 D 확인 완료(D-040, `docs/decision-log.md` 참고)**: 안 B
> (1차 Scoring 10개·concentration 없음 → 상위 5개 → 2차 Scoring 5개+concentration)
> 채택. 이 절이 전제하던 "concentration이 매번 시도되고 없으면 재분배되는 단일
> Scoring" 모델(안 A)은 대안으로만 유지하고 실채택하지 않았다 — **1차 Scoring엔
> concentration 키 자체가 존재하지 않는다**(결측이 아니라 "이 단계에서는 안
> 다룸", `domain/evidence.py`의 `_FEATURE_ORDER`가 3-Feature 그대로인 이유).
> 아래 결측·계산 규칙은 **2차 Scoring(`rerank_with_concentration()`, 5개 한정)
> 에서만** 적용된다. 구현은 `domain/scoring.py::concentration_score()`,
> 배경은 [concentration-conditions.md §2.3](./concentration-conditions.md#23-scoring-반영-개요---d-039-확정-1차2차-구조)에
> 있다.

`concentration_intent`가 `null`/`IGNORE`면 2차 Scoring 자체가 호출되지 않는다
(`agent_runtime.py`의 `_CONCENTRATION_RANK_INTENTS` 게이트). `AVOID`/`SEEK`일 때만
계산하며, C의 후보 보강 응답이 반환하는 `concentration_rate`(0~100대 상대 비율,
후보별로 `no_data`/`unavailable`일 수 있음 — 그 경우 해당 후보만 §5.2의 개별
결측·재분배 규칙을 탄다)를 사용한다.

```
concentration_score (SEEK)  = clamp(concentration_rate / 100, 0.0, 1.0)
concentration_score (AVOID) = clamp(1 - concentration_rate / 100, 0.0, 1.0)
```

**선형 정규화안을 채택했다.** 대안으로 검토했던 `concentration_policy.py`의
4단계 구간(`quiet`/`normal`/`slightly_crowded`/`crowded`, 임계값 20/50/70%)을
점수 구간(예: 1.0/0.67/0.33/0.0)으로 매핑하는 방식은, distance/
remaining_operating_time과 같은 연속값 스타일을 유지하고 정보 손실을 피하기
위해 채택하지 않았다. 다만 4단계 구간 자체(`ConcentrationLevel`)는 폐기되지
않고 `RankedCandidate.concentration_level`에 원본 그대로 보존되어 근거 문장
조립(`domain/explanation.py`)에 쓰인다 — concentration_score는 이미 SEEK/AVOID
방향이 반영된 값이라 그것만으로는 실제 붐빔 정도를 알 수 없기 때문이다.

## 5. 가중치

### 5.1 기본 가중치

| Feature | 기본 가중치 |
| --- | --- |
| 날씨 | 0.40 |
| 남은 운영시간 | 0.40 |
| 거리 | 0.20 |

날씨와 남은 운영시간을 동일 비중으로 두고, 거리는 그 절반 비중으로 둔다. 카테고리는
1차 하드 필터가 처리한다고 보고 가중치 계산에서 제외한다(§1, §3).

**혼잡도 가중치(D-040 확정)**: 위 3-Feature `DEFAULT_WEIGHTS`(날씨 0.40/남은
운영시간 0.40/거리 0.20)는 **1차 Scoring 전용으로 그대로 유지**한다. 혼잡도는
별도의 2차 전용 상수 `CONCENTRATION_WEIGHTS`(`domain/scoring.py`)로 관리한다.

| Feature | 2차 Scoring 가중치(`CONCENTRATION_WEIGHTS`) |
| --- | --- |
| 날씨 | 0.35 |
| 남은 운영시간 | 0.35 |
| 거리 | 0.15 |
| 혼잡도 | 0.15 |

`concentration_intent`가 `null`/`IGNORE`인 실행은 2차 Scoring 자체가 호출되지
않으므로(§4.4) 1차 결과의 `DEFAULT_WEIGHTS` 배분(0.40/0.40/0.20)이 전혀 바뀌지
않는다 — 안 A였다면 발생했을 "혼잡도 무관심 실행에도 기존 가중치가 미세하게
바뀌는 문제"(날씨 0.4118/남은 운영시간 0.4118/거리 0.1765로 재정규화)가 안 B
구조에서는 애초에 생기지 않는다.

### 5.2 결측 시 재분배

날씨와 남은 운영시간은 각각 독립적으로 결측될 수 있다.

- 날씨: `weather_condition`이 `None`이면 해당 실행의 모든 후보에 공통으로 결측
- 남은 운영시간: 후보별 `operating_hours`가 `None`(운영시간 미확인)이면 그
  후보에만 결측
- **(D-040 확정) 혼잡도**: `concentration_intent`가 `null`/`IGNORE`면 2차
  Scoring 자체가 호출되지 않으므로 이 재분배 규칙과 무관하다(§4.4). `AVOID`/
  `SEEK`인 2차 Scoring 안에서 C의 후보 보강 응답이 `no_data`/`unavailable`인
  후보는 그 후보에만 결측(남은 운영시간과 동일한 패턴)

결측된 Feature(하나 또는 둘)를 제외하고, 그 가중치를 나머지 Feature에 **기존
비중에 비례**하여 재분배한다. 일반식은 다음과 같다.

```
remaining = {f: weight[f] for f in weights if f not in missing}
new_weight[f] = weight[f] / sum(remaining.values())   (f in remaining)
```

기본 가중치 기준 재분배 예시:

| 결측 Feature | 재분배 가중치 |
| --- | --- |
| 날씨만 결측 | 남은 운영시간 0.6667 (0.40/0.60), 거리 0.3333 (0.20/0.60) |
| 남은 운영시간만 결측 | 날씨 0.6667 (0.40/0.60), 거리 0.3333 (0.20/0.60) |
| 둘 다 결측 | 거리 1.0 |

재분배는 후보마다 다를 수 있으므로(남은 운영시간 결측이 후보별로 다름),
`weights_used`는 실행 전체가 아니라 **후보(`RankedCandidate`)마다** 노출한다
(§7 참고).

### 5.3 최종 점수

```
score = Σ (feature_score[f] * weights_used[f])   (weights_used는 후보별로 다를 수 있음)
```

## 6. 정렬 (Ranking) 및 tie-break

- 1차: `score` 내림차순
- 2차(동점 시): `distance_km` 오름차순
- 3차(그래도 동점 시): `place_id` 오름차순 (결정적 정렬 보장)

## 7. 출력 구조

```python
@dataclass(frozen=True)
class RankedCandidate:
    place_id: str
    name: str
    category: str
    rank: int
    score: float
    feature_scores: Mapping[str, float | None]  # 결측 Feature는 None
    weights_used: Mapping[str, float]  # 결측 Feature는 키 자체가 없음
    is_unverified: bool
    warnings: tuple[str, ...]

@dataclass(frozen=True)
class ScoringResult:
    ranked: tuple[RankedCandidate, ...]
    excluded_place_ids: tuple[str, ...]
    # 운영종료라서 제외된 후보만 별도로 센다(이전 노출/거절 제외와 구분) —
    # 호출부가 "결과가 0건인 이유가 전부 운영종료인가"를 판단해 no_data_closed
    # 되묻기를 띄울지 결정하는 데 쓴다(2026-08-13 추가).
    excluded_closed_place_ids: tuple[str, ...] = ()
```

`weights_used`는 §5.2의 이유로 `ScoringResult`가 아니라 `RankedCandidate`마다
따로 노출한다 (기본 가중치를 그대로 썼는지, 어떤 Feature가 결측되어
재분배됐는지는 후보마다 다를 수 있다).

`feature_scores`는 이후 Response Generator/LLM이 추천 이유를 생성할 때 근거로 쓸 수
있도록 Feature별 점수를 그대로 노출한다 (`docs/api-contracts.md`의
`RecommendationResult.featureScores`와 동일한 의도). `reason` 문자열 자체는 Scoring
v1의 책임이 아니며 후속 업무(Response Generator)에서 `feature_scores`를 입력으로
받아 생성한다. `category`는 점수에는 반영되지 않지만 표시/설명용으로 그대로
노출한다.

## 8. 카테고리를 가중치에서 제외한 이유

1차 하드 필터가 `place_type`/`place_tag` 불일치 후보를 이미 제거한다는 전제
하에서는, 필터를 통과한 후보들이 전부 "허용된 카테고리"에 속하므로 카테고리
자체는 더 이상 변별력이 없다. 다만 아래 두 가지는 이 결정의 한계로 남는다.

- 사용자가 카테고리를 2개 이상 허용한 경우(예: "카페 아니면 공원") 그 안에서
  어느 쪽을 더 우선할지는 Scoring v1이 표현하지 못한다. 이 우선순위가 필요해지면
  Interpret/Filter 단계에서 순서를 반영하거나, Scoring에 카테고리 가중치를
  다시 추가하는 논의가 필요하다.
- 현재 저장소에는 카테고리 하드 필터 자체가 아직 구현되어 있지 않다
  (`backend/docs/provider-contract-v1.md` §10.2). 하드 필터가 실제로 붙기
  전까지는 Scoring 결과에 원하지 않는 카테고리가 섞여 나올 수 있다.

## 9. 관련 문서

- [`docs/decision-log.md`](../decision-log.md) — D-008
- [`docs/architecture.md`](../architecture.md) — Recommendation Engine 책임
- [`docs/api-contracts.md`](../api-contracts.md) — `Candidate`/`RecommendationResult` 목표 계약
- [`concentration-conditions.md`](./concentration-conditions.md) §2.3 — §4.4·§5.1·§5.2의 혼잡도 Feature 배경 (D-040 확정)
- [`docs/decision-log.md`](../decision-log.md) D-040 — 혼잡도 2차 Scoring 구현 결정 기록
- [INT-01: RECOMMEND](./int-01-recommend.md) — Conditions/카테고리 배경

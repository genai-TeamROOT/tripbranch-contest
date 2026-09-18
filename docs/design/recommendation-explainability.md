# 추천 Explainability Layer 설계 (D-06)

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1 |
| 상태 | Accepted (A 담당 Agent Runtime과 API Contract 협의 반영 완료) |
| 최종 수정 | 2026-07-27 |
| 관련 코드 | `backend/app/domain/explanation.py`, `backend/app/domain/evidence.py`, `backend/app/domain/scoring.py`, `backend/app/services/recommendation_pipeline.py`, `backend/tests/test_explanation.py`, `backend/tests/test_recommendation_pipeline.py` |

이 문서는 D-02(`recommendation-evidence-fixture.md`)의 `RecommendationEvidence`를
Rule 기반·결정적 방식으로 한국어 문장으로 변환하는 D-06 Explainability Layer의
설계와, A(Agent Runtime) 담당과 협의해 확정한 Response Contract를 정리한다.

---

## 1. 범위

D-06이 다루는 것:

- `RecommendationEvidence.contributions`를 입력으로 받아 Rule 기반 한국어 문장
  목록(`explanations`)을 생성하는 로직
- 임계값·정렬·생략 규칙 정의
- 날씨 결측·임계값 미달로 근거가 조용히 생략되는 경우의 warning 보완 (D-030)
- A 담당과의 Recommendation Response Contract 협의

D-06이 다루지 않는 것 (`범위 제외`):

- LLM 기반 자연어 생성 — 이 Layer는 계산값이 채워진 Rule 기반 "사실" 문장만
  만든다. 여러 문장을 자연스러운 한 문단으로 잇거나 평가·어투("여유롭게
  방문할 수 있어요" 같은 표현)를 더하는 것은 Response Generator(LLM, `TBD`)
  영역이다
- 점수가 애매하거나(0.4~0.7) 낮은(<0.4) Feature에 대한 부정적 근거 문장 생성
  (예: "거리가 멀어요" 같은 부정적 서술은 만들지 않음)
- Chat API(`RecommendationResult`)로의 최종 통합 — A 담당 Runtime이 담당

## 2. Explanation 생성 Rule

`backend/app/domain/explanation.py`

D-06 구체화 요청(`package_D/[D-06]explainability_detail.txt`) 이후, 문장은
고정 텍스트가 아니라 `RecommendationEvidence`가 들고 있는 원본 계산값
(`distance_km`/`remaining_minutes`/`weather_condition`/`environment_type`)을
그대로 채워 넣은 "사실" 문장이다. 문장에는 평가·어투("여유롭게 방문할 수
있어요" 등)를 넣지 않는다 — 그 역할은 A 담당 Response Generator(LLM)의
몫이다(§3.2).

```python
_EXPLANATION_SCORE_THRESHOLD = 0.7

def _distance_sentence(evidence) -> str:
    # 1km 미만은 m(10m 반올림), 이상은 km(소수 첫째자리)로 "직선거리"를 명시.
    # "현재 위치에서 직선거리 약 400m예요." / "...약 1.2km예요."
    ...

def _remaining_time_sentence(evidence) -> str:
    # remaining_minutes를 "N시간 M분"/"N시간"/"M분"으로 변환(0분 나머지 생략).
    # "마감까지 약 2시간 30분 남았어요."
    ...

def _weather_sentence(evidence) -> str:
    # (weather_condition, environment_type) 조합을 "{날씨}에 적합한 {환경 }장소예요."
    # 로 조립. "비 예보에 적합한 실내 장소예요."
    ...

def build_explanations(evidence: RecommendationEvidence) -> tuple[str, ...]: ...
```

### 2.1 포함 조건

Feature 점수가 **0.7 이상**인 것만 문장화한다. 결측(`None`)이거나 애매한
점수(<0.7)는 생략한다.

**임계값 0.7의 근거**: `scoring.py`의 `_WEATHER_FIT_TABLE`(9개 날씨×환경
조합) 기준으로, 0.7 이상은 9개 중 7개 조합이 통과하는 값이다(0.85 이상으로
올리면 3개만 통과). 즉 "웬만큼 맞는 조건이면 강조한다"는 의도에 맞는
느슨한 편의 임계값으로 선택했다.

### 2.2 정렬 규칙

기여도(`contribution = score × weight`) 내림차순으로 정렬한다. 기여도가
동일하면 `contributions`에 고정된 Feature 순서(`weather` →
`remaining_operating_time` → `distance`)를 tie-break로 사용해 결정적 순서를
보장한다.

### 2.3 왜 Rule 기반인가

- LLM 호출이 없어 비용·지연시간이 없다.
- 동일 입력에 항상 동일한 문장이 나와 D-02 Fixture로 회귀 검증 가능하다.
- 추후 LLM 기반 Response Generator가 붙어도, 이 Feature별 판단을 근거
  재료로 그대로 재사용할 수 있도록 설계했다.

## 3. Response Contract (A 담당과 협의 완료)

### 3.1 `explanations`는 추가 필드, 대체 아님

기존 `recommendation_reason`(고정 템플릿 한 줄)은 그대로 유지하고,
`explanations: string[]`을 신규 필드로 추가한다. 기존 계약을 깨지 않고,
Frontend `PlaceCard.tsx`가 이미 `recommendation_reason`을 렌더링하고 있어
하위 호환을 유지한다. 나중에 LLM 기반 Response Generator가 붙어도
`recommendation_reason`(한 줄 요약)과 `explanations`(근거 목록)의 역할이
겹치지 않는다. `recommendation_reason`을 폐지하는 방향은 검토했으나,
최종적으로 두 필드를 함께 유지하기로 확정했다.

### 3.2 Rule 텍스트는 있는 그대로 노출

`explanations`는 이미 검증된 한국어 문장이므로, A 담당 Runtime은 문장
내용 자체를 LLM으로 재작문하지 않고 그대로 노출하는 것을 권장한다. 단
포맷팅(배열을 bullet list로 보여줄지, 한 문단으로 이어붙일지, 순서를
조정할지)은 Runtime 재량이다.

### 3.3 최종 대화 문장 조립에 필요한 필드

| 필드 | 용도 |
|---|---|
| `place_id` | 문장 자체엔 안 쓰이지만, 다음 턴 `shown_place_ids`/`rejected_place_ids` 추적 및 Package B `record_recommendation` 호출에 필수 |
| `name` | 장소 이름 |
| `category` | 장소 종류(이름만으론 안 드러날 때 보완) |
| `explanations` | 추천 이유 문장, 있는 그대로 노출 |
| `warnings` | 주의 문구, 있는 그대로 노출 |
| 소속 리스트 (`recommendations`/`unverified_recommendations`) | 운영시간 확인/미확인 여부 — `warnings` 유무만으로는 판단 불가(§4 참고) |

참고만 하면 되는 것: `rank`는 별도 응답 필드가 아니라 배열 순서이며(Package
B에 이력을 저장할 때는 이 배열 순서를 `rank`로 변환해서 넘겨야 함),
`score`는 필요하면 참고용으로 쓰되 0~1 스케일(100점 만점 아님)이다.
`distance_km`/`remaining_minutes`/`feature_scores`/`weights_used`는 D-06
구체화 이후 `explanations` 문장 자체를 만드는 데(예: "약 400m") 이미
쓰였으므로, A가 문장을 직접 새로 짓지 않는 이상 이 원본 숫자 필드를 별도로
다시 참조할 필요는 없다.

### 3.4 `explanations`(근거)와 `warnings`(경고) 분리 유지

의미가 다르고(긍정 근거 vs 주의사항), UI/톤 표현을 다르게 하기 쉬우며,
이미 각각 독립적으로 구현·테스트돼 있어 분리 유지가 효율적이라고 판단했다.
조립 가이드: 근거(`explanations`)를 먼저 말하고, 경고(`warnings`)는
"다만~" 식으로 마지막에 덧붙이는 순서를 권장한다.

## 4. Warning 커버리지 보완 (D-030)

운영시간 결측은 원래부터 warning이 있었지만, 아래 두 경우는 근거가 조용히
생략되면서도 아무 경고가 없었다. A 담당과의 협의 준비 과정에서 발견해,
D-06 완료 전에 우리 쪽 구현 책임으로 판단하고 해결했다.

| 케이스 | Warning 문구 |
|---|---|
| 날씨 결측으로 `weather` Feature 점수가 없는 경우 | "현재 날씨 정보를 확인하지 못해 이 조건은 반영되지 않았어요." |
| 점수는 있지만 모든 Feature가 0.7 미만이라 `explanations`가 완전히 비는 경우 | "이 장소는 특별히 강조할 만한 조건은 없지만, 조건에 맞아 추천했어요." |

운영시간 결측처럼 `unverified_recommendations`로 분리하지는 않는다 —
"존재 자체를 모르는" 운영시간 결측과 달리, 날씨 결측·낮은 점수는 그 정도로
심각한 불확실성이 아니라고 판단했기 때문이다. `distance`는
`ScoringCandidate.distance_km`가 필수 필드라 결측 케이스 자체가 존재하지
않으므로 이 보완 대상에서 제외된다.

**중요**: 이 보완 때문에, `recommendations`(정상 리스트)에 있는 항목도
`warnings`가 채워질 수 있다. 즉 "`warnings`가 비어있지 않다"가 곧
"`unverified_recommendations` 소속이다"를 의미하지 않는다. A 담당이 이
둘을 다르게 처리하고 싶다면 `warnings` 유무가 아니라 어느 배열에서 왔는지
직접 확인해야 한다.

## 5. 평가 기준

| task.txt 완료 기준 | 확인 방법 |
|---|---|
| Evidence 기반 추천 설명 생성 가능 | `build_explanations()`가 `RecommendationEvidence.contributions`를 입력으로 사용 |
| Rule 기반 Explanation 생성 구조 적용 | LLM 미호출, 고정 문장표 + 임계값·정렬 규칙만 사용 |
| Recommendation 응답 구조에 설명 데이터 반영 | `RecommendationItem.explanations` |
| Explanation 생성 Rule 및 테스트 추가 | 본 문서(Rule 정의) + `test_explanation.py`(13개: 기본 4개 + 거리 단위 경계 3개 + 운영시간 경계 4개 + 날씨·환경 조합 매트릭스 2개) + `test_recommendation_pipeline.py`(D-030 warning 2개) |

## 6. 알려진 제한사항

- **자연어 다듬기 없음**: 문장은 계산값이 채워진 "사실"까지만 만들며, 여러
  문장을 자연스럽게 이어붙이거나 평가·어투를 더하는 것은 Response
  Generator(LLM, `TBD`)의 몫이다.
- **날씨는 여전히 조합별 고정 문장**: 거리·남은 운영시간은 실제 계산값을
  그대로 문장에 반영하지만, 날씨·환경은 (`weather_condition`,
  `environment_type`) 9개 조합별 고정 문장이다 — 같은 조합 안에서 점수
  크기(예: BAD+indoor 1.00 vs NEUTRAL+indoor 0.80)에 따라 문구 강도를
  조절하지는 않는다.
- **v2 조건 미반영**: `weather_intent`/`environment`/`companion`/`budget`/
  `transport`/`max_travel_time` 등은 Interpret·Package B 상태에는 이미
  존재하지만 Scoring/Explanation에는 아직 연결되지 않았다(`scoring.py`
  docstring 기준 v2 이후 범위).

## 7. 관련 문서

- [`docs/decision-log.md`](../decision-log.md) — D-029, D-030, D-031
- [추천 Evidence·평가 Fixture 설계](./recommendation-evidence-fixture.md) — D-02, 입력이 되는 `RecommendationEvidence` 정의
- [추천 점수 설계](./recommendation-scoring.md) — Scoring v1 전체 설계
- [`docs/api-contracts.md`](../api-contracts.md) — `RecommendationItem` 계약

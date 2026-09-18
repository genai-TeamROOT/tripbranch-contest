# TripBranch 일정 엔진 개선 작업 인수인계

## 1. 작업 목적

현재 TripBranch의 일정 생성은 일반 추천 후보를 받은 뒤 LLM이 장소 선택, 방문 순서,
체류시간, 이동시간, 도착시각, 총 소요시간을 한 번에 생성하는 구조다.

이를 다음과 같은 하이브리드 구조로 개선한다.

> LLM은 사용자 맥락을 반영한 복수 일정 후보 생성, 의미적 평가, 최종 설명을
> 담당하고, 일정 엔진은 체류시간 정책, 실제 시간표 계산, 하드 제약 검증,
> 일정 스코어링과 최종 선택을 담당한다.

LLM을 설명 전용으로 축소하는 것이 아니다. 의미적 판단과 일정 후보 생성에는 계속
적극적으로 사용하되, 틀리면 안 되는 시간 계산과 제약 검증은 결정론적 코드가
보장하는 것이 목표다.

---

## 2. 현재 구현 상태

### 2.1 주요 파일

```text
backend/app/schedule/schemas.py
backend/app/schedule/planner.py
backend/app/schedule/associations.py
backend/app/providers/gemini.py
backend/app/providers/gemini_prompts.py
backend/app/prompts/schedule/plan.md
backend/app/prompts/schedule/plan_context.md
backend/app/services/runtime/agent_runtime.py
backend/app/schemas.py
backend/tests/schedule/test_planner.py
backend/tests/schedule/test_schemas.py
backend/tests/test_agent_runtime.py
docs/design/int-07-schedule.md
```

### 2.2 이미 구현된 기능

- SCHEDULE 인텐트 분류와 실행 배선
- 일반 추천 파이프라인에서 일정 후보 최대 10개 전달
- 추천 결과와 미검증 후보 병합
- 사용자가 보관함에 담은 장소를 후보에 재주입
- 보관함 장소를 `must_include_place_ids`로 일정 모듈에 전달
- 사용자 `time_available`, `transport` 등 조건 전달
- 후보 좌표 수집
- 장소 간 Haversine 직선거리 행렬 생성
- 이전 추천 좌표 스냅샷 fallback
- `co_visited_hints` 전달
- 일정 결과 세션 기록
- 활동 가능 시간에 따른 목표 장소 수 조절
- 필수 장소 누락 검증과 LLM 한 번 재시도
- 운영시간과 도착시각 충돌 시 경고
- 도착시각 10분 단위 올림
- 특정 장소만 교체하는 부분 일정 수정
- 부분 수정 시 기존 항목 pinned 처리
- 교체 이후 뒤쪽 도착시각 재동기화
- 프론트 일정 타임라인 표시

Agent 실행 배선과 일반 추천 후보 공급은 이미 준비돼 있으므로, 이번 작업에서 대규모
재구현 대상으로 잡지 않는다.

---

## 3. 현재 구조의 핵심 문제

현재 LLM이 다음 값을 직접 만든다.

- 일정에 포함할 장소
- 방문 순서
- `estimated_duration_min`
- `travel_to_next_min`
- `estimated_arrival`
- `total_duration_min`
- 장소별 `reason`
- `route_summary`

현재 체류시간도 DB 데이터나 결정론적 정책이 아니라 프롬프트 예시를 바탕으로 LLM이
추정한다.

```text
카페 60분, 관광지 90분 등을 기준으로 LLM이 체류시간을 추정
전달된 직선거리를 도보/대중교통 시간으로 환산해 이동시간 추정
시작 시각부터 이동시간과 체류시간을 누적해 도착시각 계산
```

현재 Python 후처리는 필수 장소 누락과 운영시간 충돌 일부를 확인하지만 다음은
구조적으로 보장하지 않는다.

- LLM 체류시간의 최소·최대 범위
- 가용시간 초과 여부
- 실제 장소 간 이동시간
- `total_duration_min`의 정확성
- 전체 도착시각 계산의 정확성
- 복수 일정 후보 비교
- 일정 전용 최종 스코어링

---

## 4. 담당 범위

### 4.1 일정 담당자가 소유할 범위

일정 담당자는 LLM 사용을 포함한 일정 편성 모듈 전체를 소유한다.

1. 일정 엔진 입력·출력 모델
2. 장소별 체류시간 정책
3. 결정론적 시간표 계산
4. 하드 제약 검사
5. 엔진 기반 일정 후보 생성
6. LLM 기반 복수 일정 후보 생성
7. 일정 전용 스코어링
8. LLM 기반 의미적 적합도 평가
9. 최적 일정 선택
10. 제외 장소와 실패 사유 생성
11. 최종 설명 생성
12. 기존 부분 일정 수정 연결

### 4.2 다른 담당자가 구현할 범위

장소 간 실제 이동시간 행렬은 별도 담당자가 구현한다. 일정 담당자는 이동정보 산출
로직이나 경로 API를 직접 구현하지 않고, 합의된 입력 계약을 소비한다.

TP-217에서 계약과 추정 생성기까지 구현했다. 아래는 예상이 아니라
`backend/app/domain/schedule_travel.py`에 실제로 들어간 모양이다.

```python
@dataclass(frozen=True)
class ScheduleTravelEdge:
    from_place_id: str
    to_place_id: str
    mode: TravelMode              # walking | transit | driving
    status: RouteStatus           # success | no_data | unavailable
    source: RouteSource           # kakao_walking | naver_driving
                                  # | kakao_transit | straight_line_estimate
    duration_min: int
    distance_m: int | None
    confidence: TravelConfidence  # high | low
    error_code: str | None = None
```

`mode`·`status`·`source`는 기존 경로 조회 계약(`app.domain.travel_route`)의
열거형을 그대로 쓴다. 문자열 리터럴이 아니므로 `source`로 실측/추정을 가를 때는
`source is RouteSource.STRAIGHT_LINE_ESTIMATE`로 판정한다.

`confidence`는 `high`와 `low` 둘뿐이다. 직선거리 추정은 전부 `low`이고, 실제 경로
API로 채운 구간이 `high`가 된다. 중간 단계가 필요해지면 그때 `TravelConfidence`에
값을 추가한다.

이동 행렬은 방향성을 갖는다. `A → B`와 `B → A`는 서로 다른 값일 수 있다. 행렬
형태가 필요하면 결과 객체가 그대로 만들어 준다.

```python
result = estimate_schedule_travel_edges(...)   # app.tools.schedule_travel
travel_matrix = result.edge_by_pair()          # dict[tuple[str, str], ScheduleTravelEdge]
```

좌표가 없거나 후보 목록에 없는 place ID, 자기 자신 구간, 중복 요청은 Edge를 만들지
않고 건너뛴다. 이때 전체 요청이 실패하지는 않고 `result.warnings`에
`ScheduleTravelWarning(code, from_place_id, to_place_id)`로 남으므로, 일정 엔진은
빠진 구간을 사유와 함께 확인할 수 있다.

```python
@dataclass(frozen=True)
class ScheduleTravelWarning:
    code: str            # schedule_travel_duplicate_pair
                         # | schedule_travel_self_pair
                         # | schedule_travel_unknown_place
    from_place_id: str
    to_place_id: str
```

실제 경로 조회 실패 시 직선거리 기반 추정값이 전달될 수 있으므로 일정 엔진은
`source`와 `confidence`를 이용해 경고 또는 불확실성 감점을 적용한다.

추정 생성기는 속도 셋과 도보 전환 임계값(`SCHEDULE_WALK_TRANSFER_THRESHOLD_MIN`,
기본 20분)을 설정에서 직접 읽지 않고 인자로 받으므로, 일정 엔진이 이 함수를 부를 때
`Settings` 값을 넘겨야 한다. 넘기는 호출자가 생기기 전까지는 그 설정을 바꿔도
동작이 달라지지 않는다.

### 4.2.1 유력 일정 구간의 실측 (TP-219)

추정 Edge 중 **실제로 쓰인 구간만** 경로 API로 다시 재는 함수가 같은 모듈에 있다.
출력이 추정과 같은 `ScheduleTravelEstimateResult`라 소비 코드는 하나로 쓴다.

```python
result = await measure_schedule_travel_edges(   # app.tools.schedule_travel
    tool=travel_route_tool,                     # factory.get_travel_route_tool()
    candidates=candidates,
    estimated_edges=estimated.edges,            # 추정 생성기의 결과
    max_measured_segments=settings.schedule_max_measured_segments,
)
```

넘기는 방향 쌍을 고르는 일은 일정 엔진 몫이다. 후보 전체 행렬(10곳이면 90간선)은
실측하지 않는다는 것이 TP-216·TP-217이 함께 정한 방향이므로, 유력 일정에 실제로
쓰인 인접 구간만 넘긴다.

**이동수단을 다시 고르지 않는다.** 추정 Edge에 박힌 `mode`를 그대로 써서 잰다.
같은 구간의 추정값과 실측값이 항상 같은 이동수단 기준이므로, 값이 바뀌었다면 그건
경로 때문이지 이동수단이 바뀐 탓이 아니다.

**요청한 구간은 반드시 돌아온다.** 실측에 실패해도 Edge가 사라지거나 0분이 되지
않고 추정값 그대로 남는다. 구분은 이렇게 한다.

| 결과 | `source` | `confidence` | `error_code` |
| --- | --- | --- | --- |
| 실측 성공 | `kakao_walking` 등 | `high` | 없음 |
| 실측 실패 → 추정 유지 | `straight_line_estimate` | `low` | 실패 사유 |
| 상한 초과로 안 잼 | `straight_line_estimate` | `low` | 없음 |

`status`는 이 경우에도 `success`로 남는다. **이 Edge에 쓸 수 있는 이동시간이 들어
있는가**를 말하는 자리이고 추정값도 쓸 수 있는 값이기 때문이다. 실측 여부는
`source`·`confidence`로, 실패 사유는 `error_code`로 판단한다 — `status`가
`success`라는 이유로 실측이라고 읽으면 안 된다.

실측하지 못한 구간은 `result.warnings`에도 남는다.

| 경고 코드 | 뜻 |
| --- | --- |
| `schedule_travel_measure_failed` | 그 구간의 경로 조회가 실패했다 |
| `schedule_travel_measure_unavailable` | 이동수단 Provider가 통째로 죽어 그룹 전체가 못 왔다 |
| `schedule_travel_measure_budget_exceeded` | 실측 구간 수 상한을 넘겨 호출하지 않았다 |

상한은 `SCHEDULE_MAX_MEASURED_SEGMENTS`(기본 12)이고 **입력 순서 앞에서부터** 채운다.
어느 구간을 먼저 실측할지는 넘기는 순서로 정하면 된다.

---

## 5. 목표 아키텍처

```text
일반 추천 후보 최대 10개
+ 보관함 필수 장소
+ 사용자 조건
+ 운영시간
+ 실제/추정 이동 행렬
        ↓
LLM: 의미적으로 다른 일정 순서 후보 생성
+ 엔진: 이동·운영시간 중심 후보 생성
        ↓
체류시간 정책 적용
        ↓
도착·출발·대기·총시간 계산
        ↓
하드 제약 검증
        ↓
결정론적 일정 점수
+ 선택적 LLM 의미 점수
        ↓
최적 일정 선택
        ↓
LLM: 장소별 이유·동선 요약·제외 사유 설명
        ↓
ScheduleResult
```

---

## 6. LLM 역할

### 6.1 LLM이 담당할 것

- 사용자 여행 목적과 일정 스타일 해석
- 장소 조합의 의미적 자연스러움 판단
- 성격이 다른 복수 일정 초안 생성
- 사용자 취향에 맞는 장소 순서 제안
- 장소 카테고리 반복과 다양성 판단
- 체류시간 정책 범위 안에서 조정 제안
- 가능한 일정 후보의 의미적 적합도 평가
- 최종 장소별 배치 이유 작성
- 전체 동선 요약
- 제외 장소 설명
- 일정 생성 실패 시 조정안 제안

### 6.2 LLM이 후보만 제안할 항목

- 포함할 장소
- 방문 순서
- 체류시간 조정안

위 항목들은 LLM 제안을 일정 엔진이 검증한 후 최종 확정한다.

### 6.3 LLM이 담당하지 않을 항목

- 실제 이동시간 생성
- 정확한 도착·출발시각 계산
- 총 소요시간 계산
- 운영시간 위반 최종 판정
- 가용시간 초과 최종 판정
- 필수 장소 포함 최종 판정
- 예약시간 충족 여부
- 하드 제약을 무시한 최종 일정 확정

---

## 7. 구현해야 할 세부 구조

### 7.1 체류시간 정책

현재 LLM이 임의로 생성하는 `estimated_duration_min`을 범위 정책으로 변경한다.

```python
class VisitDurationPolicy(BaseModel):
    minimum_min: int
    preferred_min: int
    maximum_min: int
```

초기 정책 예시:

| 장소 유형 | 최소 | 권장 | 최대 |
|---|---:|---:|---:|
| 카페 | 40분 | 60분 | 90분 |
| 식당 | 60분 | 90분 | 120분 |
| 쇼핑 | 30분 | 60분 | 90분 |
| 관광지 | 60분 | 90분 | 120분 |
| 전시·문화시설 | 90분 | 120분 | 180분 |
| 공원·산책 | 40분 | 60분 | 120분 |

최종 값의 우선순위:

```text
사용자 지정값
→ 장소별 estimated_visit_minutes
→ 세부 카테고리 정책
→ 상위 카테고리 기본값
```

현재 `place_enrichments.estimated_visit_minutes` 컬럼은 존재하지만 실제 값은 거의
또는 전혀 채워져 있지 않으므로, 초기에는 카테고리 정책이 필요하다.

LLM이 체류시간을 제안하더라도 엔진의 최소·최대 범위를 벗어나면 허용하지 않는다.

### 7.2 LLM 일정 후보 출력

현재 LLM이 완성된 `ScheduleItem`을 생성하는 계약을 복수 순서 후보 계약으로 변경하는
방안을 검토한다.

```python
class ScheduleLLMCandidatePlan(BaseModel):
    strategy: str
    ordered_place_ids: list[str]
    semantic_reason: str | None = None


class ScheduleLLMCandidatePlans(BaseModel):
    plans: list[ScheduleLLMCandidatePlan]
```

LLM은 예를 들어 다음 후보를 생성한다.

- 동선 효율 중심
- 사용자 취향·여행 목적 중심
- 여유로운 일정
- 장소 다양성 중심

LLM은 이 단계에서 도착시각이나 이동시간을 계산하지 않는다.

### 7.3 엔진 일정 후보 생성

LLM 후보만으로 탐색 공간을 제한하지 않도록 엔진 기반 후보도 생성한다.

- 총 이동시간 최소
- 운영 종료가 빠른 장소 우선
- 필수 장소 중심
- 방문 장소 수 최대
- 대기시간 최소
- 추정 이동정보 사용 최소
- 여유시간 확보

후보가 최대 10개이므로 초기에는 greedy insertion 또는 작은 beam search로 충분하다.
LLM 후보와 엔진 후보를 합친 뒤 모두 동일한 검증과 점수 함수를 적용한다.

### 7.4 시간표 계산기

순서가 주어지면 엔진이 다음 값을 직접 계산한다.

- 장소별 도착시각
- 운영 시작 전 대기시간
- 실제 방문 시작시각
- 체류시간
- 출발시각
- 다음 장소까지 이동시간
- 일정 종료시각
- 총 소요시간
- 남는 가용시간

```python
class PlannedStop(BaseModel):
    order: int
    place_id: str
    arrival_at: datetime
    visit_start_at: datetime
    departure_at: datetime
    visit_duration_min: int
    waiting_before_visit_min: int
    travel_to_next_min: int | None
```

`ScheduleResult`의 표시용 `estimated_arrival` 등은 이 결과에서 변환한다.

### 7.5 하드 제약 검사

점수를 계산하기 전에 불가능한 후보를 제거한다.

초기 하드 제약:

- 필수 장소 누락
- 전체 가용시간 초과
- 운영시간 밖 방문
- 최소 체류시간 미확보
- 장소 간 이동정보 누락
- 동일 장소 중복
- 잘못된 방문 순서
- 도착·출발시각 불일치

향후 확장:

- 예약·공연 고정시간
- 출발지와 최종 도착지
- 사용자가 고정한 방문 순서
- 최대 도보시간
- 환승 횟수 제한

운영시간이 확인되지 않은 장소는 바로 탈락시키지 말고 `unknown availability`로
처리해 경고나 불확실성 감점을 적용하는 방안을 우선 검토한다.

```python
class ScheduleViolation(BaseModel):
    code: str
    place_ids: list[str]
    detail: str | None = None


class ValidationResult(BaseModel):
    feasible: bool
    violations: list[ScheduleViolation]
```

### 7.6 일정 전용 스코어링

하드 제약을 통과한 후보에만 한 번 적용한다.

```python
class ScheduleScore(BaseModel):
    total: float
    priority_score: float
    semantic_fit_score: float
    time_utilization_score: float
    buffer_score: float
    travel_cost: float
    waiting_cost: float
    fatigue_cost: float
    uncertainty_cost: float
```

```text
일정 점수 =
  필수·선호 장소 포함
+ 사용자 목적과의 적합성
+ 적절한 시간 활용
+ 적절한 버퍼
- 총 이동시간
- 불필요한 대기
- 이동 피로도
- 추정 이동정보 불확실성
- 일정 밀도 불일치
```

기존 일반 추천 점수는 일정 점수의 중심으로 다시 강하게 사용하지 않는다. 사용한다면
필수가 아닌 장소 사이의 보조 우선순위, 일정 후보가 거의 동일할 때 동점 해소,
낮은 가중치의 보조 feature 정도로 제한한다.

### 7.7 LLM 의미 점수

AI 에이전트 특성을 유지하기 위해 하드 제약을 통과한 후보에 대해 선택적으로 LLM
의미 평가를 적용할 수 있다.

평가 항목:

- 사용자 여행 목적과의 적합성
- 장소 흐름의 자연스러움
- 장소 카테고리 다양성
- 반복되는 활동으로 인한 단조로움
- 휴식 지점의 적절성
- `힐링`, `데이트`, `알차게` 같은 요청 반영

```python
class ScheduleSemanticEvaluation(BaseModel):
    score: float
    user_intent_fit: float
    flow_quality: float
    variety: float
    reason: str
```

권장 조건:

- 하드 제약을 통과한 후보에만 실행
- 전체 점수에서 낮은 가중치로 적용
- 초기에는 약 10~20% 이내
- LLM 평가 실패 시 결정론적 점수만으로 동작
- LLM 점수가 하드 제약을 뒤집을 수 없음

### 7.8 제외 사유와 생성 실패 결과

일정에서 빠진 장소와 생성 실패 원인을 구조화한다.

```python
class OmittedPlace(BaseModel):
    place_id: str
    reason: str
    required_extra_minutes: int | None = None
```

예상 코드:

```text
insufficient_time
outside_operating_hours
excessive_travel
lower_priority
missing_travel_edge
unknown_availability
item_limit
```

필수 장소가 모두 들어가지 못하면 임의로 제거한 정상 일정처럼 반환하지 말고,
구조화된 생성 불가 사유와 조정안을 제공한다.

### 7.9 최종 설명 생성

최적 일정이 확정된 뒤 LLM에 다음 구조화 정보를 전달한다.

- 확정된 장소와 순서
- 확정된 도착·출발시각
- 체류시간
- 구간별 이동시간
- 배치에 사용한 점수 근거
- 제외 장소와 제외 코드
- 불확실한 운영시간·이동정보

LLM은 각 장소를 해당 순서에 배치한 이유, 전체 동선 요약, 제외 장소 설명,
사용자가 조정할 수 있는 대안을 생성한다. 설명 생성에 실패하더라도 계산된 일정은
반환할 수 있도록 결정론적 문구 fallback을 둔다.

### 7.10 부분 일정 수정

현재 `REJECT_SPECIFIC` 부분 수정 기능을 새 엔진에 연결한다.

```text
유지 장소
→ pinned hard constraint

교체 대상 자리
→ 비움

새 후보
→ LLM 또는 엔진이 제안

이후
→ 전체 시간표 재계산
→ 이동시간 재적용
→ 운영시간·가용시간 전체 재검사
→ 동일한 일정 스코어로 최적안 선택
```

부분 수정 전용 스코어를 별도로 만들지 말고 최초 생성과 동일한 엔진과 평가 함수를
재사용한다.

---

## 8. 권장 모듈 구조

기존 `backend/app/schedule` 내부를 다음과 같이 나누는 방안을 검토한다.

```text
backend/app/schedule/
├── schemas.py
├── duration.py
├── timeline.py
├── availability.py
├── generator.py
├── scorer.py
├── optimizer.py
├── semantic.py
├── explainer.py
└── planner.py
```

| 파일 | 책임 |
|---|---|
| `schemas.py` | 일정 엔진 입력·출력·내부 모델 |
| `duration.py` | 장소별 체류시간 정책 |
| `timeline.py` | 도착·출발·대기·총시간 계산 |
| `availability.py` | 운영시간과 하드 제약 검사 |
| `generator.py` | 결정론적 일정 후보 생성 |
| `scorer.py` | 일정 전용 정량 점수 |
| `optimizer.py` | LLM·엔진 후보 병합 및 최적안 선택 |
| `semantic.py` | LLM 후보 생성과 의미 평가 |
| `explainer.py` | 최종 설명 생성과 fallback |
| `planner.py` | 기존 호출부와 호환되는 공개 진입점 |

이 파일 분리는 제안이며, 저장소의 기존 스타일과 테스트 구조를 확인한 뒤 더 적합한
구성을 선택해도 된다.

---

## 9. 구현 순서 제안

1. 기존 일정 코드와 테스트를 기준으로 회귀 동작 파악
2. 이동 행렬 입력 계약 합의
3. 엔진 내부 입력·출력 스키마 작성
4. 카테고리별 체류시간 정책 구현
5. 결정론적 시간표 계산기 구현
6. 하드 제약 검사 구현
7. 단순 엔진 후보 생성 구현
8. 일정 전용 스코어링 구현
9. LLM 출력 계약을 복수 후보 중심으로 변경
10. LLM 후보와 엔진 후보 병합
11. 최적 일정 선택
12. 최종 설명 생성과 fallback
13. 기존 `plan_schedule()`에 연결
14. 부분 수정 기능 연결
15. 기존 테스트 회귀 및 신규 단위 테스트 추가

실제 이동 행렬 구현이 완료되기 전에는 Fake 행렬을 사용해 일정 엔진을 독립적으로
개발할 수 있다.

---

## 10. 테스트 요구사항

### 10.1 체류시간

- 카테고리 기본값 적용
- 최소값 아래 축소 방지
- 최대값 초과 방지
- 가용시간 부족 시 낮은 우선순위 장소 제외

### 10.2 시간표

- 이동시간과 체류시간 누적
- 운영 시작 전 대기시간
- 일정 종료시각
- 총 소요시간
- 자정 교차 처리

### 10.3 하드 제약

- 필수 장소 누락
- 운영시간 위반
- 가용시간 초과
- 이동 간선 누락
- 동일 장소 중복
- 운영시간 unknown 처리

### 10.4 후보 생성과 점수

- 서로 다른 일정 후보 생성
- 하드 제약 위반 후보 제거
- 이동시간이 짧은 후보 우대
- 추정 이동정보 불확실성 감점
- 일반 추천 점수의 과도한 중복 반영 방지
- 동일 입력에서 결정론적 점수 재현

### 10.5 LLM

- 후보 place ID 환각 차단
- 필수 장소 누락 후보 제거
- 잘못된 LLM 후보가 전체 흐름을 실패시키지 않음
- 의미 평가 실패 시 정량 점수 fallback
- 설명 생성 실패 시 고정 문구 fallback

### 10.6 부분 수정

- pinned 장소 유지
- 교체 후 전체 도착시각 재계산
- 교체 후 운영시간 재검사
- 교체 후 총 소요시간 재계산
- 기존 일정 스코어 함수 재사용

---

## 11. 우선 요청사항

바로 전체 구현에 들어가기 전에 먼저 다음을 산출한다.

1. 현재 코드 기준 상세 변경 설계
2. 변경할 스키마 목록
3. 기존 동작과의 하위 호환 전략
4. 이동 행렬 인터페이스 제안
5. 일정 후보 생성 알고리즘 제안
6. 일정 점수 항목과 초기 가중치 제안
7. LLM 호출 횟수와 지연시간 관리 방안
8. 단계별 구현 계획
9. 테스트 계획
10. 담당 경계를 넘어 Agent 또는 추천 파이프라인 수정이 정말 필요한 지점

현재 구현돼 있는 실행 배선, 보관함 주입, 추천 후보 공급, 부분 수정 기능을 다시
만들지 말고 재사용한다.

최종 설계 원칙은 다음과 같다.

> LLM 의존도를 제거하는 것이 아니라, LLM은 사용자 맥락과 장소 조합 같은 의미적
> 판단에 집중하고, 이동시간·체류시간 범위·도착시각·운영시간·가용시간처럼 정확성이
> 필요한 부분은 일정 엔진이 검증하고 확정한다.

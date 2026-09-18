# LLM Output Schema v1

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1.1 |
| 작성자 | 임기민 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-23 |
| 경로 | `docs/design/llm-output-schema.md` |

---

## 1. 개요

### 이 문서의 역할

LLM이 사용자 발화를 해석한 결과를 **어떤 형태로 출력하는지**를 정의한다.

### 기존 문서와의 관계

| 문서 | 역할 | 본 문서와의 관계 |
|------|------|-----------------|
| `intent-definition.md` | Intent 정의, 판별 규칙 | 본 문서의 intent 필드 기준 |
| `int-01~06 개별 문서` | Intent별 상세 스키마 | 본 문서의 payload body 기준 |
| `conditions-schema.md` | 조건 변경 연산 규칙 | 본 문서의 operations 규칙 기준 |
| `agent-state-contract-v1.md` (B) | State 저장·병합 계약 | 본 문서의 데이터 전달 대상 |

### 핵심 구조

```
사용자 입력
    ↓
A: LLM 호출 → Structured Output (본 문서가 정의하는 것)
    ↓
A → B: operations 전달 → B가 user_conditions 저장
    ↓
B → A: user_conditions + api_context 반환
    ↓
A: 병합 → answer_conditions 생성
    ↓
A: answer_conditions로 추천/응답 생성
```


---

## 2. 3층 Conditions 구조

### 개요

| 층 | 이름 | 저장 위치 | 생성 주체 | 설명 |
|----|------|-----------|-----------|------|
| ① | `user_conditions` | B State | A (LLM 추출) | 사용자 발화에서 추출한 조건 (14개 필드) |
| ② | `api_context` | B State | A/Runtime (외부 API) | GPS, 날씨 API로 확보한 외부 데이터 |
| ③ | `answer_conditions` | 저장하지 않음 | A (병합) | ①+② 병합 결과. 추천/응답 생성에 사용 |

### ① user_conditions (14개 필드)

```typescript
interface UserConditions {
  current_location: string | null;
  search_center: string | null;
  place_types: PlaceType[];
  place_tags: PlaceTag[];
  weather: string | null;
  weather_intent: string | null;
  transport: string | null;
  max_travel_time: number | null;
  time_available: number | null;
  environment: string | null;
  companion: string | null;
  budget: string | null;
  exclude_tags: string[];
  special_requirements: string[];
}
```

### ② api_context (B 별도 저장)

```typescript
interface ApiContext {
  gps_location: string;
  api_weather: string | null;
  gps_location_updated_at: string;
  api_weather_updated_at: string | null;
}
```

### ③ answer_conditions (매 실행 시 생성)

병합 우선순위:

```
위치: user_conditions.current_location > api_context.gps_location
날씨: user_conditions.weather > api_context.api_weather
나머지: user_conditions 값 그대로
```

---

## 3. 통합 Payload Envelope

모든 Intent가 이 하나의 envelope에 담겨 반환된다.

```typescript
interface LLMOutput {
  intent: Intent;
  status: OutputStatus;

  // Intent별 payload (intent에 따라 하나만 존재)
  recommend?: RecommendPayload;
  info?: InfoPayload;
  modify?: ModifyPayload;
  compare?: ComparePayload;
  general?: GeneralPayload;
  out_of_scope?: OutOfScopePayload;

  // 확인 필요 시
  clarification?: ClarificationPayload;
}
```

---

## 4. Status 체계

```typescript
type OutputStatus =
  | "complete"              // 처리에 필요한 정보가 모두 추출됨
  | "needs_clarification";  // 누락·모호한 정보가 있어 사용자 확인 필요
```

| status | 의미 | 후속 동작 |
|--------|------|-----------|
| `complete` | 조건 추출 완료, 즉시 처리 가능 | B에 전달 → 추천/응답 실행 |
| `needs_clarification` | 필수 정보 누락 또는 의도 모호 | 사용자에게 되묻기, State 변경 없음 |

---

## 5. Intent별 Payload Body

### 5-1. RECOMMEND

```typescript
interface RecommendPayload {
  conditions: UserConditions;
}
```

### 5-2. INFO

```typescript
interface InfoPayload {
  place_name: string | null;
  place_context: "explicit" | "from_recommendation" | "from_conversation";
  question_type: QuestionType;
  specific_question: string | null;
}
```

### 5-3. MODIFY

```typescript
interface ModifyPayload {
  modify_type: "REJECT_ALL" | "CHANGE_CONDITION";
  condition_changes: Partial<UserConditions> | null;
}
```

- `REJECT_ALL`: 추천 결과 전체 거부 → 다른 장소 보여줘
- `CHANGE_CONDITION`: 조건 변경 → condition_changes에서 null이 아닌 필드만 변경 대상

### 5-4. COMPARE

```typescript
interface ComparePayload {
  targets: "all" | number[];
  criteria: "distance" | "time" | "overall";
}
```

### 5-5. GENERAL

```typescript
interface GeneralPayload {
  topic: GeneralTopic;
  original_question: string;
}
```

### 5-6. OUT_OF_SCOPE

```typescript
interface OutOfScopePayload {
  category: "harmful" | "unrelated" | "role_request" | "prompt_injection";
  severity: "high" | "medium" | "low";
}
```

---

## 6. Clarification 반환 구조

status가 `needs_clarification`일 때 함께 반환.

```typescript
interface ClarificationPayload {
  missing_fields: MissingField[];
  ambiguous_fields: AmbiguousField[];
  message: string;  // 사용자에게 보여줄 되묻기 문구
}

interface MissingField {
  field: string;
  reason: string;
}

interface AmbiguousField {
  field: string;
  user_input: string;
  candidates: string[];
  reason: string;
}
```

### 발생 예시

| 상황 | 유형 | 예시 |
|------|------|------|
| 위치 없음 (GPS도 없음) | missing | "현재 위치를 알려주세요" |
| weather_intent 모호 | ambiguous | "눈 오는데" → 실내? 야외? |
| 위치 검색 결과 여러 개 | ambiguous | "성수" → 성수동? 성수역? |

---

## 7. 전달 예시

### RECOMMEND — complete

```json
{
  "intent": "RECOMMEND",
  "status": "complete",
  "recommend": {
    "conditions": {
      "current_location": null,
      "search_center": "경복궁",
      "place_types": ["cultural_facility", "restaurant"],
      "place_tags": ["박물관", "카페"],
      "weather": "rain",
      "weather_intent": "AVOID",
      "transport": "walk",
      "max_travel_time": null,
      "time_available": null,
      "environment": "indoor",
      "companion": null,
      "budget": null,
      "exclude_tags": [],
      "special_requirements": []
    }
  },
  "clarification": null
}
```

### RECOMMEND — needs_clarification

```json
{
  "intent": "RECOMMEND",
  "status": "needs_clarification",
  "recommend": {
    "conditions": {
      "current_location": null,
      "search_center": null,
      "place_types": ["restaurant"],
      "place_tags": ["카페"],
      "weather": "snow",
      "weather_intent": null,
      "transport": null,
      "max_travel_time": null,
      "time_available": null,
      "environment": null,
      "companion": null,
      "budget": null,
      "exclude_tags": [],
      "special_requirements": []
    }
  },
  "clarification": {
    "missing_fields": [],
    "ambiguous_fields": [
      {
        "field": "weather_intent",
        "user_input": "눈 오는데 카페 추천해줘",
        "candidates": ["AVOID", "ENJOY"],
        "reason": "눈을 피해 실내를 원하시는지, 눈 오는 풍경을 즐기고 싶으신지 확인이 필요합니다"
      }
    ],
    "message": "눈 오는 풍경을 즐기고 싶으신가요, 아니면 실내 장소를 찾으시나요?"
  }
}
```

### MODIFY — complete (CHANGE_CONDITION)

```json
{
  "intent": "MODIFY",
  "status": "complete",
  "modify": {
    "modify_type": "CHANGE_CONDITION",
    "condition_changes": {
      "environment": "indoor",
      "budget": "free"
    }
  },
  "clarification": null
}
```

### INFO — complete

```json
{
  "intent": "INFO",
  "status": "complete",
  "info": {
    "place_name": "경복궁",
    "place_context": "explicit",
    "question_type": "operating_hours",
    "specific_question": "오늘 몇 시까지 해?"
  },
  "clarification": null
}
```

---

## 8. Pydantic 모델 초안

아래 `UserConditions` 필드 정의는 [conditions-schema.md § 2. Conditions 필드 정의](./conditions-schema.md#2-conditions-필드-정의)를 기준으로 파생되었다. 필드 의미·예시·PlaceType/PlaceTag enum 전문은 해당 문서를 참조한다.

```python
from __future__ import annotations
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel


class Intent(str, Enum):
    RECOMMEND = "RECOMMEND"
    INFO = "INFO"
    MODIFY = "MODIFY"
    COMPARE = "COMPARE"
    GENERAL = "GENERAL"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class OutputStatus(str, Enum):
    COMPLETE = "complete"
    NEEDS_CLARIFICATION = "needs_clarification"


class ModifyType(str, Enum):
    REJECT_ALL = "REJECT_ALL"
    CHANGE_CONDITION = "CHANGE_CONDITION"


class WeatherIntent(str, Enum):
    AVOID = "AVOID"
    ENJOY = "ENJOY"
    NO_MENTION = "NO_MENTION"
    IGNORE = "IGNORE"


class CompareCriteria(str, Enum):
    DISTANCE = "distance"
    TIME = "time"
    OVERALL = "overall"


class OutOfScopeCategory(str, Enum):
    HARMFUL = "harmful"
    UNRELATED = "unrelated"
    ROLE_REQUEST = "role_request"
    PROMPT_INJECTION = "prompt_injection"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GeneralTopic(str, Enum):
    SERVICE_IDENTITY = "service_identity"
    TRAVEL_TIP = "travel_tip"
    SEASON_INFO = "season_info"
    AREA_INFO = "area_info"
    PLACE_KNOWLEDGE = "place_knowledge"
    PLANNING_TIP = "planning_tip"
    FOOD_CULTURE = "food_culture"
    TRANSPORT_INFO = "transport_info"


class QuestionType(str, Enum):
    OPERATING_HOURS = "operating_hours"
    FEE = "fee"
    PARKING = "parking"
    FACILITY = "facility"
    EVENT = "event"
    LOCATION_INFO = "location_info"
    GENERAL_INFO = "general_info"


class PlaceContext(str, Enum):
    EXPLICIT = "explicit"
    FROM_RECOMMENDATION = "from_recommendation"
    FROM_CONVERSATION = "from_conversation"


# === Conditions ===

class UserConditions(BaseModel):
    current_location: Optional[str] = None
    search_center: Optional[str] = None
    place_types: list[str] = []
    place_tags: list[str] = []
    weather: Optional[str] = None
    weather_intent: Optional[WeatherIntent] = None
    transport: Optional[str] = None
    max_travel_time: Optional[int] = None
    time_available: Optional[int] = None
    environment: Optional[str] = None
    companion: Optional[str] = None
    budget: Optional[str] = None
    exclude_tags: list[str] = []
    special_requirements: list[str] = []


# === Intent Payloads ===

class RecommendPayload(BaseModel):
    conditions: UserConditions


class InfoPayload(BaseModel):
    place_name: Optional[str] = None
    place_context: PlaceContext
    question_type: QuestionType
    specific_question: Optional[str] = None


class ModifyPayload(BaseModel):
    modify_type: ModifyType
    condition_changes: Optional[UserConditions] = None


class ComparePayload(BaseModel):
    targets: list[int] | str
    criteria: CompareCriteria


class GeneralPayload(BaseModel):
    topic: GeneralTopic
    original_question: str


class OutOfScopePayload(BaseModel):
    category: OutOfScopeCategory
    severity: Severity


# === Clarification ===

class MissingField(BaseModel):
    field: str
    reason: str


class AmbiguousField(BaseModel):
    field: str
    user_input: str
    candidates: list[str]
    reason: str


class ClarificationPayload(BaseModel):
    missing_fields: list[MissingField] = []
    ambiguous_fields: list[AmbiguousField] = []
    message: str


# === LLM Output (Top-level) ===

class LLMOutput(BaseModel):
    intent: Intent
    status: OutputStatus
    recommend: Optional[RecommendPayload] = None
    info: Optional[InfoPayload] = None
    modify: Optional[ModifyPayload] = None
    compare: Optional[ComparePayload] = None
    general: Optional[GeneralPayload] = None
    out_of_scope: Optional[OutOfScopePayload] = None
    clarification: Optional[ClarificationPayload] = None
```

---

## 9. B 전달 시 협의 필요 사항

아래 항목들은 본 문서(A 출력)와 Agent State(B 수신) 사이의 **인터페이스 계약**으로, 태화님과 협의 후 확정 예정.

## 9. B 전달 시 협의 사항

본 문서(A 출력)와 Agent State(B 수신) 사이의 인터페이스 계약.

### 확정 사항

| # | 항목 | 확정 내용 | 확정일 |
|---|------|----------|--------|
| 1 | LLMOutput → B 전달 포맷 | A가 LLMOutput을 StateApplyRequest로 변환한 뒤 전달한다. B에 LLMOutput 원본이 가지 않는다. LLMOutput의 intent별 payload를 읽고 operations로 변환하는 건 해석 행위이므로 A의 영역 | 2026-07-23 |
| 2 | operations 연산 체계 | Add / Update / Remove 3종. Keep은 별도 op가 아니라 operations 배열에 없으면 자동 Keep. 필드별 허용 연산은 [conditions-schema.md § 4절](./conditions-schema.md#4-조건-변경-연산) 기준 | 2026-07-23 |
| 3 | reset_scope 트리거 조건 | B가 조건 변경을 감지해 자동으로 판단하지 않는다. A가 soft / history / full / null 중 하나를 명시적으로 판정하여 operations와 함께 전달한다 (예: search_center 변경 시 A가 `reset_scope: "history"`를 함께 전달) | 2026-07-23 |
| 4 | condition_changes에서 Remove 표현 | 시그널 값(`"__REMOVE__"` 등)을 쓰지 않는다. A가 제거 의도를 판단하면 Operation을 `{"op": "Remove", "field": "..."}`로 직접 생성하여 전달한다 | 2026-07-23 |
| 5 | api_context 갱신 경로 | operations와 별도 경로로 갱신한다. A/Runtime이 GPS·날씨 API를 재호출한 뒤 B에 직접 전달하며, condition_version은 증가하지 않는다 | 2026-07-23 |


### 미확정 항목 (B 확인 필요)

| # | 항목 | 현재 상태 | 협의 내용 |
|---|------|----------|----------|
| 6 | answer_conditions 생성 | A 내부 전용. B에 저장하지 않으며 B는 관여하지 않는다. 상세 스키마는 추천 엔진 설계 시 A가 확정한다 | 이 형식으로 확정 가능한지 확인 |
| 7 | rejected_places — reason_code 목록 | 후보 5종 (`too_far` / `not_interested` / `already_visited` / `closed` / `other`) | 이 5종으로 확정 가능한지, B쪽에서 reason_code별 다른 처리가 필요한지 확인 |
| 8 | rejected_places — place_id 형식 | TourAPI contentid 문자열 그대로 (예: `"126508"`) | 이 형식으로 확정 가능한지 확인 |
| 9 | confirmed: false일 때 B 동작 범위 | 초안 있음 | operations·rejected_places는 무시하되, intent 기록(last_intent)·Trace(run_id 발급)·세션 TTL은 갱신하는 "부분 저장" 방식을 A가 제안 중. B쪽 설계에 맞는 방식 확인 필요 |

---

## 10. 미확정 항목 (A 내부)

| # | 항목 | 잠정 결정 | 확정 시점 |
|---|------|-----------|-----------|
| 1 | LLM 호출 방식 | 1단계 (Intent + Conditions 동시 추출) 우선 시도 | 프롬프트 설계 시 |
| 2 | place_types 교체 시 place_tags 정리 | A가 Remove 연산 함께 생성 | 구현 시 |
| 3 | MODIFY의 condition_changes 표현 | null = 변경 없음, 값 있음 = 변경 대상 | 구현 시 |

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v1.0 | 2026-07-23 | 초안 작성. 3층 구조, Envelope, Status, Intent별 Payload, Clarification, Pydantic 모델 |
| v1.1 | 2026-07-23 | 9절 협의 필요 사항 중 4건(#2,4,6,8: operations 연산 체계, reset_scope, condition_changes Remove 표현, api_context 갱신 경로)을 확정 사항으로 반영. 8절 Pydantic 모델에 conditions-schema.md 기준 파생임을 명시(소유권 정리) |

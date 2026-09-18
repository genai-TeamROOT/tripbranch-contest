# 조건 스키마 v0.3

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.3 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-23 |
| 경로 | `docs/design/conditions-schema.md` |

---

## 1. Intent와 Conditions 관계

```
사용자 입력
    ↓
Intent (목적 1개)
    ↓
Conditions (조건 N개, Intent가 RECOMMEND/MODIFY일 때 추출)
```

- Intent: 사용자가 **무엇을** 하고 싶은가
- Conditions: **어떤 조건으로** 하고 싶은가
- RECOMMEND, MODIFY에서 Conditions를 사용한다
- INFO, COMPARE, GENERAL, OUT_OF_SCOPE는 별도 스키마를 사용한다

---

## 2. Conditions 필드 정의

아래 인터페이스는 B가 저장하는 `user_conditions`에 해당한다. 사용자 발화에서 LLM이
추출한 값만 담으며, GPS·날씨 API로 보충한 값은 별도의 `api_context` 구조(3절 참고)에
저장한다. `user_conditions`와 `api_context`를 병합한 최종 조건은 `answer_conditions`이며,
이는 A가 매 실행 시 생성하고 B는 저장하지 않는다.

```typescript
interface Conditions {
  // 위치 (사용자 발화 기준 — API로 보충한 값은 api_context.gps_location에 별도 저장)
  current_location: string | null;
  search_center: string | null;

  // 장소 유형 (복수 가능)
  place_types: PlaceType[];
  place_tags: PlaceTag[];

  // 날씨 (사용자 발화 기준 — API로 보충한 값은 api_context.api_weather에 별도 저장)
  weather: "rain" | "snow" | "hot" | "cold" | "good" | null;
  // "비 와도 괜찮아"처럼 날씨를 감수하는 허용 표현은 ENJOY가 아니라 IGNORE다.
  weather_intent: "AVOID" | "ENJOY" | "NO_MENTION" | "IGNORE" | null;

  // 혼잡도 (weather_intent와 동일 패턴 — 상세는 concentration-conditions.md 참고)
  // "사람 많아도 괜찮아"는 SEEK가 아니라 IGNORE다. SEEK는 혼잡함을 목적으로 원할 때만 쓴다.
  concentration_intent: "AVOID" | "SEEK" | "IGNORE" | null;

  // 이동
  transport: "walk" | "public" | "car" | null;
  max_travel_time: number | null;
  // 이동시간의 출발점 판정(사실이 아니라 판정 — D-071). "안국역에서/까지 10분"처럼
  // 조사가 출발점을 확정할 때만 "search_center". "근처/주변"이나 미언급은 null이며
  // 그때는 기존 기본값(D-067, 사용자 위치 우선)이 그대로 적용된다. "user_location"은
  // 추출 단계에서 쓰지 않고, 결과를 받은 뒤 기준점 전환 버튼이 생기면 그때 쓴다.
  travel_origin: "user_location" | "search_center" | null;

  // 시간
  time_available: number | null;

  // 환경
  // 언급이 없으면 null. "any"는 "실내외 상관없어"나 "야외도 괜찮아"처럼
  // 선택지를 넓히는 허용 표현을 명시했을 때만 쓴다.
  // — 미언급을 "any"로 표현하면 되묻기 답변 턴에서 앞 턴의 indoor를 덮어쓴다(D-053).
  environment: "indoor" | "outdoor" | "any" | null;

  // 동행
  companion: "solo" | "couple" | "friend" | "parent" | "child" | "pet" | null;

  // 예산
  budget: "free" | string | null;

  // 태그 (복수 가능)
  exclude_tags: string[];
  special_requirements: string[];


type PlaceType =
  | "attraction"         // 관광지 (contentTypeId: 12)
  | "cultural_facility"  // 문화시설 (contentTypeId: 14)
  | "festival"           // 축제/공연/행사 (contentTypeId: 15)
  | "leisure"            // 레포츠 (contentTypeId: 28)
  | "shopping"           // 쇼핑 (contentTypeId: 38)
  | "restaurant";        // 음식점/카페 (contentTypeId: 39)

type PlaceTag =
  // attraction 하위
  | "공원" | "궁궐" | "산" | "해변" | "호수" | "계곡"
  | "전망대" | "테마파크" | "동물원" | "수목원"
  | "사찰" | "성곽" | "마을" | "둘레길"
  | "전통체험" | "공예체험" | "웰니스"
  // cultural_facility 하위
  | "박물관" | "미술관" | "도서관" | "공연장" | "과학관" | "전시관"
  // festival 하위
  | "축제" | "전시회" | "공연" | "콘서트"
  // shopping 하위
  | "시장" | "쇼핑몰" | "면세점" | "백화점"
  // restaurant 하위
  | "한식" | "일식" | "중식" | "양식" | "카페" | "찻집" | "주점" | "분식";
}
```

---

## 3. 상태 구조

Conditions는 3층 구조로 분리되어 관리된다. B는 `user_conditions`와 `api_context`를
저장하고, `answer_conditions`는 A가 병합하여 생성한다 (B에 저장하지 않음).

### user_conditions

첫 RECOMMEND 요청 시 LLM이 추출한 최초 조건. 사용자가 직접 언급한 값만 채워지며,
언급하지 않은 위치·날씨는 null로 남는다 (API로 보충한 값은 `api_context`에 별도 저장).

```json
{
  "current_location": null,
  "search_center": "경복궁",
  "place_types": ["restaurant"],
  "place_tags": ["카페"],
  "weather": null,
  "weather_intent": "NO_MENTION",
  "transport": "walk",
  "max_travel_time": null,
  "time_available": null,
  "environment": "indoor",
  "companion": null,
  "budget": null,
  "exclude_tags": [],
  "special_requirements": []
}
```

MODIFY를 거치며 갱신된 현재 유효 조건. 추천 엔진은 이 값을 직접 쓰지 않고, 아래
`api_context`와 병합한 `answer_conditions`를 사용한다.

```json
{
  "current_location": null,
  "search_center": "경복궁",
  "place_types": ["restaurant"],
  "place_tags": ["카페"],
  "weather": "rain",
  "weather_intent": "AVOID",
  "transport": "walk",
  "max_travel_time": 15,
  "time_available": null,
  "environment": "indoor",
  "companion": null,
  "budget": "free",
  "exclude_tags": [],
  "special_requirements": []
}
```

### api_context

GPS·날씨 API로 확보한 값. `user_conditions`와 분리되어 저장되며, operations 대상이
아니다. A/Runtime이 API를 재호출한 뒤 B에 직접 갱신을 전달하는 별도 경로로 관리된다.

```json
{
  "gps_location": "37.5665,126.9780",
  "api_weather": "rain",
  "gps_location_updated_at": "2026-07-23T09:00:00+09:00",
  "api_weather_updated_at": "2026-07-23T09:00:00+09:00"
}
```

| 필드 | 필수 여부 | 유효기간 | 비고 |
|------|-----------|---------|------|
| `gps_location` | 필수 (세션 시작 전 확보) | 1시간 | GPS 알럿 → 확보 후 세션 시작 |
| `api_weather` | 선택 | 1시간 | 실패 시 null 유지 (이전 만료값 재사용 금지), 가중치 제외 |

### answer_conditions (병합 결과, B에 저장 안 함)

A가 `user_conditions` + `api_context`를 병합하여 매 실행 시 생성하는 최종 조건.
LLM 답변 생성과 추천 엔진에 전달되며, B에는 저장하지 않는다.

병합 우선순위:
- `current_location`: user_conditions 값이 있으면 사용, 없으면 api_context.gps_location
- `weather`: user_conditions 값이 있으면 사용, 없으면 api_context.api_weather (둘 다 없으면 가중치 제외)
- 나머지 필드: user_conditions 값 그대로

```json
{
  "current_location": "37.5665,126.9780",
  "search_center": "경복궁",
  "place_types": ["restaurant"],
  "place_tags": ["카페"],
  "weather": "rain",
  "weather_intent": "AVOID",
  "transport": "walk",
  "max_travel_time": 15,
  "time_available": null,
  "environment": "indoor",
  "companion": null,
  "budget": "free",
  "exclude_tags": [],
  "special_requirements": []
}
```

### missing_conditions

추천 실행에 필수이지만 아직 확보되지 않은 조건. (= 조건 부족 시 기본 정책, 이 문서가 소유)

| 필드 | 위치 | 필수 여부 | 미확보 시 처리 |
|------|------|-----------|---------------|
| `gps_location` | api_context | 필수 (세션 시작 전 확보) | GPS 알럿 → 확보 후 세션 시작 |
| `search_center` | user_conditions | 선택 | null이면 answer_conditions에서 gps_location 사용 |
| `place_types` | user_conditions | 선택 | 빈 배열이면 전체 유형 검색 |
| `weather` / `api_weather` | 병합 | 선택 | 둘 다 없으면 가중치 제외 |
| `weather_intent` | user_conditions | 선택 | 미언급은 `NO_MENTION`, 무관 명시는 `IGNORE`, 모호(null)하면 사용자에게 실내/야외 선호 추가 질문 |
| `concentration_intent` | user_conditions | 선택 | 결측/null이면 `concentration` Scoring 가중치 제외, 추가 질문 없음 (하드 필터에 관여하지 않으므로 weather_intent와 다름 — [concentration-conditions.md §2.1](./concentration-conditions.md#21-필드-정의) 참고) |
| `transport` | user_conditions | 선택 | 기본값 도보 기준 (default_transport: walk) |
| `max_travel_time` | user_conditions | 선택 | 없으면 기본 2km, 있으면 MVP 도보 기준 `분 × 0.07km`를 후보 수집 반경으로 적용(최소 0.3km, 최대 20km) |
| `budget` | user_conditions | 선택 | 예산 필터 미적용 |
| `companion` | user_conditions | 선택 | 동행자 필터 미적용 |
| 모든 조건 없음 ("추천해줘") | — | — | api_context.gps_location 확인 우선 |

```
missing_conditions 처리 흐름:
  ① 필수 조건(gps_location) 미확보 → 사용자에게 GPS 알럿 (추천 진행하지 않음)
  ② 선택 조건 미확보 → 기본값 적용 또는 해당 가중치 제외
```

---

## 4. 조건 변경 연산

### 4가지 처리 방식

| 처리 방식 | 설명 | 예시 |
|-----------|------|------|
| **Add** | 기존 조건은 유지하고 새로운 항목을 추가 | "주차 가능한 곳" → special_requirements에 "주차" 추가 |
| **Update** | 같은 필드의 값을 새로운 값으로 교체 | "카페 말고 맛집" → place_tags 교체 |
| **Remove** | 특정 조건을 명시적으로 해제 | "가격 상관없어" → budget을 null로 |
| **Keep** | 언급되지 않은 조건은 그대로 유지 | "더 가까운 곳" → 나머지 조건 전부 유지 |

### 핵심 원칙

```
1. 새로운 항목이면 추가 (Add)
2. 같은 필드이면 최신 값으로 교체 (Update)
3. 사용자가 명시적으로 해제하면 삭제 (Remove)
4. 언급하지 않은 조건은 그대로 유지 (Keep)
```

### 필드별 적용 방식

| 필드 | 단일/복수 | 변경 시 처리 | 해제 시 처리 |
|------|-----------|-------------|-------------|
| `current_location` | 단일 | Update | Remove → null (사용자 발화 기준, 필수 아님 — 필수인 것은 api_context.gps_location) |
| `search_center` | 단일 | Update | Remove → null (current_location 사용) |
| `place_types` | 복수 | Update (전체 교체) | Remove → 빈 배열 (전체 검색) |
| `place_tags` | 복수 | Add / Update / Remove | Remove → 빈 배열 |
| `weather` | 단일 | Update | Remove → null |
| `weather_intent` | 단일 | Update | Remove → null |
| `concentration_intent` | 단일 | Update | Remove → null (가중치 제외) |
| `transport` | 단일 | Update | Remove → null (기본값 walk 적용) |
| `max_travel_time` | 단일 | Update | Remove → null (기본 반경 적용) |
| `travel_origin` | 단일 | Update | Remove → null (D-067 기본값 적용) |
| `time_available` | 단일 | Update | Remove → null |
| `environment` | 단일 | Update | Remove → null |
| `companion` | 단일 | Update | Remove → null |
| `budget` | 단일 | Update | Remove → null (예산 필터 미적용) |
| `exclude_tags` | 복수 | Add | Remove (특정 항목 제거) |
| `special_requirements` | 복수 | Add | Remove (특정 항목 제거) |

### place_types와 place_tags의 차이

| 필드 | 변경 시 동작 | 이유 |
|------|-------------|------|
| `place_types` | **Update** (전체 교체) | "카페 말고 맛집"은 기존 유형을 대체하는 의도 |
| `place_tags` | **Update**(교체) 또는 명시적 추가/제거 | "공원도 추천해줘"는 새 유형으로 교체, "공원도 포함해줘"는 기존 태그에 추가 |

단, place_types가 교체될 때 소속되지 않는 place_tags의 정리는 B가 자동으로
수행하지 않는다. A가 place_types Update 연산과 함께 해당 place_tags에 대한
Remove 연산을 명시적으로 함께 전달한다.

---

## 5. 연산 적용 예시

### 예시 1: 조건 추가 (Add)

```
user_conditions:
  place_types: ["restaurant"]
  place_tags: ["카페"]
  special_requirements: []

사용자: "주차 가능한 곳"

처리:
  special_requirements: Add "주차"

결과:
  place_types: ["restaurant"]         (Keep)
  place_tags: ["카페"]                (Keep)
  special_requirements: ["주차"]      (Add)
```

### 예시 2: 조건 교체 (Update)

```
user_conditions:
  place_types: ["restaurant"]
  place_tags: ["카페"]
  budget: null

사용자: "카페 말고 맛집, 무료인 곳"

처리:
  place_tags: Remove "카페"  (명시적 제거)
  budget: Update "free"

결과:
  place_types: ["restaurant"]   (Keep)
  place_tags: []                (Remove "카페")
  budget: "free"                (Update)
```

### 예시 3: 조건 해제 (Remove)

```
user_conditions:
  budget: "free"
  environment: "indoor"

사용자: "가격 상관없어, 야외도 괜찮아"

처리:
  budget: Remove → null
  environment: Update "any"

결과:
  budget: null         (Remove)
  environment: "any"   (Update)
```

### 예시 4: 유지 (Keep)

```
user_conditions:
  search_center: "경복궁"
  place_types: ["restaurant"]
  place_tags: ["카페"]
  companion: "parent"
  budget: "free"

사용자: "더 가까운 곳"

처리:
  max_travel_time: Update (감소)

결과:
  search_center: "경복궁"      (Keep)
  place_types: ["restaurant"]   (Keep)
  place_tags: ["카페"]          (Keep)
  companion: "parent"           (Keep)
  budget: "free"                (Keep)
  max_travel_time: 15           (Update)
```

### 예시 5: place_types 교체에 따른 place_tags 정리 (A가 명시적으로 Remove 지시)

```
user_conditions:
  place_types: ["cultural_facility", "restaurant"]
  place_tags: ["박물관", "카페"]

사용자: "음식점 빼고 쇼핑으로"

처리:
  A가 place_types 교체를 감지하고, 소속되지 않는 place_tags("카페")에 대한
  Remove 연산을 함께 생성하여 전달 (B는 자동으로 정리하지 않음)

operations:
  { "op": "Update", "field": "place_types", "value": ["cultural_facility", "shopping"] }
  { "op": "Remove", "field": "place_tags", "value": ["카페"] }

결과:
  place_types: ["cultural_facility", "shopping"]   (Update)
  place_tags: ["박물관"]                            (A가 명시한 Remove "카페" 적용)
```

---

## 6. 상태 전이 다이어그램

```
[시작]
    ↓
RECOMMEND → user_conditions 최초 생성
    ↓
MODIFY → Add/Update/Remove 적용 → user_conditions 갱신 (나머지 Keep)
    ↓
MODIFY → Add/Update/Remove 적용 → user_conditions 갱신 (나머지 Keep)
    ↓
... (반복)
    ↓
새 RECOMMEND → user_conditions 재생성 (초기화)

※ 매 단계에서 추천 엔진은 user_conditions를 직접 쓰지 않고, api_context와
  병합한 answer_conditions(A가 생성, 미저장)를 사용한다.
```

---

## 7. MODIFY에서의 전체 처리 흐름

```
사용자 MODIFY 입력
    ↓
LLM이 condition_changes 추출 (변경된 필드만)
    ↓
각 필드별 연산 판별:
  - 새 값이 있으면 → Update 또는 Add
  - 명시적 해제면 → Remove
  - 언급 없으면 → Keep
    ↓
user_conditions에 반영
    ↓
place_types 변경 시 → A가 소속 안 되는 place_tags에 대한 Remove 연산을 함께 전달
    ↓
갱신된 user_conditions로 재추천 (실제로는 api_context와 병합한 answer_conditions 사용)
```

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-07-22 | 초안 작성 |
| v0.2 | 2026-07-23 | Conditions 3층 구조(user_conditions/api_context/answer_conditions) 반영, place_tags 자동 제거 서술을 A의 명시적 Remove로 수정, 용어 통일(current_conditions → user_conditions) |
| v0.3 | 2026-07-23 | 소유권 기반 문서 정리: "조건 부족 시 기본 정책"을 이 문서(missing_conditions)로 통합, weather_intent/transport/max_travel_time/budget/companion 기본값 추가. 다른 문서는 이 절을 참조하도록 정리 |
| v0.4 | 2026-07-27 | C 후보 수집 반경을 A 기준인 기본 2km 또는 `max_travel_time × 0.07km`로 정의하고 최소·최대 범위를 명시 |
| v0.5 | 2026-07-29 | `concentration_intent` 필드 추가(15번째 필드, weather_intent와 동일 패턴). §2 필드 정의, §3 missing_conditions, §4 필드별 적용 방식에 반영. 상세 설계는 [concentration-conditions.md](./concentration-conditions.md) 참고 |

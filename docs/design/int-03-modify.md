# INT-03: MODIFY

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.2 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-23 |

---

## 1. 정의

**목적:** 직전 추천 결과가 마음에 들지 않을 때, 기존 조건을 유지한 상태에서 일부 조건을 변경하거나 이전 추천을 제외하여 재추천한다.

**판별 기준:**
- 이전 추천 이력이 존재함 (필수 전제조건)
- 추천 결과에 대한 변경/거절/조건 추가 표현이 있음

**MODIFY가 아닌 경우:**
- "카페 추천해줘" (추천 이력 없음) → `RECOMMEND`
- "경복궁 오늘 열어?" → `INFO`
- "첫 번째랑 두 번째 중 어디가 좋아?" → `COMPARE`
- "처음부터 다시 추천해줘" (조건 전체 초기화 의도) → `RECOMMEND`

---

## 2. 전제조건

MODIFY는 반드시 이전 추천 이력이 존재해야 한다.

```
이전 추천 이력 있음
  → MODIFY 정상 처리

이전 추천 이력 없음
  → "아직 추천한 결과가 없어요. 어떤 장소를 찾고 계신가요?" 안내
  → RECOMMEND로 유도
```

---

## 3. 처리 흐름

```
사용자 입력
    ↓
LLM Intent 분류 → MODIFY
    ↓
LLM Structured Output → ModifyRequest 추출
    ↓
기존 Conditions에 변경사항 병합
    ↓
제외 장소 목록 갱신
    ↓
API 호출 (필요 시, 조건 변경된 경우)
    ↓
Hard Filter (기존 + 신규 제외 반영)
    ↓
Score 계산
    ↓
정렬 → 상위 3~5개 재추천
```

---

## 4. ModifyRequest 스키마

```typescript
interface ModifyRequest {
  // 수정 유형
  modify_type: ModifyType;

  // 조건 변경 (변경할 필드만 포함)
  condition_changes: Partial<Conditions> | null;
}
```

---

## 5. ModifyRequest 필드 정의

| 필드 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `modify_type` | ModifyType | 수정 유형 | `REJECT_ALL`, `CHANGE_CONDITION` |
| `condition_changes` | Partial\<Conditions\> \| null | 변경할 조건만 포함 | {"budget": "free"} |

---

## 6. modify_type 정의

### enum 목록

```typescript
type ModifyType =
  | "REJECT_ALL"        // 이전 추천 전체 거부
  | "CHANGE_CONDITION"; // 조건 변경
```

### 상세 정의

| modify_type | 의미 | 예시 입력 | 처리 |
|-------------|------|-----------|------|
| `REJECT_ALL` | 이전 추천 전체 불만족, 다른 결과 요청 | "다른 곳 보여줘", "전부 별로야", "다른 거 없어?" | 이전 추천 전체를 제외 목록에 추가 → 동일 조건으로 재추천 |
| `CHANGE_CONDITION` | 추천 조건 자체를 변경 | "더 가까운 곳", "무료인 곳으로", "실내로 바꿔줘" | 조건 병합 → 이전 추천 제외 → 재추천 |

---

## 7. 조건 병합 규칙

### 기본 원칙

```
1. 명시된 필드만 변경 (condition_changes에 포함된 것만)
2. 언급되지 않은 조건은 그대로 유지
3. 제외 장소는 누적
4. place_types 교체 시 소속되지 않는 place_tags는 B가 자동으로 제거하지 않는다.
   A가 place_types Update와 함께 해당 place_tags에 대한 Remove를 명시적으로 전달한다.
```

### 필드별 병합 동작

| 필드 | 병합 방식 | 예시 |
|------|-----------|------|
| `current_location` | Update | user_conditions 내 사용자 발화 반영 기준 — "나 지금 홍대야" → 위치 변경 (GPS 보충값은 api_context.gps_location 별도 관리) |
| `search_center` | Update | "경복궁 말고 인사동 근처로" |
| `place_types` | 전체 교체 | "카페 말고 맛집" → ["restaurant"] |
| `place_tags` | 최종 목록으로 교체, 명시적 추가/제거 시만 누적·제거 | "공원도 추천" → 교체 / "공원도 포함" → 추가 |
| `weather` | Update | user_conditions 내 사용자 발화 반영 기준 — "비 그쳤어" → weather 변경 (API 갱신값은 api_context.api_weather 별도 관리) |
| `weather_intent` | Update | "야외도 괜찮아" → IGNORE |
| `transport` | Update | "차로 갈게" → car |
| `max_travel_time` | Update | "10분 이내로" → 10 |
| `time_available` | Update | "30분밖에 없어" → 30 |
| `environment` | Update | "야외로" → outdoor |
| `companion` | Update | "아이도 같이 가" → child |
| `budget` | 교체 또는 제거 | "무료만" → "free" / "가격 상관없어" → null |
| `exclude_tags` | Add / Remove | "붐비는 곳 빼줘" → 추가 |
| `special_requirements` | Add / Remove | "주차 가능한 곳" → 추가 |

### place_types 교체 시 place_tags 정리

```
기존 상태:
  place_types: ["cultural_facility", "restaurant"]
  place_tags: ["박물관", "카페"]

사용자: "음식점 빼고 쇼핑으로"

A가 place_types 교체를 감지하여, 소속되지 않는 place_tags에 대한 Remove를
명시적으로 함께 전달 (B는 자동으로 정리하지 않음):
  operations:
    { "op": "Update", "field": "place_types", "value": ["cultural_facility", "shopping"] }
    { "op": "Remove", "field": "place_tags", "value": ["카페"] }

변경 후:
  place_types: ["cultural_facility", "shopping"]  (restaurant → shopping)
  place_tags: ["박물관"]  (A가 명시한 Remove "카페" 적용)
```

---

## 8. 제외 장소 관리

### 제외 목록 구조

```typescript
interface ExcludedPlaces {
  // 이전 추천에서 표시되었거나 거절된 장소
  excluded_place_ids: string[];
}
```

### 제외 동작

| modify_type | 제외 동작 |
|-------------|-----------|
| `REJECT_ALL` | 이전 추천 결과 전체의 contentId를 excluded_place_ids에 추가 |
| `CHANGE_CONDITION` | 이전 추천 결과 전체를 excluded_place_ids에 추가 (조건이 바뀌었으므로 새 결과 우선) |

### 제외 목록 누적

```
1차 추천: [A, B, C] 표시
    ↓
사용자: "다른 곳 보여줘" (REJECT_ALL)
    → excluded: [A, B, C]
    ↓
2차 추천: [D, E, F] 표시 (A, B, C 제외)
    ↓
사용자: "더 가까운 곳" (CHANGE_CONDITION)
    → excluded: [A, B, C, D, E, F]
    ↓
3차 추천: [G, H, I] 표시 (A~F 제외)
```

### 제외 목록 초기화 조건

제외 목록 초기화는 B가 조건 변경을 감지해서 자동으로 판단하지 않는다. A가
reset_scope(soft/history/full/null)를 명시적으로 판정하여 operations와 함께
전달해야 초기화가 일어난다.

```
다음 경우 A가 reset_scope: "history"를 판정하여 함께 전달:
- search_center가 변경됨 (새로운 지역 검색)
  예) { "reset_scope": "history", "operations": [{ "op": "Update", "field": "search_center", "value": "인사동" }] }
- place_types가 완전히 다른 유형으로 교체됨

다음 경우 A가 reset_scope: "full"을 판정하여 함께 전달:
- 사용자가 명시적으로 "처음부터 다시" 요청

다음 경우 reset_scope를 null로 전달 (제외 목록 유지):
- 같은 지역 내 조건 세부 변경 (budget, environment 등)
- place_tags 추가/제거
- REJECT_ALL
```

---

## 9. "더 ~한 곳" 처리

사용자가 상대적 비교 표현을 사용할 때의 처리 규칙.

### 상대적 표현 매핑

| 입력 | 해석 | condition_changes |
|------|------|-------------------|
| "더 가까운 곳" | 검색 반경 축소 | max_travel_time 감소 |
| "더 먼 곳도 괜찮아" | 검색 반경 확대 | max_travel_time 증가 |
| "더 싼 곳" | 예산 하향 | budget 하향 조정 |

### "더 가까운 곳" 세부 처리

```
현재 max_travel_time이 있는 경우:
  → 현재값의 50%로 축소 (최소 5분)
  예) 30분 → 15분

현재 max_travel_time이 null인 경우 (기본 반경 2km):
  → 기본 반경의 50%로 축소 (1km)

검색 반경이 이미 최소인 경우:
  → "현재 범위에서 가장 가까운 곳을 보여드리고 있어요" 안내
```

### "더 먼 곳도 괜찮아" 세부 처리

```
현재 검색 반경에서 1km 확장
  예) 2km → 3km → 4km (최대 5km)
```

---

## 10. 재추천 시 API 호출 판단

모든 MODIFY가 새로운 API 호출을 필요로 하는 것은 아니다.

### API 재호출이 필요한 경우

| 변경 사항 | 이유 |
|-----------|------|
| search_center 변경 | 검색 중심 좌표가 달라짐 |
| place_types 변경 | contentTypeId가 달라짐 |
| max_travel_time 증가 (반경 확대) | 더 넓은 범위 검색 필요 |
| user_conditions.weather 변경 (사용자 발화) | 날씨 점수 재계산 필요 — api_context.api_weather 재호출과는 무관 (1시간 만료 기반 별도 경로, [conditions-schema.md](./conditions-schema.md) 3절 참고) |

### 기존 후보 내에서 재정렬로 충분한 경우

| 변경 사항 | 이유 |
|-----------|------|
| REJECT_ALL | 기존 후보에서 제외 후 다음 순위 표시 |
| budget 변경 | 기존 후보에 Hard Filter 재적용 |
| environment 변경 | 기존 후보에 Hard Filter 재적용 |
| max_travel_time 감소 (반경 축소) | 기존 후보에 거리 필터 재적용 |

### 판단 로직

```
condition_changes에 [search_center, place_types] 포함
  → API 재호출

condition_changes에 [max_travel_time 증가] 포함
  → API 재호출 (더 넓은 범위 필요)

그 외
  → 기존 후보 풀에서 필터링 + 재정렬
  → 후보 부족 시에만 API 재호출
```

---

## 11. 후보 부족 처리

MODIFY 후 추천 가능한 후보가 부족한 경우.

### 부족 판단 기준

```
추천 가능 후보 < 3개 (minimum_recommendation_count)
```

### 처리 순서

```
① 기존 후보 풀에서 조건 완화 없이 검색
    → 3개 이상이면 추천

② 3개 미만이면 사용자에게 선택지 제공:
    - "검색 범위를 넓혀볼까요?" (반경 확대)
    - "다른 종류의 장소도 포함할까요?" (place_types 확장)
    - "운영시간을 확인할 수 없는 장소도 볼까요?" (unknown 포함)

③ 시스템이 임의로 조건을 완화하지 않음
```

---

## 12. LLM 추출 예시

### REJECT_ALL

| 입력 | modify_type | condition_changes |
|------|-------------|-------------------|
| "다른 곳 보여줘" | REJECT_ALL | null |
| "전부 별로야" | REJECT_ALL | null |
| "다른 거 없어?" | REJECT_ALL | null |
| "다 마음에 안 들어" | REJECT_ALL | null |

### CHANGE_CONDITION

| 입력 | modify_type | condition_changes |
|------|-------------|-------------------|
| "더 가까운 곳" | CHANGE_CONDITION | {max_travel_time: 감소} |
| "무료인 곳으로" | CHANGE_CONDITION | {budget: "free"} |
| "실내로 바꿔줘" | CHANGE_CONDITION | {environment: "indoor"} |
| "카페 말고 맛집" | CHANGE_CONDITION | {place_types: ["restaurant"], place_tags: [remove "카페"]} |
| "야외도 괜찮아" | CHANGE_CONDITION | {environment: "outdoor"} |
| "주차 가능한 곳" | CHANGE_CONDITION | {special_requirements: [add "주차"]} |
| "경복궁 말고 인사동 근처로" | CHANGE_CONDITION | {search_center: "인사동"} |
| "걸어서 갈 수 있는 곳" | CHANGE_CONDITION | {transport: "walk", max_travel_time: 15} |
| "예산 상관없어" | CHANGE_CONDITION | {budget: null} |

### 전체 JSON 예시

```json
{
  "intent": "MODIFY",
  "modify_request": {
    "modify_type": "CHANGE_CONDITION",
    "condition_changes": {
      "budget": "free",
      "environment": "indoor"
    }
  }
}
```

```json
{
  "intent": "MODIFY",
  "modify_request": {
    "modify_type": "REJECT_ALL",
    "condition_changes": null
  }
}
```

---

## 13. 상태 변화 예시 (전체 시나리오)

```
[1] 사용자: "경복궁 근처 카페 추천해줘"
    Intent: RECOMMEND
    Conditions:
      current_location: GPS
      search_center: "경복궁"
      place_types: ["restaurant"]
      place_tags: ["카페"]
    추천 결과: [A카페, B카페, C카페]

[2] 사용자: "다른 곳 보여줘"
    Intent: MODIFY
    modify_type: REJECT_ALL
    Conditions: (변경 없음)
    제외: [A, B, C]
    추천 결과: [D카페, E카페, F카페]

[3] 사용자: "무료인 곳으로"
    Intent: MODIFY
    modify_type: CHANGE_CONDITION
    condition_changes: {budget: "free"}
    Conditions (병합 후):
      search_center: "경복궁"  (유지)
      place_types: ["restaurant"]  (유지)
      place_tags: ["카페"]  (유지)
      budget: "free"  (추가)
    제외: [A, B, C, D, E, F]
    추천 결과: [G카페, H카페]

[4] 사용자: "카페 말고 맛집으로"
    Intent: MODIFY
    modify_type: CHANGE_CONDITION
    condition_changes: {place_tags: [remove "카페"]}
    Conditions (병합 후):
      search_center: "경복궁"  (유지)
      place_types: ["restaurant"]  (유지)
      place_tags: []  ("카페" 제거)
      budget: "free"  (유지)
    제외: [A, B, C, D, E, F, G, H]
    추천 결과: [I맛집, J맛집, K맛집]

[5] 사용자: "인사동 근처로 바꿔줘"
    Intent: MODIFY
    modify_type: CHANGE_CONDITION
    condition_changes: {search_center: "인사동"}
    Conditions (병합 후):
      search_center: "인사동"  (변경)
      place_types: ["restaurant"]  (유지)
      place_tags: []  (유지)
      budget: "free"  (유지)
    제외: 초기화 (A가 reset_scope: "history" 판정하여 함께 전달)
    추천 결과: [L맛집, M맛집, N맛집]
```

---

## 14. 경계 사례

| 입력 | 이전 추천 | 판정 | 이유 |
|------|-----------|------|------|
| "다른 곳 보여줘" | 있음 | MODIFY (REJECT_ALL) | 기존 결과 거절 |
| "다른 곳 보여줘" | 없음 | → 안내 후 RECOMMEND | 전제조건 미충족 |
| "카페 말고 맛집" | 있음 (카페 추천 상태) | MODIFY (CHANGE_CONDITION) | place_tags 변경 |
| "카페 추천해줘" | 없음 | RECOMMEND | 새로운 추천 |
| "처음부터 다시 추천해줘" | 있음 | RECOMMEND | 조건 전체 초기화 의도 |
| "더 가까운 곳" | 있음 | MODIFY (CHANGE_CONDITION) | 상대적 조건 변경 |
| "더 가까운 곳" | 없음 | RECOMMEND | 새로운 추천으로 처리 |

---

## 15. MODIFY와 RECOMMEND의 구분 기준

| 기준 | MODIFY | RECOMMEND |
|------|--------|-----------|
| 이전 추천 이력 | 필수 | 불필요 |
| 조건 연속성 | 기존 조건 유지 + 부분 변경 | 새로운 조건 세트 |
| 제외 장소 | 누적 | 없음 (또는 초기화) |
| 사용자 의도 | "지금 결과를 기반으로 바꿔줘" | "새로 추천해줘" |
| 대표 표현 | "다른 곳", "말고", "빼고", "더 ~한" | "추천해줘", "알려줘", "갈 만한 곳" |

---

## 16. 관련 문서

- [INT-01: RECOMMEND](./int-01-recommend.md) — Conditions 스키마 및 추천 처리 상세
- [INT-04: COMPARE](./int-04-compare.md) — 후보 비교
- [추천 점수 설계](./recommendation-scoring.md) — 가중치 및 점수 계산 상세(현재x)
- [MVP 설계 기준서](./mvp-design-spec.md) — 후보 부족 시 처리 원칙(현재x)

---

## 17. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-07-22 | 초안 작성 |
| v0.2 | 2026-07-23 | reset_scope를 B 자동 감지가 아닌 A의 명시적 판정으로 수정(8·13절), place_tags 자동 제거 서술 수정(7절), weather API 재호출 경로 분리 서술(10절) |

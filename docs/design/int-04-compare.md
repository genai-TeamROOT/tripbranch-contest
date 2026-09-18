# INT-04: COMPARE

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.2 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-23 |

---

## 1. 정의

**목적:** 추천 결과 중 여러 후보를 비교하여 사용자의 선택을 돕는다.

**판별 기준:**
- 이전 추천 결과가 2개 이상 존재함 (필수 전제조건)
- 비교/선택 표현이 있음 ("어디가 좋아?", "뭐가 나아?", "차이가 뭐야?")

**COMPARE가 아닌 경우:**
- "다른 곳 보여줘" → `MODIFY` (거절 의도)
- "경복궁 오늘 열어?" → `INFO` (특정 장소 정보 조회)
- "카페 추천해줘" → `RECOMMEND` (새로운 추천)

---

## 2. 전제조건

COMPARE는 반드시 이전 추천 결과가 2개 이상 존재해야 한다.

```
추천 결과 2개 이상 있음
  → COMPARE 정상 처리

추천 결과 1개만 있음
  → "비교할 장소가 더 필요해요. 다른 추천을 볼까요?" 안내
  → RECOMMEND 또는 MODIFY로 유도

추천 결과 없음
  → "먼저 장소를 추천해드릴까요?" 안내
  → RECOMMEND로 유도
```

---

## 3. 처리 흐름

```
사용자 입력
    ↓
LLM Intent 분류 → COMPARE
    ↓
LLM Structured Output → CompareRequest 추출
    ↓
비교 대상 장소 식별 (추천 이력에서)
    ↓
비교 기준에 따른 데이터 조회/계산
    ↓
비교 결과 생성
    ↓
사용자에게 비교 정보 제공
```

---

## 4. CompareRequest 스키마

```typescript
interface CompareRequest {
  // 비교 대상
  targets: "all" | number[];

  // 비교 기준
  criteria: CompareCriteria;
}
```

---

## 5. CompareRequest 필드 정의

| 필드 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `targets` | "all" \| number[] | 비교 대상 추천 결과 번호 (1-indexed) 또는 전체 | "all", [1, 2], [1, 3] |
| `criteria` | CompareCriteria | 비교 기준 | `distance`, `time`, `overall` |

---

## 6. targets 정의

| 값 | 의미 | 예시 입력 |
|----|------|-----------|
| `"all"` | 현재 추천 결과 전체 비교 | "어디가 제일 좋아?", "뭐가 나아?" |
| `[1, 2]` | 1번과 2번 비교 | "첫 번째랑 두 번째 중에?", "1번 2번 비교해줘" |
| `[1, 3]` | 1번과 3번 비교 | "첫 번째랑 세 번째 차이가 뭐야?" |

### targets 결정 규칙

번호(1-indexed)는 `get_session_context` 반환값의 `shown_place_ids`(현재 노출된
장소 ID 목록, 순서 보장)를 기준으로 해석한다. 예) `[1, 2]` → `shown_place_ids[0]`,
`shown_place_ids[1]`.

```
사용자가 번호를 명시한 경우
  → shown_place_ids에서 해당 순번의 장소 사용

사용자가 번호를 명시하지 않은 경우 ("어디가 좋아?")
  → targets: "all" (shown_place_ids 전체 비교)

지정한 번호가 shown_place_ids 범위를 초과하는 경우
  → "추천 결과는 N개까지 있어요. 몇 번을 비교할까요?" 안내
```

---

## 7. criteria 정의

### enum 목록

```typescript
type CompareCriteria =
  | "distance"    // 거리 비교
  | "time"        // 남은 운영시간 비교
  | "overall";    // 종합 비교
```

### 상세 정의

| criteria | 정의 | 비교 데이터 | 예시 입력 |
|----------|------|------------|-----------|
| `distance` | 검색 기준점으로부터의 거리 비교 | 직선거리 (km) | "어디가 더 가까워?", "거리 차이?" |
| `time` | 남은 운영시간 비교 | 영업 종료까지 남은 시간 (분) | "어디가 더 오래 열어?", "몇 시까지 해?" |
| `overall` | 종합 추천 점수 비교 | 추천 점수 항목별 비교 | "어디가 더 좋아?", "뭐가 나아?" |

### criteria 결정 규칙

```
사용자가 비교 기준을 명시한 경우
  → 해당 criteria 사용

사용자가 기준 없이 "어디가 좋아?"만 한 경우
  → criteria: "overall" (종합 비교)
```

---

## 8. 비교 응답 구조

### 정상 응답

```json
{
  "intent": "COMPARE",
  "comparison": {
    "targets": [1, 2],
    "criteria": "distance",
    "results": [
      {
        "rank": 1,
        "place_name": "A카페",
        "value": "0.3km",
        "raw_value": 0.3
      },
      {
        "rank": 2,
        "place_name": "B카페",
        "value": "0.8km",
        "raw_value": 0.8
      }
    ],
    "summary": "A카페가 B카페보다 약 0.5km 더 가까워요."
  }
}
```

### 종합 비교 응답

```json
{
  "intent": "COMPARE",
  "comparison": {
    "targets": "all",
    "criteria": "overall",
    "results": [
      {
        "rank": 1,
        "place_name": "A카페",
        "distance_km": 0.3,
        "remaining_minutes": 180,
        "environment_type": "indoor"
      },
      {
        "rank": 2,
        "place_name": "B카페",
        "distance_km": 0.8,
        "remaining_minutes": 90,
        "environment_type": "indoor"
      },
      {
        "rank": 3,
        "place_name": "C카페",
        "distance_km": 0.5,
        "remaining_minutes": 240,
        "environment_type": "mixed"
      }
    ],
    "summary": "거리는 A카페가 가장 가깝고, 운영시간은 C카페가 가장 여유 있어요."
  }
}
```

---

## 9. 비교 항목별 데이터 소스

| criteria | 데이터 소스 | 표시 형식 |
|----------|------------|-----------|
| `distance` | 추천 시 계산한 직선거리 | "약 0.3km" |
| `time` | 추천 시 계산한 남은 운영시간 | "약 3시간 남음", "운영시간 확인 불가" |
| `overall` | 거리 + 운영시간 + 환경유형 종합 | 항목별 나열 비교 |

### 데이터 없음 처리

```
운영시간을 확인할 수 없는 장소:
  → time 비교 시: "운영시간 확인 불가" 표시
  → overall 비교 시: 해당 항목 "확인 불가"로 표시, 나머지 항목만 비교
```

---

## 10. 비교 후 후속 안내

비교 결과 제공 후 자연스러운 후속 동작을 안내한다.

```
비교 결과 제공 후:
  → "이 중에서 선택하시겠어요?"
  → "다른 조건으로 다시 추천받을 수도 있어요."

사용자가 장소를 선택한 경우:
  → 해당 장소의 상세 정보 제공 (INFO로 연계)

사용자가 "둘 다 별로" 한 경우:
  → MODIFY로 전환
```

---

## 11. LLM 추출 예시

| 입력 | targets | criteria |
|------|---------|----------|
| "어디가 더 가까워?" | "all" | distance |
| "첫 번째랑 두 번째 중 어디가 좋아?" | [1, 2] | overall |
| "1번이랑 3번 거리 차이?" | [1, 3] | distance |
| "어디가 더 오래 열어?" | "all" | time |
| "뭐가 나아?" | "all" | overall |
| "두 번째랑 세 번째 비교해줘" | [2, 3] | overall |
| "가장 가까운 곳은?" | "all" | distance |

### 전체 JSON 예시

```json
{
  "intent": "COMPARE",
  "compare_request": {
    "targets": [1, 2],
    "criteria": "distance"
  }
}
```

```json
{
  "intent": "COMPARE",
  "compare_request": {
    "targets": "all",
    "criteria": "overall"
  }
}
```

---

## 12. 경계 사례

| 입력 | 이전 추천 | 판정 | 이유 |
|------|-----------|------|------|
| "어디가 좋아?" | 2개 이상 | COMPARE (overall) | 비교 의도 |
| "어디가 좋아?" | 1개 | → 안내 후 RECOMMEND/MODIFY 유도 | 전제조건 미충족 |
| "어디가 좋아?" | 없음 | → 안내 후 RECOMMEND 유도 | 전제조건 미충족 |
| "첫 번째가 좋아, 거기 정보 알려줘" | 2개 이상 | INFO | 선택 + 정보 조회 의도 |
| "둘 다 별로야" | 2개 이상 | MODIFY (REJECT_ALL) | 거절 의도 |
| "더 가까운 곳 없어?" | 2개 이상 | MODIFY (CHANGE_CONDITION) | 조건 변경 의도 |
| "경복궁이랑 창덕궁 중 어디가 좋아?" | 추천과 무관 | INFO | 추천 결과가 아닌 특정 장소 비교 → INFO로 각각 조회 |

### "추천 결과 외 장소 비교" 처리

```
사용자가 추천 결과가 아닌 임의의 장소를 비교 요청한 경우:
  예) "경복궁이랑 창덕궁 중 어디가 좋아?"

MVP 처리:
  → "추천 결과 중에서 비교해드릴 수 있어요. 
     경복궁이나 창덕궁의 정보를 따로 확인해드릴까요?"
  → INFO로 유도
```

---

## 13. MVP 제한사항

다음 항목은 MVP에서 처리하지 않는다:

- 추천 결과 외 임의 장소 간 비교
- 리뷰/평점 기반 비교
- 혼잡도 비교
- 분위기/취향 기반 비교
- 가격대 상세 비교 (입장료 등 — 데이터가 비정형)
- 3개 초과 장소 동시 상세 비교

비교는 추천 시 이미 계산된 데이터(거리, 운영시간, 환경유형)만을 기반으로 수행한다.

---

## 14. 관련 문서

- [INT-01: RECOMMEND](./int-01-recommend.md) — 추천 결과 및 점수 계산
- [INT-02: INFO](./int-02-info.md) — 비교 후 장소 상세 조회 연계
- [INT-03: MODIFY](./int-03-modify.md) — 비교 후 거절 시 재추천
- [추천 점수 설계](./recommendation-scoring.md) — 비교에 사용되는 점수 항목(현재x)
- [MVP 설계 기준서](./mvp-design-spec.md) — 추천 결과 표시 항목(현재x)

---

## 15. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-07-22 | 초안 작성 |
| v0.2 | 2026-07-23 | targets 번호 해석이 get_session_context의 shown_place_ids 기준임을 명시(6절) |
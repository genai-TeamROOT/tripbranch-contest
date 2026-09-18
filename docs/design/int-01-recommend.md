# INT-01: RECOMMEND

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.5 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-08-05 |

---

## 1. 정의

**목적:** 사용자의 현재 상황과 조건에 맞는 새로운 장소를 추천한다.

**판별 기준:**
- 추천/알려줘/갈 만한 곳 등 요청 표현이 있음
- 특정 장소를 지정하지 않고 조건만 제시함
- 이전 추천 이력이 없는 상태에서의 장소 요청

**RECOMMEND가 아닌 경우:**
- "경복궁 오늘 열어?" → `INFO` (특정 장소 정보 요청)
- "다른 곳 보여줘" (이전 추천 존재 시) → `MODIFY`
- "첫 번째랑 두 번째 중 어디가 좋아?" → `COMPARE`

---

## 2. 처리 흐름

```
사용자 입력
    ↓
LLM Intent 분류 → RECOMMEND
    ↓
LLM Structured Output → Conditions 추출
    ↓
위치 확정 (current_location + search_center 좌표 변환)
    ↓
사용자 확인/수정
    ↓
API 호출 (장소 + 날씨(조건부), 병렬)
    ↓
Hard Filter (필수 조건 미충족 제외)
    ↓
Score 계산 (가중치 적용)
    ↓
정렬 → 상위 3~5개 추천
```

---

## 3. Conditions 스키마

Conditions는 3층 구조로 관리된다. 상세 스키마는
[conditions-schema.md](./conditions-schema.md)를 따른다.

```
① user_conditions  — 사용자 발화에서 추출한 값만 저장 (아래 인터페이스, B 저장)
② api_context       — GPS·날씨 API로 확보한 값, 별도 구조로 B 저장 (operations 대상 아님)
③ answer_conditions — ①+②를 병합한 최종 조건, A가 생성 (B에 저장 안 함)
                      추천 엔진에는 answer_conditions가 전달된다
```

14개 필드(UserConditions) 정의, PlaceType/PlaceTag enum, 필드별 허용 연산은
[conditions-schema.md § 2. Conditions 필드 정의](./conditions-schema.md#2-conditions-필드-정의) 및
[§ 4. 조건 변경 연산](./conditions-schema.md#4-조건-변경-연산)을 참조한다.

---

## 4. Conditions 필드 정의

14개 필드의 타입·설명·허용 연산 전문은
[conditions-schema.md § 2. Conditions 필드 정의](./conditions-schema.md#2-conditions-필드-정의) 및
[§ 4. 조건 변경 연산 § 필드별 적용 방식](./conditions-schema.md#필드별-적용-방식)을 참조한다.

---

## 5. 위치 처리 상세

### 필드 역할 구분

| 필드 | 역할 | 데이터 소스 |
|------|------|------------|
| `current_location` | 사용자가 지금 있는 곳 | ① 사용자 직접 입력 |
| `search_center` | 장소 검색 반경의 중심점 | ① 사용자 발화에서 추출 ② 없으면 current_location과 동일 |

### 위치 확보 순서

```
current_location 확보:
  ① 사용자가 "나 지금 ~~야"로 현재 위치를 명시 → GPS 대신 사용

search_center 결정:
  ① 사용자가 "~~ 근처", "~~ 주변", "~~ 가려는데"로 목적지 명시
     → search_center = 해당 장소
  ② 목적지 언급 없음
     → search_center = null → current_location을 기준점으로 사용
```

### 동작 케이스

| 케이스 | 입력 | current_location | search_center | 검색 기준 | 거리 계산 기준 |
|--------|------|-----------------|---------------|-----------|--------------|
| 근처 검색 | "근처 카페 추천" | GPS 좌표 | null | current_location | current_location → 후보 |
| 목적지 지정 | "경복궁 근처 맛집" | GPS 좌표 | "경복궁" | search_center | search_center → 후보 |
| 현재 위치 직접 입력 | "나 지금 성수야. 카페 추천" | "성수" | null | current_location | current_location → 후보 |
| 출발지+목적지 | "종로 가려는데 근처 볼거리" | GPS 좌표 | "종로" | search_center | search_center → 후보 |

### API 호출 시 위치 사용

| 용도 | 사용 필드 |
|------|-----------|
| `locationBasedList2` (mapX, mapY, radius) | search_center 좌표 (null이면 current_location) |
| 거리 계산 (후보까지의 거리) | search_center 좌표 (null이면 current_location) |
| 날씨 API 호출 | `weather_intent=NO_MENTION`일 때만 current_location 좌표 |

### 좌표 변환

```
문자열 위치 → 좌표 변환:
  ① 정확한 주소 → 지오코딩 API
  ② 대표 장소명 (경복궁, 강남역) → 장소 검색 → 대표 좌표 사용
  ③ 지역명 (성수동, 종로) → 해당 지역 중심 좌표

검색 결과 처리:
  - 결과 1개 → 자동 선택
  - 결과 여러 개 → 사용자에게 후보 목록 제공
  - 결과 없음 → 더 구체적으로 입력 요청
```

---

## 6. place_types 정의

enum 목록은 [conditions-schema.md § 2. Conditions 필드 정의](./conditions-schema.md#2-conditions-필드-정의)를 참조한다.

### 상세 정의

| place_type | contentTypeId | 정의 | 포함 범위 | MVP |
|-----------|--------------|------|-----------|-----|
| `attraction` | 12 | 관광지·자연·역사·체험 | 공원, 궁궐, 자연경관, 전망대, 테마파크, 체험관광, 사찰 | ✅ |
| `cultural_facility` | 14 | 문화시설 | 박물관, 미술관, 도서관, 공연장, 과학관, 전시관 | ✅ |
| `festival` | 15 | 축제/공연/행사 | 축제, 전시회, 공연, 콘서트, 스포츠경기 | ✅ |
| `leisure` | 28 | 레포츠 | 자전거, 수상레저, 스키, 캠핑, 골프 | ⬜ 심화 |
| `shopping` | 38 | 쇼핑 | 시장, 쇼핑몰, 면세점, 백화점, 아웃렛 | ✅ |
| `restaurant` | 39 | 음식점·카페·주점 | 한식, 양식, 카페, 찻집, 바, 분식 | ✅ |

### API 호출 매핑

```python
PLACE_TYPE_TO_CONTENT_TYPE_ID = {
    "attraction": "12",
    "cultural_facility": "14",
    "festival": "15",
    "leisure": "28",
    "shopping": "38",
    "restaurant": "39",
}
```

### 복수 선택 시 API 호출 전략

```
place_types 개수에 따른 전략:

1개
  → locationBasedList2(contentTypeId=해당값) 단일 호출

2~3개
  → 각 contentTypeId별 병렬 호출
  → 결과 병합 → 통합 점수 계산 → 정렬

  예) place_types: ["cultural_facility", "restaurant"]
  → locationBasedList2(contentTypeId=14) 호출
  → locationBasedList2(contentTypeId=39) 호출
  → 병합 후 정렬

0개 (빈 배열, 전체 검색)
  → contentTypeId 미지정으로 전체 조회
  → 응답에서 contenttypeid 기준 후처리 필터링

4개 이상
  → contentTypeId 미지정으로 전체 조회
  → 응답에서 해당 유형만 필터링
```

### 빈 배열의 의미

```
place_types: []  → 전체 유형 검색 (사용자가 유형을 지정하지 않음)
place_types: ["restaurant"]  → 음식점/카페만 검색
```

---

## 7. place_tags 정의

enum 목록(MVP)은 [conditions-schema.md § 2. Conditions 필드 정의](./conditions-schema.md#2-conditions-필드-정의)를 참조한다.

### place_tags → place_type 소속 매핑 (수정)
 - '신분류체계정보 관광타입정보 연계 정의서.xlsx' 참고 


| place_tag | 소속 place_type | 신분류 코드 (참고) |
|-----------|----------------|-------------------|
| 공원 | attraction | VE030100~VE030500 |
| 궁궐 | attraction | HS010100 |
| 산 | attraction | NA010100 |
| 해변 | attraction | NA020900 |
| 호수 | attraction | NA020200 |
| 계곡 | attraction | NA010400 |
| 전망대 | attraction | VE010200 |
| 테마파크 | attraction | VE020100 |
| 동물원 | attraction | VE020300 |
| 수목원 | attraction | NA040700 |
| 사찰 | attraction | HS030100 |
| 성곽 | attraction | HS010200 |
| 마을 | attraction | VE040200 |
| 둘레길 | attraction | VE040300 |
| 전통체험 | attraction | EX010100 |
| 공예체험 | attraction | EX020100~EX020400 |
| 웰니스 | attraction | EX050100~EX050800 |
| 박물관 | cultural_facility | VE070100 |
| 미술관 | cultural_facility | VE070600 |
| 도서관 | cultural_facility | VE090300 |
| 공연장 | cultural_facility | VE060100 |
| 과학관 | cultural_facility | VE070500 |
| 전시관 | cultural_facility | VE070300 |
| 축제 | festival | EV010100~EV010600 |
| 전시회 | festival | EV030100 |
| 공연 | festival | EV020100~EV021000 |
| 콘서트 | festival | EV020700 |
| 시장 | shopping | SH060100~SH060200 |
| 쇼핑몰 | shopping | SH020100 |
| 면세점 | shopping | SH040100~SH040300 |
| 백화점 | shopping | SH010100 |
| 한식 | restaurant | FD010100~FD010200 |
| 일식 | restaurant | FD020200 |
| 중식 | restaurant | FD020100 |
| 양식 | restaurant | FD020300 |
| 카페 | restaurant | FD050100 |
| 찻집 | restaurant | FD050200 |
| 주점 | restaurant | FD040100~FD040500 |
| 분식 | restaurant | FD030400 |


### place_tags 처리 규칙

```
규칙 1: place_tags가 있으면 소속 place_type을 자동 추론 가능
  예) place_tags: ["박물관"] → place_types에 "cultural_facility" 자동 포함

규칙 2: place_types + place_tags 둘 다 있으면 place_tags로 세부 필터링
  예) place_types: ["attraction"], place_tags: ["공원"]
  → attraction 조회 후 공원만 필터

규칙 3: place_types만 있고 place_tags 없으면 해당 유형 전체 검색
  예) place_types: ["restaurant"], place_tags: []
  → 음식점/카페 전체

규칙 4: place_tags만 있고 place_types 없으면 매핑에서 place_type 자동 설정
  예) place_tags: ["카페", "박물관"]
  → place_types: ["restaurant", "cultural_facility"] 자동 설정

규칙 5: place_tags 언급 순서가 선호 순위
  예) "박물관이나 카페" → rank_1: 박물관, rank_2: 카페
```

---

## 8. weather_intent 판별

| 값 | 의미 | 예시 입력 | 추천 정책 |
|----|------|-----------|-----------|
| `AVOID` | 날씨를 피하고 싶음 | "비 오는데 갈 곳", "더운데 시원한 곳" | environment=indoor 설정 |
| `ENJOY` | 날씨를 즐기고 싶음 | "눈 오는 거리 걷고 싶어", "단풍 보러" | environment=outdoor 설정 |
| `NO_MENTION` | 날씨 언급 없음 | "경복궁 근처 카페 추천해줘" | 날씨 API 호출 후 API 값으로 계산 |
| `IGNORE` | 날씨 무관·감수를 명시 | "날씨 상관없이 추천해줘", "비 와도 괜찮아" | 날씨 API 미호출, 날씨 가중치 제외 |
| `null` | 판별 불가 | "눈 오는데 추천" (의도 모호) | 사용자에게 추가 질문 |

**모호한 경우 처리:**

```
LLM이 AVOID/ENJOY 판별 불가
  → weather_intent: null 반환
  → 사용자에게 "실내 장소를 원하시나요, 눈 오는 풍경을 즐기고 싶으신가요?" 질문
  → 응답 반영 후 추천 진행
```

---

## 9. concentration_intent 판별

상세 필드 정의와 값별 의미는
[concentration-conditions.md §2.1](./concentration-conditions.md#21-필드-정의)이 소유한다.
이 절은 weather_intent와 다른 점만 요약한다.

| 값 | 의미 | 예시 입력 |
|----|------|-----------|
| `AVOID` | 혼잡한 곳을 피하고 싶음 | "조용한 공원 추천해줘", "한적한 곳 가고싶어" |
| `SEEK` | 혼잡한(인기 있는) 곳을 원함 | "핫한 관광지 어디야", "인기 많은 곳 추천해줘" |
| `IGNORE` | 혼잡도 무관·감수를 명시 | 언급 없음, "사람 많아도 괜찮아" |
| `null` | 판별 불가 | 혼잡도 단어는 있으나 방향 모호 (드묾) |

`SEEK`는 "핫한", "인기 많은", "북적이는 데"처럼 혼잡함을 **목적으로** 원할 때만 쓴다.
"사람 많아도 괜찮아"는 허용 표현이므로 `IGNORE`이며, 혼잡한 후보를 위로 올리지 않는다.

**weather_intent와의 차이**: `weather_intent`의 `null`은 environment 하드 필터를
결정 못 해 사용자에게 추가 질문한다(§8). `concentration_intent`는 하드 필터에
관여하지 않고 Scoring 가중치에만 영향을 주므로, `null`도 `IGNORE`와 동일하게
가중치만 제외하고 **추가 질문 없이 진행**한다.

`concentration_intent`가 `AVOID`/`SEEK`일 때만 C에 혼잡도 포함 조회를 요청한다
([concentration-conditions.md §2.2](./concentration-conditions.md#22-c-조회-플래그)).

---

## 10. 날씨 정보 확보 순서

(2026-08-05, A 수정) `weather_intent`의 "언급 없음"과 "무관 명시"를 분리했다.
`NO_MENTION`은 API 조회, `IGNORE`는 조회 생략으로 동작을 분리한다.

```
① 사용자 입력에 날씨 포함(weather_intent in {AVOID, ENJOY})
  → LLM이 weather + weather_intent 추출
  → C는 날씨 API를 호출하지 않는다
  → C가 사용자 발화 weather와 intent를 바탕으로 D 입력 날씨를 결정한다

② 사용자가 날씨를 전혀 언급하지 않음(weather_intent == NO_MENTION)
  → C가 GPS 좌표 기준으로 날씨 API 호출
  → API 값(3단계: good/neutral/bad)으로 날씨 점수 계산

③ 사용자가 날씨 무관을 명시(weather_intent == IGNORE)
  → 날씨 API를 호출하지 않는다
  → 날씨 항목을 추천 계산에서 제외
  → 나머지 가중치 재정규화

④ 날씨 API 실패(②에서 호출은 했으나 실패)
  → 날씨 항목을 추천 계산에서 제외, 나머지 가중치 재정규화
  → "확인하지 못했다"와 "언급이 없어 반영 안 함"은 사용자에게 다른 문구로
    안내한다(D-038 결정 1)
```

날씨 정보가 없다는 이유로 임의로 처리하지 않는다.

(과거엔 B의 세션 컨텍스트에도 `api_context.api_weather`를 별도로 조회·저장하는
경로가 있었으나, 이 값을 읽는 소비자가 없어 제거했다 — D-038 참고. 현재 날씨
확보 경로는 위 하나뿐이다.)

---

## 11. 조건 부족 시 기본 정책

조건 미확보 시의 필수/선택 구분과 기본값 정책은
[conditions-schema.md § missing_conditions](./conditions-schema.md#missing_conditions)를 따른다.

---

## 12. 필드별 변경 규칙

필드별 허용 연산(Update/Add/Remove), place_types가 Update인 이유, place_tags가
Add/Remove인 이유, place_types 교체 시 place_tags 정리 규칙은
[conditions-schema.md § 4. 조건 변경 연산](./conditions-schema.md#4-조건-변경-연산)을 참조한다.

---

## 13. LLM 추출 예시

### 기본 추출

| 입력 | current_location | search_center | place_types | place_tags | 기타 조건 |
|------|-----------------|---------------|------------|------------|-----------|
| "근처 카페 추천" | GPS | null | ["restaurant"] | ["카페"] | — |
| "경복궁 근처 맛집" | GPS | "경복궁" | ["restaurant"] | [] | — |
| "나 지금 성수야. 카페 추천" | "성수" | null | ["restaurant"] | ["카페"] | — |
| "비 오는데 갈 만한 곳" | GPS | null | [] | [] | weather=rain, weather_intent=AVOID, environment=indoor |
| "부모님과 걸어서 갈 카페" | GPS | null | ["restaurant"] | ["카페"] | companion=parent, transport=walk |
| "30분 안에 갈 수 있는 무료 전시" | GPS | null | ["festival"] | ["전시회"] | max_travel_time=30, budget=free |
| "종로 가려는데 근처 볼거리" | GPS | "종로" | ["attraction"] | [] | — |
| "핫한 관광지 어디야" | GPS | null | ["attraction"] | [] | concentration_intent=SEEK |
| "조용한 공원 추천해줘" | GPS | null | ["attraction"] | ["공원"] | concentration_intent=AVOID |

### 복수 유형 추출

| 입력 | place_types | place_tags |
|------|------------|------------|
| "박물관이나 카페 가고 싶어" | ["cultural_facility", "restaurant"] | ["박물관", "카페"] |
| "공원 산책하고 근처 카페" | ["attraction", "restaurant"] | ["공원", "카페"] |
| "시장 구경하고 맛집 갈래" | ["shopping", "restaurant"] | ["시장"] |
| "박물관이나 미술관, 쇼핑도 괜찮아" | ["cultural_facility", "shopping"] | ["박물관", "미술관"] |
| "오늘 전시 볼 만한 곳" | ["festival", "cultural_facility"] | ["전시회", "전시관"] |

### 전체 JSON 예시

```json
{
  "intent": "RECOMMEND",
  "conditions": {
    "current_location": "홍대",
    "search_center": "경복궁",
    "place_types": ["cultural_facility", "restaurant"],
    "place_tags": ["박물관", "미술관", "카페"],
    "weather": "rain",
    "weather_intent": "AVOID",
    "concentration_intent": null,
    "transport": "walk",
    "max_travel_time": 15,
    "time_available": null,
    "environment": "indoor",
    "companion": "parent",
    "budget": null,
    "exclude_tags": [],
    "special_requirements": []
  }
}
```

---

## 14. 추천 점수 카테고리 반영

### Hard Filter (추천 후보 제외)

```
다음 조건을 만족하지 않으면 후보에서 제외:
- 운영 종료 / 휴무
- 검색 반경 초과
- 예산 초과 (budget 기준)
- place_types 불일치 (place_types가 비어있지 않은 경우)
- environment 불일치 (weather_intent=AVOID일 때 outdoor 제외)
```

### 카테고리 점수

> **[2026-07-23 Superseded]** 카테고리를 가중치 점수로 계산하는 아래 방식은
> Scoring v1 결정(D-008, [`recommendation-scoring.md`](./recommendation-scoring.md))에
> 따라 폐기되었습니다. 카테고리(place_type/place_tag)는 가중치 계산이 아니라
> 1차 하드 필터로만 처리하며, 여러 태그를 동시에 허용한 경우의 우선순위 표현은
> 아직 `TBD`입니다. 최신 가중치(날씨/운영 유무/거리)는 위 문서를 참고하세요.

```
place_tags 언급 순서 기반 (선호 순위):
  rank_1: 1.00  (첫 번째 언급)
  rank_2: 0.85  (두 번째 언급)
  rank_3: 0.70  (세 번째 언급)
  rank_4+: 0.60 (네 번째 이후)

place_types에는 포함되지만 place_tags에 매칭되지 않는 장소:
  → 카테고리 점수 0.50 (기본값)

place_types 빈 배열 (전체 검색) + place_tags 없음:
  → 카테고리 점수 일괄 1.00 (차등 없음)
```

---

## 15. 경계 사례

| 입력 | 판정 | 이유 |
|------|------|------|
| "경복궁" (단독) | INFO | 정보 조회 의도 |
| "경복궁 같은 곳" | RECOMMEND | 유사 장소 추천, search_center는 미설정 |
| "경복궁 근처 카페" | RECOMMEND | search_center="경복궁" |
| "나 경복궁인데 카페 추천" | RECOMMEND | current_location="경복궁", search_center=null |
| "맛집 추천" | RECOMMEND | place_types: ["restaurant"] |
| "더 가까운 곳" (추천 이력 있음) | MODIFY | 조건 변경 |
| "더 가까운 곳" (추천 이력 없음) | RECOMMEND | 조건으로 처리 |

---

## 16. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-07-22 | 초안 작성 |
| v0.2 | 2026-07-23 | Conditions 3층 구조 명시(3절), preference_tags 필드 제거, weather 변경 규칙을 user_conditions/api_context 기준으로 수정, place_types 교체 시 place_tags 정리를 A의 명시적 Remove로 수정. (5·9·12절의 GPS↔api_context 위치 프레이밍은 후속 정리 예정) |
| v0.3 | 2026-07-23 | 소유권 기반 문서 정리: Conditions 필드 정의(3·4절), PlaceType/PlaceTag enum(6·7절), 조건 부족 시 기본 정책(10절), 필드별 변경 규칙(11절)을 conditions-schema.md 참조 링크로 교체. 추천 처리 흐름·위치 처리·날씨 확보·점수 계산 등 RECOMMEND 고유 로직은 유지 |
| v0.4 | 2026-07-29 | `concentration_intent` 판별 절 신설(신규 9절, weather_intent §8 패턴 요약) — 이후 9~15절을 10~16절로 순연. 13절(구 12절) LLM 추출 예시에 concentration 사례 2건 추가 |
| v0.5 | 2026-08-05 | `weather_intent`에 `NO_MENTION` 추가. §8에서 `NO_MENTION`(언급 없음)과 `IGNORE`(무관 명시) 의미를 분리하고, §10 날씨 확보 순서를 "발화 날씨는 사용자 값 사용 / `NO_MENTION`만 API 조회"로 갱신 |
| v0.6 | 2026-08-18 | "~도 괜찮아" 허용 표현을 조건 완화로 명시. "비 와도 괜찮아"는 `weather_intent=IGNORE`, "사람 많아도 괜찮아"는 `concentration_intent=IGNORE`이며, 혼잡·날씨 선호로 좁히지 않는다. 환경 허용은 `environment=any`로 처리 |

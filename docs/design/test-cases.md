# 4. 대표 테스트 입력·기대 결과

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.2 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-23 |
| 경로 | `docs/design/test-cases.md` |

---

## 1. RECOMMEND 케이스

### TC-01: 기본 추천

| 항목 | 내용 |
|------|------|
| 입력 | "경복궁 근처 카페 추천해줘" |
| Intent | RECOMMEND |
| Conditions | search_center: "경복궁", place_types: ["restaurant"], place_tags: ["카페"] |
| 처리 | 경복궁 좌표 기준 반경 1km, contentTypeId=39 조회, "카페" 태그 필터링 |
| 기대 결과 | 카페 3~5개 추천, 거리순+운영시간 반영 |

### TC-02: 날씨 반영 추천

| 항목 | 내용 |
|------|------|
| 입력 | "비 오는데 갈 만한 곳 추천" |
| Intent | RECOMMEND |
| Conditions | weather: "rain", weather_intent: "AVOID", environment: "indoor", place_types: [] |
| 처리 | current_location 기준, 전체 유형 검색, outdoor Hard Filter 적용 |
| 기대 결과 | 실내 장소 추천 (박물관, 카페, 쇼핑몰 등) |

### TC-03: 복수 유형 추천

| 항목 | 내용 |
|------|------|
| 입력 | "박물관이나 카페 가고 싶어" |
| Intent | RECOMMEND |
| Conditions | place_types: ["cultural_facility", "restaurant"], place_tags: ["박물관", "카페"] |
| 처리 | contentTypeId=14, 39 병렬 조회, 병합 후 카테고리 점수 적용 (박물관 rank_1, 카페 rank_2) |
| 기대 결과 | 박물관+카페 혼합 추천, 박물관이 상위 |

### TC-04: 조건 부족

| 항목 | 내용 |
|------|------|
| 입력 | "추천해줘" |
| Intent | RECOMMEND |
| Conditions | 모든 필드 null/빈 배열 |
| missing_conditions | api_context.gps_location |
| 처리 | 추천 미진행, 현재 위치(GPS) 질문 알럿 |
| 기대 결과 | "현재 위치를 알려주세요" GPS 응답 |

---

## 2. INFO 케이스

### TC-05: 운영시간 조회

| 항목 | 내용 |
|------|------|
| 입력 | "경복궁 오늘 열어?" |
| Intent | INFO |
| InfoQuery | place_name: "경복궁", place_context: "explicit", question_type: "operating_hours" |
| 처리 | searchKeyword2 → contentId 확보 → detailIntro2(contentTypeId=12) 호출 |
| 기대 결과 | 운영시간 + 오늘 휴무 여부 응답 |

### TC-06: 추천 결과에 대한 정보 조회

| 항목 | 내용 |
|------|------|
| 이전 상태 | 추천 결과 [A카페, B카페, C카페] 있음 |
| 입력 | "첫 번째 거기 주차 돼?" |
| Intent | INFO |
| InfoQuery | place_name: null, place_context: "from_recommendation", question_type: "parking" |
| 처리 | 추천 이력 1번 → contentId → detailIntro2 호출 |
| 기대 결과 | A카페 주차 정보 응답 |

---

## 3. MODIFY 케이스

### TC-07: 전체 거절

| 항목 | 내용 |
|------|------|
| 이전 상태 | 추천 결과 [A, B, C] 있음 |
| 입력 | "다른 곳 보여줘" |
| Intent | MODIFY |
| ModifyRequest | modify_type: "REJECT_ALL", condition_changes: null |
| 처리 | excluded에 [A, B, C] 추가, 동일 조건으로 재추천 |
| 기대 결과 | [D, E, F] 새로운 추천 |

### TC-08: 조건 변경

| 항목 | 내용 |
|------|------|
| 이전 상태 | 추천 결과 있음, budget: null |
| 입력 | "무료인 곳으로" |
| Intent | MODIFY |
| ModifyRequest | modify_type: "CHANGE_CONDITION", condition_changes: {budget: "free"} |
| 처리 | user_conditions.budget = "free"로 갱신, 기존 추천 제외, 재추천 |
| 기대 결과 | 무료 장소만 추천 |

### TC-09: search_center 변경 (제외 초기화)

| 항목 | 내용 |
|------|------|
| 이전 상태 | search_center: "경복궁", excluded: [A, B, C] |
| 입력 | "인사동 근처로 바꿔줘" |
| Intent | MODIFY |
| ModifyRequest | modify_type: "CHANGE_CONDITION", condition_changes: {search_center: "인사동"}, reset_scope: "history" |
| 처리 | A가 search_center 변경을 감지해 reset_scope: "history"를 명시적으로 함께 전달 → excluded 초기화 → 새 API 호출 |
| 기대 결과 | 인사동 기준 새로운 추천, 이전 제외 목록 리셋 |

---

## 4. COMPARE 케이스

### TC-10: 거리 비교

| 항목 | 내용 |
|------|------|
| 이전 상태 | 추천 결과 [A(0.3km), B(0.8km), C(0.5km)] |
| 입력 | "어디가 더 가까워?" |
| Intent | COMPARE |
| CompareRequest | targets: "all", criteria: "distance" |
| 처리 | 추천 결과의 distance_km 비교 |
| 기대 결과 | "A가 가장 가까워요 (약 0.3km)" |

---

## 5. GENERAL 케이스

### TC-11: 배경지식 질문

| 항목 | 내용 |
|------|------|
| 입력 | "경복궁은 언제 지어졌어?" |
| Intent | GENERAL |
| GeneralRequest | topic: "place_knowledge", original_question: "경복궁은 언제 지어졌어?" |
| 처리 | LLM 응답 생성 |
| 기대 결과 | 경복궁 역사 배경지식 응답 |

---

## 6. OUT_OF_SCOPE 케이스

### TC-12: 유해 발언

| 항목 | 내용 |
|------|------|
| 입력 | (욕설/비방) |
| Intent | OUT_OF_SCOPE |
| OutOfScopeRequest | category: "harmful", severity: "high" |
| 처리 | 즉시 차단 |
| 기대 결과 | "해당 요청에는 답변할 수 없어요." |

### TC-13: 서비스 범위 외

| 항목 | 내용 |
|------|------|
| 입력 | "주식 추천해줘" |
| Intent | OUT_OF_SCOPE |
| OutOfScopeRequest | category: "unrelated", severity: "low" |
| 처리 | 거절 + 서비스 가이드 |
| 기대 결과 | "저는 국내 여행 추천 도우미예요. 이런 것들을 도와드릴 수 있어요: ..." |

---

## 7. 엣지 케이스

### TC-14: 추천 이력 없이 MODIFY 시도

| 항목 | 내용 |
|------|------|
| 이전 상태 | 추천 이력 없음 |
| 입력 | "다른 곳 보여줘" |
| Intent 판별 | MODIFY 패턴이지만 전제조건 미충족 |
| 처리 | 안내 후 RECOMMEND 유도 |
| 기대 결과 | "아직 추천한 결과가 없어요. 어떤 장소를 찾고 계신가요?" |

### TC-15: 후보 부족

| 항목 | 내용 |
|------|------|
| 상황 | 조건 충족 후보가 2개뿐 |
| 처리 | open 후보 2개 + unknown 후보를 별도 영역에 추가 |
| 기대 결과 | 추천 2개 + "운영시간 확인 필요" 영역에 추가 장소 |

### TC-16: 날씨 API 실패

| 항목 | 내용 |
|------|------|
| 상황 | 사용자 날씨 미입력 + API 실패 |
| 처리 | 날씨 가중치 제외, 재정규화 적용 |
| 기대 결과 | 추천 제공 + "날씨 조건을 제외하고 추천했어요" 안내 |

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| v0.1 | 2026-07-22 | 초안 작성 |
| v0.2 | 2026-07-23 | Conditions 3층 구조 반영 — TC-04 missing_conditions를 api_context.gps_location으로 수정, TC-08 용어 통일(current_conditions→user_conditions), TC-09 reset_scope 명시 방식 반영 |
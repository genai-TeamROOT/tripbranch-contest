# 혼잡도(Concentration) 기능 실서버 E2E 검증

## 문서 정보

| 항목 | 값 |
|------|-----|
| 작성일 | 2026-08-05 |
| 작성자 | A(임기민) |
| 목적 | RECOMMEND의 `concentration_intent`(AVOID/SEEK/IGNORE)와 INFO의 `question_type=concentration`이 실서버에서 실제로 연결·작동하는지, 응답 시간은 얼마나 걸리는지 케이스별로 검증 |
| 관련 결정 | `decision-log.md` D-037/D-040(RECOMMEND 2차 Scoring), D-036(INFO 근접치 fallback), B-06(`concentration_intent` 필드 등록, PR #78) |
| 테스트 환경 | `PROVIDER_MODE=real`(LLM=Gemini, Place=TourAPI, Geocoding/Local Search=Naver, Weather=기상청, Concentration=TourAPI 혼잡도 API) — 모든 Provider 실제 연동, Fake 없음 |

## 1. 결론 요약

- **RECOMMEND·INFO 양쪽 다 실서버에서 end-to-end로 정상 동작을 확인했다** — C의 보강 조회(`EnrichmentProvider.enrich()`)와 D의 2차 Scoring(`rerank_with_concentration()`), B의 `concentration_intent` 저장까지 전부 실제로 연결돼 있다.
- INFO 경로는 정확 일치 조회와 근접치(proxy) fallback, 상대 날짜("이번 주말") 파싱까지 전부 실동작을 확인했다.
- 일부 케이스에서 "혼잡도가 반영 안 됨"으로 보이는 결과가 있었는데, **버그가 아니라 혼잡도 데이터 자체가 "관광지" 카테고리 전용이라 카페·음식점류 후보에는 매핑 데이터가 없어서 생기는 정상 동작**이다(2절 참고).
- 위치 모호성 2건(케이스 2, 3)은 혼잡도 로직과 무관한 별개 이슈로 확인했다(4절 참고) — 이번 검증 결론에는 영향 없음.

## 2. 왜 "카페 위주 추천"에서는 혼잡도가 반영 안 되는가

혼잡도 재순위는 상위 5개 후보를 카테고리 구분 없이 그대로 C에 보강 조회로 보낸다. 문제는 **혼잡도 데이터 소스 자체의 범위**다.

1. **1차 결과 그대로 보강 요청** — `_apply_concentration_rerank()`(`agent_runtime.py`)가 1차 Scoring 상위 5개(카페든 관광지든 무관)를 그대로 `CandidateEnrichmentRequest`로 만들어 C에 보낸다. 여기서 카테고리 필터링은 하지 않는다.
2. **후보 이름으로 개별 조회** — `CandidateEnrichmentService._enrich_candidate()`(`enrichment_service.py`)가 후보 이름을 그대로 `GetConcentrationTool`에 넘겨 종로구 범위로 조회한다.
3. **근본 원인 — 데이터 소스가 "관광지" 전용** — 사용하는 API가 한국관광공사의 **"관광지 집중률 예측 API"**다(`concentration-conditions.md` §1). 이름 그대로 관광지 전용 데이터라 카페·음식점 같은 일반 상업시설의 혼잡도는 애초에 다루지 않는다. C가 구축한 `place_concentration_mappings` 매핑 테이블도 종로구 관광지 100건(별칭 포함 101곳)만 등록돼 있고 카페·음식점 카테고리는 매핑 대상 자체가 아니다.
4. **매핑이 없으면 `no_data`** — 후보가 카페면 조회할 매핑이 없어 `status="no_data"`가 나온다. 상위 5개가 전부 카페·음식점이면 5개 다 `no_data`가 된다.
5. **전부 `no_data`면 2차 Scoring 자체를 건너뜀** — `resolve_enrichment_status()`(`enrichment_schemas.py`)가 5개 상태를 종합해 전부 `no_data`면 응답 전체 상태도 `no_data`로 판정하고, `agent_runtime.py`의 `_ENRICHMENT_TERMINAL_STATUSES = {"no_data", "unavailable"}`에 걸려 D의 2차 Scoring 호출 자체를 스킵한다 — 1차 결과를 그대로 반환한다.

**즉 "미반영"은 재순위할 데이터가 없어서 안 하는 것이지, 연결이 끊어진 게 아니다.** 관광지가 하나라도 후보에 섞이면(테스트 케이스 4 등) 일부 성공(`"partial"`) 상태가 되어 2차 Scoring이 정상적으로 실행된다.

## 3. 테스트 결과 표

| # | 시나리오 | 사용자 입력 | 소요시간 | concentration_intent | 혼잡도 반영 판정 | 비고 |
|---|---|---|---|---|---|---|
| 1 | RECOMMEND-SEEK | 경복궁 근처 핫한 곳 추천해줘 | 8.40s | SEEK | 미반영(1차 그대로) | 후보가 카페 위주 — 2절 설명대로 정상 동작 |
| 2 | RECOMMEND-AVOID | 인사동 근처 한적한 카페 추천해줘 | 6.25s | AVOID | N/A(추천 없음) | "인사동" 자체가 여러 곳으로 해석됨 — 혼잡도 무관, 순수 위치 모호성(4절) |
| 3 | RECOMMEND-SEEK | 종로 근처 사람 많은 인기 관광지 가고싶어 | 5.83s | SEEK | N/A(추천 없음) | LLM이 place_tags를 23개 과다 추출해 카테고리 충돌로 unsupported — 혼잡도 무관(4절) |
| 4 | RECOMMEND-AVOID | 북촌한옥마을 근처 조용한 곳으로 추천해줘 | 7.32s | AVOID | **반영됨** ✅ | 2차 Scoring 정상 트리거 확인 |
| 5 | RECOMMEND-IGNORE(대조군) | 경복궁 근처 카페 추천해줘 | 7.45s | IGNORE | 미반영(1차 그대로) | 기대대로 정상 스킵 |
| 6 | INFO-concentration(정확일치) | 경복궁 지금 사람 많아? | 5.45s | — | **실데이터 응답** ✅ | "경복궁은 2026-08-05 기준 다소 혼잡" |
| 7 | INFO-concentration(정확일치) | 오늘 창덕궁 붐빌까? | 6.66s | — | no_data | 창덕궁 혼잡도 매핑 데이터 미보유(데이터 커버리지 이슈, 코드 문제 아님) |
| 8 | INFO-concentration(근접치) | 인사동 카페거리 혼잡해? | 6.50s | — | 되묻기(장소 모호) | "인사동 카페거리" 자체가 모호한 장소명으로 판정 — 혼잡도 무관 |
| 9 | INFO-concentration(주말 visit_time) | 이번 주말 광화문 사람 많을까? | 5.66s | — | **실데이터 응답(근접치 fallback)** ✅ | "광화문 자체 데이터 없지만 경복궁 기준 2026-08-08 혼잡" — 근접치 fallback과 주말 날짜 파싱 둘 다 확인 |
| 10 | INFO-concentration(정확일치) | 종묘 지금 한산해? | 7.17s | — | no_data | 종묘 혼잡도 매핑 데이터 미보유 |

**GPS 좌표로 재확인한 보조 케이스** (카페 vs 관광지 카테고리 차이만 격리해서 확인)

| 시나리오 | 사용자 입력 | 소요시간 | 혼잡도 반영 |
|---|---|---|---|
| RECOMMEND-AVOID(GPS) | 한적한 카페 추천해줘 | 7.32s | 미반영 — 카페류 후보는 혼잡도 매핑 자체가 없음(정상) |
| RECOMMEND-SEEK(GPS) | 사람 많은 인기 관광지 가고싶어 | 6.85s | **반영됨** ✅ — "관광지"로 명시하니 매핑된 후보가 잡혀 정상 재순위 |

## 4. 혼잡도와 무관한 부수 발견 (참고용, 이번 검증 결론에 영향 없음)

- **케이스 2** ("인사동 근처 한적한 카페"): "인사동"이라는 지명 자체가 Naver Local Search에서 실제로 여러 후보로 해석돼 위치 되묻기로 빠진다. 혼잡도 로직 이전 단계(위치 해석)에서 멈춘 케이스다.
- **케이스 3** ("종로 근처 사람 많은 인기 관광지"): LLM이 "인기 관광지"를 지나치게 넓게 해석해 place_tags를 23개(전망대·궁궐·시장·백화점 등 서로 다른 place_type에 걸친 태그 포함)나 한 번에 추출했다. C가 이를 카테고리 충돌로 판정해 `unsupported` 응답을 반환했다 — 혼잡도 로직 진입 전 단계에서 막힌 것이다.

두 건 모두 별도 트랙(LLM 태그 추출 튜닝, 위치 해석 모호성 처리)의 이슈라 이번 문서 범위에서는 발견 사실만 기록한다.

## 5. 검증 방법 재현

```bash
# .env에 PROVIDER_MODE=real 및 각 API 키가 설정된 상태에서
cd backend
.venv/bin/python -m uvicorn app.main:app --port 8000

# 별도 터미널에서 케이스별로 재현 가능, 예:
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"user_input":"북촌한옥마을 근처 조용한 곳으로 추천해줘"}' | jq '.recommendations.recommendations[0].feature_scores'
# "concentration" 키가 있으면 2차 Scoring이 실제로 실행된 것이다.
```

## 6. 부가 발견 — 혼잡도 반영 "순서"만 B에 남고 "값"은 안 남음

2차 재순위 결과는 `agent_runtime.py` 7단계에서 B에 기록되지만(`recommendations` 변수가
6-1에서 재할당돼 최종 순서가 저장됨), `RecommendedPlace`는 `place_id`/`rank`만 갖고
있어 혼잡도 점수·등급·`feature_scores`/`weights_used`는 그 턴의 응답에만 존재하고
B에는 남지 않는다. 상세는 D-050(`decision-log.md`) 참고.

## 변경 이력

| 날짜 | 변경 |
| --- | --- |
| 2026-08-05 | 최초 작성 — RECOMMEND/INFO 혼잡도 실서버 E2E 검증 10건 + 보조 GPS 케이스 2건 |
| 2026-08-05 | §6 추가 — 혼잡도 재순위 "순서"는 B에 저장되지만 점수/등급 "값"은 저장 안 됨(D-050) |

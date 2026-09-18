# C 정규화 Context Fixture 설계

## 1. 목적

D가 A Runtime이나 실제 Provider를 실행하지 않고도 C의 정규화된
`RecommendationContext`를 입력으로 사용해 후보 변환과 추천 품질을 검증할 수
있도록 고정 Fixture를 제공한다.

Fixture는 원본 Provider 응답이 아니라 현재 A–C 계약의
`AgentContextResponse` JSON이다. D는 전체 응답 중 `context`만 추천 파이프라인에
전달한다.

Fixture의 날짜, 장소, 날씨와 운영정보는 반복 가능한 테스트를 위해 고정한 예시다.
실제 최신 정보를 나타내지 않으며 운영 환경의 사용자 응답이나 데이터 적재에
사용하지 않는다. JSON 자체는 실제 C 계약을 그대로 검증해야 하므로 Fixture 전용
필드는 추가하지 않고, 이 문서와 Fixture 폴더의 README에서 용도를 명시한다.

관련 파일:

- 계약: `backend/app/agent_context/schemas.py`
- C→D 후보 변환: `backend/app/domain/candidate_mapper.py`
- D 공개 진입점: `backend/app/services/recommendation_pipeline.py`
- Fixture: `backend/tests/fixtures/agent_context/`
- 검증 테스트: `backend/tests/agent_context/test_recommendation_context_fixtures.py`

## 2. 책임 경계

### C Fixture가 제공하는 값

- 검색 중심 위치와 좌표
- 날씨 상태 및 예측 시각
- 장소 ID, 이름, 카테고리, 좌표
- 운영정보 원문과 정규화된 `operating_schedule`
- Tool별 `status`, `warnings`, `provider_metadata`
- 최상위 Context 상태와 적용된 Rule 버전

### D가 계산하거나 결정하는 값

- 중심 좌표와 후보 좌표 사이의 `distance_km`
- 카테고리에 따른 `environment_type`
- 방문 시각에 적용되는 당일 운영 구간
- 폐점·이전 노출·거절 후보 제외
- Feature 점수, 가중치, 추천 순위 및 추천 이유

따라서 Fixture에는 `distance_km`, `environment_type`, Feature 점수 또는 기대 추천
순위를 중복 저장하지 않는다. 기대 추천 순위와 품질 기준은 D가 별도로 정의한다.

`visit_at`, `search_radius_km`, 이전 노출·거절 ID도 C 응답 계약의 필드가 아니므로
Fixture JSON에 넣지 않고 D 테스트 실행 인자로 제공한다.

## 3. Fixture 구성

| 구분 | 파일 | 최상위 상태 | 데이터 특성 | 주요 검증 목적 |
|---|---|---|---|---|
| 정상 | `success.json` | `success` | 위치·날씨·장소·공휴일 정상 | 기본 Candidate 변환 |
| 정상 | `success_bad_weather.json` | `success` | 나쁜 날씨, 실내·실외 후보 | 날씨 적합도 계산 |
| 정상 | `success_operating_schedule.json` | `success` | 영업 중·폐점·24시간 후보 | 폐점 제외와 운영시간 계산 |
| 부분 성공 | `partial_weather_unavailable.json` | `partial` | 날씨 Provider 실패 | 날씨 Feature 제외 후 추천 지속 |
| 부분 성공 | `partial_place_details.json` | `partial` | 일부 운영정보 누락 | 검증·미검증 후보 분리 |
| 결측 | `missing_weather.json` | `success` | Weather Tool을 실행하지 않음 | 미실행과 조회 실패 구분 |
| 결측 | `missing_operating_hours.json` | `partial` | 전체 후보 운영시간 누락 | 전 후보 미검증 처리 |
| 후보 부족 | `insufficient_candidates.json` | `partial` | 후보 1개 | 목표 추천 수 미달 처리 |
| 후보 없음 | `no_place_candidates.json` | `no_data` | 장소 조회 결과 빈 목록 | 오류 없는 빈 추천 반환 |

기존 `needs_location_clarification.json`은 C 계약 검증용으로 유지한다. 이 응답은
`context=null`이므로 D 추천 품질 Fixture에는 포함하지 않는다.

## 4. 상태 및 결측 표현

- `success`: 필요한 데이터가 정상적으로 존재한다.
- `partial`: 사용할 수 있는 데이터가 있지만 일부 Provider 결과나 장소 상세정보가
  누락됐다.
- `no_data`: 조회는 성공했지만 데이터가 없다. 장소 결과는 빈 배열로 표현한다.
- `unavailable`: Provider 호출 실패로 데이터를 확보하지 못했다. `error`와
  `provider_metadata`를 함께 제공한다.
- Tool 미실행: 실패 상태를 만들지 않고 해당 Context 필드를 `null`로 둔다.

Provider 조회 시각인 `retrieved_at`과 날씨 예측 시각인 `forecast_for`는 모두
timezone-aware ISO 8601 문자열을 사용한다.

## 5. 검증 범위

자동 테스트는 다음을 확인한다.

1. 9개 Fixture가 현재 `AgentContextResponse` Pydantic 계약을 통과한다.
2. 각 Fixture의 상태와 후보 수가 정의된 시나리오와 일치한다.
3. `response.context`만으로 D 공개 추천 파이프라인을 실행할 수 있다.
4. 폐점 후보가 제외된다.
5. 운영정보가 없는 후보는 `unverified_recommendations`로 분리된다.
6. 날씨 Tool 미실행과 Provider 실패가 서로 다른 경고로 표현된다.
7. 장소 `no_data`는 예외 대신 빈 추천 결과를 반환한다.

이 테스트는 추천 순위의 정답을 고정하지 않는다. 순위·점수 기대값은 D의 Scoring
규칙과 품질 평가 Fixture에서 관리한다.

# TripBranch API 및 내부 계약

## 1. 계약 구분

- **현재 공개 API**: 저장소에서 실제로 실행되는 FastAPI 계약
- **내부 계약**: Backend 모듈 사이에서만 사용하며 Frontend에 노출하지 않음

확정되지 않은 항목은 `TBD`로 표시합니다.

최종 확인: 2026-08-08.

## 2. 현재 공개 API

| 경로 | 용도 |
| --- | --- |
| `POST /api/chat` | **프론트 실사용 진입점.** 해석·추천·응답 조립을 한 번에 |
| `POST /api/agent-debug` | `/api/chat`과 같은 구현, 개발 패널 전용 |
| `POST /api/interpret` | Intent 분류·조건 추출만. 개발 패널 전용 |
| `POST /api/recommendations` | 추천 파이프라인만. 개발 패널 전용 |
| `GET /api/state/{session_id}` | 세션 상태 조회 |
| `DELETE /api/state/{session_id}` | 세션 삭제 |
| `GET /api/health` | 헬스체크 |

### `POST /api/chat`

프론트 실사용 진입점입니다. Intent 분류·조건 병합·Tool 조회·Scoring·응답 조립이
한 번의 호출로 끝나며, 라우터는 `run_agent()`에 그대로 위임합니다.

```ts
type AgentRequest = {
  user_input: string;      // 최소 길이 1
  session_id?: string;     // 없으면 Backend가 생성해 응답에 실어 보냄
  device_location?: string; // "위도,경도"
};

type AgentResponse = {
  llm_output: LLMOutput;
  state: StateApplyResponse;
  message: string;                          // 챗봇 말풍선 텍스트
  recommendations?: RecommendationResponse; // RECOMMEND/MODIFY + complete일 때만
  schedule?: ScheduleResult;                // SCHEDULE + complete일 때만
  suggested_follow_ups?: string[];          // 다음 발화 제안 버튼 문구, 0~3개
  llm_execution?: LLMExecutionMetadata;     // 개발자 Audit용
  tool_execution?: ToolExecutionDebug;      // 개발자 Audit용
};
```

- `recommendations`와 `schedule`은 동시에 채워지지 않습니다.
- `message`에 카드·일정 상세를 다시 풀어쓰지 않습니다. 상세는
  [Agent 응답 생성 설계](./design/agent-response-generation.md) 참고.
- `suggested_follow_ups`는 이 턴 뒤에 버튼으로 보여줄 다음 발화 후보입니다. 버튼을 누르면
  **그 문구가 그대로 `user_input`으로 재전송됩니다** — `clarification.options`가 `id`를
  `clarification_choice`로 보내 Intent를 못 박는 것과 다릅니다. 되묻기 턴
  (`status = needs_clarification`)과 `OUT_OF_SCOPE` 턴에서는 항상 빈 배열입니다(D-102).
  **`POST /api/chat/stream`에서는 이 필드가 항상 빈 배열이고**, 문구는 `done` 뒤에 오는
  별도 `follow_ups` 이벤트로 전달됩니다
  ([스트리밍 설계](./design/agent-response-streaming.md) 4.3절).
- 상세 필드는 `backend/app/schemas.py`의 `AgentRequest`, `AgentResponse`를
  기준으로 합니다.

> **이 응답 형상은 아직 확정 계약이 아닙니다.** 지금은 프론트 전환 비용을 줄이려고
> `AgentResponse`를 그대로 내보내고 있어 `llm_output` 전체와 B의 내부 state까지
> 노출됩니다. 필요한 필드만 남기는 축소는 D-016 확정 대기 중입니다
> (`routes/chat.py` TODO). **외부에 고정 계약으로 인용하지 마십시오.**

### `POST /api/agent-debug`

`/api/chat`과 같은 구현(`run_agent()`)을 공유하며 요청·응답 형식도 같습니다.
용도만 다릅니다 — 개발자 패널(`AgentRuntimeDebugPanel`)이 Intent 분류부터 최종
메시지 생성까지를 단계별로 확인할 때 씁니다.

### `GET /api/health`

```ts
type HealthResponse = { status: string };
```

### `GET /api/state/{session_id}`

```ts
type SessionContextResponse = {
  session_id: string | null;
  session_exists: boolean;
  has_recommendation: boolean;
  recommended_count: number;
  shown_place_ids: string[];
  excluded_place_ids: string[];
  last_recommended_run_id: string | null;
  last_intent: string | null;
  pending_clarification: string | null;
  user_conditions: UserConditions;
  api_context: {
    gps_location: string | null;
    api_weather: string | null;
    gps_expired: boolean;
    weather_expired: boolean;
  };
  condition_version: number;
};
```

### `DELETE /api/state/{session_id}`

```ts
type DeleteSessionResponse = {
  session_id: string;
  deleted: boolean;
};
```

### `POST /api/interpret`

> 현재 프론트에서는 `IntentDebugPanel`만 호출합니다. 실사용 발화는 `/api/chat`을
> 지납니다.

```ts
type InterpretRequest = {
  user_input: string; // 최소 길이 1
  session_id?: string;
  device_location?: string; // "위도,경도"
  // 아래 필드는 하위 호환용이며 세션이 있으면 Backend 상태로 대체됨
  has_previous_recommendation?: boolean;
  shown_place_count?: number;
  current_conditions?: UserConditions;
};

type InterpretResponse = {
  output: LLMOutput;
  state: SessionState;
};
```

`output`은 Intent별 구조화 결과이며, `state`에는 Backend가 생성한 `session_id`,
`run_id`, 병합된 `user_conditions`, 노출·제외 장소 ID와 GPS·날씨 만료 상태가
포함됩니다. 실제 상세 필드는 `backend/app/schemas.py`의 `LLMOutput`,
`SessionState`를 기준으로 합니다.

### `POST /api/recommendations`

> 현재 프론트에서는 디버그 경로로만 호출합니다. 실사용 추천은 `/api/chat`이
> 내부적으로 같은 파이프라인을 지납니다.

공개 요청 모델의 이름이 `RecommendationRequest`이지만 §3의 내부 추천 입력과
이름이 겹칩니다. 공개 모델 이름 변경 여부는 `TBD`입니다.

```ts
type CurrentRecommendationRequest = InterpretedConditions & {
  shown_place_ids: string[];
};

type RecommendationItem = {
  place_id: string;
  name: string;
  category: string;
  distance_km: number;
  remaining_minutes: number | null;
  // 그 후보에 실제 적용된 당일 운영 구간. 운영시간 미확인 후보는 null이다.
  // 24시간 개방은 "24시간", 원문 "09:00~24:00"은 "09:00~24:00"으로 내려간다.
  operating_hours_display: string | null; // 예: "09:00~18:00"
  // 실측 경로값. 값이 있으면 distance Feature 점수도 직선거리가 아니라 이 소요시간으로
  // 계산된 것이다. 조회 실패나 그 이동수단의 경로 Provider가 아직 없으면 세 필드가 함께
  // null이고, 그때는 distance_km(직선거리)가 유일한 거리 정보다. 프론트는 null일 때
  // 시간을 자체 추정하지 않고 직선거리로만 표시한다.
  // travel_mode는 어떤 이동수단으로 잰 값인지다. 지금 서버가 실제로 채우는 값은
  // "walking"뿐이고, 대중교통·자동차는 각 이동수단 카드에서 Provider가 붙는다.
  travel_distance_m: number | null;
  travel_duration_seconds: number | null;
  travel_mode: "walking" | "transit" | "driving" | null;
  environment_type: "indoor" | "outdoor" | "mixed" | "unknown";
  recommendation_reason: string;
  explanations: string[]; // Rule 기반 Feature별 설명 문장(0~3개), 기여도 큰 순
  warnings: string[];
  score: number;
  feature_scores: Record<string, number | null>; // Feature별 원점수(weather/remaining_operating_time/distance)
  weights_used: Record<string, number>; // 실제 적용된 가중치(재분배 후, 결측 Feature는 키 자체가 없음)
};

type RecommendationResponse = {
  recommendations: RecommendationItem[];
  unverified_recommendations: RecommendationItem[];
  elapsed_ms: number; // 전체 추천 파이프라인 처리시간(ms)
};
```

`elapsed_ms`는 위치 해석 시작부터 장소·날씨·공휴일 조회, Candidate 변환,
Scoring, 상위 후보 집중률 후조회와 응답 조립이 끝날 때까지 Backend에서 측정한
wall-clock 시간입니다. HTTP 전송시간과 Frontend 렌더링 시간은 포함하지 않습니다.

`score`/`feature_scores`/`weights_used`는
`backend/app/domain/evidence.py::build_evidence()`가 만든
`RecommendationEvidence`를 그대로 반영한 값입니다(D-028). `feature_scores`는
`contributions`의 `{feature: score}`를, `weights_used`는 `{feature: weight}`를
평탄화한 값이며, 날씨나 남은 운영시간이 결측이었던 Feature는 `weights_used`
키 자체에서 빠집니다. 자연어 추천 이유(`recommendation_reason`)는 여전히 고정
템플릿 문자열입니다.

`explanations`는 `backend/app/domain/explanation.py::build_explanations()`가
`RecommendationEvidence.contributions`를 Rule 기반으로 문장화한 값입니다
(D-029, A 담당 Agent Runtime과 API Contract 협의 반영 완료 — 상세는
[추천 Explainability Layer 설계](./design/recommendation-explainability.md)
참고). LLM을 호출하지 않으며, Feature 점수가 0.7 이상인 것만 기여도
(score × weight) 큰 순서로 포함합니다. 결측이거나 애매한 점수(<0.7)인
Feature는 생략되므로 배열이 빈 값(`[]`)일 수 있습니다.

`explanations`가 조용히 비거나 줄어드는 두 상황은 `warnings`에 문구를
추가해 안내합니다(`backend/app/services/recommendation_pipeline.py::
_extra_warnings()`). (1) 날씨 결측으로 `feature_scores.weather`가
`null`이면 "현재 날씨 정보를 확인하지 못해 이 조건은 반영되지 않았어요.",
(2) 점수는 있지만 모든 Feature가 0.7 미만이라 `explanations`가 완전히
비면 "이 장소는 특별히 강조할 만한 조건은 없지만, 조건에 맞아
추천했어요."가 추가됩니다. 운영시간 결측처럼 `unverified_recommendations`
로 분리되지는 않고, 기존 `recommendations`/`unverified_recommendations`
분류는 그대로 유지됩니다.

### 공통 오류

```ts
type ErrorResponse = {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details: unknown | null;
  };
};
```

현재 확인된 대표 코드는 `invalid_request`, `location_not_found`,
`weather_no_data`, `geocoding_unavailable`, `weather_unavailable`,
`provider_timeout`, `provider_unavailable`, `place_not_found`,
`internal_server_error`입니다.

## 3. Backend 내부 계약

### Interpret 결과

현재 구현 모델은 `LLMOutput`입니다(`backend/app/schemas.py`). Intent와 Intent별
payload, `status`, 되묻기 정보를 담습니다. `InterpretedConditions`는
`/api/recommendations`의 공개 요청 모델로만 남아 있습니다.

아래는 초기 설계의 초안이며, 신뢰도/근거 표현은 아직 도입되지 않았습니다.

```ts
type InterpretResultDraft = {
  intent: Intent;
  condition_patch: ConditionPatch;
  missing_required_fields: string[];
  // confidence/evidence 구조 TBD
};
```

Interpret 결과는 추천 장소나 Provider 원본 데이터를 포함하지 않습니다.

### 내부 `RecommendationRequest`

이 모델은 Frontend와 주고받는 공개 DTO가 아니라 Recommendation Engine 입력입니다.

```ts
type RecommendationRequest = {
  session_id: string;
  recommendation_run_id: string;
  origin: ResolvedLocation;
  conditions: NormalizedConditions;
  candidates: Candidate[];
  shown_place_ids: string[];
  rejected_place_ids: string[];
  current_context: CurrentExternalContext;
  scoring_policy_version: string;
};
```

각 하위 타입과 필수/선택 여부는 `TBD`입니다. Builder는 검증·정규화·이전 상태
병합·외부 데이터 보완을 마친 뒤에만 이 모델을 생성합니다.

### `Candidate` / Feature

현재 구현의 후보 모델은 다음 필드를 가진 `PlaceCandidate`입니다.

```ts
type PlaceCandidate = {
  place_id: string;
  content_type_id?: string | null;
  lcls_systm1?: string | null;
  lcls_systm2?: string | null;
  lcls_systm3?: string | null;
  name: string;
  category: string;
  latitude: number;
  longitude: number;
  address?: string | null;
  operating_hours?: string | null;
  raw_source: string;
};
```

Scoring v1용 `Candidate`는 `backend/app/domain/models.py::ScoringCandidate`로
구현되어 있으며, `PlaceCandidate`와 별도 모델입니다.

```ts
type OperatingHours = {
  open_time: string;  // "HH:MM", 당일 개장 시각
  close_time: string; // "HH:MM", 당일 마감 시각
};

type ScoringCandidate = {
  place_id: string;
  name: string;
  category: string;
  environment_type: "indoor" | "outdoor" | "unknown";
  distance_km: number;
  operating_hours: OperatingHours | null; // null이면 운영시간 미확인
  raw_source: string;
};
```

`backend/app/domain/scoring.py::score_candidates(candidates, *, now, ...)`가 이
모델을 입력받아 weather fit, 남은 운영시간(remaining_operating_time), distance
3개 Feature로 가중치 점수를 계산하고 정렬합니다. 운영 유무(폐점 여부)는 `now`와
`operating_hours`를 비교해 최종 하드 필터로 판정하며(가중치 Feature가 아님),
`operating_hours`가 `null`이면 폐점으로 보지 않고 남은 운영시간 Feature만
결측 처리해 나머지 가중치로 재분배합니다. `category`는 1차 하드 필터
(place_type/place_tag)가 이미 처리한다고 보고 가중치 계산에는 사용하지 않으며
표시용 메타데이터로만 남깁니다. `weights_used`는 날씨/남은 운영시간이 후보마다
다르게 결측될 수 있어 `ScoringResult` 전체가 아니라 `RankedCandidate`마다
따로 노출됩니다. congestion, evidence confidence Feature는 아직 미구현입니다.
Feature·가중치·제외 규칙 상세는
[추천 점수 설계](./design/recommendation-scoring.md)를 참고합니다. 이 엔진은
`backend/app/services/recommendation_pipeline.py`를 통해
`/api/recommendations` 라우트에 연결되어 있으며, 결과 근거(`score`/
`feature_scores`/`weights_used`)도 `RecommendationItem`에 노출됩니다(D-028,
아래 참고).

`backend/app/domain/evidence.py::build_evidence_list()`는 `RankedCandidate`를
자연어 없이 Feature별 기여도(score × weight)로 재구성한
`RecommendationEvidence`로 변환합니다.

```ts
type FeatureContribution = {
  feature: string;
  score: number | null;
  weight: number | null;
  contribution: number | null; // score * weight; 결측 시 null
};

type RecommendationEvidence = {
  place_id: string;
  name: string;
  category: string;
  rank: number;
  score: number;
  contributions: FeatureContribution[];
  is_unverified: boolean;
  warnings: string[];
};
```

상세와 고정 평가 Fixture v1은
[추천 Evidence·평가 Fixture 설계](./design/recommendation-evidence-fixture.md)를
참고합니다.

### `RecommendationResult`

```ts
type RecommendationResult = {
  place_id: string;
  name: string;
  score?: number;
  rank?: number;
  reason: string;
  warnings: string[];
  feature_scores?: Record<string, number | null>;
  snapshot?: RecommendationSnapshot;
};
```

점수 공개 범위, Feature 설명 형식, Snapshot 상세 스키마는 `TBD`입니다. 참고로
`/api/recommendations`의 `RecommendationItem`(§2)은 이미
`score`/`feature_scores`/`weights_used`를 노출하고 있고(D-028), `/api/chat`도
`recommendations`에 같은 `RecommendationResponse`를 그대로 싣습니다.

D-029(Explainability Layer v1)에서 Rule 기반 `explanations: string[]`도
추가됐습니다. A(Agent Runtime) 담당과 협의한 결과, `reason: string`(단일
문장) 자리에 합치지 않고 **별도 필드로 유지**하기로 확정했습니다 — 상세
근거는 [추천 Explainability Layer 설계](./design/recommendation-explainability.md)
§3.1 참고. Rule 기반 문장은 있는 그대로 노출하고 포맷팅만 Runtime 재량으로
둡니다(같은 문서 §3.2). `/api/chat`에서는
`response_composer.py::compose_recommendation_message()`가 이 원칙에 따라
`explanations`/`warnings`를 재작문 없이 이어붙입니다(D-054).

## 4. Provider 계약

모든 Provider 메서드는 외부 I/O를 고려해 비동기이며 Fake/Real 구현이 같은 계약을
따릅니다.

### 공통 결과 메타데이터

Provider의 정상 결과에는 다음 공통 metadata를 포함합니다. 이 계약은 설계상
확정됐지만 현재 코드 모델에는 아직 반영되지 않았습니다.

```ts
type ProviderMetadata = {
  source: ProviderSource;
  status: "success" | "no_data" | "partial";
  retrieved_at: string; // UTC ISO 8601: 2026-07-23T05:30:00.123Z
};

type ProviderSource =
  | "naver_geocoding"
  | "kma_ultra_short_forecast"
  | "tour_api_place"
  | "tour_api_concentration"
  | "kasi_holiday"
  | "fake_geocoding"
  | "fake_weather"
  | "fake_place"
  | "fake_concentration"
  | "fake_holiday";
```

- `success`: 유효 데이터가 하나 이상 있는 정상 결과
- `no_data`: 호출·파싱은 성공했지만 유효 데이터가 없는 정상 결과
- `partial`: 일부 누락이 있으나 안전하게 사용할 데이터가 있는 결과
- `unavailable`: 결과 status가 아니라 Provider/Tool 오류로 처리
- `retrieved_at`: 캐시 반환 시각이 아닌 최초 외부 조회·정규화 완료 시각
- Python 모델과 Backend JSON 모두 `retrieved_at` 사용

단건 Geocoding과 정확 장소 조회는 찾지 못한 경우 기존 `not_found` 오류 의미를
유지합니다. `no_data`는 장소 후보, 집중률, 공휴일 같은 목록 또는 선택 Feature의
빈 결과에 사용합니다.

### Provider 실패 계약

정상 결과에는 `ProviderMetadata`가 붙고, 호출 실패 시에는 정상 결과 대신
`ProviderError`가 발생합니다.

```ts
type ProviderError = {
  source: ProviderSource;
  code: "invalid_input" | "not_found" | "unavailable" | "internal_error";
  cause:
    | "timeout"
    | "unauthorized"
    | "rate_limited"
    | "network"
    | "upstream_error"
    | "parse_error"
    | "validation_error"
    | "unknown";
  occurred_at: string;
  retryable: boolean;
  message: string;
  details?: Record<string, unknown>;
};
```

- `retrieved_at`: 데이터를 성공적으로 조회하고 정규화한 시각
- `occurred_at`: Provider 오류를 감지한 시각
- 실패 결과에 `retrieved_at`을 생성하지 않음
- `no_data`는 오류가 아니라 `status="no_data"`인 정상 결과
- 파싱 실패는 `unavailable/parse_error`
- `message`와 `details`에는 Secret과 전체 요청 URL을 포함하지 않음

| Provider | 주요 메서드 | 내부 반환 타입 |
| --- | --- | --- |
| `GeocodingProvider` | `geocode(location_query)` | `GeocodeResult` |
| `WeatherProvider` | `get_current_condition(latitude, longitude)` | `WeatherCondition` |
| `PlaceProvider` | `search_places(...)` | `list[PlaceCandidate]` |
|  | `search_by_keyword(...)` | `list[PlaceCandidate]` |
|  | `get_details(content_id, content_type_id)` | `PlaceDetails` |
|  | `find_details_by_name(...)` | `PlaceDetails` |
| `ConcentrationProvider` | `get_forecast(...)` | `ConcentrationResult` |
| `HolidayProvider` | `get_holidays(year, month)` | `HolidayResult` |

`PlaceDetails`는 정규화된 제목, 주소, 소개, 홈페이지, 연락처, 운영시간과 진단용
`raw_common`, `raw_intro`를 포함합니다. `operating_schedule`은 원문을 보존하면서
해석 가능한 시간·월·휴무 규칙을 구조화합니다. `parse_status`는 `parsed`,
`partial`, `unknown`, `assumed`이며, 여행코스의 운영시간 누락을 24시간 이용
가능으로 처리한 경우 `assumed`와 `course_without_operating_hours` 사유를 함께
반환합니다. Provider 상세 계약은
[`backend/docs/provider-contract-v1.md`](../backend/docs/provider-contract-v1.md)를
참고합니다.

### Weather 시간 metadata

Weather 결과는 관측과 예보를 혼동하지 않도록 Provider 공통 metadata를 확장합니다.

```ts
type WeatherMetadata = ProviderMetadata & {
  data_type: "forecast";
  forecast_for: string;
  observed_at: string | null;
};
```

- `data_type`: MVP에서는 `forecast`
- `retrieved_at`: Provider 조회·정규화 시각
- `forecast_for`: 추천 판단에 사용한 예보 대상 시각
- `observed_at`: 현재 관측값은 사용하지 않으므로 `null`
- 즉시 추천은 현재와 가장 가까운 예보 선택
- 특정 시간 추천은 방문 예정 시각과 가장 가까운 예보 선택

`GetWeatherForecastTool`이 방문 시각에 가장 가까운 예보를 선택하고 위 시간
metadata를 반환합니다. 공통 `ProviderMetadata` wrapper가 Fake/Real Provider에
적용되어 Tool Context로 전달됩니다.

## 5. Tool 계약 초안

각 Tool은 업무별 payload를 유지하되 `status`, `error`, `warnings`,
`provider_metadata` 필드를 공통으로 제공하며 `ToolResult<T>` Protocol을
만족합니다.

구현된 Tool (`backend/app/tools/`):

| Tool | 책임 | Provider |
| --- | --- | --- |
| `ResolveLocationTool` | 장소명/주소를 좌표로 해석 | LocalSearch + Geocoding |
| `NearbyPlaceDetailsTool` | 주변 후보 수집 후 제한된 동시성으로 다건 상세조회 | Place Search + Place Details |
| `GetPlaceDetailTool` | 이미 특정된 장소 1건의 상세 조회 (D-054) | Place |
| `GetFestivalsTool` | 지역 행사 중 기준일에 진행 중인 것 (D-055) | Festival |
| `GetWeatherForecastTool` | 방문 예정 시각의 초단기예보 선택 | Weather |
| `GetConcentrationTool` | 장소/지역 혼잡도 조회 | Concentration |
| `GetHolidaysTool` | 공휴일 조회 | Holiday |

미구현:

| Tool | 책임 | 예상 Provider |
| --- | --- | --- |
| `estimate_travel_time` | 이동수단별 예상 시간 계산 | 지도/위치 Provider `TBD` |
| `search_place_feature_evidence` | 조용함·분위기 근거 수집 | Naver Blog Search `TBD` |

A↔C 공통 요청·응답 envelope(`tool_type` 기반)은
[Tool Intelligence Contract v1](./design/tool-intelligence-contract-v1.md)에
있습니다. 단, 그 envelope은 현재 실행 경로에 배선돼 있지 않습니다 — 실제 A→C
호출은 `agent_context/service.py`의 Context 단위 인터페이스를 지납니다(같은
문서 §13.1).

### `resolve_location` 구현 계약

```python
ResolveLocationTool(provider: GeocodingProvider)
```

- 입력: `location_query` 1~200자
- 지원 범위: 서울특별시 종로구
- alias 우선 조회 후 정상 빈 결과에만 원문으로 1회 fallback
- Provider 장애에는 fallback하지 않고 `unavailable`
- 종로구 밖 또는 행정구를 확인할 수 없는 결과는 `unsupported`
- 직접·fallback 조회의 복수 후보는 `no_data`,
  `details.reason="ambiguous_location"`으로 사용자 재질문
- 성공 method: `direct`, `alias`, `fallback`
- fallback 성공 시 `fallback_used` warning
- Provider 결과에는 `candidate_count`, `administrative_district` 포함

### `get_weather_forecast` 구현 계약

- 입력: 위도, 경도, 선택적 `visit_at`
- `visit_at=None`: Backend Clock의 현재 시각 사용
- timezone 없는 시각: `Asia/Seoul`로 간주하고 `timezone_assumed=true`
- timezone 포함 시각: `Asia/Seoul`로 변환
- 가장 가까운 예보 선택, 동률이면 미래 예보 우선
- 명시 시각이 예보 범위 밖이면 `unsupported/outside_forecast_range`
- 빈 예보는 `no_data/forecast_not_found`
- 장애는 `unavailable`이며 timeout과 upstream error를 구분
- Weather Tool 자체는 지역을 제한하지 않고 좌표 범위만 검증
- 결과: condition, SKY, PTY, forecast_for, retrieved_at, KMA 격자,
  data_type=forecast, observed_at=null

### `get_nearby_place_details` 구현 계약

`NearbyPlaceDetailsTool`은 목록과 상세 데이터 소스를 별도로 주입받습니다.

```python
NearbyPlaceDetailsTool(
    search_provider: PlaceSearchProvider,
    details_provider: PlaceDetailsProvider,
    max_concurrency: int = 3,
)
```

- 입력: 좌표, 검색 반경, 최대 결과 수, 선호 카테고리, TourAPI 분류 필터,
  제외할 `place_id`
- 제한: 반경 `0 < km <= 20`, 결과 수 `1..20`, 동시 상세조회 `1..10`
- 순서: 검색 결과 순서를 유지하며 제외 ID 적용 후 최대 결과 수만 반환
- 상세 상태: `success`, `no_data`, `unavailable`
- 전체 상태: 후보가 없으면 `no_data`, 모든 상세조회가 성공하면 `success`,
  일부 상세정보가 없거나 실패하면 `partial`
- 부분 실패: 한 장소의 상세조회 실패가 다른 장소 조회를 중단하지 않음
- 교체 경계: 현재는 두 역할 모두 `RealPlaceProvider`가 담당하지만,
  `details_provider`만 DB 구현으로 교체할 수 있음
- 관측 정보: 결과에 `source`, `retrieved_at`, 전체 `elapsed_ms` 포함

### 공통 Tool 결과

```ts
type ToolResult<T> =
  | {
      ok: true;
      data: T;
      warnings: ToolWarning[];
      provider_metadata: ProviderMetadata[];
    }
  | {
      ok: false;
      error: ToolError;
      provider_metadata: ProviderMetadata[];
    };

type ToolWarning = {
  code: "partial_data" | "stale_data" | "fallback_used";
  message: string;
};
```

Tool이 Provider를 호출하지 못한 경우 `provider_metadata`는 빈 배열일 수 있습니다.
복수 Provider를 조합하면 각 정상 결과의 metadata를 호출 순서대로 보존합니다.

### 공통 오류 코드

```ts
type ToolErrorCode =
  | "invalid_input"
  | "not_found"
  | "no_data"
  | "unavailable"
  | "unsupported"
  | "internal_error";

type ToolErrorCause =
  | "timeout"
  | "unauthorized"
  | "rate_limited"
  | "network"
  | "upstream_error"
  | "parse_error"
  | "validation_error"
  | "unknown";

type ToolError = {
  code: ToolErrorCode;
  message: string;
  retryable: boolean;
  tool: string;
  cause?: ToolErrorCause;
  source?: ProviderSource;
  occurred_at: string;
  details?: Record<string, unknown>;
};
```

`message`는 사용자 또는 상위 계층에 전달 가능한 비민감 문장입니다. ProviderError를
변환할 때 `source`, `cause`, `occurred_at`, `retryable`을 보존합니다. `details`에는
API 키, 인증 헤더, 전체 요청 URL, 원본 사용자 발화를 넣지 않습니다.

### `no_data`와 `unavailable`

| 구분 | `no_data` | `unavailable` |
| --- | --- | --- |
| 외부 호출 | 성공 | 실패 또는 응답 신뢰 불가 |
| 파싱 | 성공 | 실패할 수 있음 |
| 데이터 존재 여부 | 요청 조건에 없음을 확인 | 존재 여부를 판단할 수 없음 |
| 동일 요청 즉시 재시도 | 기본적으로 의미 없음 | 원인에 따라 가능 |
| 사용자 행동 | 조건/범위 변경 가능 | 잠시 후 재시도 또는 운영 조치 |
| 추천 처리 | 선택 Feature면 제외 가능 | 선택 Feature면 fallback 가능, 필수 데이터면 중단 |

HTTP 200이더라도 응답 schema가 깨져 파싱할 수 없으면 `no_data`가 아니라
`unavailable(cause="parse_error")`입니다. 반대로 빈 `items`가 API의 정상 계약이면
`no_data`입니다.

### 코드 판정 기준

| 코드 | 의미 | 동일 요청 기본 retry |
| --- | --- | --- |
| `invalid_input` | Tool 입력 형식·범위가 잘못됨 | 아니오 |
| `not_found` | 위치나 특정 장소 같은 식별 대상을 찾지 못함 | 아니오 |
| `no_data` | 식별 대상은 유효하지만 요청한 부가/목록 데이터가 없음 | 아니오 |
| `unavailable` | 외부 의존성 문제로 결과를 확인할 수 없음 | 원인에 따라 |
| `unsupported` | 현재 Tool이 요청 기능·지역·형식을 지원하지 않음 | 아니오 |
| `internal_error` | Tool 자체의 예상하지 못한 오류 | 아니오, 운영 확인 |

`timeout`, `unauthorized`, `rate_limited`는 Orchestrator의 업무 분기를 불필요하게
늘리지 않도록 v1 최상위 code로 두지 않고 `unavailable`의 `cause`로 둡니다.

### Provider 오류 매핑

| Provider 결과/오류 | Tool 오류 code | cause | retryable 기본값 |
| --- | --- | --- | --- |
| `invalid_request`, `ValueError` | `invalid_input` | `validation_error` | `false` |
| `location_not_found` (`resolve_location`) | `no_data` | `location_not_found` | `false` |
| `place_not_found` | `not_found` | 생략 가능 | `false` |
| 정상 빈 후보/forecast/holiday 결과 | `no_data` | 생략 가능 | `false` |
| `weather_no_data` | `no_data` | 생략 가능 | `false` |
| `provider_timeout` | `unavailable` | `timeout` | `true` |
| 인증 401/403 | `unavailable` | `unauthorized` | `false` |
| HTTP 429 | `unavailable` | `rate_limited` | `true` |
| `geocoding_unavailable`, `weather_unavailable` | `unavailable` | `upstream_error` | `true` |
| `provider_unavailable` | `unavailable` | 실제 원인에 맞춤 | 원인에 따라 |
| Tool 내부 예상 밖 예외 | `internal_error` | `unknown` | `false` |

현재 Provider 오류가 HTTP status나 세부 원인을 구조화해 보존하지 않는 경우가 있어
`cause`를 정확히 설정하지 못할 수 있습니다. 이 경우 `upstream_error` 또는
`unknown`을 사용하고 후속 Provider 오류 모델 구현에서 보완합니다.

### Orchestrator 기본 처리

| Tool 오류 | 필수 데이터 | 선택 Feature |
| --- | --- | --- |
| `invalid_input` | 요청 검증 실패 또는 사용자 수정 요청 | 해당 Feature 입력 무시 금지; 수정 요청 |
| `not_found` | 위치/장소 재확인 요청 | 대상 Feature 제외 가능 |
| `no_data` | 조건 완화 여부를 사용자에게 확인 | Feature 제외 후 warning과 함께 진행 가능 |
| `unavailable` | 제한된 재시도 후 실행 실패 | fallback/Feature 제외 후 warning 가능 |
| `unsupported` | 지원 범위 안내 | Feature 제외 가능 |
| `internal_error` | 안전하게 실패하고 실행 ID 로그 | 원칙적으로 자동 무시하지 않음 |

명시적 필수 조건을 자동으로 완화하지 않습니다. Tool은 오류를 분류하고,
중단·부분 진행·재질문의 최종 결정은 Orchestrator가 담당합니다.

## 6. 세션과 실행 식별자

| 필드 | 생성 주체 | 역할 | 현재 구현 |
| --- | --- | --- | --- |
| `session_id` | Backend State Service | 현재 채팅 세션 식별, 사용자 ID 아님 | 구현 |
| `run_id` | Backend State Service | 조건 변경·추천 이력 실행 연결 | 구현 |
| `recommendation_run_id` | Backend | Snapshot·로그 식별자 | 현재 `run_id`와 통합 방식 `TBD` |

`session_id`와 `run_id`는 UUID 기반 문자열입니다.

`POST /api/chat`은 `session_id`를 선택 필드로 받습니다. 없으면 Backend가 새로
만들고 프론트는 응답으로 받은 값을 이후 발화에 실어 보냅니다. **초기 설계에
있던 별도 식별자 `chat_session_id`는 도입하지 않았고 `session_id`로
일원화했습니다.**

영속 저장 위치와 중복·재시도 정책은 `TBD`입니다.

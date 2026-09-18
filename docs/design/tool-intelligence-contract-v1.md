# Tool Intelligence Contract v1

## 문서 정보

| 항목 | 값 |
| --- | --- |
| 문서 유형 | A(Agent Runtime) ↔ C(Tool Intelligence) 인터페이스 계약 |
| 버전 | v1.0 |
| 상태 | **확정 — v1 구현 기준** |
| 작성 범위 | A(Agent Runtime) ↔ C(Tool Intelligence) 요청·응답 경계 |
| 최종 수정 | 2026-08-08 |

> 이 문서는 A Runtime과 C Tool Intelligence 사이의 v1 요청·응답 형식과
> 책임 경계를 정의한다. 아직 v1 범위에 포함되지 않은 정책은 `후속 결정`으로
> 명시하며, 확정된 계약과 구분한다.

## 0. 계약 범위와 읽는 방법

### 이 문서에서 정의하는 것

1. A가 C에 요청을 전달하는 공통 envelope
2. C가 A에 결과를 반환하는 공통 envelope
3. 지원할 `tool_type`과 Tool별 최소 입력
4. A의 `ApiContext`를 만들기 위해 C가 제공해야 하는 값
5. 단건·다건 호출, 식별자, 오류와 부분 성공 표현
6. A와 C 중 어느 계층이 위치·날씨 값을 변환하는지

### 표기

| 표기 | 의미 |
| --- | --- |
| `Existing` | 현재 저장소 코드나 기존 문서에서 확인된 사실 |
| `Contract` | v1 구현에서 따라야 하는 확정 계약 |
| `Follow-up` | v1 계약을 변경하지 않는 후속 결정 또는 구현 항목 |
| `Accepted` | A·C가 합의한 내용 |
| `Rejected` | 검토 후 채택하지 않기로 한 내용 |

### 현재 계약 상태

위치 표현, 날씨 enum, 운영시간 원문·정규화 정보, Provider metadata와
`retrieved_at` 의미를 포함한 v1 요청·응답 구조가 확정되었다. 계절별·요일별 복수
운영시간과 일부 상태의 A 처리 정책은 후속 결정 항목으로 분리한다.

## 1. 현재 확인된 기반 (`Existing`)

| 항목 | 현재 확인된 내용 | 근거 |
| --- | --- | --- |
| A 조건 상태 | `ApiContext`에 GPS·날씨와 갱신 시각 저장 | `llm-output-schema.md` |
| C 내부 Tool 공통 필드 | `status`, `error`, `warnings`, `provider_metadata` | `backend/app/tools/contracts.py` |
| Provider metadata | `source`, `status`, `retrieved_at` | `backend/app/providers/contracts.py` |
| C Tool | 위치, 날씨, 주변 장소 상세, 장소 상세 단건, 행사, 집중률, 공휴일 Tool 구현 | `backend/app/tools/` |
| A↔C 공통 요청 DTO | `ToolRequest` discriminated union | `backend/app/tool_intelligence/schemas.py` |
| A↔C 공통 응답 DTO | `ToolResponse[T]` | `backend/app/tool_intelligence/schemas.py` |

`ApiContext`는 A가 B State에 저장하는 축약 상태로 보이며, C Tool의 전체 응답
형식으로 사용하기에는 장소 목록, 부분 성공, 오류, Provider 출처를 표현할 필드가
부족하다. 이 해석이 맞는지 A 확인이 필요하다.

## 2. 핵심 계약 결정

| ID | 안건 | 선택지 | C 권장안 | 상태 |
| --- | --- | --- | --- | --- |
| `TI-01` | 요청 단위 | Tool 단건 / 다건 배열 | v1은 단건 | `Contract` |
| `TI-02` | 요청 식별자 | 없음 / `request_id` | A가 `request_id` 생성 | `Contract` |
| `TI-03` | 실행 식별자 | `request_id`만 / `recommendation_run_id` 포함 | v1 Tool 계약은 `request_id` 사용 | `Contract` |
| `TI-04` | Tool 이름 표기 | 대문자 enum / 소문자 `snake_case` | 소문자 `snake_case` | `Contract` |
| `TI-05` | 위치 연속 호출 | A가 좌표 전달 / C가 내부 연결 | A가 Runtime 흐름 제어 | `Accepted` |
| `TI-06` | 응답 payload | Tool별 필드 / 공통 `data` | 공통 `data` | `Contract` |
| `TI-07` | metadata | 단일 객체 / 배열 | 복합 Tool을 위해 배열 | `Contract` |
| `TI-08` | A↔C 위치 표현 | 문자열 / 좌표 객체 | 좌표 객체 | `Accepted` |
| `TI-09` | 날씨 enum | A의 5개 / C의 기존 3개 | ~~`good/neutral/bad` 유지~~ → 판정 자체를 D로 이관 | `Superseded` (D-038·D-051) |
| `TI-10` | 알 수 없는 입력 필드 | 허용 / 거부 | strict validation | `Contract` |

이 표는 현재 구현 및 A·C 협의 결과를 반영한 v1 기준이다.

## 3. 목적 및 책임 경계 (`Contract`)

A가 필요한 정보와 조건만 C에 전달하고, C가 적절한 Tool과 Provider를 선택할 수
있도록 `ToolRequest` 형식을 정의한다.

A는 다음 정보를 지정하지 않는다.

- Fake/Real Provider 선택
- 외부 API 이름 또는 endpoint
- API Key
- TourAPI 분류 코드
- Provider별 요청·응답 원본 필드
- Provider 호출 순서와 재시도 방식

C의 책임 경계는 요청 검증, 내부 Tool 입력 변환, Provider 호출과 정규화 결과
반환까지다. 추천 장소 선택, 점수 계산, 사용자용 자연어 응답 생성은 담당하지 않는다.

## 4. v1 요청 원칙 (`Contract`, `TI-01`~`TI-05`)

1. 하나의 `ToolRequest`는 하나의 `tool_type`만 요청한다.
2. 여러 정보가 필요하면 A가 여러 요청을 생성한다.
3. 요청 간 병렬 실행과 호출 순서는 A Runtime이 결정한다.
4. 하나의 Tool 내부에서 여러 Provider가 필요하면 C가 조합한다.
5. `request_id`는 A가 생성하며 C가 응답에 그대로 반환한다.
6. 요청과 응답의 필드명 및 enum 값은 소문자 `snake_case`를 사용한다.
7. 선택 필드를 생략한 경우 C가 이 문서에 정의된 기본값을 적용한다.
8. 알 수 없는 필드는 v1에서 허용하지 않는 방향으로 한다.

다건 요청을 한 envelope에 담는 `tool_requests` 배열은 v1 범위에서 제외한다. 실제
Runtime 연동에서 네트워크 왕복 감소가 필요하다고 확인되면 v1.1에서 검토한다.

## 5. 공통 요청 Envelope (`Contract`)

```ts
type ToolType =
  | "resolve_location"
  | "search_nearby_places"
  | "get_place_details"
  | "get_weather_forecast"
  | "get_concentration"
  | "get_holidays";

type ToolRequest =
  | ResolveLocationRequest
  | SearchNearbyPlacesRequest
  | GetPlaceDetailsRequest
  | GetWeatherForecastRequest
  | GetConcentrationRequest
  | GetHolidaysRequest;
```

모든 요청은 다음 공통 구조를 사용한다.

```ts
type ToolRequestEnvelope<TType extends ToolType, TParameters> = {
  request_id: string;
  tool_type: TType;
  parameters: TParameters;
};
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `request_id` | `string` | 필수 | A가 생성하는 요청 식별자. UUID 문자열 권장 |
| `tool_type` | `ToolType` | 필수 | C가 수행할 업무 단위 |
| `parameters` | `object` | 필수 | `tool_type`별 입력값 |

`request_id`는 채팅을 식별하는 `chat_session_id`나 추천 실행을 식별하는
`recommendation_run_id`를 대체하지 않는다.

## 6. Tool별 Request Schema (`Contract`)

### 6.1 `resolve_location`

장소명이나 주소를 C가 지원하는 지역의 좌표로 해석한다.

```ts
type ResolveLocationRequest = ToolRequestEnvelope<
  "resolve_location",
  {
    location_query: string;
  }
>;
```

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `location_query` | `string` | 필수 | 공백 제거 후 1~200자 |

MVP 지원 범위는 서울특별시 종로구다. 범위 밖이거나 행정구를 확인할 수 없는
위치는 `unsupported`로 처리하고, A가 사용자에게 위치를 다시 확인한다.

```json
{
  "request_id": "d4d60aa4-2f11-4e43-b3bc-43e82a90bc64",
  "tool_type": "resolve_location",
  "parameters": {
    "location_query": "경복궁"
  }
}
```

### 6.2 `search_nearby_places`

기준 좌표 주변의 장소 후보와 추천 판단에 필요한 상세정보를 조회한다. C 내부에서는
현재 `NearbyPlaceDetailsTool`로 매핑하며, 후보 검색과 상세조회를 조합한다.

```ts
type SearchNearbyPlacesRequest = ToolRequestEnvelope<
  "search_nearby_places",
  {
    location: Coordinates;
    radius_km?: number;
    limit?: number;
    place_types?: PlaceType[];
    place_tags?: string[];
    excluded_place_ids?: string[];
  }
>;

type Coordinates = {
  latitude: number;
  longitude: number;
};

type PlaceType =
  | "attraction"
  | "cultural_facility"
  | "festival"
  | "leisure"
  | "shopping"
  | "restaurant";
```

| 필드 | 타입 | 필수 | 기본값·제약 |
| --- | --- | --- | --- |
| `location` | `Coordinates` | 필수 | 위도 `-90..90`, 경도 `-180..180` |
| `radius_km` | `number` | 선택 | 기본 `2.0`, `0 < radius_km <= 20` |
| `limit` | `integer` | 선택 | 기본 `10`, `1..20` |
| `place_types` | `PlaceType[]` | 선택 | 생략 또는 빈 배열이면 유형 제한 없음 |
| `place_tags` | `string[]` | 선택 | `카페`, `박물관` 등 내부 조건 Schema의 태그 |
| `excluded_place_ids` | `string[]` | 선택 | 기본 빈 배열 |

A는 `content_type_id`, `lcls_systm1`, `lcls_systm2`, `lcls_systm3` 같은 TourAPI
코드를 전달하지 않는다. C가 `place_types`와 `place_tags`를 Provider 분류 코드로
변환한다. 이 변환은 현재 후속 구현 대상이다.

```json
{
  "request_id": "b87c3211-afb5-4c48-bd85-d904b29b6750",
  "tool_type": "search_nearby_places",
  "parameters": {
    "location": {
      "latitude": 37.5796,
      "longitude": 126.977
    },
    "radius_km": 2.0,
    "limit": 10,
    "place_types": ["restaurant"],
    "place_tags": ["카페"],
    "excluded_place_ids": []
  }
}
```

### 6.3 `get_place_details`

식별된 장소 하나의 공통 상세정보와 운영정보를 조회한다.

```ts
type GetPlaceDetailsRequest = ToolRequestEnvelope<
  "get_place_details",
  {
    place_id: string;
    place_type?: PlaceType;
  }
>;
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `place_id` | `string` | 필수 | C가 이전 장소 결과에서 반환한 식별자 |
| `place_type` | `PlaceType` | 선택 | 상세조회 Provider 선택을 보조하는 내부 유형 |

A는 Provider 전용 `content_type_id`를 전달하지 않는다. 현재 Real Place Provider가
상세조회에 `content_type_id`를 요구하므로, C가 이전 검색 결과의 Context나 내부
분류 Mapper에서 이를 확보해야 한다. 확보하지 못한 경우의 처리 정책은 A·C 협의
후 확정한다.

### 6.4 `get_weather_forecast`

좌표와 방문 예정 시각을 기준으로 가장 가까운 기상청 초단기예보를 조회한다.

```ts
type GetWeatherForecastRequest = ToolRequestEnvelope<
  "get_weather_forecast",
  {
    location: Coordinates;
    visit_at?: string;
  }
>;
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `location` | `Coordinates` | 필수 | 날씨를 조회할 좌표 |
| `visit_at` | ISO 8601 문자열 | 선택 | 생략하면 Backend 현재 시각 사용 |

timezone이 없는 `visit_at`은 `Asia/Seoul`로 해석한다. 명시한 시각이 제공되는 예보
범위 밖이면 `unsupported`로 처리한다.

```json
{
  "request_id": "3e23c295-2ea1-4bb7-9c3f-e55a48c922e8",
  "tool_type": "get_weather_forecast",
  "parameters": {
    "location": {
      "latitude": 37.5796,
      "longitude": 126.977
    },
    "visit_at": "2026-07-24T15:00:00+09:00"
  }
}
```

### 6.5 `get_concentration`

현재 지원되는 관광 집중률 예측정보를 조회한다.

```ts
type GetConcentrationRequest = ToolRequestEnvelope<
  "get_concentration",
  {
    area_code: string;
    district_code: string;
    place_name?: string;
  }
>;
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `area_code` | `string` | 필수 | 법정동 시도코드 |
| `district_code` | `string` | 필수 | 법정동 시군구코드 |
| `place_name` | `string` | 선택 | 가능한 경우 특정 장소 결과를 선택하기 위한 이름 |

이 API는 임의 장소의 실시간 혼잡도를 보장하지 않는다. 요청한 장소 데이터가 없으면
C는 임의의 인근 장소나 구 전체 수치로 대체하지 않고 `no_data`를 반환한다.

### 6.6 `get_holidays`

연도 또는 연월에 해당하는 공휴일 목록을 조회한다.

```ts
type GetHolidaysRequest = ToolRequestEnvelope<
  "get_holidays",
  {
    year: number;
    month?: number;
  }
>;
```

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `year` | `integer` | 필수 | `1..9999` |
| `month` | `integer` | 선택 | `1..12` |

## 7. 공통 응답 Envelope (`Contract`, `TI-06`~`TI-07`)

C는 Tool별 내부 결과 필드(`location`, `forecast`, `places` 등)를 A에 그대로
노출하지 않고 공통 `data` 필드로 감싸 반환한다.

```ts
type ToolStatus =
  | "success"
  | "no_data"
  | "partial"
  | "invalid_input"
  | "unsupported"
  | "unavailable"
  | "internal_error";

type ToolResponse<TType extends ToolType, TData> = {
  request_id: string;
  tool_type: TType;
  status: ToolStatus;
  data: TData | null;
  error: ToolError | null;
  warnings: ToolWarning[];
  provider_metadata: ProviderMetadata[];
};
```

| 필드 | 타입 | 항상 포함 | 설명 |
| --- | --- | --- | --- |
| `request_id` | `string` | 예 | A의 요청 ID를 그대로 반환 |
| `tool_type` | `ToolType` | 예 | 요청한 Tool 종류를 그대로 반환 |
| `status` | `ToolStatus` | 예 | Tool 실행 결과 |
| `data` | Tool별 payload 또는 `null` | 예 | 사용할 수 있는 정규화 결과 |
| `error` | `ToolError` 또는 `null` | 예 | 실패·미지원·빈 결과의 구조화 정보 |
| `warnings` | `ToolWarning[]` | 예 | 부분 성공, fallback, 가정 정보 |
| `provider_metadata` | `ProviderMetadata[]` | 예 | C가 호출한 정상 Provider 결과의 출처와 시각 |

상태별 필드 규칙은 다음과 같다.

| `status` | `data` | `error` | 의미 |
| --- | --- | --- | --- |
| `success` | 필수 | `null` | 요청한 데이터가 정상적으로 있음 |
| `partial` | 필수 | `null` | 사용할 데이터는 있으나 일부가 누락됨 |
| `no_data` | `null` 또는 빈 목록 | 필수 | 호출은 성공했지만 조건에 맞는 데이터가 없음 |
| `invalid_input` | `null` | 필수 | 요청 형식·자료형·허용 범위 오류 |
| `unsupported` | `null` | 필수 | 현재 C가 지원하지 않는 지역·기능·시간 범위 |
| `unavailable` | `null` | 필수 | Provider 또는 C 장애로 결과를 확인할 수 없음 |
| `internal_error` | `null` | 필수 | C 내부의 예상하지 못한 오류 |

입력 검증은 C 진입점에서 수행한다. 요청 DTO 자체를 만들 수 없는
`invalid_input`과 예상하지 못한 `internal_error`는 현재 내부 `ToolStatus` enum에
없으므로, A·C 연결 구현 전 내부 enum에 추가하거나 Response Mapper 경계에서
별도 변환해야 한다.

### 7.1 Provider metadata

```ts
type ProviderStatus = "success" | "no_data" | "partial";

type ProviderMetadata = {
  source: ProviderSource;
  status: ProviderStatus;
  retrieved_at: string;
};
```

- `retrieved_at`은 timezone을 포함한 UTC ISO 8601 문자열이다.
- `source`는 Provider 구현 클래스명이 아니라 데이터 출처를 나타낸다.
- 복합 Tool은 여러 Provider 호출 결과를 배열에 호출 순서대로 보존한다.
- Provider 호출 전에 검증이 실패했거나 Provider 결과를 받지 못했다면 빈 배열이다.
- API Key, 인증 헤더, 전체 요청 URL은 포함하지 않는다.

### 7.2 Error

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
  | "unknown"
  | string;

type ToolError = {
  code: ToolErrorCode;
  message: string;
  cause: ToolErrorCause | null;
  retryable: boolean;
  details: Record<string, string>;
};
```

`message`는 A가 사용자 재질문이나 안내문 생성에 참고할 수 있는 비민감 문장이다.
`details`에는 API Key, 인증정보, Provider 원본 응답, 전체 사용자 발화를 넣지 않는다.

### 7.3 Warning

```ts
type ToolWarningCode =
  | "partial_data"
  | "stale_data"
  | "fallback_used"
  | "assumed_data";

type ToolWarning = {
  code: ToolWarningCode;
  message: string;
};
```

현재 C 내부 Tool은 warning을 문자열 tuple로 반환한다. A 응답 경계에서는
`code`와 `message` 객체로 변환하는 Mapper가 필요하다.

## 8. Tool별 Response Data (`Contract`)

### 8.1 `resolve_location`

```ts
type ResolveLocationData = {
  requested_query: string;
  resolved_name: string;
  location: Coordinates;
  administrative_district: string;
  resolution_method: "direct" | "alias" | "fallback";
  confidence: "exact" | "approximate" | "unknown";
  retrieved_at: string;
};
```

`retrieved_at`은 A가 `api_context.gps_location_updated_at`을 구성할 수 있도록
payload에도 제공한다. 값은 위치 Provider metadata의 조회 시각과 같다.
현재 `ResolvedLocation`은 행정구를 결과에 보존하지 않으므로
`administrative_district`를 응답하려면 내부 모델 또는 Response Mapper 입력을
확장해야 한다.

```json
{
  "request_id": "d4d60aa4-2f11-4e43-b3bc-43e82a90bc64",
  "tool_type": "resolve_location",
  "status": "success",
  "data": {
    "requested_query": "경복궁",
    "resolved_name": "경복궁",
    "location": {
      "latitude": 37.5796,
      "longitude": 126.977
    },
    "administrative_district": "종로구",
    "resolution_method": "alias",
    "confidence": "exact",
    "retrieved_at": "2026-07-24T01:00:00Z"
  },
  "error": null,
  "warnings": [],
  "provider_metadata": [
    {
      "source": "naver_geocoding",
      "status": "success",
      "retrieved_at": "2026-07-24T01:00:00Z"
    }
  ]
}
```

### 8.2 `search_nearby_places`

```ts
type PlaceSummary = {
  place_id: string;
  name: string;
  category: string;
  location: Coordinates;
  address: string | null;
  distance_km: number;
  operating_hours: OperatingHours | null;
  detail_status: "success" | "no_data" | "unavailable";
};

type NormalizedTimeRange = {
  start: string;
  end: string;
  crosses_midnight: boolean;
};

type OperatingHours = {
  availability: "scheduled" | "all_day" | "unknown";
  raw_operating_hours: string | null;
  raw_rest_date: string | null;
  cleaned_operating_hours: string | null;
  cleaned_rest_date: string | null;
  parse_status: "parsed" | "partial" | "unknown" | "assumed";
  time_ranges: NormalizedTimeRange[];
  assumption_reason: string | null;
  warnings: string[];
};

type SearchNearbyPlacesData = {
  places: PlaceSummary[];
  count: number;
  search_center: Coordinates;
  radius_km: number;
  elapsed_ms: number;
  retrieved_at: string;
};
```

- 운영시간 원문과 정규화 상태를 함께 제공하되 Provider `raw_common`,
  `raw_intro` 전체는 A에 전달하지 않는다.
- 일부 장소의 상세조회가 실패해도 사용할 장소가 있으면 `partial`로 반환한다.
- `distance_km`는 현재 후보 좌표 기준 직선거리이며 실제 이동시간이 아니다.
- `category`는 현재 C Mapper가 반환하는 정규화 전 분류 문자열이다.

```json
{
  "request_id": "b87c3211-afb5-4c48-bd85-d904b29b6750",
  "tool_type": "search_nearby_places",
  "status": "partial",
  "data": {
    "places": [
      {
        "place_id": "126508",
        "name": "카페 예시",
        "category": "카페",
        "location": {
          "latitude": 37.581,
          "longitude": 126.978
        },
        "address": "서울특별시 종로구",
        "distance_km": 0.3,
        "operating_hours": {
          "availability": "scheduled",
          "raw_operating_hours": "09:00~21:00",
          "raw_rest_date": "연중무휴",
          "cleaned_operating_hours": "09:00~21:00",
          "cleaned_rest_date": "연중무휴",
          "parse_status": "parsed",
          "time_ranges": [
            {
              "start": "09:00",
              "end": "21:00",
              "crosses_midnight": false
            }
          ],
          "assumption_reason": null,
          "warnings": []
        },
        "detail_status": "success"
      }
    ],
    "count": 1,
    "search_center": {
      "latitude": 37.5796,
      "longitude": 126.977
    },
    "radius_km": 2.0,
    "elapsed_ms": 1250.32,
    "retrieved_at": "2026-07-24T01:00:02Z"
  },
  "error": null,
  "warnings": [
    {
      "code": "partial_data",
      "message": "일부 장소의 상세정보를 확인하지 못했습니다."
    }
  ],
  "provider_metadata": [
    {
      "source": "tour_api_place",
      "status": "success",
      "retrieved_at": "2026-07-24T01:00:01Z"
    }
  ]
}
```

### 8.3 `get_place_details`

```ts
type GetPlaceDetailsData = {
  place_id: string;
  name: string | null;
  place_type: PlaceType | "unknown";
  address: string | null;
  overview: string | null;
  homepage: string | null;
  telephone: string | null;
  operating_hours: OperatingHours | null;
  retrieved_at: string;
};
```

Provider 진단용 `raw_common`과 `raw_intro`는 A 응답에서 제외한다.

### 8.4 `get_weather_forecast`

```ts
type GetWeatherForecastData = {
  location: Coordinates;
  grid: {
    x: number;
    y: number;
  };
  sky_code: string | null;
  precipitation_type: string | null;
  data_type: "forecast";
  requested_visit_at: string;
  forecast_for: string;
  observed_at: null;
  retrieved_at: string;
  timezone: "Asia/Seoul";
  timezone_assumed: boolean;
  selection_method: "nearest" | "earliest_available";
};
```

`condition`(`good | neutral | bad`)은 **D-051로 제거됐다.** 날씨는 C가 사실을,
D가 판정을 맡는다 — C는 `sky_code`와 `precipitation_type`만 넘기고, 사용자
`weather_intent`와 합친 3단계 판정은 D의 `judge_weather_condition_from_facts()`가
한다. C가 판정값을 미리 채우면 같은 사실에 대해 두 개의 판정이 생긴다.

현재 Weather Provider는 온도와 습도를 반환하지 않는다. 따라서 A 응답에도
`temperature`, `humidity`를 임의로 추가하지 않는다.

```json
{
  "request_id": "3e23c295-2ea1-4bb7-9c3f-e55a48c922e8",
  "tool_type": "get_weather_forecast",
  "status": "success",
  "data": {
    "location": {
      "latitude": 37.5796,
      "longitude": 126.977
    },
    "grid": {
      "x": 60,
      "y": 127
    },
    "sky_code": "4",
    "precipitation_type": "1",
    "data_type": "forecast",
    "requested_visit_at": "2026-07-24T15:00:00+09:00",
    "forecast_for": "2026-07-24T15:00:00+09:00",
    "observed_at": null,
    "retrieved_at": "2026-07-24T01:00:00Z",
    "timezone": "Asia/Seoul",
    "timezone_assumed": false,
    "selection_method": "nearest"
  },
  "error": null,
  "warnings": [],
  "provider_metadata": [
    {
      "source": "kma_ultra_short_forecast",
      "status": "success",
      "retrieved_at": "2026-07-24T01:00:00Z"
    }
  ]
}
```

### 8.5 `get_concentration`

```ts
type GetConcentrationData = {
  area_code: string;
  district_code: string;
  requested_place_name: string | null;
  forecasts: {
    place_name: string;
    forecast_date: string | null;
    concentration_rate: number | null;
  }[];
  retrieved_at: string;
};
```

`concentration_rate`는 Provider 원본 결과로부터 C가 정규화한 상대 집중률이며
실시간 혼잡률로 표현하지 않는다. Provider `raw_data`는 A에 전달하지 않는다.

### 8.6 `get_holidays`

```ts
type GetHolidaysData = {
  year: number;
  month: number | null;
  holidays: {
    date: string;
    name: string;
    kind: string | null;
    sequence: number | null;
  }[];
  retrieved_at: string;
};
```

`is_holiday=false`인 기념일은 `holidays`에서 제외한다. Provider `raw_data`는 A에
전달하지 않는다.

## 9. A `ApiContext` 연결 (`Contract`, `TI-08`~`TI-09`)

C는 `ApiContext` 자체를 생성하거나 B State를 갱신하지 않는다. 대신 A가
`ApiContext`를 만들 수 있도록 다음 값을 Response에 제공한다.

| A `ApiContext` 필드 | C Response 원본 | 변환 주체 |
| --- | --- | --- |
| `gps_location` | `resolve_location.data.location` | A |
| `gps_location_updated_at` | `resolve_location.data.retrieved_at` | A |

`api_weather` / `api_weather_updated_at`은 **D-038로 무효가 됐다.** 날씨를
세션 컨텍스트에 조회·저장하던 `session_orchestrator`의 경로가 사라져
`api_context.api_weather`는 이제 항상 `null`이다. C Response에서 이 값으로
변환되는 항목은 없다.

A↔C Tool Interface의 위치는 `Coordinates` 객체로 확정했다. 장소명은 A가
`resolve_location`으로 좌표 변환을 요청하고, 성공 응답의 좌표를 후속 Tool에
전달한다.

`TI-09`(A의 `api_weather`가 C의 `good | neutral | bad`를 그대로 사용)는
D-038·D-051로 무효다. C는 3단계 판정값을 만들지 않고, A도 이 값을 받지 않는다.

## 10. 오류 응답 예시 (`Contract`)

```json
{
  "request_id": "3e23c295-2ea1-4bb7-9c3f-e55a48c922e8",
  "tool_type": "get_weather_forecast",
  "status": "unavailable",
  "data": null,
  "error": {
    "code": "unavailable",
    "message": "날씨 정보를 가져오지 못했습니다.",
    "cause": "timeout",
    "retryable": true,
    "details": {}
  },
  "warnings": [],
  "provider_metadata": []
}
```

정상 호출이지만 결과가 없는 경우는 다음과 같이 구분한다.

```json
{
  "request_id": "b87c3211-afb5-4c48-bd85-d904b29b6750",
  "tool_type": "search_nearby_places",
  "status": "no_data",
  "data": null,
  "error": {
    "code": "no_data",
    "message": "조건에 맞는 주변 장소를 찾지 못했습니다.",
    "cause": null,
    "retryable": false,
    "details": {}
  },
  "warnings": [],
  "provider_metadata": [
    {
      "source": "tour_api_place",
      "status": "no_data",
      "retrieved_at": "2026-07-24T01:00:00Z"
    }
  ]
}
```

## 11. 공통 입력 검증 (`Contract`, `TI-10`)

| 상황 | C 판정 |
| --- | --- |
| 필수 필드 누락 | `invalid_input` |
| 자료형 또는 허용 범위 오류 | `invalid_input` |
| 정의되지 않은 `tool_type` | `unsupported` |
| 종로구 밖 위치 요청 | `unsupported` |
| 유효한 요청이나 정상 빈 결과 | `no_data` |
| Provider timeout·인증·네트워크·파싱 실패 | `unavailable` |

명시적인 필수 조건을 C가 임의로 완화하지 않는다. 조건 변경이나 사용자 재질문은
A Runtime이 결정한다.

## 12. A → C 호출 흐름 (`Contract`)

“경복궁 근처 카페와 방문 시각 날씨가 필요하다”는 요청은 v1에서 세 번의 독립
호출로 표현한다.

```text
resolve_location
    ↓ 성공 좌표
search_nearby_places ─┐
                      ├─ A Runtime이 결과 조합
get_weather_forecast ─┘
```

A는 `resolve_location` 결과의 좌표를 장소와 날씨 요청에 전달할 수 있다. 장소와
날씨 요청은 좌표가 확보된 뒤 병렬로 실행할 수 있다.

## 13. 현재 구현 매핑 (`Existing`)

### 13.1 이 문서의 envelope은 실행 경로에 배선돼 있지 않다

**이 계약을 구현한 `app/tool_intelligence/`(`schemas.py`·`service.py`·`mappers.py`)는
`app/` 안 어디에서도 import되지 않는다.** 참조하는 것은
`backend/tests/test_tool_intelligence_contract.py` 하나뿐이다(2026-08-08 확인).

실제 A→C 호출은 이 `ToolRequest`/`ToolResponse` envelope이 아니라
`app/agent_context/service.py`의 Context 단위 인터페이스를 지난다 — 추천은
`RecommendationContext`, INFO는 `InfoContextRequest`/`InfoContextResponse`다.
아래 표의 "구현됨"은 **C 내부 Tool 자체가 동작한다는 뜻이며, 그 tool_type으로
호출이 들어오고 있다는 뜻이 아니다.**

envelope을 실제로 쓸지, 아니면 Context 인터페이스로 일원화하고 이 계약을
`Superseded`로 내릴지는 A와 함께 정할 사항이라 여기서 결정하지 않는다.

### 13.2 tool_type별 매핑

| `tool_type` | C 내부 구현 | 상태 |
| --- | --- | --- |
| `resolve_location` | `ResolveLocationTool` | Tool 구현됨 |
| `search_nearby_places` | `NearbyPlaceDetailsTool` | Tool 구현됨, A 요청 Mapper 필요 |
| `get_place_details` | envelope에 연결된 Tool 없음 | **`unsupported`를 반환한다** (`mappers.py::unsupported_place_details_response`). 장소 상세 단건 조회 자체는 13.3의 `GetPlaceDetailTool`이 담당 |
| `get_weather_forecast` | `GetWeatherForecastTool` | Tool 구현됨 |
| `get_concentration` | `GetConcentrationTool` | Tool 구현됨 |
| `get_holidays` | `GetHolidaysTool` | Tool 구현됨 |

### 13.3 envelope에 없는 Tool

INFO(`question_type`) 확장으로 추가된 다음 두 Tool은 `ToolType`에 없고 Context
인터페이스로만 호출된다. envelope에 편입할지는 13.1의 결정에 딸린 문제다.

| C 내부 Tool | 용도 | 근거 |
| --- | --- | --- |
| `GetPlaceDetailTool` (`tools/place_detail.py`) | 이미 특정된 장소 1건의 상세정보를 이름으로 조회. `NearbyPlaceDetailsTool`과 달리 후보 검색을 하지 않는다 | D-054 |
| `GetFestivalsTool` (`tools/festival.py`) | 지역 행사 목록을 받아 기준일에 진행 중인 것만 남긴다. 장소와의 연결은 좌표를 가진 상위 Service가 판단 | D-055 |

`GetPlaceDetailTool`은 `PLACE_DETAILS_SOURCE`와 무관하게 항상
`PlaceProvider`(TourAPI)를 쓴다. Supabase 상세 캐시는 동기화 대상이
운영시간·휴무일 원문뿐이라 `overview`·편의시설이 비어 있고, 캐시를 둔 근거(후보
N건의 상세조회 제거)가 장소 1건인 INFO에는 적용되지 않기 때문이다. 여기서
캐시를 쓰면 질문 유형 대부분이 조용히 `no_data`로 떨어진다.

## 14. 후속 결정 항목

다음 항목은 현재 v1 envelope와 확정 필드를 변경하지 않는 후속 협의 대상이다.

1. **이 envelope을 존속시킬지 (13.1)** — 실행 경로가 Context 인터페이스로 굳었다.
   envelope을 A 호출 경로로 되살릴지, Context 인터페이스로 일원화하고 이 계약을
   `Superseded`로 내릴지 A와 결정한다. 다른 항목은 이 결정에 딸려 있다.
2. `recommendation_run_id`를 Tool envelope에 추가할지
3. `get_place_details` 호출 시 C가 `content_type_id`를 Context에서 복원하는 방법
4. `GetPlaceDetailTool`·`GetFestivalsTool`을 `ToolType`에 편입할지 (13.3)
5. 계절별·요일별 복수 운영시간을 A가 소비할 최종 구조
6. `partial`, `unknown`, `assumed` 운영시간을 A가 처리하는 정책
7. A가 Provider metadata를 State Snapshot에 그대로 보존할지

## 15. 결정 기록

| 날짜 | 안건 | 결정 | 참여자 | 이유·영향 |
| --- | --- | --- | --- | --- |
| 2026-07-24 | `TI-05` | A가 `resolve_location`을 먼저 요청하고 좌표를 후속 Tool에 전달 | A·C | Tool 책임과 오류 경계를 분리 |
| 2026-07-24 | `TI-08` | A↔C 위치는 위도·경도 객체 사용 | A·C | 순서·파싱 오류 방지 |
| 2026-07-24 | `TI-09` | 날씨는 `good/neutral/bad` 유지 | A·C·D | 기존 C Scoring 호환 유지 |
| 2026-07-24 | 운영시간 | 원문과 정규화된 시간 구간 모두 반환 | A·C | 원본 보존과 계산 지원 |
| 2026-07-24 | metadata | `retrieved_at`은 실제 외부 조회·정규화 완료 시각 | A·C | 데이터 최신성 의미 유지 |
| 2026-08-06 | `TI-09` 무효화 | `get_weather_forecast.data.condition` 제거, §9의 `api_weather` 매핑 삭제 | C | D-038로 `api_weather` 경로가, D-051로 C의 날씨 판정이 각각 사라짐 |
| 2026-08-08 | 구현 현황 정정 | §13에 envelope 미배선 사실과 envelope 밖 Tool 2종을 기록 | C | 문서가 "구현됨"으로 읽히던 상태와 실제 호출 경로가 달랐음. envelope 존속 여부는 §14-1로 넘김 |

## 16. 구현 및 후속 작업

1. Pydantic discriminated union과 `ToolResponse[T]` 유지·검증
2. A 요청 DTO에서 C Tool Query 모델로 변환하는 Mapper 확장
3. C 내부 Tool 결과를 공통 `ToolResponse`로 변환하는 Mapper 확장
4. 후속 결정 항목이 확정되면 v1.x 또는 차기 계약에 반영

# Provider 테스트 가이드

TripBranch의 Geocoding, Weather, Place, Concentration, Holiday Provider 테스트 명령을
한곳에 정리한다. 모든 명령은 `backend` 디렉터리를 기준으로 실행한다.

## 1. 최초 환경 준비

```bash
cd backend
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Python 실행 경로 확인:

```bash
which python
python --version
```

`which python`은 프로젝트의 `backend/.venv/bin/python`을 가리켜야 한다.

`backend/pyproject.toml`의 의존성이 바뀐 커밋을 받은 뒤에도 위 설치 명령을 다시 실행한다.
가상환경은 커밋되지 않아 `git pull`만으로는 새 패키지가 들어오지 않는다. 건너뛰면 테스트가
수집 단계에서 `ModuleNotFoundError`로 멈춘다 —
[개발 가이드](../../docs/development-guide.md)의 "의존성이 바뀐 뒤"를 본다.

## 2. 환경변수

실제 Provider 테스트에는 `backend/.env`의 다음 값이 필요하다.

```env
PROVIDER_MODE=real

NAVER_MAP_CLIENT_ID=...
NAVER_MAP_CLIENT_SECRET=...
WEATHER_API_KEY=...
TOUR_API_SERVICE_KEY=...
```

개별 Provider Override는 선택 사항이다.

```env
GEOCODING_PROVIDER=
WEATHER_PROVIDER=
PLACE_PROVIDER=
CONCENTRATION_PROVIDER=
HOLIDAY_PROVIDER=
```

빈 값이면 `PROVIDER_MODE`를 사용한다. 예를 들어 전체는 Real이지만 Place만
Fake로 실행하려면 다음처럼 설정한다.

```env
PROVIDER_MODE=real
PLACE_PROVIDER=fake
```

## 3. 일반 테스트

전체 테스트:

```bash
python -m pytest -v
```

간결한 출력:

```bash
python -m pytest -q
```

일반 테스트는 로컬 `.env`가 Real이어도 Fake 또는 Mock Provider를 사용하며 실제
외부 API를 호출하지 않는다.

Provider 계약 테스트:

```bash
python -m pytest tests/test_provider_contracts.py -v
```

Place Provider Mock 테스트:

```bash
python -m pytest tests/test_place_provider.py -v
```

집중률 Provider Mock 테스트:

```bash
python -m pytest tests/test_concentration_provider.py -v
```

공휴일 Provider Mock 테스트:

```bash
python -m pytest tests/test_holiday_provider.py -v
```

설정 전환 테스트:

```bash
python -m pytest tests/test_provider_settings.py -v
```

추천 파이프라인 집중 테스트:

```bash
python -m pytest \
  tests/test_candidate_mapper.py \
  tests/test_provider_contracts.py \
  tests/test_recommendation_pipeline.py \
  tests/test_recommendations.py \
  -v
```

다음을 Fake/Mock Provider로 검증하며 실제 외부 API는 호출하지 않는다.

- 거리순 후보 예산 내 Candidate 변환과 Scoring
- 최종 추천 수 제한
- Scoring 상위 후보만 집중률 후조회
- 날씨 실패 시 날씨 Feature를 제외한 가중치 재분배
- 장소 목록 실패의 `unavailable` 변환
- 이미 노출된 `place_id` 제외
- Fake 장소 카테고리 정규화·필터링과 `category_filter` 우선순위
- 월요일 정기휴무와 운영시간 시작·마감 경계

## 4. 코드 검사

Ruff:

```bash
python -m ruff check app tests
```

Python 컴파일 검사:

```bash
python -m compileall -q app tests
```

## 5. 로컬 추천 API 통합 확인

`backend/.env`에서 Provider 모드와 추천 후보 예산을 설정한다.

```env
PROVIDER_MODE=real
RECOMMENDATION_RESULT_LIMIT=5
RECOMMENDATION_CANDIDATE_LIMIT=30
```

첫 번째 터미널에서 서버를 실행한다.

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

다음 로그가 나오면 서버 준비가 완료된 것이다.

```text
Uvicorn running on http://127.0.0.1:8000
Application startup complete.
```

두 번째 터미널에서 Health API를 확인한다. Health 경로는 `/health`가 아니라
`/api/health`다.

```bash
curl http://127.0.0.1:8000/api/health
```

정상 응답:

```json
{"status":"ok"}
```

추천 API를 호출한다.

```bash
curl -X POST http://127.0.0.1:8000/api/recommendations \
  -H "Content-Type: application/json" \
  -d '{
    "location_query": "경복궁",
    "preferred_categories": ["museum"],
    "weather_condition": null,
    "search_radius_km": 2.0,
    "shown_place_ids": []
  }'
```

확인할 내용:

- `recommendations`와 `unverified_recommendations`의 합이 설정된 결과 상한 이내
  (기본 `RECOMMENDATION_RESULT_LIMIT=5`)
- 응답 최상위 `elapsed_ms`에 전체 추천 파이프라인 처리시간이 millisecond로 표시
- 이미 표시한 장소는 `shown_place_ids`에 넣으면 다음 응답에서 제외
- Real 모드는 거리순 후보 최대 10개의 상세정보를 API로 조회
- Scoring 순위가 확정된 뒤 상위 후보에 대해서만 집중률 조회
- 집중률 결과가 없거나 실패해도 기존 추천 순위와 장소는 유지

Real 모드에서는 후보마다 `detailCommon2`, `detailIntro2`를 호출하므로 응답에
수십 초가 걸릴 수 있다. 서버 터미널의 traceback 또는 HTTP 오류를 함께 확인한다.
테스트가 끝나면 서버 터미널에서 `Ctrl+C`로 종료한다.

`curl: (28) Failed to connect`는 서버가 실행되지 않았거나 시작 완료 전이라는
뜻이다. `{"detail":"Not Found"}`는 서버는 실행 중이지만 요청 경로가 잘못됐다는
뜻이다.

## 6. 실제 API Smoke Test

Smoke Test는 실제 외부 API를 호출하고 최소 응답 계약을 검증한다.

전체 실행:

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke -v -s
```

### C ContextService 전체 조립

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_context_service_real_smoke \
  -v -s
```

A의 경복궁·카페 조건 한 건으로 Geocoding, Weather, Place, Holiday Real
Provider를 호출하고 C의 공통 `AgentContextResponse`까지 조립되는지 검증한다.
외부 호출량을 제한하기 위해 장소 후보는 최대 3개만 상세조회하며, 결과에는 최상위
상태, 장소 수, metadata의 Provider source 목록을 출력한다. Concentration은
A–C v0 초기 Context 범위에 포함되지 않아 이 테스트에서 호출하지 않는다.

### Naver Geocoding

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_naver_geocoding_real_smoke \
  -v -s
```

경복궁 질의를 Naver Geocoding에 전달하고 종로구 범위의 좌표가 반환되는지
검증한다.

Tool의 alias 우선 조회, 원문 fallback, 종로구 제한, 모호한 결과와 Provider 장애
분리는 실제 API를 호출하지 않는 다음 테스트로 확인한다.

```bash
python -m pytest tests/test_resolve_location_tool.py -v
```

### KMA 날씨

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_kma_weather_real_smoke \
  -v -s
```

경복궁 좌표를 KMA 격자로 변환하고 `GetWeatherForecastTool`이 현재와 가장 가까운
예보를 선택하는지 검증한다. condition과 `forecast_for`가 출력된다.

시간대 가정, 동률 미래 우선, 범위 밖, 빈 예보와 장애 분리는 다음 단위 테스트로
검증한다.

```bash
python -m pytest tests/test_weather_forecast_tool.py -v
```

### TourAPI 좌표 기반 장소 검색

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_tour_api_place_real_smoke \
  -v -s
```

경복궁 반경 1km 장소 목록이 반환되는지 검증한다.

### TourAPI 키워드 검색 및 상세정보

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_tour_api_keyword_and_details_real_smoke \
  -v -s
```

다음 흐름을 검증한다.

```text
find_details_by_name("경복궁")
→ searchKeyword2에서 장소명 정확 일치 후보 선택
→ contentid/contenttypeid 확보
→ detailCommon2
→ detailIntro2
→ PlaceDetails
```

정확히 일치하는 장소가 없으면 유사 후보를 임의 선택하지 않고
`place_not_found` 오류를 반환한다.

### TourAPI 관광지 집중률

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_tour_api_concentration_real_smoke \
  -v -s
```

`areaCd=11`, `signguCd=11110`, `tAtsNm=경복궁` 조건으로 날짜별 집중률이
반환되고 숫자로 정규화되는지 검증한다.

### 한국천문연구원 공휴일

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest \
  tests/test_provider_smoke.py::test_kasi_holiday_real_smoke \
  -v -s
```

`getRestDeInfo`의 2026년 공휴일을 조회하고 날짜·명칭·휴일 여부가 정규화되는지
검증한다. 인증에는 기존 `TOUR_API_SERVICE_KEY`를 공공데이터포털 서비스 키로
사용한다.

## 7. 실제 요청·원본 응답 Inspection Test

Inspection Test는 실제 API 요청 파라미터, HTTP 상태, 원본 JSON 응답과 정규화
결과를 출력한다. `-s` 옵션이 없으면 출력이 보이지 않을 수 있다.

인증 쿼리와 헤더는 `<redacted>`로 마스킹된다. 실패 로그를 공유하기 전에도
요청 URL에 인증키가 포함되지 않았는지 반드시 확인한다.

### Fake 추천 API 요청·응답

```bash
RUN_RECOMMENDATION_INSPECTION=true python -m pytest \
  tests/test_recommendation_inspection.py \
  -v -s
```

외부 API를 호출하지 않고 금요일 카페 추천, 월요일 전체 추천, 월요일 휴무
카테고리의 요청·응답 JSON과 `elapsed_ms`를 출력한다. 이 테스트는
`RUN_RECOMMENDATION_INSPECTION=true`가 없으면 일반 테스트에서 건너뛴다.

실제 Provider 전체 실행:

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest -m inspection -v -s
```

### Naver Geocoding 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_naver_geocoding_request_and_response \
  -v -s
```

### KMA 날씨 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_kma_weather_request_and_response \
  -v -s
```

### TourAPI 좌표 기반 장소 검색 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_place_request_and_response \
  -v -s
```

### TourAPI 키워드 검색·상세정보 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_keyword_and_details_request_and_response \
  -v -s
```

이 테스트는 `searchKeyword2`, `detailCommon2`, `detailIntro2`의 원본 응답을
순서대로 출력한다.

### TourAPI 카페 대·중·소분류 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_cafe_category_request_and_response \
  -v -s
```

경복궁 반경 5km를 기준으로 `contentTypeId=39`, `lclsSystm1=FD`,
`lclsSystm2=FD05`, `lclsSystm3=FD050100`을 전달한다. 출력되는 요청 쿼리에서
분류 필드를 확인하고, 원본 응답과 정규화된 장소명·`content_type_id` 표본을 함께
검증한다. `serviceKey`는 `<redacted>`로 표시된다.

### 경복궁 인근 10개 장소 목록·상세정보 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_nearby_place_details_request_and_response \
  -v -s
```

경복궁 좌표 반경 2km에서 거리순 후보를 조회하고, 경복궁 자체를 제외한 최대 10개
장소에 `NearbyPlaceDetailsTool`을 통해 `detailCommon2`와 `detailIntro2`를 호출한다.
후보 검색과 상세조회는 분리된 Protocol로 주입되며, 이 테스트에서는 하나의
`RealPlaceProvider`가 두 역할을 담당한다. 상세조회는 동시에 최대 3개만 실행하며,
일부 상세조회가 실패해도 다른 장소 결과는 계속 수집한다. 마지막에는
장소 ID·유형·주소·좌표·신분류 코드와 소개·홈페이지·전화번호·운영시간·휴무
정보를 정규화한 요약을 출력한다. `operating_schedule`에는 `availability`,
`parse_status`, 가정 사유, 정리된 원문, 월·요일·시간 구간, 입장마감, 휴무 규칙과
warning이 포함되어 실제 파싱 결과를 장소별로 확인할 수 있다. 원본 응답에서는
장소 유형별 `usetime*`와
`restdate*` 필드도 확인할 수 있다. 요청과 원본 응답의 인증정보는 마스킹된다.
각 외부 요청에는 응답 본문 수신 완료까지 걸린 `elapsed_ms`가 표시되며, 전체
목록·상세조회에는 Tool 상태와 `normalized_nearby_total_elapsed_ms`가 함께 출력된다.
장소 10개는 후보 목록 1회와 장소별 상세 API 2회로 최대 21회의 외부 요청을
발생시킨다. 2026-07-23 동시성 3의 로컬 실측은 약 20초였으며, 이는 환경에 따라
달라지는 참고값이다. 실서비스 성능 고려사항과 DB 전환 방향은
[Provider Contract v1의 10.11절](./provider-contract-v1.md#1011-다건-상세조회-성능-제한과-db-전환-고려사항)을
참고한다.

### TourAPI 집중률 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_concentration_request_and_response \
  -v -s
```

경복궁의 날짜별 `baseYmd`, `cnctrRate` 원본값과 `ConcentrationForecast`
정규화 결과를 출력한다.

### 한국천문연구원 공휴일 요청·응답

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_kasi_holiday_request_and_response \
  -v -s
```

인증키가 마스킹된 요청 파라미터, 원본 XML 응답, 정규화된 공휴일 목록을 출력한다.

## 8. 결과 해석

```text
PASSED      요청과 최소 응답 계약 검증 성공
FAILED      인증, 권한, 네트워크, 응답 형식 또는 데이터 검증 실패
SKIPPED     실제 호출 실행 플래그가 없거나 필요한 키가 없음
DESELECTED  선택한 marker 또는 단일 테스트 외의 테스트를 실행 대상에서 제외
```

대표적인 실패 원인:

```text
401/403                 인증키 또는 활용 권한 문제
provider_timeout        외부 API 응답 시간 초과
provider_unavailable    외부 API 장애 또는 예상하지 못한 응답
빈 장소 목록            검색 반경·키워드·지역 조건 또는 데이터 부족
집중률 파싱 실패        원본 응답 필드 변경 가능성
```

## 9. 권장 실행 순서

```bash
python -m ruff check app tests
python -m pytest -q
RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke -v -s
```

원본 요청과 응답 확인이 필요할 때만 Inspection Test를 추가로 실행한다.

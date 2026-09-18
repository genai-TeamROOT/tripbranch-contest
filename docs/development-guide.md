# TripBranch 개발 가이드

## 1. 요구 환경

- Node.js 24 이상 (`.nvmrc`, `package.json`의 `engines` 기준)
- Python 3.11 이상 (`backend/pyproject.toml` 기준)
- npm
- macOS/Linux 명령을 기준으로 작성; Windows는 가상환경 활성화 명령이 다름

## 2. 최초 설치

```bash
git clone <repository-url>
cd TripBranch
npm ci

cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
cd ..

cd frontend
npm ci
cp .env.example .env
cd ..
```

저장소 원격 URL은 환경마다 다르므로 `<repository-url>`로 표시했습니다.

Windows PowerShell 가상환경 활성화:

```powershell
backend\.venv\Scripts\Activate.ps1
```

### 의존성이 바뀐 뒤

`backend/pyproject.toml`의 의존성이 바뀐 커밋을 받으면 백엔드 패키지를 다시 설치합니다.
가상환경은 커밋되지 않으므로 `git pull`만으로는 새 패키지가 들어오지 않습니다.

```bash
cd backend
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

설치를 건너뛰면 테스트가 실패하는 것이 아니라 수집 단계에서 `ModuleNotFoundError`로
멈춥니다. 변경한 코드와 무관한 파일까지 한꺼번에 죽어 원인이 자기 변경처럼 보이므로,
여러 테스트 파일이 갑자기 수집 실패하면 이 절차를 먼저 확인합니다.

## 3. 환경변수

### Backend

실제 값은 `backend/.env`에 두며 커밋하지 않습니다.

| 변수 | 기본/예시 | 현재 사용 |
| --- | --- | --- |
| `APP_ENV` | `local` | 설정값 보관 |
| `PROVIDER_MODE` | `fake` | Provider 공통 Fake/Real 모드 |
| `GEOCODING_PROVIDER` | 빈 값 | Geocoding 개별 Override |
| `LOCAL_SEARCH_PROVIDER` | 빈 값 | Naver Local Search 개별 Override |
| `WEATHER_PROVIDER` | 빈 값 | Weather 개별 Override |
| `PLACE_PROVIDER` | 빈 값 | Place 개별 Override |
| `CONCENTRATION_PROVIDER` | 빈 값 | Concentration 개별 Override |
| `HOLIDAY_PROVIDER` | 빈 값 | Holiday 개별 Override |
| `LLM_PROVIDER` | 빈 값 | Fake/Real LLM 개별 Override |
| `PLACE_DETAILS_SOURCE` | `supabase` | 장소 상세·운영정보 출처 (`supabase`/`tour_api`). 추천은 후보 전량의 상세를 받으므로 `tour_api`면 후보 1곳당 호출 2회가 나간다 |
| `LLM_FAST_MODEL_NAME` | `gemini-3.5-flash-lite` | 짧은 구조화 판단(분류·조건 추출)용 1순위 모델 |
| `LLM_FAST_FALLBACK_MODEL_NAMES` | `gemini-3.5-flash` | 위 모델 실패 시 폴백(콤마 구분) |
| `LLM_GENERATION_MODEL_NAME` | `gemini-3.5-flash` | 사용자 문장·일정 생성용 1순위 모델 |
| `LLM_GENERATION_FALLBACK_MODEL_NAMES` | `gemini-3.5-flash-lite` | 위 모델 실패 시 폴백(콤마 구분) |
| `MODE_JUDGE_MODEL_NAME` | `gemini-3.1-flash-lite` | 구간 이동수단(도보·대중교통) 판정 전용 모델. 비우면 `LLM_GENERATION_MODEL_NAME`을 쓴다. 프롬프트 `mode_judge.select` 1.1.0과 짝이다 |
| `MODE_JUDGE_FALLBACK_MODEL_NAMES` | 빈 값 | 위 모델 실패 시 폴백(콤마 구분). 비우면 생성 모델 묶음 전체를 쓴다 |
| `GEMINI_AUDIO_MODEL_NAME` | 빈 값 | 음성→텍스트 전용 모델. 비우면 `LLM_FAST_MODEL_NAME`을 쓴다 |
| ~~`LLM_MODEL_NAME`~~ / ~~`LLM_FALLBACK_MODEL_NAMES`~~ | — | **폐지됐다.** 남아 있으면 부팅에서 막는다(D-042) — 역할별 위 네 개로 대체 |
| `NAVER_MAP_CLIENT_ID` | 빈 값 | Real Geocoding |
| `NAVER_MAP_CLIENT_SECRET` | 빈 값 | Real Geocoding |
| `NAVER_LOCAL_SEARCH_CLIENT_ID` | 빈 값 | Real Naver Local Search API Key ID |
| `NAVER_LOCAL_SEARCH_CLIENT_SECRET` | 빈 값 | Real Naver Local Search API Key |
| `WEATHER_API_KEY` | 빈 값 | Real Weather |
| `TOUR_API_SERVICE_KEY` | 빈 값 | Place, Concentration, Holiday |
| `LLM_API_KEY` | 빈 값 | Real Gemini |
| `SUPABASE_URL` | 빈 값 | Place 동기화 저장소. `PLACE_DETAILS_SOURCE` 기본값이 `supabase`라 real 모드에서는 필수 |
| `SUPABASE_SECRET_KEY` | 빈 값 | Place 동기화 저장소. `PLACE_DETAILS_SOURCE` 기본값이 `supabase`라 real 모드에서는 필수 |
| `STATE_STORE_BACKEND` | `memory` | Package B State(세션·이력·트레이스) 저장소 (`memory`/`supabase`) |
| `EXTERNAL_API_TIMEOUT_SECONDS` | `10` | Real Provider(TourAPI/Naver/Supabase 등, LLM 제외) timeout |
| `LLM_API_TIMEOUT_SECONDS` | 빈 값(EXTERNAL_API_TIMEOUT_SECONDS로 폴백) | Gemini 전용 timeout — Tool/DB와 분리(2026-08-11, EXTERNAL_API_TIMEOUT_SECONDS를 Gemini 지연 대응으로 올리면 TourAPI/Naver/Supabase까지 같이 오래 기다리는 문제로 분리) |
| `RECOMMENDATION_RESULT_LIMIT` | `5` | Scoring 후 반환할 최대 추천 수 |
| `RECOMMENDATION_CANDIDATE_LIMIT` | `30` | 거리순으로 상세조회·평가할 후보 수 (상한 30). `PLACE_DETAILS_SOURCE=tour_api`에서는 10을 넘기면 부팅이 막힌다 — 후보 1곳당 TourAPI 호출 2회라 일일 한도가 금방 소진된다 |
| `EXTERNAL_API_RETRY_COUNT` | `2` | Gemini 호출에만 적용(재시도 루프 소비). 그 외 Real Provider는 이 값을 안 쓴다 |
| `FAKE_WEATHER_SKY_CODE` | `4` | Fake Weather의 기상청 SKY 코드 (`1` 맑음/`3` 구름많음/`4` 흐림) |
| `FAKE_WEATHER_PRECIPITATION_TYPE` | `0` | Fake Weather의 기상청 PTY 코드 (`0` 없음/`1` 비/`2` 비눈/`3` 눈) |
| `FAKE_CURRENT_DATETIME` | 고정 ISO 시각 | 예약값; 현재 추천 로직에서 미사용 |

`PROVIDER_MODE=real`이면 개별 값이 비어 있는 모든 Provider가 Real 모드가 됩니다.
특정 Provider만 Fake로 유지하려면 예를 들어 `PLACE_PROVIDER=fake`를 지정합니다.

`LOCAL_SEARCH_PROVIDER=real`은 Naver Maps Geocoding과 별도의 Naver API Hub
자격 증명(`NAVER_LOCAL_SEARCH_CLIENT_ID`, `NAVER_LOCAL_SEARCH_CLIENT_SECRET`)을
사용합니다. 이 Provider는 저장된 `places`의 정확한 이름 매칭에 실패했을 때 장소명
검색 결과를 좌표로 보완하며, 후보가 여러 개인 경우 임의 선택하지 않고 기존 위치 해석
흐름으로 넘깁니다. 위치 해석은 도로명·지번 주소 패턴(예: `인사동길 44`,
`관훈동 38`)이면 Geocoding을 먼저 호출하고, 그 외 장소명은 저장된 장소 정확 일치 →
Local Search → Geocoding 순서로 처리합니다.

`PLACE_DETAILS_SOURCE`는 Fake/Real과 축이 다릅니다. 장소 후보 **검색**은 언제나
`PLACE_PROVIDER`를 따르고, 이 값은 후보별 **상세·운영정보**를 어디서 읽을지만
고릅니다. `supabase`로 두면 후보마다 TourAPI 상세 API를 호출하는 대신 미리
동기화된 `places` 테이블을 `content_id`로 한 번에 조회합니다(후보 10건 기준
평균 18.0초 → 0.33초, `backend/scripts/compare_place_details_latency.py` 측정).
`SUPABASE_URL`/`SUPABASE_SECRET_KEY`가 필요하며, 비어 있으면 부팅 단계에서
실패합니다. 요청 도중 TourAPI로 자동 폴백하지 않으므로 저장소 장애는 그대로
`unavailable`로 노출됩니다. `PLACE_PROVIDER=fake`이면 상세도 Fake Provider가
담당하므로 이 값은 무시됩니다.

`STATE_STORE_BACKEND`도 Fake/Real과 축이 다릅니다. Package B의 세션 상태·추천
이력·트레이스를 어디에 저장할지만 고르며, `supabase`로 두면
`SUPABASE_URL`/`SUPABASE_SECRET_KEY`가 필요하고 비어 있으면 부팅 단계에서
실패합니다(`validate_provider_config()`가 함께 검증).

`PROVIDER_MODE`와 `*_PROVIDER`에는 `fake`와 `real`만 허용됩니다. 오타나 옛 이름을
넣으면 앱이 기동하지 않고 어떤 변수가 잘못됐는지 즉시 보고합니다. Real 모드에
필요한 키가 비어 있는 경우에도 첫 요청이 아니라 부팅 단계에서 실패하며, 누락된
키를 한 번에 모아서 알려줍니다(`app/providers/factory.py`의
`validate_provider_config()`가 `app/main.py` lifespan에서 실행됩니다).

#### Real Provider 실패 시 Fake로 전환하지 않는다

Real Provider 호출이 실패해도 요청 도중 Fake Provider로 낮추지 않습니다. 실패는
`unavailable` 상태로 사용자에게 드러나거나, 같은 성격의 다른 **Real** 경로로 넘어갑니다.
Fake는 `PROVIDER_MODE=fake` 또는 `*_PROVIDER=fake`로 **명시적으로 선택했을 때만**
사용됩니다(D-042).

조용한 폴백을 넣지 않는 이유는, 그러면 개발자도 사용자도 "지금 보고 있는 게 실데이터인지"
알 수 없기 때문입니다. 실제로 `npm run dev`가 `backend/.env`를 읽지 못해 전 Provider가
fake로 뜬 적이 있는데, 오류 없이 "테스트 카페"가 추천돼 원인을 찾는 데 시간이 걸렸습니다.
같은 이유로 부팅 시 자격증명을 검증하고, Provider 모드를 로그로 남깁니다.

| 상황 | 처리 | 이유 |
|------|------|------|
| Naver Local Search 실패 | Geocoding으로 진행 | Fake가 아니라 다른 Real Provider |
| Supabase 상세조회 실패 | `unavailable` 반환 | 요청 중 TourAPI fallback을 하지 않기로 결정 |
| 기상청 실패 | 날씨 없이 `partial` | 날씨는 선택 정보 |
| Geocoding 실패 | `unavailable` | 검색 중심점이 없으면 추천 자체가 불가 |

즉 "Real → 다른 Real"이나 "실패를 드러낸 채 진행"은 허용되고, "Real → Fake"만 금지됩니다.
재시도는 이 정책과 별개로, 같은 Real Provider를 다시 부르는 것은 허용됩니다.

API 키는 채팅, 로그, 테스트 traceback, 커밋에 포함하지 않습니다. 새 키 환경변수를
도입할 때는 `app/config.py`, `.env.example`, 실제 로컬 `.env`의 변수명을 함께
정리하되 `.env`의 값은 공유하지 않습니다.

### Frontend

| 변수 | 기본/예시 | 설명 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 빈 값 | 비우면 동일 출처 `/api` 사용 |
| `VITE_SHOW_INTERPRETATION_DEBUG` | `true` | Interpret 디버그 카드 표시 |

`VITE_` 값은 브라우저 번들에 포함되므로 비밀정보를 넣지 않습니다. 변경 후 Vite
개발 서버를 재시작합니다.

## 4. 로컬 실행

루트에서 Backend 가상환경을 활성화한 뒤 실행합니다.

```bash
source backend/.venv/bin/activate
npm run dev
```

이 명령은 다음 서버를 함께 실행합니다.

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`

개별 실행이 필요하면 각 `package.json`과 `scripts/dev.mjs`를 기준으로 하며,
문서에 별도의 미확인 명령은 추가하지 않습니다.

## 5. 일반 검사

루트 통합 명령:

```bash
npm run lint
npm run test
npm run build
```

Backend만 검사:

```bash
cd backend
python -m ruff check app tests
python -m pytest -q
```

Frontend만 검사:

```bash
cd frontend
npm run lint
npm run test -- --run
npm run build
```

## 6. Provider 테스트

일반 pytest는 `tests/conftest.py`에 의해 로컬 `.env`가 Real이어도 Fake/Mock
Provider를 사용합니다. 실제 외부 API는 명시적 marker와 환경 플래그로만 호출합니다.

```bash
cd backend

# 전체 Mock/단위 테스트
python -m pytest -q

# 모든 실제 Smoke Test
RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke -v -s

# 마스킹된 요청과 원본 응답 확인
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest -m inspection -v -s
```

장소명·주소별 위치 해석 Tool의 실제 호출 경로를 확인하려면 아래처럼 실행합니다.
출력에는 호출된 Local Search/Geocoding 입력, Local Search 후보 수·이름,
Provider source, 상태·오류 원인, 최종 해석 방식만 표시하며 인증 키와 원본 응답은
표시하지 않습니다.

```bash
# 장소명: Local Search 호출 후 성공하면 Geocoding 미호출
RUN_REAL_PROVIDER_INSPECTION=true LOCAL_SEARCH_PROVIDER=real \
  python -m pytest tests/test_resolve_location_inspection.py::test_inspect_place_name_calls_local_search_before_geocoding -v -s

# 주소: Geocoding만 직접 호출
RUN_REAL_PROVIDER_INSPECTION=true GEOCODING_PROVIDER=real \
  python -m pytest tests/test_resolve_location_inspection.py::test_inspect_address_calls_geocoding_directly -v -s
```

특정 장소 상세정보 확인:

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_tour_api_keyword_and_details_request_and_response \
  -v -s
```

공휴일 확인:

```bash
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest \
  tests/test_provider_inspection.py::test_inspect_kasi_holiday_request_and_response \
  -v -s
```

전체 명령은 [`backend/docs/provider-test-guide.md`](../backend/docs/provider-test-guide.md)를
참고합니다.

## 7. 코드 구조와 구현 규칙

- Backend Python 필드와 Backend JSON 필드는 모두 `snake_case`를 사용합니다.
- Python↔JSON 직렬화에 camelCase alias를 추가하지 않습니다.
- Frontend API 타입도 Backend JSON의 `snake_case` 필드명을 그대로 선언합니다.
- Frontend 내부 컴포넌트 상태는 TypeScript 관례를 따를 수 있으나 API DTO와 섞지
  않습니다.
- 외부 API의 `serviceKey`, `contentid` 같은 원본 이름은 Provider/Mapper 경계에서만
  사용하고 내부 모델로 정규화합니다.
- 공개 HTTP 모델은 `backend/app/schemas.py`에 둡니다.
- 외부 API와 독립적인 Provider 모델은 `backend/app/domain/models.py`에 둡니다.
- Provider 계약은 `backend/app/providers/protocols.py`에 정의합니다.
- Real/Fake 구현은 동일한 비동기 계약을 지켜야 합니다.
- 외부 응답 필드 차이는 Provider/Mapper에서 흡수합니다.
- 서비스·추천 코드는 Provider 원본 JSON/XML을 직접 사용하지 않습니다.
- 새로운 Real Provider에는 Mock 단위 테스트, Smoke Test, Inspection Test를 함께 둡니다.
- Inspection 출력에서는 query/header 인증정보를 마스킹합니다.
- Frontend API 타입은 현재 수동 관리 중이며 OpenAPI 자동 생성은 `TBD`입니다.

## 8. 개발 흐름 권장안

1. 관련 문서와 현재 계약 확인
2. Fake/Protocol부터 변경해 호출부 계약 확정
3. Real Provider/Service 구현
4. 단위 테스트와 오류 케이스 추가
5. Ruff, Backend/Frontend 테스트 실행
6. 실제 API가 필요한 경우에만 Smoke/Inspection 실행
7. API 키와 `.env`가 스테이징되지 않았는지 확인
8. 관련 변경만 선별 커밋

브랜치·PR naming 규칙과 필수 리뷰 인원은 저장소에서 확인되지 않아 `TBD`입니다.

## 9. 현재 알려진 제약

- Interpret는 Fake/Real LLM Provider와 Backend 세션 상태 병합을 사용합니다.
- `/api/recommendations`는 Fake/Real이 동일한 Tool·Candidate·Scoring 경로를 사용합니다.
  A Runtime의 D 연결만 현재 Runtime 전용 Fake 구현을 사용합니다.
- 가중치 Scoring 엔진(Scoring v1, `backend/app/domain/scoring.py`)은
  `backend/app/services/recommendation_pipeline.py`를 통해
  `/api/recommendations` 라우트에 연결되어 있으며, 추천 결과에는 점수 근거
  (`score`/`feature_scores`/`weights_used`)도 함께 노출됩니다(D-028). 이동시간
  계산(실제 경로 기반)은 아직 미구현이며 직선거리로 대체합니다.
- Weather, Concentration, Holiday Provider는 추천 파이프라인에 연결되어
  있습니다.
- `EXTERNAL_API_RETRY_COUNT`는 설정만 있고 실제 재시도에 사용되지 않습니다.
- Frontend는 목표의 `localStorage`가 아니라 현재 `sessionStorage`를 사용합니다.
- Supabase, 인증, 배포, Docker 구성은 없습니다.

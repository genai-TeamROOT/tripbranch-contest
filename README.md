# TripBranch

사용자의 자연어 요청과 현재 상황을 해석해 서울 지역의 장소 후보를 수집하고,
조건별 가중치를 적용해 적합한 장소를 추천하는 서비스입니다.

> 현재 단계: **Phase 1-A — Core AI Agent Flow**
> 통합 Chat API(`POST /api/chat`)가 프론트 실사용 경로로 연결돼 있고, Intent 분류부터
> 조건 병합·외부 데이터 조회·추천 점수 계산·자연어 응답 생성까지 한 번의 호출로
> 처리합니다. 이동시간 Tool, 블로그 근거 수집, 외부 데이터 스냅샷 저장은 아직
> 구현 전입니다.

## 해결하려는 문제

여행 중에는 날씨, 남은 시간, 이동 거리, 운영 여부, 혼잡도처럼 상황이 계속
바뀝니다. TripBranch는 “경복궁 근처인데 비가 와”, “여기는 너무 붐벼” 같은
자연어 요청을 구조화하고 이전 대화 맥락과 현재 외부 데이터를 결합해 다음 장소를
추천합니다.

사용자 요청은 다음 흐름으로 처리됩니다.

```text
사용자 자연어 입력
→ POST /api/chat
→ Intent 분류 및 Intent별 조건 추출 (A)
→ 이전 대화 조건과 병합 (B)
→ Tool / Provider를 통한 외부 데이터 보완 (C)
→ 가중치 기반 후보 평가와 근거 생성 (D)
→ 자연어 응답 조립
→ 프론트엔드 표시
```

`/api/interpret`과 `/api/recommendations`도 등록돼 있지만 현재는 개발용 디버그
패널 전용입니다. 계층별 책임과 남은 차이는
[아키텍처 문서](docs/architecture.md)에 정리되어 있습니다.

## 지원 Intent

사용자 발화는 항상 하나의 Intent로 분류되고, 조건은 여러 개가 동시에 붙을 수
있습니다. 상세 정의는 [Intent 정의](docs/design/intent-definition.md)를 따릅니다.

| Intent | 내용 | 상태 |
| --- | --- | --- |
| `RECOMMEND` | 조건에 맞는 장소 추천 | 구현됨 |
| `SCHEDULE` | 여러 장소를 시간 순서로 묶은 일정·코스 편성 | 구현됨. 부분 수정(순번·이름 지목) 포함 |
| `INFO` | 특정 장소의 정보 조회 | 구현됨. `question_type` 8종 |
| `MODIFY` | 기존 추천의 조건 변경과 재추천 | 구현됨 |
| `COMPARE` | 추천받은 장소 간 비교 | 구현됨 |
| `GENERAL` | 여행 관련 배경지식 질문 | 구현됨. LLM 답변 |
| `OUT_OF_SCOPE` | 서비스 범위를 벗어난 요청 차단 | 구현됨 |

조건이 모호하면 `needs_clarification` 상태로 되묻고, 사용자의 답변을 같은 Intent의
후속 턴으로 이어받습니다.

## 현재 주요 기능

### 대화와 응답

- 통합 `POST /api/chat`과 SSE 스트리밍 `POST /api/chat/stream`
- Agent 진행 상태와 LLM 답변을 순서대로 전달하는 스트리밍 이벤트
- Intent 분류, Intent별 구조화 출력 추출, 출력 검증
- 멀티턴 되묻기와 되묻기 답변의 Intent 유지
- Rule 기반 카드 문장 조립 + 요약·GENERAL 답변의 LLM 생성
- Backend 세션 상태의 Add/Update/Remove/Keep 조건 병합과 이력 관리
- 역할별 Gemini 모델 묶음(판단·생성·추천 이유·이동수단 판정)과 호출 실패 시 같은 벤더 내 대체 모델 fallback

### 추천과 일정

- 위치·장소·날씨 Tool → Candidate → Scoring → 상위 추천 파이프라인
- 날씨·남은 운영시간·거리 Feature 가중치 점수와 결정적 정렬 (Scoring v1)
- 이전 노출·거절 장소 제외, 운영시간 미확인과 폐점의 구분
- Feature별 기여도 기반 근거 문장(`explanations`)과 경고(`warnings`) 생성
- Scoring 상위 후보에 한정한 집중률 후조회
- 후보·거리·조건을 받아 LLM으로 일정을 편성하는 Schedule Planner
- 활동 가능 시간에 따른 일정 항목 수 조정과 후보 부족 시 안내 반환
- 날씨·동행·무장애 조건을 보고 일정 구간과 추천 후보의 도보·대중교통을 정하는 이동수단 판정(실패 시 직선거리 규칙)

### 외부 데이터 (Tool·Provider)

- Fake/Real Provider 전환 구조와 부팅 시 설정 검증
- Naver Geocoding 기반 위치 좌표 변환, Naver Local Search 좌표 보완
- 종로구 범위·alias fallback·모호성 검증을 적용하는 `ResolveLocationTool`
- 기상청 초단기예보 기반 방문 시각 날씨 조회 (`GetWeatherForecastTool`)
- TourAPI 위치 기반 검색·키워드 검색·상세조회
- 주변 후보와 다건 상세정보를 결합하는 `NearbyPlaceDetailsTool`
- 특정된 장소 1건을 조회하는 `GetPlaceDetailTool`
- 기준일에 진행 중인 지역 행사를 찾는 `GetFestivalsTool`
- 관광지 집중률 예측 조회와 데이터 없는 장소의 근접 대체 장소 선택
- 한국천문연구원 공휴일 조회
- TourAPI 운영시간·휴무 원문 보존과 요일별 구조화, 정기 휴무 유도
- TourAPI 대·중·소분류 기준 데이터 240건 JSON 정규화
- 7개 Provider 공통 `ProviderResult`·`ProviderMetadata` 계약
- Tool 결과를 동일 형식으로 보관하는 `AgentToolContext`

### 저장소와 운영

- Supabase 9개 테이블(장소 데이터·동기화 관리·세션 상태)과 마이그레이션
- TourAPI 목록·상세를 Supabase에 반영하는 장소 동기화 파이프라인
- 스냅샷 대조(added/removed/updated) 후 반영하는 2단계 동기화 CLI
- 세션 상태의 메모리·Supabase 저장소 전환 (`STATE_STORE_BACKEND`)
- 외부 API 호출량을 오퍼레이션 단위로 집계하는 관측 모듈
- 개발자 화면 두 개: 채팅 디버그(`/dev-chat`), 운영 패널(`/dev-ops`)
- 개발자 전용 `/api/dev/*`는 `APP_ENV=local`일 때만 라우터를 등록
- 실제 외부 요청을 명시적으로만 실행하는 Smoke/Inspection Test

## 아직 구현되지 않은 범위

- 이동시간 Tool (`estimate_travel_time`) — 일정의 이동시간은 현재 LLM 추정값
- Naver Blog Search 기반 분위기·조용함 근거 수집
- 외부 데이터 스냅샷 저장과 과거 대화 열람 시 재현
- 공휴일·복합 예외가 섞인 운영시간 판정 (감지만 하고 판정은 보류)
- 독립 계층으로서의 `RecommendationRequest Builder`
- `AgentResponse`를 공개 계약용으로 좁히는 응답 축소

## 기술 스택

- Backend: Python 3.11+, FastAPI, Pydantic 2, httpx, sse-starlette, pytest, Ruff
- Frontend: Node.js 24+, React 19, TypeScript, Vite, React Router, Tailwind CSS
- 개발 실행: npm, Node.js 스크립트, Uvicorn
- 저장소: Supabase(PostgreSQL), 프로세스 내 State Store
- LLM Provider: Fake, Google Gemini (`google-genai`)
- CI: GitHub Actions 단일 워크플로에서 Backend/Frontend Job 병렬 실행

## 저장소 구조

```text
TripBranch/
├── README.md
├── package.json                 # 루트 통합 dev/lint/test/build 명령
├── scripts/                     # 프론트·백엔드 동시 실행 및 Ruff 래퍼
│   ├── dev.mjs
│   └── ruff.mjs
├── backend/                     # FastAPI 앱
│   ├── app/
│   │   ├── main.py              # FastAPI 조립 및 오류 처리
│   │   ├── config.py            # 환경변수 설정
│   │   ├── schemas.py           # 공개 API Pydantic 모델과 Intent 정의
│   │   ├── routes/              # health, interpret, recommendations, chat, agent, state, dev
│   │   ├── services/            # Interpret Orchestrator, Agent Runtime, 추천 파이프라인
│   │   ├── schedule/            # 일정 편성 모듈
│   │   ├── state/               # 세션 조건 병합·이력·Trace 저장소
│   │   ├── agent_context/       # A–C 계약, Category Rule, Context Service
│   │   ├── tools/               # 위치·날씨·장소·행사·집중률·공휴일 Tool
│   │   ├── domain/              # Scoring, 운영시간, 근거 생성 등 판정 로직
│   │   ├── providers/           # 외부 API 격리 계층 (Fake/Real, Mapper)
│   │   ├── repositories/        # Supabase 장소 저장소
│   │   └── observability/       # 외부 호출량·요청 기록 집계
│   ├── resources/tour_api/      # TourAPI 분류 기준 정적 데이터
│   ├── scripts/                 # 장소 동기화·검증 CLI
│   ├── tests/                   # 구현 파일에 대응하는 테스트
│   ├── docs/                    # Provider 계약·테스트 가이드 (구현 레벨 문서)
│   ├── pyproject.toml
│   └── .env.example
├── frontend/                    # React/Vite
│   ├── src/
│   │   ├── api/                 # 백엔드 API 클라이언트
│   │   ├── components/          # 채팅·장소 카드·개발자 패널 컴포넌트
│   │   ├── pages/               # HomePage, ChatPage, DeveloperChatPage, DeveloperOpsPage
│   │   ├── state/               # React Context와 sessionStorage
│   │   └── types.ts
│   ├── package.json
│   └── .env.example
├── docs/                        # 제품·아키텍처 문서 (제품 레벨 문서)
│   ├── architecture.md
│   ├── api-contracts.md
│   ├── development-guide.md
│   ├── decision-log.md
│   └── design/                  # Intent별 설계와 도메인 설계 문서
└── supabase/
    ├── migrations/              # DB 스키마 마이그레이션
    ├── data/                    # 동기화 스냅샷·대조 결과 CSV
    └── README.md
```

`backend/app` 안은 `routes → services → domain` 계층으로 나누고, 외부 API 접근은
`providers/`에 격리합니다. `schemas.py`, `config.py`, `errors.py`처럼 전역에서 쓰는
모듈은 현재 규모에서는 `app/` 최상위에 둡니다.

## 단일 저장소로 관리하는 이유

Backend와 Frontend를 분리하지 않고 한 저장소에서 관리합니다. 지금 시점에는 분리의
이득보다 비용이 크다고 판단했습니다.

| 기준 | 현재 상태 | 분리를 검토할 조건 |
| --- | --- | --- |
| API 계약 안정성 | `docs/api-contracts.md`에 `TBD`가 남아 있어 계약이 확정 전 | 계약 확정 후 안정화 |
| 클라이언트 수 | 이 백엔드를 사용하는 클라이언트가 프론트엔드 하나 | 모바일 등 다른 클라이언트 등장 |
| 팀·배포 주기 | 동일 배포 주기 | 팀 분리 또는 독립 배포 주기 필요 |

계약이 자주 바뀌는 단계에서는 Backend 스키마와 Frontend 타입을 **같은 PR에서 함께
고치고 함께 리뷰**할 수 있는 편이 안전합니다. 저장소가 갈라지면 같은 변경이 두
PR로 쪼개지고, 한쪽만 머지된 중간 상태가 생깁니다. CI도 하나의 워크플로에서
Backend와 Frontend Job을 함께 돌리므로 계약 변경이 한쪽만 통과하는 상황을 막습니다.

동시에 `backend/`, `frontend/`, `docs/`, `supabase/`는 최상위에서 서로 독립적인
디렉터리로 두고 있어, 분리가 필요해지는 시점에 코드 이동 없이 떼어낼 수 있습니다.
위 조건 중 하나라도 바뀌면 그때 분리를 재검토합니다.

### 문서를 저장소 안에서 관리하는 이유

`docs/`와 `backend/docs/`의 설계·계약 문서도 외부 위키가 아니라 코드와 같은
저장소에서 관리합니다.

- **드리프트 방지** — `api-contracts.md`나 `design/*`는 코드 변경과 강하게 결합돼
  있습니다. 외부 도구에 두면 "코드는 바뀌었는데 문서는 그대로"가 쉽게 생기지만,
  저장소 안에 있으면 같은 PR에서 코드 diff와 문서 diff를 함께 봅니다.
- **변경 이력 추적** — `decision-log.md`처럼 의사결정 이력 자체가 내용인 문서는
  `git log`/`git blame`으로 "언제, 왜, 어떤 커밋과 함께 바뀌었는지"까지 남습니다.
- **리뷰에 자연스럽게 편입** — "이 API 변경이 계약 문서에도 반영됐는지"를 코드
  리뷰와 같은 화면에서 확인할 수 있어, 반영 누락이 리뷰어의 기억에 의존하지
  않습니다.
- **버전 고정** — 특정 브랜치·태그 시점의 문서 상태를 코드와 같은 커밋으로
  되돌아볼 수 있습니다.

문서는 두 층위로 나눕니다. 제품·아키텍처 수준은 `docs/`에, Provider 계약이나
테스트 가이드처럼 구현 세부는 `backend/docs/`에 둡니다. 회의록이나 일정처럼 코드와
직접 결합되지 않는 자료는 저장소 밖에서 관리합니다.

## 환경변수 설정

예시 파일을 복사합니다.

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Backend Provider는 `PROVIDER_MODE=fake|real`로 일괄 전환하며, 개별 `*_PROVIDER`
값으로 재정의할 수 있습니다. 장소명 기반 좌표 보완을 위한 Naver Local Search는
`LOCAL_SEARCH_PROVIDER=real`로 별도 활성화합니다. **Real Provider 실패 시 Fake로
자동 전환하지 않으며**(D-042), 설정 오류는 첫 요청이 아니라 부팅에서 드러납니다.

실제 Provider 사용 시 필요한 키는 다음과 같습니다.

| 환경변수 | 용도 |
| --- | --- |
| `LLM_API_KEY` | Google Gemini API |
| `WEATHER_API_KEY` | 기상청 날씨 API |
| `TOUR_API_SERVICE_KEY` | TourAPI, 행사, 집중률, 공휴일 API |
| `NAVER_MAP_CLIENT_ID` | Naver Geocoding Client ID |
| `NAVER_MAP_CLIENT_SECRET` | Naver Geocoding Client Secret |
| `NAVER_LOCAL_SEARCH_CLIENT_ID` | Naver API Hub Local Search API Key ID |
| `NAVER_LOCAL_SEARCH_CLIENT_SECRET` | Naver API Hub Local Search API Key |
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_SECRET_KEY` | Supabase 서버 전용 키. 프론트엔드에 노출하지 않습니다 |

동작을 바꾸는 주요 설정은 다음과 같습니다.

| 환경변수 | 기본값 | 용도 |
| --- | --- | --- |
| `PLACE_DETAILS_SOURCE` | `supabase` | 후보별 상세·운영정보 출처(`tour_api` 시 TourAPI 상세를 직접 호출) |
| `STATE_STORE_BACKEND` | `memory` | 세션 상태 저장소(`supabase` 시 DB 영속화) |
| `LLM_FAST_MODEL_NAME` | `gemini-3.5-flash-lite` | 의도 분류·조건 추출 등 짧은 구조화 판단 모델 |
| `LLM_GENERATION_MODEL_NAME` | `gemini-3.5-flash` | 답변·비교·일정 편성 등 문장 생성 모델 |
| `PLACE_REASON_MODEL_NAME` | `gemini-3.1-flash-lite` | 장소 상세 카드의 추천 이유 문장 모델 |
| `MODE_JUDGE_MODEL_NAME` | `gemini-3.1-flash-lite` | 구간 이동수단(도보·대중교통) 판정 모델. 비우면 `LLM_GENERATION_MODEL_NAME`을 따름 |
| `*_FALLBACK_MODEL_NAMES` | 역할마다 다름 | 위 모델별 재시도 소진 시 순서대로 시도할 대체 모델(쉼표 구분) |
| `LLM_API_TIMEOUT_SECONDS` | 빈 값 | LLM 전용 timeout. 비우면 `EXTERNAL_API_TIMEOUT_SECONDS`를 사용 |
| `EXTERNAL_API_TIMEOUT_SECONDS` | `10` | TourAPI·Naver·Supabase 등 일반 외부 호출 timeout |

Frontend의 `VITE_API_BASE_URL`은 비워두면 `/api`를 사용하며(Vite dev 서버가
`http://localhost:8000`으로 프록시), `VITE_SHOW_INTERPRETATION_DEBUG`는 Interpret
디버그 카드 표시를, `VITE_TEST_DEVICE_LOCATION`은 로컬 테스트용 고정 위치를
제어합니다. `VITE_` 변수에는 비밀값을 넣으면 안 됩니다.

예전 단일 모델 설정 `LLM_MODEL_NAME`·`LLM_FALLBACK_MODEL_NAMES`는 폐지됐으며, `.env`에
남아 있으면 부팅에서 막습니다. 모델별 선택 근거는 `backend/.env.example` 주석과
`backend/test_results/`의 측정 기록에 있습니다.

상세한 설정은 [개발 가이드](docs/development-guide.md)를 참고하세요.

## 로컬 설치 및 실행

```bash
npm ci

cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd ..

cd frontend
npm ci
cd ..

npm run dev
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- 개발자 채팅 디버그: http://localhost:5173/dev-chat
- 개발자 운영 패널: http://localhost:5173/dev-ops

루트 명령은 활성화된 Python 환경을 사용하므로 `npm run dev`, `npm run test`,
`npm run lint` 전에 `backend/.venv`를 활성화해야 합니다. Backend는 반드시
`backend/`를 작업 디렉터리로 실행해야 `backend/.env`를 읽습니다. 저장소 루트에서
띄우면 오류 없이 전 Provider가 fake로 뜹니다.

`backend/pyproject.toml`의 의존성이 바뀐 커밋을 받으면 백엔드 패키지를 다시 설치해야
합니다. 절차와 증상은 [개발 가이드](docs/development-guide.md)의 "의존성이 바뀐 뒤"에
있습니다.

## Naming 규칙

Backend가 소유하는 Python 필드와 JSON 필드에는 모두 `snake_case`를 사용합니다.

```text
retrieved_at
session_id
run_id
provider_metadata
```

Python과 JSON 사이에 camelCase alias를 두지 않습니다. Frontend 컴포넌트의 내부
상태는 TypeScript 관례를 따를 수 있지만 Backend API 요청·응답 타입은 Backend JSON
계약의 `snake_case`를 그대로 사용합니다. TourAPI처럼 외부 Provider가 정한 원본
필드명은 Provider/Mapper 경계 안에서만 유지합니다.

세션 식별자는 Backend가 생성합니다. `POST /api/chat`은 `session_id`를 선택 필드로
받고, 없으면 새로 만들어 응답에 실어 보냅니다.

## 테스트와 검사

```bash
npm run lint
npm run test
npm run build
```

Provider 단위·실제 연동 테스트는 별도 가이드를 따릅니다.

```bash
cd backend
python -m pytest -q
RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke -v -s
RUN_REAL_PROVIDER_INSPECTION=true python -m pytest -m inspection -v -s
```

실제 API 테스트는 키와 네트워크를 사용하므로 명시적 플래그가 있을 때만 실행됩니다.
요청·응답 Inspection 출력은 인증 쿼리와 헤더를 `<redacted>`로 마스킹합니다.

## TourAPI 장소 분류 데이터

TourAPI의 대·중·소분류 기준 데이터는
[`backend/resources/tour_api/tour_api_category_codes.json`](backend/resources/tour_api/tour_api_category_codes.json)에
보관합니다. CSV 원본의 계층형 빈 셀을 부모 분류 값으로 채워, 각 JSON 항목만으로
대분류부터 소분류 및 `content_type_id`까지 확인할 수 있도록 정규화했습니다.

서버 시작 시 파일을 한 번 읽어 대·중·소분류의 이름·코드 인덱스를 생성하고,
프로세스 안에서 같은 Registry를 재사용합니다. 소분류 조회 결과는
`PlaceCategoryFilter`로 변환해 `PlaceProvider` 요청에 전달합니다. 실행 중인 서버에는
JSON 변경이 자동 반영되지 않으며, MVP에서는 서버 재시작 시 다시 로드합니다.

## 문서

- [아키텍처](docs/architecture.md)
- [API 및 내부 계약](docs/api-contracts.md)
- [개발 가이드](docs/development-guide.md)
- [의사결정 로그](docs/decision-log.md)
- [Scoring 버전 이력](docs/scoring-version.md)
- [Intent 정의](docs/design/intent-definition.md)
- [패키지별 업무 분담](docs/design/package_work_breakdown.md)
- [Provider Contract v1](backend/docs/provider-contract-v1.md)
- [Provider 테스트 가이드](backend/docs/provider-test-guide.md)
- [Agent State Contract v1](backend/docs/package-b/agent-state-contract-v1.md)
- [Supabase 마이그레이션 관리](supabase/README.md)

## 다음 작업

1. `AgentResponse`를 공개 계약용으로 축소 (D-016 확정 대기)
2. 이동시간 Tool 도입과 일정의 LLM 추정 이동시간 대체
3. 공휴일·복합 운영정보 판정과 후보 부족 시 추가 조회 정책 확정
4. 혼잡도 Feature의 Scoring 반영 여부와 fallback 정책 확정
5. 외부 데이터 스냅샷 저장 모델과 과거 대화 열람 정책 확정
6. 프론트 저장소를 `sessionStorage`에서 `localStorage`로 바꿀지 결정

상기 항목의 세부 계약과 일정은 현재 논의 중이며 확정되지 않은 값은 각 문서에서
`TBD`로 표시합니다.

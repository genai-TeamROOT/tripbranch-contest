# TripBranch 의사결정 로그

## 사용 방법

이 문서는 구현에 영향을 주는 합의와 아직 결정되지 않은 항목을 기록합니다.

- `Accepted`: 현재 설계 원칙으로 채택
- `Implemented`: 코드에 반영됨
- `Proposed`: 제안 상태
- `TBD`: 결론 필요
- `Superseded`: 다른 결정으로 대체

날짜는 저장소 기록 또는 현재 작업일을 기준으로 합니다.

## 결정 목록

### D-001 — LLM은 Backend에서만 호출

- 상태: `Accepted`, Fake/Real Gemini Provider 구현
- 결정: Frontend는 LLM Provider를 직접 호출하지 않고 FastAPI Backend만 호출한다.
- 이유: API 키 보호, 프롬프트·모델 교체의 캡슐화, 로깅과 오류 정책 일원화
- 후속: LLM Provider와 모델 선정 `TBD`

### D-002 — Interpret와 Recommendation 분리

- 상태: `Accepted`, `/api/interpret`와 `/api/recommendations` 분리 구현
- 결정: Interpret는 자연어를 조건으로 추출하고 최종 장소 선택은 하지 않는다.
- 이유: 언어 해석과 결정적 추천 정책의 책임을 분리하고 외부 사실을 검증하기 위함

### D-003 — 내부 `RecommendationRequest` Builder 도입

- 상태: `Proposed`
- 결정: Interpret 결과를 추천기에 바로 전달하지 않고 검증, 정규화, 이전 상태 병합,
  외부 API 보완 후 내부 요청을 생성한다.
- 미결: 최종 필드, 출처/신뢰도 표현, 버전 정책 `TBD`

### D-004 — Provider와 Tool 분리

- 상태: Provider `Implemented`, 다건 장소 상세조회 Tool `Implemented`, 나머지 Tool `TBD`
- 결정: Provider는 외부 통신, Tool은 추천 파이프라인의 업무 단위를 담당한다.
- 이유: Tool과 외부 엔드포인트의 1:1 결합을 피하고 Provider 교체 영향을 제한

### D-005 — Provider 독립 내부 모델 사용

- 상태: `Implemented`
- 결정: 외부 응답은 Provider/Mapper에서 `PlaceCandidate`, `PlaceDetails`,
  `GeocodeResult` 등으로 정규화한다.
- 이유: Provider별 필드 차이와 변경을 추천 로직에서 격리

### D-006 — 장소 기준 데이터는 TourAPI 사용

- 상태: `Accepted`, 주요 조회 `Implemented`
- 결정: 위치 기반 후보, 키워드 후보, 상세정보의 기준 데이터로 TourAPI를 사용한다.
- 현재: `locationBasedList2`, `searchKeyword2`, `detailCommon2`, `detailIntro2` 연결
- 미결: 데이터 누락 시 보완 Provider와 충돌 해결 정책 `TBD`

### D-007 — 특정 장소 조회는 정확 일치 우선

- 상태: `Implemented`
- 결정: `find_details_by_name()`은 키워드 결과에서 정규화된 이름이 정확히 일치하는
  후보만 상세조회하고 유사 결과를 임의 선택하지 않는다.
- 이유: 동명·유사 장소의 잘못된 운영정보를 반환하는 위험 방지

### D-008 — 추천은 하드 필터와 가중치 점수 조합

- 상태: `Implemented`; Recommendations API 파이프라인에 연결
- 결정: 명시적 필수 조건은 하드 필터, 선호 조건은 가중치 점수로 처리한다.
- 현재: `backend/app/domain/scoring.py::score_candidates()`로 날씨·남은 운영
  시간·거리 Feature 기반 가중치 점수 계산과 정렬을 구현. 이전 노출·거절 ID
  하드 필터에 더해, `now`와 `OperatingHours`(개장~마감)를 비교해 폐점 여부를
  최종 하드 필터로 직접 판정한다(운영 유무는 가중치 Feature가 아니라 필터).
  운영시간 미확인(`operating_hours=None`)은 폐점과 달리 하드 필터에서
  제외하지 않고, 남은 운영시간 Feature만 결측 처리한다. 상세는
  [추천 점수 설계](./design/recommendation-scoring.md) 참고.
- 카테고리는 가중치 계산에서 제외한다: place_type/place_tag 1차 하드 필터가
  이미 처리한다고 보고, `category`는 표시용 메타데이터로만 남긴다.
- 기본 가중치: 날씨 0.40 / 남은 운영시간 0.40 / 거리 0.20. 남은 운영시간은
  boolean이 아니라 `now` 기준 마감까지 남은 분을 정규화한 값(120분 이상이면
  만점)이다.
- 결측 시 재분배: 날씨와 남은 운영시간은 후보마다 독립적으로 결측될 수 있어
  (날씨는 실행 전체 공통, 남은 운영시간은 후보별 `operating_hours` 유무에
  따름) 결측된 Feature(들)의 가중치를 나머지 Feature에 기존 비중 비례로
  재분배한다. 이 때문에 `weights_used`는 `ScoringResult` 전체가 아니라
  `RankedCandidate`마다 따로 노출한다.
- tie-break: score 내림차순 → distance_km 오름차순 → place_id 오름차순
- 입력 모델: `ScoringCandidate`(Provider/Tool 독립적인 Candidate Model v1).
  C-01 Tool 계약이 아직 없어 현재는 Stub 데이터로 검증했으며, Tool 확정 후
  "Tool 출력 → `ScoringCandidate`" 매퍼만 추가하면 됨
- 범위 제한: `OperatingHours`는 `open_time <= close_time`인 당일 운영만
  다루며 자정을 넘기는 운영시간은 `TBD`
- 미결: 카테고리 하드 필터 자체(및 다중 카테고리 허용 시 우선순위 표현),
  혼잡도·근거 신뢰도 Feature, 실제 이동시간 기반 거리, 예산/동행 하드 필터,
  실제 운영시간 원문("0900~1800" 등)을 `OperatingHours`로 정규화하는 파서
  (`PLC-03`과 동일 범위), 자정을 넘기는 운영시간, 기준 시각(`now`)의 실제
  출처(즉시 방문 vs 방문 예정 시각, D-022 연계)

### D-009 — Naver Blog Search는 보완 근거로 사용

- 상태: `Accepted`, 구현은 `TBD`
- 결정: TourAPI에 없는 조용함·분위기 등의 근거를 보완하되 기준 장소 데이터로
  사용하지 않는다.
- 미결: 검색어, 신뢰도, 최신성, 인용/저장 정책

### D-010 — 사용자 자연어 원문은 Supabase에 영구 저장하지 않음

- 상태: `Accepted`, Persistence 미구현
- 결정: 서버에는 정규화 조건과 실행·결과 Snapshot 중심으로 저장한다.
- 이유: 개인정보와 불필요한 원문 보존 최소화
- 미결: 보존기간, 삭제 정책, 민감정보 필터링, Supabase RLS

### D-011 — 채팅 복원 원문은 브라우저 저장소 사용 가능

- 상태: 목표 `Accepted`, 구현 차이 존재
- 결정: 채팅 복원용 원문은 Frontend 저장소에 둘 수 있다.
- 현재: `sessionStorage` 키 `tripbranch_state`, 버전 2 사용
- 목표 문맥: `localStorage`
- 미결: `localStorage` 전환 여부, 만료와 사용자 삭제 UX

### D-012 — 과거 조회는 Snapshot, 후속 입력 시 현재 데이터 재조회

- 상태: `Accepted`, 구현은 `TBD`
- 결정: 과거 채팅을 열 때 당시 외부 데이터와 추천 결과를 표시하고, 사용자가
  대화를 이어갈 때만 현재 Provider 데이터를 조회한다.
- 미결: Snapshot 스키마와 TTL

### D-013 — 세션 ID와 추천 실행 ID 분리

- 상태: `Accepted`, 구현은 `TBD`
- 결정: Frontend가 `chat_session_id`, Backend가 매 실행 `recommendation_run_id`를 생성한다.
- 이유: 하나의 채팅 안에서 여러 추천 실행과 Snapshot을 구분

### D-014 — Provider 모드는 공통 플래그와 개별 Override 사용

- 상태: `Implemented`
- 결정: `PROVIDER_MODE`로 일괄 Fake/Real 전환하고 `*_PROVIDER`로 개별 재정의한다.
- 테스트: 일반 pytest는 Real 설정이어도 Fake/Mock으로 격리

### D-015 — 공휴일은 `getRestDeInfo` 사용

- 상태: `Implemented`
- 결정: 기념일용 `getAnniversaryInfo`가 아니라 공휴일 전용 `getRestDeInfo`를 사용한다.
- 응답: XML을 Provider에서 `HolidayResult`로 정규화

### D-016 — 공개 Chat API 통합

- 상태: `Proposed`
- 결정 후보: `POST /api/chat`에서 Intent 분류부터 추천·응답 생성을 오케스트레이션
- 현재: `/api/interpret`, `/api/recommendations` 분리
- 미결: 기존 API 유지/폐기, streaming 여부, idempotency

### D-017 — Provider 공통 결과 메타데이터

- 상태: `Accepted`, 코드 반영은 후속 작업
- 결정: 모든 Provider 정상 결과는 `source`, `status`, `retrieved_at` metadata를
  포함한다.
- `source`: 데이터 공급자와 기능을 나타내는 폐쇄 목록 사용; Real/Fake를 구분
- `status`: `success`, `no_data`, `partial`
- `unavailable`: 정상 결과 status에 포함하지 않고 오류로 처리
- `retrieved_at`: UTC ISO 8601 밀리초 `Z`; 외부 응답 정규화 완료 시각
- 캐시: 캐시 반환 시각이 아닌 최초 외부 조회의 `retrieved_at` 유지
- 복합 결과: 마지막 필수 호출의 정규화 완료 시각 사용
- naming: Python과 Backend JSON 모두 `retrieved_at`
- 이유: 출처, 결측, 데이터 신선도를 Provider 종류와 무관하게 판단하고 Snapshot과
  로그에서 같은 의미로 사용하기 위함
- 후속: 공통 모델/wrapper 및 Clock 구현, 기존 `provider`/`raw_source` 필드와의
  마이그레이션

### D-018 — Tool 공통 오류와 no_data/unavailable 경계

- 상태: `Accepted`, 다건 장소 상세조회에 일부 적용, 공통 envelope 구현은 후속 작업
- 공통 code: `invalid_input`, `not_found`, `no_data`, `unavailable`, `unsupported`,
  `internal_error`
- 결정: `no_data`는 호출·파싱 성공 후 데이터 없음이 확인된 상태
- 결정: `unavailable`은 데이터 존재 여부를 판단할 수 없는 외부 의존성 실패
- timeout, unauthorized, rate limit, network, parse error는 최상위 code가 아니라
  `unavailable`의 `cause`로 표현
- 동일 요청 retry: `no_data`는 기본 false, `unavailable`은 cause에 따라 결정
- 책임: Tool은 분류, Orchestrator는 중단·부분 진행·재질문 결정
- 보안: ToolError details에 Secret, 전체 요청 URL, 사용자 원문을 포함하지 않음
- 이유: 추천 파이프라인의 분기 수를 제한하면서 데이터 부재와 시스템 장애를
  혼동하지 않기 위함
- 후속: `ToolResult<T>`, Provider 오류 cause 보존, Orchestrator fallback 구현

### D-019 — Provider Blocker 우선순위와 관리 기준

- 상태: `Accepted`
- 등급: `P0` 보안/데이터 손상, `P1` Core 흐름 차단, `P2` 품질·복원력 저하,
  `P3` 확장성·운영 효율
- 결정: Provider별 Blocker는 영향, 현재 대응, 객관적인 해결 조건, 상태를 함께 기록
- 결정: `Resolved`는 해결 조건과 관련 테스트가 모두 충족된 경우에만 사용
- 현재 P0: 일부 Provider 예외 traceback의 인증 쿼리 노출 가능성
- 현재 주요 P1: metadata/공통 Tool envelope, Place 운영정보,
  Weather/Concentration 연결,
  혼잡도 coverage, Holiday와 장소 휴무 규칙 결합
- 상세 목록: `backend/docs/provider-contract-v1.md` 16장

### D-020 — Backend Python/JSON snake_case 통일

- 상태: `Accepted`
- 결정: Backend가 소유하는 Python 필드와 JSON 필드는 모두 `snake_case` 사용
- 결정: Python↔JSON 직렬화에 camelCase alias를 두지 않음
- 결정: Frontend API DTO도 Backend JSON 필드명을 그대로 사용
- 예외: Frontend 내부 상태와 외부 Provider 원본 요청·응답 필드는 각 소유자의 규칙
  사용 가능
- 이유: 현재 Pydantic 모델·공개 API·Frontend API 타입의 기존 규칙과 일치시키고
  계층 사이의 불필요한 alias 및 변환 비용을 제거하기 위함

### D-021 — Provider 정상 metadata와 오류 계약 분리

- 상태: `Accepted`, 코드 반영은 후속 작업
- 정상 결과: `ProviderMetadata(source, status, retrieved_at)` 포함
- 실패 결과: 정상 결과 대신 `ProviderError` 발생
- ProviderError: `source`, `code`, `cause`, `occurred_at`, `retryable`, 안전한
  `message`, 선택 `details`
- 시각 의미: `retrieved_at`은 데이터 정규화 성공 시각, `occurred_at`은 오류 감지 시각
- 결정: 실패 시 `retrieved_at`을 생성하지 않음
- 결정: `no_data`는 ProviderError가 아니라 정상 결과 status
- 결정: 파싱 실패처럼 존재 여부를 판단할 수 없는 경우 `unavailable`
- Tool 변환: source/cause/occurred_at/retryable을 ToolError에 보존
- 보안: ProviderError 메시지·details·traceback에 Secret과 전체 요청 URL을 포함하지 않음
- 이유: 데이터 부재와 호출 실패를 구분하면서 실패 시점과 출처를 추적하기 위함

### D-022 — 방문 예정 시각의 초단기예보 우선

- 상태: `Accepted`, Weather Tool 구현 완료
- 결정: MVP Weather 기본 데이터는 현재 관측이 아닌 기상청 초단기예보
- 즉시 방문: 현재 시각과 가장 가까운 예보 사용
- 특정 시간/일정: 방문 예정 시각과 가장 가까운 예보 사용
- 현재 관측 날씨: MVP 필수 범위에서 제외
- 향후 관측값 추가 시에도 방문 예정 시각 예보가 우선이며 관측은 보완 정보로만 사용
- 추가 검토 조건: 실제 테스트에서 즉시 방문 추천 품질 부족이 확인된 경우
- Weather metadata: `data_type=forecast`, `retrieved_at`, `forecast_for`,
  `observed_at=null`
- 구현: timezone 없는 입력은 KST로 가정하고, 가장 가까운 예보를 선택하며 동률이면
  미래 예보를 우선
- 이유: 사용자의 요청 시점보다 실제 장소 도착·방문 시점의 날씨가 추천 판단에 중요

### D-023 — 장소 다건 상세조회 Provider 교체 경계

- 상태: `Accepted`, DB 구현은 `TBD`
- 결정: 후보 검색과 상세조회를 각각 `PlaceSearchProvider`,
  `PlaceDetailsProvider`로 분리하고 `NearbyPlaceDetailsTool`에서 조합한다.
- 근거: TourAPI로 장소 10개의 상세정보를 조회하면 목록 1회와 장소별 상세 2회로
  최대 21회의 외부 요청이 발생하며, 2026-07-23 로컬 실측에서 동시성 3 기준 약
  20초가 소요됐다.
- 방향: 실서비스에서는 상세정보를 우선 DB 다건 조회로 교체하고, 누락되거나 오래된
  데이터만 TourAPI로 보완한다. 필요하면 후보 검색도 이후 DB로 이전한다.
- 미확정: DB 종류·스키마, 갱신 주기, stale 기준, 캐시 및 fallback 정책

### D-024 — 여행코스 운영시간 누락 처리

- 상태: `Accepted`, 운영 상태 evaluator는 `TBD`
- 결정: 여행코스(`content_type_id=25`)에 운영시간 원본이 없으면 `all_day`로
  정규화한다.
- 구분: 실제 Provider 명시값과 혼동하지 않도록 `parse_status=assumed`,
  `assumption_reason=course_without_operating_hours`를 기록한다.
- 예외: 다른 장소 유형의 운영시간 누락은 `unknown`을 유지한다.
- 원문: 운영시간과 휴무 원문은 그대로 보존하고 HTML을 정리한 별도 필드를 둔다.
- 이유: 여행코스 자체는 개별 시설의 입장시간과 다른 이동 경로 데이터이며, 누락을
  이유로 후보 전체를 운영 미확인으로 제외하지 않기 위함

### D-025 — `resolve_location` 지원 지역 범위와 재질문 정책

- 상태: `Accepted`, Tool 구현 완료. **2026-08-24 개정(TP-126) — 지원 범위가 종로구
  한 곳에서 여러 구로 바뀌었다.**
- 지원 범위: `app.service_area.SUPPORTED_DISTRICTS`가 정한다(2026-08-24 기준 서울
  종로구·중구·용산구·성동구). 범위 밖은 `unsupported`. 개정 전에는 "MVP는 서울특별시
  종로구로 한정"이었다.
- alias: 공식 주소를 우선 조회하고 정상 빈 결과에만 원문으로 1회 fallback
- 장애: timeout·인증·통신·파싱 실패에는 fallback하지 않고 `unavailable`
- 모호성: 직접·fallback 결과가 복수이면 임의 선택하지 않고 `no_data`와
  `details.reason=ambiguous_location`으로 사용자에게 구체적인 위치를 요청
- 검증: 좌표 bounding box가 아니라 Provider의 행정구 정보를 사용

#### 2026-08-24 개정 — 장소 검색의 구 고정을 푸는 방법

지원 구가 여럿이 되면서 "TourAPI 요청에 어느 구를 실을 것인가"를 정해야 했다.
개정 전에는 `lDongSignguCd=110`을 요청에 고정으로 실었다.

| 후보 | 채택 여부 |
| --- | --- |
| 검색 중심 좌표로 구를 판정해 그 구 코드를 요청에 싣는다 | 기각. 반경이 구 경계를 넘을 때 바로 옆 지원 구 후보가 잘린다. 좌표와 등록 구가 어긋나는 장소도 통째로 빠진다(아래). |
| 지원 구마다 호출해 병합한다 | 기각. TourAPI 호출이 구 수만큼 늘어난다. `detailIntro2` 일일 한도가 1,000회다. |
| 요청은 시도(`lDongRegnCd=11`)까지만 좁히고 응답의 `lDongSignguCd`로 거른다(채택) | 호출 1회 그대로. 반경 안에 있으면 어느 지원 구든 후보로 남는다. |

- **좌표가 아니라 응답이 말하는 구를 믿는다.** 둘이 어긋나는 장소가 실재하기
  때문이다 — 서울역 부속 시설 72건은 `district_code=170`(용산구)으로 등록돼
  있지만 좌표는 중구 폴리곤 안에 있다(2026-08-24 실측). 좌표로 판정하면 이
  72건이 통째로 후보에서 빠진다. 저장소 `places`의 `district_code`도 같은 값이라
  상세 조회와 기준이 어긋나지 않는 이점도 있다.
- 응답에 `lDongSignguCd`가 비어 있으면 그 항목은 버리되 **경고 로그를 남긴다.**
  TourAPI가 필드를 빼면 전량이 조용히 사라져 "이 근처에 장소가 없다"로 둔갑한다.
- 좌표 판정(폴리곤, D-044)과 검색 필터가 같은 `SUPPORTED_DISTRICTS`를 본다. 구를
  늘릴 때 한쪽만 늘어나는 일을 막기 위해서다.
- 축제 조회(`searchFestival2`)도 같은 방식이다. 시도까지만 좁혀 받아 지원 구만
  남긴다.
  - **2026-09-07 개정(TP-253).** 전에는 "서울 전체가 51건(2026-08-24 실측)이라
    한 번에 받는다"고 적고 `numOfRows=100`으로 한 번만 불렀다. 9월 7일 실측에서
    189건이 되어(2주에 3.7배) 진행 중 19건 중 12건이 응답에 아예 들어오지 않았다.
    거리순 정렬인데 모집단이 3분의 1이라 5개 지점에서 상위 5칸 중 4~5칸이 바뀌었다.
  - 고친 것은 이 결정이 아니라 그 아래 딸린 전제다. **요청은 여전히
    `lDongRegnCd=11` 하나이고 구 필터도 응답으로 한다.** TourAPI가 `numOfRows`에
    100 상한을 걸지 않아(200·500·1000 요청 모두 전량 반환) 넉넉히 요청하면 평시
    호출은 1회 그대로다. `totalCount`가 그보다 크면 `pageNo`로 이어받고, 상한에
    걸려 잘리면 경고 로그를 남긴다 — 이 사건의 핵심이 "잘렸는데 아무도 몰랐다"였다.
- 확인(2026-08-24, 실 API 반경 2km): 경복궁 23건 전부 종로구, 명동 26건 전부 중구,
  이태원역 25건 전부 용산구, 성수동 29건 전부 성동구, 홍대입구역(마포) 0건.

### D-026 — Weather Tool v1 시간·범위 정책

- 상태: `Accepted`, Tool 구현 완료
- 서비스 전제: 국내 사용자·국내 장소, 별도 표기 없는 시각은 `Asia/Seoul`
- 즉시 방문: `visit_at=None`이면 Backend Clock 사용
- 명시 시각: 예보 범위 밖이면 마지막 예보로 대체하지 않고 `unsupported`
- 지역: Weather Tool은 좌표만 검증하며 서비스 지역 통제는 위치 Tool과 상위 계층 책임
- v1 필드: condition, SKY, PTY와 예보·조회 시각만 사용
- 범위 밖: 온도·습도·강수량·풍속, 해외 현지 시간, DST

### D-027 — 추천 Evidence·평가 Fixture v1

- 상태: `Accepted`, `Implemented`
- 결정: `RankedCandidate`의 `feature_scores`/`weights_used`를 자연어 없이
  Feature별 기여도(score × weight)로 재구성하는 `RecommendationEvidence`를
  도입하고, 반복 검증 가능한 고정 평가 Fixture v1을 구축한다.
- 구현: `backend/app/domain/evidence.py::build_evidence()`/`build_evidence_list()`,
  `backend/tests/fixtures/scoring_fixture_v1.py`(7개 시나리오),
  `backend/tests/test_scoring_fixture.py`(정렬·제외·미확인 검증 + 동일 입력
  반복 실행 결정성 검증 + Evidence 대응 검증, 21개 테스트). 상세는
  [추천 Evidence·평가 Fixture 설계](./design/recommendation-evidence-fixture.md)
  참고.
- 범위 제외: `/api/recommendations` 라우트 연결, Provider 운영시간 원문
  정규화(`operating_hours.py`의 `OperatingSchedule`↔`ScoringCandidate.operating_hours`
  매퍼 포함), 카테고리 하드 필터, 혼잡도·신뢰도 Feature, LLM 기반 자연어
  추천 이유 생성
- 이유: Scoring 결과를 사용자에게 설명 가능한 형태로 준비하면서도, 아직
  확정되지 않은 Response Generator(LLM)나 Persistence(Snapshot) 설계에
  선행 결합되지 않도록 순수 데이터 변환으로 한정함

### D-028 — 추천 파이프라인 1차 E2E 통합: Evidence 응답 노출

- 상태: `Accepted`, `Implemented`
- 결정: D-027에서 만든 `evidence.py::build_evidence()`를
  `recommendation_pipeline.py`의 응답 조립 단계에 연결해 `RecommendationItem`에
  `score`/`feature_scores`/`weights_used`를 추가 노출한다. 상위 추천 개수는
  기존 `RECOMMENDATION_RESULT_LIMIT`(기본 5)을 그대로 유지한다 — 작업 지시
  문서에 한때 "2~3개"로 적혀 있었으나 팀 논의 후 "5개"로 정정되어 별도
  설정 변경은 필요하지 않았음.
- 구현: `backend/app/schemas.py::RecommendationItem`(신규 필드),
  `backend/app/services/recommendation_pipeline.py::_build_response()`,
  `frontend/src/types.ts` 동기화. 당시 레거시 stub 응답에도 필드를 맞췄으나
  이후 실제 Fake/Real 공통 파이프라인으로 대체되어 제거됨. 날씨 조회
  성공/실패 대응 E2E 테스트, 동일
  입력 결정성 테스트, 재사용 가능한
  `backend/tests/fixtures/recommendation_pipeline_fixture_v1.py`를 추가.
- 범위 제외: 자연어 추천 이유 생성(`recommendation_reason`은 기존 고정
  템플릿 유지), Persistence/Snapshot 연결, 카테고리 하드 필터
- 이유: D-02(D-027)에서 준비해 둔 Evidence 모델을 실제 `/api/recommendations`
  응답과 연결해, 추천 점수 근거를 Frontend/사용자가 그대로 소비할 수 있게 함

### D-029 — Recommendation Explainability Layer v1 (Rule 기반)

- 상태: `Accepted` — A(Agent Runtime) 담당과 API Contract 협의 반영 완료.
  상세는 [추천 Explainability Layer 설계](./design/recommendation-explainability.md) 참고
- 결정: `RecommendationEvidence.contributions`(D-027)를 입력으로 받아, LLM을
  호출하지 않는 Rule 기반·결정적 방식으로 Feature별 한국어 설명 문장을
  생성한다. Feature 점수가 0.7 이상인 것만 "기여도(score × weight) 큰
  순서"로 문장화하고, 결측이거나 애매한 점수(<0.7)는 생략한다(결측은 이미
  `warnings`가 별도로 안내하므로 중복 설명하지 않음). 기존
  `recommendation_reason`(고정 템플릿 한 줄)은 유지하고, `explanations:
  string[]`을 신규 필드로 추가한다(대체가 아니라 추가 — Frontend가 이미
  `recommendation_reason`을 렌더링하고 있어 하위 호환 유지).
- 구현: `backend/app/domain/explanation.py::build_explanations()`,
  `backend/app/services/recommendation_pipeline.py::_build_response()`
  연결, `backend/app/schemas.py::RecommendationItem.explanations`,
  `frontend/src/types.ts` 동기화, `backend/tests/test_explanation.py`(D-02
  Fixture 재사용, 결정성·순서·빈 리스트 케이스 검증)
- 범위 제외: LLM 기반 자연어 생성, 점수가 애매하거나(0.4~0.7) 낮은(<0.4)
  Feature에 대한 부정적 근거 문장 생성, Chat API(`RecommendationResult`)로의
  최종 반영(A 담당과 협의 후 확정)
- 이유: 순수 Rule 기반이라 비용·지연시간이 없고, 동일 입력에 항상 동일한
  설명이 나와 D-02 Fixture로 그대로 회귀 검증 가능함. 추후 LLM 기반 Response
  Generator가 붙을 때도 이 Feature별 판단을 근거 재료로 재사용할 수 있도록
  설계함

### D-030 — 임계값 미달·날씨 결측 시 warning 커버리지 보완

- 상태: `Implemented`
- 결정: D-029 이후 남아 있던 빈틈 — (1) 날씨 결측으로 `weather` Feature
  점수가 `None`인 경우, (2) 세 Feature 모두 0.7 미만이라 `explanations`가
  완전히 비는 경우 — 둘 다 지금까지 아무 warning 없이 조용히 생략되던
  것을 확인하고, `warnings` 문구를 추가해 안내하기로 결정. 운영시간 결측처럼
  `unverified_recommendations`로 분리하지는 않는다 — "존재 자체를 모르는"
  운영시간 결측과 달리, 날씨 결측·낮은 점수는 그 정도로 심각한 불확실성이
  아니라고 판단했기 때문. 두 케이스는 원인이 다르므로(데이터 없음 vs.
  데이터는 있으나 애매함) 문구도 분리했다. `distance`는 `ScoringCandidate.
  distance_km`가 필수 필드라 결측 케이스 자체가 존재하지 않으므로 대상에서
  제외
- 구현: `backend/app/services/recommendation_pipeline.py::_extra_warnings()`
  신설, `_build_response()`에서 기존 상세정보 결측 warning과 함께 조립.
  `backend/tests/test_recommendation_pipeline.py`에 날씨 결측·전체 임계값
  미달 케이스 각각 검증하는 테스트 추가
- 범위 제외: `recommendation_reason` 존폐 여부, Explanation Rule 정의
  문서화 — 둘 다 A 담당과의 협의 이후로 보류
- 이유: Explainability Layer 협의를 준비하며 발견한 실사용자 관점의 빈틈으로,
  A의 결정이 아니라 우리 쪽 구현 책임 영역이라고 판단해 협의 전에 먼저 해결

### D-031 — Explanation 문장 구체화: 고정 텍스트 → 계산값 기반 사실 문장

- 상태: `Implemented`
- 결정: D-029의 Feature별 고정 문장(예: "지금 날씨 조건에 잘 맞는 장소예요.")을
  실제 계산값이 들어간 문장으로 교체한다(`package_D/[D-06]explainability_detail.txt`
  구체화 요청). 거리는 1km 미만 m(10m 반올림)/이상 km(소수 첫째자리)로
  "직선거리"를 명시해 실제 이동거리로 오해되지 않게 하고, 남은 운영시간은
  "N시간 M분" 형태로, 날씨·환경은 `(weather_condition, environment_type)`
  9개 조합별 문장으로 조립한다. 문장은 "여유롭게 방문할 수 있어요" 같은
  평가·어투 없이 사실만 전달하도록 의도적으로 짧게 유지한다 — 평가·어투를
  더해 자연스러운 문단으로 잇는 것은 Response Generator(LLM, A 담당)의
  몫이라고 판단했다(둘 다 넣으면 최종 응답에서 비슷한 평가 표현이 중복될
  위험이 있음). 정렬 규칙(기여도 내림차순)과 임계값(0.7)·결측 시 생략 로직은
  D-029에서 그대로 유지.
- 구현: `backend/app/domain/scoring.py::RankedCandidate`에 원본 계산값
  (`distance_km`/`remaining_minutes`/`weather_condition`/`environment_type`)
  필드 추가, `backend/app/domain/evidence.py::RecommendationEvidence`로
  그대로 전달, `backend/app/domain/explanation.py`의 문장 생성 로직을 고정
  매핑에서 계산 함수로 교체. `backend/tests/test_explanation.py`에 거리
  단위 경계(3개)·운영시간 경계(4개)·날씨-환경 조합 매트릭스(2개) 테스트 추가
- 범위 제외: 여러 `explanations` 문장을 하나의 자연스러운 문단으로 잇는 것
  (A 담당 Response Generator 영역)
- 이유: task.txt 예시가 "Rule 계층은 사실만, 평가·어투는 LLM 몫"이라는
  경계를 명확히 보여주고 있어, 그 경계에 맞춰 문장 톤을 다시 다듬음

### D-032 — RecommendationContext → RecommendationResponse 신규 진입점

- 상태: `Implemented`
- 결정: A(Agent Runtime)가 C에서 받은 `RecommendationContext`를 그대로
  넘기면, D 내부(후보 변환→Scoring→Evidence→Explanation 조립)를 전부
  처리해 `RecommendationResponse`만 반환하는 공개 함수
  `run_recommendation_pipeline_from_context()`를 신설한다
  (`package_D/[A] RecommendationContext → RecommendationResponse 진입점
  요청.txt`). A가 D 내부 구현(`score_candidates()`/`build_evidence()`/
  `build_explanations()`/private `_build_response()`)에 직접 의존하지
  않도록 하기 위함. 기존 Tool 직접 호출 구조(`run_recommendation_pipeline()`,
  `recommendations.py`)는 이 시점엔 그대로 유지하고 대체하지 않기로
  했다 — 두 진입점이 공존하는 상태였다. **(주: 이 판단은 이후 D-034에서
  뒤집혔다. `run_recommendation_pipeline()`은 완전히 삭제됐고
  `recommendations.py`도 이 진입점 기반으로 마이그레이션됐다 — 상세는
  D-034 참고.)**
  - `candidate_limit`은 새 시그니처에 포함하지 않기로 판단했다. C가 이미
    최종 후보 목록을 확정해서 넘기는 구조라, D가 그 뒤에 개수를 다시
    제어할 근거가 없기 때문이다.
  - `search_radius_km`은 호출자(A)가 C가 해당 요청에서 실제로 후보를
    조회할 때 사용한 반경과 동일한 값을 넘겨야 한다는 전제를 docstring에
    명시했다 — Scoring의 거리 점수 정규화(`max_distance_km`)가 이 값을
    그대로 재사용하기 때문이며, 코드로는 이 일치 여부를 검증할 수 없다.
  - `context.location`/`context.places` 상태를 구분해서 처리한다:
    `success`/`partial`(정상 진행), `no_data`(정상 조회했지만 결과 없음 →
    빈 `RecommendationResponse`, 에러 아님), `unavailable`(조회 자체 실패
    → `AppError`). "확인 못 함"과 "확인했는데 없음"을 구분하기 위함이다.
- 구현: `backend/app/services/recommendation_pipeline.py`에
  `run_recommendation_pipeline_from_context()`, `_weather_condition_from_context()`
  추가. 기존 `_build_response()`/`_extra_warnings()`가 Tool 전용 타입
  `EnrichedPlace`에 의존하던 것을 "상세정보 결측 place_id 집합"
  (`frozenset[str]`)으로 일반화해 두 진입점에서 공유하도록 리팩터링.
  `backend/tests/test_recommendation_pipeline.py`에 정상/날씨 결측/후보
  없음/조회 실패/위치 결측 5개 시나리오 테스트 추가
- 범위 제외: `candidate_limit` 관련 논의는 A에게 별도로 공유하되 이번
  구현엔 반영하지 않음. 혼잡도(concentration) 조회는 `RecommendationResponse`
  자체에 포함되지 않는 부가 정보라 이 진입점에서 다루지 않음
- 이유: `map_context_to_scoring_candidates()`가 이미 존재하지만 유닛
  테스트로만 검증되고 어떤 파이프라인에도 연결되지 않은 상태였음. A가
  D 내부 함수 4개를 직접 조합하는 대신, 단일 공개 진입점을 통해 D 내부
  구현 변경에 영향받지 않도록 경계를 명확히 함

### D-033 — Agent Runtime RecommendationProvider 실제 구현체 연결

- 상태: `Implemented`
- 결정: `run_recommendation_pipeline_from_context()`(D-032)를 Agent
  Runtime의 `RecommendationProvider` Protocol(`app/services/runtime/
  protocols.py`, A 소유)에 연결하는 실제 구현체 `RealRecommendationProvider`를
  신설해 `run_agent()`의 기본 provider를 기존 `FakeRecommendationProvider`
  대신 이걸로 교체한다(`package_D/[TECH-02] C-D 직접 의존 제거 및
  RecommendationContext 경계 정리.txt`). Protocol 시그니처
  (`recommend(conditions, context, excluded_place_ids)`)는 A가 이미
  확정해둔 형태 그대로 사용했고 D 쪽에서 변경하지 않았다.
- 구현: `backend/app/services/runtime/recommendation_provider.py` 신설(주: 이
  파일은 이후 D-035에서 develop과의 중복으로 삭제되고
  `real_recommendation_provider.py`로 대체됐다 — 클래스명·시그니처는 동일).
  `search_radius_km`은 A가 이미 구현해둔
  `recommendation_transform.to_search_radius_km(conditions)`로 계산하고,
  `visit_at`은 `now_kst()`(현재 시각)를 쓴다 — 사용자가 미래 방문 시각을
  지정하는 입력 경로가 아직 없기 때문. `excluded_place_ids`는
  `shown_place_ids`로 그대로 전달한다(현재 `score_candidates()`에서
  `shown_place_ids`/`rejected_place_ids`는 둘 다 단순 제외로 동일하게
  동작하므로 구분할 실익이 없음). `run_agent()`/`agent_runtime.py`
  docstring, `stubs.py`/`protocols.py`의 "D 계약 확정 전" 관련 주석을
  현재 상태에 맞게 갱신
- 이유: A의 `agent_runtime.py`/`recommendation_transform.py`가 이미 D의
  진입점을 기다리는 상태로 배선까지 끝나 있었음(주석에 "D 확인 대기 중"
  명시). D-032에서 만든 진입점을 실제로 연결하는 것이 A-D 계약을
  "확정"하는 마지막 단계였음

### D-034 — Tool 직접 호출 파이프라인 완전 삭제 및 레거시 라우터 마이그레이션

- 상태: `Implemented`
- 결정: D-033에서 "레거시 라우터 전용으로 남긴다"고 판단했던
  `run_recommendation_pipeline()`(Tool 직접 호출 구조)을 완전히
  삭제하고, 그 유일한 호출자였던 `/api/recommendations` 라우터
  (`app/services/recommendations.py`)를 `run_recommendation_pipeline_
  from_context()` 기반으로 마이그레이션한다. "D 코드에서 C Tool
  Intelligence를 직접 호출하지 않는다"는 완료 기준을 예외 없이 만족시키기
  위함
- 구현: `recommendations.py`가 여전히 위치·날씨·장소 Tool
  (`ResolveLocationTool`/`GetWeatherForecastTool`/`NearbyPlaceDetailsTool`)을
  직접 호출하지만, 이 호출은 D 코드(`recommendation_pipeline.py`)가 아니라
  호출자 쪽(레거시 라우터 서비스)에서 일어난다 — Tool 결과를
  `app.agent_context.mappers`의 `map_location_context()`/
  `map_weather_context()`/`map_places_context()`(C가 실제
  `ContextService`에서 쓰는 것과 동일한 순수 변환 함수)로 `RecommendationContext`에
  조립한 뒤 D에 넘긴다. 위치 조회 실패 시 404/422/502로 세분화하던 기존
  에러 매핑(`_location_error()`)은 그대로 `recommendations.py`로 옮겨
  동일하게 유지했다 — Context 기반 진입점의 위치 실패 처리(일괄 502)에
  맡기면 API 응답 status_code가 달라지는 회귀가 생기기 때문
- 범위 축소: 혼잡도(concentration)·공휴일(holiday) Tool 호출은
  라우터에서 제거했다. 기존에도 두 값 모두 `RecommendationPipelineResult.
  context`/`.concentrations`에만 담기고 실제 `RecommendationResponse`
  (`build_recommendations()`가 반환하는 값)에는 전혀 반영되지 않던 죽은
  코드였음을 확인했다 — 기능 회귀 없이 제거
- 함께 삭제: `RecommendationPipelineRequest`/`RecommendationTools`/
  `RecommendationPipelineResult`/`build_pipeline_request()`/
  `CandidateConcentration`/`_fetch_ranked_concentrations()`/
  `_build_context()`/`_aggregate_concentration_status()`
  (`recommendation_pipeline.py`), 이들만 참조하던
  `app/domain/agent_context.py`(`AgentToolContext`, A-C 계약 이전의
  중복 Context 모델)와 그 단위 테스트, Tool 직접 호출 전용 Fixture
  (`tests/fixtures/recommendation_pipeline_fixture_v1.py`)와 그 테스트
  (`test_recommendation_pipeline_fixture.py`). 하드 필터·이전 노출/거절
  제외·날씨 결측 재분배 같은 D-03 완료 기준은 `test_scoring.py`(단위)와
  `test_recommendation_pipeline.py`의 Context 기반 E2E 테스트(결정성,
  shown_place_ids 제외 테스트 추가)로 계속 커버됨을 확인했다
- 이유: 처음엔 조건 스키마(`InterpretedConditions`→`UserConditions`)
  통합이 끝나야만 라우터를 옮길 수 있다고 판단했으나, 실제로는
  `ContextService`/`UserConditions`를 거칠 필요 없이 C의 순수 매퍼
  함수만 재사용하면 조건 스키마와 무관하게 `RecommendationContext`를
  조립할 수 있다는 것을 확인했다. 따라서 별도 트랙 마이그레이션을
  앞당기지 않고도 완료 기준을 예외 없이 만족시킬 수 있었음
- 추가 정리: 완료 후 재검증 과정에서 `app/domain/candidate_mapper.py`의
  `map_places_to_scoring_candidates()`(D-01/D-02 시절, `NearbyPlaceDetailsResult`를
  직접 받는 함수)가 `run_recommendation_pipeline()` 삭제로 production에서
  전혀 쓰이지 않는 죽은 코드가 된 것을 발견했다. C Tool 결과 타입
  (`app.tools.nearby_place_details.NearbyPlaceDetailsResult`) import가
  D 도메인 코드에 남아있던 마지막 잔재라 이 함수와 전용 헬퍼
  (`_operating_hours_for_visit()`), 관련 테스트 2개(`test_candidate_mapper.py`)까지
  함께 삭제해 D 도메인 코드에서 C Tool 타입 의존을 완전히 제거했다

### D-035 — develop 재병합 시 RecommendationProvider 중복 구현 정리

- 상태: `Implemented`
- 결정: `feature/tech-02-context-boundary`에 최신 `develop`(mintee의
  A-04 작업 포함)을 재병합하는 과정에서, D-033에서 만든
  `recommendation_provider.py`(`RealRecommendationProvider`)와 develop에
  이미 병합돼 있던 mintee의 `real_recommendation_provider.py`가 같은
  `RecommendationProvider` Protocol을 사실상 동일하게 구현한 중복임을
  확인했다. 우리 쪽 파일을 삭제하고 `real_recommendation_provider.py`
  하나로 통합한다
- 구현: `backend/app/services/runtime/recommendation_provider.py`와
  `tests/test_recommendation_provider.py` 삭제. `agent_runtime.py`의
  `run_agent()`가 주입하는 provider를
  `app.services.runtime.real_recommendation_provider.RealRecommendationProvider`
  로 교체. `protocols.py` docstring의 파일 경로 참조도 함께 갱신
- 이유: 두 구현이 로직상 동일(`run_recommendation_pipeline_from_context()`
  호출, `to_search_radius_km()`로 반경 계산)해 어느 쪽을 남겨도 기능
  차이는 없었다. develop에 이미 병합되어 있고 자체 단위 테스트
  (`test_real_recommendation_provider.py`)까지 갖춘 mintee 쪽을 정본으로
  채택해 이후 develop과의 재충돌을 줄이는 쪽을 택했다
- 확인: 병합 후 `ruff check .` 전체 통과, `pytest` 596 passed / 20
  skipped로 회귀 없음을 확인했다. 이 병합으로 develop이 함께 가져온
  `place_search_policy.py`의 `DEFAULT_PLACE_SEARCH_RADIUS_KM`(1.0→2.0)/
  `MIN_PLACE_SEARCH_RADIUS_KM`(0.1→0.3) 변경은 D 코드가 상수를 import해서
  쓰는 값이라 D 쪽 수정 없이 그대로 반영됐다

### D-036 — 혼잡도 fallback: 장소 근접치 채택 (INFO 전용)

- 상태: `Accepted` (INFO 전용)
- 결정: 카페·음식점처럼 집중률 API가 다루는 "관광지" 콘텐츠에 없는 장소를 물으면,
  기존 미결이었던 세 선택지(장소 근접치·구 단위·Feature 제외) 중 **장소
  근접치**를 채택한다 — 대상 장소 자체의 데이터가 없으면
  `search_nearby_places`(`place_types=["attraction"]`)로 가장 가까운 관광지를
  찾아 그 예측치를 "근처 관광지 기준 추정"이라고 명시하며 대체 제공한다
- 이유: "데이터 없음"만 반환하는 것보다 사용자에게 참고할 만한 근사치를 주는
  편이 유용하고, 이미 구현된 `NearbyPlaceDetailsTool`을 그대로 재사용할 수 있어
  새 Provider 연동 없이 구현 가능하다. 구 단위 평균은 데이터를 왜곡할 소지가
  크고, Feature 제외는 사용자 질문에 아예 답하지 못한다는 문제가 있어 제외했다
- 범위: INFO(`question_type=concentration`)의 단일 장소 질의에만 적용한다.
  RECOMMEND 후보에는 직접 조회된 혼잡도만 사용하며, 근접치 fallback은 적용하지 않는다.
  추후 혼잡도 데이터 부족으로 추천 품질 또는 결과 수에 문제가 확인될 때만, C·D 사전
  협의 후 확장을 검토한다.
- 상세 설계: [`docs/design/concentration-conditions.md`](./design/concentration-conditions.md) §3.3
- 탐색 반경: INFO fallback 전용 기본값을 0.5km로 적용한다. 실제 테스트 결과에 따라
  조정할 수 있으며, 코드 단일 기준은 `INFO_CONCENTRATION_FALLBACK_RADIUS_KM`이다.

### D-037 — 혼잡도 반영 방식 재검토: 초기 Context 확장 vs 1차 Scoring 후 상위 5개 보강 재계산 (A-C 협의, D 미확인)

- 상태: `Proposed` (A-C 협의 완료, **D는 아직 확인하지 않음** — 최종 확정 아님)
- 배경: D-036 이전에 확정해뒀던 안(concentration-conditions.md v0.4)은
  "concentration을 초기 Context 요청 단계에서 지역 전체 한 번에 받아오고 D는
  1회만 호출한다"였다. 세션 중 Real Provider로 직접 실측한 결과:
  - 장소 후보 10개 검색(`NearbyPlaceDetailsTool`) — 약 3.0초
  - 집중률 종로구 전체(113곳) 병렬 페이지 조회(20페이지) — 약 3.5초, 순차는
    약 11.8초(`numOfRows=100` 하드코딩이라 페이지네이션 필수)
  - 집중률 장소 1곳 `tAtsNm` 지정 개별 조회 — 약 0.12초

  즉 이미 후보 검색에만 3초가 드는데 지역 전체 집중률까지 얹으면 6.5초 이상으로
  늘어나 이득이 없었다. 반대로 좁혀진 소수 후보만 개별 조회하면 압도적으로 빠르다.
- 제안: "1차 Scoring(10개 후보, concentration 없음 — 기존과 완전 동일) → 상위
  5개 추출 → 그 5개만 기존 `CandidateEnrichmentRequest`/`Response`
  (`CandidateEnrichmentService.enrich()`, 이미 C가 구현해둔 인프라)로 집중률
  보강 조회 → 2차 Scoring(5개+concentration, D 신규 인터페이스)으로 재순위
  계산 → 최종 3개만 사용자에게 노출"로 방향 전환을 제안한다.
- D의 1차/2차 호출 모양(정밀하게 구분): 1차는 입력 10개·concentration 없음
  (기존과 완전 동일, 새로 만들 것 없음). 2차는 입력이 **5개로 축소**되고
  concentration이 추가되는 **신규 인터페이스** — `score_candidates()`는 현재
  단일 호출만 지원해 이런 진입점 자체가 없다(0단계 확인 결과). **D가 이 2차
  인터페이스를 만들어줄 수 있는지 확인이 이 제안의 핵심 블로커다.**
- 참고 근거(develop 병합, 2026-07-30 발견): C가 `place_concentration_mappings`
  테이블(커밋 `019709e`, [place-database-schema.md §6.1](./design/place-database-schema.md#61-place_concentration_mappings))을
  이미 구축해뒀다 — `place_id` ↔ 집중률 API 대표명 매핑 100건(별칭 포함 101곳,
  미매칭 12곳). 아직 런타임 코드엔 미연결이지만, 제안 흐름의 5단계(후보→집중률
  이름 매칭)를 뒷받침하는 정황 근거다.
- 영향 범위: `concentration_intent`가 `AVOID`/`SEEK`일 때만 적용된다.
  `null`/`IGNORE`는 이 재검토와 무관하게 기존 그대로 D 1회 호출로 끝난다.
- "최종 3개" 노출은 기존 `RECOMMENDATION_RESULT_LIMIT`(5, `.env`)와 다른 새
  숫자다 — 기존 설정 어디에도 3을 만드는 상수가 없어 새 상수/자르기 단계가
  필요하다(정확히 3으로 고정할지도 미확정).
- 상세 설계: [`docs/design/concentration-conditions.md`](./design/concentration-conditions.md) §2.2,
  [`docs/design/agent-runtime-contract.md`](./design/agent-runtime-contract.md) §6.5
- TODO: D의 2차 Scoring 신규 인터페이스 확인(가장 중요), "최종 3개" 상수화 여부,
  안 A(초기 Context 확장)와 안 B(이번 제안) 중 최종 택1 — **D 확인 대기**

| 항목 | 선택지/질문 | 상태 |
| --- | --- | --- |
| LLM Provider | 공급자, 모델, timeout, fallback | 모델 fallback `Accepted`(D-052, 구현 완료). 공급자 전환·timeout 정책 자체는 여전히 `TBD` |
| Chat 계약 naming | Backend Python/JSON `snake_case` | `Accepted` |
| Backend 상태 저장 | Supabase 테이블과 캐시 역할 | `TBD` |
| Frontend 저장 | `sessionStorage` 유지 또는 `localStorage` 전환 | `TBD` |
| Scoring v1 | Feature/가중치/tie-break `Implemented`(D-008); Evidence·평가 Fixture `Implemented`(D-027); 응답 Evidence 노출·E2E 통합 `Implemented`(D-028); Explainability Layer v1 `Accepted`(D-029, A 협의 반영 완료); warning 커버리지 보완 `Implemented`(D-030); Explanation 문장 구체화 `Implemented`(D-031); RecommendationContext 진입점 `Implemented`(D-032); Agent Runtime RecommendationProvider 연결 `Implemented`(D-033); Tool 직접 호출 파이프라인 삭제·레거시 라우터 마이그레이션 `Implemented`(D-034); develop 재병합 시 RecommendationProvider 중복 정리 `Implemented`(D-035); 혼잡도 2차 Scoring(`rerank_with_concentration()`) `Implemented`(D-040) | 구현 완료 |
| 혼잡도 fallback | 장소 근접치, 구 단위, Feature 제외 | INFO 전용 장소 근접치 적용(D-036), RECOMMEND 확장은 후속 검토 |
| 혼잡도 반영 방식 | 초기 Context 확장(안 A) vs 1차 Scoring 후 상위 5개 보강 재계산(안 B) | 안 B 채택, `rerank_with_concentration()` 구현 완료(D-040) |
| 운영시간 파싱 | 기본 시간·월별·주간 휴무 구현, 요일 범위 전개·요일별 구간 분리·미열거 요일 휴무 유도 `Implemented`(D-058), 공휴일·회차 예외 확대 | `부분 구현` |
| 이동시간 | 지도 Provider 및 교통수단별 계산 | `TBD` |
| 조건 완화 | 자동 완화 범위와 사용자 확인 UX | `TBD` |
| 관측성 | 구조화 로그, tracing, 보존기간 | `TBD` |
| Provider metadata 구현 | 5개 Provider 공통 `ProviderResult`와 UTC metadata 적용 | 구현 완료 |
| Tool 오류 구현 | 5개 Tool에 공통 상태·오류·warning·provider metadata 적용 | 구현 완료 |
| Provider Blocker | P0~P3 표의 해결 조건 기준으로 추적 | 목록 확정/해결 진행 `TBD` |
| ProviderError 구현 | 공통 오류 모델, sanitize, ToolError 변환 | 설계 확정/구현 `TBD` |
| Weather 방문시각 선택 | visit_at 입력, forecast_for 선택, 범위 초과 처리 | 구현 완료 |
| 배포 | Hosting, CI/CD, Secret 관리 | `TBD` |

### D-038 — 날씨 warning 문구 분리(IGNORE vs 조회 실패) 및 날씨 조회 경로 이원화 정리

- 상태: `Implemented` (2026-08-05, TODO 1/2 해결 완료 — 아래 참고)
- 주의: 이후 D-049(2026-08-05)에서 `weather_intent`에 `NO_MENTION`을 도입하면서
  "언급 없음=IGNORE" 전제가 해소됐다. 이 항목의 IGNORE 서술은 당시 구현 기준의
  배경 설명이며, 현재 동작 기준은 D-049를 따른다.
- 배경: `"경복궁 근처 카페 추천해줘"`처럼 날씨 언급이 없는 발화는 LLM이
  `weather_intent=IGNORE`로 판정하고([int-01-recommend.md §8](./design/int-01-recommend.md#8-weather_intent-판별)의
  정의: "날씨 언급 없음"), C가 Weather Tool을 실행하지 않는다
  (`tool_rules.py`). 정상 흐름인데도 사용자에게 "현재 날씨 정보를 확인하지
  못해 이 조건은 반영되지 않았어요"라는 **조회 실패 문구**가 나갔다.
- 결정 1 (`Implemented`): `recommendation_pipeline.py`의 날씨 warning을 두 개로
  나눈다. `context.weather`가 `None`이면 조회를 시도하지 않은 것(IGNORE)이므로
  `_WEATHER_IGNORED_WARNING`("날씨 조건을 따로 말씀하지 않으셔서 …"), 값이
  있는데 status가 실패면 기존 `_WEATHER_MISSING_WARNING`을 쓴다. 개발 단계에서는
  IGNORE로 처리됐다는 사실 자체를 보여줄 필요가 있어 warning을 없애지 않고
  문구만 구분했다 — 사용자 노출용 문구 확정은 UX 논의가 필요하다.
- TODO 1 (`Implemented`, 2026-08-05) — **§10과 구현 불일치 해결**: B(이태화)의
  리뷰를 계기로 재조사한 결과, "구현이 맞고 문서가 틀렸다"로 결론냈다 —
  `weather_intent=IGNORE`면 API 호출 자체를 안 하는 현재 구현이 의도된 동작이고
  (안 쓸 값을 굳이 조회할 이유가 없다), [int-01-recommend.md
  §10](./design/int-01-recommend.md#10-날씨-정보-확보-순서)을 이 실제 동작에
  맞게 재작성했다.
- TODO 2 (`Implemented`, 2026-08-05) — **날씨 조회 경로 이원화 해결**: 직접
  재검증한 결과 B 세션 `api_context.api_weather`를 실제로 읽는 소비자가
  backend/frontend 어디에도 없음을 확인했다(패스스루 복사와 TTL 타임스탬프
  판정만 있고, 값 자체를 쓰는 로직은 전무) — 실제 RECOMMEND 날씨 Feature는
  D가 `to_weather_condition()`으로 참조하는 C `context.weather` 경로 하나뿐이었다.
  `api_weather`를 채우던 A 소유 코드(`session_orchestrator.py::_refresh_weather()`
  와 그 호출부, `weather_provider` 파라미터 일체)를 제거해 조회 경로를 C
  하나로 통합했다. B 소유 스키마(`ApiContext.api_weather`/
  `api_weather_updated_at`/`weather_expired`, `is_weather_expired()`)는
  건드리지 않았다 — 값이 영구히 비게 될 뿐 깨지는 소비자가 없어서다. B가
  원하면 이 필드들을 스키마에서 정리(deprecate)하는 것도 가능하다는 걸
  제안으로 남긴다.
- 확인 방법: `device_location`을 넣어도 `weather_intent=IGNORE`면
  `feature_scores.weather`가 `null`이고 `weights_used`에서 날씨 0.4가 빠져
  나머지에 재분배된다(정상). 실제 응답으로 확인함. TODO 1/2 해결은 전체
  pytest 회귀(회귀 없음)와 `api_context.api_weather`가 항상 `None`으로
  응답에 남는지로 확인.

### D-039 — 되묻기 답변은 새 요청이 아닌 기존 요청의 연속으로 처리

- 상태: `Implemented` (PR #64)
- 배경: 첫 요청에 위치가 없어 C가 `needs_clarification`을 반환한 뒤 사용자가
  위치만 답하면, 두 번째 발화가 새 `RECOMMEND`로 분류되면서
  `reset_scope="soft"`가 적용됐다. 이 과정에서 첫 요청의 카테고리·선호 조건이
  초기화되어 사용자가 이미 말한 조건과 다른 후보가 추천될 수 있었다.
- 결정: LLM 또는 C 단계의 되묻기로 끝난 턴은 B에
  `pending_clarification`으로 기록한다. 다음 `RECOMMEND`/`MODIFY` 발화는 새 요청이
  아니라 같은 요청을 완성하는 답변으로 보고 `reset_scope=None`을 사용한다.
  답변에서 새로 추출된 조건만 Operation으로 전달하고, 언급되지 않은 기존 조건은
  B가 유지한다.
- 책임 분리:
  - A는 LLM/C 응답을 보고 되묻기 여부와 저장할 사유 코드를 결정한다.
  - B는 `pending_clarification`을 해석하지 않고 세션 상태에 저장·조회만 한다.
  - `pending_clarification` 갱신은 사용자 조건 변경이 아니므로
    `condition_version`을 증가시키지 않는다.
- 소비 규칙: 조건을 처리하는 `RECOMMEND`/`MODIFY`에서만 기존 플래그를 소비한다.
  `INFO`/`COMPARE`/`GENERAL` 같은 곁가지 발화는 이전 되묻기 상태를 유지한다. 같은
  턴이 다시 되묻기로 끝나면 새로운 사유 코드로 다시 저장한다.
- 예외: 사용자가 "처음부터 다시"처럼 명시적인 초기화 표현을 사용하면 되묻기
  답변 중이어도 새 요청으로 보고 기존 `reset_scope="soft"` 규칙을 적용한다.
- 구현: `AgentState`/`SessionContextResponse.pending_clarification`,
  `set_pending_clarification()`, `state_transform.transform()`의 조건 병합 분기,
  `agent_runtime.run_agent_flow()`의 LLM/C 되묻기 기록·소비 흐름.
- 확인: 위치 없는 카페 추천 → `location_required` 되묻기 → "경복궁 근처" 답변
  시 기존 `place_tags`가 유지되고 위치만 추가되는 흐름, 명시적 재시작 시 soft
  reset이 적용되는 흐름, 성공 추천 후 플래그가 남지 않는 흐름을 테스트한다.
- 제한사항: `pending_clarification`은 InMemory 상태에는 반영됐지만, 현재
  `SupabaseStateStore`와 DB migration에는 컬럼·영속화 매핑이 없다. Supabase 모드에서
  턴 간 되묻기 상태를 보존하려면 별도 migration과 store 매핑을 추가해야 한다. 단독 위치 답변의
  분류 문제는 TP-67(PR #113)과 D-053에서 결론이 났다 — 지명에 근처/조사가 붙은 발화는 이전
  추천이 있으면 `MODIFY`로 분류하고, 지명 단독은 정보 조회 의도로 보아 `INFO`를 유지한다.

### D-040 — 혼잡도(concentration) 2차 Scoring 구현: 안 B 채택, D 신규 인터페이스 확정

- 상태: `Implemented`
- 결정: D-037이 제안한 안 B(1차 Scoring 후 상위 5개만 혼잡도 보강 재계산)를
  D가 확인·채택한다. `RecommendationProvider.rerank_with_concentration()`(A가
  `protocols.py`에 미리 배선해둔 메서드)을 `RealRecommendationProvider`에
  실제로 구현했다.
- **A에 Protocol 시그니처 변경 요청**: `rerank_with_concentration(conditions,
  first_pass, concentration)`엔 원본 `RecommendationContext`가 없어 날씨
  근거 문장을 1차와 동일하게 재구성할 방법이 없었다 — 4번째 파라미터
  `context: RecommendationContext`를 추가해달라고 요청했고, `agent_runtime.py`의
  `_apply_concentration_rerank()`가 이미 갖고 있던 `tool_context`를 그대로
  전달하도록 배선을 바꿨다(`protocols.py`, `agent_runtime.py`, `stubs.py`의
  Fake 구현체, `tests/test_agent_runtime.py`의 테스트 더블 모두 시그니처 갱신).
- concentration_score 공식: 4단계 구간 매핑 대신 **선형 정규화**를 채택했다
  (`concentration_score(rate, seek) = clamp(rate/100, 0, 1)`, AVOID는
  `1 - rate/100`). distance/remaining_operating_time과 같은 연속값 스타일을
  유지하고 정보 손실을 피하기 위함(`domain/scoring.py::concentration_score()`).
- 가중치: A 제안값(`CONCENTRATION_WEIGHTS` — 날씨 0.35/운영시간 0.35/거리
  0.15/혼잡도 0.15)을 그대로 채택했다. 이 상수는 2차 Scoring 전용이라 1차
  `DEFAULT_WEIGHTS`(0.40/0.40/0.20)에는 영향이 없다 — D-037이 걱정했던
  "혼잡도 무관심 실행에도 기존 가중치가 미세하게 바뀌는 문제"는 안 B 구조라
  애초에 발생하지 않는다.
- 구현 방식: 2차 Scoring은 새 `ScoringCandidate`를 다시 만들지 않고, 1차
  `RecommendationItem.feature_scores`(weather/remaining_operating_time/distance,
  concentration과 무관하게 불변)를 그대로 재사용해 concentration만 추가하고
  `redistribute_weights()`(기존 함수 재사용)로 재분배한 뒤 재정렬한다.
  결측(C가 `no_data`/`unavailable` 반환) 처리는 weather/remaining_operating_time과
  동일한 개별 결측 패턴을 따른다.
- Evidence/Explanation 확장: `evidence.py`의 `_FEATURE_ORDER`(1차, 3-Feature)는
  그대로 두고 `CONCENTRATION_FEATURE_ORDER`(4-Feature)를 별도로 추가했다 —
  `_FEATURE_ORDER`를 직접 확장하면 1차 결과의 `feature_scores`에도 `concentration:
  null`이 항상 끼어들어 `test_scoring_fixture.py`의 `{c.feature for c in
  evidence.contributions} == set(ranked.feature_scores)` 검증이 깨지는 걸
  구현 중 발견해서 분리했다. `RankedCandidate`/`RecommendationEvidence`에
  `concentration_level: ConcentrationLevel | None = None`(4단계 구간 원본)을
  추가했다 — concentration_score는 이미 SEEK/AVOID 방향이 반영된 값이라
  notable 여부만으로는 실제로 붐비는지 한적한지 알 수 없어서, 문장 조립에는
  방향과 무관한 원본 구간을 따로 보존해야 했다.
- 범위 제외: `_CONCENTRATION_FINAL_LIMIT`(최종 3개) 확정, 안 A(초기 Context
  확장) 대비 이번 선택의 재검토는 A/기획 몫 — D는 2차 인터페이스 구현까지만
  책임진다.
- 확인 방법: `tests/test_scoring.py`(concentration_score·재분배 단위),
  `tests/test_explanation.py`(4단계 구간별 문장·임계값·결측),
  `tests/test_recommendation_pipeline.py`(AVOID/SEEK 재정렬 실제로 뒤집히는지,
  부분 결측, unverified 분리 유지), `tests/test_real_recommendation_provider.py`
  (Protocol 위임·seek 변환)로 검증. 전체 회귀(709 passed, 20 skipped) 확인.

### D-041 — 장소명 위치 해석: `places` DB 우선 조회

- 상태: `Implemented` (정확 일치 MVP 범위)
- 결정: 장소명 입력을 Naver Geocoding에 바로 전달하지 않는다. `ResolveLocationTool`이
  활성 `places` 행을 정확 일치로 먼저 조회해 TourAPI 기준 좌표를 사용하고, 미조회 시에만
  기존 별칭·주소 Geocoding 경로로 진행한다.
- 배경: Geocoding은 주소 중심 API라 `쌈지길` 같은 장소명은 `location_not_found`가 될 수
  있지만, 해당 장소는 TourAPI `places` DB에 좌표가 존재한다.
- 집중률: 장소 행에 `place_concentration_mappings`가 있으면 대표 집중률명을 함께 읽어
  INFO 직접 조회에 사용한다. 직접 조회에 좌표가 필요 없더라도, 매핑이 없는 경우의 0.5km
  fallback에는 같은 DB 좌표를 사용한다.
- 안전장치: Fake Provider 모드에서는 실제 Supabase 조회를 주입하지 않아 일반 테스트가
  외부 데이터에 의존하지 않는다.
- 범위 제외: 별칭·부분 일치, Naver Local Search, DB 미등록 일반 상호명 처리, RECOMMEND
  후보 보강의 매핑 연결은 후속 작업이다.


### D-042 — Real Provider 실패 시 Fake로 자동 전환하지 않는다

- 상태: `Accepted`, 코드에 이미 반영됨(요청 중 Provider를 교체하는 경로가 없음)
- 결정: Provider는 부팅 시점에 설정(`PROVIDER_MODE`, `*_PROVIDER`)으로 한 번 정해지고,
  요청 처리 중 Real → Fake 전환은 하지 않는다. 실패는 `unavailable`로 드러내거나 같은
  성격의 다른 Real 경로로 넘어간다.
- 이유: 조용한 폴백은 "실데이터를 보고 있는지"를 판단 불가능하게 만든다. 잘못된 데이터가
  정상 응답처럼 나가면 사용자도 개발자도 문제를 인지하지 못한다.
- 근거 사례: `npm run dev`가 `backend/.env`를 읽지 못해 전 Provider가 fake로 뜬 사건
  (커밋 `f201f0b`). 오류 없이 `"테스트 카페"`가 추천됐고, 실행 위치 문제임을 찾는 데
  시간이 걸렸다. 이후 `env_file`을 절대경로로 고정하고 부팅 시 Provider 모드를 로그로
  남기도록 했다.
- 이미 이 원칙에서 나온 결정들:
  - `validate_provider_config()` — real 모드에 키가 없으면 첫 요청이 아니라 **부팅에서** 실패
  - Supabase 상세조회 실패 시 요청 중 TourAPI fallback 없음(D-041 관련)
  - Local Search 장애 시 Geocoding으로 진행 — Fake가 아니라 다른 Real이라 위배 아님(D-041)
- 범위 밖: 재시도(retry)와 서킷 브레이커는 이 결정과 별개다. 같은 Real Provider를 다시
  부르는 것은 허용된다.


### D-043 — 혼잡도 장소 결정은 이름 우선, 좌표는 최후수단

- 상태: `Accepted`, 2026-08-04 구현 완료. 마이그레이션(`20260804055402`)과 매핑 101건
  적재까지 반영했다.
- 배경: 집중률 API의 `tAtsNm`은 부분 일치 검색인데, **공백이 든 값을 넘기면 0건**이
  돌아온다(2026-08-04 실측: `운현궁` 30건, `서울 운현궁` 0건). 그래서 정식 명칭을 그대로
  조회에 쓸 수 없고, 공백 없는 검색어를 따로 뽑아야 한다.
- 확인된 오답(현재 실서비스):
  - `북촌 혼잡해?` → `tAtsNm='북촌'`이 2곳을 반환하고 첫 행인 **북촌생활사박물관 52.19**를
    답한다. 의도한 북촌한옥마을은 61.97이다.
  - `종로 혼잡해?` → 3곳이 걸리고 **낙지볶음 골목 77.08**을 "종로의 혼잡도"로 답한다.
  - `종묘` → 종묘광장공원(35.28)이 섞여 오지만 응답 순서 덕에 우연히 맞는다. 순서가
    바뀌면 조용히 틀린다.
- 결정한 우선순위:
  1. DB `places` 정확 일치 → 매핑 사용 (경복궁·종묘)
  2. 로컬 검색으로 이름을 얻은 뒤 **그 이름으로 DB 재조회** → 매핑 사용 (북촌)
  3. 좌표 최근접 매핑 장소 → `is_proxy=true`로 어느 장소인지 밝힘 (종로)
  4. 반경 밖 → 정보 없음
- 좌표를 주 경로로 올리지 않는 이유: 넓은 장소는 대표점이 부정확하다. 로컬 검색이 준
  "북촌 한옥마을" 좌표 기준 최근접은 **가회민화박물관(27m)**이고 북촌한옥마을은 293m로
  밀린다. 종묘와 종묘광장공원도 67m 차이라 지오코딩이 흔들리면 뒤바뀐다.
- 끊겨 있는 지점 두 곳:
  - `resolve_location.py`의 `_local_search_success()`가 `concentration_name`을 채우지
    않는다 — 로컬 검색이 얻은 이름으로 매핑을 다시 찾지 않는다.
  - `find_active_places_by_name()`이 정확 일치뿐이라 "북촌 한옥마을"이 "북촌한옥마을"과
    안 맞는다. 공백 무시 조회가 필요하다.
- 폴백 축소: `select_concentration_forecast()`는 이름이 안 맞으면 `forecasts[0]`을 쓴다.
  **여러 장소가 왔는데 정식 명칭 일치가 없으면 `no_data`**로 바꾼다. 한 곳만 왔을 때는
  표기 차이여도 그 장소가 맞으므로 현행을 유지한다. 틀린 값보다 "정보 없음"이 낫다.
- 검색어와 정식 명칭 분리: `primary_concentration_name`에 `앞길`·`100주년` 같은 검색어를
  넣으면 컬럼 의미가 어긋나고, "별칭 첫 항목이 정식 명칭"이라는 암묵 규약이 생긴다.
  `concentration_search_key` 컬럼을 추가해 조회용과 대조용을 나눈다(nullable로 두면 기존
  데이터가 그대로 동작해 배포 순서를 안 따져도 된다).
- 검색어 추출 규칙(`build_concentration_mappings.py`에 구현·테스트 완료):
  공백 없으면 그대로 → 괄호·대괄호 부기 제거 → 집중률 목록 안에서 유일한 최장 토큰.
  유일성은 **집중률 API 목록(113건) 안에서만** 따진다. `places`에 "한옥"을 쓰는 장소가
  25건 있어도 `tAtsNm`은 집중률 데이터셋만 검색하므로 무관하다. 짧고 흔한 토큰을 피해
  최장을 고르는 이유는, 나중에 장소가 추가되면 모호해질 수 있어서다. 이 계산은 수집한
  목록이 완전하다는 전제에 기대므로 **매핑할 때마다 다시 계산**해야 한다.
- 저장소 이름 조회는 좁은 것부터 넓힌다: 정확 일치 → 공백 무시(`북촌 한옥마을` ↔
  `북촌한옥마을`) → 괄호 부기(`종묘` → `종묘 [유네스코 세계유산]`) → 사람이 지정한 별칭
  (`창덕궁` → `창덕궁과 후원 [유네스코 세계유산]`). 접두 매칭으로 넓히지 않는다 —
  `창덕궁*`은 낙선재·약다방·상품관까지 11건을 끌어온다.
- 별칭은 "이 장소를 가리키는 다른 이름"이다. 집중률 API에 있을 필요가 없다(`창덕궁`은
  없다). 조회는 `concentration_search_key`가 맡으므로 별칭을 집중률 목록으로 거르지
  않는다. 이 완화로 넣어두고도 쓰이지 않던 `청와대` 별칭이 함께 살아났다.
- 구현 후 실측(2026-08-04):

  | 질의 | 이전 | 이후 |
  | --- | --- | --- |
  | 북촌 | 북촌생활사박물관 52.19 | **북촌한옥마을 61.97** |
  | 종로 | 낙지볶음 골목 77.08 | 되묻기 |
  | 종묘 | 67.69(응답 순서에 의존) | **67.69(이름 대조로 확정)** |
  | 종묘광장공원 | — | **35.28(별개로 구분)** |
  | 창덕궁 | 되묻기 | **창덕궁과 후원 60.91** |
  | 청와대 | 되묻기 | **청와대 앞길 33.43** |
  | 서울 운현궁 | 조회 0건 | **69.42** |
  | 내자상회 | 정보 없음 | **사직공원 43.3(`is_proxy`)** |

- fake 환경에는 저장소가 없어 INFO 혼잡도가 전부 `no_data`로 막힌다.
  `FakePlaceLocationRepository`를 추가하고 `ProviderSource.FAKE_PLACES`로 구분한다 —
  fake 저장소가 실저장소로 보이면 안 된다(D-042).
- 남은 과제: `종로`처럼 지역·도로 단위 질문은 최근접 한 곳으로 대표시키는 것이 맞는지
  자체가 의문이다(종로는 길이 2.7km). 우선 `is_proxy`로 밝히고 넘어가되 별도로 다룬다.
  매핑 101건 전수 조회 검증(`verify_concentration_mappings`)도 아직 실행하지 않았다.

### D-044 — 지원 지역 밖 위치는 해석 단계에서 unsupported로 끊는다

- 상태: `Accepted`, 2026-08-04 구현 완료.
- 문제: 종로구 밖 위치를 막는 코드가 없었다. `resolve_location`의 첫 줄은 "종로구 범위의
  좌표로 해석"이라고 적혀 있지만 실제로 좌표를 검사하지 않았고, 종로구 랜드마크 별칭
  테이블이 결과적으로 그 역할을 대신하는 것처럼 보였다.
- 증상: 세 경로가 각각 다른 이유로 실패하며 **모두 틀린 안내**를 냈다.

  | 경로 | 실패 지점 | 사용자에게 나간 말 |
  | --- | --- | --- |
  | 추천 | 좌표는 마포, 검색은 종로 고정 → 교집합 0건 | "조건에 맞는 곳을 찾지 못했어요. 검색 범위를 넓혀볼까요?" |
  | 혼잡도 | 매핑 없음 → 0.5km 내 종로 장소 없음 | "이 장소 유형은 혼잡도 데이터가 없어요." |
  | 되묻기 | 지역 검색 후보를 못 좁힘 | "종로구 안에서 어느 장소인지 알려주세요" |

  추천 문구가 특히 나쁘다 — 범위를 넓혀도 종로구 밖은 영영 나오지 않는데 넓혀보라고 한다.
- 결정: `ResolveLocationTool`이 좌표를 얻은 직후 한 곳에서 판정한다. 아래 로직은 모두
  "종로구 안"을 전제로 하므로(장소 검색이 `lDongSignguCd` 고정, 집중률 매핑도 종로구
  장소만 보유) 해석 단계에서 끊는 것이 맞다. 세 경로가 한 번에 정리된다.
- 상태·코드는 이미 계약에 있었다 — `ToolStatus.UNSUPPORTED`, `cause="outside_supported_region"`,
  `_error_message()`의 "현재는 서울특별시 종로구 내 장소만 지원합니다."까지 준비돼 있고
  A의 `response_composer`도 `unsupported`를 종결 처리한다. **판정하는 코드만 없었다.**
- 판정 방법으로 폴리곤을 쓴다.

  | 후보 | 채택 여부 |
  | --- | --- |
  | 경계 상자 | 기각. 종로구가 남북으로 길쭉하고 북악산 쪽으로 굽어 있다. 여유를 주면 중구 명동(37.5636)·서울역이 안쪽으로 들어온다. |
  | 역지오코딩 | 기각. 네이버는 별도 구독 상품이라 현재 키로 403이다. 구독하더라도 판정 하나에 외부 호출과 장애 지점을 매 요청 추가하게 된다. |
  | 폴리곤(채택) | 정적 데이터라 외부 호출 0. 좌표 194개, 8KB. 명동·서울역도 정확히 갈린다. |

- 저장소에서 해석된 장소는 판정을 생략한다. 이미 종로구 장소로 등록된 것이라 경계선에
  붙어 있어도 지원 대상이 맞다. 활성 844건 중 폴리곤 밖으로 판정되는 2건(북악산 숙정문,
  청계천 남쪽 행사)이 모두 저장소 장소라 이 규칙으로 해소된다.
- 지역 판정을 모호성 판정보다 앞에 둔다. 지원 범위 밖이면 "어느 장소인지" 되물어도
  소용없다. 지역 검색이 후보를 못 좁혔을 때도 찾은 후보가 전부 밖이면 지역 문제로 본다 —
  "부산 해운대"에 "종로구 안에서 어느 장소인지"라고 되묻지 않기 위해서다.
- 구현 후 실측(2026-08-04): 망원역·서울역·부산 해운대·강남역·제주도가 모두
  `unsupported`, 경복궁·북촌·내자상회·창덕궁·인사동길 44는 그대로 `success`.
- A 응답 문구도 함께 고쳤다. `_TOOL_UNSUPPORTED_MESSAGE`가 "죄송하지만 아직 지원하지 않는
  요청이에요"로 고정이라 무엇을 바꿔야 할지 알 수 없었다. `error.code`별 템플릿을 두고
  `unsupported_region`이면 "지금은 서울 종로구 안의 장소만 안내할 수 있어요."를 보낸다.
  RECOMMEND 경로는 코드를 받지 못하고 있어 `compose_chat_message(tool_error_code=...)`를
  추가했다. **A 담당 부재(2026-08-04)로 C에서 대신 수정했으므로 복귀 후 공유가 필요하다.**
- 경계 데이터 갱신: 행정구역 개편 시 `backend/resources/boundaries/README.md` 절차를 따른다.
- **2026-08-24 확장(TP-125): 지원 구가 종로구 하나에서 종로구·중구·용산구·성동구
  네 곳으로 늘었다.** 판정 방식은 그대로다 — 구별 폴리곤을 순회해 어느 하나 안이면
  지원 대상으로 본다.
- 경계 파일은 지원 구만 담지 않고 **서울 25개 구를 한 파일(`seoul.geojson`)에 다
  담는다.** 지원 구를 늘릴 때 파일 작업 없이 `SUPPORTED_DISTRICTS`에 한 줄만 더하면
  되게 하기 위해서다. 미리 담는 비용은 전체 214KB·파싱 2.5ms·프로세스당 1회라
  사실상 없다(지원 구 4곳만 담으면 30KB였다).

  | 후보 | 채택 여부 |
  | --- | --- |
  | 구별 파일 | 기각. 구를 늘릴 때마다 추출 스크립트를 돌려 파일을 만들어야 한다. |
  | 파일에 있는 구를 전부 지원 | 기각. 지원 범위는 팀이 합의하는 결정이라 코드에 드러나고 리뷰를 거쳐야 한다. 파일 하나로 범위가 조용히 바뀌면 안 된다. |
  | 환경변수로 지원 구 지정 | 기각. 개발·운영 범위가 조용히 달라질 수 있다(D-042와 같은 유형). |
  | 25개 구 한 파일 + 코드에 지원 목록(채택) | 구 추가가 한 줄. 목록에 있는데 경계가 없으면 첫 판정에서 예외로 끊는다. |

- 활성 2,570건 중 폴리곤 밖은 4건(0.16%)이고, 그중 둘은 경계 정밀도가 아니라 원본
  데이터 문제다(등록 구가 틀린 것 1건, 좌표가 깨진 것 1건). 장소 검색은 여전히
  `lDongSignguCd`로 종로구에 고정돼 있어, 이 변경만으로는 추천 결과가 달라지지
  않는다(TP-126에서 해제).

### D-045 — 같은 역의 노선별 후보는 한 장소로 본다

- 상태: `Accepted`, 2026-08-04 구현 완료.
- 문제: "종로3가역 근처 카페"는 되묻기로 빠지는데 "종각역 근처 카페"는 추천이 나갔다.
  차이는 환승역 여부다. 지역 검색이 "종로3가역"에 1·3·5호선을 각각 돌려주는데, 이름이
  달라 정확 일치가 안 되고 첫 토큰은 셋 다 같아 못 좁히므로 재질문으로 떨어졌다.
- 되물어도 답이 될 수 없다. 카페를 찾는 사용자에게 몇 호선인지 묻는 것은 무의미하고,
  사용자가 "종로3가역 3호선"처럼 노선까지 적어야 진행된다. 검색 반경이 2km라 어느
  출입구를 골라도 결과는 같다.
- 결정: 첫 토큰이 모두 일치하는 후보가 **전부 교통 시설이고 서로 0.5km 이내**면 한
  장소로 보고 첫 후보를 쓴다. 둘 중 하나라도 어긋나면 기존대로 재질문한다.
- 거리 기준 근거 — 실측(2026-08-04) 역별 후보 간 최대 거리:

  | 역 | 후보 | 최대 거리 |
  | --- | --- | --- |
  | 청량리역 | 5 | 381m |
  | 서울역 | 5 | 341m |
  | 종로3가역 | 3 | 291m |
  | 시청역 | 2 | 267m |
  | 김포공항역 | 5 | 248m |
  | 공덕역 | 4 | 187m |
  | 왕십리역 | 5 | 137m |
  | 충무로역 | 2 | 47m |

  노선 수가 아니라 역사 구조가 거리를 결정한다(5개 노선인 왕십리역이 가장 좁다).
  관측 최대가 381m라 0.5km면 덮는다. 같은 역을 재조회했을 때 291m/170m로 흔들린 적이
  있어 여유가 필요하다.
- 카테고리를 함께 보는 이유: 거리만 보면 "종각역 김밥천국"처럼 역명을 그대로 앞에 붙인
  상호가 함께 묶인다. 그러면 "첫 후보를 임의로 고르지 않는다"는 원칙이 깨진다 — 쌈지길
  검색에서 정답이 3번째였던 것이 그 원칙의 근거다. 상호가 하나라도 섞이면 묶지 않는다.
- 카테고리 표기는 실측 4종을 덮는다: `지하철,전철` 25건, `KTX,SRT정차역`,
  `기차역`, `KTX정차역` 각 1건. 네이버가 표기를 바꾸면 조용히 재질문으로 돌아가므로,
  되묻기가 늘면 이 목록을 먼저 확인한다.
- 정확 일치가 중복인 경우(1단계)는 손대지 않았다. 이름이 완전히 같은 것은 서로 다른
  지점일 수 있어 성격이 다르다.
- `haversine_km`을 `app/geo.py`로 옮겼다. `tools`가 `agent_context`를 import하면 순환
  참조가 되고(agent_context가 tools를 쓴다), 같은 공식의 세 번째 사본을 만들지 않기
  위해서다. `app/domain/candidate_mapper.py`의 사본은 D 범위라 그대로 뒀다.

### D-046 — environment_type을 TourAPI 중분류(lcls_systm2) 기반으로 세분화

- 상태: `Implemented` (`app/domain/candidate_mapper.py::_environment_type()`)
- 배경: `environment_type`은 날씨 적합도(가중치 0.40)에 쓰이는데, 대분류(`category`)
  만으로는 실내외를 정확히 가릴 수 없다 — C의 실측 기준(2026-08-04 종로구 스냅샷) 관광지
  150건에 고궁·공원(실외)과 체험관(실내)이, 쇼핑 211건에 면세점 189건(실내)과 시장
  9건(실외)이 섞여 있다. C가 `bde29a3`(카테고리 어휘를 `PlaceType`으로 통일)·`5a3dacc`
  (C→A 계약에 `lcls_systm1/2/3` 3단계 분류 코드 추가)로 재료를 제공했고, 세분 판정
  규칙은 D가 정하기로 했다.
- 결정: 후보의 소분류 코드(`lcls_systm3`)를 `TourCategoryRegistry.get_by_small_code()`
  로 조회해 `(content_type_id, lcls_systm2)` 중분류 조합을 얻고, D가 정한 판정표와
  비교한다. 소분류 코드가 없거나 Registry에 없는 코드(과거 데이터 등)면 기존 대분류
  기반 최소 매핑(`_INDOOR_CATEGORIES`/`_OUTDOOR_CATEGORIES`)으로 폴백한다.
- 판정 기준: (1) 날씨를 실제로 막아주는 지붕·벽이 있는가를 핵심 질문으로 삼는다.
  (2) 중분류 안 소분류가 한쪽으로 쏠리면 그쪽으로, 진짜 애매하면 `unknown` 유지.
  (3) 같은 중분류 코드가 content_type에 따라 다른 뜻일 수 있어(`VE12`: 문화시설=서점,
  레포츠=카지노) `content_type_id`와 조합해서만 조회한다. (4) 확신 없으면 `unknown`
  유지 — 이미 안전한 중간값 폴백(맑음 0.85/보통 0.80/나쁨 0.60)이 있다.
- 결과: D가 다루는 6개 content_type(12/14/15/28/38/39)의 중분류 48개를 전수 검토해
  indoor 19개·outdoor 16개·unknown 13개로 판정했다(축제·공연·시장 일부 등 장소마다
  실내외가 갈리는 항목은 `unknown` 유지). 상세 판정 근거와 표는
  `package_D/feature-environment-type-classification.md` 참고(개인 기록, gitignored).
- 테스트: `tests/test_candidate_mapper_environment_type.py`에 48개 중분류 판정을
  고정하는 파라미터화 테스트, `tests/test_candidate_mapper.py`에 소분류 우선순위·폴백
  동작 검증 테스트를 추가했다.
- 경계 판단: `app/domain/`이 `app/providers/`(`TourCategoryRegistry`)를 직접 import하는
  첫 사례다. TECH-02가 없앤 건 "D가 실행 중에 C의 Tool을 직접 호출"하는 런타임 의존인데,
  이 조회는 JSON 파일을 프로세스 시작 시 한 번 로드해 참조만 하는 정적 테이블(부작용
  없음)이라 성격이 다르다고 보고 TECH-02 위반이 아니라고 판단했다. C도 `5a3dacc`에서 이
  조회 방식을 직접 지정했지만, 이 "정적 참조 vs 런타임 호출" 구분 자체는 C 확인 없이
  D가 내린 판단이라 별도로 확인 요청했다 — **C 승인 완료(2026-08-04): D의 판단대로
  진행하면 된다고 확인받음.**
- 범위 제한: 2차 Scoring(혼잡도, D-040)에는 영향 없음 — `environment_type`은 1차
  Scoring의 날씨 Feature 계산에만 쓰인다.

### D-047 — `rerank_with_concentration()` 시그니처 축소 제안: `RecommendationContext` → `WeatherCondition`

- 상태: `Implemented`(2026-08-05, 커밋 `baf4051`)
- 배경: D-040 구현 중 D가 요청해 `rerank_with_concentration()`에
  `context: RecommendationContext` 전체를 4번째 파라미터로 추가했다(날씨 근거
  문장을 1차와 동일하게 재구성하려면 원본 `WeatherCondition`이 필요하다는 이유).
  이후 D가 코드 리뷰 중 재확인한 결과, `services/recommendation_pipeline.py::
  rerank_with_concentration()`(141-282행) 안에서 `context`는 165행
  `_weather_condition_from_context(context)` 호출 **한 곳에서만** 쓰이고
  `location`/`places`/`concentration` 등 다른 필드는 전혀 참조하지 않는다는 걸
  확인했다 — "1차 호출과 반드시 동일한 context여야 한다"는 암묵적 전제를 없애기
  위해 시그니처를 `WeatherCondition` 값 하나로 좁히자고 A에 역제안했다.
- 제안 시그니처: `rerank_with_concentration(conditions, weather_condition:
  WeatherCondition | None, first_pass, concentration)` — 2번째 파라미터(현재
  `context`) 자리를 유지한 채 타입만 `RecommendationContext` → `WeatherCondition
  | None`로 좁힌다.
- **D 확인 필요 — 값 도출 필터 기준**: D 내부의 `_weather_condition_from_context()`
  (같은 파일 287-293행)는 `weather.status`가 `{"success", "partial"}`일 때 값을
  반환한다. A가 이미 갖고 있던 `to_weather_condition()`
  (`app/services/runtime/recommendation_transform.py:53-64`, 현재 프로덕션 어디서도
  호출되지 않는 dead code — 자체 테스트에서만 참조됨)은 `"success"`일 때만 값을
  반환해 필터 기준이 다르다. 시그니처를 좁히면서 A가 새 값을 도출할 때 D의 기존
  `{"success", "partial"}` 필터를 그대로 따라야 동작이 안 바뀐다 — A의 기존
  `to_weather_condition()`을 그대로 재사용하면 `"partial"` 상태일 때 결과가 조용히
  달라진다. 이 필터 기준을 D가 명시적으로 확인해달라.
- 변경 대상 파일: A 소유 — `app/services/runtime/protocols.py`(Protocol 선언),
  `app/services/runtime/agent_runtime.py`(`_apply_concentration_rerank()` 호출부),
  `app/services/runtime/stubs.py`(`FakeRecommendationProvider`, 내부에서
  `context`/`conditions` 둘 다 안 쓰므로 기계적 변경). D 소유 —
  `app/services/runtime/real_recommendation_provider.py`(48-58행, `context`를
  그대로 전달하던 부분), `app/services/recommendation_pipeline.py`(141-148행,
  실제 로직). 테스트 3파일(`test_agent_runtime.py`,
  `test_real_recommendation_provider.py`, `test_recommendation_pipeline.py`)
  총 12개 함수가 이 시그니처를 만든다.
- **왜 A가 지금 코드를 안 바꾸는지**: `protocols.py`의 Protocol 선언은 A 소유지만,
  Real 구현체(`RealRecommendationProvider`/`recommendation_pipeline.py`, D 소유)가
  같은 순간에 같이 안 바뀌면 실제 SEEK/AVOID 요청에서 D 코드가 `context.weather`를
  호출하려다 `WeatherCondition` 값 객체에 그 속성이 없어 `AttributeError`로 깨진다.
  D-040은 현재 정상 동작 중이므로, A 혼자 반영해서 이걸 망가뜨리지 않는다 — D가 위
  필터 기준을 확인하면 A/D 양쪽 파일을 같은 타이밍에 반영한다.
- 확인 방법(채택 시): 위 12개 테스트 함수 전부 시그니처 갱신 후 통과 확인, 특히
  `test_recommendation_pipeline.py`의 partial-weather 케이스로 필터 기준 회귀가
  없는지 확인.
- **구현 완료 메모(2026-08-05, 커밋 `baf4051`)**: 위에서 확인을 요청했던 필터
  기준은 D의 `{"success", "partial"}`로 확정됐다 — `to_weather_condition()`
  (`recommendation_transform.py`)의 필터를 `"success"` 단독에서
  `{"success", "partial"}`로 넓히고 반환 타입도 `WeatherCondition | None`로
  바꿔 그대로 재사용했다. `protocols.py`/`real_recommendation_provider.py`/
  `recommendation_pipeline.py`/`stubs.py`와 관련 테스트가 모두 이 커밋에서
  같은 타이밍에 반영됐다.

### D-048 — MODIFY 경로 exclude_tags/special_requirements Add/Remove 정합화 (제안, 범위 확정 필요)

- 상태: `Proposed`(A 제안, 구현 전 범위 확정 필요)
- 배경: B(이태화)가 티켓 댓글로 보고한 `_serialize()` int→str 버그를 고치면서,
  같은 세션에서 `_full_replace_operations()`(RECOMMEND 경로)의
  `exclude_tags`/`special_requirements`가 B의 field_spec.py(Add/Remove만 허용,
  Update 없음 — agent-state-contract-v1.md §2.2)와 안 맞아 `unsupported_operation`
  으로 조용히 드롭되던 버그도 함께 발견해 고쳤다(RECOMMEND는 `Add`로 변경
  완료). 그런데 `_changed_field_operations()`(MODIFY/CHANGE_CONDITION 경로,
  `state_transform.py:207-226`)는 `changed_fields`에 포함된 모든 필드에 동일하게
  `Update`를 쓴다 — `exclude_tags`/`special_requirements`가 MODIFY로 바뀔 때도
  똑같이 `unsupported_operation`으로 드롭된다(1a와 같은 계약 위반 패턴이 MODIFY
  경로에도 남아 있음).
- 왜 RECOMMEND와 같은 방식(무조건 `Add`)으로 못 고치는지: RECOMMEND는
  `reset_scope="soft"`로 baseline이 항상 비어 있어 `Add`-with-full-list가
  replace와 동치이지만, MODIFY는 incremental이라 기존 `exclude_tags`/
  `special_requirements`가 비어있지 않다. LLM이 전체 교체 리스트를 돌려줬을 때
  그대로 `Add`하면 새 값이 append+dedupe되어 기존 제외 목록과 합쳐질 뿐
  교체되지 않는다 — "이제 이것만 제외해줘"처럼 사용자가 치환을 의도했을 때
  의미가 달라진다.
- 제안(설계는 후속 세션): diff 기반으로 (LLM이 준 새 값 − 기존 값)은 `Add`,
  (기존 값 − LLM이 준 새 값)은 `Remove`로 나눠 보내는 방식이 필요해 보인다.
  다만 diff 대상이 되는 "기존 값"을 어디서 가져올지(`session_context.
  user_conditions` 재사용 가능해 보임), LLM이 항상 전체 교체 리스트를 주는지
  아니면 증분만 주는지(조건 추출 프롬프트 계약 재확인 필요)는 이번 세션에서
  답을 내지 않는다.
- 확인 필요: 이 diff 로직을 스코핑할 다음 세션에서 (1) LLM 프롬프트가
  exclude_tags/special_requirements를 항상 전체 리스트로 주는지 증분으로
  주는지, (2) diff 계산에 쓸 "기존 값" 소스가 `session_context.user_conditions`
  로 충분한지 확인한다.
- 변경 대상 파일(후속): `backend/app/services/interpret/state_transform.py`
  (`_changed_field_operations()`), `backend/tests/test_state_transform.py`.
- 왜 지금 구현하지 않는지: diff 알고리즘 설계와 LLM 프롬프트 계약 확인이 먼저
  필요한 별도 스코프의 작업이라, 이번 세션의 계약 위반 수정(1a 유형)과 섞으면
  리뷰 범위가 커진다. 이번 세션은 발견 사실을 기록만 한다.

### D-049 — `conditions.weather`(사용자 발화 5단계 날씨 값) 소비 경로 복구

- 상태: `Implemented` (2026-08-05)
- 배경: D-049 발견 시점에는 `conditions.weather`가 C 요청 페이로드에만 실리고
  실제 Scoring에서는 전혀 읽히지 않아 죽은 필드처럼 보였다.
- 결정:
  - `weather_intent` 의미를 분리한다.
    - `NO_MENTION`: 날씨 언급 없음(기본) → C가 날씨 API 조회
    - `IGNORE`: "날씨 상관없어" 명시 → API 미조회 + 날씨 가중치 제외
    - `AVOID`/`ENJOY`: 날씨 언급 있음 → API 미조회, 사용자 발화 weather를 사용
  - 사용자 발화 weather(5단계: rain/snow/hot/cold/good)의 D 입력 변환/판단은
    C가 담당하고, A는 기존처럼 조건을 C에 전달한다.
- 효과:
  - `conditions.weather`가 실제 1차 Scoring 날씨 Feature에 반영된다.
  - "언급 없음"과 "무관 명시"가 분리되어, 조회 게이팅과 문서/구현 불일치가 해소된다.
  - 날씨 API 호출은 `NO_MENTION` 경로에서만 일어난다.
- 후속 확인:
  - `AVOID/ENJOY` 방향(선호 반영) 자체는 별도 판정 로직 개선 범위로 유지한다.

### D-050 — 혼잡도 2차 Scoring 결과: "순서"는 B에 남지만 "값"은 안 남음 (발견만, 미확정)

- 상태: `Observed`(발견만 기록, 코드 미변경 — 필요 여부 확인 필요)
- 배경: `concentration-e2e-verification.md` 작성 중 "혼잡도까지 포함한 답변 내용이
  저장되는지" 질문에 답하려고 `agent_runtime.py`의 노출 기록 경로(7단계)를
  재확인했다.
- 발견: `recommendations` 변수가 6-1단계(`_apply_concentration_rerank()`)에서
  재순위 결과로 재할당된 뒤 7단계 `record_recommendation()`에 그대로 쓰이므로,
  B에 기록되는 노출 이력의 **순서(rank)는 혼잡도 반영 이후 최종 순서**가 맞다.
  다만 `RecommendedPlace`(`app/state/service.py:102-106`)는 `place_id`/`rank`
  두 필드만 가진 스키마라, 혼잡도 점수(`concentration_rate`)·등급(`혼잡`/`보통`/
  `한산`)·`feature_scores`/`weights_used` 같은 세부 근거 값은 그 턴의 HTTP 응답
  에만 존재하고 B에는 전혀 남지 않는다. 나중에 "그때 왜 이 순서였는지"를 다시
  보고 싶어도 저장된 건 순서뿐이다.
- 참고: 이건 혼잡도에만 국한된 문제가 아니라 날씨·운영시간·거리 등 1차 Scoring의
  다른 Feature 값도 마찬가지로 저장 안 된다 — `RecommendedPlace`가 원래부터
  순서만 기록하는 설계였다. 혼잡도 검증 과정에서 다시 확인된 것일 뿐 새로 생긴
  문제는 아니다.
- 이번 세션에서 결정하지 않는 것: 이 세부 근거 값들을 B에 남길 필요가 있는지
  (예: 추후 분석/디버깅/사용자에게 "왜 이 순서인지" 재설명 등의 용도) — 필요성
  자체가 불확실해 원 설계 의도·B팀 확인 없이 스키마를 확장하지 않는다.
- 확인 필요: `RecommendedPlace`에 세부 Feature 값을 남길 실익이 있는지, 있다면
  B의 저장 계약(`agent-state-contract-v1.md`) 확장이 필요한지 결정.

### D-051 — 날씨는 C가 사실을, D가 판정을 맡는다

- 상태: 사실 전달과 `NO_MENTION` 수용은 `Implemented`(2026-08-05, PR #97),
  판정 이관(사실 기반 + 발화 우선)은 `Implemented`(2026-08-05, D). 2차 Scoring
  배선 정리(A)와 `WeatherForecast.condition` 필드 제거(C)도 `Implemented`
  (2026-08-06). 도메인 모델 `WeatherForecastSlot.condition`과 그 연쇄
  (`SelectedWeatherForecast.condition`, `map_sky_pty_to_condition()`,
  `tool_intelligence` 계약, `fake_weather_condition` 설정)도 `Implemented`
  (2026-08-06, C가 D 소유 파일 1줄을 포함해 처리). 사실/판정 분리는 이로써
  완결됐다 — 남은 항목(`conditions.environment`, `tool_intelligence`의 벤더 코드
  노출)은 아래 "남은 것" 참고
- 배경: D-049(`conditions.weather` 미사용)를 확인하다가 날씨 처리 전반을 훑었고,
  서로 얽힌 문제 다섯 개가 나왔다. 하나씩 고치면 다른 하나가 어긋나는 구조라 함께
  정리한다.

#### 확인된 문제

1. **`ENJOY`일 때 결과가 반대로 나온다.** `weather_intent`를 읽는 곳은
   `tool_rules.py:47` 한 줄뿐이고 조회 여부만 정한다. `AVOID`와 `ENJOY`가 완전히
   동일하게 처리되어, "비 오는 날 산책하고 싶어"에 실내가 만점(BAD+indoor=1.00),
   야외가 최하점(BAD+outdoor=0.30)이 된다.
2. **기온이 판정에 없다.** `T1H`가 파싱 필터에서 제외돼 계약의
   `temperature_celsius`가 항상 `null`이었다. 폭염 35도에 SKY=맑음이면 `GOOD`이 되어
   야외를 우대한다.
3. **비와 눈이 구분되지 않는다.** PTY는 값과 무관하게 전부 `BAD`로 뭉개진다.
4. **`IGNORE`가 두 의미를 겸한다.** "언급 없음"과 "무관하다고 명시함"의 동작이
   반대인데 값이 하나다. `int-01-recommend.md §10`과 구현이 서로 반대다(D-038 TODO 1).
5. **`conditions.environment`도 소비처가 없다.** `app/` 전수 확인 결과 `stub.py`뿐이다.
   D-049는 이 필드를 "indoor/outdoor 하드 필터"로 서술했지만 **실제로 필터하는 코드는
   없다** — Scoring의 하드 필터는 영업시간뿐이다. 해당 서술은 정정이 필요하다.

#### 결정 — 역할 분리

```
C   기상청 조회 → 벤더 코드를 도메인 용어로 번역 → 사실만 전달
D   사실 + weather_intent → good/neutral/bad 판정 → 점수 계산
```

판정에는 사용자 의도가 필요하고, 그 값을 가진 쪽은 D다
(`RecommendationProvider.recommend(conditions, context, excluded_place_ids)`).
**A→D 계약 변경 없이** 의도를 쓸 수 있다.

C가 판정을 맡으면 `WeatherCondition`의 의미가 "날씨 상태"에서 "이 사용자에게 좋은가"로
바뀌어, `explanation.py:94`가 비 오는 날 "좋은 날씨에 적합한 야외 장소예요"라고 말하게
된다. 사실과 판단을 나누면 그 문제가 생기지 않는다.

기상청 코드(PTY/SKY)는 그대로 넘기지 않는다. 코드 체계가 D까지 새면 기상 API를 바꿀 때
D도 함께 고쳐야 한다. 번역만 C가 하고 판정은 하지 않는다.

#### 완료된 것 (`Implemented`, PR #97, C)

- 계약에 사실 3종 추가: `precipitation`(none/rain/snow/sleet/shower),
  `sky`(clear/cloudy/overcast), `temperature_celsius`
- `T1H` 파싱을 Provider부터 계약까지 연결 (문제 2)
- PTY 코드 번역으로 비·눈 구분 가능 (문제 3)
- `NO_MENTION` 수용과 게이팅 명시화 (문제 4의 C 몫)

#### 완료된 것 (`Implemented`, 2026-08-05, D)

- `app/domain/weather_judgment.py` 신설: `judge_weather_condition_from_facts()`
  (사실 기반, 강수 > 기온 > 하늘 순 판정 + 원인 태깅), `judge_weather_condition_from_stated()`
  (발화 5단계 기반), 공용 `_apply_intent()`(ENJOY는 원인이 강수(비/눈)인 BAD만
  GOOD으로 뒤집음 — 문제 1 해소, 기온이 원인인 BAD는 안 뒤집음).
- PR #102(C)가 `run_recommendation_pipeline_from_context()`에 연 `conditions`
  파라미터를 받아, `resolve_weather_condition()`(구 `_weather_condition_from_context()`,
  public으로 전환)이 `context.weather`(사실)를 우선 쓰고 없으면 `conditions.weather`
  (발화값)로 대체하도록 배선(`recommendation_pipeline.py`). AVOID/ENJOY에서 발화
  날씨가 조용히 빠지던 문제(PR #102가 부분 해결)가 완전히 해소된다.
- `weather_ignored` 판별도 함께 정리: `conditions`가 있으면 `IGNORE`만 "제외했어요"
  문구를 쓰고, 그 외 날씨 결측은 전부 "확인 못 했어요"로.
- `resolve_weather_condition(context, conditions)`을 **public 함수로 노출**했다 —
  1차 Scoring 내부에서만 쓰던 걸 2차 Scoring 호출자(A, `agent_runtime.py`)도 같은
  판정을 재사용할 수 있게 하려는 목적이다(아래 "남은 것" 참고). Fixture 검증
  테스트(`test_recommendation_context_fixture_quality.py`)도 이 함수를 직접
  import해서 쓰도록 정리해, 판정 로직 중복을 없앴다.
- 테스트: `tests/test_weather_judgment.py`(판정 함수 전수), `tests/test_recommendation_pipeline.py`
  (사실/발화/ENJOY 반전/IGNORE-vs-실패 케이스), Context Fixture 4종에 `precipitation`/
  `sky` 보강.
- **근거 문장이 원인(비/눈/폭염/한파)까지 정확히 말하게 수정**(2026-08-05 검수에서
  발견). `explanation.py`가 `weather_condition`만 보고 라벨을 골랐는데, 이러면
  ENJOY 반전으로 비인데 GOOD이 된 경우 "맑은 날씨"라고 말하고, 폭염/한파로 BAD가
  된 경우도 전부 "비 예보"라고 말하는 사실-근거 불일치가 있었다(온도가 판정에
  없던 시절엔 BAD=강수뿐이라 문제없었지만, 이번에 온도를 판정에 추가하면서
  새로 생겼다). 판정 함수가 `(WeatherCondition, WeatherReason)` 튜플을 반환하도록
  바꾸고(`WeatherReason` = rain/snow/heat/cold — 강수는 sleet/shower를 rain으로
  뭉치고 snow만 구분), `scoring.py::RankedCandidate`/`evidence.py::RecommendationEvidence`에
  `weather_reason`을 실어 `explanation.py`까지 관통시켰다. reason이 있으면 그걸로
  라벨을 고르고(비/눈/폭염/한파 예보), 없으면(맑음/흐림, 발화 GOOD) 기존
  `weather_condition` 기반 라벨로 폴백한다.
- **기온 판정을 주의보/경보 2단계로 재설계**(2026-08-05). 원래는 폭염주의보/
  한파주의보 기준(33°C/-12°C) 하나만 넘으면 BAD, 아니면 곧바로 하늘 상태로
  판정했다 — 그 사이 완충 NEUTRAL 구간을 안 뒀는데, 처음엔 28°C/0°C 같은 근거
  없는 값을 새로 만들지 않으려는 선택이었다. 재검수하면서 기상청 실제 특보
  단계(폭염주의보 33°C/경보 35°C, 한파주의보 -12°C/경보 -15°C)를 확인했고, 이
  경계를 그대로 가져와 3단계로 나눴다: 주의보 미만은 기존처럼 하늘 상태로 판정,
  주의보 이상~경보 미만은 NEUTRAL, 경보 이상만 BAD. 두 경계 모두 임의값이
  아니라 공식 등급이라 "근거 없는 완충"이라는 원래 문제를 재현하지 않는다.
  다만 이 변경은 부분적이다 — 30~32°C처럼 주의보 미만인 "그냥 더운 날"은
  여전히 하늘 상태로만 판정되어 맑으면 GOOD이다. 그 구간까지 다루려면 다시
  근거 없는 임의값이 필요해지므로 의도적으로 남겨뒀다(날씨 Feature 가중치가
  40%로 가장 크고 GOOD/BAD 낙차가 outdoor 기준 0.7점이라, 이 트레이드오프가
  순위에 미치는 영향은 결코 작지 않다는 걸 인지하고 있다).

#### 해결됨 (2026-08-06)

- **2차 Scoring(`rerank_with_concentration()`) 배선 불일치** — A가 정리했다(커밋
  `dc0de63`). `rerank_with_concentration()`이 사전 계산된 `weather_condition` 대신
  `context`를 받고, `RealRecommendationProvider`가 내부에서 `resolve_weather_condition()`을
  호출해 `weather_condition`/`weather_reason`을 함께 넘긴다. 구버전
  `to_weather_condition()`은 삭제됐다.
- **`WeatherForecast.condition` 필드 제거** — C가 정리했다. 제거 전에 소비처를 전수
  확인한 결과, 값을 읽어 판정에 쓰는 곳은 A의 `to_weather_condition()`이 마지막이었고
  위 커밋으로 사라졌다. 나머지는 전부 쓰기 전용(mapper가 채우고 아무도 안 읽음)이었다.
  `StrictModel(extra="forbid")`이라 A의 `services/runtime/stubs.py`가 넘기던
  `condition="neutral"` 한 줄을 함께 지웠고, Context Fixture 7종의 `condition` 키도
  제거했다(안 지우면 파싱 자체가 거부된다).
  - 함께 정리: `WeatherProvider.get_current_condition()` — 앱 코드에서 아무도 호출하지
    않고 테스트에서만 쓰이던 죽은 경로였다(protocol/Real/Fake 3곳 제거).
  - 함께 정리: `has_usable_sky_or_precipitation()` 분리. slot 폐기 규칙이 원래
    `map_sky_pty_to_condition()`이 던지는 예외로만 표현돼 있어서, 판정을 걷어낼 때
    필터까지 같이 사라질 자리였다. 규칙을 별도 함수로 빼고 테스트로 못 박았다.

#### 남은 것 (`Proposed`)

- **`conditions.environment` 처리 방침** (문제 5) — 살릴지 제거할지 미정
- **`tool_intelligence` 계약의 `precipitation_type`** — 기상청 코드가 원문으로 노출돼
  있다. 이번 범위에 넣지 않았다. 이 디렉터리는 `package_work_breakdown.md`의 A/B/C/D
  어느 목록에도 없어 소유자 확인이 선행돼야 한다.

### D-052 — Gemini 동일 벤더 내 모델 fallback (`LLM_FALLBACK_MODEL_NAMES`)

- 상태: `Accepted`, 구현 완료
- 배경: 멀티턴 대화("비오는날 실내 추천해줘" → "더 추천해줘" → "반경 넓혀서
  추천해줘") 3번째 턴에서 502(`Gemini 연동에 문제가 발생했어요`)가 발생했는데,
  서버 로그가 한 줄도 없었다. 조사 결과 원인은 두 가지였다.
  1. `RealGeminiProvider._generate()`부터 `handle_app_error`(`main.py`)까지
     `AppError`가 전파되는 경로 전체에 `logger.error`/`warning` 호출이 전혀
     없었다 — Gemini 전용 문제가 아니라 어떤 Provider든 502가 나면 조용히
     사라졌다.
  2. Gemini 5xx/타임아웃 재시도는 이미 있었지만(지수 백오프, `max_retries`회)
     항상 같은 모델만 재시도했다. 턴마다 Gemini를 2회씩 호출하므로(Intent 분류 +
     조건 추출) 턴이 누적될수록 일시적 5xx를 만날 누적 확률이 올라가고, 재시도
     예산이 소진될 가능성도 커진다 — "3번째 턴에서 유독 실패"는 프롬프트가
     커져서가 아니라(확인함 — `current_conditions`는 매턴 새로 덮어써서 누적
     안 됨) 호출 횟수 누적 문제로 보인다.
- 결정: `RealGeminiProvider`가 `LLM_MODEL_NAME`(1순위) 재시도 소진 후
  `LLM_FALLBACK_MODEL_NAMES`(쉼표 구분, 선택 사항)에 지정된 모델을 순서대로
  시도한다. 각 모델의 재시도 예산(`EXTERNAL_API_RETRY_COUNT`)은 폴백 목록
  길이와 무관하게 기존과 동일하게 유지한다 — 두 설정을 서로 독립적으로
  유지하기 위함이며, 대신 모델 수 × 재시도 수만큼 최악 지연이 늘어나는 건
  감수한다. 4xx 등 비재시도 오류는 모델을 바꿔도 결과가 같으므로 폴백하지
  않는다(`_try_model()`에서 즉시 raise, `_RetryableExhaustedError`로 감싸지
  않음). 폴백 전환 시 `logger.warning`, 전 모델 소진 시 `logger.error`로 어떤
  모델이 실패했고 어떤 모델로 넘어갔는지, 최종적으로 어떤 모델이 응답했는지
  남긴다. 추가로 `main.py`의 `handle_app_error`(모든 `AppError`가 거쳐가는
  단일 지점)에도 `logger.error` 한 줄을 추가해, Gemini 외 다른 Provider의 502도
  같이 로그에 남도록 했다.
- D-042와의 관계: D-042(Real Provider 실패 시 Fake로 자동 전환하지 않는다)는
  Real→Fake 조용한 전환만 범위로 하며, "같은 성격의 다른 Real 경로로 넘어가는
  것"과 재시도/서킷 브레이커는 명시적으로 범위 밖이라고 밝히고 있다. 같은
  벤더(Gemini)의 다른 모델로 넘어가는 이번 결정은 D-042 위반이 아니다. 다만
  D-042의 취지("조용한 폴백 금지")를 지키기 위해 폴백 발생을 반드시 로그로
  남긴다 — 로그 없는 폴백은 D-042가 경계하는 것과 본질적으로 같은 문제다.
- 기본값: `LLM_FALLBACK_MODEL_NAMES`가 비어 있으면(기본값) 기존과 동일하게
  단일 모델만 사용한다. 기존 `.env`는 변경 없이 그대로 동작한다.
- 구현: `app/config.py`(`llm_fallback_model_names`, `resolved_llm_models`),
  `app/providers/gemini.py`(`RealGeminiProvider._generate()`을 모델 목록 순회
  + `_try_model()`로 분리, `_RetryableExhaustedError` 내부 신호로 재시도-소진과
  비재시도-오류를 구분), `app/providers/factory.py`(배선 + 부팅 시 중복 모델명
  거부), `app/main.py`(`handle_app_error` 로깅). 테스트는
  `tests/test_gemini_provider.py`(폴백 성공/전체 소진/4xx 무폴백/로깅 확인)와
  `tests/test_provider_settings.py`(`resolved_llm_models` 파싱, 중복 모델명
  거부)에 추가했다.
- 범위 밖(후속 검토 필요): 서로 다른 LLM 공급사(Anthropic/OpenAI 등) 간
  fallback, timeout 정책 자체의 재검토, 모델별 비용/품질 차이를 고려한 라우팅.
  이 결정은 "같은 Gemini 계정 내 모델 fallback"으로 범위를 좁힌다.
- 확인 방법: `tests/test_gemini_provider.py`(신규 5건: 1순위 성공/폴백 성공/전
  모델 소진/4xx 무폴백/로깅), `tests/test_provider_settings.py`(신규 5건).
  전체 회귀(1020+ passed) 확인.

### D-053 — 위치 변경 판정에서 지명 단독은 제외하고, `environment` 미언급은 조건을 해제하지 않는다

- 상태: `Accepted`, 구현 완료
- 배경: TP-67(PR #113)에서 "이전 추천 뒤 위치만 바꾸는 발화가 RECOMMEND로 분류되어
  soft reset으로 앞 턴 조건이 사라지는" 문제를 프롬프트·Fake·되묻기 경로에서 고쳤다.
  그 결과를 재검증하면서 두 가지가 남아 있는 걸 확인했다.
  1. `environment`가 여전히 소실된다. 되묻기 답변 경로는 `reset_scope=None`이라 조건이
     유지되지만, LLM이 미언급을 `ANY`로 표현해 보내면 `_full_replace_operations()`가
     `Update`를 만들어 앞 턴의 `indoor`(비를 피하려던 조건)를 덮어쓴다. TP-67에서
     `weather_intent=NO_MENTION`·`concentration_intent=IGNORE`는 막았지만
     `environment`는 목록에 없었다 — 원 증상(`environment: indoor → any`)의 절반이
     남아 있던 셈이다.
  2. 지명 단독(`"경복궁"`)까지 위치 변경 MODIFY로 분류됐다. 추천을 받은 뒤 지명 하나로
     그 장소를 묻는 흐름(경계 사례 `"경복궁" (단독) → INFO`)이 위치 변경에 가려진다.
- 결정 1 (지명 단독은 INFO): 위치 변경 판정은 **지명에 근처/주변 또는 조사·어미가 붙은
  발화**("광화문 근처에서", "광화문 근처", "광화문으로")로 한정한다. 지명 단독은 이전
  추천 이력이 있어도 INFO로 둔다 — 위치를 바꾸려는 사용자는 보통 조사나 근처 표현을
  함께 쓰고, 지명만 던지는 건 그 장소를 지목한 질문으로 보는 쪽이 실제 발화에 가깝다.
  `"경복궁 오늘 열어?"`처럼 질문 형태는 원래부터 INFO라 영향이 없다.
- 결정 2 (`environment` 미언급): 추출 프롬프트에 "언급이 없으면 null, `any`는 무관함을
  명시했을 때만"이라는 규칙을 명시하고(근본), 되묻기 답변 경로의
  `_CLARIFICATION_DEFAULT_FIELDS`에 `environment: ANY`를 추가한다(안전망). `ANY`를
  미언급으로 보는 게 안전한 이유는 되묻기 답변에 실내외 무관 선언이 함께 오는 경우가
  드물기 때문이고, 실내/실외를 명시한 값(`indoor`/`outdoor`)은 그대로 적용된다.
- 한계: `WeatherIntent`에는 `NO_MENTION`과 `IGNORE`가 나뉘어 있지만 `Environment`에는
  `ANY` 하나뿐이라 "언급 안 함"과 "실내외 상관없음"을 타입으로 구분할 수 없다. 그래서
  구분을 프롬프트 규칙에 의존하고, 되묻기 경로에서는 미언급 쪽으로 해석한다. 스키마에
  `NO_MENTION` 상당 값을 추가하는 건 A-C/B 계약이 함께 움직여야 해 별건으로 남긴다.
- 범위 밖: 일반 RECOMMEND의 soft reset은 그대로 둔다(conditions-schema.md §6의 의도된
  설계). 프롬프트를 고쳐도 LLM 분류는 확률적이라 간헐적으로 RECOMMEND로 오분류되면 그
  턴의 조건은 여전히 소실되는데, 상태 레벨 방어(RECOMMEND의 soft reset 예외 확대)는
  `_full_replace_operations()`의 "Add == replace" 등가성 가정을 깨뜨려
  `exclude_tags`/`special_requirements` 처리까지 함께 손봐야 하므로 채택하지 않았다.
  실서버 반복 측정에서 오분류가 잔존하면 그때 별건으로 다룬다.
- 구현: `app/providers/gemini_prompts.py`(맥락 의존 규칙에서 지명 단독 제외 + 경계
  사례 원복 + `environment` 추출 규칙 추가, `PROMPT_VERSION` 1.0.2),
  `app/providers/stub.py`(`_is_location_only_change()`가 지명 단독을 False로),
  `app/services/interpret/state_transform.py`(`_CLARIFICATION_DEFAULT_FIELDS`).
- 확인 방법: `tests/test_llm_provider.py`(지명 단독이 이력 유무 무관하게 INFO),
  `tests/test_state_transform.py`(되묻기 답변의 `ANY`는 Operation 미생성, 명시된
  `outdoor`는 적용), `tests/test_agent_runtime.py`(되묻기 E2E에서 `weather`·
  `environment` 유지). 전체 회귀 1137 passed / 22 skipped, ruff 통과.
- 실 Gemini 측정(2026-08-06, `gemini-2.5-flash`, 프롬프트 1.0.2,
  `has_previous_recommendation=True`/`shown_place_count=5`): TP-67 리포트의 발화 11종을
  3회씩 재측정해 전부 의도한 값으로 고정됐다. 오분류 4종("광화문 근처에서", "광화문 근처",
  "광화문 근처 어때?", "종로3가역 근처에서")이 모두 `MODIFY` 3/3, 지명 단독("광화문")은
  `INFO` 3/3, 원래 정상이던 6종도 `MODIFY` 3/3을 유지했다. 리포트에서 5회 중 1회만
  `MODIFY`였던 "북촌 근처에서"는 5회 반복에서 `MODIFY` 5/5로 흔들림이 사라졌다. 반대
  방향 회귀도 확인했다 — 이력 없음 + "광화문 근처에서"는 `RECOMMEND` 5/5, "경복궁 오늘
  열어?"는 `INFO` 5/5, "다른 곳 보여줘"는 `MODIFY` 5/5.
- Fake와 실 Gemini의 판정 정렬(2026-08-06 해소): "경복궁 근처 카페 추천해줘"는 이전
  추천이 있을 때 실 Gemini가 `MODIFY` 5/5로 분류한다(위치·카테고리를 바꾸는 조건 변경으로
  보는 쪽이 맞다고 확인함). `FakeLLMProvider`는 `_is_location_only_change()`가 "카페"라는
  잔여 조건 때문에 False가 되어 fallback인 `RECOMMEND`로 떨어졌는데, 이 차이를 없앴다 —
  Fake는 회귀 테스트가 실제 설계를 반영해야 하는 물건이라(stub.py docstring, PR #113 방침)
  갈리는 지점을 남겨둘 이유가 없다.
  - `_is_location_scoped_change()`를 새로 두고 `_is_location_only_change()`는 그대로
    뒀다. 전자는 "지명 바로 뒤에 근처/주변이 붙는가"만 보므로 잔여 조건이 있어도 통과하고,
    지명 단독은 뒤에 근처/주변이 없어 자연히 빠져 결정 1(지명 단독은 `INFO`)이 유지된다.
    정보/일반 질문 어휘(`_INFO_QUESTION_MARKERS`/`_GENERAL_MARKERS`)가 섞이면 빠지게 해
    "경복궁 근처에 화장실 있어?"가 `INFO`, "경복궁 근처 동네는 어때?"가 `GENERAL`로 남는다.
    두 함수를 합치지 않은 건 잔여 조건 화이트리스트(`_LOCATION_ONLY_REMAINDERS`)가
    "광화문으로"·"광화문에서"처럼 근처/주변 없이 조사만 붙는 발화를 계속 맡아야 해서다.
  - `extract_modify_conditions()`에 날씨 처리를 추가했다 — "비"면
    `weather=RAIN`/`weather_intent=AVOID`/`environment=INDOOR`를 `changed_fields`와 함께
    채운다. `extract_recommend_conditions()`의 기존 비 처리와 같은 결이다. `search_center`
    갱신 조건에도 새 판정을 함께 물렸다.
  - 이 차이에 기대던 `test_agent_runtime.py`의
    `test_agent_context_request_weather_not_mixed_with_provider_weather`는 2턴째가 이제
    `MODIFY` 경로를 타므로 그 전제를 명시(`intent == "MODIFY"` 단언)하도록 고치고,
    원래 검증하려던 RECOMMEND 경로는 이력 없는 단일 턴으로 같은 발화를 태우는
    `test_agent_recommend_path_weather_not_mixed_with_provider_weather`로 분리해 남겼다.
  - 확인 방법: `tests/test_llm_provider.py`(지명+근처+다른 조건이 이력 있으면 `MODIFY`,
    이력 없으면 `RECOMMEND`, 정보/일반 질문은 안 가려짐, MODIFY 경로 비 처리),
    `tests/test_agent_runtime.py`(위 두 E2E). 전체 회귀 1145 passed / 22 skipped,
    ruff 통과.

### D-054 — INFO 상세 질의는 Supabase 상세 캐시가 아니라 TourAPI를 직접 조회한다

- 상태: `Accepted`, C 구현 완료 (A 배선 대기)
- 배경: INT-02(INFO)의 `question_type` 8종 중 실제로 동작하는 건 `concentration`
  하나뿐이었다. 나머지를 채우려고 상세 데이터 출처를 확인하다가, 운영 설정
  (`PLACE_DETAILS_SOURCE=supabase`)에서는 답할 데이터가 아예 없다는 걸 발견했다 —
  `SupabasePlaceDetailsProvider._to_place_details()`가 `overview`/`homepage`/
  `telephone`을 `None`으로, `raw_common`/`raw_intro`를 빈 dict로 채운다. places
  테이블의 동기화 대상이 `operating_hours_raw`/`rest_date_raw`뿐이기 때문이다.
- 결정: INFO 상세 질의는 `PLACE_DETAILS_SOURCE`와 무관하게 `PlaceProvider`(TourAPI)를
  직접 호출한다. 전용 Tool(`tools/place_detail.py::GetPlaceDetailTool`)을 두고
  Factory가 `get_place_provider()`를 주입한다.
- 근거: Supabase 캐시를 도입한 이유는 추천 후보 N건의 상세조회를 없애는 것이었다
  (18.0초 → 0.33초). INFO는 대상이 장소 1건이라 그 근거가 적용되지 않는다. 반대로
  캐시를 쓰면 `fee`/`parking`/`facility`/`general_info` 4종이 **조용히** 빈 응답으로
  떨어진다 — 오류가 아니라 "정보 없음"으로 보이므로 발견도 늦다. 이 저장소가 D-042를
  만든 사건과 같은 성격이다.
- 예외 하나: `location_info`는 `ResolveLocationTool`이 주소를 이미 들고 나오므로
  상세 조회를 아예 하지 않는다.
- `operating_hours`는 지금도 이 경로를 함께 탄다. 값 자체는 provider가 유형별 키를
  정규화해둔 `PlaceDetails.operating_hours`/`rest_date`에서 읽으므로 Supabase 캐시로도
  답할 수 있지만, 그러려면 한 Tool이 질문 유형에 따라 두 출처를 오가야 한다. 호출
  1회를 아끼려고 경로를 둘로 만드는 것보다 단순한 쪽을 택했다 — 필요해지면
  `operating_hours`만 캐시로 빼는 건 나중에도 가능하다.
- 범위 밖: `event`는 같은 날 D-055로 별도 처리했다(최초에는 `unsupported`로 두었다).
  동기화 파이프라인에 `overview`/요금/주차 컬럼을 추가하는 근본 해결은 DB 마이그레이션
  + 847건 재동기화가 필요하고 TourAPI 일일 한도에 묶여 있어 별건으로 남긴다.
- 조용한 fake 방지: `FakePlaceProvider.get_details()`의 `raw_intro`가 빈 dict라
  필드 추출 로직이 한 줄도 안 돈 채 테스트가 통과하는 상태였다. 실 detailIntro2와
  같은 키 이름(`usefee`/`parkingculture`/`parkingfood` …)으로 채우고,
  `tests/agent_context/test_info_field_rules.py::TestFakeProviderCarriesIntro`가
  Fake로도 추출 결과가 비지 않는지 고정한다.
- 구현: `app/agent_context/info_schemas.py`(`question_type` 8종 확장,
  `specific_question` 추가, `result`를 `ConcentrationInfoResult | PlaceInfoResult`
  union으로), `app/agent_context/info_field_rules.py`(신설),
  `app/tools/place_detail.py`(신설), `app/agent_context/service.py`
  (`fetch_info_context()` 분기 + 기존 집중률 흐름을 `_fetch_concentration_info()`로
  추출, 로직 변경 없음), `app/agent_context/factory.py`, `app/providers/stub.py`.
- 확인 방법: `tests/agent_context/test_info_field_rules.py`(23건),
  `tests/agent_context/test_info_place_detail.py`(15건). 전체 회귀 1231 passed /
  22 skipped, ruff 통과.
- A 배선 필요: `to_info_context_request()`가 `question_type`을 넘기지 않고,
  `agent_runtime.py`의 게이트가 `CONCENTRATION`으로 한정돼 있으며,
  `response_composer`에 상세 응답 렌더 경로가 없다. 셋 다 A 소유라 별도 카드로 넘긴다.

### D-055 — 트리비 페르소나와 추천 결과 요약 LLM 추가

- 상태: Implemented
- 배경: `RECOMMEND`/`MODIFY` 성공 응답의 `AgentResponse.message`가 고정 문구
  `"이런 곳들을 찾아봤어요:"`만 반환해 국내 여행 챗봇의 정체성과 대화감이 약했다.
  또한 "넌 누구야?", "이름이 뭐야?", "뭘 할 수 있어?" 같은 서비스 정체성 질문을
  안정적으로 처리할 별도 topic이 없었다.
- 결정:
  - 챗봇 이름은 **트리비**로 정한다.
  - 서비스 정체성 질문은 `build_interpretation()`에서 LLM 1차 분류 전에
    `GENERAL(service_identity)`로 선처리하고, 트리비가
    TripBranch의 국내 여행 챗봇이며 위치·날씨·운영시간·거리·혼잡도 선호를 함께
    고려해 장소 추천을 돕는다고 답한다.
  - `RECOMMEND`/`MODIFY` 성공 경로에는 `generate_recommendation_summary()` LLM 호출을
    추가해 추천 카드들을 감싸는 1~2문장 요약을 만든다.
- 안전장치:
  - 추천 요약 LLM 입력은 `name`, `category`, `distance_km`, `remaining_minutes`,
    `recommendation_reason`, `explanations`로 제한한다.
  - `warnings`, `score`, `feature_scores`, `weights_used`는 넘기지 않는다. 사용자에게
    "날씨 점수 제외", "가중치 재분배", "API 실패" 같은 내부 계산 사정을 말하지 않게
    하기 위한 경계다.
  - 요약 LLM 호출이 실패하면 추천 카드 응답은 유지하고 기존 고정 wrapper로 fallback한다.
  - "넌 누구야?", "이름이 뭐야?"는 Gemini가 `role_request`/`OUT_OF_SCOPE`로 오분류할
    수 있으므로 프롬프트만 믿지 않고 A 오케스트레이터에서 deterministic하게 처리한다.
- 구현: `app/schemas.py`(`GeneralTopic.SERVICE_IDENTITY`),
  `app/providers/gemini_prompts.py`(트리비 페르소나, service_identity 분류/답변 규칙,
  추천 요약 프롬프트, `PROMPT_VERSION` 1.0.5), `app/providers/gemini.py`/
  `app/providers/stub.py`(`generate_recommendation_summary()`),
  `app/services/interpret/orchestrator.py`(service_identity 선처리),
  `response_composer.py`(추천 성공 메시지에서 요약 LLM 호출).
- 확인 방법: `tests/test_llm_provider.py`(정체성 질문 분류/답변), `tests/test_response_
  composer.py`(추천 요약 호출 및 fallback), `tests/test_gemini_provider.py`(요약 입력에서
  내부 scoring 필드 제외).

### D-055 — INFO `event`는 지역 행사 목록 + 좌표 근접으로 답한다

- 상태: `Accepted`, C 구현 완료 (A 배선 대기)
- 배경: D-054에서 `event`만 `unsupported`로 남겼다가, 실제로 답할 데이터가 있는지
  실측했다(2026-08-07). 처음 조회에서 "종로구에 진행 중인 행사 0건"이 나와 미지원이
  타당해 보였는데, 필터를 바꾸자 결론이 뒤집혔다.
- **발견 1 — `sigunguCode` 필터가 함정이다.** `searchFestival2` 응답 항목의 상당수가
  `areacode`/`sigungucode`를 빈 문자열로 내려주고, 그 항목들은 서버 필터에서 통째로
  탈락한다. 같은 종로구를 두 방식으로 조회한 결과:

  | 필터 | 결과 | 오늘 진행 중 |
  | --- | --- | --- |
  | `sigunguCode=23` | 14건 (전부 2025년) | **0건** |
  | `lDongRegnCd=11` + `lDongSignguCd=110` | 25건 | **6건** |

  장소 검색이 이미 같은 이유로 법정동 코드를 쓰고 있었다
  (`place_search_policy.PLACE_SEARCH_LDONG_*`, 2026-08-03). 같은 함정을 두 번째로
  밟은 셈이라 provider 모듈 docstring에 근거를 남겼다.
- **발견 2 — 장소명 매칭은 안 붙지만 좌표는 100% 있다.** `eventplace`는
  `"광화문광장&세종로공원"`, `"청와대 사랑채 1층"`, `"서울 전역"`처럼 우리 `places.title`과
  형태가 다르다. 2026-08-07 진행 중 6건 중 이름이 붙는 건 0건이었다. 반면 목록 응답의
  `mapx`/`mapy`는 전 항목에 채워져 있다.
- 결정: `event`를 **"그 장소에서 열리는 행사"가 아니라 "그 장소 근처에서 진행 중인 행사"**로
  답한다. 종로구(법정동 110) 행사를 받아 기준일에 진행 중인 것만 남기고, 대상 장소
  좌표에서 가까운 순으로 정렬해 최대 5건을 싣는다. 제목에 장소명이 들어간 행사
  (`"경복궁 별빛야행"`)만 `is_direct_match=True`로 올려 먼저 보여준다.
- INFO 명세는 바꾸지 않는다: `place_name`이 여전히 앵커이고, 답변만 근접 의미로 나간다.
  이건 집중률의 D-036 인근 대체 조회와 같은 패턴이다 — 직접 데이터가 없으면 인근
  기준으로 답하되 반드시 고지한다. `EventItem.is_direct_match`/`distance_km`이
  `ConcentrationInfoResult.is_proxy`에 대응한다. **A는 이 구분을 문구에 반드시 반영해야
  한다** — 요청한 장소의 행사인 것처럼 말하면 사실과 다르다.
- 반경 컷오프를 두지 않은 이유: 조회가 이미 종로구로 한정돼 지역 필터가 반경 역할을
  한다. 여기서 임의 반경을 하나 더 두면 근거 없는 숫자가 늘어난다. 대신 건수 상한
  (`INFO_EVENT_RESULT_LIMIT = 5`)만 둔다.
- `eventplace`를 안 쓴 이유: 목록 응답에 없고 행사마다 `detailIntro2`를 열어야 한다(N+1).
  제목 매칭으로 직접/근접을 가르는 선에서 멈췄다. 정확도가 문제가 되면 상위 몇 건만
  상세를 여는 방식으로 넓힐 수 있다.
- 계약: `EventInfoResult`/`EventItem`을 신설하고 `InfoContextResponse.result` union에
  추가했다. 행사가 다건이라 `PlaceInfoResult.fields`(`dict[str, str]`)로는 표현할 수 없다.
- 조용한 fake 방지: `FakeFestivalProvider`의 좌표·기간·명칭을 실측 응답에서 가져왔고,
  진행 중/종료/예정과 직접 매칭/근접이 모두 섞이도록 구성했다. Fake가 전부 직접
  매칭이면 근접 경로가 한 번도 실행되지 않는다
  (`tests/agent_context/test_info_event.py::TestFakeProviderShape`).
- 구현: `app/providers/festival.py`(신설), `app/providers/protocols.py`(`FestivalProvider`),
  `app/providers/contracts.py`(`TOUR_API_FESTIVAL`/`FAKE_FESTIVAL`),
  `app/tools/festival.py`(신설), `app/agent_context/info_schemas.py`,
  `app/agent_context/service.py`(`_fetch_event_info()`), 양쪽 factory.
- 확인 방법: `tests/test_festival_provider.py`(12건), `tests/agent_context/test_info_event.py`
  (14건). 전체 회귀 1276 passed / 22 skipped, ruff 통과. 실 TourAPI 실측으로 경복궁
  기준 5건(0.21~0.86km), 북촌한옥마을 기준 5건(0.69~1.23km)이 거리순으로 나오는 것을
  확인했다.
- 한계: 오늘 기준 진행 중 6건 전부가 근접 매칭이다(직접 매칭 0건). 사용자에게는 "경복궁
  근처에서 진행 중인 행사"로 나가므로 질문이 "경복궁에서 뭐 해?"였다면 기대와 다를 수
  있다. 종로구 등록 행사 자체가 25건뿐이라 표본이 작다는 점도 함께 둔다.

### D-056 — TourAPI 주차·요금·이미지 원문은 `places`에 직접 둔다

- 상태: `Accepted`, 마이그레이션 작성 완료 (DB 적용·코드 배선 대기)
- 배경: 추천 카드에 주차·요금·썸네일을 노출하려는데, 저장 위치를 `places` 컬럼 추가와
  별도 테이블 분리 중 어디로 할지 정해야 했다.
- 결정: `places`에 컬럼 6개를 추가한다 — `parking_info_raw`, `parking_fee_raw`,
  `use_fee_raw`, `discount_info_raw`, `first_image_url`, `thumbnail_url`.
- 근거: 출처(`detailIntro2`)·관계(1:1)·갱신 주기(`detail_fetched_at` TTL)가
  기존 `operating_hours_raw`와 동일하다. 분리하면 수명주기 컬럼(`detail_fetch_status`,
  `detail_fetched_at`)을 복제하거나 두 테이블의 신선도가 어긋난다. `place_enrichments`도
  아니다 — 그쪽 `source_type`은 `manual_research`/`external_data`/`derived`로 제한된
  사람 조사·파생값이고, TourAPI 원문은 성격이 다르다.
- 외부 호출은 늘지 않는다: `place_sync`가 이미 장소마다 `detailIntro2`를 부르고
  있고(운영시간·휴무일 용도) 같은 응답에서 더 읽기만 한다. 이미지 2개는
  `areaBasedList2` 목록 응답에 이미 들어 있어 상세조회조차 필요 없다.
- **함정 — 축제(15)의 이용요금 필드명은 `usetimefestival`이다.** 이름은 시간처럼
  보이지만 내용은 요금이다. `real_place.py`의 `_OPERATING_HOURS_KEYS`가 이 키를 일부러
  제외하고 축제는 `playtime`을 쓰는 이유이므로, 요금 매핑을 추가할 때 그 구분을 깨면
  영업시간 자리에 "5,000원"이 들어간다.
- 커버리지 한계(2026-08-08 실측, 종로구 844건):
  - 주차 806건(95%) — 축제(15) 38건에는 주차 필드가 아예 없다.
  - 이용요금 204건(24%) — 14·15·28에만 있다. 12 관광지·32 숙박·38 쇼핑은 요금이
    `detailCommon2`의 `overview` 산문에 섞여 있어 별도 파싱이나 수동 보강이 필요하다.
  - 우선 있는 값으로 카드를 채우고, 누락분은 이후 `place_enrichments`로 보강한다.
- 이미지 2개는 `detail_fetched_at`이 아니라 `list_fetched_at` 주기를 따른다. 상세조회가
  실패한 장소에서도 이미지는 최신일 수 있다.
- 구현: `supabase/migrations/202608080001_add_place_parking_fee_image_columns.sql`.
  provider 매핑(`real_place.py`)과 `place_sync.py` 저장 반영은 후속 작업.

### D-057 — 집중률 검색어는 토큰 전부를 희소성 순으로 쓰고, 대조는 임계값 0.9를 둔다

- 상태: `Accepted`, 구현 완료
- 배경: `concentration_search_key`(D-051 계열 결정)는 장소당 검색어를 **하나만** 둔다.
  공백이 든 값을 넘기면 0건이 돌아오기 때문에 이름을 잘라 하나를 골랐는데, 그 결과
  변별력 없는 토큰이 검색어가 된 사례가 남았다 — `'청와대 앞길'` → `'앞길'`,
  `'한국교회 100주년 기념관'` → `'100주년'`, `'창덕궁과 후원'` → `'창덕궁과'`.
- 결정:
  1. **검색어는 공백 토큰 전부를 저장한다.** 하나만 고르지 않는다.
  2. **호출 순서는 ① 기존 `concentration_search_key`가 있으면 그것을 1순위로 고정,
     ② 나머지 토큰은 코퍼스 등장 빈도 오름차순, 동률이면 긴 토큰 우선**으로 한다.
     결과가 나오면 멈춰 호출 수를 아낀다.
  3. **대조는 양쪽 공백을 모두 제거한 뒤** 수행한다. 지금은 `.strip()`만 해서
     `'서울 운현궁'`과 `'운현궁'`이 다른 문자열로 취급된다.
  4. **채택은 정확 일치 > 유사도 0.9 이상 최고값 > `no_data`** 순이다.
- **기존 검색어를 1순위로 고정하는 이유 — 희소성만으로는 순서가 정해지지 않는다.**
  종로구 이름 113개는 토큰 대부분이 1회만 등장해 동률이 쏟아진다. 빈도만으로 정렬하면
  현재 검색어 24건 중 **10건의 1순위가 바뀌고**, 그중 다수가 더 나빠진다(2026-08-08 실측):
  `'서울 문묘와 성균관'` `성균관`→`문묘와`, `'홍지문 및 탕춘대성'` `탕춘대성`→`홍지문`,
  `'아름다운 차박물관'` `차박물관`→`아름다운`, `'낙원동 아구찜 거리'` `아구찜`→`낙원동`.
  현재 24건은 전부 정상 조회되는 것이 확인됐으므로, 검증된 값을 휴리스틱으로 재계산해
  회귀를 만들 이유가 없다. 토큰 추가는 **순수한 능력 추가**로만 둔다.
- **적용 범위는 INFO 질의 경로다.** 그 경로는 발화에서 장소를 뽑아 매핑 테이블과
  대조한 뒤 검색어로 **1회** 호출한다. 목적은 `'닭한마리 골목 혼잡해?'`처럼 정식 명칭
  (`서울 동대문 닭한마리 골목`)과 어긋나는 발화를 찾아내는 것이다.
- **추천 경로의 호출 수 문제는 이 결정에 포함하지 않는다(보류).**
  `enrichment_service._enrich_candidate()`는 후보마다 1회씩 호출한다. `tAtsNm` 없이
  종로구 전량을 4회에 받을 수 있음을 확인했으나(3,390행 = 113장소 × 30일,
  `baseYmd` 20260808~20260906), 이는 검색어 설계와 무관한 별개의 최적화다. INFO
  경로는 요청당 1회라 전량 수집이 오히려 손해이므로 함께 묶지 않는다.
- **손으로 쓴 불용어 목록을 두지 않는다.** 처음에 `서울`·`골목`·`거리`와 함께
  `닭한마리`·`낙지볶음` 같은 음식명을 불용어 후보로 묶었는데, 실측하니 정반대였다
  (2026-08-08, 종로구 113개 이름):
  - 2회 이상 등장하는 토큰은 7개뿐 — `서울`(9), `동대문`·`터`·`골목`·`쌈지길`·
    `[유네스코`·`세계유산]`(각 2).
  - `닭한마리`·`낙지볶음`·`아구찜`·`앞길`·`전망대`는 모두 **1회**로, 코퍼스에서 가장
    변별력이 높다.
  즉 기준은 품사나 의미가 아니라 **코퍼스 내 희소성**이다. 빈도 정렬 하나로 `서울`은
  자동으로 뒤로 밀리고, "닭한마리 골목 혼잡해?"는 `닭한마리`로 바로 찾아진다.
  데이터가 바뀌면 빈도도 따라 바뀌므로 목록을 손보지 않아도 된다.
- **임계값 0.9는 유사도 매칭 금지 방침의 조건부 예외다.**
  `build_concentration_mappings.py`는 "편집거리 같은 유사도 매칭은 쓰지 않는다 —
  이름이 크게 다른 장소를 잘못 붙이면 엉뚱한 곳의 혼잡도를 답한다"는 이유로 보수적
  매칭만 해왔다. 그 취지를 유지하려면 바닥 없는 `max()`를 쓰면 안 된다 — 찾는 장소가
  응답에 없어도 가장 덜 틀린 것이 정답인 척 나가고, 지금 `None`을 반환하며 포기하는
  자리([`enrichment_service.py`의 `select_concentration_forecast()`])가 조용한 오답으로
  바뀐다. 0.9는 사실상 표기 차이만 흡수하는 값이다. 낮추는 것은 실측 결과를 보고
  판단한다.
- 실측으로 확인한 전제(2026-08-08):
  - `tAtsNm`은 부분 일치이며 **`areaCd`/`signguCd`로 좁혀진 뒤** 적용된다.
    `'전망대'`가 종로구에서 고유 이름 1건(`채석장 전망대`), 강남구에서 0건이다.
    전국을 훑지 않으므로 종로구 코퍼스 기준 빈도가 올바른 척도다.
  - `'닭한마리'` 단독 검색은 `서울 동대문 닭한마리 골목` 하나만 반환한다.
  - `'서울'`은 고유 이름 4건으로 갈린다.
  - 집중률 API의 `signguCd`는 `11110`으로, TourAPI 목록의 `lDongSignguCd`(`110`)와
    체계가 다르다. 코드를 섞으면 조용히 0건이 돌아온다.
- 남는 한계: 집중률 API에만 있고 `places`에 짝이 없는 이름 12건은 이 변경으로
  풀리지 않는다(`돈의문박물관마을`, `부엉이박물관`, `화정박물관` 등). 수동 오버라이드가
  따로 필요하다.
- 구현 예정 범위(C): `app/providers/concentration.py`,
  `app/agent_context/enrichment_service.py`, `scripts/build_concentration_mappings.py`,
  `place_concentration_mappings` 스키마(검색어 복수 저장).
- 구현: 마이그레이션 `202608080002_add_concentration_search_keys.sql`이 배열 컬럼
  추가·backfill·단수 컬럼 삭제를 한 번에 처리하고, 코드도 같은 PR에서 전부
  이관했다. 단수 컬럼을 남겨 병행하지 않은 이유는 목록의 1순위가 기존 검색어와
  같은 값이라 진실의 원천이 둘이 되기 때문이다(저장소에서 반복된 "레거시 필드의
  이중 경로" 유형). 스키마 문서는
  [place-database-schema.md §6.1](./design/place-database-schema.md)에 반영했다.

### D-058 — 운영시간이 요일을 열거했으면 빠진 요일은 휴무로 유도한다

- 상태: `Accepted`, 구현 완료 (D 리뷰 대기 — D-008 경계를 옮기는 결정이라 소유자 확인 필요)
- 배경: 운영시간 파서가 요일별 시간을 분리하지 못해 두 가지가 조용히 통과하고 있었다.
  `_WEEKLY_CLOSURE_PATTERN`의 구분자에 `~`가 없어 `매주 월요일~화요일`이 `[월]`만
  남겼고(활성 33건), `_parse_operating_rules()`가 `weekdays=None` 고정이라 요일마다
  시간이 다른 원문이 요일 구분 없는 규칙들로 평탄화됐다(활성 88건). 둘 다
  `parse_status=parsed`에 warning도 없었다.
- 요일 분리를 구현하고 나니 남는 질문이 하나 생겼다 — **원문이 요일을 열거했는데 어떤
  요일이 빠져 있으면, 그 요일은 휴무인가 미확인인가.** D-008은 "운영시간 미확인은
  폐점이 아니다"라 미확인이면 하드 필터를 통과하고 가중치만 재분배된다.
- 결정: **빠진 요일을 정기 휴무로 유도해 `ClosureRule`로 내보낸다.** 단, 요일 없는
  규칙(`weekdays=None`)이 하나라도 있으면 그 규칙이 7요일 전부를 덮으므로 유도하지
  않는다.
- 근거(2026-08-08 실측, 활성 844건): 요일을 열거하고도 빠진 요일이 있는 장소가 39건인데,
  그중 **38건은 그 빠진 요일이 휴무 원문에 이미 명시**돼 있었다. 원문 자체가 규칙을
  38번 교차검증해준 셈이다. 남은 1건(북촌문화센터, `130903`)은 반례가 아니라 휴무
  필드가 비어 있는 원본 결함이고, 실제로도 월요일에 문을 닫는다.
- **원문 휴무와 유도 휴무는 `source_text`로만 구분한다** — 유도분은
  `DERIVED_CLOSURE_SOURCE_TEXT`("운영시간에 열거되지 않은 요일")를 갖는다. `place_sync`가
  `source_text`를 직렬화하므로 DB에서도 골라낼 수 있다. D 판정 자체는 원문 휴무와 같다.
- **파싱 실수의 대가가 커진다.** 요일 범위를 하나 잘못 읽으면 결과가 "점수가 틀림"이
  아니라 "후보가 하드 필터로 사라짐"이 된다. 복구 경로가 없다. 현재 코퍼스에서 유도가
  붙는 장소는 1건뿐이라 노출이 작지만, **종로구 밖으로 데이터를 넓히면 유도가 붙는
  장소 수부터 다시 재야 한다.**
- 회귀 범위(구/신 파서를 7일 × 6개 시각으로 대조): 활성 844건 중 **97건**의 운영 판정이
  달라졌다 — 열림→닫힘 335건, 구간변경 404건(그중 운영점수가 실제로 달라진 것 61건,
  최대 ±0.75점 → 가중치 0.4 기준 총점 0.3점). `parse_status` 분포는 바뀌지 않는다.
  이번 변경은 "몇 건을 읽었나"가 아니라 "읽은 내용이 맞나"를 고친 것이다.
- 구현: `app/domain/operating_hours.py` — `_split_weekday_segments()`(요일 선언 단위
  분할, 줄 경계를 넘어 유지), `_expand_weekdays()`(범위 전개, 주 경계 포함),
  `_derive_unlisted_weekday_closures()`. 소비 측(`candidate_mapper.py`)의
  `_rule_applies()`·`_is_regular_closure()`가 이미 요일을 읽고 있어 D 코드 변경은 없다.
  `OPERATING_PARSER_VERSION` `1.1.0` → `1.2.0`(저장분 재파싱 트리거).
- 함께 잡은 오독 3종: 요일 선언과 시간 구간이 다른 줄에 오는 원문(`[평일]<br>-
  10:00~17:00`), 시설 구획이 바뀔 때 앞 구획 요일이 새어나가는 문제(`[자율학습실]
  07:00~22:00`이 앞의 `주말` 전용이 됨), 요일이 범위가 아닌 안내인 경우
  (`※ 매주 화요일 휴관`, `주중 브레이크타임`, `토요일 미사`, `매월 마지막 수요일`).
- 남는 것: `준비시간`·`브레이크타임` 구간이 운영 구간으로 파싱된다. 지금은 앞 구간이
  먼저 매칭돼 실질 피해가 없어 범위 밖으로 둔다.

### D-059 — SCHEDULE 되묻기 답변은 프롬프트 컨텍스트로 이어간다(상태 레벨 override 아님)

- 상태: `Accepted`, 구현 완료
- 배경: 1턴 "주말에 종로에서 일정 짜줘" → Intent=SCHEDULE, 장소가 여러 곳으로 해석돼
  Tool(C) 레벨 `location_ambiguous` 되묻기로 끝남. 2턴 "광화문으로 알려줘" → Intent=MODIFY로
  오분류되어 일정 편성이 아니라 장소 추천/변경 응답이 나갔다.
- 원인 조사 결과 3가지: (1) `build_interpretation()`이 `classify_intent()`를 호출할 때
  `has_previous_recommendation`/`shown_place_count`만 넘기고 "직전 턴이 되묻기로
  끝났는지/그 되묻기가 어떤 Intent였는지"는 전혀 전달하지 않았다. RECOMMEND는
  `_INTENT_PRIORITY`의 fallback 기본값이라 이런 상황에서도 대개 자연스럽게 RECOMMEND로
  떨어지지만, SCHEDULE은 "일정/코스/순서" 키워드가 있어야만 선택되는 명시적 분류라
  fallback이 없다 — "광화문으로"는 오히려 `_CONTEXT_DEPENDENT_RULES`의 MODIFY 예시
  패턴("지명+조사")과 정확히 일치해 그쪽으로 끌린다. (2) 되묻기 플래그 소비
  화이트리스트(`agent_runtime.py`)가 `(RECOMMEND, MODIFY)`뿐이라, 설령 SCHEDULE로 옳게
  분류되더라도 `pending_clarification` 플래그가 지워지지 않았다. (3)
  `state_transform.transform()`은 이미 SCHEDULE을 RECOMMEND와 동일하게 취급해 되묻기
  답변 시 soft reset을 건너뛰는 로직을 갖추고 있었다 — 분류만 SCHEDULE로 바로잡으면
  나머지(조건 병합)는 이미 정상 동작하는 상태였다.
- 결정: D-053("단독 지명은 정보 조회")과 같은 방향 — **상태 레벨 결정적 override가 아니라
  `classify_intent`에 필요한 컨텍스트를 넘기고 프롬프트 규칙만 추가**해 LLM 판단을
  바로잡는다. `InterpretRequest`에 `pending_clarification`/`last_intent`
  (B의 `SessionContextResponse`에서 그대로 채움)를 추가하고, `classify_intent()`
  시그니처(Protocol/Real/Fake 3곳)에 동일 키워드 인자를 추가해 `orchestrator.py`가
  그대로 전달한다. 실 Gemini 프롬프트(`_CONTEXT_DEPENDENT_RULES`/`_BOUNDARY_CASES`)에
  "직전 턴이 SCHEDULE 되묻기로 끝났고 이번 발화가 그 답변으로 보이면 SCHEDULE 유지"
  규칙을 추가하고, "현재 대화 컨텍스트" 블록에 되묻기 여부를 한 줄 노출한다(SCHEDULE
  외 다른 `last_intent`는 이번 범위 밖이라 노출하지 않음 — 프롬프트 비대화 방지).
  `PROMPT_VERSION` 1.0.6 → 1.0.7.
- Fake(`FakeLLMProvider`)도 동일한 정보를 받아 미러링한다 — `_SCHEDULE_MARKERS` 매칭
  분기 바로 뒤, `has_previous_recommendation` MODIFY 분기보다 먼저 "직전 SCHEDULE
  되묻기 + 명시적 재시작 표현 아님" 조건을 검사해 SCHEDULE로 분류한다. 재시작 표현
  목록은 `state_transform._RESET_SCOPE_PHRASES`와 같은 문구를 stub.py 로컬 상수로
  미러링한다 — Fake는 프로덕션 상태 모듈에 의존하지 않는 기존 레이어 분리를 유지한다.
- 부가 수정: `agent_runtime.py`의 되묻기 플래그 소비 화이트리스트에 `Intent.SCHEDULE`
  추가 — 옳게 SCHEDULE로 이어져도 플래그가 안 지워지던 문제를 함께 고쳤다.
- 대안(state-level override)을 채택하지 않은 이유: classify_intent 호출 자체를 건너뛰고
  "직전이 SCHEDULE 되묻기면 무조건 SCHEDULE로 강제"하는 방식도 검토했으나, LLM이
  OUT_OF_SCOPE/GENERAL 등으로 정확히 분류해야 하는 경우(되묻기 답변에 욕설이나 완전히
  무관한 질문이 온 경우)까지 SCHEDULE로 덮어써 버릴 위험이 있다. 프롬프트 규칙은 LLM의
  판단을 계속 신뢰하면서 이 특정 경계(되묻기 이어가기 vs MODIFY)만 바로잡는다 — D-053이
  이미 이 방향을 팀 방침으로 확정했다.
- 범위 밖: SCHEDULE 외 다른 Intent(INFO, COMPARE 등)가 되묻기로 끝나는 경우의 이어가기는
  이번 재현 범위가 아니다. 필요성이 확인되면 같은 패턴(컨텍스트 전달 + 프롬프트 규칙)을
  확장한다. `MODIFY`가 `current_conditions is None`일 때의 기존 자체 되묻기 처리
  (`orchestrator.py` 별도 분기)는 이번 버그와 무관해 손대지 않았다.
- 구현: `app/schemas.py`(`InterpretRequest`), `app/services/runtime/agent_runtime.py`
  (컨텍스트 전달, 소비 화이트리스트), `app/services/interpret/orchestrator.py`
  (`classify_intent()` 호출부), `app/providers/protocols.py`/`gemini.py`/`stub.py`
  (시그니처), `app/providers/gemini_prompts.py`(프롬프트 규칙 + 버전).
- 확인 방법: `tests/test_llm_provider.py`(SCHEDULE 되묻기 이어가기가 MODIFY 패턴보다
  우선함, 명시적 재시작 표현은 강제되지 않음, 컨텍스트 없을 때 기존 동작 회귀 없음,
  프롬프트 텍스트에 규칙·컨텍스트 줄 반영 확인), `tests/test_agent_runtime.py`
  (SCHEDULE 되묻기 E2E — intent 유지 + 플래그 소비 확인). 전체 회귀 1333 passed / 22
  skipped, ruff 통과.

### D-060 — INFO 상세 질의는 정규화 필드로 읽고, 출처는 캐시로 단일화한다

- 상태: `Accepted`, C 구현 완료 (마이그레이션 적용 완료, 값 적재 대기)
- 배경: 추천 카드에 이어 INFO 상세 질의도 places 캐시로 답하려 했는데 세 가지가
  막혔다. (1) `extract_info_fields()`가 요금·주차·편의시설을 `PlaceDetails.raw_intro`
  에서 TourAPI 원본 키로 찾는데, 저장소는 유형별 키를 한 컬럼으로 눌러 담아 원본
  키를 복원할 수 없다. (2) 전화번호의 출처가 불분명했다. (3) 편의시설은 캐시에
  대응 컬럼이 아예 없었다.
- 결정 1 — **요금·주차·편의시설을 정규화 필드로 옮긴다.** `PlaceDetails`에 `fee`/
  `parking`/`parking_fee`/`baby_carriage`/`pet`/`credit_card`/`restroom`을 추가하고
  provider가 contenttypeid별 키를 훑어 채운다. `extract_info_fields()`는 이 필드를
  읽고, **`raw_intro`를 읽던 옛 경로는 전부 지웠다.**
- 근거: `operating_hours`가 진작부터 이 방식이었다("provider가 이미 유형별 키를 훑어
  정규화해둔 값이 있다"). 대안으로 하이브리드가 `raw_intro`를 합성 dict로 되돌려
  채우는 방식도 검토했으나, 키 몇 개짜리 합성 dict는 "없는 키"와 "그 장소에 없는
  정보"를 구분할 수 없어 같은 조용한 실패를 다시 만든다. 두 경로를 함께 두는
  선택지는 버렸다 — 같은 질문이 provider에 따라 다르게 답한다.
- 결정 2 — **전화번호는 `detailIntro2`의 안내처가 출처다.** `places.info_center_raw`를
  추가하고 동기화가 채운다.
- 근거(2026-08-10 실측, 표본 35건): `detailCommon2`의 `tel`이 채워진 것은 축제(15)
  5/5뿐이고 12·14·28·32·38·39는 전부 0/5였다. 같은 표본의 `detailIntro2`
  `infocenter*` 계열은 33건 중 32건(97%)이 채워져 있다. 축제는 `infocenter` 계열이
  없어 `tel`을 쓰므로 두 출처를 순서대로 본다(안내처 → tel).
- 결정 3 — **편의시설도 컬럼 4개로 캐시한다.** `baby_carriage_raw`/`pet_raw`/
  `credit_card_raw`/`restroom_raw`. jsonb 하나로 합치지 않는다 — 소비 측이 키 이름을
  알아야 하고 "키가 없다"와 "정보가 없다"가 구분되지 않아 결정 1이 걷어낸 문제를
  되살린다. 이름은 A에게 나가는 계약 키와 1:1로 맞춘다.
- 근거(2026-08-10 실측, facility 필드가 존재하는 유형 5종에서 55건): 값이 하나라도
  있는 장소가 22/55(40%)다. 카드결제 19/55(쇼핑 12/12·음식점 6/12·관광지 1/12),
  화장실 12/55(쇼핑 12/12), 유모차 4/55(값은 모두 `없음`), 반려동물 0/55.
  **쇼핑에 몰려 있다는 점이 결정적이다** — 인사동·광장시장 같은 곳의 "화장실 있어?"를
  캐시만으로 답하려면 이 컬럼이 필요하다.
  (조사 초기에 "표본 33건 중 1~4건"으로 과소평가했는데, 그 표본에는 facility 필드가
  아예 없는 축제(15)·숙박(32)이 섞여 있어 비율이 희석된 것이었다.)
- 결정 4 — **INFO 상세 출처를 고르는 설정을 두지 않는다.** 하이브리드 경로만 남긴다.
  캐시가 `question_type` 전부를 덮게 되면서 TourAPI 직접 조회와 답할 수 있는 질문이
  같아졌고, 고를 이유가 사라졌다. 검토 단계에서 `INFO_PLACE_DETAIL_SOURCE`를 도입했다가
  편의시설 공백이 메워지면서 철회했다. 설정을 남겨두면 두 경로의 동작이 갈리는지
  계속 확인해야 한다.
- 외부 호출: INFO 상세 질의가 3회(searchKeyword2 + detailCommon2 + detailIntro2)에서
  **1회**(detailCommon2)로 준다. `overview`(표본 35건에서 100%, 평균 326자)와
  `homepage`(63%)는 캐시에 없어 그 1회가 남는다.
- D-054를 대체한다. 그 결정은 "캐시에는 INFO가 답할 데이터가 없다"가 전제였고, 당시
  동기화 대상은 `operating_hours_raw`/`rest_date_raw`뿐이었다. D-056(주차·요금)과
  이번 안내처·편의시설로 전제가 사라졌다.
- 미완결: `info_center_raw`와 편의시설 4개 컬럼은 마이그레이션만 적용됐고 값은 비어
  있다. 동기화를 다시 돌려야 채워진다. 그때까지 INFO 전화번호·편의시설은 빈 응답이다.
- 별건으로 남긴 것: `location_info`는 `_fetch_place_detail_info()`에서 상세조회 전에
  early return 하므로(주소만으로 답이 성립한다는 판단) `extract_info_fields()`의
  `location_info` 분기가 서비스에서 도달하지 않는다. 캐시에 전화번호가 생기면서
  "외부 호출 한 번을 아낀다"는 그 근거가 사라졌으므로 early return을 재검토해야
  전화번호가 실제로 쓰인다.
- 조용한 실패 방지: 정규화 필드를 provider가 안 채워도 규칙 테스트는 통과한다. 그래서
  `tests/test_place_details_normalized_fields.py`가 **provider를 직접 태우고**
  `extract_info_fields()` 출력이 비지 않는지 단언한다. `_to_place_details`에서
  `parking=None`·`restroom=None`으로 배선을 끊으면 실제로 실패하는 것을 확인했다.
  `app/providers/tour_intro_keys.py`를 분리해 실 provider와 fake가 같은 키 목록을
  쓰도록 구조로 고정했다.
- 구현: `supabase/migrations/202608100001_add_place_info_center_column.sql`,
  `202608100002_add_place_facility_columns.sql`,
  `app/providers/hybrid_place_details.py`, `app/providers/tour_intro_keys.py`,
  `app/domain/models.py`, `app/providers/real_place.py`/`stub.py`/
  `supabase_place_details.py`/`factory.py`/`protocols.py`,
  `app/agent_context/info_field_rules.py`/`factory.py`,
  `app/repositories/supabase_places.py`/`protocols.py`, `app/services/place_sync.py`,
  `app/tools/place_detail.py`.

### D-061 — 상세 카드용 운영시간 표시 구조를 C가 정규화해 제공한다

- 상태: `Proposed` — C 확인·구현 필요
- 배경: 현재 INFO·추천 상세 카드의 `PlaceCard.operating_hours`는 TourAPI 운영시간 원문을
  문자열로 전달한다. 월별 표기(`[1월~2월]09:00~17:00...`)는 프론트가 읽기 좋게 나눌 수
  있지만, `10:00~18:00 수,토 10:00~21:00`처럼 기본 구간과 요일 예외가 한 줄로 붙은
  원문은 구조를 알 수 없어 그대로 노출된다. 이 값은 Scoring의 당일 개폐 판정에는 이미
  쓰이지만, 사용자가 읽는 상세 카드에는 별도 표시 계약이 없다.
- 제안: C가 기존 `OperatingSchedule` 파싱 결과를 바탕으로 INFO `PlaceCard`와 추천 상세
  조회가 함께 쓸 표시 전용 구조화 운영시간을 제공한다. 예:
  `[{"label":"기본","hours":"10:00–18:00"}, {"label":"수요일 · 토요일","hours":"10:00–21:00"}]`.
  원문 `operating_hours`는 감사·파서 보완용으로 계속 보존하고, A/프론트는 새 구조가 있을
  때만 카드 행으로 렌더링하며 없으면 원문 표시로 폴백한다.
- C 확인 사항: (1) `OperatingSchedule`에서 요일별 예외를 손실 없이 꺼낼 수 있는지,
  (2) 월별·요일별·휴무·24시간 표기를 하나의 표시 모델에 어떤 필드로 담을지,
  (3) `PlaceCard` 계약에 추가할지 또는 단건 상세조회 전용 모델로 분리할지 결정한다.
- 범위 밖: 이번 작업은 운영시간 계산·Scoring 필터·파서 규칙을 바꾸지 않는다. 프론트의
  임시 문자열 분해는 새 C 계약이 확정될 때까지 표시 폴백으로만 유지한다.

### D-062 — 게스트 로그인은 Supabase 익명 사용자로 발급한다

- 상태: `Proposed` — 설계 확정, 구현 전
- 배경: 정식 로그인 기능이 아직 없다. 현재 사용자 개념 자체가 없고 익명
  `session_id`(TTL 30분) 하나만 있어서, 로그인 도입 전까지의 사용자 데이터를 담을
  주체가 없다. 정식 로그인은 Supabase Auth로 가기로 정해져 있다.
- 결정: 게스트를 "로그인 안 한 상태"로 두지 않고 Supabase Auth
  `signInAnonymously()`로 발급한 **정식 사용자 한 명**(`auth.users`에 행 생성,
  `is_anonymous = true`)으로 취급한다.
- 이유: 나중에 `linkIdentity()`/`updateUser()`로 계정을 연결하면 `sub`(uid)가 유지된
  채 `is_anonymous`만 false가 되므로, 게스트 데이터 승계가 이관이 아니라 플래그
  전환이 된다. 자체 `guest_id`를 발급했다가 병합하는 방식에서 필요한 병합 로직·부분
  실패 복구·중복 소유자 처리를 만들지 않아도 된다.
- 계층: 신원(uid, 기기 영속)과 세션(`sess_...`, TTL 30분)을 분리한다. B 계약 5.2절의
  세션 발급·만료·재발급 규칙은 바꾸지 않는다.
- 전달: `Authorization: Bearer <supabase access token>` 헤더로만 받는다.
  `AgentRequest` body에 `user_id`를 두지 않는다 — 클라이언트가 임의의 uid를 적어 보낼
  수 있기 때문이다.
- 조용한 통과 방지: 초기에는 인증을 optional로 두되, **토큰 없음**은 통과시키고 `WARN`
  로그와 응답 메타 `authenticated: false`로 드러내며, **토큰이 있는데 검증 실패**한
  경우는 익명으로 강등하지 않고 401로 끊는다 (D-042와 같은 방향).
- 저장: `agent_states`/`recommendation_histories`에 `user_id uuid null`을 추가한다.
  RLS 정책은 신설하지 않는다 — 프론트는 DB에 직접 붙지 않고 FastAPI만 통하므로 서버
  secret key 단독 경로를 유지한다.
- 운영 전제: Supabase 대시보드에서 익명 로그인을 활성화한다(2026-08-19 완료). 누구나
  무제한으로 사용자를 만들 수 있는 엔드포인트라 방치하면 MAU가 부풀어난다. 남용 방지는
  rate limit(기본 IP당 시간당 30회)을 먼저 쓰고, CAPTCHA는 익명 사용자 수가 실제로
  비정상적으로 늘 때 켠다 — 켜면 모든 auth 엔드포인트에 적용돼 이후 소셜 로그인까지
  영향을 받고 프론트 위젯·토큰 전달 배선이 따라온다. 익명 사용자 발급은 페이지 로드가
  아니라 "게스트로 시작하기" 버튼을 눌렀을 때만 한다.
- 미결: (1) 오래된 익명 사용자 정리 주기와 참조 행 처리, (2) 이미 계정이 있는 사용자가
  게스트 상태에서 로그인할 때의 데이터 병합 — uid가 달라지는 유일한 케이스로, 로그인 도입 시점에 별도
  결정으로 다룬다.
- 개인정보: 게스트라고 익명 데이터가 아니다 — uid가 유지된 채 계정으로 승격되므로
  게스트 기간 기록이 실명 사용자에게 소급 귀속된다. 대화 원문은 자유 텍스트라 민감정보
  혼입을 통제할 수 없어 이번 범위에서 서버 저장을 하지 않는다. 대화 이어쓰기가 필요해지면
  프론트 `localStorage` → 조건만 복원 → 서버 대화 로그 순으로 검토한다(위험도와 구현
  비용 순서가 같다). 음성은 현재대로 저장하지 않는다.
- LLM 전송: 발화를 Gemini에 넘기는 것 자체는 처리위탁 구조라 문제가 아니지만, 현재
  경로가 Vertex AI가 아니라 Gemini Developer API(AI Studio)라 (1) 무료 티어면 입력이
  모델 학습에 쓰일 수 있고 (2) 리전 지정이 안 돼 사실상 국외이전이다. `LLM_API_KEY`의
  티어 확인과 처리방침 명시가 선행 조건이다. **프롬프트와 LLM 호출 메타데이터에는
  게스트 uid·`session_id`·좌표를 넣지 않는다** — 현재 `gemini_prompts.py`가 이미 그런
  상태이고 이를 규칙으로 고정한다. 추적이 필요하면 내부에만 남는 `trace_records`를 쓴다.
- 검증: 이 프로젝트 Auth는 비대칭 키(ES256)로 서명하고 JWKS로 공개키를 공개한다
  (`key_ops: ["verify"]`). 백엔드는 비밀값을 보관하지 않고 공개키를 캐시해 로컬에서
  검증한다 — `GET /auth/v1/user` 호출 방식은 요청마다 왕복이 붙고 Auth 장애가 전 API
  장애로 번진다. 위험은 공개키 유출이 아니라 검증 코드에 있다: `algorithms`를 우리가
  고정하고(헤더의 `alg`를 따르면 알고리즘 혼동 공격으로 공개키가 HMAC 비밀키로
  악용된다), JWKS 주소는 설정값으로 고정하며, `iss`/`exp`를 확인하고, 모르는 `kid`면
  JWKS를 다시 받는다.
- 상세: `docs/design/guest-auth-design.md`

### D-063 — 게스트 신원을 세션에 저장하기 전에 정할 것 (B 확인 필요)

- 상태: `Proposed` — **Package B 확인 대기.** 네 항목 모두 B 소유 영역이다.
- 배경: D-062 Phase 1~2로 프론트가 신원을 발급해 보내고 백엔드가 검증까지 하게 됐다
  (PR #183). 다음 단계는 검증한 `user_id`를 세션에 붙여 저장하는 것인데, 대상이
  `agent_states`·`recommendation_histories`와 `AgentState` 계약이라 B 판단이 선행해야
  한다. 상세와 마이그레이션 초안은 `docs/design/guest-auth-design.md` 6-1절에 있다.
- 결정 1 — **저장소 전환 시점.** `STATE_STORE_BACKEND` 기본값이 `memory`라 이대로
  `user_id`를 저장하면 재시작마다 소유자 정보가 사라진다. 권장: 전환을 Phase 3에
  포함하지 않고, `supabase`로 켜는 순간 동작하는 상태까지만 만든다. 전환은 모든 세션
  읽기·쓰기가 네트워크를 타게 되어 지연·장애 특성이 달라지므로 저장소 소유자가 시점을
  정한다. 이 설정은 게스트 로그인이 만든 것이 아니라 `[B-05]`(2026-08-03)로 이미
  있던 것이다.
- 결정 2 — **소유권 검증 위치.** 지금은 `session_id`만 알면 남의 세션도 조회된다.
  권장: Phase 4(인증 필수화)로 미룬다. 신원이 반드시 있다는 전제가 서는 시점이라
  소유자 대조를 넣기 자연스럽고, Phase 3에 넣으면 신원 없는 요청 처리가 애매해진다.
- 결정 3 — **비어 있는 세션에 신원이 붙는 경우.** 권장: `user_id`가 비어 있으면 채우고,
  값이 있으면 절대 덮어쓰지 않는다. 빈 칸을 채우는 것은 소유권 이전이 아니지만 값이
  있는 것을 덮어쓰는 것은 소유권 탈취다.
- 결정 4 — **`auth.users(id)` 외래키.** 사용자 테이블은 이미 있다(Supabase 관리
  `auth.users`, `signInAnonymously()`마다 행 생성). 권장: FK를 걸지 않는다.
  (1) `db-store-design-v2.md` §2-3이 테이블 간 FK를 의도적으로 두지 않았고,
  (2) `public`이 통제 밖인 `auth` 스키마에 의존하게 되며,
  (3) 오래된 익명 사용자 정리와 충돌한다(`restrict`면 삭제가 막히고 `cascade`면 세션이
  함께 지워진다).
- 상태 갱신(2026-08-20): 네 결정 모두 확정되어 TP-101 3단계 착수·완료. 마이그레이션
  `supabase/migrations/202608200002_add_user_id_to_agent_state_tables.sql` 작성
  완료(아직 미적용), `AgentState`/`RecommendationHistory.user_id` 필드와 연결
  로직(`session.attach_user_id()`/`history.attach_user_id()`) 구현·테스트 완료.
  `record_recommendation`/`record_closed_exclusions`/`apply()`의 rejected_places
  경로까지 세 곳 모두 연결되어, 세션(`agent_states`)과 이력(`recommendation_histories`)
  양쪽 모두 신원이 채워진다. 남은 건 마이그레이션을 실제 Supabase에 적용하는 것뿐이다.
- 상세: `docs/design/guest-auth-design.md` 6-1절

### D-064 — 프롬프트 `meta.yaml`은 당분간 사람이 읽는 선언으로 두고, 런타임은 Markdown만 읽는다

- 상태: `Superseded` — 아래 "미룬 것 1"(`version:` 소비)은 **D-065로 즉시 착수해 완료**했다.
  나머지(`owner:`, `evals:`, 미조합 공통 규칙 3건)는 여전히 `Deferred`.
- 배경: 인텐트별 프롬프트 라이브러리(`backend/app/prompts/`)를 도입하면서 프롬프트 본문을
  `gemini_prompts.py`의 f-string에서 인텐트 폴더의 Markdown으로 옮겼다(커밋 `0695d86`).
  이관 직후 점검에서 `meta.yaml`의 일부 필드가 어떤 코드에도 소비되지 않는다는 것이
  확인됐다. 이관 자체는 프롬프트 텍스트를 바꾸지 않는 순수 이동이었고, 렌더 결과
  스냅샷 27건이 0바이트 차이로 이를 증명한다.
- 결정 — **`meta.yaml`을 런타임 설정값으로 승격하지 않는다.** 런타임(`loader.py`)은
  Markdown 자산만 읽고, `meta.yaml`은 (1) 사람이 읽는 소유·버전 표시와 (2) CI 검사의
  입력으로만 쓴다. 강의 교재 25-6의 YAGNI 원칙 — *"필요해질 때 도입하는 것이 원칙"* — 을
  따른다. 지금 승격해도 사용자에게 달라지는 것이 없다.
- 현재 강제되는 것 (`backend/tests/prompts/test_prompt_assets.py`):
  - `template`·`bundle` 선언이 **실제 조합과 일치**해야 한다. 선언만 하고 코드가 안 읽으면
    CI 실패 — 이관 전 실제로 있었던 "담당자가 `.md`를 고쳐도 서비스는 그대로인" 조용한
    실패를 막는다.
  - 아무도 읽지 않는 자산(고아 파일)이 생기면 CI 실패. 예외는 이유를 적어
    `KNOWN_UNCOMPOSED`에 올려야 한다.
- ~~미룬 것 1 — `version:`이 아무 데서도 소비되지 않는다.~~ → **D-065로 해소됨.**
- 미룬 것 2 — **`owner:`도 소비처가 없다.** `app/prompts/OWNERS.md`와 이중으로 적혀 있어
  어긋날 수 있다. 필요해지면 `.github/CODEOWNERS`와의 일치를 CI로 검사한다.
- 미룬 것 3 — **`evals:`가 가리키는 인텐트별 평가 케이스가 아직 없다.** `evals/` 아래에
  README만 있고 실제 케이스는 0건이다. 인텐트별 단위 평가를 채우더라도 **머지 게이트는
  기존 `backend/test_results/agent_quality/`의 다중 턴 전수 실행을 유지한다** — 골드셋
  다중 턴 케이스가 인텐트 경계를 넘나들어(dev 35건 중 13건) 인텐트별로 쪼갤 수 없고,
  강의 27-4가 요구하는 것도 *"세트 전체의 향상"*이기 때문이다. 인텐트별 평가는 담당자의
  빠른 반복용이지 게이트가 아니다.
- 함께 미룬 것 — `_shared/rules/{factuality,safety,service_scope}.md` 3건은 작성돼 있으나
  아직 어느 프롬프트에도 조합되지 않았다. 넣으면 프롬프트 출력이 바뀌므로 골드셋 평가와
  함께 별도 변경으로 진행한다. 이유는 `KNOWN_UNCOMPOSED`에 기록돼 있다.
- 범위 밖: 프롬프트 편집 UI, 런타임 핫스왑, 프롬프트 DB 저장, Langfuse 등 외부 프롬프트
  관리 SaaS. 채점자가 저장소를 직접 읽는 프로젝트라 자산을 저장소 밖으로 빼지 않는다.
  관측 도구가 필요해지면 그때 붙이되 직접 만들지 않는다(강의 94-5).
- 상세: `backend/app/prompts/README.md`, `backend/app/prompts/OWNERS.md`

### D-065 — 실행 기록에는 그 턴이 실제로 쓴 슬롯의 버전을 남긴다

- 상태: `Accepted` — 구현 완료. D-064의 "미룬 것 1"을 대체한다.
- 배경: 기존에는 `record_trace(prompt_version=...)`에 손으로 적은 고정 문자열
  `agent-interpret-prompts-1.0.16` **하나만** 실렸다. 인텐트별로 담당자가 나뉜 뒤에는
  INFO 담당자가 `info/extract.md`를 고쳐도 이 값이 그대로여서, *"이 응답은 어느
  프롬프트에서 나왔나"*에 답할 수 없었다. 진짜 버전은 `meta.yaml`에 있는데 기록에는
  무관한 고정값이 붙는 상태였다.
- 결정 — **`registry.py`가 `meta.yaml`을 실제로 파싱**하고, 턴마다 그 턴이 사용한 슬롯의
  버전을 조합해 기록한다.

  | | 값 |
  |---|---|
  | 이전 | `agent-interpret-prompts-1.0.16` |
  | 현재 | `router.classify@2.0.0+info.extract@3.0.0` |

  과거 기준선으로 실행 중이면(`TRIPBRANCH_PROMPT_VARIANT`) 뒤에
  `+variant:<ID>`가 붙어 "옛 프롬프트로 낸 기록"임이 남는다.
- 슬롯 선택은 `INTENT_SLOTS` 라우팅 테이블로 한다(강의 89-3 "새 의도는 테이블에 한 줄").
  분류(`router.classify`)는 항상 돌고 그 뒤 인텐트별 추출/편성 슬롯이 하나 더 돈다.
  실제 렌더된 슬롯을 런타임에서 수집하지 않는 이유는 요청 컨텍스트 전파 배선이
  4~5개 파일에 걸치는 데 비해 인텐트→슬롯 대응이 결정적이기 때문이다.
- 답변 생성 슬롯(`*.answer`, `*.summary`)은 넣지 않았다 — 기록 시점
  (`step="llm_interpret"`)에는 아직 돌지 않았고, 회귀 판정에 쓰는 지표(intent·조건 추출
  정확도)가 전부 해석 단계 산출물이라 없이도 추적이 성립한다. 필요해지면
  `step="llm_answer"` trace를 추가한다.
- B 영향 없음 — 계약상 B는 이 값을 해석하지 않고 문자열로만 저장한다
  (`llmops-trace-contract-v1.md` §7 Q2). 형식이 바뀌어도 B 쪽 스키마 변경이 없다.
- 프롬프트 출력 불변 — 딱지만 바꾼 변경이라 챗봇 응답은 그대로다. 렌더 스냅샷 27건이
  0바이트 차이로 이를 보증한다.
- 안전장치 3건 (`backend/tests/prompts/test_prompt_assets.py`):
  - `INTENT_SLOTS`가 참조하는 슬롯이 `meta.yaml`에 전부 있어야 한다 — 슬롯 이름을 바꾸고
    표를 안 고치면 기록에서 슬롯 하나가 **조용히 빠지므로** 여기서 먼저 터뜨린다.
  - 모든 `Intent`가 `INTENT_SLOTS`에 등록돼야 한다 — 새 인텐트를 추가하고 등록하지 않으면
    분류 슬롯 버전만 남는다.
  - 조합 문자열이 슬롯을 빠짐없이 담는지 인텐트별로 확인한다.
- 남은 운영 부담: 프롬프트를 고칠 때 `meta.yaml`의 `version:`도 함께 올려야 한다. 지금은
  사람이 챙긴다 — 스냅샷 테스트가 "텍스트가 바뀌었다"는 사실 자체는 잡아주므로(갱신
  시점에 버전도 올리면 된다), 자동 강제는 두지 않았다.
- 상세: `backend/app/prompts/registry.py`

### D-066 — 답변·요약 계열 5곳에도 thinking_budget=0을 적용한다

- 상태: `Accepted` — 구현 완료. `_thinking_config_for()`(gemini.py)가 남겨뒀던 미해결
  메모를 대체한다.
- 배경: gemini-2.5-flash → gemini-3.5-flash 전환 이후 실사용에서 GENERAL 인사말
  ("안녕") 응답에도 6~7초 TTFT가 걸리고, COMPARE류 후속 질문에서 분류+추출 두 호출이
  합쳐 18초 넘게 걸려 클라이언트 45초 무활동 타임아웃에 근접하는 문제가 확인됐다
  (실제로 한 세션에서 48초 `stream_inactive` 오류 재현). 원인은 답변·요약 계열
  5곳(`generate_general_answer`/`stream_general_answer`/
  `generate_recommendation_summary`/`stream_recommendation_summary`/
  `stream_info_answer`/`generate_compare_summary`)이 `thinking_budget`을 아예 안 넘겨
  모델 기본 동작(gemini-3.5-flash는 MEDIUM, 항상 켜짐)을 그대로 썼기 때문 — 이전
  thinking_budget=0 적용(SCHEDULE·classify_intent·extract_recommend_conditions) 때는
  "문장 생성·요약류는 품질 저하 리스크"라는 이유로 의도적으로 제외했던 곳들이다
  (`_thinking_config_for()` docstring 참고).
- 결정 — `scripts/compare_answer_thinking_budget.py`로 5개 케이스 × 3회 실측
  (thinking_budget=None vs 0)한 결과, thinking_budget=0이 평균 **3.9배** 빠르면서
  (예: GENERAL 자기소개 5.9초→1.3초, COMPARE 요약 6.4초→1.6초) 답변 문구는 페르소나·
  자기소개("트리비")·문장 수 규칙을 그대로 지켰다(수동 확인 — 우려했던 품질 저하는 이
  케이스들에서 근거로 뒷받침되지 않았다). 결과:
  `test_results/answer_thinking_budget_latency.csv`. 5개 호출부 모두 SCHEDULE과 같은
  방식으로 `thinking_budget=0`을 내부에 고정했다(공개 API로 노출하지 않음 — 프로덕션에서
  이 값을 바꿔 부를 필요가 없다).
- 실사용 검증: `/api/chat/stream`에 같은 대화를 재현해 확인.
  - "안녕"(GENERAL): 9.35초 → 5.06초, TTFT 6.5초 → 0.85초
  - "첫 번째랑 두 번째 중에 어디가 더 가까워?"(COMPARE, 분류+추출): 18.8초 → 2.8초
- 안전장치: `tests/test_gemini_provider.py`에 5곳 각각 실제로 `thinking_config.
  thinking_level == MINIMAL`이 실리는지 확인하는 회귀 테스트 6건 추가(구조화 출력
  4곳 + 스트리밍 3곳, 스트리밍은 `generate_content_stream` mock으로 검증).
- Out of Scope: 분류·추출 계열 중 아직 손 안 댄 4곳(`extract_modify_conditions`/
  `extract_info_query`/`extract_compare_request`/`extract_general_request`)은 이번
  범위 밖 — 이번 작업은 "답변 생성 계열부터"로 한정했다. 45초 타임아웃의 다른 절반
  원인(분류·추출 단계에 SCHEDULE 같은 하트비트가 없는 문제)도 별도 작업이다.
- 상세: `backend/app/providers/gemini.py`(`_thinking_config_for()`),
  `backend/scripts/compare_answer_thinking_budget.py`

### D-067 — 후보를 줄 세우는 기준점은 검색 기준점이 아니라 사용자 위치다

- 상태: `Accepted` — 구현 완료. TP-109와 TP-112 카드가 남긴 "`distance_km` 기준점을
  바꾸지 않는다"를 대체한다.
- 배경: `"지금 혜화역인데 안국역 근처 갈만한 곳"`처럼 사용자 위치와 검색 기준점이
  갈리는 요청에서, 거리·경로·근거 문장이 전부 검색 기준점(안국역)에서 계산됐다.
  실사용 실측에서 `"자동차 이동 1분"`으로 표시된 후보가 사용자(서대문역) 기준으로는
  23분이었다 — 어긋남 22분(TP-112 코멘트, 네이버 Directions 실측).
- 결정 — **모으는 중심과 줄 세우는 기준점을 분리한다.**
  - 후보 수집은 그대로 검색 기준점 중심이다. `"안국역 근처"`라고 했으면 안국역
    주변을 뒤지는 것이 맞다.
  - 거리(`distance_km`)·실측 경로 origin·근거 문장의 기준점 이름은 사용자 위치
    (`context.user_location`)에서 잰다. 실제로 이동하는 사람이 사용자이기 때문이다.
  - 사용자 위치를 모르는 요청(발화도 기기 GPS도 없음)은 예전처럼 검색 기준점으로
    돌아간다. 지어낸 좌표로 줄을 세우지 않는다.
- 근거 — 검색 기준점에서 **같은 거리에 있는 두 후보를 타겟 기준으로는 영영 구분할 수
  없다.** 방향이 반대여도 동점이라 `place_id` 순으로 밀린다. 사용자 기준으로 재야
  사용자 쪽 후보가 앞선다(`tests/test_ranking_origin.py`의
  `test_user_side_candidate_wins_a_target_side_tie`).
- 거리 점수 분모: **사용자가 이동시간을 말한 요청은 손대지 않는다.** 그때 분모는
  `max_travel_time × 속도`이고 실측 분기에서 같은 속도로 다시 나뉘어 "사용자가 말한
  30분"이 그대로 예산이 된다 — 시간 약속은 어디서 재든 같은 값이라 애초에 원점이
  없다. 여기 거리를 더하면 "30분"이 사실상 30분+α가 된다.

  **말하지 않은 요청에만 사용자 → 검색 기준점 거리를 더한다.** 그때 분모는
  `DEFAULT_PLACE_SEARCH_RADIUS_KM`(2.0km)인데, 이 값은 "타겟 주변 얼마를
  뒤지는가"라는 수집 정책에서 빌려온 거리라 원점이 타겟에 묶여 있다. 분자만 사용자
  기준으로 바꾸면 사용자가 타겟에서 멀 때 모든 후보가 분모를 넘겨 거리 Feature
  (가중치 0.20)가 통째로 죽고, 순위가 날씨·운영시간만으로 정해진다. 후보는 전부 타겟
  중심 수집 반경 안에 있으므로 삼각부등식에 따라 사용자 기준 거리는
  `사용자→타겟 + 수집 반경`을 넘을 수 없다 — 이 값을 더하면 어떤 후보도 0으로 잘리지
  않고, 사용자가 기준점에 서 있으면 0.0이라 기존 분모로 되돌아간다.
- 채택하지 않은 것:
  - 수집 중심까지 사용자 위치로 옮기기 — `"안국역 근처 추천해줘"`라는 요청 자체를
    배신한다.
  - 후보들의 사용자 기준 거리로 min-max 정규화 — 변별력은 최대지만 분모가 후보
    집합에 따라 매번 달라져 "사용자가 말한 시간이 곧 예산"이라는 설계를 버리게 된다.
  - 분모를 타겟 기준으로 둔 채 분자만 옮기기 — 위 0점 문제가 그대로 남는다.
- 남은 것: 이동시간 미언급 분기의 분모에는 `MAX_PLACE_SEARCH_RADIUS_KM`(20km) clamp를
  걸지 않았다. 그 상수는 장소 검색 Provider가 허용하는 **수집** 상한이라 채점 분모에
  걸면 0점 문제가 되살아난다. 종로구 MVP 범위에서 20km 밖 사용자는 드물지만, 채점
  분모 전용 상한이 필요한지는 정하지 않았다.
- 상세: `backend/app/domain/ranking_origin.py`,
  `backend/app/services/recommendation_pipeline.py`(`_distance_denominator_offset_km`),
  `backend/tests/test_ranking_origin.py`

### D-068 — 피드백을 남긴 턴에 한해서만 질문·답변 원문을 저장한다

- 상태: `Accepted` — 구현 완료.
- 배경: `response_feedback`(roadmap.md 14번, D-062/D-063와 별개 트랙)은 `session_id`/
  `run_id`/`rating`만 저장해, 테스트 중 "이 반응이 정확히 뭐에 대한 것인지" 원문으로
  확인할 방법이 없었다. `guest-auth-design.md` 9절은 대화 원문을 저장 위험도가 가장
  높은 항목으로 분류하고(자유 텍스트, 개인정보·민감정보 통제 불가), 서버 대화 로그
  저장 자체를 9-4절에서 의도적으로 미루고 있다 — 이 전제는 그대로 유지한다.
- 결정 — **대화 전체가 아니라, 사용자가 좋아요/싫어요를 명시적으로 남긴 턴만** 그
  턴의 질문(`user_input`)·답변(`assistant_message`) 원문을 `response_feedback`에
  함께 저장한다. 두 컬럼 모두 nullable — 프론트가 텍스트를 못 찾거나 안 보내도
  `rating` 기록 자체는 그대로 유효하다.
- 근거: 전체 대화 로그(9-4절의 "3번")보다 노출 범위가 훨씬 좁다 — 사람이 실제로
  반응한 턴만 남는다. 그 목적(테스트 중 피드백 검토 편의)에도 대화 전체보다
  정확히 맞는다.
- 채택하지 않은 것:
  - 모든 턴의 원문을 저장 — guest-auth-design.md 9절이 경고한 가장 높은 위험
    시나리오와 같다. 지금 목적(피드백 검토)에 비해 과도하다.
  - `conditions`처럼 구조화해서 저장 — 자유 발화 자체를 구조화 없이 그대로 보여줘야
    "뭐라고 물어봤는지" 확인이라는 목적을 만족한다.
- 남은 것: 보관기간·자동삭제 정책은 아직 없다 — 지금은 개발/테스트 단계 전제다.
  실서비스 공개 전에는 9-3절(보관기간·동의 지점·삭제 요구 경로)을 이 컬럼에도
  적용할지 다시 결정해야 한다.
- 상세: `supabase/migrations/202608210004_add_feedback_turn_text.sql`,
  `backend/app/state/schema.py`(`FeedbackRecord`), `backend/app/state/feedback.py`,
  `frontend/src/components/chat/FeedbackButtons.tsx`,
  `frontend/src/utils/turnText.ts`

### D-069 — 피드백에 intent(그대로 복사)와 comment(싫어요 사유)를 추가한다

- 상태: `Accepted` — 구현 완료.
- 배경: D-068 구현 직후, 다른 제안(피드백 테이블에 intent/is_clarification/
  clarification_code/comment까지 함께 저장하고 모두 NOT NULL로 두자는 안)이
  들어왔다. 검토 결과 그대로 채택할 수 없는 부분과 그대로 채택 가능한 부분이
  섞여 있었다 — 아래 "채택하지 않은 것"에 이유를 정리한다.
- 결정:
  1. `intent`(선택, nullable) 추가 — 그 턴의 `assistant_text` 메시지가 이미
     들고 있는 값을 그대로 복사해 저장한다. B는 값을 검증하지 않는다
     (step/prompt_version과 같은 성격).
  2. `comment`(선택, nullable) 추가 — "싫어요" 사유를 사용자가 직접 남기는
     자유 텍스트. 클릭 즉시 전송하지 않고, 인라인 입력창(제출/건너뛰기)을
     먼저 띄운 뒤 하나의 레코드로 함께 보낸다 — append-only 구조에서 사유
     없이 먼저 보내고 나중에 사유 있는 레코드를 또 쌓으면 같은 run_id의
     dislike가 중복 집계되기 때문.
- 근거: intent는 이미 프론트가 들고 있는 값을 옮기기만 하면 되어 구현 부담이
  거의 없다. comment는 D-068과 같은 "테스트 중 피드백 검토 편의" 목적에
  직접 부합한다(사유가 있으면 왜 별로였는지 바로 보인다) — 다만 원 요청
  범위를 넘는 새 UI라 AskUserQuestion으로 사용자에게 직접 확인받았다.
- 채택하지 않은 것:
  - 모든 컬럼 NOT NULL — 프론트가 턴 텍스트를 못 찾는 예외 상황(레거시
    카드 재구성 경로 등)에서 텍스트만 비는 게 아니라 rating 기록 자체가
    통째로 실패하게 된다. nullable로 두고 텍스트는 "있으면 같이"가 더
    안전한 실패 방식이다(D-068과 동일한 판단).
  - `is_clarification`/`clarification_code` — 지금 구조에서는 되묻기
    (clarification) 메시지에 피드백 버튼 자체가 없다(추천/일정 결과
    카드에만 있음). 채울 방법이 없는 컬럼을 미리 만들지 않는다 — 되묻기
    메시지에도 피드백을 받고 싶어지면 그때 버튼 배선부터 다시 설계한다.
  - 프론트 `state.user_input` 등 reducer 최상위 상태를 그대로 사용 —
    세션에 하나뿐인 필드라 새 턴이 오면 덮어써진다. 화면을 스크롤해 예전
    카드에 피드백을 남기면 최신 턴의 값이 잘못 붙는다. 대신 카드 위치
    기준으로 거슬러 올라가 찾는 기존 `findTurnText`를 그대로 확장해 intent도
    같이 찾도록 했다.
- 병합 후기(같은 날 develop merge): 이 작업 직후 develop에 팀원이 독립적으로
  구현한 comment 기능(PR)이 먼저 merge됐다 — 같은 목적을 다른 방식으로
  구현한 우연한 충돌. 병합 시 develop 쪽을 기준으로 정리했다: comment 컬럼은
  develop의 마이그레이션(`202608210003_add_comment_to_response_feedback.sql`,
  DB단 500자 CHECK 제약 포함, pydantic max_length=500과 이중 방어)을 그대로
  채택하고 우리 쪽 comment 컬럼 추가는 제거, FeedbackButtons UI도 develop의
  아이콘 기반 버전(더 완성도 높음)을 채택해 우리 버전은 버렸다. intent만
  우리 쪽 추가로 남았다. 또한 develop이 `recommendation_result`/
  `schedule_result` 메시지에서 피드백 버튼을 분리해 별도 `"feedback"`
  ChatMessage 타입(카드 뒤에 이어지는 독립 메시지)으로 구조를 바꿔서,
  `findTurnText` 호출 지점도 결과 카드가 아니라 이 `"feedback"` 메시지의
  index로 옮겼다 — `findTurnText`는 텍스트가 없는 메시지를 건너뛰므로 로직
  변경은 필요 없었다. 마이그레이션 번호 충돌(둘 다 202608210002/003을 씀)로
  우리 쪽 파일은 004/005로 재번호했다.
- 정정(병합 후 발견): 위 "채택하지 않은 것"의 `is_clarification`/
  `clarification_code` 제외 근거("되묻기 메시지에 피드백 버튼 자체가
  없다")가 병합 이후로는 더 이상 사실이 아니다 — develop이 피드백을
  `run_id`만 있으면 붙는 범용 `"feedback"` 메시지로 바꾸면서, 백엔드가
  되묻기 응답에도 항상 `run_id`를 발급하기 때문에(`service.py` `apply()`,
  확정 여부와 무관하게 매 턴 발급) 되묻기 턴에도 피드백 버튼이 이미 뜨고
  rating은 저장되고 있었다. 다만 `findTurnText`가 답변 자리에서
  `"assistant_text"`만 찾고 `"clarification"` 타입은 인식하지 못해
  `assistant_message`가 계속 비어 있던 것을 확인해, `"clarification"`도
  답변으로 인식하도록 확장했다(`intent`는 clarification 메시지에 그 값 자체가
  없어 여전히 비어 있다 — `is_clarification`/`clarification_code` DB 컬럼을
  새로 만들지 않기로 한 결정 자체는 유지, 그 컬럼들을 채울 데이터가 필요해지면
  그때 다시 검토).
- 상세: `supabase/migrations/202608210005_add_feedback_intent.sql`,
  `backend/app/state/schema.py`(`FeedbackRecord`), `backend/app/state/feedback.py`,
  `backend/app/state/service.py`, `frontend/src/utils/turnText.ts`,
  `frontend/src/components/chat/FeedbackButtons.tsx`,
  `frontend/src/components/chat/ChatMessageList.tsx`

### D-070 — reason_code(구조화된 싫어요 사유) PR 검토·반영, 마이그레이션 뷰 버그 수정

- 상태: `Accepted` — 구현은 팀원(PR #214, `feature/llm-interpret`)이 완료해 develop에
  merge, B는 상태·Supabase 저장 계약 검토와 마이그레이션 적용을 요청받아 수행.
- 배경: D-069 병합 직후, 같은 팀원이 "싫어요" 사유를 자유 텍스트(comment)뿐 아니라
  집계 가능한 표준 코드(`reason_code`)로도 남길 수 있게 하는 기능을 독립적으로
  구현해 PR #214로 develop에 merge했다. PR 설명에서 B(상태·Supabase 저장 계약
  소유자)에게 두 가지를 요청 — ① 상태·저장 계약이 깨지지 않는지 검토, ②
  `202608210006_add_feedback_reason_code.sql` 마이그레이션을 실서비스 DB에 적용.
- 결정:
  1. `reason_code` 그대로 채택 — `FeedbackReasonCode` 7값 Literal(intent_mismatch/
     clarification_unhelpful/context_not_preserved/location_misunderstood/
     conditions_not_applied/recommendation_not_suitable/other)이 DB CHECK 제약과
     정확히 일치하고, `RecordFeedbackRequest` validator가 "좋아요"에는 reason_code/
     comment 둘 다 못 붙이게 막아 계약 위반 없음을 확인.
  2. `intent`/`user_input`/`assistant_message` 캡처 아키텍처는 이쪽(render-time
     `findTurnText`)을 그대로 유지 — PR #214가 자체적으로 시도했던 reducer 시점
     임베딩 방식(`ChatMessage` 생성 시 값을 미리 박아두는 방식)은 최종 merge에서
     채택되지 않았다. D-069에서 정리한 "스크롤해 예전 카드에 피드백을 남기면
     최신 턴 값이 잘못 붙는" 문제를 render-time 방식이 이미 해결한 상태라 되돌아갈
     이유가 없었다.
  3. 마이그레이션 적용 중 발견한 버그 수정 — `202608210006`의 `response_feedback_kst`
     뷰 재정의가 기존 컬럼 순서(`...user_input, assistant_message, intent...`)를
     깨고 `intent`를 앞으로 옮기려다 PostgreSQL의 `create or replace view` 제약
     (42P16: 기존 컬럼 이름·순서 변경 불가)에 걸려 실서비스 적용이 실패했다. 기존
     순서를 그대로 두고 `reason_code`를 맨 뒤에 추가하도록 수정. `begin`/`commit`으로
     감싸져 있어 실패 시 전체 롤백 — 부분 적용된 상태는 없었다.
- 근거: 표준 사유 코드는 "좋아요/싫어요 수만으로는 어떤 인텐트·응답에서 문제가
  생겼는지 분석할 수 없다"는 D-069 이후로도 유효했던 한계를 정확히 메운다. B
  자체 구현 없이 검토·인프라(마이그레이션) 역할만 맡는 것도 팀 내 실제 구현자와
  중복 작업하지 않는다는 원칙에 맞는다.
- 채택하지 않은 것: PR #214의 reducer-embedding 방식(intent/user_input/
  assistant_message를 `"feedback"` 메시지 생성 시점에 미리 채워두는 안) — 코드
  구조는 더 단순하지만 D-069가 이미 해결해둔 문제를 다시 열 이유가 없어 review
  단계에서 채택하지 않았다.
- 남은 것: 없음 — 계약 검토·마이그레이션 적용 모두 완료. `llmops-trace-contract-v1.md`
  §8-3에 계약 반영.
- 상세: `supabase/migrations/202608210006_add_feedback_reason_code.sql`,
  `backend/app/state/schema.py`(`FeedbackReasonCode`),
  `backend/app/state/service.py`(`RecordFeedbackRequest` validator),
  `frontend/src/components/chat/FeedbackButtons.tsx`

### D-071 — 이동시간 출발점(`travel_origin`) 필드를 신설해 "~~에서"와 "~~ 근처"를 구분한다

- 상태: `Accepted` — 코드 변경 완료, 실 LLM 검증·골드셋 회귀는 별도로 이어서 진행.
- 배경: "안국역에서 10분 안에 갈 수 있는 카페"(출발점=안국역)와 "안국역 근처에 10분
  안에 갈 수 있는 카페"(출발점=사용자 위치)는 이동시간을 재는 기준점이 다르다.
  그런데 `location_rules.md`가 "~~ 근처/주변/가려는데"와 지명 단독을 전부
  `search_center` 하나로 묶어, 두 발화가 완전히 같은 조건(`search_center="안국역"`)
  으로 추출됐다 — 구분할 필드 자체가 없었다. D-067(랭킹 기준점을 검색 기준점에서
  사용자 위치로 이관)은 이 구분이 없는 상태에서 어느 기본값이 더 흔한 케이스를
  맞추는지 고른 결정이라, "~~ 근처"는 맞고 "~~에서"는 틀린 채로 남아 있었다(그
  전에는 반대였다). 구분이 없는 한 어느 기본값을 골라도 한쪽은 항상 틀린다.
- 검토 과정: 애매한 발화를 되묻기 버튼으로 확인할지, 조건 필드로 자동 추출할지를
  먼저 나눠 검토했다. 발화를 세 유형으로 분류했다 — ① 조사로 이미 확정("~에서/
  까지", 되물을 필요 없음), ② 기본값으로 충분("근처/주변", 골드셋의 관련 사례
  전부 이 유형이라 D-067 그대로 맞다), ③ 진짜 애매(조사 없는 "안국역 10분 거리"
  등, 소수). ①은 필드로 조용히 처리하고 ③만 비차단형 전환 버튼(답을 먼저 준 뒤
  "OO 기준으로 다시 보기") 대상으로 남기기로 했다 — 버튼을 전체 애매 케이스에
  걸면 흔한 ②까지 매번 눌러야 하는 게 되고, 필드를 전체에 걸면 판정 못 하는 소수
  케이스까지 억지로 값을 채우게 된다.
- 결정:
  1. `UserConditions`(A)에 `travel_origin: Literal["user_location",
     "search_center"] | None` 신설(`app.schemas.TravelOrigin`). "~~에서/까지
     N분"처럼 조사가 출발점을 확정하는 발화만 `"search_center"`로 채우고, 그 외
     (근처/주변, 조사 없는 발화, max_travel_time 미언급)는 null로 둔다 — null이면
     기존 D-067 기본값이 그대로 적용된다. `"user_location"`은 추출 단계에서는
     쓰지 않고, 위 비차단형 전환 버튼이 실제로 생기면 그 전환에 쓸 자리로
     미리 만들어 둔 값이다.
  2. `domain/ranking_origin.py::resolve_ranking_origin()`이 `travel_origin`이
     `SEARCH_CENTER`면 검색 기준점을 그대로 랭킹 기준점으로 쓰고, 그 외에는
     기존 사용자 위치 우선 규칙(D-067)을 그대로 따른다.
  3. `recommendation_pipeline.py::_distance_denominator_offset_km()`도
     `travel_origin=SEARCH_CENTER`면 0.0을 반환하도록 맞췄다 — 이때는 분자도
     검색 기준점 기준으로 재므로(2) 사용자→기준점 거리를 분모에 얹을 이유가
     없다.
  4. `state_transform.py`의 soft reset 시 `search_center`를 복원하는 기존
     로직(대학로 근처 → 카페 추천해줘류)에, `travel_origin`도 함께 복원하는
     로직을 추가했다 — 같은 장소가 이어지는 한 그 장소를 어떻게 쓸지의 판정도
     함께 이어져야 한다. 안 그러면 "안국역에서 10분" 다음 턴 "그럼 조용한
     데로"에서 `search_center`만 복원되고 `travel_origin`은 초기화돼 기준점이
     도로 사용자 위치로 바뀐다.
  5. C(`app.agent_context.schemas.UserConditions`)에는 이 필드를 추가하지
     않았다 — C는 위치 문자열을 좌표로 해석하는 역할만 하고 랭킹 판정에는
     관여하지 않으며, `context_transform.to_agent_context_request()`가 C가
     모르는 필드를 자동으로 걸러내므로(과거 `concentration_intent` 과도기와
     같은 방식) 추가하지 않아도 안전하게 무시된다.
- 채택하지 않은 것:
  - **되묻기 버튼으로 전부 확인** — ②(근처/주변, 골드셋 대부분)까지 매번 눌러야
    해서 버튼 피로가 커진다.
  - **`UserConditions`가 아니라 턴 한정(non-persistent) 페이로드에 담기** —
    처음엔 B 계약(field_spec.py) 변경 부담을 피하려고 이 방향을 검토했으나,
    실제로는 이번 턴 추출값이 recommendation_pipeline에 도달하기 전에 반드시
    `state_transform.py`/B 영속 상태를 통과하는 구조라(`RecommendPayload.
    conditions`가 곧 B 상태 스냅샷) 턴 한정 경로 자체가 존재하지 않았다.
  - **필드만 먼저 넣고 추출 규칙은 나중에** — `taste_query`가 겪은 "채워지기만
    하고 아무도 읽지 않는 필드" 패턴(1.0.17 HISTORY 참고)과 같은 실수를 막기
    위해 스키마·상태 배선·프롬프트 규칙을 한 번에 반영했다.
- 검증: pytest 2283건 통과(`test_ranking_origin.py`·`test_state_transform.py`
  신규 케이스 포함), 프롬프트 스냅샷 갱신, ruff 통과. 골드셋에 "~~에서/까지"
  패턴 사례가 없어(`test_results/intent_classification_results.csv` 확인)
  `scripts/verify_travel_origin_extraction.py`로 신규 발화 8건을 만들어
  `gemini-3.5-flash-lite` 2회 실행 16/16 통과 — 조사 확정 3건 전부
  `search_center`, 근처/주변/가려는데·조사 없음·시간 미언급 5건 전부 null.
- 남은 것:
  - 비차단형 전환 버튼(③ 대상)은 이번 범위에 없다. 필요해지면 그때 프론트
    작업으로 착수한다.
  - MODIFY(조건 변경) 경로에서 `travel_origin`을 사용자가 직접 바꾸는 발화는
    아직 다루지 않았다(`_changed_field_operations()`의 `changed_fields`에
    LLM이 이 필드를 넣을 상황을 아직 검토하지 않음).
- 상세: `backend/app/schemas.py`(`TravelOrigin`, `UserConditions.travel_origin`),
  `backend/app/state/schema.py`, `backend/app/state/field_spec.py`,
  `backend/app/services/interpret/state_transform.py`,
  `backend/app/domain/ranking_origin.py`, `backend/app/services/
  recommendation_pipeline.py`, `backend/app/services/runtime/agent_runtime.py`,
  `backend/app/services/runtime/tool_debug.py`, `backend/app/services/runtime/
  real_recommendation_provider.py`, `backend/app/domain/candidate_mapper.py`,
  `backend/app/prompts/recommend/location_rules.md`(2.2.0 → 2.3.0),
  `backend/docs/package-b/agent-state-contract-v1.md`,
  `docs/design/conditions-schema.md`

### D-072 — 인텐트 라우팅과 추천 파이프라인을 LangGraph 그래프로 옮긴다

- 상태: `Implemented` — 코드 이관 완료, `langgraph-adoption.md` §10.3 병합 판정 기준
  6개 전부 통과.
- 배경: `run_agent_flow()` 한 함수가 1,227줄이었고 그 안에 인텐트 분기 40개가
  중첩 if/elif로 들어 있었다. 강의 교재 61·91강 기준으로 "처음부터 설계했다면
  LangGraph가 맞았는가"를 코드 실측으로 판단했고, 결론은 맞다였다 — 우리는 이미
  "코드가 라우팅을 못 박는 명시적 그래프" 편에 서 있었고, 그 판단을 표현할 도구만
  없었다. 로드맵 16번(AI Agent 도구 경험)의 실행이기도 하다.
- 결정:
  1. 그래프를 **두 개**로 나눈다. 조기 반환 그래프(Tool·Scoring 없이 끝나는 턴)와
     추천 파이프라인 그래프(tool_fetch → scoring → schedule/finalize).
  2. **인텐트 분류와 조건 병합은 그래프 밖에 남긴다.** 이 구간은 B 계약
     (`agent-state-contract-v1.md`)의 소유이고, 그래프로 끌어들이면 조건 병합의
     Add/Update/Remove 의미론 소유권이 프레임워크로 넘어간다.
  3. **SSE는 sink를 `RunnableConfig`로 주입해 노드가 직접 호출한다.** 0단계
     스파이크에서 `astream_events` 번역 방식과 비교해 정한 것으로, `message_delta`는
     노드 경계가 아니라 노드 *내부*에서 발생해 노드 단위 이벤트로는 재현할 수 없다.
     노드의 `config` 파라미터는 반드시 `RunnableConfig`로 어노테이션해야 주입된다.
  4. **checkpointer는 쓰지 않는다.** 아래 참고.
  5. 기능 플래그 2개(`use_langgraph_early_return`/`use_langgraph_pipeline`, 기본
     `True`)를 롤백 스위치로 남기되 영구히 두지 않는다 — 같은 로직이 두 벌 남는
     비용이 있어, 실사용에서 문제없음을 확인한 뒤 기존 경로와 함께 걷어낸다.
- 채택하지 않은 것:
  - **checkpointer(`MemorySaver` 및 `StateStore` 어댑터)** — 검토 문서 v1.0은
    "`session_id`가 곧 `thread_id`, `StateStore`가 곧 `BaseCheckpointSaver`"라고
    적었으나 **틀렸다.** `StateStore`는 조건·이력·Trace를 담는 도메인 저장소이고
    checkpointer는 그래프 재개용 스냅샷이라, 갈아끼우면 조건 병합 소유권이 B에서
    그래프로 넘어간다. 게다가 붙여둔 `MemorySaver`는 실측 결과 (a) 같은
    `thread_id`의 다음 턴에 이전 턴 값이 남고 (b) 세션 6개에 체크포인트 21건이
    RAM에 쌓여 줄지 않았다. 우리 그래프는 한 턴에 시작하고 끝나 보관함이 할 일이
    없다. 떼어냈고 `test_graphs_have_no_checkpointer`로 재발을 막았다.
  - **인텐트별 노드 7개로 팬아웃** — 계획 단계의 전제가 틀렸다. 분기 40개가 전부
    조기 반환 *앞*에 있어서, 그 뒤는 갈라지는 흐름이 아니라 순차 파이프라인이었다.
    조건부 엣지는 "중간에 끝나는가"와 "SCHEDULE인가" 두 판정에만 쓴다.
  - **한 번에 전면 재작성** — 단계별 커밋(0~3단계)로 쪼개 되돌릴 지점을 남겼다.
- 검증: pytest 2,323건 통과(그래프 테스트 18건 신규, develop 동기화 포함), ruff 통과, 프론트엔드 변경
  **0줄**. 같은 발화를 플래그 ON/OFF로 돌려 최종 응답 JSON 전체와 SSE 이벤트 순서를
  비교하는 차등 테스트를 13개 케이스에 대해 수행 — 전부 일치. 실제 Provider에서 2건이
  달랐으나 **그래프를 켜지 않고 기존 경로만 두 번 돌려도 같은 2건이 달라져** 외부 API
  잡음으로 판정했다.
  되묻기 재진입도 프론트가 보내는 형태(버튼 라벨을 `user_input`, 버튼 id를
  `clarification_choice`)를 그대로 흉내 내 5단계 시나리오로 비교 — 차이 0건.
  응답 지연은 ON/OFF 번갈아 12회씩 측정해 **호출당 약 1ms 고정 오버헤드**를 확인했다
  (외부 호출이 붙는 RECOMMEND는 428ms→439ms, SCHEDULE은 오히려 426ms→422ms로 잡음
  범위). 실 LLM이 붙으면 응답이 초 단위라 체감되지 않는다.
- 남은 것:
  - 기능 플래그와 기존 경로 제거(위 결정 5).
  - `langgraph`가 새 의존성이라 팀원 각자 재설치 필요. `npm run dev`는 PATH의
    `python`을 그대로 쓰므로 가상환경 밖에도 설치돼 있어야 백엔드가 뜬다.
- 곁가지로 드러난 기존 문제(이 결정 범위 밖): `.env`가 개별 Provider 키를 지정해
  `PROVIDER_MODE=fake`를 무력화한다. `settings.fake_current_datetime`은 정의만 있고
  참조가 0건이며 주석이 가리키는 `app/core/clock.py`는 존재하지 않는다.
- 상세: `docs/design/langgraph-adoption.md`(§9.6~§9.10, §10.3~§10.6),
  `backend/app/services/runtime/graph/`(8개 파일),
  `backend/app/services/runtime/stream_events.py`,
  `backend/app/services/runtime/agent_runtime.py`, `backend/app/config.py`,
  `backend/pyproject.toml`, `backend/tests/graph/`


### D-073 — 인증된 요청에 한해 세션 소유권을 대조한다 (D-063 결정 2 후속)

- 상태: `Accepted` — 구현 완료.
- 배경: TP-101 3단계로 인증된 신원(`user_id`)을 `agent_states`에 저장하는
  배선은 끝났지만, 저장된 `user_id`와 요청을 보낸 `Principal`을 대조하는
  검증은 없었다 — `session_id`만 알면(추측·유출) 다른 사용자의 세션을
  그대로 조회·수정·삭제할 수 있었다. D-063 결정 2는 이 검증을 "Phase
  4(인증 필수화)로 미룬다"고 명시했는데, Phase 4 자체의 착수 시점(토큰
  없는 요청 비율 임계값)이 아직 정해지지 않아 이 항목도 함께 멈춰 있었다.
  실제로 `routes/state.py`의 GET/DELETE 라우트는 `principal` 파라미터를
  이미 선언해두고도 서비스 함수에 넘기지 않는 상태였다 — Phase 4를 염두에
  두고 자리만 만들어 둔 흔적으로 보인다.
- 결정: Phase 4 전면 필수화를 기다리지 않고, **`Principal`이 있는 요청에
  한해서만** 소유권을 대조하는 범위로 먼저 닫는다.
  1. `session.verify_ownership(state, principal)` 신설 — `principal`이
     없으면(토큰 미전송) 통과, `state.user_id`가 비어 있으면(아직 아무도
     신원을 붙이지 않음) 통과, 값이 있는데 다르면 `SessionOwnershipError`
     (403, `session_ownership_mismatch`)를 던진다.
  2. 세션을 확보·조회·삭제하는 세 진입점에 배선 — `apply()`(세션 확보
     직후, `attach_user_id()`보다 먼저), `get_session_context()`(조건·
     이력·GPS까지 노출하는 읽기 경로), `delete_session()`(되돌릴 수 없는
     삭제 경로). `ensure_current_context()`(interpret 흐름의 1단계 컨텍스트
     확보, `apply()`보다 먼저 실행됨)와 각 라우트(`routes/state.py`,
     `routes/interpret.py`, `routes/recommendations.py`)까지 `principal`을
     끝까지 흘려보냈다.
  3. `record_recommendation`/`record_trace`/`set_last_intent` 등 나머지
     진입점은 이번에 손대지 않았다 — 전부 `agent_runtime.py`의 같은 요청
     흐름 안에서 `apply()`가 이미 통과시킨 `session_id`만 이어받아 호출되고
     있어(같은 턴 안에서 세션을 바꿔 부르지 않음), 독립적으로 재노출되는
     경로가 아니다.
- 근거: 401(신원 자체가 무효)과 403(신원은 유효하지만 이 세션 권한 없음)을
  구분해, 이미 있는 `_unauthorized()`(401) 패턴을 재사용하지 않고 별도
  오류 클래스를 뒀다 — 사유가 다르면 상태 코드도 달라야 클라이언트가
  구분해 대응할 수 있다.
- 채택하지 않은 것:
  - **Phase 4 전면 필수화를 함께 착수** — 이 카드의 범위를 넘는다. 토큰
    없는 요청 비율 임계값(guest-auth-design.md 5절)이 아직 정해지지
    않았고, 필수화되면 모든 라우트가 `RequiredPrincipal`로 바뀌어야 해서
    범위가 전혀 다른 작업이다.
  - **모든 B 진입점에 동일하게 배선** — `record_recommendation` 등은
    `apply()` 뒤에만 호출되는 내부 체인이라, 거기까지 대조를 넣으면 같은
    검사를 같은 요청 안에서 중복 수행하게 된다. 독립적으로 HTTP에
    노출되는 경로가 생기면 그때 같은 패턴(`verify_ownership` 호출 추가)을
    그대로 적용한다.
- 남은 것: Phase 4 필수화 시점 자체는 여전히 미정(guest-auth-design.md
  5절 열린 질문). 필수화되면 이 로직이 새 전제(신원 항상 존재)와
  일관되는지 재확인 필요 — 지금 로직은 `principal is None`을 그대로
  통과시키므로 Phase 4 전환 자체와 충돌하지는 않는다.
- 상세: `backend/app/state/errors.py`(`SessionOwnershipError`),
  `backend/app/state/session.py`(`verify_ownership`),
  `backend/app/state/service.py`(`apply`/`get_session_context`/
  `delete_session`), `backend/app/services/interpret/session_orchestrator.py`
  (`ensure_current_context`), `backend/app/services/runtime/agent_runtime.py`,
  `backend/app/routes/state.py`, `backend/app/routes/interpret.py`,
  `backend/app/routes/recommendations.py`,
  `docs/design/guest-auth-design.md`, `backend/docs/package-b/
  agent-state-contract-v1.md`

### D-074 — 만료된 익명 세션·이력을 30일 기준으로 정리한다 (TP-134)

- 상태: `Accepted` — 구현 완료.
- **정정(2026-09-08)**: 아래 결정 2의 대상 테이블 목록이 낡았다. 그때는 네
  테이블이었지만 지금 `_delete_one()`은 **여섯**을 지운다 —
  `session_messages`(TP-222 후속 화면 기록)와 `saved_places`(SCHEDULE-12 보관함)가
  뒤에 세션 수명에 묶였다. 삭제 순서도 `condition_change_logs` → `trace_records`
  → `session_messages` → `recommendation_histories` → `saved_places` →
  `agent_states`로 늘었고, `agent_states`를 마지막에 둔다는 원칙(결정 3)은
  그대로다. **`session_messages`가 들어온 것이 이 결정의 무게를 바꿨다** — 그
  테이블에는 사용자 원문 발화와 화면에 나간 응답이 담기므로(계약 5.6절
  "화면 기록은 원문 금지의 예외다"), 기본 30일은 이제 저장 용량 정책이 아니라
  **사실상의 개인정보 보관 기간**이다. 값 자체는 그대로 두고 성격만 정정한다.
  결정 2의 `response_feedback` 제외는 유효하다.
- 배경: 세션 TTL(30분, `session.py::SESSION_TTL`)은 그 세션이 다시 조회될 때만
  상태를 `expired`로 바꾸는 lazy 판정이라, 실제 행을 지우지 않는다.
  `agent_states`/`recommendation_histories`/`condition_change_logs`/
  `trace_records` 네 테이블이 무기한 쌓이고, 뒤의 두 append-only 테이블은
  세션이 오래 쓰일수록 계속 커진다. `agent-state-contract-v1.md`는 "Phase
  1에서는 만료된 세션 데이터를 즉시 삭제하지 않는다"고 이미 명시해 이후
  단계에서 정리 메커니즘이 필요함을 예고해뒀다. `guest-auth-design.md` 10절은
  "오래된 익명 사용자 정리 스케줄(예: 30일 미접속 삭제)"을 열린 과제로
  남겼고, D-063 결정 4는 `agent_states.user_id`에 FK를 걸지 않은 이유로 이
  정리와의 충돌을 들었다.
- 결정:
  1. 기준: `agent_states.last_active_at`이 기준 일수(기본 30일, `--days`로
     조정 가능)보다 오래되면 정리 대상.
  2. 대상 4개 테이블: `agent_states`/`recommendation_histories`/
     `condition_change_logs`/`trace_records`. `response_feedback`은 세션
     생애주기와 무관한 별도 분석 데이터라 제외.
  3. 삭제 순서: `condition_change_logs` → `trace_records` →
     `recommendation_histories` → `agent_states`. `agent_states`를 마지막에
     지우는 이유는, 도중 실패해도 `agent_states`가 남아 있으면
     `list_stale_session_ids`가 다음 실행에서 같은 세션을 다시 찾아내
     재시도할 수 있기 때문이다 — `agent_states`를 먼저 지우면 나머지 3개
     테이블 행이 영원히 못 찾는 고아가 된다.
  4. 실행 방식: Supabase pg_cron이 아니라 `backend/scripts/
     cleanup_expired_sessions.py` 수동/외부 스케줄 스크립트로 구현. 지금
     트래픽 규모에서 pg_cron 확장 활성화·SQL 작성 비용 대비 얻는 이득이
     작다고 판단(팀 확인 완료).
  5. Supabase 익명 계정(`auth.users`) 정리와의 관계: 이번 범위에서는 다루지
     않는다. FK가 없어(D-063 결정 4) 두 정리가 서로 의존하지 않고 독립적으로
     실행 가능하다 — `agent_states` 쪽을 먼저 정리해도, `auth.users` 쪽을
     먼저 정리해도 서로의 무결성을 깨지 않는다. `auth.users` 정리는 Supabase
     Admin API(service role) 접근이 필요해 별도 작업으로 분리했다.
- 근거: append-only 테이블(`condition_change_logs`/`trace_records`)의
  "수정·삭제 없음" 원칙은 개별 레코드를 골라 고쳐 이력을 조작하지 못하게
  막는 것이 목적이지, 세션이 통째로 만료된 뒤에도 무기한 보관해야 한다는
  뜻은 아니다 — 이번에 추가한 `delete_change_logs`/`delete_traces`는 세션
  단위 일괄 삭제만 제공하고, 개별 레코드를 골라 수정·삭제하는 경로는
  여전히 없다.
- 채택하지 않은 것:
  - **Supabase pg_cron으로 DB 안에서 자동 실행** — 서버 없이도 동작하는
    장점은 있지만, pg_cron 확장 활성화와 별도 SQL 작성이 필요해 지금
    규모에서는 비용 대비 이득이 작다. 필요해지면 정리 로직을 SQL로 옮기는
    것 자체는 어렵지 않다.
  - **`auth.users` 익명 계정 정리를 같은 작업에서 함께 구현** — Admin API
    접근 권한 확보와 배포 환경 설정이 별도로 필요해 범위를 분리했다. FK가
    없어 두 정리가 서로 막지 않으므로 순서를 강제할 필요도 없다.
- 남은 것: 실제 운영에서 스크립트를 얼마나 자주 돌릴지(수동 vs cron
  자동화)는 트래픽이 늘어난 뒤 다시 판단한다. `auth.users` 정리는 별도
  카드로 분리 검토.
- 상세: `backend/app/state/store.py`(`list_stale_session_ids`/
  `delete_change_logs`/`delete_traces`), `backend/app/state/supabase_store.py`,
  `backend/scripts/cleanup_expired_sessions.py`, `docs/design/
  guest-auth-design.md` 10절, `backend/docs/package-b/
  agent-state-contract-v1.md`

### D-075 — LLM 실행 기록은 갈아끼우지 않고 같은 리스트에 붙인다

- 상태: `Implemented`
- **번호 정정(2026-08-24)**: 이 항목과 아래 D-075는 처음 D-073·D-074로 적었다.
  같은 날 develop에 세션 소유권 검증이 D-073으로 먼저 자리 잡아(PR #227) 번호가
  겹쳤고, develop 쪽이 코드·계약 문서 10곳에서 이미 참조되고 있어 우리 번호를
  하나씩 미뤘다. **PR #226 본문과 지라 TP-133은 옛 번호(D-073)로 적혀 있다** —
  그 문서들이 가리키는 것은 이 항목이다.
- 배경: D-072 이관 후 팀 검토에서 **개발자 감사 패널의 LLM 정보가 사라지는 것**이
  발견됐다. 원인은 `llm_execution.py`의 `_calls` ContextVar를 **값 교체**로
  갱신했다는 것이다(`_calls.set((*_calls.get(), call))`). LangGraph는 노드를 별도
  asyncio 태스크에서 돌리고, 파이썬은 태스크 생성 시 ContextVar 값을 **복사해서**
  넘긴다. 그래서 노드 안에서 교체한 값은 복사본만 갈리고 노드가 끝나면 버려졌다.
  태스크 경계는 값을 안으로 들여보내지만 밖으로 내보내지 않는다.
- 실측으로 확인한 유실(2026-08-24):
  1. 조기 반환 경로(GENERAL·OUT_OF_SCOPE·되묻기) 정상 응답의 `llm_execution`이
     통째로 `None`. 감사 패널의 "LLM 응답 모델"이 빈칸이 되고, "LLM 폴백"은
     `llmExecution?.calls.some(...)`이 `undefined`로 떨어져 **틀린 "없음"**을 찍는다.
     빈칸은 "모르겠다"로, "없음"은 "확인했고 안 일어났다"로 읽히므로 후자가 더 나쁘다.
  2. 노드 안에서 LLM이 실패하면 시도 모델 목록이 502 응답 본문의
     `details.llm_execution`에서 빠진다(단발 `POST /api/chat`·`/api/agent-debug`
     한정 — SSE 경로는 제너레이터가 `AppError`를 자체 처리해 이 값을 원래 안 싣는다).
  3. 추천 파이프라인은 앞 노드 기록을 뒤 노드가 못 읽는다. 지금은 `tool_fetch`·
     `scoring`이 LLM을 안 불러 잃을 것이 0건이지만, 앞 노드에 호출이 하나 생기면
     그 줄만 조용히 빠진다(예외·로그·테스트 실패 없음).
- 결정: `_calls`가 **리스트를 담고**, `reset_llm_execution_metadata()`에서만 새
  리스트를 넣고, `record_llm_call()`은 그 리스트에 `append`한다. 태스크가 복사해
  가는 것은 리스트 참조이므로 노드 안에서 붙인 항목이 노드 밖에서도 보인다.
- 채택하지 않은 것:
  - **기본값(`default`) 제거** — 검토 문서의 제안이었으나 그대로 하면 새 회귀가
    생긴다. `main.py`의 `handle_app_error()`는 **전역** `AppError` 핸들러이고,
    `/api/transcribe`·`/api/dev/*`·`/chat/place-details`처럼 `run_agent()`를 거치지
    않는(따라서 reset을 부르지 않는) 라우트도 이 핸들러를 탄다. 기본값이 없으면
    거기서 `LookupError`가 나 **502 계약이 500 미처리 예외로 깨진다.** 대신 기본값을
    불변 센티널 `None`으로 두고 `record_llm_call()`이 첫 호출에서 리스트를 만든다.
  - **기본값에 리스트를 두기** — 모든 요청이 같은 리스트를 공유해 이력이 섞인다.
    ContextVar를 쓰는 목적 자체가 깨지므로 불변 센티널이어야 한다.
  - **노드가 기록을 상태(state)로 반환해 리듀서로 병합** — 노드를 얇게 유지한다는
    D-072 원칙과 어긋나고(노드마다 기록 수집 코드가 붙는다), 소비처가 관측 전용
    필드 하나뿐인데 서류철 칸을 늘리는 값이 없다.
- 검증: pytest **2,332건 통과**(신규 9건), ruff 통과, 플래그 끈 상태도 동일.
  신규 테스트는 **`record_llm_call()`을 실제로 부르는 LLM 더블**로 돈다 —
  `FakeLLMProvider`는 이 함수를 부르지 않아 Fake로 쓰면 수정 전에도 통과해버린다.
  수정을 되돌려 그중 4건이 실제로 실패하는 것을 확인했다.
- 이 문제를 기존 검증이 못 잡은 이유: `record_llm_call()`을 부르는 것은
  `RealGeminiProvider` 하나뿐인데, `tests/conftest.py`가 모든 테스트에서 Fake를
  강제하고 `scripts/compare_langgraph_parity.py`도 `LLM_PROVIDER=fake`를 무조건
  지정한다(`--real`에서도). 그래서 D-072의 "차등 비교 전부 일치"는 이 필드에
  관해서는 **양쪽 모두 `None`이었던** 비교였다. `tests/`에 `llm_execution`을
  단정하는 테스트가 0건이었던 것도 같은 원인이다. CLAUDE.local.md가 "조용한 fake"로
  적어둔 실패 유형과 같다 — Fake가 소비 측이 읽는 값을 비워두면, 테스트는 통과하는데
  검증하려던 로직은 실행되지 않는다.
- 함께 기록한 것(수정 아님): `_score_recommendations()`가 `tool_executions` 리스트를
  제자리에서 추가하는데 `scoring_node`는 그 키를 반환하지 않는다. checkpointer가
  없어 LangGraph가 같은 리스트 객체를 그대로 넘기기 때문에 지금은 `finalize_node`가
  추가 항목을 정상적으로 읽는다. **checkpointer를 달면 이 결합이 깨진다** — D-072가
  checkpointer를 쓰지 않는 이유가 하나 더 있는 셈이고, 깨질 때 망가지는 것도 같은
  감사 패널(Tool 호출 목록의 외부 API 호출 수)이다.
- 상세: `backend/app/services/runtime/llm_execution.py`,
  `backend/tests/graph/test_llm_execution_across_nodes.py`,
  `docs/design/langgraph-adoption.md` §9.13

### D-076 — thinking 끄기는 모델 목록으로 포기하지 않고 `thinking_level`로 바꿔 보낸다

- 상태: `Implemented`
- 배경: `thinking_budget=0`을 거부하는 모델 목록(`_REJECTS_ZERO_THINKING_BUDGET`,
  D-052 계열 eae832f)에 걸리면 `thinking_config`를 **아예 싣지 않았다.** 400
  INVALID_ARGUMENT는 비재시도라 폴백도 못 타고 즉시 죽으므로 그 자체는 옳은 방어였다.
  그런데 2026-08-18에 두 가지가 겹쳤다 — (a) `_thinking_config_for()`가 0을 숫자가
  아니라 `thinking_level=MINIMAL`로 바꿔 보내게 되고(e3a9e2e), (b) fast 모델이
  `gemini-3.5-flash-lite`(그 목록에 있는 모델)로 바뀌었다(89b5bdf). 그 결과
  `classify_intent`·`extract_recommend_conditions`의 thinking 끄기가 **조용히
  무효화**됐다. 코드가 아니라 모델만 바뀐 것이라 아무도 알아채지 못했고, 발견까지
  6일이 걸렸다.
- 실 API 실측(2026-08-24, 모델 5개 × 설정 4개 × 3회):
  - 거부되는 것은 **숫자 `0`뿐**이다 — `thinking_budget=512`는 다섯 모델 전부 성공,
    `thinking_level=MINIMAL`은 3.x 세대 전부 성공.
  - `thinking_level=MINIMAL`은 이름만 최소가 아니라 **실제로 생각 토큰 0**이다
    (두 방식이 다 되는 `gemini-3.5-flash`에서 `예산=0`과 동일하게 0, 설정 없으면 377).
  - 근거 데이터: `backend/test_results/gemini_thinking_matrix_2026-08-24/`,
    서술은 `docs/실험-Gemini-thinking-설정-20260824.md`.
- 결정: `_resolve_thinking_budget()`에서 그 목록을 근거로 `None`을 돌려주던 분기를
  없앤다. `0`은 `_thinking_config_for()`가 항상 `thinking_level=MINIMAL`로 바꿔
  보내므로 400이 날 입력을 애초에 만들지 않는다. **목록 자체는 지우지 않는다** —
  실측으로 얻은 사실이고, 그 사실을 지키는 불변식
  (`test_zero_budget_is_never_sent_as_a_number`)이 이 상수를 직접 읽어 검증한다.
  모델이 늘어나면 목록에만 추가하면 테스트가 따라온다.
- 채택하지 않은 것:
  - **`_REJECTS_ZERO_THINKING_BUDGET`과 `_MODEL_BUDGET_OVERRIDES` 삭제** — 둘 다
    실측 근거로 들어온 값이라 남긴다. `_MODEL_BUDGET_OVERRIDES`의
    `gemini-2.5-flash-lite × classify_intent = 512`는 지금 도달할 경로가 없다(그 모델을
    쓰지 않는다). 그래도 지우지 않고 문서에 "현재 미사용" 단서만 붙였다 — 폴백으로
    되살릴 때 같은 실험을 다시 하지 않기 위해서다.
  - **숫자 예산 자체를 막기** — 검토 중 제안됐으나 실측이 반대였다. `512`는 거부
    모델에서도 정상이다. 막으면 되는 것을 막는다.
  - **지연 개선을 근거로 삼기** — 실측하면 이득이 없다. `classify_intent` 15회 중앙값
    958ms → 949ms(-0.9%). `gemini-3.5-flash-lite`는 설정을 안 해도 생각 토큰이 0인
    모델이라 끌 것이 없었다. **6회만 재면 -17%·-7%까지 나오지만 15회로 늘리면
    사라진다** — 표본이 적을 때 없는 효과를 있다고 읽은 사례로 남긴다. 이 결정의
    근거는 속도가 아니라 "모델을 바꾸는 순간 최적화가 조용히 사라지는 구조"의 제거다
    (기본 thinking이 무거운 `gemini-3.6-flash`는 설정 없음 3,518ms vs MINIMAL 1,416ms).
- 함께 정리한 것: Gemini 키 교체로 `gemini-2.5-*`를 쓰지 않게 됐는데 문서·스크립트에
  남아 있던 참조를 정리했다. 특히 `development-guide.md`와 `llm-hyperparameters.md`가
  **폐지된 `LLM_MODEL_NAME`을 현행 설정으로 안내**하고 있었다 — 그대로 따라 `.env`를
  쓰면 부팅에서 막힌다(D-042). 역할별 설정 4개로 교체했다.
- 남은 것(별건): 지금 코드는 `0`을 **항상** `thinking_level`로 보내는데, `gemini-2.5`
  세대는 그 방식을 거부한다(실측 확인). 옛 모델을 폴백으로 되살리면 분류를 뺀 호출이
  전부 400으로 죽는다 — 이번 문제와 방향만 반대인 같은 함정이다. 해결 형태는 검증해
  뒀다(목록을 "포기 조건"이 아니라 "어느 방식을 보낼지 고르는 기준"으로 쓰면 다섯 모델
  전부 통과). 지금은 옛 모델을 쓰지 않아 당장 아프지 않으므로 이번 범위에서 제외했다.
- 상세: `backend/app/providers/gemini.py`, `backend/tests/test_gemini_provider.py`,
  `backend/scripts/measure_fast_thinking_level.py`,
  `docs/실험-Gemini-thinking-설정-20260824.md`, `docs/design/llm-hyperparameters.md` §4.1

### D-077 — 무장애 여행 정보는 places 컬럼이 아니라 전용 테이블에 담는다

- 상태: `Implemented`
- 배경: places의 접근성 관련 컬럼은 `restroom_raw`(일반 화장실)·`baby_carriage_raw`
  둘뿐이고, 그마저 detailIntro2가 거의 채우지 않는다(종로구 무장애 등록 181건에서
  `restroom_raw`는 10건). "휠체어로 들어갈 수 있나요", "장애인 화장실 있나요" 같은
  INFO 질문에 답할 근거가 없었다. 한국관광공사가 같은 인증키로 무장애 여행 정보를
  따로 제공한다(`KorWithService2`) — 별도 활용신청 없이 기존
  `TOUR_API_SERVICE_KEY`로 조회된다(2026-08-25 확인).
- 실측(2026-08-25, 4개 구 전수):
  - 무장애 정보가 등록된 장소는 496건이다 — 종로 181/842, 중구 159/892,
    용산 118/486, 성동 38/350. places 전체의 19%다.
  - 목록(`areaBasedList2`)에 없는 장소에 `detailWithTour2`를 부르면 `totalCount: 0`이
    온다. 목록으로 먼저 좁히면 종로구 기준 842회가 아니라 182회로 끝난다.
  - 응답 필드 28개 중 채움률 5%를 넘긴 것은 15개다(숙박을 뺀 427건 기준).
    `route` 64.9% · `exit` 62.1% · `restroom` 52.2% · `parking` 47.1% ·
    `elevator` 42.2% · `handicapetc` 22.2% · `braileblock` 19.7% ·
    `wheelchair` 16.9% · `publictransport` 13.6% · `stroller` 13.6% ·
    `infantsfamilyetc` 13.1% · `lactationroom` 12.4% · `brailepromotion` 10.5% ·
    `audioguide` 9.6% · `helpdog` 9.1%.
  - 목록에 있는데 15개 필드가 전부 빈 장소가 496건 중 60건이다.
- 결정 1 — **전용 테이블(`place_barrier_free`)로 나눈다.** places 컬럼으로 붙이면
  39 → 54컬럼이 되는데 행의 81%가 전부 null이고, 무엇보다 동기화 계보가 다르다.
  대상 목록이 다른 엔드포인트에서 오므로 `places.detail_fetch_status`(detailIntro2
  조회 상태)에 얹으면 한 컬럼이 서로 다른 두 조회를 뜻하게 된다.
- 결정 2 — **컬럼 이름은 응답 키가 아니라 의미로 짓는다.** 두 필드가 이름과 반대로
  읽히기 때문이다. `wheelchair`는 휠체어 출입이 아니라 **대여**이고("대여 가능
  (1대/안내데스크)"), `exit`는 출구가 아니라 **주출입구**다. 키 이름을 그대로 믿고
  옮기면 "휠체어로 들어갈 수 있다"는 답이 대여 여부에서 나온다.
- 결정 3 — **목록을 먼저 부르고 거기 있는 장소만 행으로 만든다.** 반대 순서로
  하면(확인 안 한 장소 전체를 대상으로 잡고 목록으로 걸러내면) 목록에 없는 장소까지
  "확인했다"고 기록해야 하는데, 종로구 첫 적재에서 754행 중 590행이 그런 빈 행이었다.
  무장애 레코드가 없다는 사실은 목록 조회가 매번 알려주므로 저장할 이유가 없다.
  대가는 실행마다 목록 1회다 — 다 채운 구에서도 목록을 봐야 대상이 0인지 알 수 있다.
  - **값이 전부 빈 행은 남긴다.** 목록에 있는데 28개 필드가 모두 빈 장소가 4개 구에서
    60건이고, 전부 쇼핑(38)이며 용산구에 50건이 몰려 있다. 이름을 보면 몰 입점
    매장이고("나이키 롯데아울렛 서울역점" 등) 콘텐츠 등록연도가 2022년 46건·2024년
    14건으로 몰려 있다 — 일괄 등록되면서 레코드만 만들어지고 항목은 입력되지 않았다.
    그 행을 지우면 실행할 때마다 같은 빈 응답에 호출을 쓴다. 즉 무장애 목록은
    "정보가 있는 장소"가 아니라 **"레코드가 만들어진 장소"**다(496건 중 값이 있는
    것은 436건).
- 결정 4 — **대상은 상세조회 대상을 따라가지 않고 TTL로 고른다.** 상세조회 대상은
  "이번에 바뀐 장소"라서, 그걸 따라가면 기능을 넣은 날 이미 DB에 있던 2,600여 건이
  무장애 정보를 영영 못 받는다. 대신 한 번 확인한 장소는 `detail_ttl` 안에는 다시
  부르지 않으므로, 구별로 처음 한 번만 목록 크기만큼(종로구 164건) 호출한다.
- 결정 5 — **대조(reconcile)도 무장애 목록을 1회 부른다.** 화면의 예상 호출수를
  상한이 아니라 실제 수로 보여주기 위해서다. 목록 없이 "아직 확인하지 않은 장소 수"를
  쓰면 종로구에서 755회로 뜨지만 실제 상세 호출은 164회다(나머지 591건은 호출 없이
  "목록에 없음" 행만 쓴다). 하루 한도 1,000회 옆에 붙는 숫자라 4.6배 부풀려진 상한은
  누를지 말지를 잘못 판단하게 만든다. 대상 선정 규칙은 동기화와 같은 함수를 공유한다
  (`barrier_free_candidate_ids`/`barrier_free_stale_ids`) — 조건을 두 곳에 적으면
  한쪽만 고쳤을 때 화면이 실제와 다른 수를 보여준다. 구당 목록 호출은 대조 1회 +
  반영 1회로 총 2회다.
- 결정 6 — **숙박(32)은 제외한다.** 관광 대상에서 빼기로 한 결정을 따른다. 숙박에만
  있는 필드 `room`(장애인 객실, 숙박 69건 중 42건)도 담지 않는다. 나중에 포함하기로
  해도 재적재 69회면 따라잡는다.
- 채택하지 않은 것:
  - **`place_enrichments.official_facts`에 담기** — 이 컬럼은 사람이 공식 출처를
    조사해 검증한 값이고 `merge_policy: fallback_if_places_missing`(places가 1차)
    전제 위에 있다. API가 정기로 덮어쓰는 값을 같은 칸에 넣으면 동기화가 수작업
    결과를 지울지 말지를 매 필드마다 따져야 하고, "이 값은 사람이 확인했다"는 계보가
    무너진다. 다만 그 컬럼에 이미 들어간 무장애 키 6종(`accessible_restroom_raw` 등)은
    전부 이 API가 같은 값을 갖고 있었다(대조한 6개 장소 전부 일치, 예: 운현궁의
    "남녀 공용이나 내부가 좁아 불편"). 병합 순서 정리는 남은 것으로 둔다.
  - **28개 필드 전부 컬럼으로 만들기** — `videoguide`·`promotion`은 427건 중 1건,
    `signguide`·`bigprint`는 5건이다. 담지 않은 13개가 필요해지면 그때 늘린다.
  - **`guidehuman`(안내 도우미) 포함** — 4.9%로 컷에 0.1%p 차이로 걸렸다. 숙박을
    빼기 전에는 5.8%였다. 기준을 흔들지 않기 위해 뺐다.
  - **원문 정제** — `publictransport`의 `<br/>`도 `parking`의 `_무장애 편의시설`
    접미사도 지우지 않는다. places의 `_raw` 컬럼들과 같은 규칙이다.
- 남은 것: (a) `official_facts`의 무장애 키 6종과의 병합 순서, (b) 적재한 값을 INFO
  응답으로 내보내는 배선(`info_field_rules` 계약 키), (c) 무장애 적재 건수는 job
  결과에만 남고 `place_sync_runs`에는 기록하지 않는다.

### D-078 — 만료된 익명 계정(`auth.users`)을 30일 기준으로 정리한다 ([B] auth.users 정리)

- 상태: `Accepted` — 구현 완료.
- 배경: D-074(TP-134)가 B 소유 4개 테이블(`agent_states` 등)의 만료 세션 정리는
  닫았지만, 실제 로그인 주체인 Supabase Auth의 익명 계정(`auth.users`) 자체는
  그때 범위에서 명시적으로 제외했다(D-074 결정 5). `guest-auth-design.md` 10절도
  "`auth.users`의 익명 계정 자체를 지우는 스케줄은 여전히 별도 과제로 남아
  있다"고 이미 표시해뒀다. 익명 계정은 `signInAnonymously()`를 호출할 때마다
  하나씩 생겨(D-063 배경) 정리하지 않으면 무기한 쌓인다.
- 결정:
  1. 기준: `auth.users.created_at`이 기준 일수(기본 30일, `--days`로 조정
     가능)보다 오래되면 정리 대상. `last_sign_in_at`이 아니라 `created_at`을
     쓰는 이유는, B 소유 테이블처럼 "마지막 활동 시각"을 이 레벨에서 알 방법이
     없어서다(FK가 없어 join하지 않기로 했으므로, D-063 결정 4) — Supabase가
     공식 문서에서 권장하는 정리 쿼리(`delete from auth.users where
     is_anonymous is true and created_at < now() - interval '30 days'`)와
     동일한 기준을 그대로 따른다.
  2. 대상: `auth.users` 중 `is_anonymous = true`인 행만. 실제 가입자(이메일·
     소셜 로그인)는 대상에서 완전히 제외.
  3. 판별: PostgREST가 아니라 Supabase Auth Admin API(GoTrue,
     `{SUPABASE_URL}/auth/v1/admin/*`)로 접근한다 — `auth.users`는 PostgREST가
     노출하는 스키마가 아니다. Admin API는 `apikey` 헤더만으로는 인증되지
     않고 `Authorization: Bearer <secret key>`가 함께 필요해, 기존
     `SupabaseStateStore`의 PostgREST 클라이언트와는 별도로 작은
     `AuthAdminClient`를 새로 구현했다.
  4. 실행 방식: D-074와 동일하게 Supabase pg_cron이 아니라
     `backend/scripts/cleanup_anonymous_users.py` 수동/외부 스케줄 스크립트로
     구현(`--days`, `--dry-run` 지원). D-074의 정리 스크립트와는 완전히
     별개로 실행되며 순서를 강제할 필요가 없다(D-063 결정 4, FK 없음).
- 근거: 두 정리 작업(D-074, D-078)을 하나로 합치지 않은 이유는 D-074에서 이미
  "채택하지 않은 것"으로 명시해뒀다 — Admin API 접근 권한 확보와 PostgREST
  접근 권한이 성격이 달라 배포·권한 설정이 분리되는 편이 낫다.
- 채택하지 않은 것:
  - **`last_sign_in_at` 기준 판정** — GoTrue 응답에 필드는 있지만, 이 필드로
    "활동"을 판정하면 익명 로그인 이후 재로그인이 없는 정상 사용 패턴(토큰이
    localStorage에 남아 재사용됨, `guest-auth-design.md` 3절)까지 오래된
    것으로 오판할 위험이 있어 Supabase 공식 권장 기준(`created_at`)을 그대로
    따랐다.
  - **D-074 스크립트에 통합** — 위 근거 참고.
- 검증: 2026-08-25 실 Supabase 프로젝트에서 `--dry-run` 실행. `--days 30`
  (기본값)은 대상 0건 — 이 프로젝트에 아직 30일 넘은 익명 계정이 없었을
  뿐임을 `--days 0`으로 재확인(13건, 전부 8/19~8/24 생성 — created_at 필터가
  의도대로 동작함을 확인). 단위 테스트 14건도 로컬(Python 3.11)에서 통과.
- 남은 것: 실제 운영에서 스크립트를 얼마나 자주 돌릴지는 D-074와 마찬가지로
  트래픽이 늘어난 뒤 다시 판단한다.
- 상세: `backend/scripts/cleanup_anonymous_users.py`,
  `backend/tests/test_cleanup_anonymous_users_cli.py`, `docs/design/
  guest-auth-design.md` 10절

### D-079 — 피드백 통계를 dev-ops 패널에서 볼 수 있게 한다 (TP-146)

- 상태: `Accepted` — 구현 완료.
- 배경: `response_feedback`에 `rating`(like/dislike)·`reason_code`(7개 고정값,
  dislike에만)·`intent`(자유 텍스트)가 이미 쌓이고 있었지만, 조회는
  `GET /feedback/dislikes`로 원시 리스트를 가져오는 것뿐이었다. 집계
  API가 없었고, 있어도 지금까지 볼 화면이 없었다 — API만 추가하면
  "쌓이는데 아무도 안 보는" 문제를 API 레벨에서 반복하는 셈이라 dev-ops
  패널 노출까지 한 단위로 묶었다.
- 결정:
  1. `GET /feedback/stats` 신규(`since`/`until`/`top_intents` 쿼리 파라미터,
     전부 선택). 응답은 rating별 전체 건수, reason_code별 건수(dislike만
     — 표준 7개 값 + 사유 없이 남긴 dislike를 위한 `unclassified`, 항상
     8개 키 전부 포함), intent별 건수(상위 `top_intents`개 + 롱테일
     `other_intent_count` + intent 자체가 없는 `missing_intent_count`)로
     구성.
  2. 집계는 SQL group-by가 아니라 Python에서 한다. 다른 조회
     메서드(`list_dislikes` 등)도 전부 원본 행을 `FeedbackRecord`로 그대로
     돌려주는 방식을 따르고 있어 그 패턴을 유지했고, PostgREST의
     `count()` 집계가 이 프로젝트 설정에서 기본 활성화된다는 보장이
     없었다. `StateStore`에 `list_feedback_for_stats(since, until)`을
     새로 추가했다 — `list_dislike_feedback`과 달리 rating을 가리지
     않고(like까지) `limit`도 없이 전량을 반환한다.
  3. Supabase 구현은 `since`/`until` 동시 지정 시 PostgREST `and=(...)`
     문법으로 `recorded_at` 두 조건을 합성한다(같은 컬럼에 조건을 두 개
     걸 때 쿼리 파라미터 하나에 값 하나만 담을 수 있어서다).
  4. 프론트: `frontend/src/api/feedback.ts`에 `fetchFeedbackStats()` 추가
     — 애초 카드 초안에는 `api/dev.ts`로 적었지만, 구현하면서 보니 이
     엔드포인트는 `routes/dev.py`가 아니라 `routes/feedback.py` 소속이라
     `api/feedback.ts`가 맞는 위치였다(`api/dev.ts`의 다른 함수들과 달리
     `APP_ENV=local` 게이팅도 없다 — `feedback_router`는 무조건
     `include_router`된다). `FeedbackStatsPanel.tsx`를 기존
     `ApiUsagePanel`/`PlaceSyncPanel`/`DbStatusPanel`과 동일한 패턴(페이지가
     fetch, 패널은 props만 받아 렌더링)으로 신설하고 `DeveloperOpsPage`에
     네 번째 패널로 배선.
- 근거: LLMOps Trace 조회 API(같은 시점에 검토했던 다른 후보)는 이번
  범위에 넣지 않았다 — `trace_records`는 `response_feedback`과 다른
  테이블·다른 도메인이라, 앞서 정리한 원칙(테이블/도메인이 독립이면
  카드도 분리)대로 별도 카드로 남겨뒀다.
- 채택하지 않은 것:
  - **PostgREST group-by 집계** — 위 결정 2 참고.
  - **API만 추가하고 화면은 나중에** — 이번 카드가 막 지적한 "데이터는
    쌓이는데 아무도 안 본다"는 문제를 그대로 반복하게 된다.
- 검증: 2026-08-25 실 Supabase 프로젝트(`STATE_STORE_BACKEND=supabase`)에서
  브라우저 + curl로 확인. `POST /feedback`으로 넣은 값이 실제로 DB에
  적재되고, 파라미터 없는 `GET /feedback/stats`가 정상 집계해 dev-ops
  패널에 표시되는 것까지 확인. `since`/`until`을 동시에 넣어 PostgREST
  `and=(...)` 문법을 타는 경로는 단위 테스트(mock)로만 검증했고 실
  Supabase로는 별도 확인하지 않았다 — 패널에 날짜 UI가 아직 없어(아래
  "남은 것") 실사용에서도 당분간 이 경로를 안 타므로, 그 UI를 붙일 때
  같이 확인하기로 한다.
- 남은 것: 기간 필터(`since`/`until`)는 백엔드 API는 지원하지만 패널에는
  아직 날짜 선택 UI가 없다 — 지금은 항상 전체 기간을 본다. 필요해지면
  그때 추가하면서 `since`+`until` 동시 지정 경로도 실 Supabase로 함께
  확인한다.
- 상세: `backend/app/state/store.py`, `backend/app/state/supabase_store.py`,
  `backend/app/state/feedback.py`, `backend/app/state/service.py`,
  `backend/app/routes/feedback.py`, `backend/tests/state/test_feedback.py`,
  `backend/tests/state/test_supabase_store.py`, `frontend/src/api/feedback.ts`,
  `frontend/src/types.ts`, `frontend/src/components/dev/FeedbackStatsPanel.tsx`,
  `frontend/src/pages/DeveloperOpsPage.tsx`,
  `frontend/src/pages/DeveloperOpsPage.test.tsx`

### D-080 — LLMOps Trace 조회를 dev-ops 패널에서 볼 수 있게 한다 (TP-157)

- 상태: `Accepted` — 구현 완료.
- 배경: `trace_records`에는 A/C/D가 남긴 실행 단계(step)별 지연시간·에러가
  이미 쌓이고 있었지만, 조회는 세션 하나를 좁혀 보는
  `get_traces(session_id)`뿐이었다 — "step별 평균 지연시간이 얼마인지",
  "최근에 어떤 에러가 났는지"처럼 세션을 가리지 않는 질문에 답할 방법이
  없었다. D-079에서 같은 문제를 `response_feedback`에 대해 풀면서 "다른
  테이블·다른 도메인이라 별도 카드로 남긴다"고 정리했던 것의 후속.
- 결정:
  1. `GET /trace/stats` 신규(`since`/`until`/`recent_errors_limit` 쿼리
     파라미터, 전부 선택). 응답은 등장한 step만 담는 step별 집계(건수,
     평균/최대 `latency_ms`, 에러 건수)와 최근 에러 목록(`error_type`이
     있는 행만 최근순 상위 N건: session_id/run_id/step/error_type/시각)로
     구성.
  2. `step_stats`는 `reason_code_counts`(D-079)와 달리 고정된 값 집합이
     아니다 — step은 A/C/D가 자유롭게 붙이는 문자열이라 B가 미리 알 수
     없다(`agent-state-contract-v1.md`/`llmops-trace-contract-v1.md`의
     경계 원칙과 동일). 등장한 step만 담고, 화면도 그 순서를 그대로
     쓴다.
  3. 집계는 이번에도 PostgREST group-by가 아니라 Python에서 한다
     (D-079 결정 2와 동일한 근거). `StateStore`에
     `list_traces_for_stats(since, until)`을 신설 — `get_traces`와 달리
     세션 하나로 좁히지 않고 전체 테이블을 대상으로 한다. Supabase
     구현은 `since`/`until` 동시 지정 시 `list_feedback_for_stats`와
     동일한 `and=(...)` 문법으로 `recorded_at` 두 조건을 합성한다.
  4. 프론트: `frontend/src/api/trace.ts`에 `fetchTraceStats()`,
     `TracePanel.tsx`를 신설해 기존 패널들과 같은 패턴(페이지가 fetch,
     패널은 props만 받아 렌더링)으로 `DeveloperOpsPage`에 다섯 번째
     패널로 배선. `trace_router`는 `feedback_router`와 마찬가지로
     `APP_ENV=local`과 무관하게 무조건 `include_router`한다.
- 근거: 세션 단위 조회(`get_traces`)를 API로 별도 노출하지 않았다 — 지금
  필요한 것은 통계뿐이고, 세션 단위 원시 조회가 필요해지는 시점(예: 특정
  세션 디버깅 화면)이 오면 그때 범위를 정해 추가하는 게 맞다고 판단했다.
- 채택하지 않은 것:
  - **PostgREST group-by 집계** — 결정 3 참고.
  - **API만 추가하고 화면은 나중에** — D-079와 같은 이유로 기각.
- 남은 것: D-079와 동일하게 기간 필터(`since`/`until`)는 API는 지원하지만
  패널에는 날짜 선택 UI가 없다 — 지금은 항상 전체 기간을 본다.
- 상세: `backend/app/state/store.py`, `backend/app/state/supabase_store.py`,
  `backend/app/state/trace.py`, `backend/app/state/service.py`,
  `backend/app/routes/trace.py`, `backend/app/main.py`,
  `backend/tests/state/test_trace.py`, `backend/tests/state/test_supabase_store.py`,
  `frontend/src/api/trace.ts`, `frontend/src/types.ts`,
  `frontend/src/components/dev/TracePanel.tsx`,
  `frontend/src/pages/DeveloperOpsPage.tsx`,
  `frontend/src/pages/DeveloperOpsPage.test.tsx`

### D-081 — `list_traces_for_stats`/`list_feedback_for_stats`가 PostgREST 기본 1000행 상한에 걸려 있던 문제 수정 (D-079/D-080 후속)

- 상태: `Accepted` — 구현 완료.
- 배경: TP-157 브라우저 테스트 중 발견. dev-ops 패널의 "전체 실행"이 정확히
  1000으로 뜨는 게 단서였다 — 실제 `trace_records`는 그보다 많았는데,
  `list_traces_for_stats()`가 PostgREST(Supabase REST)에 조건 없이 `GET`
  한 번만 보내고 있었다. PostgREST는 `limit`을 명시하지 않아도 Supabase
  프로젝트의 API 설정(기본 max rows=1000)에 따라 응답을 자른다 — D-079
  결정 2가 "원본 행을 그대로 반환"하는 패턴을 유지하기로 하면서 그 반환이
  실은 전량이 아니라 첫 1000행일 수 있다는 것을 놓쳤다.
  `list_feedback_for_stats()`도 완전히 같은 코드 패턴이라 `response_feedback`이
  1000행을 넘으면 동일하게 잘린다(아직 실측은 안 됐지만 같은 결함).
- 결정: `SupabaseStateStore`에 `_fetch_all_rows(path, params)` 헬퍼를
  신설 — `limit`/`offset`을 페이지(1000행) 단위로 넘겨가며 반환된 행 수가
  페이지 크기보다 작아질 때까지 반복 조회해 합친다. `list_traces_for_stats`/
  `list_feedback_for_stats` 둘 다 이 헬퍼로 교체. 세션 범위 조회(`get_traces`,
  `get_feedback`, `list_dislike_feedback`)는 애초에 이 정도로 커질 일이
  없어 대상에서 제외했다.
- 근거: 두 메서드 모두 "세션을 가리지 않고 테이블 전체를 대상으로 한다"는
  것이 설계 의도(D-079 결정 2, TP-157 설계)라, 응답이 조용히 잘리면 그
  의도 자체가 깨진다 — 집계 API가 틀린 총합·평균을 "정상 응답"으로
  돌려주는 것이 가장 나쁜 실패 형태다.
- 채택하지 않은 것:
  - **Supabase 프로젝트 설정의 max rows를 올린다** — 인프라 설정 변경은
    이 프로젝트 코드베이스 밖의 결정이고, 값을 아무리 올려도 언젠가는
    다시 넘긴다. 페이지네이션이 근본 해법이다.
  - **PostgREST `Range` 헤더 대신 `limit`/`offset` 쿼리 파라미터** —
    Range 헤더도 결국 서버의 max rows를 넘을 수 없어 여러 요청이
    필요한 건 같고, `_request()`가 이미 `params` 인자를 받는 구조라
    쿼리 파라미터 쪽이 기존 코드와 더 잘 맞았다.
- 검증: 단위 테스트(mock)로 페이지 경계 동작 확인 — 첫 페이지가 1000행
  꽉 차면 두 번째 요청(offset=1000)을 보내 나머지를 더하는 것,
  1000행보다 적게 오면 한 번만 요청하고 멈추는 것 둘 다 확인. 실
  Supabase 재확인은 사용자가 브라우저에서 진행 중.
- 남은 것: 없음.
- 상세: `backend/app/state/supabase_store.py`,
  `backend/tests/state/test_supabase_store.py`


### D-082 — `place_embeddings` HNSW 인덱스 누락 발견·복구 (Package D 테이블, B가 발견해 직접 복구)

- 상태: `Accepted` — 복구 완료.
- 배경: `place_embeddings`는 Package D 소유 테이블(RAG용 벡터 저장소)이라
  B의 공식 작업 범위는 아니지만, 다른 작업 중 우연히 `pg_indexes`를
  조회하다 원래 생성 마이그레이션(`202608180001_create_place_embeddings.sql`)에
  정의돼 있던 `place_embeddings_embedding_hnsw_idx`가 실제 프로덕션 DB에는
  없다는 것을 발견했다. 정확한 경위를 남긴 기록은 없지만, 2026-08-20 중구
  RAG 확장 실험(`backend/scripts/import_place_embeddings.py`)의 코드 주석이
  HNSW 인덱스가 걸린 상태로 대량 upsert하면 인덱스 갱신 비용 때문에
  `statement_timeout`(57014)에 걸리는 문제를 언급하고 있어, 이를 우회하려
  인덱스를 지운 뒤 다시 만들지 않은 것으로 추정된다. 실측 결과 57,331건
  (장소 1,516곳)이 인덱스 없이 쌓여 있었다.
- 결정: 마이그레이션 파일(`202608250002_restore_place_embeddings_hnsw_index.sql`)로
  `create index if not exists`를 다시 남기고, 실제 DB에도 적용했다.
- 근거: RAG는 아직 추천 파이프라인에 노출되지 않아 지금 당장 장애는
  아니지만, 인덱스 없이 데이터가 계속 쌓이면 나중에 RAG가 실사용에
  연결될 때 전역 최근접 이웃 검색(§2.10)이 전부 순차 스캔을 타게 된다.
  발견한 시점에 바로 남기지 않으면 다음에 또 같은 경위로 놓칠 수 있어
  기록을 남겼다.
- 채택하지 않은 것:
  - **Package D에 넘기고 B는 손대지 않는다** — 별도 조율 없이 방치하면
    복구가 계속 미뤄질 위험이 커서, 우선 복구하고 사후에 D 담당자에게
    공유하는 쪽을 택했다.
- 검증: `pg_indexes`로 복구 전(3개)·복구 후(4개) 인덱스 목록을 직접 조회해
  확인.
- 남은 것: Package D 담당자에게 인덱스가 없었던 사실과 복구 내역 공유
  (아직 안 함). 인덱스가 다시 빠지지 않도록 후속 마이그레이션에서
  `place_embeddings` 관련 DDL을 만질 때 이 이력을 참고할 것.
- 상세: `supabase/migrations/202608250002_restore_place_embeddings_hnsw_index.sql`

### D-083 — 서비스 지원 지역을 4개 구에서 12개 구로 확장한다

- 상태: `Accepted` — 구현 완료.
- 배경: PR #224(D-044/D-025)가 서비스 지역을 종로구 한 곳에서 종로구·중구·용산구·
  성동구 네 곳으로 늘리며 "구를 늘릴 때 `SUPPORTED_DISTRICTS`에 한 줄만 추가하면
  된다"는 구조를 만들어뒀다. Supabase `places`를 다시 보니 그 네 구 밖에도 이미
  여덟 구(광진·동대문·중랑·성북·강북·도봉·노원·은평, 합계 1,103건)가 적재돼
  있었다 — 적재는 끝났는데 서비스 지역 판정이 여전히 네 구만 통과시켜 후보로
  나올 수 없는 상태였다.
- 결정:
  1. `app/service_area.py`의 `SUPPORTED_DISTRICTS`에 여덟 구를 추가한다(12곳).
     PR #224가 만든 구조 그대로 한 줄씩만 늘렸다 — 경계 파일(`seoul.geojson`)은
     이미 서울 25개 구를 다 담고 있어 손댈 필요가 없었고, 좌표 판정·장소 검색·
     안내 문구 전부 `SUPPORTED_DISTRICTS`/`SUPPORTED_DISTRICT_CODES`/
     `supported_district_label()`을 동적으로 읽어 다른 코드는 한 곳도 고치지
     않았다.
  2. district_code(215/230/260/290/305/320/350/380)는 추정하지 않고 Supabase
     `places`에서 구별 표본 좌표·주소를 뽑아 실제 행정구역명과 대조해 확인했다
     (예: 215 → "서울특별시 광진구 능동로 216").
  3. `tests/test_service_area.py`의 `_OFFICIAL_AREA_KM2`에 여덟 구의 공식 면적을
     추가하고, 폴리곤 면적을 계산해 전부 1% 이내(최대 0.49%)임을 확인했다.
     `_INSIDE`/`_OUTSIDE` 대표 좌표도 새 경계에 맞게 갱신했다 — 청량리역(동대문구)·
     건대입구역(광진구)이 "밖"에서 "안"으로 옮겼고, 새로 인접한 미지원 구(송파·
     강동·구리·남양주·고양)의 좌표를 "밖"에 추가해 확장이 그쪽으로 새지 않는지
     잡는다.
- 채택하지 않은 것:
  - **파일에 있는 25개 구를 전부 지원 처리** — PR #224가 이미 기각한 방향과
    같은 이유다. 지원 범위는 팀 합의가 필요한 결정이라 코드에 드러나야 한다.
  - **경계 판정과 별개로 이 여덟 구를 우선 서비스 지역에서 뺀 채 데이터만
    쌓아두기** — 이미 적재가 끝난 데이터를 추천에서 계속 배제할 이유가 없다.
- 검증: `pytest tests/test_service_area.py` 83건 통과(기존 54건 → 29건 추가,
  전부 목록만 따라가는 파라미터화 테스트라 손으로 늘린 것은 좌표 몇 개뿐).
  활성 장소 1,103건의 좌표를 폴리곤과 대조해 밖으로 나온 것 7건(0.63%,
  기존 네 구는 0.16%)을 확인 — 다섯 건은 2018년 경계의 능선 정밀도 한계
  (아차산·망우산·북악산 계열), 두 건은 좌표 자체가 깨졌다.
- 곁가지 발견: 좌표 (19.694, 117.993)(남중국해)이 서로 다른 구의 서로 다른
  장소 세 곳(용산구 성촌공원, 광진구 아차산배수지체육공원, 은평구 증산체육공원)
  에서 정확히 같은 값으로 나왔다 — 우연이 아니라 적재 파이프라인 어딘가의
  결측치 대체값으로 보인다. 저장소 경로는 경계 판정을 생략해(D-044) 지금
  서비스에 영향은 없지만, 원인은 확인하지 않았다.
- 남은 것:
  - 여덟 구의 집중률(혼잡도) 매핑, 취향 근거 임베딩은 이번 범위 밖이다
    (README `지원 구를 늘릴 때` 체크리스트의 "경계·판정 밖" 항목).
  - `agent_runtime.py`의 `_LOCATION_REQUIRED_QUICK_PICKS`와
    `docs/design/clarification-options.md` §7이 여전히 "서비스 지역이 종로구
    한정"을 전제로 대표 스팟 4곳만 고정해 두고 있다 — 이 전제는 PR #224
    시점부터 이미 틀려 있었고 지금 더 틀렸다. 구가 늘 때마다 버튼을 늘릴지,
    다른 방식으로 바꿀지는 UX 결정이 필요해 이번 범위에서 건드리지 않았다.
  - (19.694, 117.993) 결측치 대체값 패턴의 원인 조사.
- 상세: `backend/app/service_area.py`, `backend/tests/test_service_area.py`,
  `backend/resources/boundaries/README.md`

### D-084 — 서울시 실시간 지역 목록을 JSON으로 옮기고, 조회 경로별로 맞는 목록에 연결한다

- 상태: `Accepted` — 구현 완료.
- 배경: TP-141(작성: JinHyeong Kim)이 "지금 경복궁 붐벼?"에 북촌한옥마을(0.85km)
  값이 대신 나가는 걸 신고했다. 원인은 `seoul_commercial_areas.py`의 `_RAW_AREAS`
  (하드코딩 82곳, "서울시 주요 82장소 영역 경계 SHP(2026-04-10)"에서 뽑아 고정)를
  인구 혼잡도 조회에도 그대로 썼기 때문이다. TP-141은 이걸 "목록이 낡았다"로
  진단하고, 82곳을 121곳으로 채우는 건 서울시 도시데이터 연동을 실제로 소유한
  패키지(A)로 넘기도록 범위를 잘랐다(응답을 바꾸는 판정 규칙 변경은 A 리뷰가
  필요하다는 이유).
- 재해석: 서울시가 공개한 공식 매뉴얼(`실시간 도시데이터 매뉴얼 V8.5`, 2026-04)을
  받아 대조한 결과, 82/121 분리는 "목록이 아직 못 따라간 것"이 아니라 **서울시가
  처음부터 정한 영구적인 API 설계 차이**였다. 인구데이터 API(`citydata` 통합,
  `citydata_ppltn` 전용)는 처음부터 121곳을 지원하고(매뉴얼 6p, 8~10p 표 2-2),
  상권현황 API(`citydata_cmrcl`)는 "정확한 정보 전달을 위해 121장소 중 가맹점 수가
  적거나 소비가 적은 장소를 제외한 82장소에 대해 서비스 제공"(매뉴얼 36p)이라는
  이유로 82곳만 지원한다 — 카드소비 데이터가 통계적으로 의미 있으려면 가맹점이
  일정 수 이상 있어야 하는데 공원 33곳 등은 애초에 그 조건을 못 채운다. 즉 진짜
  원인은 "인구 조회에 상권 전용 82개 목록을 잘못 가져다 쓴 것"이었다. 이 발견을
  근거로 82→121 확장을 A의 판단 없이도 되는 데이터 정정으로 보고 범위에 포함시켰다
  — 다만 TP-141의 리뷰 원칙(서울시 도시데이터 연동은 A 소유, PR에 타 패키지 수정
  명시하고 A 리뷰 요청)은 그대로 따른다.
- 결정:
  1. **목록을 JSON으로 이관**. `backend/resources/seoul_realtime/`에
     `population_areas_121.json`(인구용)·`commercial_areas_82.json`(상권용) 두
     파일을 둔다. 둘 다 서울 열린데이터광장 공식 파일(OA-21778/OA-22385의
     xlsx+SHP)에서 받았고, 좌표는 같은 SHP·같은 계산식(면적가중 중심, shoelace
     공식)으로 다시 뽑아 82/121이 서로 다른 기준으로 어긋나지 않게 했다. 파일에
     출처·조회일·`coordinate_source`를 담는다. 로더(`seoul_realtime_areas.py`,
     옛 이름 `seoul_commercial_areas.py`에서 개명)가 로드 시점에 코드 중복·좌표
     범위·필수 필드는 물론 **카테고리별 개수가 매뉴얼 표 2-2/3-9와 일치하는지도**
     검증해 예외로 끊는다 — 매뉴얼과 어긋나면 우리 스냅샷이 조용히 깨진 것이다.
  2. **조회 경로 3개를 각자 맞는 목록에 연결**(`app/agent_context/service.py`):
     `_fetch_realtime_population_or_concentration_info`(인구)와
     `_fetch_realtime_city_info`(citydata 통합 — 주차·지하철·버스·행사)는
     121개 목록을, `_fetch_realtime_commercial_info`(상권)는 82개 목록을 쓴다.
     상권은 82개가 구조적 한계라 앞으로도 확장하지 않는다.
  3. **낡음 감지 probe + 개발자 배너**(TP-141 2·3번, 목적 재정의). 121개 목록도
     서울시가 "시민 의견을 수렴해 지속 확대"(매뉴얼 48p)할 예정이라 언젠가
     뒤처질 수 있다. 최근접 대체가 일어났을 때(`area.name != place_name`)만
     이미 들고 있는 `GetRealtimeCityDataTool`로 실제 해석된 이름을 한 번 더
     조회해, 성공하고 우리 목록에 없으면 `StaleAreaProbeDebug`(대체 지역·거리
     포함)를 감사 메타데이터에 싣는다. **응답(대체 판정·문구)은 절대 바꾸지
     않는다** — probe 실패는 이유를 따지지 않고 조용히 넘어간다(서울시 API
     장애와 미지원 지역을 구분하려 들면 본 요청에 영향을 줄 위험이 있다).
     같은 이름 반복 조회는 프로세스 메모리 캐시로 막고,
     `SEOUL_AREA_STALENESS_PROBE_ENABLED`(기본 true)로 배포 없이 끌 수 있다.
     프론트는 `DeveloperChatPage`의 `TurnLocationBadges` 바로 위에 `StaleAreaBanner`를
     새로 둔다 — `ErrorBanner`를 재사용하지 않았다: 이건 오류가 아니라 참고
     정보라 빨강 alert 톤·재시도 버튼이 맥락에 안 맞는다.
- 채택하지 않은 것:
  - **82곳을 그대로 두고 121곳 확장은 다음 카드로 미루기**(TP-141 원안) —
    매뉴얼로 "낡음"이 아니라 "애초에 잘못 연결된 참조 데이터"임을 확인한
    뒤에는 미룰 이유가 약해졌다고 판단했다. 다만 A 리뷰는 그대로 요청한다.
  - probe가 실 API 장애와 미지원 지역을 구분하는 것 — 구분하려 들면 판정
    로직이 복잡해지고, 그 구분이 본 요청의 실패 판정에 영향을 줄 위험이
    생긴다. TP-141 원안대로 "탐색 실패는 전부 신호 없음"으로 단순화했다.
- 검증: 신규 pytest 19건(지역 목록 11건 + probe 3건 + 기존 회귀 갱신) 포함
  전체 2745 passed(무관한 기존 langfuse 테스트 1건 제외), ruff 클린. 프론트
  vitest 160 passed, tsc 클린, eslint 새 경고 없음.
- 상세: `backend/resources/seoul_realtime/`, `backend/app/agent_context/seoul_realtime_areas.py`,
  `backend/app/agent_context/service.py`, `backend/app/schemas.py`,
  `backend/app/agent_context/info_schemas.py`,
  `backend/tests/test_seoul_realtime_areas.py`,
  `backend/tests/agent_context/test_realtime_population_staleness_probe.py`,
  `frontend/src/components/dev/StaleAreaBanner.tsx`, `frontend/src/types.ts`,
  `frontend/src/pages/DeveloperChatPage.tsx`
- 관련 작업: TP-141

### D-085 — 서비스 지역 밖 안내에서 구 목록을 본문과 분리해 각주로 뺀다

- 상태: `Accepted` — 구현 완료.
- 배경: D-083으로 지원 구가 4개에서 12개로 늘면서, `unsupported_region` 안내
  문구("현재는 베타 서비스로 종로구·중구·용산구·성동구·광진구·동대문구·중랑구·
  성북구·강북구·도봉구·노원구·은평구의 장소 추천만 가능해요.")가 한 문장 안에
  구 이름을 전부 나열해 지나치게 길어졌다. 구는 계속 늘어날 예정이라 이 문장은
  앞으로도 계속 길어진다.
- 결정:
  1. `AgentResponse`에 `message_footnote: str | None` 필드를 신설한다. 본문
     (`message`)은 "이 위치는 지금 서비스 지역이 아니에요. 다른 위치를 말씀해
     주세요."로 짧게 고정하고, 구 목록은 이 필드로 뺀다. 화면은 이 필드가 있으면
     본문 아래 작고 옅은 글씨(`text-xs text-gray-400`)로 보여준다.
  2. `response_composer.py`에 `unsupported_region_footnote(error_code)` 헬퍼를
     신설 — `error_code == "unsupported_region"`일 때만
     `supported_district_label(with_city=True)`로 만든 문자열을 돌려주고, 그 외는
     `None`이다. `compose_chat_message()`의 시그니처·내부 흐름은 손대지 않았다 —
     `AgentResponse`를 조립하는 지점(`agent_runtime.py`)에 이미 `tool_error_code`/
     `info_response.error.code`가 있어서, 그 자리에서 각주만 별도로 계산해
     끼워 넣는 것으로 끝났다(RECOMMEND/MODIFY/SCHEDULE 경로, INFO 경로 두 곳).
  3. 프론트는 `AgentResponse.message_footnote`를 `assistant_text` 메시지의
     `footnote` 필드로 그대로 옮기기만 한다 — `ClarificationMessage.tsx`가
     `location_ambiguous` 동적 후보를 이미 그대로 렌더링만 하듯, 여기도 백엔드가
     보낸 값을 그대로 그린다.
- 채택하지 않은 것:
  - **문자열 하나에 구분자로 이어붙이고 프론트에서 split** — 이 저장소가 어디서나
    타입 계약으로 처리하는 것과 결이 안 맞고, 구분자가 우연히 본문에 등장하면
    깨진다.
  - **`compose_chat_message()`의 반환 타입을 `(message, footnote)` 튜플로 바꾸기**
    — 이 함수가 ~10곳에서 단순 `str`을 반환하고 있어(INFO 세부 유형별 4개 함수
    포함) 전부 고쳐야 한다. `unsupported_region` 하나만 각주가 필요한데 모든
    반환 경로의 타입을 바꾸는 건 과했다.
- 검증: `compose_chat_message()`/`compose_info_concentration_message()` 단위
  테스트로 본문이 짧아졌는지, `unsupported_region_footnote()`가 해당 코드에만
  반응하는지 확인. `run_agent_flow()` 종단 테스트(`_UnsupportedRegionToolProvider`)로
  실제 `AgentResponse.message`/`message_footnote` 둘 다 확인. 프론트는 SSE 흐름부터
  렌더링까지 통합 테스트 1건 추가. `pytest` 2,749 passed(무관한 기존 langfuse 테스트
  1건 제외), `ruff` 클린, 프론트 `vitest` 161 passed, `tsc`/`eslint` 클린.
- 남은 것: `resolve_location.py`의 `_error_message()`가 가진 같은 모양의 긴 문자열
  (`ambiguous_location`/`outside_supported_region` cause)은 실제로는
  `response_composer.py`가 에러 코드만 보고 자체 문구로 덮어써서 화면에 안 뜨는
  것으로 보인다(죽은 문자열 추정) — 이번엔 확인만 하고 정리하지 않았다.
- 상세: `backend/app/schemas.py`, `backend/app/services/runtime/response_composer.py`,
  `backend/app/services/runtime/agent_runtime.py`,
  `backend/tests/test_response_composer.py`, `backend/tests/test_agent_runtime.py`,
  `frontend/src/types.ts`, `frontend/src/state/TripContext.tsx`,
  `frontend/src/components/chat/ChatMessageList.tsx`, `frontend/src/App.test.tsx`

### D-086 — 서비스 지원 지역을 12개 구에서 16개 구로 확장한다

- 상태: `Accepted` — 구현 완료.
- 배경: 2026-08-26에 place-sync로 서대문·마포·양천·강서구의 장소 목록·상세정보를
  새로 적재했다(서대문 162건, 마포 522건, 양천 135건, 강서 200건 — 강서는 상세
  181/200, 은평구 141건 백필분과 함께 TourAPI 일일 한도 소진으로 다음 실행에서
  마저 채울 예정). D-083과 같은 이유로, 적재는 끝났는데 `SUPPORTED_DISTRICTS`가
  그대로면 이 네 구는 후보로 나올 수 없다.
- 결정:
  1. `SUPPORTED_DISTRICTS`에 네 구를 추가한다(16곳) — 서대문구(410)·마포구(440)·
     양천구(470)·강서구(500). D-083과 같은 구조 그대로 한 줄씩만 늘렸다.
     district_code는 place-sync가 실제로 적재한 TourAPI 응답 주소로 확인했다.
  2. `tests/test_service_area.py`의 `_OFFICIAL_AREA_KM2`에 네 구의 공식 면적을
     추가했다(위키백과 infobox 기준, 2026-08-26 확인: 서대문 17.61·마포 23.85·
     양천 17.41·강서 41.43km²). 계산 면적과의 오차는 전부 1% 이내(최대 0.75%).
     `_INSIDE`에 네 구 대표 좌표를 추가하고, `_OUTSIDE`의 마포·서대문 자리에 있던
     망원역·홍대입구역·신촌역은 이제 "안"이라 `_INSIDE`로 옮겼다. 새로 인접하게 된
     미지원 구(영등포·구로)의 좌표를 `_OUTSIDE`에 새로 추가해 확장이 그쪽으로
     새지 않는지 잡는다.
  3. 이 확장으로 깨지는 기존 테스트 3건(망원역·마포구·440을 "지원 밖" 예시로 쓰던
     `test_festival_provider.py`·`test_place_provider.py`·
     `test_resolve_location_tool.py`)을 발견 — 예시를 영등포구(560)로 교체했다.
     같은 패턴이 D-083 때도 있었다(명동·서울역).
- 검증: `pytest tests/test_service_area.py` 94건 통과(기존 71건 → 23건 추가).
  활성 장소 1,019건의 좌표를 폴리곤과 대조해 밖으로 나온 것 4건(0.39%) 확인 —
  그중 3건은 D-083에서 이미 발견한 것과 똑같이 깨진 좌표(19.694, 117.993)라
  이번에도 재현됐다. 전체 `pytest` 2,760 passed(무관한 기존 langfuse 테스트 1건
  제외), `ruff` 클린.
- 채택하지 않은 것: 구로·영등포·금천구까지 함께 추가 — 아직 place-sync를 돌리지
  않아 목록·상세정보가 DB에 없다. 데이터가 없는 구를 지원 목록에 넣으면 추천은
  항상 0건이 나오고 이유가 사용자에게 안 보인다(D-044와 같은 이유로 기각).
- 곁가지 발견: (19.694, 117.993) 결측치 대체값이 이번에 두 구를 더 대조하며 세
  번 더 나왔다(계남근린공원·롯데시티호텔 김포공항·롯데백화점 김포공항점) —
  누적 여섯 번째부터. 특정 구의 우연이 아니라 적재 파이프라인 전반의 문제라는
  심증이 짙어졌지만 원인은 여전히 확인하지 않았다.
- 남은 것:
  - 은평구 141건·강서구 19건 상세정보 백필. 오늘 TourAPI 일일 한도(1,000회)
    소진으로 다음 실행에서 이어간다.
  - 구로·금천·영등포구 place-sync 및 그 이후 서비스 지역 확장 여부 판단.
  - 여덟 구 확장 때와 마찬가지로 새 네 구의 집중률 매핑·취향 근거 임베딩은
    범위 밖이다.
  - (19.694, 117.993) 결측치 대체값 패턴의 원인 조사(D-083부터 이어지는 미해결).
- 상세: `backend/app/service_area.py`, `backend/tests/test_service_area.py`,
  `backend/resources/boundaries/README.md`, `backend/tests/test_festival_provider.py`,
  `backend/tests/test_place_provider.py`, `backend/tests/test_resolve_location_tool.py`
  
### D-087 — 장소 사진으로 "분위기가 비슷한 곳"을 찾는 이미지 임베딩을 도입한다

- 상태: `Accepted` — 구현 완료(TP-162·TP-163, 패키지 C).
- 배경: 리뷰나 컬럼에 적힌 적 없는 공간의 인상을 다룰 방법이 없었다. `places`에도
  리뷰에도 "이 카페는 미술관 같은 분위기"라는 문장이 없어, 텍스트 검색
  (`place_embeddings`)으로는 찾을 수 없는 유사성이다. 실제로 카페(마우스래빗)의
  이웃이 갤러리·소극장·전시실로 나온다.
- 결정:
  1. 모델은 `google/siglip2-base-patch16-224`(768차원)를 쓴다. `-384`와 비교했으나
     정답표 채점 차이가 평균 +0.030으로 잡음 범위였고(`warm_toned` +0.077은 장소 한 곳
     차이에서 나온다) 연산이 3배라 224로 확정했다.
  2. **축 문구(질의)는 영어로 만든다.** 한국어 문구는 "북적이고 활기찬 장소"에
     국립중앙박물관·순교성지·산을 내놓았다. 구체적 명사 개념은 한국어로도 되지만
     추상적인 분위기 형용사에서 무너진다.
  3. 분위기 축은 후보 11개를 여섯 단계 검사로 걸러 여덟을 남기고 **다섯을 켠다** —
     `indoor` 0.992 · `calm` 0.844 · `traditional` 0.832 · `warm_toned` 0.790 ·
     `weathered` 0.787(사람 정답표 77곳 기준 AUC). 끈 셋은 `spacious`(규모, 두 사람
     일치도가 0.600으로 **사람도 못 정하는 축**)·`tidy`(정돈, 여덟 중 유일하게 모델이
     사람보다 뒤처진다)·`vivid`(색감, `traditional`과 +0.47로 겹친다)이다.
     **축 키는 영문이고 부호는 `+` 쪽을 가리킨다** — `calm`이 양수면 조용한 쪽이다.
     축 벡터를 만든 앵커 문구가 영어(결정 2)라 이름도 거기서 땄다. 한글 이름으로
     두면 A가 넘긴 영문 enum을 C가 한글로 옮겼다 되돌리는 왕복이 생긴다
     (2026-08-26 정정 — 처음에는 한글 키로 적재했다가 631행의 jsonb 키를 바꿨다.
     값과 벡터는 그대로이고 이름만 바뀌었으며, `anchors_version`은 해시에 축 이름이
     들어가므로 `#39b424217e` → `#b0902678b3`으로 함께 돌렸다).
  4. 여덟을 모두 저장하고 `enabled` 목록으로 켜고 끈다. **붙이는 것이 빼는 것보다
     쉽기 때문이다** — 사용자가 써 본 축을 없애면 그 요청이 갑자기 안 먹히고 로그
     해석도 꼬인다.
  5. 저장은 테이블 둘로 나눈다. `place_image_embeddings`(사진별 2,263행)와
     `place_mood_vectors`(장소별 631행)다.
  6. **인덱스는 적재가 끝난 뒤에 건다.** 마이그레이션을 `202608260002`(테이블)와
     `202608260003`(인덱스)로 쪼개 순서를 파일로 강제했다.
  7. 축 점수는 조회 때 계산하지 않고 `axis_scores` jsonb에 미리 담는다. 발화 경로가
     SQL 정렬만으로 끝난다.
- 근거:
  - **텍스트 임베딩에 얹지 않은 이유.** `place_embeddings`와 우연히 둘 다
    768차원이지만 **한쪽은 한국어 문장, 다른 쪽은 사진이 사는 공간**이다. 섞으면
    계산은 되고 결과가 무의미하며, 기존 HNSW 인덱스가 한 좌표계를 가정하므로 RAG
    검색까지 망가진다. 컬럼 구조도 맞지 않는다 — `source_text`가 not null인데
    사진에는 본문이 없다.
  - **사진별 테이블을 따로 둔 이유.** 장소 평균만 저장하면 정규화 과정에서 원래
    합을 잃어 부분 갱신을 할 수 없고, 사진 한 장이 늘 때마다 그 장소를 전량 재
    임베딩해야 한다. 또 "올리신 사진과 이 사진이 닮았다"는 근거를 보여주려면 사진
    단위 벡터가 있어야 한다.
  - **인덱스를 나중에 거는 이유(D-082 반복 방지).** HNSW가 걸린 채 대량 upsert하면
    인덱스 갱신 비용 때문에 매 요청이 `statement_timeout`(57014)에 걸린다.
    텍스트 임베딩 쪽에서 그때 인덱스를 지워 우회한 뒤 다시 만들지 않아 57,331건이
    인덱스 없이 쌓인 채 발견됐다. 631행 규모에서는 순차 스캔으로 충분하다.
  - **벡터에서 전체 평균을 빼지 않는다.** 29곳에서는 허브 완화 효과로 업로드 검색이
    1/8 → 4/8이 됐으나, 631곳에서는 평균 순위가 15.2 → 18.8로 뒤집혔다. 후보가
    많아지면 허브가 희석되고, 중심에는 "이건 실내 공간이다" 같은 쓸모 있는 신호도
    섞여 있다. **작은 표본에서 얻은 개선이 규모에서 뒤집힌 사례다.**
- 검증: **사람 판단으로 천장을 먼저 쟀다.** 삼중 비교 51문항을 5명이 답해
  사람끼리의 일치도가 0.851, 모델이 0.800이었다(94%). 천장을 재지 않으면 모델
  점수를 해석할 수 없다는 것이 이번의 방법론적 교훈이다 — 0.800이 좋은 값인지
  나쁜 값인지는 사람이 얼마나 일치하는지를 모르면 말할 수 없다.
  - 작은 표본이 상관계수와 η²를 부풀린다: 29곳 +0.59 → 461곳 +0.45, η² 0.54 → 0.22.
  - **부호 분포로 축을 판정하면 안 된다**: 세월은 631곳 중 양수가 24곳뿐인데 순위
    정확도는 0.787이다. 그래서 축 점수는 정렬에만 쓰고 임계값으로 쓰지 않는다.
- 알려진 약점: **관람시설**(박물관·갤러리)이 세 시험에서 같은 곳을 가리킨다 —
  커버리지(12곳 중 7곳이 서술되지 않음), leave-one-out(Recall@5 0.687), 삼중 비교
  (만장일치인데 틀린 앵커 3건 전부). 외관·전시실·유물이 제각각이라 사진 평균이
  중앙으로 몰리는 것으로 보인다. 전시 작품을 찍은 사진이 섞이는 것도 원인 중
  하나다(바라캇 서울은 4장 중 1장이 작품 사진이다).
- 채택하지 않은 것:
  - **쇼핑을 사진 분석에서 제외** — 오히려 두 번째로 잘 맞는 분류였다(0.805).
    다만 사진이 한 장뿐인 장소가 631곳 중 170곳(27%)이고 대부분 쇼핑이다.
  - **평균 차감(mean-centering)** — 위 근거 참고. 규모에서 뒤집혔다.
- 남은 것:
  - 서비스 배선(조회·재정렬)은 이 결정의 범위 밖이다 → D-094에서 조회까지 붙였다.
  - 발화에서 축을 고르는 단계는 A 패키지 소관이라, **"정해진 축 이름 하나를 입력으로
    받는다"** 는 계약만 정한다.
  - 종로구 외 지역. 2026-08-26 기준 631곳(종로 602 + 검증용 29)만 적재돼 있다.
  - `tidy` 축(모델 0.600 대 사람 0.700)과 `spacious` 축(사람끼리 0.600)은 끈 상태로 둔다.
- 상세: `supabase/migrations/202608260002_create_place_mood_embeddings.sql`,
  `supabase/migrations/202608260003_add_place_mood_vectors_hnsw_index.sql`,
  `backend/scripts/import_mood_embeddings.py`,
  `supabase/data_dictionary/place_mood_vectors.md`

### D-088 — 관광지별 연관 관광지 정보(TarRlteTarService1)를 종로구·중구 파일럿으로 수집·매칭·적재한다 (패키지 경계 밖 실험, B가 진행)

- 상태: `Accepted` — 수집·매칭·적재는 서비스 지원 16개 구 전체 완료(2026-08-27
  기준). SCHEDULE 연동(D-091)·RECOMMEND 2차 스코어링 연동(D-092)도 완료.
  INFO/COMPARE 인텐트 연동, 서비스 미지원 지역 확장은 범위 밖으로 남음.
- 배경: 한국관광공사가 공공데이터포털에 새로 공개한 TourAPI
  "관광지별 연관 관광지 정보"는 Tmap 실내비게이션 co-visitation(실제
  동선) 데이터 기반이라, 기존 TourAPI 정적 속성만으로는 못 만드는
  "이 장소와 실제로 같이 다닌 곳" 정보를 준다. 추천/일정(SCHEDULE) 개선에
  쓸모가 있다고 보고, 패키지 경계를 벗어나더라도 우선순위 높은 순으로
  실험해보기로 했다(원 소유는 D 영역에 가깝지만 인프라 구축은 B가 맡음).
- 결정:
  1. `collect_place_associations.py` — `areaBasedList1`을 구 단위로 호출해
     원본 응답(JSONL)을 그대로 보존한다. `NODATA_ERROR`는 그 구만 건너뛰는
     정상 흐름으로 처리하고, `baseYm` 기본값은 매월 8일 갱신 특성을 고려해
     항상 저번 달을 쓴다.
  2. `build_place_association_mappings.py` — 원본의 `tAtsCd`/`rlteTatsCd`
     (32자리 해시코드, TourAPI 표준 content_id와 다른 체계)를 이름+구
     기준으로 `places.content_id`에 매칭한다.
     `place_concentration_mappings`(D-043/D-057)가 이미 푼 같은 문제
     (장소 고유 ID가 없는 외부 API를 이름으로만 매칭)와 같은 보수적 원칙을
     그대로 재사용했다 — exact → normalized → 유일 후보일 때만 substring,
     모호하면 자동 매칭하지 않고 사람 확인용 unmatched로 남긴다. 구 필터를
     이름 비교보다 먼저 적용해 동명이인 장소 오매칭(EXP-01 교훈)을 막는다.
  3. `place_associations` 테이블(마이그레이션
     `202608260001_create_place_associations.sql`) — `from_content_id`/
     `to_content_id`/`base_ym` 복합 PK로 월별 스냅샷을 이력으로 보존한다.
     `import_place_associations.py`가 원본 JSONL과 매핑 CSV를 조인해 양쪽
     다 매칭된 엣지만 적재하고, 재수집 시 upsert(`on_conflict`+
     `merge-duplicates`)로 `rank`/`category`만 덮어써 `created_at`(최초
     적재 시각)은 유지한다. `query_place_associations.py`로 content_id
     기준 조회 헬퍼를 뒀다.
- 근거: 매칭 실패 시 대충 편집거리로 이어붙이면 엉뚱한 장소를 "함께 다니면
  좋은 곳"으로 추천하는 사고가 나므로, 이미 검증된 D-043/D-057 원칙을
  그대로 재사용하는 쪽이 새 휴리스틱을 만드는 것보다 안전하다고 판단했다.
- 채택하지 않은 것:
  - **네이버 포스트 데이터도 함께 적재** — 이 작업 조사 과정에서 중구 RAG
    임베딩(`place_embeddings`)이 구글 리뷰만 있고 다른 구에 있는 네이버
    포스트 소스가 중구엔 없다는 게 눈에 띄었지만, RAG 자체가 아직 추천
    파이프라인에 안 붙어 있고(D-082 참고) 저장소에 네이버 블로그 검색
    연동이 아예 없어 이번 범위에 넣지 않았다. 필요하면 별도 카드로 D와
    협의.
  - **서울 25개 구 전체 선수집** — 파일럿(종로구·중구)으로 매칭률·데이터
    품질부터 확인하는 쪽을 택했다. 원본 2,300건 중 698건만 양쪽 다
    매칭됐고(나머지는 미동기화 구 388건 + 호텔·프랜차이즈 등 매칭 실패
    344건), 전역 확장 전에 이 커버리지 한계를 먼저 알아야 한다고 판단했다.
- 곁가지 발견: `build_place_association_mappings.py`의 `load_places_from_supabase`가
  D-081과 완전히 같은 패턴으로 PostgREST 기본 1000행 상한에 걸려 있었다
  (`limit=2000`을 명시해도 1000건에서 잘림). 같은 페이지네이션 헬퍼 패턴으로
  수정 — 매칭 가능 장소가 1,000건에서 3,671건으로 늘며 매칭 건수도
  76→237건으로 뛰었다. D-081(list_traces_for_stats/list_feedback_for_stats)에
  이어 이 클래스의 버그가 두 번째로 재발한 것이라, PostgREST REST 호출을
  새로 짤 때는 기본적으로 페이지네이션을 넣는 것을 원칙으로 삼아야 한다.
- 검증: 파일럿 실행 로그로 확인 — 원본 2,300건(종로구 1,556 + 중구 744) →
  content_id 매칭 354/1,086건(정확 212 / 정규화 25 / 부분일치 117) →
  엣지 698건 실제 적재(미매칭 1,532 / 자기참조 5 / 중복 65 제외) →
  content_id 조회(`945824` 경교장)로 rank/category 포함 연관 장소 4건
  확인.
- 2026-08-26 확장(같은 날 후속): 파일럿에서 서비스 지원 12개 구(D-083 —
  종로구·중구·용산구·성동구·광진구·동대문구·중랑구·성북구·강북구·도봉구·
  노원구·은평구) 전체로 수집·매칭·적재 범위를 넓혔다. 코드 변경 없이
  `collect_place_associations.py --districts`만 12개 구로 넓혀 같은
  `base_ym`(202607)으로 재실행 — 기존 종로구·중구 698건은 upsert로
  덮어써지고 나머지 10개 구가 새로 추가됐다. 결과: 원본 5,511건 →
  content_id 매칭 666/2,344건 → 엣지 1,612건 실제 적재(미매칭 3,803 /
  자기참조 13 / 중복 83 제외, category: 관광지 1,013 / 음식 438 / 숙박
  161). 매칭률(엣지/원본)이 파일럿 30.3%(698/2,300) → 확장 후 29.3%
  (1,612/5,511)로 거의 그대로 유지돼 커버리지 특성이 구가 늘어도
  일관됨을 확인했다.
- 2026-08-27 확장(2차): D-086으로 서비스 지원 지역이 12개 구에서 16개 구로
  늘었는데(서대문·마포·양천·강서 추가), place_associations는 12개 구 기준
  그대로 남아 새로 지원된 4개 구만 co-visit 데이터가 없는 공백이 생겼다.
  코드 변경 없이 `--districts`만 16개 구 전체로 넓혀 같은 방식으로 재실행 —
  기존 12개 구 행은 upsert로 덮어쓰이고 4개 구가 새로 추가됐다. 결과: 원본
  7,219건 → 매핑 CSV 751건 → 엣지 2,001건 실제 적재(미매칭 5,110 / 자기참조
  15 / 중복 93 제외). 매칭률(엣지/원본)이 27.7%(2,001/7,219)로 파일럿
  30.3%·12개 구 확장 29.3%에 이어 소폭 더 낮아졌지만 같은 하락 추세라 특이
  이상은 아니다.
- 남은 것: 서울 나머지 9개 구(비지원 지역, place-sync 자체가 안 된 구로·
  금천·영등포 등)는 place 데이터가 없어 범위 밖. RECOMMEND 설명문 연동·
  2차 스코어링 반영은 D-092로 완료. SCHEDULE 연동은 D-091로 완료. INFO/
  COMPARE 인텐트에는 아직 이 데이터가 연결되지 않았다(패키지 C·A 협의
  필요, 별도 카드).
- 상세: `backend/scripts/collect_place_associations.py`,
  `backend/scripts/build_place_association_mappings.py`,
  `backend/scripts/import_place_associations.py`,
  `backend/scripts/query_place_associations.py`,
  `supabase/migrations/202608260001_create_place_associations.sql`,
  `supabase/data_dictionary/place_associations.md`

### D-089 — "성수동"처럼 지역 검색에 상호명만 잡히는 동 이름은 Geocoding으로 폴백한다

- 상태: `Accepted` — 구현 완료.
- 배경: "성수동 카페 추천해 줘"(GPS 확보된 상태)가 "말씀하신 목적지 범위가
  여러곳으로 해석돼요"와 함께 성수동과 무관한 종로구 랜드마크 4곳
  (`_LOCATION_REQUIRED_QUICK_PICKS`)을 보여주는 버그 제보. 추적 결과
  `ResolveLocationTool.execute()`는 지역 검색(Naver Local Search)이 뭔가를
  돌려주면(성공이든 애매한 결과든) 그 아래 별칭/Geocoding 폴백 사다리를 아예
  안 탄다. "성수동"을 실제로 지역 검색에 호출해보면 상호명에 "성수"가 들어간
  카페·식당 5건뿐(오르노 성수점/화화돈 성수점/성수온실 성수본점 등)이라
  역·명소 카테고리(`_is_location_pickable`)가 하나도 없어 빈 후보로
  `NO_DATA`/`ambiguous_location`이 되고, `agent_runtime.py`가 그 빈 후보를
  종로구 quick-picks로 대체한다. 한편 같은 "성수동"을 Naver Geocoding에
  실제로 호출하면 정상 해석된다(`성동구 성수동1가`, `(37.542108, 127.04965)`)
  — `docs/api-samples.md`가 이미 기록한 "Geocoding은 행정동/법정동 이름을
  직접 인식한다"는 사실과 일치한다. 문제는 이 폴백이 지역 검색에 막혀 아예
  실행되지 않는 것이었다.
- 결정:
  1. `resolve_location.py`의 `_lookup_local_search()`에서, 지역 검색 후보 중
     역/명소가 하나도 없고(`names_source` 비어 있음) 정확히 같은 이름의
     후보도 없으면(`has_exact_match` False) 기존처럼 `NO_DATA` 에러를 바로
     반환하지 않고 `None`을 반환한다. `execute()`에 이미 있던(현재는 거의
     죽은 코드였던) 별칭/Geocoding 폴백 사다리가 자연스럽게 이어받는다.
  2. 정확히 같은 이름의 후보가 있는 경우(예: "쌈지길" 동명이인 2건)는
     이 폴백에서 제외했다 — Geocoding은 상호명을 인식하지 못하므로
     (`docs/api-samples.md`) 폴백해도 소용없고, 어느 쪽인지 되묻는 기존
     동작이 맞다.
  3. Geocoding으로 얻은 좌표는 `_success_or_policy_result()`의 기존
     `enforce_service_area`/`candidate_count` 가드를 그대로 통과한다 —
     지원 지역 밖 좌표는 여전히 `unsupported_region`, Geocoding 자체가
     애매하면 여전히 `ambiguous_location`으로 정리된다. 새 코드 없이 기존
     안전장치를 재사용했다.
  4. 검토했던 "좌표 기준 가까운 지하철역 버튼", "구 전체로 넓혀 검색
     (district_code 필터 + 넓은 반경)"은 채택하지 않았다 — 아래 참고.
- 채택하지 않은 것:
  - 좌표 기준 가까운 지하철역 3~4개를 후보 버튼으로: 우리 DB(Supabase
    `places`)엔 TourAPI 관광지만 있고 지하철역이 없다. Naver 지역검색도
    좌표 기반 "주변 카테고리 검색"을 지원하지 않아(키워드 검색만 가능)
    역 좌표를 얻으려면 새 정적 데이터셋을 들여와 유지보수해야 한다.
  - 구 전체로 넓혀 검색(`NearbyPlaceDetailsQuery.district_code` 사용 +
    반경 확대): `service_area.py`에 좌표→구 코드 판정 함수, `ResolvedLocation`에
    `district_code` 필드, `agent_context/service.py` 배선까지 필요해 손대는
    파일이 늘어난다. 실측으로 필요성부터 확인했다 — "성수동" Geocoding
    좌표에서 실제 지역 검색으로 확인한 성수역(약 0.62km)·주변 카페들
    (약 0.58~0.83km)까지 전부 기존 기본 검색 반경(`DEFAULT_PLACE_SEARCH_RADIUS_KM`
    = 2.0km, 1km 아님)에 여유 있게 들어와 불필요했다. 동 단위는 구보다 훨씬
    작아 좌표 하나로도 실질적으로 충분하다는 게 확인된 셈이다. 다만 이건
    "성수동" 한 곳에 대한 확인이라 다른 동/구에서도 항상 맞는다는 보장은
    아니다 — 실제 배포 후 "반경이 좁아서 결과가 부족하다"는 사례가 나오면
    이 방향을 다시 꺼내 쓸 수 있게 남겨둔다.
  - `NearbyPlaceDetailsQuery.district_code`를 평소 경로에도 채우는 안:
    D-025가 이미 "구로 좁히면 반경 안의 옆 지원 구 후보가 잘린다"는 이유로
    의도적으로 안 쓰기로 정한 것이라 건드리지 않았다.
- 검증: 백엔드 `pytest` 2,767 passed(무관한 기존 langfuse 환경 이슈 1건
  제외), `ruff check` 클린. Naver Local Search·Geocoding 두 API를 실제
  자격증명으로 직접 호출해 "성수동" 지역 검색 결과(카페·식당뿐)와 Geocoding
  좌표, 그 좌표에서 성수역·주변 카페까지의 거리를 실측 확인.
- 남은 것: `agent_runtime.py`의 종로구 고정 quick-picks(`_LOCATION_REQUIRED_QUICK_PICKS`)
  자체는 이번 범위 밖(TP-160). 이 수정으로 quick-picks가 필요한 잔여
  트리거는 "완전히 좌표 신호가 없는 경우"와 "Geocoding도 실패하는 경우"
  두 가지로 좁혀졌다는 점을 TP-160에 덧붙일 필요가 있다.
- 상세: `backend/app/tools/resolve_location.py`,
  `backend/tests/test_resolve_location_tool.py`,
  `backend/app/providers/geocoding.py`

### D-090 — 실시간 혼잡도 카드에 단계별 색상·게이지·전망 인사이트를 추가한다

- 상태: `Accepted` — 구현 완료.
- 배경: D-084로 82/121 지역 목록을 고친 뒤에도 "실시간 혼잡도" 기능이 잘
  체감되지 않는다는 지적. 서울시 공식 앱과 비교하면 우리는 이미 같은 데이터
  (`FCST_PPLTN` 12시간 예측, `AREA_CONGEST_LVL`)를 갖고 있으면서도, 예측
  그래프가 항상 단색(인구=파랑, 집중률=amber)이고 현재 단계를 보여주는 시각
  요소(게이지)가 없었다. 또 상세 모달(`RecommendationDetailPreviewModal`)에는
  이 그래프들이 아예 렌더링되지 않아 카드 클릭 시 기대한 시각 정보가 빠져
  있었다.
- 결정:
  1. `frontend/src/components/chat/CongestionForecastBars.tsx`를 새로 만들어
     `PlaceInfoCard.tsx`에 있던 `ConcentrationForecastBars`/
     `PopulationForecastBars`를 이 파일로 옮기고 export했다 — 요약 카드와
     상세 모달이 같은 컴포넌트를 공유한다. 막대 색을 레벨별 매핑 테이블로
     바꿨다: 인구 예측은 서울시 원문 한글(`여유`/`보통`/`약간 붐빔`/`붐빔`),
     집중률 예측은 `ConcentrationLevel` 영문 코드(`quiet`/`normal`/
     `slightly_crowded`/`crowded`) — 두 값 체계가 달라 매핑 테이블도
     각각 둔다. emerald→amber→orange→red 4단계 팔레트를 공유하고, 모르는
     레벨은 회색 fallback으로 처리해 깨지지 않게 한다.
  2. `CongestionLevelGauge` 컴포넌트를 신설 — 여유/보통/약간 붐빔/붐빔 4단계
     가로 세그먼트 바 위에 현재 단계를 가리키는 마커(▼)를 얹는다. 새 차트
     라이브러리 없이 기존 막대그래프와 같은 순수 CSS로 구현. 값이 없으면
     아무것도 렌더링하지 않는다.
  3. `RecommendationDetailPreviewModal.tsx`가 이제 `population_forecasts`/
     `concentration_forecasts`가 있을 때 같은 그래프를 렌더링한다.
     `needsDetailEnrichment()`가 `realtime_map_url`/`realtime_detail_items`
     있는 카드는 PlaceDetails 재조회를 건너뛰므로(기존 동작), 혼잡도 카드는
     원본 예측 데이터가 `detailCard`에 그대로 남아 있어 배관 작업 없이
     렌더만 추가하면 됐다.
  4. 향후 예측 중 가장 붐비는 시간대를 "16시(2시간 후)에 가장 붐빌 것으로
     예상돼요" 형태 한 줄로 요약하는 `_summarize_population_peak()`을
     `info_response_transform.py`에 추가, `InfoPlaceCard.population_peak_forecast_summary`
     로 새로 내려준다. 인덱스를 "1시간 후"로 가정하지 않고, 관측 시각과
     예측 시각을 `info_display.py`에 새로 뽑은 `parse_citydata_timestamp()`
     (기존 `format_citydata_timestamp()`와 같은 정규식 재사용)로 실제 파싱해
     시간 차이를 구한다. 전부 같은 단계거나 파싱 실패 시 문장을 생략한다
     (억지로 만들지 않음). 채팅 말풍선 텍스트(`compose_realtime_population_message`)
     는 기존 회귀 테스트가 정확한 문자열을 검증하고 있어 손대지 않았다 —
     새 인사이트는 카드에만 싣는다.
  5. "과거 12시간 추이"(참고 이미지에 있던 기능)는 서울시 API가 애초에
     미래 방향(`FCST_PPLTN`)만 제공해 원본 데이터가 없다 — 못 만든 게
     아니라 데이터가 없는 것으로 범위에서 제외했다. 현재 인구 수 실측치
     (`AREA_PPLTN_MIN/MAX`)도 참고 이미지에 노출되지 않는 값이라 이번
     스코프에서 제외했다(여전히 파싱되지 않고 버려짐).
  6. (후속) 사용자가 예측 그래프가 오후 5시 등 "현재 시각부터"만 보여
     기준점이 안 보인다고 지적. 실제 과거 시간대는 위 5번과 같은 이유로
     여전히 못 채우지만(서울시 API 미제공, 우리가 직접 폴링해 쌓는 방안은
     새 파이프라인이 필요한 큰 작업이라 이번엔 보류하기로 사용자와 합의),
     `PopulationForecastBars`의 예측 막대 맨 앞에 `population_current_level`
     기준 "현재" 막대를 하나 추가해 시각적 기준점을 뒀다 — 점선 구분선
     (`border-r-2 border-dashed`)과 강조 테두리(`ring-2`)로 예측 막대와
     구분한다. 실제 과거 데이터를 꾸며내지 않고, 지금 갖고 있는 현재값
     하나만 정직하게 강조하는 선에서 마무리했다.
- 검증: 백엔드 `pytest` 2,765 passed(무관한 기존 langfuse 테스트 1건 제외),
  `ruff` 클린. 프론트 `vitest` 24개 파일 177건 통과(`CongestionForecastBars.test.tsx`
  신규 5건, `PlaceInfoCard.test.tsx`에 게이지·색상·상세 모달 노출 테스트 추가),
  `tsc --noEmit`·`eslint`·`vite build` 클린.
- 채택하지 않은 것:
  - 채팅 말풍선 텍스트에 피크 인사이트를 직접 넣는 안 — 기존 문자열 검증
    테스트를 깨뜨리고, "자세한 건 카드, 말풍선은 짧게"라는 기존 패턴과도
    어긋나 카드 전용으로 뒀다.
  - 실제 과거 12시간을 서울시 API 주기적 폴링으로 직접 쌓는 안(6번) —
    새 스케줄러·저장소가 필요한 별도 프로젝트급 작업이고, 처음 조회하는
    장소는 데이터가 쌓이기 전까진 어차피 과거 구간이 빈다는 콜드스타트
    문제도 있어 사용자가 이번 스코프에서는 보류를 선택했다.
- 상세: `frontend/src/components/chat/CongestionForecastBars.tsx`(신규),
  `frontend/src/components/chat/PlaceInfoCard.tsx`,
  `frontend/src/components/chat/RecommendationDetailPreviewModal.tsx`,
  `frontend/src/types.ts`, `backend/app/services/runtime/info_display.py`,
  `backend/app/services/runtime/info_response_transform.py`,
  `backend/app/schemas.py`
  
### D-091 — SCHEDULE 일정 편성에 place_associations "함께 방문된 이력"을 opt-in으로 연결한다

- 상태: `Accepted` — 구현 완료. `agent_runtime.py`(A) 배선, `SchedulePartialFillRequest`
  연동, RECOMMEND 2차 스코어링 연동(D-092)까지 모두 반영됐다(아래 "남은 것" 갱신 참고).
- 배경: D-088로 만든 `place_associations`(TourAPI 관광지별 연관 관광지 정보)를
  실제 추천/일정 경로에 연결하는 건 D-088에서 범위 밖으로 남겨뒀다. SCHEDULE
  (`backend/app/schedule/`)은 프롬프트·코드 전부 B 소유(`OWNERS.md`)라 B
  혼자 구현할 수 있는 통합 지점이었고, `SchedulePlanningRequest.candidates`
  (D의 `RecommendationItem`)에 이미 `place_id`가 있어 D의 스키마 변경 없이도
  후보 집합 안에서 연관 쌍을 찾을 수 있었다.
- 결정:
  1. `backend/app/schedule/associations.py` 신설 — `fetch_co_visited_hints()`가
     후보 place_id 집합을 `from_content_id`/`to_content_id` 양쪽에 동시에
     `in.()` 필터로 걸어, 후보 집합 안에서 완결되는 co-visit 쌍만 가져온다.
     후보가 2개 미만이거나 `supabase_url`이 비어 있으면 네트워크 호출 자체를
     생략한다.
  2. `SchedulePlanningRequest`에 `co_visited_hints: list[CoVisitedHint] = []`를
     추가한다(B 소유 스키마, `app/schedule/schemas.py`). 기본값이 빈 리스트라
     이 필드를 모르는 기존 호출부는 동작이 전혀 바뀌지 않는다.
  3. `plan_schedule()`에 `co_visited_fetcher`를 opt-in 키워드 인자로 추가한다
     — 기본값 `None`이면 이 함수는 기존과 바이트 단위로 동일하게 동작한다.
     실제로 켜려면 호출부(agent_runtime.py)가
     `co_visited_fetcher=fetch_co_visited_hints`를 넘겨야 한다. 조회가
     실패해도(네트워크·설정 문제) 예외를 삼키고 힌트 없이 계속 진행한다 —
     이 힌트는 참고 정보일 뿐 SCHEDULE의 핵심 기능이 아니다.
  4. `format_schedule_planning_context()`(gemini_prompts.py)에
     `[함께 방문된 이력]` 섹션을 추가하고, 비어 있으면 "(없음)"으로 채운다
     (`format_schedule_fill_context()`의 `pinned_lines` fallback과 같은
     패턴). `plan.md`에 "쌍이 있으면 인접 배치를 고려하되 거리·운영시간·
     활동 가능 시간이 우선"이라는 규칙을 추가했다 — 이 신호 하나로 동선을
     비효율적으로 만들지 않게 하는 안전장치다. `schedule.plan`/
     `schedule.plan_context` 버전을 1.0.0 → 1.1.0으로 올렸다.
- 채택하지 않은 것:
  - **plan_schedule() 안에서 항상 자동으로 조회** — 기존 SCHEDULE 테스트
    전부가 실제 네트워크 호출을 타게 되고, agent_runtime.py의 하드 타임아웃
    가정이 깨질 위험이 있다. opt-in 키워드 인자로 만들어 A가 준비됐을 때
    한 줄만 추가하면 켜지게 했다.
  - **D의 RecommendationItem 스키마 확장(예: co-visit 플래그 미리 계산)** —
    place_id 하나로 이미 충분해서 D 쪽 변경을 요구할 이유가 없었다.
- 검증: `app/schedule/associations.py` 요청 파라미터(양쪽 컬럼 `in.()` 필터,
  후보 2개 미만/설정 없음 시 호출 생략, 중복 id 정리)를 `httpx.MockTransport`로
  고정. `plan_schedule()`/`plan_partial_schedule()`이 (a) fetcher 미지정 시
  `co_visited_hints`가 항상 빈 리스트임을, (b) fetcher가 준 힌트가 LLM 요청에
  그대로 실림을, (c) fetcher가 예외를 던져도 일정 편성 자체는 성공함을 각각
  회귀로 고정. 프롬프트 스냅샷(`schedule_plan_context`, `schedule_plan__*`,
  `schedule_fill`, `schedule_fill_context`)을 갱신해 새 섹션·규칙 문구를
  바이트 단위로 고정. 실제 `pytest`(2886건 전체 스위트)를 로컬 shim
  (StrEnum/datetime.UTC 3.11 전용 문법을 3.10에 되살리는 conftest, 세션
  한정 임시 파일)으로 실행해 통과 확인 — 이전에는 "샌드박스 Python 버전 때문에
  직접 실행 불가"로 남겨뒀던 항목인데, StrEnum shim에 `__str__`을 값 그대로
  반환하도록 보강하니 실행 가능하다는 걸 이번에 확인했다(D-092 "검증" 참고).
- 남은 것: 없음. `agent_runtime.py` 배선(A), `SchedulePartialFillRequest`
  연동, RECOMMEND 2차 스코어링 연동은 모두 D-092까지 반영됐다.
- 상세: `backend/app/schedule/associations.py`, `backend/app/schedule/schemas.py`,
  `backend/app/schedule/planner.py`, `backend/app/providers/gemini_prompts.py`,
  `backend/app/prompts/schedule/plan.md`, `backend/app/prompts/schedule/plan_context.md`,
  `backend/app/prompts/schedule/fill.md`, `backend/app/prompts/schedule/fill_context.md`,
  `backend/app/prompts/schedule/meta.yaml`, `backend/app/prompts/schedule/HISTORY.md`,
  `backend/app/services/runtime/agent_runtime.py`,
  `backend/tests/schedule/test_associations.py`, `backend/tests/schedule/test_planner.py`,
  `backend/tests/prompts/snapshots/schedule_plan_context.txt`,
  `backend/tests/prompts/snapshots/schedule_plan__no_limit.txt`,
  `backend/tests/prompts/snapshots/schedule_plan__with_time_available.txt`,
  `backend/tests/prompts/snapshots/schedule_fill.txt`,
  `backend/tests/prompts/snapshots/schedule_fill_context.txt`

### D-092 — RECOMMEND 2차 스코어링에 place_associations "함께 방문된 이력"을 반영한다 (D-040 패턴 재사용)

- 상태: `Accepted` — 구현 완료.
- 배경: D-091로 SCHEDULE에는 연결했지만, RECOMMEND(순수 장소 추천) 목록 자체의
  순위에는 아직 co-visit 신호가 없었다. D-040(`rerank_with_concentration()`,
  혼잡도 2차 Scoring)이 정확히 같은 모양의 문제 — "1차 결과를 다시 만들지 않고
  새 Feature 하나로 재채점"— 를 이미 풀어둔 패턴이라 그대로 재사용했다.
- 결정:
  1. `scoring.OPTIONAL_FEATURES`에 `"co_visited"`를 추가한다(`("taste",
     "concentration", "co_visited")`) — `_MAX_OPTIONAL_FEATURES=3`이 예고해둔
     정확히 그 세 번째 자리다. 튜플에 넣는 것만으로는 기존 요청 어느 것의
     점수도 바꾸지 않는다 — `feature_scores`에 `"co_visited"` 키가 실제로
     있는 요청(아래 3번을 탄 요청)에서만 가중치 조립이 이 이름을 본다.
  2. `scoring.co_visited_score(hit_count, max_hit_count)` 신설 — 이번 응답
     안에서 이 후보가 "함께 방문된 이력" 쌍에 몇 번 등장했는지를, 이번
     응답에서 관측된 최댓값 대비 0~1로 정규화한다. concentration_rate(0~100
     고정 스케일)와 달리 절대 스케일이 없어 상대 정규화를 택했다 — taste_score가
     실측 분포 상한(0.65)에 맞춰 클리핑한 것과 같은 이유. 쌍이 없는 후보도
     0.0이지 결측이 아니다(_taste_score와 같은 이유 — 후보마다 결측 여부가
     갈리면 한 순위 안에서 가중치 세트가 달라진다).
  3. `recommendation_pipeline.rerank_with_co_visited(response, co_visited_pairs,
     weather_condition, ...)` 신설 — `rerank_with_concentration()`과 거의
     동일한 구조(1차 feature_scores 재사용, `weights_for_feature_scores()`로
     실제 채점 키만 보고 가중치 재조립, `build_evidence()`/`build_explanations()`로
     근거 문장 재조립). 입력은 B의 `CoVisitedHint` 스키마가 아니라 순수
     `(place_id, place_id)` 쌍이다 — D가 B의 스키마를 몰라도 되게 하려고
     여기서 경계를 그었다(B-01 "판단하지 않는 기억 장치" 경계 원칙을 D→B
     방향에도 적용).
  4. 근거 문장 계층(`evidence.py`/`explanation.py`)에도 `co_visited` 축을
     추가한다 — `_BASE_FEATURE_ORDER`에 추가, `RankedCandidate`/
     `RecommendationEvidence`에 `co_visited_place_names`(함께 방문된 다른
     후보 이름, 최대 2개) 필드 추가, `explanation._SENTENCE_BUILDERS`에
     `_co_visited_sentence()` 등록. **이 등록을 빠뜨리면 co_visited가
     notable(점수 ≥ 0.7)일 때 `_SENTENCE_BUILDERS[contribution.feature]`가
     `KeyError`로 응답 자체를 깨뜨린다** — `build_explanations()`가 dict
     조회에 `.get()` 폴백을 안 쓰기 때문에 구현 중 실제로 확인했다(테스트로도
     고정, 아래 "검증" 참고).
  5. A↔D 배선은 `rerank_with_concentration()`과 동일한 3계층(Protocol
     `RecommendationProvider.rerank_with_co_visited()` — `RealRecommendationProvider`
     구현 — `agent_runtime._apply_co_visited_rerank()`)으로 넣는다. `hasattr()`
     가드는 그대로 재사용해, 테스트 더블(`FakeRecommendationProvider`)이 이
     메서드를 갖추지 않아도 기존 동작이 그대로 유지된다 — `stubs.py`는 의도적으로
     건드리지 않았다. `_apply_co_visited_rerank()`는 `_apply_concentration_rerank()`
     바로 뒤에 이어 호출한다 — concentration_intent 게이트가 없다(co-visit은
     방향 개념이 없는 사실 신호라 쌍이 없으면 0.0이 되어 무해하다). 두 2차
     Scoring이 같은 응답에 동시에 얹힐 수 있다 — OPTIONAL_FEATURES가 정확히
     그 경우(taste+concentration+co_visited=3)를 지원하도록 설계돼 있었다.
     co-visit 쌍 조회는 B가 SCHEDULE에서 쓰는
     `app.schedule.associations.fetch_co_visited_hints()`를 그대로 재사용한다.
- 채택하지 않은 것:
  - **D가 B의 `CoVisitedHint`를 직접 import** — `real_recommendation_provider.py`가
    B의 `app/schedule/` 스키마를 알아야 하게 된다. A가 힌트를 조회해
    `(place_id, place_id)` 쌍으로 낮춰서 넘기게 해 D→B 의존을 없앴다.
  - **concentration_intent와 같은 조건 게이트 추가** — co-visit은 세워야 할
    "의도"가 없는 사실 신호라 게이트가 필요 없다. 쌍이 없으면 자연히 0.0이라
    항상 켜둬도 순위가 무의미하게 흔들리지 않는다.
- 검증: `co_visited_score()` 정규화(최댓값 대비 비율, 쌍 0개는 0.0, 최댓값
  0도 0.0)를 단위 테스트로 고정(`test_scoring.py`). `weights_for_feature_scores`가
  taste+concentration+co_visited 3개 동시 활성을 정확히 조립하는지
  (`test_scoring_weight_composition.py`), `resolve_feature_order`가 co_visited를
  마지막 순서로 두는지(`test_evidence_feature_order.py`), `_co_visited_sentence()`가
  이름을 인용하고 이름이 없으면 크래시 대신 폴백 문구를 내는지·임계값 미만/
  0.0은 문장을 생략하는지(`test_explanation.py`)를 각각 고정. `rerank_with_co_visited()`는
  거리 우선 1차 순위가 co-visit 쌍으로 실제 뒤집히는지, taste 축을 이월하는지,
  자기 자신·응답 밖 id 쌍을 방어적으로 걸러내는지, 실측 이동 정보·unverified
  분리를 이월하는지를 회귀로 고정(`test_recommendation_pipeline.py`).
  `RealRecommendationProvider.rerank_with_co_visited()`가 1차와 같은
  `resolve_weather_condition()`/`origin_name`을 재사용하는지도
  고정(`test_real_recommendation_provider.py`, D-051과 같은 이유).
  실제 `pytest` 전체 스위트(2,900여 건, langfuse 포함)를 로컬 shim으로 실행해
  전부 통과 확인 — 실패했던 것은 shim 자체의 결함(StrEnum `__str__`이 값이
  아니라 `"ClassName.MEMBER"`를 반환해 상태 직렬화 테스트가 깨짐)이었고, shim을
  고치자 재발했던 135건이 전부 사라졌다. `ruff check`도 통과.
- 상세: `backend/app/domain/scoring.py`, `backend/app/domain/evidence.py`,
  `backend/app/domain/explanation.py`, `backend/app/services/recommendation_pipeline.py`,
  `backend/app/services/runtime/protocols.py`,
  `backend/app/services/runtime/real_recommendation_provider.py`,
  `backend/app/services/runtime/agent_runtime.py`,
  `backend/tests/test_scoring.py`, `backend/tests/test_scoring_weight_composition.py`,
  `backend/tests/test_evidence_feature_order.py`, `backend/tests/test_explanation.py`,
  `backend/tests/test_recommendation_pipeline.py`, `backend/tests/test_real_recommendation_provider.py`

### D-093 — 지하철 방향 충돌 버그 수정, 주차 공영/민영 그룹핑, 도로소통 신규 연결

- 상태: `Accepted` — 구현 완료.
- 배경: 사용자가 "지하철은 종로구만 되는 것 같다", "주차는 실시간 정보
  미제공인 곳이 많아 보인다", "도로소통은 연결 안 했는데 서울시가 제공하는
  것 같다"고 지적. 실제 `citydata` API를 여러 지역(경복궁·이촌한강공원·
  교대역·강남역·홍대 관광특구)에 직접 호출해 확인한 결과, 예상보다 필요한
  작업이 작았다 — 도로소통과 주차 공영/민영 구분 모두 새 API 연동이 필요
  없고, 이미 매번 호출하는 `citydata` 응답 안에 있는데 파싱만 안 하고
  버리고 있었다.
- 실측으로 확정한 사실:
  1. **지하철 "종로구만"은 지역 제한이 아니라 버그였다.** `_fetch_realtime_city_info`
     는 121개 지역(서울 전역) 중 최근접 1곳을 찾으므로 지역 제한 코드가
     없다. 진짜 원인은 요약 카드의 `fields` 딕셔너리 키를 `"역이름 호선"`
     으로만 만들어, 같은 역·같은 호선의 상행/하행 두 항목이 같은 키로
     충돌해 한쪽이 지워지는 것이었다(모달용 `detail_items`는 리스트라
     방향이 보존돼 있었다). 부수적으로 121개 지점의 구별 밀도가 크게
     다르다는 것도 로컬 계산(경계 폴리곤 vs 좌표 대조)으로 확인했다(종로구
     14·중구 10 vs 성북/도봉/노원/은평/양천 1~2, 중랑구만 구 내부 0개지만
     경계에서 0.72km라 기존 2km 허용치 안에는 든다) — 이건 코드로 보정하지
     않고 사실만 남긴다.
  2. **주차장 공영/민영 구분도 이미 받고 있었다.** `PRK_STTS`의 `PRK_TYPE`
     필드를 파싱 코드가 버리고 있었다. 실측(교대역 14곳·강남역 95곳·홍대
     51곳)으로 코드값을 확정: `NW`(노외주차장)·`NS`(노상주차장)=공영,
     `BS`(부설주차장)·`NP`(개별 민영)=민영. 교대역 실측이 사용자가 첨부한
     서울시 공식 앱 캡처와 정확히 일치했다(`NW` 1곳+`NP` 1곳+`BS` 12곳,
     민영 목록 이름까지 캡처와 동일). 별도 데이터셋(`GetParkInfo`)도 실제
     호출해봤는데(기존 키로 인증 없이 잘 불림 — 별도 API 신청 불필요)
     공영주차장 전용에 2019년까지 거슬러 올라가는 낡은 데이터라 이번
     목적엔 안 맞아 채택하지 않았다.
  3. **실시간 주차 대수는 실측 결과 대부분 비어 있다.** 교대역 14곳·강남역
     95곳·홍대 51곳 전부 `CUR_PRK_YN=N`. 이촌한강공원만 유일하게 "Y"였는데
     `CUR_PRK_TIME`이 2025-02로 낡았다. 기대치를 낮추고 공영/민영·총면수·
     요금 위주로 카드를 구성한다.
  4. **`PRK_STTS`에 같은 주차장이 중복으로 온다.** 이촌한강공원 조회에서
     "이촌3, 4주차장"(`PRK_CD` 동일)이 실시간 정보 없는 항목과 있는 항목
     두 번 들어왔다 — `PRK_CD` 기준으로 병합하고 실시간 정보가 있는 쪽을
     남기도록 고쳤다.
  5. **도로소통(`ROAD_TRAFFIC_STTS.AVG_ROAD_DATA`)이 이미 payload에 있었다.**
     단계(원활/서행/정체)·평균속도·안내문구까지 캡처 화면과 정확히
     대응한다. **24시간 추이는 이 응답 어디에도 없다** — 개별 도로 링크
     배열(좌표 폴리라인 포함)은 있지만 시간별 이력은 없다. 인구 "지난
     12시간 추이"(D-089에서 보류)와 같은 종류의 한계라 이번에도 스냅샷만
     다루고 24시간 추이는 제외했다.
- 결정:
  1. 지하철: `service.py`의 `realtime_subway` 분기에서 `fields` 키에 방향을
     포함(`"강남역 2호선 · 잠실행"`)하고, 역+호선 단위로 그룹핑해 서로 다른
     방향을 우선 포함하도록 정렬을 바꿨다.
  2. 주차: `RealtimeParkingLot`에 `code`(PRK_CD, 중복 제거용)·`lot_type`
     (공영/민영) 필드를 추가하고 `map_realtime_parking_response`에서
     매핑·중복 제거를 함께 처리한다. `service.py`의 `realtime_parking`
     분기는 공영/민영으로 나눠 각각 상위 몇 곳을 보여주고, 한쪽이 비어도
     (이촌한강공원 민영 0곳, 교대역 공영 0곳처럼) 있는 쪽만으로 정상
     응답한다. `question_type_rules.md`의 트리거도 "지금/현재/실시간"
     필수 요건을 없애 "주변 주차되는 곳 있어?"처럼 시제 없는 질문도
     받는다(TP-115).
  3. 도로소통: 새 `question_type=realtime_traffic` 추가. `RoadTrafficStatus`
     도메인 모델과 `map_realtime_traffic_response()`를 새로 만들고 기존
     `_fetch_realtime_city_info`에 분기 하나만 추가했다 — 지역당 API 호출
     1회 그대로 유지(이미 받는 응답에서 필드만 더 읽는다). 채팅 말풍선은
     다른 realtime_* 유형과 달리 "카드 확인" 유도가 아니라 단계·속도 값을
     바로 담는다(항목 하나짜리 스냅샷이라 카드로 미룰 이유가 없다).
     프론트는 `CongestionLevelGauge`를 4단계 전용에서 `levels` prop을 받는
     범용 컴포넌트로 일반화해 도로소통(3단계: 원활/서행/정체)과 인구(4단계)
     가 같은 컴포넌트를 공유한다.
  4. `question_type_rules.md` 변경은 A/C 공유 프롬프트 버전 관리 규칙을
     따른다 — 기존 v3.1.0을 `archive/question_type_rules__legacy-3.1.md`에
     보관하고 `meta.yaml`을 v3.2.0으로 올렸다. `evals/question_type_cases.csv`
     에 케이스 5건(주차 완화 2건, 도로소통 신규 2건, 정적 parking 회귀 1건)
     을 추가하고 `scripts.evaluate_info_question_type --repeat 5`로 실제
     Gemini를 호출해 검증했다 — 기존 21건 회귀 없음(전부 100%), 신규
     5건도 도입 즉시는 스키마(`InfoQuestionType`)에 `realtime_traffic`이
     없어 0%로 전부 실패했다가, 스키마에 추가한 뒤 재실행하니 23건 전체
     100%·전부 stable로 통과했다 — **프롬프트 규칙만 바꾸고 출력 스키마를
     안 바꾸면 LLM이 그 값을 고를 수 없다는 걸 실측으로 다시 확인한
     셈**이다. HISTORY.md에는 다중 턴 회귀(`evaluate_agent_quality --split
     dev`)까지는 이번 범위가 좁아 생략했다고 정직하게 남기고 Draft로
     기록한다(팀 통상 프로세스는 다중 턴까지 포함).
- 검증: 백엔드 `pytest` 2,868 passed(무관한 기존 langfuse 테스트 1건 제외),
  `ruff` 클린. 프론트 `vitest` 24개 파일 182건 통과, `tsc --noEmit`·
  `eslint`·`vite build` 클린. 단일 턴 프롬프트 eval 23케이스 100%(위 참고).
- 채택하지 않은 것: `GetParkInfo`(공영주차장 전용 별도 데이터셋) — 실제
  호출까지 해봤지만 낡고 범위가 좁아 기존 `citydata`의 `PRK_TYPE` 파싱으로
  대체.
- 상세: `backend/app/agent_context/service.py`,
  `backend/app/providers/seoul_citydata.py`, `backend/app/domain/models.py`,
  `backend/app/agent_context/info_schemas.py`, `backend/app/schemas.py`,
  `backend/app/services/runtime/response_composer.py`,
  `backend/app/prompts/info/question_type_rules.md`(+`meta.yaml`+`archive/`+
  `evals/question_type_cases.csv`),
  `frontend/src/components/chat/CongestionForecastBars.tsx`,
  `frontend/src/components/chat/PlaceInfoCard.tsx`,
  `frontend/src/components/chat/RecommendationDetailPreviewModal.tsx`

### D-094 — 분위기 임베딩을 조회 계층에 연결한다

- 상태: `Accepted` — 구현 완료(D-087 후속, 패키지 C).
- 배경: D-087로 벡터는 적재됐지만 서비스가 읽을 방법이 없었다. 631곳의 벡터가
  테이블에 앉아만 있는 상태였다. D-087이 범위 밖으로 미룬 서비스 배선 중
  **조회까지**를 붙인다.
- 결정:
  1. 계약 `PlaceMoodRepository`를 `PlaceEvidenceRepository`와 나눈다. 경로가 둘이고
     비용이 크게 다르다 — `find_mood_profiles`(발화)는 미리 계산된 `axis_scores`만
     읽어 **임베딩 모델이 필요 없고**, `search_place_mood`(사진)만 SigLIP을 요구한다.
  2. **인코더가 없어도 Provider는 만든다.** 발화 경로는 모델 없이 돌아가므로,
     사진 경로만 `photo_search_available`로 막는다.
  3. RPC `search_place_mood`의 후보 규칙을 `search_place_evidence`와 다르게 둔다.
     후보가 `null`이면 전체 검색을 허용하고, **빈 배열은 `null`과 다르게 0건으로
     끝낸다.** 후보를 넘길 때는 500건 상한을 둔다.
  4. **유사도 컷은 0.0으로 두고 순위만 쓴다.**
  5. 발화 경로 조회는 `embedding` 컬럼을 빼고 읽는다.
  6. 선택 의존성을 `[embeddings]`(취향)와 `[mood]`(사진)로 나눈다. 스위치는
     `PLACE_MOOD_ENABLED`(기본 off)다.
- 근거:
  - **계약을 나눈 이유.** 두 벡터가 둘 다 768차원이지만 좌표계가 달라, 한 계약에
    두면 호출부가 헷갈릴 수 있다(D-087과 같은 이유).
  - **인코더 부재를 빈 벡터로 흉내내지 않는 이유(D-042).** 0으로 채운 벡터를
    넘기면 유사도가 전부 같아져 아무 장소나 순서대로 돌아오고, 그게 추천으로
    나가면 **틀린 줄도 모른다.** `npm run dev`가 fake로 부팅해 "테스트 카페"를
    추천했던 사건과 같은 모양이다.
  - **후보 규칙이 다른 이유.** `search_place_evidence`는 40,389행이라 좁히지 않으면
    6~9초가 걸려 좁힘을 강제한다. `place_mood_vectors`는 장소당 한 행이라 지금
    631행이고 서울 전체로 넓혀도 6,000~10,000행이다. "이 사진과 닮은 곳 아무데나"가
    실제로 있을 수 있는 질문이고 HNSW가 그 경로를 받쳐준다. 다만 후보 배열을
    넘기면 인덱스를 무력화하므로 상한은 같이 둔다.
  - **빈 배열과 `null`을 구분하는 이유.** 후보를 좁히려다 전부 걸러진 호출이 전체
    검색으로 둔갑하면, 지역 필터를 통과하지 못한 장소가 추천에 섞인다.
  - **컷을 0.0으로 둔 이유.** 축 점수는 사람 정답표 77곳으로 AUC를 쟀지만(D-087),
    사진끼리의 "이 정도면 닮았다" 경계는 표본이 없다. **근거를 댈 수 없는 숫자를
    코드에 남기지 않는다.**
  - **`embedding` 컬럼을 빼고 읽는 이유.** 768개 float을 장소마다 실어 오면 응답이
    수 MB가 되는데, 발화 경로는 축 점수만 쓴다.
  - **의존성을 나눈 이유.** 취향만 켜는 배포가 SigLIP까지 받을 이유가 없다. torch는
    양쪽이 공유하므로 둘 다 설치해도 한 벌만 받는다.
- 검증: 마이그레이션 적용 후 이미 적재된 벡터를 질의로 써서 RPC를 직접 확인했다
  (SigLIP 없이 가능하다). 마우스래빗(카페)을 넣으면 자기 자신이 1.0000으로 1등,
  이어서 공근혜갤러리 0.8939 · 마을극장 흰고무신 0.8850 · 북한인권전시실 0.8844가
  나온다 — D-087이 기대한 "텍스트로는 찾을 수 없는 이웃"이 재현된다.
  후보 규칙은 네 경우로 확인했다: `null` 100건(limit) · 빈 배열 0건 · 후보 2곳 1건 ·
  컷 0.88 이상 4건(위 순위와 일치). 권한은 `service_role`만 실행 가능하고
  `anon`·`authenticated`는 막혀 있음을 확인했다. 테스트 23건 추가, 전체 2,881건
  통과(무관한 기존 langfuse 테스트 1건 제외), `ruff` 클린.
- 채택하지 않은 것:
  - **취향 근거와 같은 계약에 넣기** — 좌표계가 다르다(위 근거).
  - **후보 좁힘 강제** — 행 수가 두 자릿수 배 차이라 같은 규칙을 쓸 이유가 없다.
  - **결측을 0점으로 채우기** — 사진이 없는 장소가 "분위기가 안 맞는 곳"으로 잘못
    밀린다. **분위기 벡터가 없는 장소가 정상이다** — 사진 임베딩은 종로구까지만
    적재돼 있다. 커버리지는 `place_mood_coverage` 점수로 관측에 올려, 적재 범위를
    넓힐 시점을 숫자로 알 수 있게 했다.
- 남은 것:
  - **추천 재정렬** — `domain/scoring.py`, `services/recommendation_pipeline.py`는
    D 패키지 소관이라 손대지 않았다. 이것이 붙어야 순위가 실제로 바뀐다.
  - **발화에서 축을 고르는 단계** — A 패키지 소관이다(D-087의 계약 그대로).
  - **사진 업로드 경로** — 라우트와 화면이 필요해 별도 작업이다.
  - **유사도 컷 실측** — 표본을 모아 경계를 정해야 한다.
- 상세: `supabase/migrations/202608260004_create_search_place_mood.sql`,
  `backend/app/providers/place_mood.py`,
  `backend/app/providers/place_mood_encoder.py`,
  `backend/app/repositories/protocols.py`,
  `backend/app/repositories/supabase_places.py`,
  `backend/app/domain/models.py`, `backend/app/providers/factory.py`

### D-095 — 집중률 조회의 구를 장소에 맞춰 고르고, 구를 모르면 조회하지 않는다

- 상태: `Accepted` — 구현 완료.
- 배경: 집중률 조회의 구 코드가 종로구로 고정돼 있었다.
  `agent_context/enrichment_service.py`가 `JONGNO_CONCENTRATION_DISTRICT_CODE`
  (`"11110"`)를 장소와 무관하게 넘겨, 사용자 요청으로 도는 조회는 전부 종로구로
  나갔다. 매핑(`place_concentration_mappings`)이 전부 종로구였던 동안은 이 값이
  맞아 문제가 드러나지 않았다.
- 실측으로 확정한 사실:
  1. **집중률 API는 `signguCd`로 엄격하게 거른다.** 같은 이름으로 구만 바꿔
     질의했다(2026-08-26). 명동성당은 종로구(11110)에 `totalCount=0`,
     중구(11140)에 30. 덕수궁도 같다. 경복궁은 반대로 종로구 30, 중구 0.
     즉 다른 구 매핑이 들어오는 순간 그 장소는 언제나 0건이 된다.
  2. **매핑을 먼저 적재하면 지금 값이 나오던 장소가 no_data로 회귀한다.**
     대체 조회는 500m 안의 매핑 장소를 거리순 3곳까지 시도한다
     (`INFO_CONCENTRATION_FALLBACK_ATTEMPT_LIMIT`). 중구 매핑이 생기면 더 가까운
     중구 장소가 상위 3곳을 차지하는데 그 셋이 전부 종로구로 조회돼 0건이 되고,
     답을 낼 수 있는 종로구 장소는 시도 범위 밖으로 밀린다. 활성 장소 좌표를
     전수 대조한 결과 이런 장소가 중구 62곳이다(종로구는 0곳). 예: 중구
     `아시아프 (ASYAAF 100)`는 지금 세종문화회관(종로구, 470m) 기준으로 값을
     받는데, 중구 매핑 적재 후에는 1~10순위가 전부 중구가 되고 세종문화회관은
     11순위로 밀린다.
  3. **`StoredPlaceLocation`은 C가 만들고 C만 쓴다.** `domain/models.py`에 있어
     D 소유로 보이지만, 정의 커밋 `d6ea941`(2026-08-03)이 함께 건드린 파일이
     전부 C이고(`agent_context/`, `providers/factory.py`, `repositories/`,
     `tools/resolve_location.py`), 정의 이후 이 블록을 수정한 커밋도 그 하나뿐이며,
     참조하는 곳도 전부 C다. D 쪽에서 쓰는 곳은 없다.
- 결정:
  1. **조회할 구를 대상 장소에서 가져온다.** `places.district_code`를
     `StoredPlaceLocation.district_code`로 읽어 대체 조회로 넘기고, INFO 직접
     조회 경로에는 `ResolvedLocation.district_code`로 이어 나른다.
     `JONGNO_CONCENTRATION_AREA_CODE`/`JONGNO_CONCENTRATION_DISTRICT_CODE`는
     지운다.
  2. **구를 모르면 조회하지 않는다.** `district_code`가 없으면 호출을 아예
     내보내지 않고 `no_data`로 끝낸다. 대체 조회에서는 그 후보를 건너뛰고 다음
     장소로 간다.
  3. **광역 코드만 고정으로 남긴다.** 지원 구가 전부 서울이라
     `concentration_policy.CONCENTRATION_AREA_CODE = "11"`이다. 25개 구로 넓혀도
     유효하다. 구는 고정하지 않는다.
  4. **코드 자리 변환을 `concentration_policy`에 둔다.** `places.district_code`는
     시군구 3자리(`"140"`)이고 집중률 API `signguCd`는 시도를 붙인 5자리
     (`"11140"`)다. 같은 법정동 코드의 다른 자리라
     `concentration_signgu_code()`가 앞에 광역 코드를 붙인다.
  5. **매핑 데이터 적재보다 이 변경이 먼저다.** 이 카드만 머지된 시점에는 매핑이
     전부 종로구라 나가는 값이 예전과 같아 동작이 바뀌지 않는다. 순서를 뒤집으면
     위 62곳이 깨진다.
- 근거:
  - 종로구로 대신 묻는 폴백을 넣지 않은 이유가 이 결정의 핵심이다. 다른 구 장소를
    종로구로 물으면 응답이 `totalCount=0`인데, 이는 "그 장소의 데이터가 없다"와
    응답이 똑같다. 틀린 조회가 정상적인 "정보 없음"으로 위장돼 아무도 모르게
    섞인다. 실패는 조용히 흡수되지 않고 드러나야 한다는 점에서 D-042(Real 실패
    시 Fake로 자동 전환하지 않는다)와 같은 판단이다.
  - 구를 모르는 경우는 실제로는 거의 없다. 매핑된 장소는 전부 `places`에서 오고
    `district_code`가 채워져 있다. 그래도 폴백 대신 조회 생략을 택한 것은,
    드문 경로일수록 틀린 값이 오래 남기 때문이다.
- 채택하지 않은 것:
  - **`find_concentration_mapped_places`가 `content_id → district_code` 대응을
    따로 돌려주기** — `domain/models.py`를 건드리지 않으려던 방안이다. 위 사실 3
    으로 피할 경계가 없음이 확인돼 채택하지 않았다. 같은 장소의 정보가 두
    자료구조로 갈리면 쓰는 쪽이 둘을 맞춰 들고 다녀야 한다.
  - **`CandidateEnrichmentTarget`에 구를 싣기** — A–C 계약 스키마라 계약 변경이
    된다. 저장소에서 읽는 값으로 충분해 필요가 없다.
- 검증:
  - `pytest` 2,901건 통과, `ruff` 클린.
  - 회귀 방지 테스트를 못 박았다. 매핑이 종로구뿐이면 조회가
    `("11", "11110", "경복궁")`으로 예전과 똑같이 나간다.
  - 중구(`"140"`) 매핑이면 `signguCd=11140`으로 나가는 것,
    `district_code`가 없으면 Provider 호출이 0회인 것을 각각 테스트로 확인했다.
  - 기존 픽스처 14곳에 `district_code`를 채웠다. 비워 두면 조회를 건너뛰어
    테스트는 통과하는데 검증하려던 판정이 한 줄도 실행되지 않는다(CLAUDE.local.md
    가 적어둔 "조용한 fake" 유형). 매핑 캐시 없이 서비스를 만들던 테스트 헬퍼도
    프로덕션 배선(`agent_context/factory.py`)에 맞췄다.
- 남은 것:
  - 확장 구 매핑 CSV 적재는 TP-136에서 한다. 이 변경이 머지된 뒤다.
  - 집중률 API의 구 분류와 TourAPI `district_code`가 어긋나는 사례가 있다.
    `간송미술관(서울 보화각)`은 집중률 API가 중구로 분류하지만 `places`는
    성북구(290), `서울로 7017`은 용산구(170)다. 어느 구 코드로 물을지 정하지
    않았으므로 매핑하지 않은 상태로 둔다.
- 관련 파일: `backend/app/agent_context/enrichment_service.py`,
  `backend/app/agent_context/service.py`,
  `backend/app/concentration_policy.py`,
  `backend/app/domain/models.py`,
  `backend/app/repositories/supabase_places.py`,
  `backend/app/repositories/fake_places.py`,
  `backend/app/tools/resolve_location.py`

### D-096 — 사진 검색을 인텐트·채점 밖의 독립 엔드포인트로 둔다

- 상태: `Accepted` — 구현 완료(D-094 후속, TP-175, 패키지 C).
- 배경: 올린 사진과 분위기가 닮은 장소를 찾는 기능을 붙일 자리를 정해야 했다.
  D-094로 조회 통로(`PlaceMoodProvider`)는 준비돼 있었으나 부르는 쪽이 없었다.
  처음에는 발화에서 분위기 축을 뽑아 추천 점수에 섞는 안(TP-174)을 검토했으나,
  **사진을 입력받았을 때만 순위를 매겨 보여주기로** 방향을 바꾸면서 이 결정이
  대신 들어간다.
- 결정:
  1. **인텐트를 새로 만들지 않는다.** `POST /api/places/similar-by-photo`를
     독립 엔드포인트로 둔다. 인텐트는 "사용자 발화가 무엇을 원하는가"를 분류하는
     장치인데 사진은 발화가 아니라 이미 목적이 확정된 입력이다. 음성 전사
     (`/api/transcribe`)가 같은 이유로 인텐트 밖에 있다.
  2. **추천 채점을 타지 않는다.** 순위는 사진 유사도만으로 정한다.
     `recommend()`가 `prepare()` → `score_prepared()` 순서인데 여기서는
     `prepare()`까지만 부르고 멈춘다. 두 단계가 이미 나뉘어 있어 `domain/scoring.py`
     에 한 줄도 들어가지 않는다.
  3. **하드 필터는 태운다.** 지금 닫힌 가게가 1등으로 나오면 쓸모가 없다.
     `ignore_operating_hours=False`(기본값)로 둔다.
  4. **날씨를 조회하지 않는다.** 날씨는 채점(실내외 선호)에만 쓰이고 하드 필터는
     영업시간·이미 본 곳·거절한 곳만 본다. 후보 매핑도 `context.weather`를 읽지
     않는다. 쓰지도 않을 외부 호출을 요청마다 하나 더 만들 이유가 없다.
  5. **위치는 지역명이 좌표를 이긴다.** `location_query`가 있으면 그것으로 풀고
     없으면 좌표를 그대로 쓴다 — 사용자가 적은 쪽이 의도이고 좌표는 적지 않았을
     때의 기본값이다. 둘 다 없으면 기존 `location_required`로 되묻는다.
  6. **인코더가 없으면 503이다.** 빈 목록이 아니다 — "기능이 꺼졌다"와 "닮은 곳이
     없다"를 같은 응답으로 만들면 왜 안 나오는지 추적할 수 없다(D-042).
- 근거:
  - **이 결정으로 D 패키지에 요청할 것이 없어졌다.** 발화 경로(TP-174)를 갔다면
    선택 Feature 자리(`_MAX_OPTIONAL_FEATURES = 3`이라 `mood`가 네 번째),
    "조용한"이 `concentration_intent`·`taste_query`·`mood` 세 축을 채우는 문제
    (`recommend` 프롬프트 2.2.0/2.3.0에서 하루 만에 뒤집힌 자리의 3라운드),
    축 점수를 0~1로 펴는 방법, 결측 처리, 다축 처리를 전부 D와 합의해야 했다.
    다섯 가지 모두 **기존 스코어링에 섞을 때만** 생기는 문제였다.
  - **장소명을 다시 조회하지 않는 이유.** 사진 검색이 돌려주는 것은 content_id와
    유사도뿐이라 이름을 붙이려면 어디선가 가져와야 하는데, `prepare()`가 돌려준
    후보에 이미 이름·분류·거리가 있다. DB 왕복 한 번을 아낀다.
- 검증: SigLIP을 로컬에 설치해 실제로 돌렸다.
  - **좌표계 일치 확인** — 광장시장 한복매장(1013527)의 사진 5장을 로컬(CPU)에서
    다시 임베딩해 DB에 적재된 장소 벡터(코랩 GPU 산출)와 대조했다. 소수점 넷째
    자리까지 일치한다(0.012066 / 0.017011 / 0.015073 …). 올린 사진으로 검색해도
    뜻이 있다는 근거다.
  - **실제 검색** — 같은 사진으로 종로에서 찾으니 우석공예사 0.8435, 방산
    종합시장 0.8324가 뒤를 이었다. 한복매장 사진에 공예사·종합시장이 올라온다.
  - **응답 시간** — 예열 뒤 1.2초. 첫 요청은 모델 적재 44초를 뒤집어쓰므로
    `PLACE_MOOD_WARMUP_ENABLED`를 켜는 편이 낫다.
  - 테스트 14건 추가(서비스 8·라우트 6), 전체 2,979건 통과, `ruff` 클린.
- 곁가지 발견: `get_image_features()`가 transformers 버전에 따라 텐서가 아니라
  출력 객체(`BaseModelOutputWithPooling`)를 돌려주는 것을 **실제로 돌려보고서야**
  발견했다(D-094에서 들어간 인코더의 결함). 적재에 쓴 코랩 노트북에서 먼저 겪어
  `as_vector()`를 넣었는데 인코더로 옮길 때 빠뜨렸다. 테스트는 인코더를 대역으로
  바꿔 돌아 이 경로를 타지 않는다 — **대역으로 도는 테스트가 실물 경로를 검증하지
  못한다는 사례를 하나 더 남긴다.**
- 남은 것:
  - **후보가 최대 20곳이다.** `MAX_RECOMMENDATION_CANDIDATE_LIMIT`이 20이고
    후보마다 TourAPI 상세 조회가 붙어서다(초당 2건, 동시성 1·간격 0.5초).
    1,015곳을 적재해 두고 20곳 안에서만 고르는 셈이라, 사진 유사도의 값이 후보
    운에 좌우된다. 실측에서 반경 2km에 후보 7곳이었다(20곳 중 영업 중인 곳).
    **해결 방향은 순서를 뒤집는 것이다** — 사진 유사도는 DB 안에서 끝나 공짜이므로
    반경 내 전부를 먼저 줄 세우고 상위 30~40곳만 상세를 조회한다. 상한을 올리는
    안(후보 300곳이면 상세 300건 ≈ 150초)은 속도 때문에 쓸 수 없다.
  - **유사도 컷 실측.** 지금 0.0이고 순위만 쓴다(D-094).
  - **프론트 화면.** 채팅창 좌측 `+` 버튼 → 사진/갤러리 메뉴로 붙이기로 했다.
  - **앞 대화의 위치 재사용.** 지금은 위치를 명시로 받는다. 프론트를 붙일 때
    세션에 있는 검색 중심점을 쓸지 정한다.
  - **SigLIP 배포 여부.** 선택 의존성 `[mood]`이라 안 깔면 사진 경로만 꺼진다.
- 상세: `backend/app/routes/photo_similar.py`,
  `backend/app/services/photo_similar.py`,
  `backend/app/providers/place_mood_encoder.py`, `backend/app/schemas.py`,
  `backend/app/main.py`


### D-097 — 오늘 혼잡 질문에서 저장소 장소가 위치 해석에서 탈락하지 않게 한다

- 상태: `Accepted` — 구현 완료.
- 배경: TP-171. "명동성당 붐벼?", "아시아프 붐벼?"가 집중률 매핑(TP-136)도
  실시간 인구도 못 쓰고 "혼잡도 데이터가 없어요"로 끝났다. 원인은 데이터가
  아니라 위치 해석 단계 — `service.py`가 오늘 날짜 혼잡 질문(`current_population_candidate`)
  을 `LocationPurpose.REALTIME_CITYDATA`로 보내는데, 이 목적은 저장소(DB)
  조회를 아예 건너뛴다. 명동성당·아시아프는 우리 DB(TourAPI 코퍼스)엔 있지만
  Naver 지역 검색엔 상호로 안 잡히고 Naver Geocoding은 상호명 자체를 인식
  못 해(코드 주석 확인) 위치 해석이 완전히 실패했다.
- 카드가 제안한 수정은 "`current_population_candidate`를 REALTIME_CITYDATA
  조건에서 뺀다"(→ `PLACE_IDENTITY`로 통일)였다. **계획 단계에서 이 제안을
  문구 그대로 구현하면 안 되는 이유를 실측으로 발견했다.**
- 실측으로 확정한 사실:
  1. **카드 제안을 그대로 쓰면 심각한 회귀가 생긴다.** `resolve_location.py`의
     `execute()`는 `enforce_service_area = purpose is not REALTIME_CITYDATA`로
     정한다 — REALTIME_CITYDATA만 "지원 16개 구 밖이면 unsupported"를
     건너뛴다. `is_within_service_area()`로 우리 실시간 지역 목록을 전수
     검사한 결과: **인구 121곳 중 49곳, 상권 82곳 중 32곳이 지원 16개 구
     밖이다**(강남역·교대역·여의도·잠실·신도림역 등 대형 허브 다수 포함).
     이런 곳은 지하철역·업무지구라 우리 TourAPI DB에도 없다. 카드 제안대로
     `location_purpose`를 `PLACE_IDENTITY`로 완전히 바꾸면: DB 조회 실패 →
     지역 검색 폴백 → `enforce_service_area=True`라 "지원 지역 밖"으로
     막힌다 — 지금 잘 되는 "지금 강남역 붐벼?"가 깨진다. 카드가 든 두
     예시(광화문·덕수궁, 용리단길)는 우연히 둘 다 지원 구 안(종로·중구,
     용산구)이라 이 회귀를 드러내지 않았다.
  2. 저장소 우선순위와 지역 제한 미적용은 원래 독립적인 두 결정인데 enum
     하나(`LocationPurpose`)에 같이 묶여 있었다. REALTIME_CITYDATA가 DB를
     건너뛴 이유는 "권역명은 코퍼스 밖이라 조회가 헛돈다"는 비용 논리였지
     지역 제한과는 무관했다 — 명동성당처럼 코퍼스 안에 있는데 이 purpose로
     잘못 보내진 이름엔 그 논리가 애초에 안 맞는다.
  3. `_lookup_stored_place`는 DB 매치 시 `district_code`/`concentration_name`
     /`concentration_search_keys`를 목적과 무관하게 항상 채운다(D-095가
     이미 이어 둔 배선). `_fetch_concentration_info`는 이미
     `resolved_location.district_code`를 그대로 읽으므로 이 경로엔 손댈
     필요가 없었다.
  4. `_lookup_local_search`에 두 번째 DB 재조회가 있다(지역 검색이 다른
     이름을 주면 그 이름으로 다시 저장소를 본다 — 집중률 매핑을 붙이기
     위해서). PLACE_IDENTITY로 목적을 바꾸면서 오늘 혼잡 질문에도 새로
     열리는 경로다. 코드 주석대로 "재조회가 실패해도 지역 검색 결과는
     그대로 쓴다" — 재조회가 동명이인으로 실패해도(NO_DATA) 그 결과를
     버리고 지역 검색 성공을 그대로 반환하므로, 새로 사용자에게 되묻기가
     뜨는 회귀는 없다(실측 확인).
  5. 실측(실제 API 8건 호출, `scripts/try_info_context.py`류 임시 스크립트):
     명동성당(구 명동성당, `supabase_places`로 해석)·아시아프(`supabase_places`)
     둘 다 이제 실시간 인구 값을 반환한다. 광화문·덕수궁(`naver_geocoding`)·
     용리단길(`naver_local_search`)은 기존과 동일한 출처로 그대로 성공한다
     (카드의 안전 조건 충족). 새로 찾은 위험 케이스 강남역·교대역·여의도도
     전부 `naver_local_search`/`naver_geocoding`으로 그대로 성공한다(회귀
     없음 확인). 서울숲은 `supabase_places`로 해석되며 최근접 실시간
     지역이 "서울숲공원"(0.37km)으로 나온다 — 요청한 이름과 가장 자연스럽게
     대응하는 결과다.
- 결정:
  1. `resolve_location.py`의 `ResolveLocationQuery`에 `enforce_service_area:
     bool | None = None` 필드를 추가했다. `execute()`는 값이 명시되면
     `purpose` 기반 기본값보다 우선한다 — 값을 안 주면 기존과 완전히 동일.
  2. `service.py`: 오늘 날짜 혼잡 질문의 `location_purpose`는 카드 제안대로
     `PLACE_IDENTITY`로 바꾸되(저장소를 먼저 봄), 이 한 갈래에서만
     `enforce_service_area=False`를 명시했다(지역 제한만 끔). `realtime_commercial`
     /`_REALTIME_CITYDATA_QUESTION_TYPES`(subway/parking/bus/event/traffic)
     경로는 REALTIME_CITYDATA 그대로 손대지 않았다 — 최소 반경의 변경.
  3. 위치 해석 자체가 완전히 실패해도(코퍼스에도 없고 지역 검색·Geocoding도
     못 찾음) `concentration` 문항이면 `_info_no_data_response`로 바로
     끝내지 않는다. 좌표가 없어 기존 D-036 인근 대체(반경 기반)는 못 쓰므로,
     이름이 집중률 매핑 캐시(`ConcentrationMappingCache`)와 **정확히 하나만**
     일치할 때만 그 장소로 바로 조회한다(`_fetch_concentration_by_name_only`).
     일치가 없거나 둘 이상이면 억지로 고르지 않고 그대로 `no_data`. 사용자
     결정("실시간 혼잡도가 없으면 집중률이라도 보여준다")의 구현이며, 카드가
     열어 둔 완료 조건("위치 해석 실패 시 집중률로 낮출지")에 대한 답이다.
     이 폴백은 `concentration` 문항에만 적용한다.
- 검증: 백엔드 `pytest` 3,005 passed, `ruff` 클린. 신규 테스트 12건(
  `enforce_service_area` 오버라이드 단위 테스트 4건·동명이인 재조회 무해성
  1건은 `test_resolve_location_tool.py`, DB-우선 성공·지원 구 밖 성공·이름-
  일치 폴백 성공/미일치/동명이인·event 유형 미적용 6건은
  `test_service.py`). 실제 API로 8개 장소(명동성당·아시아프·광화문·덕수궁·
  용리단길·강남역·교대역·여의도·서울숲)를 오늘 혼잡 질문으로 조회해 위
  "실측으로 확정한 사실 5"의 결과를 확인했다.
- 채택하지 않은 것: 카드 문구 그대로("`current_population_candidate`를
  REALTIME_CITYDATA 조건에서만 뺀다") — 지원 구 밖 실시간 허브 다수를
  깨뜨리는 회귀가 있어, 지역 제한을 저장소 우선순위와 분리하는 `enforce_service_area`
  오버라이드를 추가로 도입했다.
- 상세: `backend/app/agent_context/service.py`, `backend/app/tools/resolve_location.py`,
  `backend/tests/test_resolve_location_tool.py`, `backend/tests/agent_context/test_service.py`

### D-098 — 사진 검색을 채팅창에 붙이고 대화가 잡은 위치를 이어받는다

- 상태: `Accepted` — 구현 완료(D-096 후속, TP-175, 패키지 C·공용 프론트).
- 배경: D-096으로 엔드포인트는 만들었으나 부르는 화면이 없었다. 붙여 보니
  위치를 못 찾아 매번 되묻기로 끝났다.
- 결정:
  1. **입력창 왼쪽 "+" 버튼**에서 사진/갤러리를 고른다. 사용자 화면과 개발자
     화면 둘 다에 붙이고, 핸들러는 `usePhotoSimilarSearch` 훅으로 뺀다 —
     복사해 두면 한쪽만 고쳤을 때 개발자 화면에서 재현한 것이 사용자 화면과
     달라진다.
  2. **대화가 잡은 위치를 이어받는다.** `session_id`를 받아 B의 누적 조건에서
     `search_center` → `current_location` 순으로 찾고, 없으면 기기 GPS로
     떨어진다. 기존 추천과 같은 순서다(agent_context/service.py).
  3. **올린 사진은 긴 변 320px로 줄여 data URL로 담는다.**
  4. **사진은 사용자 쪽(`ml-auto`), 결과는 응답 쪽(`mr-auto`)**에 둔다. 결과는
     가로로 늘어놓는다.
  5. **유사도를 숫자로 보여주지 않는다.** 순위로만 보여주고, 정말 닮았는지는
     카드를 눌러 상세를 열어 확인하게 한다.
  6. **결과 카드의 사진은 `place_image_embeddings`의 첫 장**이다.
- 근거:
  - **위치가 안 풀린 원인이 둘이었다.** 세션을 안 본 것이 하나이고, 다른 하나는
    `ResolveLocationTool`에 지오코딩만 넘긴 것이다 — 지오코딩은 주소 전용이라
    "안국역"이 `no_data/location_not_found`로 끝난다. 저장소와 지역 검색을 함께
    넘겨야 "안국역 3호선"으로 풀린다. `build_recommendations`가 지오코딩만
    넘기길래 따라 했는데, 그쪽은 옛 경로라 장소명을 다루지 않는다.
  - **`URL.createObjectURL`을 쓰지 않는 이유.** 그 주소는 탭 수명에 묶여 있어
    대화가 sessionStorage에서 복원될 때 이미 무효다. data URL은 문자열이라
    그대로 저장되고, 320px/품질 0.7이면 15~25KB라 한도에 부담이 없다.
  - **결과를 가로로 두는 이유.** 세로 목록이면 카드 하나가 한 줄을 통째로 쓰면서
    오른쪽이 비고 화면이 길어진다. 가로면 사진이 나란히 놓여 분위기를 한눈에
    견줄 수 있는데, 이 화면의 목적이 그 비교다.
  - **`places.first_image_url`을 쓰지 않는 이유.** 2,008곳 중 1,163곳이 서로 다른
    주소다(2026-08-27 실측). 사용자가 분위기가 맞는지 확인하려는 화면에서
    비교하지 않은 사진을 보여주면 틀린 근거가 된다.
- 검증: 프론트 199건·백엔드 2,995건 통과. 안국역 세션으로 실제 호출해
  강서 좌표를 함께 보내도 `안국역 3호선`으로 잡히는 것을 확인했다.
- 곁가지 발견: `python-multipart`가 의존성에 없었다. `Form`·`File`을 쓰려면
  필요한데 `fastapi`의 선택 의존성이라 따로 적어야 한다. **없으면 앱 전체가 못
  뜬다** — CI에서 `app`을 import하는 테스트 14개가 전부 수집 단계에서 터졌다
  (`test_health.py`처럼 사진과 무관한 것 포함). 로컬에는 우연히 깔려 있어
  통과했고 CI가 잡았다. 인코더 반환형 결함(D-096)과 함께, **로컬 환경이 CI보다
  넉넉하면 못 잡는다**는 사례를 하나 더 남긴다.
- 남은 것:
  - **후보가 여전히 좁다.** `RECOMMENDATION_CANDIDATE_LIMIT`이 10이고 절대 상한이
    20이라, 안국역 반경 2km에서 후보 10곳 → 하드 필터 7곳 → 사진 벡터가 있는
    5곳이 나온다. **어떤 사진을 올려도 같은 5곳이 순서만 바뀐다.** 순서를 뒤집는
    작업(TP-176)이 붙어야 사진마다 다른 결과가 나온다.
  - **깨진 사진이 500을 낸다.** 확장자만 `.jpg`인 파일이나 잘린 사진에서
    `PIL.UnidentifiedImageError`가 그대로 올라간다.
  - 앞 대화 위치가 없을 때의 되묻기 버튼 흐름. 지금은 오류 배너로만 알린다.
- 상세: `frontend/src/components/chat/PhotoInputButton.tsx`,
  `frontend/src/components/chat/PhotoSimilarResultMessage.tsx`,
  `frontend/src/hooks/usePhotoSimilarSearch.ts`,
  `frontend/src/utils/imageThumbnail.ts`,
  `backend/app/services/photo_similar.py`, `backend/app/routes/photo_similar.py`


### D-099 — 사진 검색은 순위를 먼저 매기고 상위 N곳만 상세를 확인한다

- 상태: `Accepted` — 구현 완료(D-098 후속, TP-176, 패키지 C).
- 배경: D-098로 화면까지 붙였는데 **어떤 사진을 올려도 같은 대여섯 곳이 순서만
  바뀌었다.** 안국역 반경 2km에서 후보 10곳(`RECOMMENDATION_CANDIDATE_LIMIT`) →
  하드 필터 7곳 → 사진 벡터가 있는 5곳이 나왔다. 2,009곳을 적재해 두고 5곳
  안에서만 고르는 셈이라 기능이 반쯤만 도는 상태였다.
- 결정:
  1. **순서를 뒤집는다.**

     ```
     전:  위치 → 장소 조회(상세 포함, 최대 20곳) → prepare() → 사진 순위
     후:  위치 → 사진 순위(DB, 반경 안 전부) → 상위 N곳 상세 → prepare()
     ```

  2. `search_place_mood` RPC에 `p_latitude`·`p_longitude`·`p_radius_km`를 더한다.
     후보 목록 인자도 그대로 둔다 — 둘 다 주면 교집합, 좌표만 주면 반경, 후보만
     주면 그 목록이다(D-096 호출이 계속 동작해야 한다).
  3. **거리는 하버사인을 직접 계산한다.** PostGIS가 설치돼 있지 않고
     `places.latitude/longitude`가 `double precision`이라 이 정도면 충분하다.
     1차로 위경도 사각형으로 걷어낸 뒤 하버사인으로 최종 판정한다.
  4. **상세는 DB에서 읽는다**(`get_active_place_details`). `NearbyPlaceDetailsTool`은
     TourAPI를 타고 `limit`이 20으로 막혀 있어 이 경로에 맞지 않는다.
  5. **하드 필터 판정을 직접 만들지 않는다.**
     `prepare_recommendation_from_context()`를 그대로 태운다.
  6. 보여줄 수의 **4배**를 먼저 받는다(`_OVERFETCH_FACTOR`).
- 근거:
  - **왜 뒤집는 것이 맞나.** 사진 유사도는 DB 안에서 끝나 사실상 공짜이고,
    비싼 것은 상세 조회다. 뒤집기 전에는 후보를 만들려고 상세를 먼저 다 조회해
    **결과에 안 나갈 곳까지 값을 치렀다.** 뒤집으면 "어차피 보여줄 곳"에만 쓴다.
  - **상한을 올리는 안은 쓸 수 없었다.** 후보 300곳이면 TourAPI 상세 300건이고,
    초당 2건이라 약 150초다(중구 892건에 6분 16초가 걸린 실측이 config.py 주석에
    있다). `MAX_RECOMMENDATION_CANDIDATE_LIMIT`이 20인 이유가 그 제약이다.
  - **하드 필터를 재사용하는 이유.** 영업시간 해석을 여기서 새로 만들면 같은
    장소가 추천에서는 열렸는데 사진 검색에서는 닫힌 것으로 갈릴 수 있다.
  - **4배의 근거.** 실측에서 영업 중인 비율이 20곳 중 7곳(35%)이었다. 10곳을
    채우려면 30~40곳을 훑어야 하고, 상세가 DB라 값이 싸다.
  - **좌표와 반경 중 하나만 오면 예외로 끝낸다.** 조용히 전체를 훑게 되는데
    그건 호출부가 의도한 적 없는 동작이다.
  - **상세가 없는 장소는 건너뛴다.** 영업 여부를 판정할 수 없는데 열려 있다고
    단정하면 닫힌 곳을 추천하게 된다.
- 검증: 안국역 반경 2km 실측.

  | | 전 | 후 |
  | --- | --- | --- |
  | 후보 | 7곳 | **40곳** |
  | 응답 | 1.2초 | **0.65초** |
  | TourAPI 호출 | 10회 | **0회** |

  **후보가 6배 늘었는데 응답이 절반이다.** 상세를 DB에서 읽어 TourAPI 초당 2건
  제한을 타지 않는다.

  **사진마다 다른 결과가 나온다.** 같은 위치·반경에서 한복매장 사진은
  삼익패션타운·대학천 책방거리·우석공예사를, 창덕궁 사진은 기억의 터·무궁화동산·
  삼청공원을, 민속박물관 사진은 통의동 국빈관·설가온·한국의집을 준다. **겹치는
  곳이 하나도 없다.** 상가·공원·한옥건물로 갈렸다.

  백엔드 2,997건 통과, `ruff` 클린. 서비스 테스트는 새 흐름으로 다시 썼다(12건).
- 채택하지 않은 것:
  - **`MAX_RECOMMENDATION_CANDIDATE_LIMIT`을 올리기** — A/D가 공유하는 정책이고,
    올려도 TourAPI 속도 때문에 실용적이지 않다.
  - **`PLACE_DETAILS_SOURCE=supabase`로 전역 전환** — 추천 경로 전체에 영향을
    주는 설정이라 사진 경로만 DB를 읽게 했다. DB 값은 place-sync 시점에 멈춰
    있어 신선도가 떨어지는데, 그 절충은 추천 쪽이 따로 판단할 일이다.
- 남은 것:
  - **깨진 사진이 500을 낸다.** `PIL.UnidentifiedImageError`가 그대로 올라간다.
  - **유사도 컷 실측.** 지금 0.0이고 순위만 쓴다(D-094). 후보가 넓어져 "안 닮았는데
    억지로 채운" 경우가 보일 수 있다.
  - **적재 범위.** 2,009곳(종로·중구·마포·용산·노원)뿐이라 나머지 11개 구에서는
    후보가 잡혀도 비교할 벡터가 없다.
- 상세: `supabase/migrations/202608270001_add_radius_to_search_place_mood.sql`,
  `backend/app/services/photo_similar.py`,
  `backend/app/repositories/supabase_places.py`,
  `backend/app/providers/place_mood.py`, `backend/app/providers/factory.py`

### D-100 — INFO 장소 되묻기: 편집거리 매칭 + 후보 버튼 노출 + 상태 저장 이어받기

- 상태: `Accepted` — 구현 완료.
- 배경: "성수 카페거리 주차장 정보"가 INFO에서 "여러 장소 중 어느 곳을 말씀하시는
  건가요?"라고 버튼 하나 없이 되묻는 버그 제보. 조사해보니 두 가지가 겹쳐 있었다.
  1) 지역 검색이 실제로 "성수동카페거리"(여행,명소>거리,골목 — 명소로 인식)를
     찾아주는데, 사용자 발화 "성수 카페거리"와 "동" 한 글자 차이로 정확/첫토큰
     일치 어느 쪽에도 안 걸려 후보를 하나로 못 좁혔다.
  2) `agent_context/service.py`의 INFO 처리 코드가 `resolve_location.py`가 실제로
     찾아낸 후보 이름을 받아서 쓰지 않고 `candidates=[]`를 하드코딩해 버렸다 —
     RECOMMEND/SCHEDULE는 같은 상황에서 후보를 버튼으로 보여주는데 INFO만
     안 그랬다. 게다가 `llm_output.status`를 `NEEDS_CLARIFICATION`으로 바꾸지도
     않아 애초에 버튼 UI 자체가 뜰 수 없는 구조였다.
- 결정:
  1. **편집거리 매칭(Part A)**: `resolve_location.py`의 `_select_local_search_candidate()`에
     4번째 단계 추가 — 정확/첫토큰/노선별 역 묶기가 다 실패했을 때만, 편집거리
     (Levenshtein) ≤ 1이고 역/명소 카테고리인 후보가 정확히 하나면 채택한다.
     임계값 2는 안 됨("경복궁"↔"덕수궁" 둘 다 3글자 편집거리 2인 서로 다른
     실존 랜드마크). 질의 길이 3자 미만은 이 단계를 안 탄다("신촌"↔"신천"
     처럼 편집거리 1이 완전히 다른 동네일 수 있음). 역/명소 카테고리로만
     제한해 이 파일 전체의 "부분 일치로 넓히지 않는다" 원칙과 충돌하지
     않게 했다(식당·상점까지 넓히면 "안국역"≠"안국역사거리" 같은 기존
     회귀 테스트가 지키는 경계가 무너진다).
  2. **INFO도 실제 후보를 candidates에 담는다(Part B)**: `agent_context/service.py`의
     `ambiguous_location` 분기에서 `location_result.error.details["candidate_names"]`를
     실제로 풀어 쓴다. `"|"` 구분 파싱을 `agent_context/schemas.py`의
     `parse_candidate_names()`로 공용화해 `assembler.py`(RECOMMEND 경로)와
     함께 쓴다.
  3. **question_type별 가용성 필터(Part C)**: 후보 이름마다
     `self._tools.location.execute()`로 다시 개별 해석해, `concentration`은
     `concentration_name` 있는 것만, 실시간 유형(주차·지하철·버스·행사·교통,
     현재형 혼잡 질문)은 `select_nearest_population_area`가 잡히는 것만,
     `realtime_commercial`은 `select_nearest_commercial_area`가 잡히는 것만,
     그 외 시설 상세는 `place_id`(저장소 존재) 있는 것만 남긴다. 재해석
     자체가 또 애매하면(DB 동명 타이틀 2건 등) "조회 불가"로 버리지 않고
     후보를 유지한다. 필터링으로 0건이 되면 원본 후보 목록으로 되돌린다
     (RECOMMEND의 location_ambiguous가 후보 없으면 quick-pick으로 대체하지
     버튼 없는 메시지로 안 가는 것과 같은 원칙).
  4. **상태 저장으로 정확히 이어받기(Part D)**: INFO는 RECOMMEND와 달리
     `question_type`/`specific_question`/`place_context`/`visit_time`을 세션에
     저장해두지 않아, 버튼 클릭 시 장소명만으로 처음부터 재분류되면 "주차장
     질문이었다"는 사실이 사라진다(사용자 결정: 상태 저장해 정확히 이어받는
     쪽 채택, 재분류 허용 대신). `AgentState`에 `pending_info_context` 필드
     신설, `set_pending_info_context()`를 `set_pending_clarification()`과
     같은 모양으로 추가했다. `pending_info_context`는 `pending_clarification
     == "place_ambiguous"`일 때만 의미가 있어, `set_pending_clarification()`
     내부에서 code가 `"place_ambiguous"`가 아니면 같이 지우게 했다 — 새
     호출부 하나만 추가하면 기존 9개 호출부가 자동으로 안전하게 정리된다.
     `agent_runtime.py`의 INFO 블록이 RECOMMEND의 `location_ambiguous`와
     같은 패턴으로 `NEEDS_CLARIFICATION` + `ClarificationPayload`를 만들고,
     `_resolve_clarification_choice()`에 `place_ambiguous` 분기를 추가해
     저장해둔 값으로 `InfoPayload`를 결정적으로 재구성한다. INFO가 되묻지
     않고 끝나는 지점에서 `pending_clarification == "place_ambiguous"`였다면
     정리한다 — "INFO/GENERAL 곁가지 대화는 RECOMMEND의 되묻기를 안 건드린다"는
     기존 원칙과 결이 같되, place_ambiguous는 INFO 자신의 되묻기라 INFO가
     정리할 책임이 있다고 봤다.
  5. Supabase `agent_states` 테이블에 `pending_info_context jsonb` 컬럼 추가
     마이그레이션(`202608270001`) — `pending_clarification` 컬럼 때(202608030001)와
     같은 이유로, `save_state()`가 `model_dump()`를 통째로 보내 컬럼이 없으면
     저장이 실패한다.
- 검증: 백엔드 `pytest` 3,022 passed, `ruff check` 클린. 실제 Naver 지역검색
  API로 "성수 카페거리"→"성수동카페거리"(여행,명소>거리,골목) 편집거리 1
  매칭을 실측 확인.
- 채택하지 않은 것:
  - 좌표 기준 가까운 지하철역 버튼: 우리 DB엔 TourAPI 관광지만 있고 역
    데이터가 없다. Naver 지역검색도 좌표 기반 카테고리 검색을 지원하지
    않아 별도 데이터셋이 필요해 기각.
  - 되묻기 버튼 클릭 시 장소명만으로 재분류(상태 저장 없이): 원래 질문
    (주차 등) 신호가 사라져 다른 question_type으로 잘못 재분류될 위험이
    있어, 상태 저장 쪽을 채택(위 결정 4).
- 상세: `backend/app/tools/resolve_location.py`, `backend/app/agent_context/service.py`,
  `backend/app/agent_context/assembler.py`, `backend/app/agent_context/schemas.py`,
  `backend/app/services/runtime/agent_runtime.py`, `backend/app/state/schema.py`,
  `backend/app/state/service.py`,
  `supabase/migrations/202608270001_add_pending_info_context_column.sql`,
  관련 테스트 파일 다수

### D-101 — 공영주차장 실시간 현황을 일반 근처 주차장과 분리한다

- 상태: `Accepted` — 구현 완료, 좌표 카탈로그 적재는 마이그레이션 적용 후 별도 실행.
- 배경: 서울시 실시간 도시데이터 `PRK_STTS`는 특정 핫스팟 주변의 공영·민영 주차장
  목록을 함께 내려주지만, 실시간 대수가 있는 항목이 일부이고 모든 공영주차장을
  포괄한다는 보장이 없다. 반면 서울시 `GetParkingInfo`는 구 단위 시영·공영주차장에
  `PKLT_CD`, 총면수, 현재 주차 대수, 갱신 시각을 제공한다. 2026-08-27 실측에서
  종로·중·용산·영등포구의 `PRK_STTS_YN=1` 행에 20분 이내 갱신 수치가 있음을 확인했다.
- 결정:
  1. "공영/시영주차장"을 명시한 자리·잔여 질문은 `realtime_public_parking`으로
     분류해 GetParkingInfo를 우선 사용한다. `PRK_STTS_YN=1`이고 수치가 있는 행만
     실시간 현황으로 표시하며, 잔여 면수는 `총면수 - 현재 주차 대수`로 결정적으로
     계산한다.
  2. 공영/시영을 명시하지 않은 "근처 주차장"은 기존 `realtime_parking`을 유지해
     도시데이터의 공영·민영 목록을 함께 보여준다. 같은 분류 안에서는 실시간 수치가
     있는 항목을 거리보다 먼저 노출해, 값 있는 주차장이 카드 밖으로 밀리는 문제를 막는다.
  3. GetParkingInfo에 좌표가 없으므로 `municipal_parking_lots`에는 코드·주소·한 번
     지오코딩한 좌표·기본 속성만 저장한다. 실시간 주차 대수는 DB에 적재하지 않고
     요청마다 API에서 가져온다. 카탈로그가 비어도 해당 구 최신 공영주차장 목록은
     답하되, 거리 정렬만 생략한다.
  4. 일반 `parking`이 관광지 상세정보에서 주차 필드를 찾지 못하면(예: 관광 DB에
     없는 `종각역`) 확정한 장소 좌표를 기준으로 같은 GetParkingInfo 경로를 대체
     사용한다. 관광지 자체의 주차 가능 여부·요금이 있으면 기존 상세 경로를
     유지한다. 좌표가 불명확하거나 해당 구에 실시간 공영주차장 값이 없을 때만
     `확인할 수 없음`으로 응답한다.
  5. 지원 구 이름만 말한 "종로 주차장 정보"는 장소 후보를 되묻지 않는다. `종로`와
     `종로구`를 행정구역 좌표로 직접 해석해 해당 구의 공영주차장을 조회한다.
     `종각`처럼 역·명소로도 읽히는 이름은 이 예외에 넣지 않아 기존 후보 선택을
     유지한다.
- 채택하지 않은 것: GetParkingInfo만으로 근처 민영주차장까지 포괄하는 방식. 이 API는
  공영 중심이고 좌표도 없어 민영 포함 근처 탐색에는 기존 도시데이터 경로가 더 맞다.
- 상세: `backend/app/providers/municipal_parking.py`,
  `backend/app/agent_context/service.py`,
  `supabase/migrations/202608270002_create_municipal_parking_lots.sql`,
  `docs/design/int-02-info.md`.

### D-104 — 숫자 없는 시간 표현을 `time_available` 분 단위로 고정 환산한다

- 상태: `Accepted` — 구현 완료(TP-177). 골드셋 재확인 1회가 남아 있다(아래 "남은 것").
- 배경: 골드셋(`test_results/agent_quality/evaluation_dev.csv`)의 SCHEDULE 실패가
  2026-08-20부터 같은 자리에서 반복됐다 — DEV-008/023의 `time_available`,
  DEV-008/033의 `search_center`. `prompts/recommend/HISTORY.md`는 이를 "기존
  비결정성 케이스"로 기록하고 "1회 실행으로 회귀를 판정하지 않아야 하는 사례로
  남긴다"고 적어뒀지만, 흔들림의 크기를 재는 수단이 없어 원인을 특정하지 못한 채
  네 번의 실행에 걸쳐 같은 문장이 반복됐다.
  기준선이 흔들리는 상태에서 프롬프트를 고치면 개선인지 실행 간 분산인지 구분할 수
  없으므로, 측정 도구를 먼저 만들고 가설을 하나씩 기각하는 순서로 접근했다.
- 결정:
  1. `verify_schedule_condition_extraction.py`를 신설해 **흔들림(같은 발화·같은
     설정에서 실행마다 값이 바뀌는가)** 과 **기대 일치(골드셋 라벨과 맞는가)** 를
     따로 측정한다. `record_llm_call()`의 `served_model`을 함께 읽어 폴백 발생도
     같은 표에서 본다. 기존 `verify_taste_query_extraction.py`·
     `verify_travel_origin_extraction.py`와 같은 패턴이다.
  2. `recommend.extract`(2.4.0 → 2.5.0)에 숫자 없는 시간 표현의 고정 환산 규칙을
     넣는다 — 반나절/한나절/오전·오후 내내 240, 하루 종일/온종일/아침부터 저녁까지
     480, 잠깐·짧게 120, 범위로 말했으면 하한("두세 시간" → 120), 목록에 없는
     표현은 억지로 숫자를 만들지 않고 null. 기존 시간 규칙은 숫자 환산("5시간"
     → 300)만 있었다.
  3. **SCHEDULE 전용 추출 슬롯을 신설하지 않는다.** 남은 결함이 시간 표현 하나로
     좁혀졌고, "반나절 = 4시간"은 RECOMMEND 발화에도 맞는 해석이라 슬롯을 복제할
     이유가 없다. `prompts/schedule/`가 B 소유라 전용 슬롯을 만들면 협의 없이
     끝낼 수 있었지만, 규칙 몇 줄을 위해 슬롯을 둘로 늘리면 두 슬롯이 같이 낡는다.
  4. 값은 코드가 이미 쓰는 폴백과 일관되게 맞춘다 —
     `build_schedule_planning_instruction()`이 `time_available` 미지정 시
     "3~4시간 내외로 구성"을 지시하므로 반나절을 그 상한인 240으로 둔다.
     골드셋 라벨(240)도 같은 값이다.
- 근거: "반나절"은 3회 반복에서 발화에 지명이 있으면 240, 없으면 360으로 갈렸다 —
  각각은 3회 모두 고정이었으므로 흔들림이 아니라 **규칙이 없어서 모델이 문맥으로
  해석한 결과**다. 사용자가 "반나절"이라 말하고 6시간 일정을 받는 것은 틀린 결과이고,
  같은 단어가 지명 유무로 갈리는 것 자체가 일관성 결함이다. "하루 종일"은 3회 모두
  null로 떨어져 시간 조건 없이 편성되고 있었다. 범위 표현("두세 시간")은 150/150/180
  으로 유일하게 실제로 흔들렸고, 하한을 쓰기로 정한 근거는 일정이 넘치는 쪽보다
  여유가 남는 쪽이 사용자 피해가 작다는 것이다.
- 기각한 가설(측정으로 확인): 네 개를 세워 네 개를 모두 기각했다. 기록해두는 이유는
  다음에 같은 증상을 보는 사람이 같은 길을 다시 걷지 않게 하기 위함이다.
  1. **구세대 모델 폴백이 원인** — 한가한 상태 42호출(14케이스 × 3회) 전부
     `gemini-3.5-flash`가 응답했다(`served_model`). 단, 골드셋을 돌려 호출이 몰리자
     폴백이 실제로 발동했다(아래 "곁가지로 드러난 문제").
  2. **모델 티어가 낮아서** — `config.py`의 기본값은 `gemini-3.5-flash-lite`지만
     `.env`가 `LLM_FAST_MODEL_NAME=gemini-3.5-flash`로 덮어쓰고 있었다. 기본값을
     실제 설정으로 착각한 오독이었다.
  3. **`location_rules.md`가 일정 발화를 커버하지 못해 `search_center`가 빠진다** —
     "경복궁 코스/일정/근처 일정", "북촌 반나절 코스", "광화문 반나절 일정" 5종
     전부 3회 고정으로 정확히 잡았다. 규칙 문면이 "근처/주변/지명 단독"만 열거해
     애매하지만 모델은 문제없이 처리한다.
  4. **조건 병합(`state_transform`)에서 값이 사라진다** — 골드셋이 채점하는 값은
     추출 직후가 아니라 병합된 `state.user_conditions`라서 이 가설을 세웠다.
     `POST /api/interpret`로 DEV-008과 같은 입력을 넣어 `search_center=광화문`,
     `time_available=240`이 그대로 통과하는 것을 확인해 기각했다.
- 검증: `verify_schedule_condition_extraction.py --repeat 3`을 변경 전후로 실행 —
  변경 전 흔들림 1건·기대 불일치 1건에서 **변경 후 14/14 전부 고정·기대 일치**로
  바뀌었다("반나절 일정" 360 → 240, "하루 종일" null → 480, "두세 시간"
  150/150/180 → 120 고정). RECOMMEND 대조군 2건은 변화 없음. 전체 테스트
  3,064건 통과(develop 머지 후), ruff 통과, 프롬프트 스냅샷 갱신.
  골드셋(dev 35건)은 관광 API 일일 한도가 소진돼 `PLACE_PROVIDER=fake`로 대체
  실행했고, 지표 하락(-18%p 등)은 전부 (a) Gemini 404 오류 4건 (b) fake 장소로
  1턴째 추천이 기록되지 않아 2턴째가 MODIFY 대신 RECOMMEND로 분류된 2건으로
  설명된다 — **정상 응답한 31건은 조건 불일치가 0건**이었고, 이번 변경이 건드린
  필드에서 실제 불일치는 없었다.
- 곁가지로 드러난 문제(이 결정 범위 밖): 골드셋 실행 중 `classify_intent`에서
  Gemini가 **간헐적으로 404 NOT_FOUND**를 냈다(35건 중 4건, `attempted_models`에
  주 모델과 폴백이 모두 있고 `served_model=None` — 두 모델 다 실패). 404는
  결정적인 오류인데 간헐적이라는 점이 설명되지 않는다. 같은 실행에서
  `gemini-3.5-flash` 재시도 소진 후 `gemini-2.5-flash-lite`로 폴백되는 것도
  관측됐다 — 위 기각 가설 1이 한가할 때는 발동하지 않지만 호출이 몰리면 발동한다는
  뜻이고, 그 경우 같은 발화가 실행마다 다른 모델로 처리된다. 별도 카드로 다룬다.
- 남은 것:
  - 관광 API 일일 한도가 풀린 뒤 골드셋(dev 35건)을 `--base-url` 없이 1회 재실행해
    다중 턴까지 확인한다. 이번 대체 실행은 다중 턴 통과율을 신뢰할 수 없다.
  - `evaluation_dev.csv`의 "반나절 = 240" 라벨은 이번에 프롬프트와 일치시켰지만,
    팀 합의로 정한 값이 아니라 골드셋에만 있던 값이다(README: "라벨은 팀 합의로
    검토해야 하는 기대 동작"). 다른 값이 맞다고 판단되면 프롬프트와 라벨을 함께 바꾼다.
  - MODIFY 슬롯의 `exclude_tags` 추출(DEV-029)은 이번 범위 밖이다.
- 상세: `backend/app/prompts/recommend/extract.md`,
  `backend/app/prompts/recommend/meta.yaml`,
  `backend/app/prompts/recommend/HISTORY.md`(2.5.0),
  `backend/scripts/verify_schedule_condition_extraction.py`,
  `backend/tests/prompts/snapshots/recommend_extract.txt`

### D-106 — 인텐트 분류·조건 추출 구간에 SSE 하트비트를 붙이고, 나머지 추출 4곳에 thinking_budget=0을 맞춘다

- 상태: `Accepted` — 구현 완료(TP-179).
- 배경: "가끔 답변이 매우 느릴 때가 있다"는 체감 보고를 실측으로 조사했다.
- 실측으로 확정한 사실:
  1. D-066(2026-08-20)이 답변·요약 5곳과 `classify_intent`/`extract_recommend_conditions`
     에 `thinking_budget=0`을 적용하면서 `extract_modify_conditions`/
     `extract_info_query`/`extract_compare_request`/`extract_general_request` 4곳은
     "범위 밖"으로 명시적으로 남겨뒀다.
  2. 위 4곳에 실제로 `thinking_budget=0`을 걸어 실측(2026-08-27)했더니 **지연
     차이가 거의 없었다**(1.3~1.6초 vs 1.3~1.6초). fast 모델(`gemini-3.5-flash-lite`)
     은 설정 없이도 이미 thinking이 가벼워서다 — D-076의 `classify_intent` 실측과
     같은 패턴. (측정 스크립트 첫 실행에서 12~40초가 나왔던 건 스크립트가
     `model_names`를 안 넘겨 무거운 생성 모델로 잘못 잰 스크립트 버그였다 — 프로덕션
     코드는 처음부터 정상이었다. 수정 후 재측정으로 확인.)
  3. **진짜 원인은 다른 곳이었다.** `classify_intent()` + intent별 `extract_*()`
     (최대 2번의 순차 LLM 호출, `build_interpretation()`)는 SCHEDULE 편성
     (`generate_schedule_plan/fill`)과 달리 SSE 하트비트(`await_with_heartbeat`)로
     감싸지 않았다. 평소엔 1~2초 안에 끝나 문제없지만, 외부 API 꼬리 지연(P95/P99)
     이 걸리면 "요청 의도와 조건을 파악하고 있어요." 문구 하나로 그 구간 전체가
     무응답으로 멈춘 것처럼 보인다. D-066 changelog에도 "분류·추출 단계 하트비트
     부재는 범위 밖"이라고 이미 남아 있던, 그동안 아무도 안 고친 문제였다.
- 결정:
  1. `agent_runtime.py`의 `build_interpretation(interpret_request, llm)` 호출을
     `_await_with_heartbeat()`로 감쌌다 — SCHEDULE과 같은 패턴. `stream_events.py`
     에 `INTERPRET_HEARTBEAT_MESSAGES`(4초 간격, SCHEDULE의 6초보다 짧게 — 평소
     지연이 더 짧아 꼬리 지연을 더 빨리 감지해야 함)를 신설했다. RECOMMEND/MODIFY/
     INFO/COMPARE/GENERAL 전 인텐트가 이 한 지점 수정으로 커버된다.
  2. `extract_modify_conditions`/`extract_info_query`/`extract_compare_request`/
     `extract_general_request` 4곳에 `thinking_budget=0`을 추가해 나머지 6곳과
     통일했다. 지연 개선 목적이 아니라(실측상 효과 없음), fast 모델이 다시 무거운
     모델로 바뀌는 순간 이 4곳만 조용히 최적화가 빠지는 D-076류 사고를 예방하는
     정리다.
- 검증: 백엔드 `pytest` 3,097 passed, `ruff` 클린. 프론트 `vitest` 27개 파일 208건
  통과. 신규 테스트 17건(interpret 단계 하트비트 1건, 4곳 `thinking_budget=0` 배선
  4건 + 관련 회귀).
- 채택하지 않은 것: 호출부가 `thinking_level=MINIMAL`을 직접 쓰는 안 — 검토 중
  제안됐으나 기각했다. `thinking_budget`(0/None)은 "꺼줘"라는 의도만 표현하는
  모델-세대 무관 추상이고, 실제로 API에 뭘 실을지는 `_thinking_config_for()` 한
  곳이 모델명을 보고 판단한다(gemini-3.x는 `thinking_level`만 받고 gemini-2.5는
  반대로 그걸 거부한다 — D-076 "남은 것" 참고). 호출부가 직접 `thinking_level`을
  쓰면 폴백 목록에 2.5 세대가 다시 들어오는 순간(장애 대응 등) 그 호출이 400으로
  즉시 죽는다 — 로직이 흩어지면 흩어진 곳마다 따로 깨진다는, 이번 조사의 출발점이
  된 D-076의 교훈 그대로다.
- 상세: `backend/app/providers/gemini.py`, `backend/app/services/runtime/agent_runtime.py`,
  `backend/app/services/runtime/stream_events.py`,
  `backend/tests/test_agent_runtime.py`, `backend/tests/test_gemini_provider.py`,
  `backend/scripts/measure_unoptimized_extraction_thinking.py`

### D-107 — 새 SCHEDULE 턴에서는 직전에 보여준 장소를 후보로 되살린다

- 상태: `Accepted` — 구현 완료(TP-180). 사용자 문의로 발견해 실사용 재현까지 확인했다.
- 배경: RECOMMEND로 장소를 추천받은 뒤 "이 장소들로 일정 짜줘"라고 하면 그 장소가
  일정에 한 곳도 들어가지 않고 전부 새 장소로 채워졌다. 문의로 받은 실제 세션
  (용산역 기준)에서 추천 5곳과 일정 4곳이 한 곳도 겹치지 않았다.
  원인은 각각은 옳은 두 설계가 이 흐름에서 만나 생긴 충돌이다.
  1. B의 제외 목록은 `recommended ∪ rejected ∪ closed_excluded`다
     (`state/history.py` `get_exclusion_place_ids()`). 같은 곳을 반복 추천하지
     않기 위한 장치이고 RECOMMEND 반복 흐름에서는 올바르게 동작한다. 다만 직전
     턴에 추천한 장소가 곧바로 이 목록에 들어간다.
  2. 일반 SCHEDULE 턴은 직전 노출분을 그대로 쓰지 않고 후보를 새로 채점한다
     (`_run_schedule_branch()`의 `schedule_candidates`). `shown_recommendations`는
     부분 재편성(`llm_output.modify is not None`) 경로에서 pinned_items로만 쓰인다.
  둘이 겹치면서 "이 장소들로 짜줘"가 구조적으로 "이 장소들만 빼고 짜줘"가 됐다.
- 결정:
  1. **새 SCHEDULE 턴에 한해** 마지막 run의 노출분(`shown_place_ids`)을 제외
     목록에서 뺀다(`_effective_excluded_place_ids()`). `get_exclusion_place_ids()`의
     계약은 바꾸지 않는다 — 이 턴에 무엇을 넘길지만 조정한다.
  2. 되살릴 대상은 `_revivable_shown_place_ids()`가 고른다. 재조정 턴
     (REJECT_ALL "다른 곳 보여줘", REJECT_SPECIFIC "두 번째는 별로야")은 되살리지
     않는다 — MODIFY로 분류된 뒤 SCHEDULE로 relabel되므로 `is_schedule`만으로는
     새 일정 요청과 구분되지 않고, 거절 대상이 `shown_place_ids`에도 남아 있어
     구분 없이 되살리면 REJECT 이력이 통째로 무력화된다. 판별에는
     `llm_output.modify`를 쓴다 — `_run_schedule_branch()`가 pinned_items를 쓸지
     정할 때 보는 것과 같은 신호다.
  3. **조회(`_fetch_tool_context`)와 채점(`_score_recommendations`) 두 단계 모두**에
     적용한다. 조회에서 걸러지면 채점 단계에는 후보 자체가 없고, 조회에서만
     되살리면 채점에서 다시 걸러진다.
  4. LangGraph 경로와 아직 남아 있는 구 경로의 호출부를 모두 배선한다 — 한쪽만
     고치면 기능 플래그를 껐을 때만 재발하는 종류의 버그가 된다.
- 근거: 사용자가 "이 장소들로"라고 명시한 의도가 반영되지 않는 것 자체가 결함이고,
  일정에 들어간 장소를 사용자가 본 적이 없어 "왜 이게 나왔는지" 설명도 성립하지
  않는다. 제외 목록의 의미를 바꾸는 대신 SCHEDULE 턴의 입력만 조정한 이유는, 중복
  추천 방지는 다른 인텐트에서 그대로 필요하기 때문이다.
- 채택하지 않은 것:
  - **"이 장소들로"류 발화를 인식해 재검색 없이 직전 목록을 그대로 후보로 쓰기** —
    사용자 의도에는 가장 충실하지만 발화 감지가 필요해 인텐트 분류 슬롯까지
    걸린다. 되살리기만으로 증상이 닫히는지 먼저 확인하기로 했다.
  - **SCHEDULE 턴에 제외 목록을 아예 적용하지 않기** — 거절·폐점 이력까지 함께
    풀려 REJECT가 무의미해진다.
  - **되살린 장소를 일정에 우선 배치하기** — 후보 복귀까지만 하고 배치는 기존
    채점·편성에 맡긴다. 필요하면 측정 후 별도로 판단한다.
- 검증: 단위 테스트 7건 추가(되살림/거절 유지/RECOMMEND 불변/순서 보존 등).
  첫 구현은 재조정 턴까지 되살려 기존 SCHEDULE 재조정 테스트 4건이 실패했고, 그
  실패가 결정 2의 근거가 됐다. 실사용 재현(용산역, 실제 provider)에서 추천 5곳 중
  2곳이 일정에 포함되고(수정 전 0곳), 이어진 "다른 곳 보여줘"에서는 직전 일정과
  0곳 겹치는 것을 확인했다. 브라우저 `/dev-chat`으로 부분 재조정("두 번째는
  별로야")이 1·3번을 유지하는 것까지 확인. 전체 테스트 통과, ruff 통과.
- 남은 것:
  - 되살린 장소가 일정에 얼마나 반영되는지는 채점·편성에 맡겨져 있다. "본 장소가
    한 곳도 안 들어간다"는 증상은 닫혔지만, 사용자가 기대하는 비율까지 맞추려면
    우선 배치가 필요할 수 있다 — 실사용 관측 후 판단한다.
  - LLM이 편성에서 제외한 후보가 기록되지 않는 문제(`int-07-schedule.md` 408행)는
    별건으로 남아 있다.
- 상세: `backend/app/services/runtime/agent_runtime.py`
  (`_revivable_shown_place_ids()`, `_effective_excluded_place_ids()`,
  `_fetch_tool_context()`, `_score_recommendations()`),
  `backend/app/services/runtime/graph/nodes/pipeline.py`,
  `backend/tests/test_agent_runtime.py`, `backend/app/state/history.py`

### D-108 — 서비스 지원 지역을 16개 구에서 22개 구로 확장하고 그 구의 집중률 매핑을 채운다

- 상태: `Accepted` — 구현 완료.
- 배경: place-sync로 여섯 구가 새로 적재됐는데(활성 1,516건) 서비스 지역 판정이
  16개 구만 통과시켜 후보로 나올 수 없었다. D-083(4곳 → 12곳)·D-086(12곳 → 16곳)과
  같은 상황이다. 이번에는 지원 목록 확장과 그 구의 집중률 매핑 구축을 한 PR에
  담았다 — D-083·D-086이 매핑을 남겨 두는 바람에 "추천에는 나오는데 혼잡도는
  답 못 하는" 반쪽 상태가 며칠 이어졌기 때문이다.
- 실측으로 확정한 사실:
  1. **district_code를 순서로 짐작하면 여섯 곳 전부 틀린다.** 코드 순서와 구
     이름이 한 칸씩 어긋나 있다 — 530은 영등포가 아니라 **구로**, 545는 동작이
     아니라 **금천**, 560이 영등포, 590이 동작, 620이 관악, 650이 서초다. 각 구
     표본 주소로 대조해 확인했다(D-083이 "추정하지 않고 확인했다"고 적어 둔 이유다).
  2. 폴리곤 면적을 공식 면적과 대조해 여섯 곳 전부 오차 0.33% 이내다(구로 -0.30%,
     금천 +0.05%, 영등포 +0.01%, 동작 +0.23%, 관악 +0.33%, 서초 -0.15%). 경계
     파일은 이미 25개 구를 담고 있어 손대지 않았다.
  3. 활성 장소 좌표를 폴리곤과 대조해 새 6개 구 1,516건 중 밖 4건(0.26%), 지원
     22개 구 전체 6,206건 중 13건(0.21%)이다(D-083 당시 0.63%). 밖으로 나온 4건은
     경계 정밀도가 아니라 좌표 자체의 문제다 — "도심속 바다축제"는 위도가 없고
     경도 자리에 위도값이 들어가 있으며, "방배배수지체육공원"과 "호국지장사(서울)"은
     D-083이 지목한 그 깨진 좌표 (19.694, 117.993)다.
  4. **`DISTRICT_LANDMARKS`가 안전망 노릇을 했다.** `service_area_landmarks.py`가
     임포트 시점에 `SUPPORTED_DISTRICTS`와 키가 정확히 일치하는지 assert로
     강제한다. 구만 늘리고 랜드마크를 안 채우면 앱이 아예 뜨지 않는다 — 실제로
     테스트 46건이 수집 단계에서 실패해 바로 드러났다(TP-160이 넣은 장치다).
  5. **집중률 API의 "과학전시관"은 개명 전 이름이다.** 구로·관악·중구 세 곳에서
     같은 쌍이 나왔다 — 집중률 API는 옛 이름(과학전시관)과 옛 표기(분관)를,
     `places`는 새 이름(융합과학교육원)과 새 표기(분원)를 쓴다. 세 곳 다 주소로
     대조해 서로 다른 실제 시설임을 확인했다(관악 본원 낙성대로 101, 구로 남부분원
     구로중앙로27나길 21, 중구 남산분원 소파로 46).
- 결정:
  1. `SUPPORTED_DISTRICTS`에 여섯 구를 더한다(22곳). `DISTRICT_LANDMARKS`에도
     같은 여섯 구를 채운다 — 좌표는 기존 관례대로 새로 조사하지 않고
     `tests/test_service_area.py`의 `_INSIDE`에서 구당 2곳씩 옮겼다.
  2. 여섯 구의 집중률 매핑을 만들어 적재한다. 391건 → **504건**이고 지원 22개 구
     전부 조회 성공률 100%다. 새 6개 구는 집중률 API 고유 장소 129곳 중 112건(87%)
     이다(구로 20/22 · 금천 11/11 · 영등포 20/22 · 동작 12/12 · 관악 21/27 ·
     서초 28/35).
  3. **경계에 걸친 역 좌표를 미지원 구 표본으로 쓰지 않는다.** `_OUTSIDE`에
     `"강남역": (37.4979, 127.0276)`이 강남구 미지원을 지키는 표본으로 있었는데,
     그 좌표는 실제로 **서초구 땅**이다(강남역이 두 구 경계에 걸쳐 있다). 즉 이
     항목은 이름과 달리 서초구를 검사하고 있었고, 서초구가 지원에 들어오자 깨졌다.
     강남구 안쪽인 삼성역·선릉역으로 바꾸고 주석에 이유를 남겼다 — 폴리곤 판정이
     틀린 게 아니라 좌표 선택이 애매했던 것이라, 다시 강남역으로 되돌리지 않게
     근거를 함께 적었다.
  4. **애매한 매칭은 붙이지 않는다.** 이름이 비슷해도 주소로 대조해 다른 곳이면
     뺐다 — 구로리공원↔구로거리공원(구로리어린이공원이 따로 있다), 관악산
     생태공원↔관악산(산 전체와 그 안 시설), 양재천 생태공원↔양재천 근린공원
     (양재천변에 공원이 여럿), 63스퀘어↔타임스퀘어, 강남↔힐튼 가든 인 서울 강남
     (권역명과 호텔) 등 16건이다.
- 근거:
  - 지원 목록 확장과 매핑 구축을 한 PR에 담은 이유는 반쪽 상태를 만들지 않기
    위해서다. TP-136 때는 집중률 조회의 구 고정(D-095 이전) 때문에 매핑을 먼저
    넣으면 회귀가 생겨 순서를 나눠야 했지만, 지금은 그 제약이 없다 — 매핑이 0건인
    구에 채우는 것이라 잃을 값이 없다.
  - 중구 남산분원은 2026-08-26에 "다른 장소일 수 있다"며 뺐던 건이다. 입구가 용산
    쪽이라 헷갈렸을 뿐 시설은 중구이고, 구로·관악에서 같은 개명 쌍이 나오면서
    규칙임이 드러나 되살렸다. 그래서 중구가 48건 → 49건이 됐다.
- 채택하지 않은 것:
  - **지원 목록만 늘리고 매핑은 뒤로 미루기** — D-083·D-086이 그렇게 해서 "추천에는
    나오는데 혼잡도는 답 못 하는" 상태가 이어졌다. 같은 간극을 다시 만들지 않는다.
  - **유사도가 높은 짝을 자동으로 붙이기** — 위 4번의 사례들이 전부 유사도
    0.71~0.91이었다. 주소로 대조하지 않으면 다른 곳을 붙인다.
- 검증: `pytest` 3,129건 통과, `ruff` 클린. `tests/test_service_area.py`는 83건 →
  113건. 새로 붙인 수동 매핑 8건을 집중률 API에 직접 조회해 정식 명칭까지 일치하는
  것을 확인했고, 적재 후 `verify_concentration_mappings.py`로 22개 구 504건 전부
  조회 성공을 확인했다.
- 남은 것:
  - 강남·송파·강동 3개 구가 아직 지원 밖이다. `places`에도 아직 없다.
  - `타임스퀘어`가 같은 주소로 두 건(806322, 4009379) 중복 등록돼 있다. 용산공원
    부분개방부지·계남근린공원과 같은 유형이라 매핑하지 않았다.
- 상세: `backend/app/service_area.py`, `backend/app/service_area_landmarks.py`,
  `backend/scripts/verify_concentration_mappings.py`,
  `backend/tests/test_service_area.py`, `supabase/data/concentration_manual_overrides.csv`

### D-109 — 서울 25개 구 전체를 지원 범위로 하고 집중률 매핑을 채운다

- 상태: `Accepted` — 구현 완료.
- 배경: place-sync로 강남(680)·송파(710)·강동(740)이 적재되면서 남은 세 구가 다
  들어왔다. D-108(16곳 → 22곳)의 후속이고, 이로써 **경계 파일의 25개 구와 지원
  목록이 같아졌다.** 활성 장소 1,806건이 후보로 나올 수 있게 된다.
- 실측으로 확정한 사실:
  1. 폴리곤 면적을 공식 면적과 대조해 세 곳 전부 오차 0.23% 이내다(강남 -0.23%,
     송파 -0.15%, 강동 -0.16%). 활성 장소 대조는 새 3개 구 1,806건 중 밖 2건
     (0.11%), 지원 25개 구 전체 8,012건 중 15건(0.19%)이다. 밖 2건은 둘 다 D-083이
     지목한 깨진 좌표 (19.694, 117.993)로, 이 값이 이제 5건이 됐다.
  2. **서울 안에는 "밖" 표본이 하나도 남지 않는다.** 25개 구가 전부 지원 범위라
     `_OUTSIDE`가 서울 좌표로는 성립하지 않는다. 잠실역·천호역·삼성역·선릉역이
     모두 안으로 들어왔다.
  3. **TourAPI 일일 한도는 오퍼레이션 단위다**(config 주석, 2026-08-07
     `areaBasedList2` 소진 기록). 오늘 `detailIntro2`가 996회로 소진돼 강남구
     동기화가 323건에서 `TOUR_DETAIL_QUOTA_EXCEEDED`로 끊겼고, 집중률
     (`TatsCnctrRateService`)은 전 구 검증 580건으로 따로 소진됐다. 목록
     (`areaBasedList2`)은 구당 1~2회뿐이라 여유가 많다.
- 결정:
  1. `SUPPORTED_DISTRICTS`에 세 구를 더한다(25곳). `DISTRICT_LANDMARKS`에도 채운다.
  2. **강남구를 상세 조회가 덜 찬 채로 넣는다.** 322/1,133건에서 멈춰 810건이
     `pending`이지만, 상세가 없어도 추천·혼잡도는 동작하고 지원 목록에 없으면
     후보로 아예 나오지 않아 그쪽이 더 나쁘다. 남은 810건은 place-sync 재실행이
     `pending`부터 이어받는다.
  3. **경계 파일과 지원 목록이 같아져도 "파일에 있는 구를 전부 지원"으로 바꾸지
     않는다.** 지원 범위는 팀이 합의하는 결정이라 코드에 드러나야 한다는 판단은
     그대로다(D-083에서 두 번 기각). 25개 구가 우연히 일치한 것이지 규칙이 바뀐
     것이 아니다.
  4. `_OUTSIDE`를 서울 밖 좌표로만 다시 짠다. 인접 경기 시를 **방향별로** 하나씩
     둔다(동 구리·남양주, 서 부천·김포, 남 광명·과천·안양·성남, 북 고양·의정부).
     한쪽만 두면 그 방향의 폴리곤만 지켜진다. 원거리 대조군으로 부산역을 남긴다.
  5. 집중률 매핑 76건을 적재한다(강남 36·송파 26·강동 14). 504건 → **580건**.
     수동 매핑 5건은 전부 접두·접미어 차이다.
- 근거:
  - `_OUTSIDE`가 이제 "확장이 서울 시계를 넘어가지 않는지" 하나만 지킨다. 예전에는
    "아직 안 넣은 구를 실수로 넣지 않았는지"도 함께 지켰는데, 지원할 구가 더 없어
    그 역할이 사라졌다.
  - 검증을 마치지 못한 것을 성공으로 적지 않는다. 전 구 검증이 415건 success ·
    165건 unavailable로 끝났다. `no_data`가 0건이라 **이름이 안 맞아 실패한 건은
    나오지 않았지만**, unavailable 165건은 API가 응답을 거부한 것이라 맞는지 틀린지
    아직 모른다. 새로 붙인 수동 매핑 5건은 개별 조회로 정식 명칭까지 확인했다.
- 채택하지 않은 것:
  - **강남구를 상세 완료 후에 넣기** — 상세가 없는 것보다 후보로 아예 안 나오는
    것이 나쁘다. D-083도 상세가 덜 찬 구를 넣은 선례가 있다.
  - **`_OUTSIDE`를 비우기** — 서울 안에 표본이 없다고 묶음을 없애면, 확장이 경기로
    새는 것을 잡을 수단이 사라진다.
- 남은 것:
  - 강남구 상세 810건. place-sync 재실행으로 채운다.
  - 전 구 조회 검증. 한도가 초기화되면 `--district-code`로 **새 세 구만** 잰다.
    검증은 매핑 건수만큼 호출하므로 580건이 된 지금은 전 구 실행이 한도의 절반을
    넘게 쓴다 — 습관적으로 전 구를 돌리지 않는다.
  - 깨진 좌표 (19.694, 117.993)이 5건으로 늘었다. 적재 파이프라인의 결측치 대체값
    으로 보이나 원인은 여전히 확인하지 않았다(D-083 이후 그대로다).
- 검증: `pytest` 3,141건 통과, `ruff` 클린. `tests/test_service_area.py`는 113건 →
  122건.
- 상세: `backend/app/service_area.py`, `backend/app/service_area_landmarks.py`,
  `backend/scripts/verify_concentration_mappings.py`,
  `backend/tests/test_service_area.py`, `supabase/data/concentration_manual_overrides.csv`

### D-110 — 장소 보관함은 추천 이력과 분리된 별도 엔티티로 둔다

- 상태: `Implemented` — 담기/빼기 API와 상태 저장까지. 일정 편성 반영은 후속 카드.
- 배경: "추천에서 장바구니 담듯이 장소를 저장해서 그 장소들로 일정 짜달라"는 기능
  요청(2026-08-31). INT-07 "알려진 갭"에 이미 적혀 있던 항목이다 — State에 "사용자가
  원하는 장소" 개념 자체가 없어 SCHEDULE 입력 모델을 바꿔야 한다는 것. D-107(TP-180)이
  직전 턴 노출분의 후보 복귀까지는 닫았지만, `shown_place_ids`는 **마지막 run만** 담고
  (`history.py`) "보여준 것"과 "사용자가 고른 것"을 구분하지 않는다.
- 결정:
  1. **`RecommendationHistory`에 컬럼을 더하지 않고 `SavedPlaceList` 별도 엔티티로
     둔다.** Supabase도 별도 테이블(`saved_place_lists`)이다.
  2. 담기·빼기는 **인텐트 분류를 거치지 않는 전용 REST**로 처리한다
     (`POST`/`DELETE`/`GET /api/state/{session_id}/saved-places`).
  3. 담을 수 있는 것은 **그 세션에서 노출된 적이 있는 place_id**뿐이다. 다만
     `get_shown_place_ids()`처럼 마지막 run으로 좁히지 않고 누적 이력 전체를 본다
     (`find_recommended_item()` 신설).
  4. 담기/빼기는 **멱등**이다. 이미 담긴 장소를 다시 담거나 없는 장소를 빼는 요청은
     오류가 아니라 `changed=False`다.
  5. `items`의 순서는 **담은 순서**이며 의미를 갖는다.
  6. `name`을 보관함에 저장한다 — "B는 place_id만 저장한다" 원칙의 예외를 하나 더
     쓴다(위경도는 필드만 열어두고 후속 카드에서 채운다).
  7. 세션 삭제(`delete_session`)와 만료 세션 정리 스크립트가 보관함도 함께 지운다.
- 이유:
  - **1번이 이 결정의 핵심이다.** 이력은 append-only인데 보관함은 담기/빼기가 되는
    가변 상태다. 더 중요한 건 `clear_recommended()`(계약 5.5절 history reset)가
    `recommended`와 `closed_excluded`를 비운다는 것 — 보관함이 그 테이블에 얹혀
    있으면 **"다른 곳 보여줘" 한 번에 사용자가 담아둔 장소가 함께 날아간다.**
    정식 인증(D-062 Phase 5) 이후 계정 단위로 옮길 때 이관 범위가 명확해지는 것도
    같은 분리에서 나온다.
  - 2번: 버튼 클릭은 해석할 여지가 없는 결정적 동작이다. `/api/chat`을 통하면
    오분류 위험과 LLM 지연이 그대로 붙는다. `clarification_choice`·
    `travel_origin_override`가 이미 같은 이유로 분류를 건너뛴다.
  - 3번의 누적 이력 조회: 화면에는 이전 턴의 추천 카드도 그대로 남아 있어, 사용자가
    스크롤을 올려 3턴 전 카드를 담는 것이 정상 동작이다. 마지막 run으로 좁히면 그
    경로가 400으로 막힌다. 같은 place_id가 여러 run에 걸쳐 노출됐으면 가장 최근
    항목을 쓴다.
  - 4번: 프론트가 낙관적 갱신을 쓰면 같은 요청이 두 번 날아가는 것이 정상이고,
    사용자가 원한 결과("담겨 있다"/"담겨 있지 않다")가 이미 성립한다. 다만 이력의
    중복 허용 정책(계약 3.5절)과 달리 **항목을 늘리지는 않는다** — 보관함은 누적
    기록이 아니라 현재 상태라서 같은 장소가 두 줄로 보이면 그 자체가 버그다.
  - 5번: 후속 카드에서 보관함 개수가 일정 항목 수 상한(`target_item_range()`,
    `time_available < 120`이면 최대 2개)을 넘을 때 무엇을 남길지 이 순서로 정한다.
    점수 순으로 자르면 **왜 그 곳이 빠졌는지 사용자에게 설명할 수 없다.**
  - 6번: `RecommendedItem.name`을 SCHEDULE-09 2단계에서 예외로 넣은 것과 근거가
    같다 — 지명 검색이 호출마다 다른 좌표로 resolve돼 이번 턴 후보에서 place_id를
    못 찾는 사례가 실사용에서 확인됐다(2026-08-11). 보관함은 담고 나서 여러 턴 뒤에
    쓰이는 것이 정상이라 이 재검색 실패 확률이 `recommended`보다 오히려 높다.
  - 7번: 남겨두면 같은 session_id가 재사용될 때 이전 사용자가 담아둔 장소가
    되살아난다.
- 기각한 안:
  - `RecommendationHistory.saved`로 컬럼 추가 — 마이그레이션이 한 줄로 끝나 가장
    작은 변경이지만, history reset이 보관함을 비우는 문제를 `clear_recommended()`
    안에 예외 분기로 막아야 한다. 그 함수의 계약("추천 이력만 비운다")에 예외를
    더하는 쪽이 테이블을 하나 더 두는 쪽보다 나중에 더 비싸다고 봤다.
  - 자연어 담기("2번 저장해줘")를 함께 넣기 — MODIFY 확장이 필요하고 A 경계에
    닿는다. 버튼이 없는 상태에서 자연어만 먼저 열면 검증할 대상이 흐려진다.
  - `place_id` 검증을 마지막 run(`shown_recommendations`)으로 좁히기 — 위 3번 이유로
    기각. 주입 차단 효과는 누적 이력 조회와 동일하다.
- 미결(후속 카드):
  - 보관함을 SCHEDULE 후보 풀로 되살리는 것(`_revivable_place_ids()`) — D-107 확장.
    거절 기록 시 보관함에서 자동 제거해 `saved ∩ rejected = ∅`을 구조적으로 보장하는
    것도 여기 포함이다.
  - `SchedulePlanningRequest.must_include_place_ids`와 배치 하드 검증.
  - 추천 시점 위경도 스냅샷 — 보관함 장소가 이번 턴 검색 반경 밖이면
    `_build_pairwise_distances_km()`가 좌표를 못 찾아 조용히 건너뛴다.
  - 프론트(`PlaceCard` 담기 토글, 하단 보관함 바, `schedule_from_saved`).
- 상세: `backend/app/state/schema.py`, `backend/app/state/saved_places.py`,
  `backend/app/state/store.py`, `backend/app/state/supabase_store.py`,
  `backend/app/state/history.py`, `backend/app/state/service.py`,
  `backend/app/state/errors.py`, `backend/app/routes/state.py`,
  `backend/scripts/cleanup_expired_sessions.py`,
  `supabase/migrations/202608310001_create_saved_place_lists.sql`,
  Notion `SCHEDULE-12 — 장소 보관함으로 일정을 구성한다 (설계안)`
### D-111 — 추천 후보 상한을 30으로 올리고, 상세 조회 기본 출처를 Supabase로 바꾼다

- 상태: `Accepted` — 구현 완료(패키지 C).
- 배경: "안국역 근처 갈만한곳 알려줘"를 발화만 바꿔가며 눌러도 거의 같은 곳이
  나왔다. 원인은 후보 수집이 `locationBasedList2`를 거리순으로 부르고
  `RECOMMENDATION_CANDIDATE_LIMIT`(10)만큼 자르기 때문이다. 안국역에서 그 10곳은
  반경 179m 안이고, 사용자가 "2km"라고 말해도 실제 선택지는 그 179m다. D-098이
  사진 검색에서 겪은 것과 같은 증상이다.
- 결정:
  1. `MAX_RECOMMENDATION_CANDIDATE_LIMIT`을 20 → **30**,
     `DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT`을 10 → **30**으로 올린다.
  2. `PLACE_DETAILS_SOURCE` 기본값을 `tour_api` → **`supabase`**로 바꾼다.
  3. `MAX_PLACE_PROVIDER_ROWS`를 100 → **300**으로 올린다.
  4. `tour_api` 상세 + 후보 한도 10 초과 조합을 `validate_provider_config()`에서
     막는다.
  5. SCHEDULE의 D 반환 개수를 후보 상한에서 떼어내 `SCHEDULE_RECOMMENDATION_LIMIT`
     (10)으로 고정한다.
- 근거:
  - **D-099의 기각 근거 두 개가 모두 무너졌다.** 그 카드는
    `MAX_RECOMMENDATION_CANDIDATE_LIMIT` 상향을 "A/D가 공유하는 정책이고 TourAPI
    속도 때문에 실용적이지 않다"로, 상세 출처 전역 전환을 "신선도 절충은 추천 쪽이
    따로 판단할 일"로 미뤄뒀다. 소유는 추천 쪽으로 확인됐고, 속도 전제는 후보마다
    상세를 부른다는 가정이었는데 supabase 출처면 배치 1회다. 신선도는 재보니
    활성 8,007곳 전량이 상세 30일 이내(TTL과 같은 값), 68%가 7일 이내였다.
  - **왜 30인가 — 밤이 아니라 낮 때문이다.** 반경 2km 하드 필터 통과 수 실측
    (괄호는 보충 조회 전):

    | 중심점 | 한도 | 9시 | 14시 | 19시 | 21시 | 23시 |
    | --- | --- | --- | --- | --- | --- | --- |
    | 안국역 | 10 | 6(2) | 16(8) | 12(5) | 12(3) | 6(1) |
    | 안국역 | 30 | 26(6) | 52(26) | 34(20) | 36(12) | 18(6) |
    | 경복궁 | 10 | 7(1) | 14(8) | 9(4) | 5(1) | **1(0)** |
    | 경복궁 | 30 | 24(7) | 47(20) | 32(9) | 34(5) | 16(1) |

    **처음에는 "밤에 카드가 빈다"를 근거로 삼았는데, 보충 조회를 빼고 잰 값이었다.**
    보충을 넣으면 한도 10에서도 결과 5곳을 못 채우는 칸은 경복궁 23시 하나뿐이다.
    괄호 값만 보면 9·21·23시가 전부 실패로 보이지만 실물은 보충이 돈다.
  - **진짜 근거는 낮 시간대의 후보 폭이다.** 통과율이 100%인 시각에는 보충이 아예
    돌지 않아(`_candidate_pool_exhausted`) 후보가 이 값에서 끝난다. 가장 흔한
    시간대의 선택지를 이 값 하나가 정한다 — 10일 때 안국역 상위 5곳은 갤러리
    미즈·뉘조·인사동 옥정이고, 넓히면 쌈지길·안녕인사동·북촌한옥마을이 올라온다.
    통과 수는 후보 수에 거의 비례한다(멀어진다고 통과율이 떨어지지 않는다).
  - **왜 상세 출처를 함께 바꾸나.** 추천은 후보 **전량**의 상세를 받아야 순위를
    매길 수 있다 — 하드 필터(영업 종료)와 잔여 운영시간 Feature가 운영시간을
    요구해서 "상위 5곳만 받기"가 성립하지 않는다. 그래서 출처가 곧 호출 수를
    정한다(안국역 실측): 후보 10곳에 supabase 2회 / tour_api 21회, 후보 30곳에
    supabase 2회 / tour_api 61회. tour_api는 오퍼레이션별 일일 한도 1000을 후보
    30 기준 33요청 만에 태운다.
  - **부팅에서 막는 이유는 D-042와 같다.** 이 조합은 오류를 내지 않고 한도만
    빠르게 소진시킨다. 지연이 아니라 호출 수가 문제다(후보 30곳에 2.1초, 61회).
  - **`MAX_PLACE_PROVIDER_ROWS = 100`의 근거 주석이 틀렸다.** "한 페이지에 허용하는
    최대"라고 적혀 있었는데 실측하면 요청한 만큼 그대로 준다(반경 10km, 전량
    1,598곳): 100행 69KB·321ms / 1000행 704KB·424ms / 2000행 요청 시 1,598건
    1,133KB·464ms. 행 수 16배에 지연은 44%만 는다. 300으로 잡은 것은 후보 30
    기준으로 "더 보기" 3턴까지 요청한 개수를 채우는 값이기 때문이다(100이면 2턴부터
    모자라 3턴에 15곳으로 떨어졌다).
  - **SCHEDULE을 떼어낸 이유.** 후보 상한과 "일정 편성에 몇 곳을 넘길까"는 뜻이
    다른데 우연히 같은 값을 쓰고 있었다. 상한을 올리자 SCHEDULE의 D 반환 수가
    10 → 30으로 따라 올라갔고, 그 10은 D와 협의해 확정한 값이다(int-07-schedule.md
    135행·5절). 혼잡도 2차 재순위도 그 개수 전부에 걸려 외부 조회가 비례한다.
    **협의값을 유지하는 쪽이 보수적이라 상수로 고정했다 — 바꾸려면 D와 재협의한다.**
- 채택하지 않은 것:
  - **후보 검색 출처를 Supabase로 옮기기** — `locationBasedList2`는 안국역 반경
    2km에서 279곳(숙박·여행코스 제외)을 주는데 같은 반경 `places`에는 1,082곳이
    있다. 문우약국·천도교 중앙대교당처럼 100m 안인데 반경 조회에 안 나오는 곳이
    확인됐다(`detailCommon2`로 현존·좌표 확인). 다만 279곳이면 후보 30에 충분해
    이번 문제 해결에는 필요 없다. 별건으로 둔다.
  - **`.env`만 바꾸기** — 부팅이 막힌다. `config.py`의 `le=` 검증이 상한을 참조하고,
    상한 값이 테스트·계약 스키마(`enrichment_schemas.py`의 `max_length`)·문서 3곳에
    박혀 있다.
- 곁가지 발견:
  - **도보 실측 조회가 후보 수에 정비례한다.** `_fetch_travel_routes()`가 하드 필터
    통과 후보 전량을 목적지로 만들고 Provider가 목적지마다 요청을 쏜다. 후보를
    30으로 올리자 카카오 호출이 7~13건에서 25~35건이 됐다. 후보 상한이 후보 수집
    범위를 넘어 정하는 세 번째 축이다(앞의 둘은 SCHEDULE 반환 수, 보충 조회 목표).
    **이 PR에서는 고치지 않았다** — 1차는 직선거리로 채점하고 상위 N곳만 실측을
    붙이는 2단 채점이 필요하고, 그건 별도 작업이다.
  - **real 모드가 Supabase 자격증명을 요구하게 됐다.** 상세 기본값이 supabase가
    되면서 기존 `validate_provider_config()`의 supabase 검사에 걸린다. 기존 테스트
    하나가 그 변화를 잡아냈다.
  - 상한 값을 숫자로 박아둔 테스트가 세 곳 있었다(`21`, `20`, `limit=21`). 전부
    상수 참조로 바꿨다 — 다음에 상한을 옮길 때 또 깨진다.
  - **`MAX_PLACE_PROVIDER_ROWS`를 올려도 처음에는 효과가 없었다.** 상한이 Tool과
    Provider 두 곳에 나뉘어 있어, Tool에서 300으로 올려도 `real_place.py`의
    `min(limit, 100)`이 다시 잘랐다. 요청은 270행인데 실제로는 100행이 나가고
    잘렸다는 신호도 서지 않았다(Tool 기준으로는 상한 안이라 `truncated`가 False).
    측정 스크립트의 "더 보기" 3턴에서 경복궁 새 후보가 0곳으로 나와 드러났다 —
    반경 안에 쓸 수 있는 후보가 204곳이고 60곳만 본 시점이었다. 상수를
    `place_search_policy.py`로 옮겨 한 곳에만 두고 Provider가 참조하게 했다.
  - **측정 스크립트도 같은 함정에 빠졌다.** 한도를 스윕하는데
    `_candidate_pool_exhausted()`는 인자가 아니라 전역 설정을 읽어서, 한도 10을
    잴 때 "10 < 30"이 소진으로 읽혀 보충이 한 번도 돌지 않았다. 스윕 값을 설정에
    함께 반영해 고쳤다. **이 버그 때문에 한때 상향 근거를 과대평가했다.**
- 상세: `backend/app/recommendation_limits.py`, `backend/app/config.py`,
  `backend/app/providers/factory.py`, `backend/app/providers/real_place.py`,
  `backend/app/place_search_policy.py`, `backend/app/tools/nearby_place_details.py`,
  `backend/app/services/runtime/agent_runtime.py`, `backend/.env.example`,
  `backend/scripts/measure_candidate_limit_impact.py`

### D-112 — 후보 보충 조회는 장소만 다시 받는다

- 상태: `Accepted` — 구현 완료(패키지 C).
- 배경: 하드 필터 통과 후보가 목표에 못 미치면 A가 C를 다시 부른다
  (`_MAX_CANDIDATE_REFILL_ATTEMPTS = 2`). 그런데 보충 1회가 `fetch_context()`를
  통째로 다시 불렀다. 안국역 실측으로 1회에 외부 호출 7건이다 — 기상청 1, 공휴일 1,
  TourAPI 1, supabase places 3, 네이버 지역검색 1.
- 결정: `AgentContextRequest`에 `resolved_search_center: Coordinates | None`을 더한다.
  A가 첫 조회에서 확정한 기준점을 넘기면 C는 **장소만** 다시 준다 —
  `build_tool_execution_plan(places_only=True)`가 계획을 `SEARCH_PLACES` 하나로 줄이고,
  위치 결과는 좌표에서 만든다(`_resolved_center_location_result`).
- 근거:
  - **그 6건은 계산해서 버리던 값이다.** `_merge_recommendation_context_places()`가
    첫 배치에서 places만 갈아끼우고 나머지는 그대로 두며, `merge_prepared()`도 첫
    배치의 판정 기준을 재사용한다. 같은 턴이라 조건이 바뀔 일도 없다. 동작이 아니라
    낭비만 없앤 변경이다.
  - **좌표를 A가 넘기는 이유.** 위치 해석이 3건으로 제일 크다. 같은 턴이라 기준점이
    바뀌지 않으므로 다시 풀 이유가 없다.
  - **기존 계획 장치에 얹은 이유.** 날씨를 조건부로 켜는 자리가 이미 있었다
    (`_requires_weather`). 새 분기를 만들지 않고 같은 자리에 한 갈래를 더했다.
  - **왜 항상 도는 경로인가.** 낮에는 통과율이 100%라 보충이 안 돌지만, 그 밖의
    시각에는 거의 항상 2회 돈다 — `candidate_target`이 C의 1회 반환 최대치와 같은
    값이라 하드 필터가 한 곳만 떨궈도 목표에 못 닿는다.
- 채택하지 않은 것:
  - **첫 요청을 크게 잡아 보충을 없애기.** 왕복이 줄지만 C가 하드 필터 손실을
    예측할 수 없다(14시 2.7% ~ 21시 55%). C가 영업시간을 직접 판정하면 같은 판정이
    두 곳에 생긴다 — D-099가 하드 필터를 재사용한 이유와 같은 문제다.
  - **날씨·공휴일만 빼고 위치 해석은 두기.** 계약을 안 건드리지만 절감이 절반이다
    (7건 → 5건).
- 검증: 안국역 실측으로 보충 1회 7건 → **2건**(TourAPI 1 + supabase places 1), 후보
  수는 20곳으로 동일. 보충 2회가 도는 요청이면 21건 → 11건이다. 테스트 5건 추가.
- 상세: `backend/app/agent_context/schemas.py`,
  `backend/app/agent_context/tool_rules.py`, `backend/app/agent_context/service.py`,
  `backend/app/services/runtime/context_transform.py`,
  `backend/app/services/runtime/agent_runtime.py`,
  `docs/design/a-c-context-contract-draft.md`, `docs/design/agent-runtime-contract.md`


### D-113 — 도보 실측은 1차 채점 상위 후보에만 조회한다

- 상태: `Accepted` — 구현 완료(패키지 C, TP-103 후속).
- 배경: `_fetch_travel_routes()`가 하드 필터 통과 후보 **전량**을 목적지로 만들고
  Provider가 목적지마다 요청을 쏜다(`walking_route.py`의 `asyncio.gather`). 후보
  상한을 30으로 올리자(D-111) 카카오 호출이 7~13건에서 25~35건이 됐다. 결과에
  나가는 것은 5곳뿐인데 나머지 몫까지 치르고 있었다.
- 결정: 채점을 두 번 부른다.

  ```
  1차 (실측 없이, 직선거리) → 상위 10곳 추림
  그 10곳만 도보 실측 조회
  2차 (그 10곳, 실측 반영)  → 상위 5곳
  ```

- 근거:
  - **2차 대상을 실측 받은 후보로 한정하는 것이 핵심이다.** `scoring.py`의
    `_consistent_routes()`가 "후보 중 하나라도 실측이 없으면 전부 직선거리로
    채점한다"고 정해 두었다. 전체를 채점하면서 일부에만 실측을 붙이면 실측이 통째로
    버려진다. 좁혀 두면 그 안에서는 전원이 실측을 가져 규칙을 만족한다.
  - **왜 10인가 — 5로 두면 실측이 선택에 관여하지 못한다.** 5면 1차가 고른 5곳이
    그대로 최종이 되어 표시 시간과 내부 순서만 바뀐다. 그 차이가 실제로 나는지
    재봤다(2026-08-31, 안국역·경복궁·홍대입구 x 14시·19시, 반경 2km): **6개 조합 중
    3개에서 최종 5곳의 집합이 바뀌었다.** 안국역 14시는 5곳 중 3곳이 갈렸다.
  - **직선거리와 실거리의 비율이 일정하지 않아서다.** 도심 우회 계수가 평균 1.31배,
    범위 1.07~1.71이다(2026-08-20, 종로 6개 지점). 직선 350m인 두 곳이 실제로는
    375m와 600m일 수 있다.
  - **`domain/scoring.py`(D 소유)는 건드리지 않았다.** 채점 규칙은 그대로고 부르는
    순서만 바꿨다. 채점은 순수 계산이라 두 번 돌려도 비용이 없다.
  - **`excluded_candidates`는 좁히지 않는다.** 그것은 "왜 떨어졌나"의 기록이고 A가
    `excluded_all_closed`로 읽는다. 좁히는 것은 이번 채점에 넣을 대상이지 필터
    판정이 아니다.
- 되돌아가는 경로: 이동수단 미지정·경로 Tool 없음·실측 조회 실패면 한 번만 채점한다.
  같은 후보를 실측 없이 두 번 채점할 이유가 없다.
- 검증: 도보 조회 25~35건 → **10건 고정**. 테스트 2건 추가(통과 후보 25곳일 때
  목적지 10곳, 통과 후보 3곳이면 3곳 전부).
- 남은 것: 이 방식은 1차를 직선거리로 매기므로 경계 오차가 완전히 사라지지는 않는다.
  10을 넘겨 실측 기준으로만 뒤집히는 후보가 얼마나 되는지는 재지 않았다.
- 상세: `backend/app/services/runtime/agent_runtime.py`

### D-114 — 보관함에 담은 장소는 후보로 되살리고 배치까지 구조적으로 보장한다

- 상태: `Implemented` — D-110(보관함 상태·API)의 후속. 프론트와 구 간 이동 이동시간은
  후속 카드로 남는다. **정정(D-116)**: 아래 결정 4의 하드 검증은 담아둔 장소가
  그 턴 후보 목록에 들어와 있을 때만 작동한다. 후보 주입이 없어 실제로는
  무력했고, D-116에서 채점 이전 단계 주입을 더해 닫았다.
- 배경: D-110으로 담을 수는 있게 됐지만 담아도 일정에 전혀 반영되지 않았다. 두 가지가
  막고 있었다. (1) D-107이 되살리는 `shown_place_ids`는 **마지막 run만** 담아
  (`history.get_shown_place_ids()`) 3턴 전에 담은 장소는 제외 목록에 그대로 남는다.
  (2) 후보 풀에 들어가는 것과 일정에 배치되는 것은 다르다 — 채점 순위에서 밀리면
  그대로 빠지고 사용자는 담은 이유를 잃는다.
- 결정:
  1. **`_revivable_shown_place_ids()` → `_revivable_place_ids()`.** 되살릴 대상에
     보관함을 합집합으로 더한다. 직전 노출분은 새 SCHEDULE 턴에만(D-107 그대로),
     **보관함은 재조정 턴에도** 되살린다.
  2. **`record_rejected()`가 같은 place_id를 보관함에서 뺀다.** `saved ∩ rejected = ∅`
     을 구조적으로 보장한다. 이 처리를 `service.py`가 아니라 `history.py`에 둔다.
  3. **`RecommendedItem`·`SavedPlaceItem`에 위경도를 싣는다** — "B는 place_id만
     저장한다" 원칙의 네 번째 문서화된 예외. `_build_pairwise_distances_km()`가 C
     응답에 없는 place_id를 이 스냅샷으로 메운다(C 응답이 있으면 그쪽 우선).
     `_finalize_recommendation_response()`에 `tool_context` 인자를 추가했다.
  4. **`SchedulePlanningRequest.must_include_place_ids`** 신설. 프롬프트
     (`schedule.plan` 1.1.0 → 1.2.0, `[반드시 포함]` 섹션)로 지시하고
     `plan_schedule()`이 응답 직후 `set(must_include) ⊆ {item.place_id}`를 하드
     검증한다. 누락 시 **1회만** 재시도하고, 그래도 빠지면 **결과를 살린다.**
  5. **개수 충돌은 담은 순서로 자른다.** 보관함이 `target_item_range()`의 상한을
     넘으면 앞에서부터 상한까지만 강제하고, 나머지는 이름을 `ScheduleResult.
     omitted_saved_place_names`에 실어 말풍선으로 알린다.
  6. 부분 재편성(`SchedulePartialFillRequest`, REJECT_SPECIFIC)에는 `must_include`를
     넘기지 않는다.
- 이유:
  - 1번에서 보관함만 재조정 턴에도 되살리는 근거: 사용자가 명시적으로 담아둔 것이라
    "두 번째는 별로야"가 담아둔 나머지까지 후보에서 뺄 이유가 없다. D-107이 직전
    노출분을 재조정 턴에서 제외한 이유(거절 대상이 `shown_place_ids`에도 남아
    있어 되살리면 REJECT가 무력화된다)는 보관함에는 2번 덕분에 적용되지 않는다.
  - 2번을 `history.py`에 둔 이유: 호출부가 두 번 부르는 것을 잊으면 불변식이 조용히
    깨진다. 지금 `record_rejected()`의 호출부는 `service.apply()` 한 곳뿐이지만,
    불변식은 호출 규약이 아니라 코드로 지켜야 한다. 이 덕분에 `_revivable_place_ids()`
    가 두 목록의 시간 순서를 비교할 필요가 없어졌다 — TP-180에서 테스트 4건이
    깨졌던 지점("거절된 장소도 shown에 남아 있다")이 애초에 생기지 않는다.
    순환 참조는 없다(`saved_places.py`는 `history.py`를 부르지 않는다).
  - 3번: 보관함은 **담고 나서 여러 턴 뒤에 쓰이는 것이 정상**이라, 이번 턴 검색
    반경 밖일 확률이 `recommended`보다 오히려 높다. 그러면 C 응답에 아예 없어
    거리 근거가 통째로 사라지고, 강남 장소가 종로 일정의 2번째에 꽂혀도 막을 수
    없다. 근거는 SCHEDULE-09에서 `name`을 예외로 넣은 것과 같다(지명 검색이
    호출마다 다른 좌표로 resolve되므로 재검색에 의존하면 매번 실패한다).
  - 4번에서 **부분 성공을 고른 이유**: `plan_partial_schedule()`은 같은 상황에서
    `llm_output_invalid`로 하드 실패하지만, 저쪽은 "유지해야 할 기존 일정"이 걸려
    있어 잘못된 응답이 기존 항목을 훼손한다. 보관함은 그렇지 않고, 장바구니에서
    "일부를 못 담았다"는 전체 실패(502)보다 낫다. 대신 조용히 빠뜨리지 않는다.
  - 5번에서 **점수 순이 아니라 담은 순**인 이유: 왜 그 곳이 빠졌는지 사용자에게
    설명할 수 있어야 한다. D-110에서 `SavedPlaceList.items` 순서를 담은 순서로
    고정한 것이 이 규칙의 전제다.
  - 6번: 부분 재편성은 `pinned_items`가 이미 자리를 붙들고 있고 사용자가 지목한
    자리만 새로 채우는 턴이라, 담아둔 장소를 그 자리에 밀어넣을 이유가 없다.
- 기각한 안:
  - `must_include`를 기존 `pinned_items`로 재사용 — 그건 order가 이미 정해진 기존
    일정 항목을 그 자리에 유지하는 구조라, 순서가 미정인 보관함과 의미가 다르다.
  - 후보 복귀 판정에서 담기·거절 타임스탬프를 비교해 나중 것이 이기게 하기 —
    런타임 판정은 불변식보다 약하다. 2번으로 대체했다.
  - 강제 포함 누락을 하드 실패로 처리 — 위 4번 이유로 기각.
  - 좌표를 D의 `RecommendationItem`에 추가 — 그쪽은 `distance_km`가 검색 중심 기준
    거리라 후보 간 거리를 못 구하는 것이 의도된 설계다. D 응답 계약을 넓히는 대신
    B가 스냅샷을 들고 있는 쪽을 골랐다.
- 남은 것:
  - 후보 목록에 아예 없는 보관함 장소(폐점 하드 필터 등)는 `planner`가 이름을 알
    방법이 없어 `agent_runtime`이 보관함 저장 이름으로 안내를 채운다. 안내 문구를
    두 곳에서 조립하는 형태라, 세 번째 사유가 생기면 한곳으로 모으는 편이 낫다.
  - 구 간 이동이 실제로 섞이기 시작하면 이동시간 가정(20km/h, 실측의 약 3.7배 낙관)
    문제가 드러난다 — 후속 카드.
  - `routes/recommendations.py:_record_shown()` 경로는 C 컨텍스트를 거치지 않아
    좌표가 계속 None이다(의도된 동작).
- 검증: 단위 테스트 추가(planner 6건, 보관함·거절 동기화 5건, 좌표 스냅샷·pairwise
  폴백 5건, 후보 복귀 3건, 말풍선 3건). 프롬프트 스냅샷 `schedule_plan_context__
  must_include` 신설.
- 상세: `backend/app/state/schema.py`, `backend/app/state/history.py`,
  `backend/app/state/service.py`, `backend/app/state/saved_places.py`,
  `backend/app/schemas.py`, `backend/app/schedule/schemas.py`,
  `backend/app/schedule/planner.py`, `backend/app/providers/gemini_prompts.py`,
  `backend/app/prompts/schedule/plan.md`,
  `backend/app/prompts/schedule/plan_context.md`,
  `backend/app/services/runtime/agent_runtime.py`,
  `backend/app/services/runtime/response_composer.py`,
  `backend/app/services/runtime/graph/nodes/pipeline.py`

### D-115 — 사진 검색은 조회할 때 전체 평균을 뺀다

- 상태: `Accepted` — 구현 예정(패키지 C, TP-197).
- 배경: 사진 업로드 경로의 정확도를 사람 눈가림 채점으로 처음 쟀더니
  무작위 대비 47.1% 대 4.8%로 확실히 나았지만(TP-193), 실패한 결과 11건이
  **전부 "종류·형태·구도는 맞고 분위기만 다름"**이었다. 능인선원(실내 불상)에
  성덕사(한옥 외관), 압구정 로데오(밝은 상권)에 성수동 수제화거리(주차장),
  어수선한 주택가 골목에 정돈된 익선동 한옥거리가 나오는 식이다. 갈린 차원이
  정확히 `weathered`·`calm` 축이 재는 것인데, **사진 경로는 축을 쓰지 않고
  벡터를 통째로 비교한다.** 축 정보가 벡터 안에 있어도(축 점수 = 벡터 · 축 방향)
  코사인 유사도는 768개 차원을 똑같이 취급하므로 "이것이 무슨 장면인가"라는 큰
  신호에 분위기 차이가 묻힌다.
- 결정: **사진 경로에 한해** 조회 시점에 전체 평균 벡터를 빼고 비교한다.
  적재된 벡터는 그대로 두고, 발화 경로(축 점수)는 건드리지 않는다.

  ```
  지금   질의 · 장소벡터
  바꿈   (질의 − 중심) · (장소벡터 − 중심)
  ```

- 근거:
  - **취소했던 결정을 뒤집는 것이라, 왜 그때가 틀렸는지가 먼저다.** 평균 빼기는
    한 번 채택했다가 취소한 적이 있다(종로 631곳에서 평균 순위 15.2위 → 18.8위).
    그런데 그 숫자는 **leave-one-out**으로 잰 것이다. 그 과제는 "같은 장소의 다른
    사진을 찾는" 일이라 모든 사진에 공통인 성분이 도움이 되고, 분위기 맞추기는
    그 공통 성분이 방해가 되는 과제다. **다른 과제에서 잰 값으로 이 경로의 결정을
    내린 셈이었다.**
  - **같은 질의 32장으로 두 경로를 나란히 놓고 사람이 눈가림 채점했다.**

    | | 평균 그대로 | 평균 뺀 쪽 | |
    | --- | --- | --- | --- |
    | 상위 5곳 전체 | 48.2% | **53.2%** | +5.0%p |
    | 직접 찍은 사진 | 44.3% | **51.9%** | +7.6%p |
    | 홍보 사진 | 53.3% | 55.0% | +1.7%p |
    | 1위만 | 67.9% | 67.9% | 변화 없음 |

  - **첫 측정은 채점 회차와 섞여 있었고, 다시 쟀다.** 처음에는 280칸 중 239칸을
    앞선 채점에서 재활용하고 41건만 새로 매겼는데, 그 41건이 한쪽에 몰려 있었다
    (빠진 곳은 옛 채점 39건·새 채점 1건, 들어온 곳은 그 반대). 그 상태로는
    "평균 뺀 쪽이 낫다"와 "두 번째 채점이 후했다"를 구분할 수 없다. 구조상 피할
    수 없는 오염이다 — 새로 올라온 후보는 정의상 앞선 채점에 없던 짝이라 언제나
    새로 매겨야 한다. **교체된 80칸을 한 목록에 섞어 같은 회차에서 다시 매긴
    값이 위 표다.** 겉보기 효과의 절반쯤이 회차였다(교체된 자리 기준 +30%p →
    +17.5%p).
  - **교체된 자리만 보면 빠진 곳 11/40(27.5%) 대 들어온 곳 18/40(45.0%)이다**
    (`p = 0.0812`). 양쪽 상위 5곳에 다 든 곳은 어느 쪽을 쓰든 결과에 남으므로 뺐다.
  - **유의성은 경계에 걸쳐 있다.** 세 시험이 0.05를 사이에 두고 갈린다 — 칸 단위
    `p = 0.0812`, 교체칸의 질의 단위 `p = 0.0481`, 상위 5곳의 질의 단위
    `p = 0.0730`. "효과가 있다"가 아니라 **"있어 보이는데 표본이 모자라다"**이며,
    질의가 28장뿐인 것이 그대로 드러난다. 그럼에도 채택하는 이유는 **방향이 세
    시험에서 모두 같고 나빠진 지표가 하나도 없으며, 채점과 무관한 증거(허브,
    한 장짜리 유입)가 같은 쪽을 가리키고, 되돌리는 비용이 설정 하나이기 때문이다.**
  - **때깔 격차가 9.0%p → 3.1%p로 줄었다.** 홍보 사진과 폰 사진을 가르던 것이
    바로 때깔이라는 공통 성분이었다. **실제 사용 시나리오인 폰 사진에서 개선이
    더 크다**(+7.6%p 대 +1.7%p).
  - **허브가 늘지 않는다.** 취소 때 걱정한 지점이라 따로 쟀다. 무작위 300곳을
    질의로 넣어 상위 5곳을 세니 1,500칸을 채운 장소가 1,159곳 → 1,212곳으로
    오히려 고르게 퍼졌고, 최다 등장은 양쪽 다 5회다.
  - **사진이 한 장뿐인 장소가 결과에 들어오기 시작한다**(5.0% → 15.0%). 평균이
    벡터를 가운데로 끌어당겨 DB가 두 덩어리로 갈려 있었다 — 한 장짜리를 질의로
    넣으면 이웃의 87.9%가 한 장짜리인데, 다섯 장 이상을 넣으면 1.2%만 한 장짜리다.
    DB의 56%가 한 장짜리인데 결과에는 5%만 나오던 상태였다. **새로 들어온 한
    장짜리가 오히려 잘 맞는다** — 재채점에서 들어온 40칸 중 14칸이 한 장짜리였고
    그중 9칸(64%)이 "비슷하다"로, 들어온 곳 전체의 45%보다 높다. 표본 14건이라
    확정은 아니다.
- 구현: **중심 벡터를 저장해 두고 뺀다.** 매 요청마다 `avg()`로 구하면 1,314ms가
  드는데 저장해 두면 184ms다(지금 60ms 대비 +124ms). 5,465곳 기준이고 전체 훑기라
  장소 수에 정비례하므로, 응답이 문제가 되면 그때 미리 뺀 컬럼과 색인을 따로 두는
  쪽으로 옮긴다 — 지금 그렇게 하지 않는 이유는 원본과 중심 제거본 두 벌이 생겨
  적재 때마다 어긋날 수 있기 때문이다.
- 대안: **사진별 최고점 방식**(장소 평균 대신 사진 하나하나와 비교해 장소별 최고점을
  쓴다)도 평균 편향을 없앤다. 함께 넣지 않은 이유는 조회 구조가 통째로 바뀌어
  무엇 때문에 좋아졌는지 갈라낼 수 없기 때문이다. 별건으로 남긴다.
- 남은 것: **표본을 늘려 다시 봐야 한다.** 질의 28장에서 유의성이 0.05 언저리라
  세 시험 중 하나만 통과한다. 재채점한 80칸은 전에 한 번씩 매긴 것이라 완전히
  새로운 판단이 아니고, 전부 혼자 채점했다(TP-193은 팀원 셋이 함께 봤다). 사람
  천장이 75.0%이므로 53.2%에는 아직 여지가 있다. **1위는 한 칸도 바뀌지 않았다** —
  평균 빼기는 2~5위를 고치지 1위를 고치지 않는다. 다음 측정 때는 질의를 새로
  마련해야 한다 — 지금 홍보 사진 질의 12장이 강남 것이라, 강남을 적재하면 그
  사진들이 자기 자신을 1위로 찾게 되어 못 쓴다.
- 상세: `supabase/migrations/` `search_place_mood`, `backend/app/providers/`

### D-117 — 사진 검색 순위를 VLM에게 다시 매기게 한다 (기본 끔)

- 상태: `Implemented` — 뼈대만. 후보 수와 문턱은 재측정 뒤 확정한다(TP-214)
- 결정 1: 임베딩이 좁힌 후보를 Gemini에게 보여 주고 순서를 다시 매기게 한다.
  **임베딩 안에서 개선하려는 시도를 아홉 번 했고 두 번만 통했다** — 평균 빼기
  (D-115)와 이것이다. 나머지 일곱(축 섞기·최고점·모델 교체·차원 무게 학습·PCA
  상위축 제거·축만·문구 100개)은 전부 같은 벡터를 다르게 읽는 방식이었고 사람
  눈가림 채점에서 잡음 바닥을 넘지 못했다. 사람이 매긴 성적이 뚜렷하게 오른 것은
  **다른 판단자를 붙였을 때**뿐이다 — 31.6% → 38.5%(`p = 0.171`, 후보 12곳) ·
  41.0%(`p = 0.025`, 같은 조건 다른 회차).
- 결정 2: **모델은 flash급으로 고정한다.** `gemini-3.5-flash-lite`로 내리면 개선이
  +6.8%p에서 +0.9%p(`p = 0.776`)로 사라진다. 응답이 13.1초에서 2.4초로 5.5배
  빨라지지만 그 손잡이가 곧 품질을 없애는 손잡이였다. 세 가지(후보 수·모델·사진
  크기)를 한꺼번에 줄였다가 효과가 +9.4%p → +3.5%p로 떨어졌고, 하나씩만 바꾼 네
  판을 다시 돌려 범인을 갈랐다.
- 결정 3: **응답 스키마로 점수 칸 수를 못 박는다.** 프롬프트로 부탁하면 모델이 한
  칸 모자란 답을 낸다 — flash-lite는 후보 12곳일 때 14번 중 12번을 그렇게 답했다.
  형식을 고정하면 실패가 0이 되고, 같은 모델·같은 후보·같은 사진에서 응답이
  **32.6초 → 13.1초로 2.5배 빨라진다.** 자유 형식일 때 군더더기를 만들다 느려지는
  것이라 어느 설정에서든 켠다.
- 결정 4: **1위 유사도가 0.525 미만이면 부르지 않는다.** 방향이 직관과 반대다 —
  임베딩이 헤맬 때가 아니라 잘 찾았을 때 부른다. 1위 유사도가 낮다는 것은 DB에
  닮은 곳이 아예 없다는 뜻이라 후보가 전부 안 맞는 곳이고, 순서를 바꿔봐야 나아질
  것이 없다. 오히려 임베딩이 그나마 낫게 잡아둔 것을 흐트러뜨린다 — 문턱 아래
  질의는 불러도 이득이 +0.0%였고 나빠지는 질의가 7장에서 3장으로 준다.

  | 문턱 | 호출 | 성적 | 차이 | p | 검색당 |
  | --- | --- | --- | --- | --- | --- |
  | 없음 | 100% | 35.4% | +5.7%p | 0.006 | 40원 |
  | 0.500 | 74% | 33.8% | +4.1%p | 0.008 | 30원 |
  | **0.525** | **64%** | 33.8% | +4.1%p | **0.008** | **26원** |
  | 0.550 | 54% | 32.8% | +3.1%p | 0.061 | 22원 |

  **0.525가 0.50과 성적이 같은데 14% 싸다.** 0.55부터는 도움이 되는 질의까지
  잘라내 유의성을 잃는다. 둘을 통계로 가르지는 못했고(성적이 같다) 싼 쪽을 골랐다.
  **이 눈금은 `place_mood_mean_center_enabled`가 켜져 있을 때의 것이다**(D-115) —
  평균을 빼지 않으면 유사도 분포가 달라져 문턱이 뜻을 잃는다.
- 결정 5: **자르기 전에 재랭킹한다.** 보여줄 수만큼 먼저 자르면 VLM이 순서만 바꾸고
  어떤 곳이 나올지는 못 바꾼다. 하드 필터 뒤·`limit` 자르기 앞에 넣어 뒤쪽 후보를
  앞으로 끌어올릴 수 있게 한다. 사진 주소 조회도 그만큼 앞당기는데, 같은 표를 한
  번에 읽는 조회라 후보가 몇 곱절이어도 왕복 횟수는 그대로다.
- 결정 6: **실패하면 임베딩 순서를 그대로 낸다.** 타임아웃·API 오류·응답 해석
  실패·칸 수 불일치 어디서든 예외를 올리지 않고 `None`을 돌려준다. D-042(Real
  실패 시 Fake로 자동 전환하지 않는다)와는 성격이 다르다 — 가짜 데이터로
  바꿔치기하는 것이 아니라 이미 검증된 임베딩 결과를 쓰는 것이다. 다만 **Fake LLM
  모드에서는 재랭커를 아예 만들지 않는다.** 가짜 응답으로 순서를 뒤집으면 오류
  없이 결과만 틀어지는데, 그것이 D-042가 나온 사건과 같은 성격이다.
- 결정 7: **기본값은 끔이다.** 검색 한 번에 16~47원이고 하루 500회면 월 24만원이다.
  단가는 실측이다 — 실험 253회에 입력 301만 토큰을 쓰고 크레딧 10,000원이 소진되어
  입력 100만 토큰당 약 3,300원으로 역산했다. 토큰은 **사진 장수에만 붙고 해상도와
  무관하다**(원본 14,278 · 512px 14,289). 비용을 줄이는 손잡이는 후보 수뿐이고,
  사진 축소는 시간도 토큰도 줄이지 않아 넣지 않았다.
- 결정 8: **보여줄 수보다 넉넉히 보낸다 — 5곳을 보여주고 10곳을 보낸다.** 보내는
  수와 보여주는 수가 같으면 **이득이 0이다.** 재랭커는 받은 후보의 순서만 바꾸므로
  뽑히는 곳 자체가 그대로인데, 쓰는 지표(상위 N칸 중 몇 칸이 "비슷하다"인가)는 순서와
  무관하기 때문이다. 실측으로도 뽑힌 곳이 그대로였던 질의 10장의 이득은 **정확히
  +0.0%**였고, 뽑힌 곳이 바뀐 29장에서만 +8.0%가 나왔다.

  **상위 5곳 기준으로 직접 쟀다.** 질의 39장을 같은 조건에 놓고 후보 수만 바꿨다.

  | 후보 | 임베딩 | VLM | 차이 | p | 겹침 | 검색당(문턱 0.525) |
  | --- | --- | --- | --- | --- | --- | --- |
  | 6곳 | 29.7% | 31.8% | +2.1%p | 0.128 | 4.6/5 | 16원 |
  | 8곳 | 29.7% | 33.8% | +4.1%p | 0.050 | 3.9/5 | 21원 |
  | **10곳** | 29.7% | **35.4%** | **+5.7%p** | **0.006** | 3.3/5 | **26원** |

  **여유가 클수록 이득이 커지고 10곳에서만 유의하다.** 6곳은 겹침이 4.6/5라 거의
  바뀌지 않는다 — 여유가 1곳뿐이라 재랭커가 할 일이 없다.

  **12곳으로 늘릴 근거는 없다.** 상위 3곳 기준으로 네 판을 견주면 순서가
  뒤죽박죽이고(6곳 +7.7 · 8곳 +6.0 · 10곳 +10.3 · 12곳 +6.8) 유일한 12곳 실측이
  10곳보다 낮았다. 상위 3곳은 한 칸이 33%p라 눈금이 굵어 잡음에 흔들린다 — 같은
  조건이어야 할 판들끼리 4.3%p 벌어진다. 상위 5곳(한 칸 20%p)에서 단조 증가가
  보이는 것과 대조된다.

  처음에는 상위 3곳 기준 측정만 있어 "잰 적 있는 비율"을 옮긴 8곳으로 두었는데,
  직접 재니 10곳이었다. 이 측정에 API 4,169원과 채점 276칸을 썼다. **처음 집계는 채점 96건을 은행에 합치지 않은 채 낸 값이었다** — 쓸 수 있는 질의가 33~36장으로 줄고 후보 10곳의 수치가 +5.5%p(`p = 0.010`)로 나왔다. 합친 뒤 39장 전부가 살아나 +5.7%p(`p = 0.006`)가 됐고 결론은 같다.
- 한계: **8곳이 실제로 다 넘어가지는 않는다.** RPC는 반경 안 전부를 유사도로 줄
  세워 `limit × 4 = 20곳`을 돌려주고(거리는 반경 안/밖만 가를 뿐 순위에 들어가지
  않는다), 그다음 영업시간 하드 필터를 지난 것만 재랭커에 넘어간다. 실측 생존율이
  20곳 중 7곳(35%)이므로 **여유가 3곳이 아니라 2곳인 경우가 잦다.** `_OVERFETCH_FACTOR`를
  5로 올리면 25곳 → 약 9곳이 되어 8곳을 채우지만, 그 값은 재랭킹을 꺼도 사진 검색
  전체에 걸리므로 지금은 올리지 않았다. 실측에서 여유 2곳으로도 74%의 질의가 밖에서
  후보를 끌어왔으므로 재측정 때 **실제로 몇 곳이 넘어가는지를 함께 재서** 정한다.
  생존율 35%도 한 번 잰 값이라 시간대에 따라 달라진다.
- 해소: **측정과 제품의 어긋남은 상위 5곳 재측정으로 닫혔다.** 확인 15~22가 상위
  3곳 기준이었고 그 3곳은 의도된 값이 아니었다 — 확인 13에서 "제품이 5곳을 보여주니
  5곳을 잰다"로 한 번 맞췄던 것이 앞 스크립트에서 복사돼 돌아간 값이다. 5곳으로
  다시 재니 **효과는 살아남았고**(+5.7%p, `p = 0.006`) **후보 수만 8 → 10으로
  바뀌었다.** 문턱은 0.525로 내렸다 — 0.50과 성적이 같으면서 호출이 74%에서
  64%로 줄어 검색당 30원이 26원이 된다.
- 결정 9: **후보를 글로 옮겨 벡터로 비교하는 길은 쓰지 않는다.** 검색당 26원을 0원으로
  줄이려고, 장소마다 오픈 VLM(Qwen2.5-VL-3B)이 쓴 분위기 설명글을 미리 만들어 두고 그
  글끼리 비교해 봤다. **이득이 정확히 +0.0%였다**(`p = 0.977`, 질의 29장). 같은 후보
  10곳을 Gemini가 다시 세우면 +4.8%p(`p = 0.037`)인 것과 대비된다. 원인은 설명글이
  종류를 다시 인코딩하는 것이다 — 서로 다른 두 공연장이 첫 문장까지 똑같은 글을 받았고,
  우리 실패 사례가 정확히 "종류는 맞는데 분위기가 다름"이다. **표현을 글로 압축하면
  잃는다는 결과가 이것으로 세 번째다**(축 5개는 무작위 수준, 문구 100개는 벡터의 60%).
- 한계: **문턱 값 자체는 여전히 39장·채점자 한 명으로 고른 값이다.** 0.50과 0.525가
  같은 성적을 내 둘을 가리지 못한다. 실사용 로그가 쌓이면 다시 잰다.
- 한계: **채점자 한 명의 회차 흔들림이 재려는 효과와 같은 크기다.** 이번 측정에서
  같은 짝 180건을 다시 매기니 점수가 −0.12 내려갔다(`p = 0.0058`). 재려는 효과가
  +5.7%p인데 회차 차이가 그만큼 움직이므로, **후보를 미리 다 채점해 두고 VLM을
  돌리는 순서**로 막았다 — 채점자가 VLM의 선택을 모르는 상태에서 매기므로 회차가
  진영과 얽히지 않는다. 그럼에도 다음 실험은 채점자를 늘리는 편이 낫다.
- 상세: `backend/app/providers/gemini_vlm_rerank.py`,
  `backend/app/services/photo_similar.py`, `backend/app/config.py`

### D-116 — 보관함 장소는 채점 이전 단계에서 후보 컨텍스트에 주입한다

- 상태: `Implemented` — D-114의 결함 수정. SCHEDULE-12 카드 3(담기 버튼) 검증 중
  드러났다. 상세 경위는 SCHEDULE-14 Why 노트.
- 배경: D-114가 `must_include_place_ids` + 하드 검증 + 1회 재시도로 배치를
  보장한다고 적었지만 **그 보장이 무력했다.** 담아둔 장소가 그 턴 후보 목록
  (`schedule_candidates`)에 없으면 `planner._resolve_must_include()`가 조용히
  버리고, `_missing_must_include()`는 이미 줄어든 목록만 보므로 통과한다 —
  재시도도 안 걸린다. 후보 목록은 그 턴 C 응답이 전부였다. D-114의 좌표
  스냅샷은 `_build_pairwise_distances_km()`의 `fallback_coordinates`로만 쓰이지
  후보를 만들지 않고, `_revivable_place_ids()`는 제외 목록에서 빼줄 뿐이라
  이번 턴 검색이 그 장소를 다시 물어오지 못하면 애초에 후보가 되지 못한다.
  인사동에서 4곳을 담고 일정을 요청하니 1곳만 들어간 실사용으로 드러났다 —
  지역이 달라야만 나는 문제가 아니다. 같은 지역이어도 POI가 후보 상한보다
  많으면 매 턴 다른 후보가 뽑힌다.
- 결정:
  1. **`_saved_places_context()` 신설.** 이번 턴 후보에 없는 보관함 장소를
     `get_active_place_details()`로 조회해 `tool_context.places`에 주입한다.
     Supabase 기반 일괄 조회라 TourAPI 쿼터를 쓰지 않고, `services/photo_similar.py`
     가 같은 패턴(상세 조회 → Context 구성 → 같은 하드 필터)을 이미 돌리고 있다.
  2. **`schedule_candidates`에 직접 꽂지 않는다.** 원소가 `RecommendationItem`이라
     `score`·`feature_scores`·`recommendation_reason`을 지어내야 하는데, **그 값들이
     편성 프롬프트에 그대로 들어간다** — 지어낸 점수와 사유로 LLM이 동선을 정하게
     된다. 한 단계 앞(`context.places`)에 넣으면 D가 정상 채점하므로 지어낼 값이
     없다. 후보 상한(`RECOMMENDATION_CANDIDATE_LIMIT`)을 올리는 안은 증상 완화라
     기각했다.
  3. **주입은 보충 조회 루프 뒤에, 보충 배치와 같은 방식으로 붙인다.** 루프 안이면
     C가 뒤늦게 같은 장소를 돌려줄 때 `prepared_batches`에 후보가 두 번 들어가고,
     `_candidate_pool_exhausted()`가 재는 값이 실제 C 응답과 어긋난다 — 그 함수는
     C가 더 줄 게 있는지를 재는 것이지 후보 총량을 재는 것이 아니다.

     **정정 (2026-09-01, 같은 날 후속)**: 원래 이 항목은 "주입한 개수만큼
     `recommendation_limit`을 올린다"로 끝났다. 자르기가 위험하다는 것까지는
     맞았지만 **방어가 되지 않는다** — 후보 풀이 상한보다 크면 주입분은 그냥
     하위권에 깔린다. 하필 보관함의 주력 유스케이스가 구 간 이동(= 검색 반경
     밖)이라 거리 점수가 0으로 깔려, 가장 확실하게 잘리는 것이 보관함 장소다.
     게다가 `_score_with_measured_routes()`의 `shortlist_limit`이 상한을
     따라가므로 도보 실측 조회까지 함께 늘어난다(D-113이 25~35건에서 10건으로
     줄여놓은 것을 되돌린다). 상한 인상은 걷어내고, **채점이 끝난 뒤 자르기에서
     빠진 보관함 장소만 `_narrow_prepared()`로 좁혀 한 번 더 채점해 목록 뒤에
     덧붙인다.** 점수는 D가 같은 공식으로 실제로 매기므로 결정 2의 "지어낼 값이
     없어야 한다"는 그대로 지켜진다. 덧붙이는 자리가 맨 뒤인 것은 순위를
     왜곡하지 않기 위해서다 — 배치는 `must_include_place_ids`가 보장하고 이
     목록에 필요한 것은 "후보에 있기"뿐이다. 실사용 재현(홍대 검색 중 인사동
     보관함 2곳 누락)과 재현 단위 테스트로 확인했다.
  4. **`ScheduleResult.absent_saved_place_names` 신설.** "후보에 아예 없었다"와
     "항목 수 상한·LLM 누락"은 해결책이 정반대인데 `omitted_saved_place_names`
     하나에 섞여 있어, `_with_omitted_note()`가 전자에게도 "시간을 늘리거나 다른
     곳을 빼고 다시 요청해보실래요?"를 붙이고 있었다. 후보에 없던 장소는 재시도를
     권하지 않는다.
  5. **배선은 두 곳 모두.** `run_agent_flow()`뿐 아니라 `graph/nodes/pipeline.py`의
     `PipelineDeps`·`scoring_node`까지 넘겨야 한다 — 같은 단계 함수를 두 곳에서
     부르고 실제로 도는 것은 그래프 쪽이다. D-114에서 `_finalize_recommendation_
     response()` 시그니처가 같은 함정을 냈던 것의 재발이다.
- 한계: 주입은 Supabase `places`에 상세 행이 있는 장소만 된다 — 없으면 후보가
  되지 못하고 `absent_saved_place_names`로 안내한다. "담은 장소가 반드시
  배치된다"는 real LLM으로만 확인된다. fake 스텁의 `generate_schedule_plan`은
  `must_include`를 보지 않고 후보 앞 3개를 그대로 고른다.
- 상세: `backend/app/services/runtime/agent_runtime.py`,
  `backend/app/services/runtime/graph/nodes/pipeline.py`,
  `backend/app/services/runtime/response_composer.py`, `backend/app/schemas.py`

### D-118 — 추천 실측 이동수단을 거리로 고르고, 시간 예산을 측정 수단에서 뗀다

- 상태: `Proposed` — A·D 리뷰 대기. 구현은 C가 맡고 PR에 "타 패키지 수정"을 명시한다.
- 배경:
  - 추천의 실측 이동수단은 요청당 하나로 고정된다(`to_travel_mode()`). 그런데
    `max_travel_time`이 없는 요청은 **`transport`가 무엇이든 도보**다. 자동차라고
    말한 사용자도 반경 2km 안을 도보로 재고 있었다.
  - 이동수단을 말하지 않고 이동시간만 말한 요청은 아예 실측하지 않는다(`None`).
    20km/h 가정이 대중교통인지 자동차인지 발화에 없다는 이유였다.
  - 결과적으로 반경 2km 안의 먼 후보(직선 1.5km, 실제 도보 40분)도 도보로만 재고,
    사용자가 실제로 어떻게 갈지는 판정에 들어가지 않는다.

- 결정 1 — **실측 이동수단을 1차 채점 뒤 후보별로 고른다.** 판정 자리는
  `_score_with_measured_routes()`의 상위 10곳 추린 직후이고, 입력은 1차 채점이
  이미 계산한 직선거리다.

  ```
  transport == WALK  → 도보 (거리 무관)
  transport == CAR   → 자동차 (거리 무관, 이동시간을 말하지 않았어도)
  그 외(PUBLIC·미지정):
      직선거리 ≤ 0.85km → 도보 단독 조회
      직선거리 >  0.85km → 도보·대중교통 병렬 조회 후 빠른 쪽 채택
  ```

  `estimate_schedule_travel_edges()`의 `_select_mode()`와 같은 판정이다. 두 기능이
  같은 사용자에게 "이 정도 거리는 걸어요"를 다르게 말하지 않게 한다.

- 결정 2 — **거리 점수의 시간 예산을 측정 수단에서 뗀다.** `_travel_minutes_budget()`이
  `TRAVEL_SPEED_KM_PER_MINUTE[측정 수단]`으로 나누던 것을, **그 요청이 검색 반경을
  만들 때 쓴 속도**로 나눈다. A가 `to_search_radius_km()`과 같은 분기
  (`_radius_uses_walking_speed()`)로 그 속도를 함께 넘긴다.

- 결정 3 — 임계를 넘은 후보는 도보와 대중교통을 **둘 다 조회**하고 성공한 것 중
  소요시간이 짧은 쪽을 채점에 쓴다.

- 결정 4 — 도보·대중교통 Provider가 **세마포어를 공유**한다. 지금은 인스턴스마다
  `max_concurrency=5`라 둘을 함께 돌리면 같은 카카오 키로 동시 10건이 나간다.

- 근거:
  - **결정 2가 없으면 결정 1은 역효과다.** 예산은 `반경 ÷ 속도`인데 기본 반경 2.0km는
    도보 속도로 만든 값이다. 여기에 대중교통 속도(20km/h)를 넣으면 예산이 6.0분이
    되고, 이건 "아무도 약속한 적 없는 숫자"다 — 20km/h로 2km를 가면 6분이라는 계산일
    뿐이다. 반경 2km 안의 대중교통 실측은 대부분 10~19분이라 `_travel_time_score()`의
    `_clamp`에 전부 0으로 잘린다. 거리 가중치 0.20을 통째로 잃는데, 그 손해가
    **전환된 후보에만** 간다. 도보 후보는 28.6분 예산으로 채점되기 때문이다.
    같은 반경을 도보 속도로 나눈 28.6분 하나로 재면 대중교통 12분은 0.58,
    도보 25분은 0.13이 되어 실제 소요시간 순서와 점수 순서가 어긋나지 않는다.
  - 이동시간을 말한 요청은 값이 바뀌지 않는다. 반경이 `시간 × 속도`라 같은 속도로
    나누면 그 시간이 그대로 나온다 — "분모가 사용자 약속 그 자체"라는 기존 설계는
    유지되고, 오히려 이동시간을 말하지 않은 요청까지 그 규칙을 따르게 된다.

  - **임계값 0.85km의 근거.** 규칙은 "도보로 20분을 넘으면 전환"이고, 20분은
    `schedule_walk_transfer_threshold_min`과 같은 값이다(SCHEDULE과 같은 말을 하기
    위해). 판정 시점에는 직선거리밖에 없으므로 그 20분을 직선거리로 환산해야 한다.

    | 환산 | 계산 | 전환 시작 직선거리 | 그 지점의 실제 도보 시간 |
    |---|---|---|---|
    | 보정 안 함 | 20분 × 0.07km/분 | 1.4km | 약 33분 |
    | 우회 보정(채택) | 20분 × 0.07 ÷ 1.65 | **0.85km** | 약 20분 |

    보정하지 않으면 "20분 넘으면 전환"이라고 적어놓고 실제로는 33분짜리부터 전환한다.
    우회 계수 1.65배는 카카오 실 API 실측값이다(2026-08-19, 종로 5개 지점, 직선 대비
    실제 보행 경로, 범위 1.37~2.13).

    **결정 3(양쪽 조회) 때문에 이 값을 낮게 잡는 비용이 호출 수뿐이다.** 임계를 낮춰
    도보가 빠른 구간까지 대중교통에 물어봐도, 빠른 쪽을 고르므로 틀린 답이 나오지
    않는다. 반대로 1.4km로 높이면 1.0~1.4km 구간(아래 실측에서 대중교통 12~17분,
    같은 거리를 걸으면 30분 안팎)을 통째로 놓친다. 그래서 두 값 중 낮은 쪽을 택했다.

  - **근거리 대중교통 조회 실패 우려는 실측으로 기각됐다**(2026-09-02, 안국역·
    홍대입구역, 반경 2km TourAPI 후보 40곳, 카카오 대중교통 실 API):

    | 직선거리 구간 | 대중교통 성공 | 실패 사유 |
    |---|---|---|
    | 0.4~0.7km | 8/10 | `kakao_status_no_results` 2건 (둘 다 홍대 0.41km) |
    | 0.7~1.0km | 10/10 | — |
    | 1.0~1.4km | 10/10 | — |
    | 1.4~2.0km | 10/10 | — |

    임계 0.85km 위쪽은 30건 전부 성공했다. 실패한 2건은 전환 대상 밖 거리다.

  - **카카오 대중교통은 근거리에서 도보 경로를 그대로 돌려주기도 한다.** 정독도서관
    (직선 0.42km)은 대중교통 12.2분과 도보 12.2분이 같은 값이었다. 반면 아띠인력거는
    대중교통 11.2분 · 도보 9.6분으로 대중교통이 더 느렸다. 항상 같지는 않으므로
    결정 3의 "빠른 쪽 채택"은 그대로 필요하다.

  - **결정 4는 실측 중 실제로 터졌다.** 두 Provider를 `asyncio.gather`로 함께 돌리자
    (동시 10건) 대부분이 `HTTP 400, message=API limit has been exceeded.`로 거절됐다.
    동시성을 2로 낮추고 두 수단을 직렬로 나누니 대중교통이 38/40 성공했다.

- 되돌아가는 경로: 대중교통 조회가 실패하면 그 후보는 도보 값으로 채점한다. 둘 다
  실패하면 지금과 같다 — `_consistent_routes()`가 그 회차 전체를 직선거리로 내린다.

- 검증(계획):
  - 임계 위/아래 후보가 각각 대중교통·도보 값으로 채점되는지 단위 테스트.
  - `transport=CAR`이고 `max_travel_time`이 없을 때 자동차로 재는지(현재는 도보).
  - **테스트 더블이 `KAKAO_TRANSIT`/`KAKAO_WALKING` source를 내야 한다.** Fake
    Provider는 `STRAIGHT_LINE_ESTIMATE`를 내고 `_applied_travel_route()`가 그걸
    걸러내므로, fake 그대로 두면 전환 로직을 타고도 결과가 하나도 바뀌지 않는다.
  - 실 API로 전/후 상위 5곳 집합 비교(안국역·홍대입구역 x 14시·19시).

- 남은 것:
  - **도보 엔드포인트(`dapi.kakao.com/v2/routing/walk`) 한도를 확인해야 한다.**
    2026-09-02 측정 중 도보만 `API limit has been exceeded.`로 막혔고, 같은 키·같은
    호스트의 대중교통은 정상이었다. 시간이 지난 뒤 단건 호출도 같았다. 한 번의 추천이
    도보를 10건씩 부르므로, 한도가 작다면 실측이 조용히 전부 버려진다
    (`_consistent_routes()`가 전체를 직선거리로 내린다).
  - **관측이 턴 단위로 어긋난다.** `summarize_fanout()`과
    `record_score("route_measured_ratio")`가 쿼리 하나 단위라, 양쪽 조회면 한 턴에
    span 2개와 같은 이름의 score 2개가 찍힌다.
  - `_TRAVEL_MODE_PHRASES`에 `TRANSIT` 문구가 없다. 지금은 대중교통으로 재면 근거
    문장만 직선거리로 돌아가 카드 수치와 어긋난다.
  - 프론트가 카드마다 다른 `travel_mode`를 표시할 수 있는지 확인이 필요하다.

- 상세: `backend/app/services/runtime/recommendation_transform.py`,
  `backend/app/services/runtime/agent_runtime.py`, `backend/app/domain/scoring.py`,
  `backend/app/domain/explanation.py`, `backend/app/providers/factory.py`

### D-119 — 구 이름은 반경이 아니라 그 구 전체를 후보로 삼는다

- 상태: `Proposed` — D 리뷰 대기. 후보 수집·선택은 C가 구현했고, 거리 축 재정규화만 D가 맡는다.
- 배경:
  - 위치 해석이 돌려주는 것은 언제나 좌표 한 쌍이라(`ResolvedLocation`에 범위 필드가
    없다) "강남구"도 대표점 하나로 풀린 뒤 반경 검색에 들어간다. 반경 2km 원은
    12.6km²인데 강남구는 39.5km², 서초구는 47.0km²다.
  - 게다가 실제 후보는 반경 안 전부가 아니라 거리순 상위 N곳이다. 안국역 실측으로
    후보 10곳이 반경 147m, 40곳이 206m 안이었다. 후보 30곳이면 300~400m 수준이라
    **"강남구에서 추천해줘"의 실제 선택지가 구 대표점 주변 수백 미터**가 된다.
  - 결과가 그대로 드러났다(2026-09-01, Supabase 전량). 마포구는 후보 30곳 중
    25곳이 쇼핑이고 전부 홍대 매장이었다(웍스아웃·반스·올리브영·헤메코). 강남구는
    관광지가 1곳이었다.
  - 동 단위는 이미 동작한다(실측 8건 중 6건 정상). 문제가 되는 것은 구 단위뿐이다.

- 결정 1 — **구 이름이면 Supabase `places`를 구 코드로 읽어 구 전량을 후보
  모집단으로 삼는다.** 반경 개념이 사라지고 외부 호출도 늘지 않는다.

  검토한 두 갈래는 기각했다.

  - *구 안에 대표점을 여러 개 두고 각각 반경 검색* — 거리 점수를 살리려고 기준점을
    늘리는 방식이다. 결정 2로 거리를 쓰지 않기로 한 이상 TourAPI 호출만 대표점
    수만큼 늘어난다.
  - *반경만 구 크기에 맞춰 키우기* — 후보가 거리순 상위 30곳이라 넓힌 반경 대부분이
    후보에 잡히지 않는다.

  D-025("요청은 시도까지만 좁히고 응답의 `lDongSignguCd`로 거른다")와 충돌하지
  않는다. 그 결정은 **TourAPI 반경 검색**을 전제로 한 것이고 — 구를 요청에 실으면
  반경 안의 옆 구 후보가 잘리고 구마다 호출해야 한다는 근거였다 — 이번은 저장소
  조회라 두 근거가 모두 성립하지 않는다. 반경 경로는 지금까지대로 둔다.

- 결정 2 — **구 단위 요청은 거리 점수를 쓰지 않는다.** 기준점이 없기 때문이다.
  구 이름을 푼 대표점은 후보 수집에도 정렬에도 쓰이지 않으므로 그 좌표와의 거리는
  사용자가 말한 것과 아무 관계가 없다.

  C는 판정이 아니라 사실만 싣는다(D-051) — `RecommendationContext.district_scope`가
  "이 후보들을 어느 구 전체에서 모았다"를 전한다. 거리 축을 어떻게 뺄지는 D가 정한다.

  **키만 빼면 안 된다.** `distance`는 켜고 끄는 선택 Feature가 아니라 어느 실행에서나
  채점되는 기본 3축이라(`_BASE_WEIGHTS`), 빼면 가중치 합이 0.80이 되어 합 1.0 전제가
  깨진다. `redistribute_weights()`가 그 계산을 하지만 그 함수는 **결측**(측정 실패)용이고
  `build_weights()`가 "켜지지 않은 Feature는 결측이 아니라 존재하지 않는 Feature"로
  두 개념을 갈라놓았다. 구 단위 모드를 어느 쪽으로 볼지가 D의 결정이다. 딸린 확인:
  `_MAX_OPTIONAL_FEATURES = 3`은 "distance(0.20)가 0.05씩 내놓으므로 4개째에서 0"이라는
  계산에서 나온 값이라 distance가 빠지는 모드에서는 성립하지 않는다.

- 결정 3 — **후보 선택은 분류 몫으로 개수를 정하고 격자로 어디서 채울지 정한다.**
  선택 로직은 `app/agent_context/district_selection.py`에 순수 함수로 둔다.

  ```
  관광지 7  문화시설 7  음식점 6  쇼핑 6  레포츠 2  축제공연 2   (합 30)
  ```

  다섯 전략을 견줘 고른 것이다(`칸`은 4x4 격자 중 몇 칸에 흩어졌는지).

  | 전략 | 강남구 칸 | 강남구 분류 구성 | 마포구 칸 | 마포구 분류 구성 |
  | --- | --- | --- | --- | --- |
  | 거리순(현행) | 4/16 | 관1 문4 음16 쇼9 | 1/16 | 관2 음2 레1 쇼25 |
  | 무작위 | 7/16 | 관2 문1 음9 축1 쇼17 | 8/16 | 관3 문1 음8 쇼18 |
  | 분류 균형 | 8/16 | 5·5·5·5·5·5 | 8/16 | 5·5·5·5·5·5 |
  | 격자 분산 | 12/16 | 관7 문2 음10 쇼11 | 11/16 | 관5 문1 음10 축2 쇼12 |
  | **격자+분류** | **12/16** | **몫 그대로** | **11/16** | **몫 그대로** |

  - **무작위는 쓸 수 없다.** 칸은 흩어지지만 원본 분포를 그대로 따라가 쇼핑이
    17~18곳이 된다. 강남구 쇼핑 713곳의 실체는 성형외과·약국·브랜드 매장이다
    (`SH040300` 한 중분류에 697곳).
  - **두 축을 합쳐도 서로 손해 보지 않는다.** 칸 수는 격자만 썼을 때와 같고 분류
    구성은 몫만 썼을 때와 같다.
  - **격자는 칸마다 할당량을 주는 방식이 아니다.** 개수를 정하는 것은 언제나 분류
    몫이고 격자는 그 몫을 어느 동네에서 채울지만 정한다. 칸별 할당으로 하면 채울 수
    없는 칸(장소가 1곳뿐인 칸이 실재한다)의 몫을 어디로 보낼지 또 정해야 하고,
    무엇보다 그 칸에 쇼핑밖에 없으면 분류 균형이 깨진다.
  - 균등하게 5씩 주지 않은 이유는 희소 분류 과대표집이다 — 강남구 레포츠는 전체
    6곳인데 5곳이 뽑혀 세븐럭카지노·청소년센터까지 올라온다.
  - **사용자가 분류를 말했으면 몫도 소진율 상한도 걸지 않는다.** "강남구 카페"에
    관광지 7곳을 넣으면 요청을 무시하는 것이 된다. 그때는 격자 분산만 적용한다.

- 결정 4 — **한 분류에서 그 구가 가진 것의 60%를 넘게 쓰지 않고, 쇼핑은 그와 별개로
  6을 절대 넘지 않는다.**

  상한이 없으면 얇은 구가 바닥을 긁는다 — 25개 구 전량 검증에서 소진율 50% 이상이
  40건이었고 금천구 문화시설 7/7, 동작구 8/8, 강동구 7/7이 100% 소진이었다.

  쇼핑 절대 상한이 따로 필요한 이유는 **소진율 상한만 걸면 다른 분류에서 넘친 몫이
  전부 쇼핑으로 흘러가기 때문이다.** 쇼핑만 모든 구에서 두껍다(금천 224·구로
  135·관악 115). 상한 50%에서 금천구가 쇼핑 18곳, 60%에서 15곳이었다.

- 결정 5 — **모자라면 모자란 대로 넘긴다.** 25개 구 중 셋이 30을 못 채운다
  (중랑 29·도봉 28·금천 21).

  금천구가 21곳인 것은 이 규칙의 결함이 아니라 데이터가 그만큼이기 때문이다 —
  251곳 중 224곳이 쇼핑이라 여행 추천에 쓸 만한 장소가 27곳뿐이다. 억지로 30을
  맞추면 쇼핑 15곳이 들어간다.

- 결정 6 — **"더 보기"는 기존 배선(`excluded_place_ids`)을 쓰고 규칙 셋을 얹는다.**

  - *소진율 상한은 턴 누적으로 잰다.* 턴마다 초기화하면 1턴에 60%, 2턴에 남은 것의
    60%를 또 써서 누적 84%가 되고 상한의 뜻이 사라진다. 따로 저장할 필요는 없다 —
    분모(구 전량)는 고정이고 이미 보여준 것은 제외 목록으로 넘어온다.
  - *넘기기는 첫 턴에만 허용한다.* 더 보기 턴에서도 허용하면 3~4턴째에 한 분류가
    목록을 먹는다(강남구 4턴 문화시설 17곳, 마포구 4턴 음식점 24곳).
  - *이번 턴 후보가 15곳 미만이면 소진으로 끝낸다.* 없으면 금천구가 쇼핑 6곳을
    무한히 내보낸다. 10이 아니라 15인 것은 후반 턴이 "음식점 6 + 쇼핑 6"으로 수렴해
    쇼핑 비중이 20%에서 50%로 오르는 구간을 잘라내기 위해서다.

  세 규칙을 적용한 턴별 후보 수: 종로구 30·30·29·28·28…(유지), 강남구
  30·29·28·22·19·18 → 7턴에 소진, 마포구 30·30·24·15 → 5턴에 소진, 도봉구·금천구는
  2턴에 소진.

- 결정 7 — **좌표가 깨진 장소는 폴리곤이 아니라 서울 bbox로 거른다.**

  활성 8,007곳 중 12건이 서울 밖 좌표다. 10건이 `(19.69442748, 117.9925662504)`라는
  같은 값(남중국해)을 갖고 있고, 삼각산은 경기 광주에, 한 건은 위도 칸이 비고 경도
  칸에 위도가 들어 있다. 반경 검색에서는 어떤 중심점에서도 안 걸려 드러나지 않았고,
  구 전량 조회가 그 전제를 깨는 첫 경로다(첫 측정에서 대치유수지체육공원이 강남구
  후보 1번으로 뽑혔다).

  | 기준 | 깨진 좌표를 걸러냄 | 정상 장소를 함께 잃음 |
  | --- | --- | --- |
  | bbox | 12 / 12 | **0곳** |
  | 서울 폴리곤 | 12 / 12 | 4곳 (0.05%) |
  | 자기 구 폴리곤 | 12 / 12 | 82곳 (1.02%) |

  걸러내는 성능이 셋 다 같으므로 차이는 정상 장소를 얼마나 잃느냐뿐이다. 폴리곤이
  잃는 4곳은 아차산성·망우산·옛골토성 청계산점·서울둘레길 5코스로, 경기도와 맞닿은
  산이라 좌표가 경계 밖으로 조금 넘어간 것들이다 — 넷 다 관광지 유형인데 그 유형이
  구마다 귀하다. "자기 구 안"이 잃는 82곳 중 72곳은 용산구로 등록됐지만 좌표는 중구
  안인 서울역 부속 시설이고, `providers/mappers.py`가 이미 같은 이유로 좌표 대신
  응답의 구를 믿는다.

  이 판정의 목적이 "이 좌표가 어느 구인가"가 아니라 **"이 좌표가 아예 말이 안
  되는가"**라서다. 지원 지역 판정(`is_within_service_area`)은 지금대로 폴리곤을 쓴다.
  한계는 분명하다 — 서울 안쪽에 잘못 찍힌 좌표는 bbox로 못 잡는다.

- 곁들여 고친 것:
  - **Fake 지오코더가 구 이름을 풀게 했다.** 실제 Naver Geocoding은 "서울특별시
    강남구"를 인식하는데 Fake만 못 풀어 구 단위 경로가 로컬·테스트에서 아예 돌지
    않았다. 좌표는 지어내지 않고 경계 파일에서 대표점을 계산한다
    (`district_representative_point()`). 무게중심이 오목한 구에서 밖으로 나가면 그
    위도의 가장 넓은 내부 구간 가운데를 쓴다 — 25개 구 전부 자기 구 안에 들어온다.
  - **브라우저 허용 출처를 설정으로 뺐다**(`CORS_ALLOW_ORIGINS`). 기본값은 지금까지와
    같은 `http://localhost:5173`이다. 워킹트리를 둘 띄우면 두 번째 프론트엔드가 다른
    포트로 떠서 교차 출처가 되는데, 백엔드는 정상이고 curl도 200을 주는데 화면만
    "서버에 연결할 수 없다"가 되어 원인을 찾기 어렵다.

- 검증:
  - 선택 규칙 단위 테스트 13건(`test_district_selection.py`). 기대값은 지어내지 않고
    실측 구성을 그대로 넣었다 — 강남구 구성이면 30곳에 7·7·6·6·2·2, 금천구 구성이면
    21곳에 관6 문4 음4 쇼6 축1이다. 금천구 21이 특히 값어치 있다: 소진율 상한·쇼핑
    절대 상한·모자란 대로 넘기기가 **동시에** 걸려야만 나오는 수라 셋 중 하나가
    깨지면 잡힌다.
  - 배선 테스트 5건(`test_district_context.py`). Fake 구는 쇼핑이 90곳으로 압도적이라
    선택 규칙이 안 돌면 그 비율이 결과에 그대로 나온다 — 쇼핑이 6곳으로 눌려 있는지를
    본다(조용한 fake 방지). Provider가 없을 때 반경 검색으로 되돌아가지 않는 것도
    함께 못 박았다.
  - 실 API 스모크(2026-09-03): "강남구에서 갈 만한 곳 추천해줘"가 후보 출처
    `supabase_places`, 후보 30곳으로 돌아 청담근린공원·도산공원·강남청소년센터·
    봉은사·청담패션거리가 나왔다.

- 남은 것:
  - **거리 축 재정규화(D 소유)가 없으면 절반만 동작한다.** 스모크에서 `weights_used`가
    여전히 `distance: 0.2`였고 결과 5곳이 전부 중심점 1.3km 안이었다 — 후보는 구
    전역에서 오는데 채점이 다시 좁힌다.
  - 분류 조건이 붙은 구 요청("강남구 카페")이 실제로 좁혀지는지 테스트가 없다.
  - 끝난 축제가 후보로 남는 문제(TP-201)는 이 결정과 별개로 축제 몫 2에 영향을 준다.

- 상세: `backend/app/agent_context/district_selection.py`,
  `backend/app/agent_context/service.py`, `backend/app/agent_context/schemas.py`,
  `backend/app/tools/nearby_place_details.py`,
  `backend/app/providers/supabase_district_search.py`,
  `backend/app/repositories/supabase_places.py`, `backend/app/service_area.py`

### D-120 — 축제공연행사는 추천 후보에서 뺀다

- 상태: `Accepted` — C 단독. 행사 조회(INFO)는 그대로 두고 추천 후보에서만 뺀다.
- 배경:
  - `places`에 행사 시작일·종료일 컬럼이 없다. place-sync가 `detailIntro2`를 부르면서
    `eventstartdate`/`eventenddate`를 받지 않아, 축제의 `operating_schedule`에는 하루
    영업시간(`playtime`)만 들어간다.
  - `domain/scoring.py::_is_closed()`가 그 스케줄만 보므로 요일·월 제한이 없는
    10:00~18:00 축제는 오늘 14시에 "영업 중"이 된다. **행사가 작년에 끝났다는 사실이
    이 판정에 들어갈 자리가 없어** 하드 필터를 그대로 통과한다.
  - 규모(2026-09-03·04 실측). 활성 축제 189건 중 그날 진행 중인 것은 17건이고
    172건(91%)이 아니었다. 반경 2km 무분류 조회를 네 지점(경복궁·명동·홍대입구·
    강남역)에서 돌리자 걸린 축제 10건이 **전부** 종료된 행사였다 — 가장 늦게 끝난 것이
    2025-12-23이다. TP-201은 제목에 지난 연도가 박힌 12건을 하한으로 잡았는데 실제로는
    그 14배였다.

- 결정 1 — **`_UNSUPPORTED_CONTENT_TYPE_IDS`에 `"15"`를 넣는다.**

  `resolve_place_category()`를 네 경로가 공유하므로 한 곳으로 전부 걸린다 — 반경
  검색(TourAPI `locationBasedList2`), 구 단위 검색, 무장애 검색, fake. 규칙을 한 곳에
  두는 것은 이 함수가 원래 노린 설계이고, fake가 같은 집합을 보므로 테스트에서 확인한
  동작이 실 경로에서 재현된다.

  반경 경로는 미지원 분류 필터가 TourAPI의 `numOfRows` **다음에** 걸려 후보가 줄어드는데,
  `nearby_place_details.py`가 이미 필요분의 3배를 요청해 최저 생존율 40%(홍대입구)까지
  덮고 있다. 축제는 활성 8,007곳 중 189곳(2.4%)이라 숙박(88건/종로구 스냅샷)보다 폭이
  작다.

- 결정 2 — **구 단위 분류 몫에서 축제 2자리를 관광지·문화시설에 1씩 얹는다**(7·7 → 8·8).

  몫 합 30을 유지해야 하고, 둘은 D-119가 이미 가장 두껍게 두기로 한 분류다. 모집단도
  충분해(강남 39·69, 금천 10·7) 몫을 늘려도 소진율 상한(60%)에 먼저 걸린다 — 금천구
  관광지는 몫이 8이어도 6곳에서 멈춘다.

- 기각 — **기간 컬럼을 만들어 거르는 안.**

  `detailIntro2`가 축제 기간을 실제로 돌려주는 것은 확인했다(2026-09-03, 축제 3건 조회:
  응답 키 22개에 `eventstartdate`/`eventenddate`가 있고 `playtime` 값이
  `places.operating_hours_raw`와 정확히 일치). 즉 **place-sync가 이미 받는 응답에서 두
  키를 안 읽고 버리는 중**이라, 새 오퍼레이션 없이 컬럼을 채울 수 있다.

  그런데도 기각한 것은 균형 때문이다. 후보의 91%가 유효하지 않은데 그 유형을 살리려고
  마이그레이션 + place-sync + 저장소 5곳 + 딕셔너리 + 픽스처를 늘리는 값어치가 지금은
  없다. 축제를 추천에 다시 들이기로 하면 이 길은 그대로 열려 있다.

- 근거 — **사용자가 행사를 못 보게 되는 것은 아니다.** 행사 발화는 INFO의 두 갈래가
  기간을 보고 답한다. `event`는 `searchFestival2`(응답 기간으로 `is_ongoing()` 판정),
  `realtime_event`는 서울시 실시간 도시데이터(원본이 진행 중인 것만 내려준다)다.

  반경 경로가 만날 수 있는 축제는 전부 `searchFestival2`에도 있다 — 서울 전역을
  `contentTypeId=15`로 훑어 56건을 얻었고 56건 모두 `searchFestival2` 193건 안에 있었다
  (2026-09-04). 즉 이 결정으로 경로가 바뀔 뿐 없어지지 않는다.

- 결정 3 — **행사 어휘는 RECOMMEND가 아니라 INFO로 보낸다**(TP-237, 같은 PR).

  제외만 하면 "축제 추천해줘"가 이유 없는 빈 결과가 된다. 추천 프롬프트에 축제 설명이
  없는데도 모델이 `place_types=['festival']`을 내기 때문이다 — 구조화 출력 스키마
  (`LLMOutput` → `PlaceType`/`PlaceTag` 열거형)가 허용 값으로 제시하므로 프롬프트에
  없어도 정상 동작이다. 2026-09-04 Gemini 실호출에서 축제·전시회·콘서트·공연 발화 6건이
  전부 RECOMMEND로 갔다.

  가른 기준은 **찾는 대상이 장소냐 행사냐**다. TourAPI가 이미 그 선을 긋고 있다 —
  공연장(VE060100)·전시관(VE070300)·미술관(VE070600)·박물관(VE070100)은 문화시설(14)이고,
  그 안에서 열리는 것이 15다. 그래서 "미술관 추천해줘"는 RECOMMEND로 남고 "전시회
  추천해줘"는 INFO로 간다.

  슬롯 셋을 함께 올렸다. 하나만 바꾸면 여전히 빈 결과다 — 라우터가 보내줘도 info.extract가
  `question_type`을 못 뽑으면 실패한다(변경 전 실측: "추천해줘"가 붙은 6발화 전부
  `question_type=None`).

  - `router.classify` 2.4.0 → 2.5.0 (**A 소유**, @kiminlim 리뷰)
  - `info.extract` 3.4.0 → 3.5.0 (C 소유)
  - `recommend.extract` 2.7.0 → 2.8.0 (**D 소유**, @rayquaza410 리뷰) — 라우터가 놓친
    경우의 안전망

  실측 13발화가 행사 7건 INFO(`realtime_event`) · 장소 6건 RECOMMEND로 갈린다. 지명이
  없는 발화는 `place_name`이 비어 되묻기로 간다 — 어느 지역 행사인지 모르면 답할 수 없다.

- 결정 4 — **`realtime_event`가 비면 TourAPI(`event`)로 한 번 더 본다.**

  두 출처가 거의 겹치지 않기 때문이다. 2026-09-04에 그날 진행 중인 행사가 서울시 실시간
  95건·TourAPI 21건인데 **양쪽에 다 있는 것은 3건**이었다. 담는 것도 다르다 — 서울시는
  미술관 기획전·문화재단 프로그램, TourAPI는 왕궁수문장 교대의식·한강야경투어 같은 관광
  콘텐츠다. 서울시가 비었다고 "행사가 없다"고 답하면 TourAPI에 있는 것을 못 본 채로 끝난다.

  서울시가 지원하지 않는 지역(121개 목록 밖)도 같은 경로로 넘긴다. TourAPI까지 비면
  그대로 `no_data`다. 주차·지하철 같은 다른 realtime 유형에는 두 번째 출처가 없어 이
  분기가 없다.

- 남은 것:
  - `places`에만 있고 두 API 어디에도 없는 축제가 3건이다(DDP 루프탑투어·용산 국가유산
    야행·세계유산 조선왕릉축전). TourAPI가 더는 안 주는 콘텐츠를 들고 있는 것이라 이
    결정과 별개로 적재 범위 문제다 — `places` 활성 축제 189건인데 `locationBasedList2`로
    닿는 것은 56건뿐인 것도 같은 계열의 의문이다.
  - `PlaceType.FESTIVAL`(`schemas.py`)과 `category_rules.PLACE_TYPE_TO_CONTENT_TYPE_ID`의
    축제는 A 소유 계약이라 그대로 뒀다. 라우터가 놓쳐 RECOMMEND로 새면 여전히 후보가
    0건이므로 `recommend/place_tag_rules.md`의 규칙이 안전망이다.
  - 프롬프트 회귀는 돌렸다(2026-09-04). 단일 턴 `evaluate_info_question_type --repeat 5`가
    30건 전부 정확·전부 안정이고(새 `realtime_event` 5건 포함), 다중 턴
    `evaluate_agent_quality --split dev`는 Intent 94.0% · Macro F1 0.972 · 조건 96.6%로
    **`RECOMMEND × INFO` 칸이 0을 유지했다.** 실패 2건은 MODIFY 축이라 이 결정이 가른
    경계가 아니다 — 7회씩 다시 돌려 DEV-025는 재현되지 않았고 DEV-030 첫 턴이
    RECOMMEND 4 · MODIFY 3으로 갈리는 원래 흔들리는 케이스였다. `--split final`은
    프롬프트 확정 후 1회만 돌린다.
  - **레포 프롬프트는 Langfuse에 올려야 실제로 돈다.** 런타임이 Langfuse에서 읽으므로
    (`prompts/registry.py`) 이 PR의 프롬프트 변경만으로는 운영 동작이 바뀌지 않는다.
    위 실측은 프롬프트 관리를 끄고 레포 프롬프트로 잰 값이다.
  - 행사 조회(`event`)가 서울 전역 193건을 `numOfRows=100` 한 번만 불러 진행 중 21건 중
    11건을 못 본다(페이징 없음). 이 결정과 별개 문제로 남긴다.

- 상세: `backend/app/providers/mappers.py`,
  `backend/app/agent_context/district_selection.py`

### D-122 — 서울시 실시간 카드에 인구 수 세로축과 인구·상권 요약을 함께 싣는다

- 상태: `Accepted` — 구현·테스트 완료.
- 배경: 혼잡도 예측 그래프가 막대 높이를 실제 인구가 아니라 단계별 고정값
  (`CONGESTION_HEIGHT`: 여유=25, 보통=45…)에서 뽑아 그렸다. 같은 "붐빔"이면 3천
  명이든 8만 명이든 높이가 같아서, 그래프가 색만 다른 네 칸짜리 그림에 가까웠다.
  사용자가 서울시 앱처럼 세로축에 인구 수를 달아 달라고 요청했고, 함께 실시간
  인구·연령대·상권 매출·결제 상위 업종도 보여 달라고 했다.
  D-090은 현재 인구 실측치(`AREA_PPLTN_MIN/MAX`)를 "참고 이미지에 없는 값"이라며
  범위에서 뺐는데, 이번에 실제 `citydata` 응답을 다시 열어보니 요청받은 항목이
  **하나만 빼고 전부 이미 받아오는 응답 안에 있었다**(2026-09-05 강남역·동대문·
  경복궁 실측). 새 API 연동 없이 파싱만 추가하면 되는 일이었다.
- 결정:
  1. **버려지던 필드를 파싱한다.** 인구는 `AREA_PPLTN_MIN/MAX`(현재 인구 구간)와
     `PPLTN_RATE_0`~`_70`(연령대 여덟 구간), 상권은 `AREA_SH_PAYMENT_CNT` /
     `AREA_SH_PAYMENT_AMT_MIN/MAX`(최근 10분 결제 건수·금액)와 업종별
     `RSB_SH_PAYMENT_*`다. 예측 구간의 인구 수(`FCST_PPLTN_MIN/MAX`)는 D-090
     시점부터 이미 프론트까지 내려가고 있었고 그래프만 안 쓰고 있었다.
  2. **그래프에 세로축을 세운다.** 막대 높이를 구간 중간값 기준 실제 비율로 바꾸고,
     눈금이 네 칸으로 떨어지도록 상한을 올린다(최대 7.9만 → 0~8만을 2만 간격).
     단, **막대 하나라도 인구 수가 비면 눈금을 세우지 않고 기존 단계 높이로
     돌아간다** — 일부만 실측치이고 나머지는 단계로 어림한 높이인데 눈금이 붙으면
     전부 실측치처럼 읽힌다. 인구 수는 구간으로만 오므로 높이 계산에만 중간값을
     쓰고 화면에는 `78,000~80,000명`처럼 범위를 그대로 보여준다.
  3. **인구·상권 요약 블록을 새로 만든다**(`SeoulRealtimeSummarySection`). 실시간
     인구 구간·가장 붐빌 시간대·가장 많은 연령대, 그리고 최근 10분 결제 금액·상권
     활동·결제 금액 Top 3 업종을 카드와 상세 모달 양쪽에 싣는다.
  4. **모든 INFO가 아니라 `concentration`·`realtime_commercial` 두 유형에만 붙인다**
     (사용자 결정). 이 둘만 서울시 `citydata`를 이미 호출하므로 **추가 호출이 0**이다.
     두 경로가 받아오는 응답은 같은 한 덩이라, 인구 질문 카드에도 상권 값을,
     상권 질문 카드에도 인구 값을 추가 조회 없이 실을 수 있다.
  5. **결제 Top 3는 서울시 원문 업종을 거르지 않는다**(사용자 결정). 강남역은 금액
     1위가 "의료 · 병원"으로 나오는데, 여행지 카드에 안 어울린다고 빼면 우리가 만든
     순위를 서울시 데이터인 것처럼 보여주게 된다. 금액이 구간이라 상한 기준으로
     정렬하고, 상한이 없으면 하한으로 대신한다.
  6. **"가장 붐빌 시간대" 타일은 지금이 이미 예측 피크만큼 붐비면 비운다.** 실측에서
     강남역이 현재 붐빔·예측 최고 약간 붐빔이었는데, 그대로 두면 지금보다 덜 붐비는
     시각을 "가장 붐빌 시간대"라고 보여준다. 이 가드는 새 타일에만 걸었고 기존
     `population_peak_forecast_summary` 문장은 회귀 테스트가 문자열을 잠그고 있어
     건드리지 않았다.
  7. **상권 구획은 없으면 통째로 감춘다.** 서울시가 인구는 121곳, 상권은 82곳에만
     제공한다(D-084). 경복궁을 실제로 호출하면 `LIVE_CMRCL_STTS`가 통째로 `null`이다
     — 이때 인구 구획만 뜨고 상권 구획은 사라진다.
  8. **(후속) 새로 붙인 값들이 안 보인다는 지적을 받고 대비를 올렸다.** 옅은 회색
     캡션으로 두던 단계(`약간 붐빔`·`분주한`)를 색 칩으로 바꾸고, `1위`처럼 글자로만
     쓰던 순위를 번호 뱃지로(1위만 채운 브랜드색) 바꿨다. 칩 색 사다리는 인구 4단계와
     상권 4단계가 낱말만 다르고 읽는 방향이 같아 한 표를 공유한다(`한산한`→`여유`,
     `분주한`→`붐빔` 자리). 서울시 원문이 "바쁜 시간대"처럼 접미사를 붙여 오기도 해서
     접미사를 떼고 매칭한다. 막대 팔레트도 함께 손봤다 — `amber-400`/`orange-500`을
     쓰고 있었는데 토큰 팔레트가 `amber-500`과 `orange-500`을 **둘 다 gold(#e0a83e)로
     재정의**해서 가운데 두 단계가 사실상 같은 색이었다(옅은 앰버 300 / 진한 앰버 500으로
     간격을 벌렸다).
  9. **(후속) 실제로 렌더링해 보고 드러난 것 셋을 고쳤다.** (a) 격자선 레이어가
     `absolute`인데 막대 행이 `static`이라 격자선이 막대 **위로** 그려지고 있었다
     (막대 행도 `relative`로), (b) "현재" 강조 테두리를 칸(track)에 걸어서 축이 선
     뒤에는 테두리가 최고 눈금까지 올라가 실제보다 많아 보였다(막대 자체에 걸도록),
     (c) 눈금이 서면 칸 배경 tint를 지운다 — 막대 위에 옅은 색 블록이 남으면 격자선과
     겹쳐 "저기까지 찼다"고 읽힌다. 인구 값도 좁은 타일에서 `30,000~32...`로 잘려서
     만 단위로 접었다(`3~3.2만명`) — 바로 위 세로축이 같은 만 단위라 읽는 방식이
     어긋나지 않는다.
  10. **(후속) 막대 높이와 색이 어긋날 수 있다는 걸 범례로 밝힌다.** 사용자가 "현재가
     약간 붐빔인데 왜 예측치의 붐빔 막대보다 더 기냐"고 물어 실측으로 확인한 결과
     **서울시 데이터가 실제로 그렇다** — 현재 단계(`AREA_CONGEST_LVL`)와 예측 단계
     (`FCST_CONGEST_LVL`)를 따로 산출해서, 단계는 더 높은데 인구 수는 더 적은 구간이
     나온다(2026-09-05 8개 지역 실측 중 난지한강공원 `약간 붐빔 3,250명` vs 18시
     `붐빔 2,750명`, 동대문 관광특구 2건). 높이를 단계에 맞춰 보정하지 않는다 — 그러면
     세로축 숫자가 거짓이 된다. 대신 "막대 높이는 인구 수, 색은 서울시 혼잡도 단계예요
     — 두 값을 따로 산출해 가끔 어긋나요" 한 줄을 눈금이 설 때만 붙인다.
  11. **(후속) 상권 금액의 기준을 화면에 밝힌다.** "이 금액이 뭘 말하는지
     모르겠다"는 지적. 지역 총액 타일만 "최근 10분"이라 적혀 있고 업종 Top 3에는
     기준이 없어서, 같은 창의 내역인지 하루 누계인지 알 수 없었다. 업종 행이 지역
     총액의 내역이 맞는지 실측으로 확인했다 — **결제 건수가 정확히 일치한다**
     (2026-09-05 17:40 강남역 328=328, 동대문 관광특구 108=108, 용리단길 39=39;
     금액 합계만 각 업종 하한의 합이라 조금 낮다). 그래서 제목을 "최근 10분 매출
     Top N 업종"으로, 총액 라벨을 서울시 앱과 같은 "최근 10분 매출 총액"으로 바꿨다.
     "신한카드 내국인 결제 기준" 안내도 구획 맨 아래에서 제목 바로 밑으로 올렸다 —
     숫자를 읽기 전에 보여야 기준을 모른 채 금액부터 읽는 일이 없다.
  12. **(후속) 서울시 지도 페이지와 값을 대조하고, 과거 12시간은 계속 안 넣기로 했다**
     (사용자 결정: "B안"). 노량진으로 맞춰보니 **현재 슬롯과 예측 12슬롯이 서울시
     페이지와 값·단계까지 그대로 같았다**(13개 중 12개 정확히 일치, 남은 1개는 두
     응답을 몇 분 차이로 받아 예측이 한 칸 갱신된 것). 우리에게 없는 건 왼쪽 절반뿐이다.
     그 페이지의 과거 12시간은 공개 API가 아니라 포털 내부 엔드포인트에서 온다 —
     `GET data.seoul.go.kr/SeoulRtd/api/ppltn_congest?hotspotNm=...`가 25슬롯(과거 12 +
     현재 + 예측 12)과 28일 동요일 평균 계열까지 한 번에 준다(45ms, 121곳 전부 응답).
     **쓰지 않기로 했다** — 인증키도 문서도 없고 **Referer 없이 부르면 302로 튕긴다**.
     브라우저 밖에서 쓰지 말라는 명시적 신호라, 베타로 나가는 기능의 기반으로 삼을 수
     없다. 직접 쌓는 안(D-090의 "새 파이프라인" 보류 건)은 이제 폴링 없이도 가능하다는
     걸 확인했다 — 혼잡도·상권 질문마다 이미 `citydata`를 부르고 그 응답에 현재 인구가
     있으니 **추가 호출 0회로 적재만 하면 된다**. 다만 콜드스타트가 남아 이번에는
     보류하고, 대신 **막대 말풍선**을 넣었다(아래).
  13. **(후속) 막대에 커서를 올리면 시각·혼잡도·인구수를 말풍선으로 보여준다.**
     브라우저 기본 `title` 속성을 쓰고 있었는데 뜨는 데 1초쯤 걸리고 꾸밀 수도 없다.
     `role="tooltip"` 말풍선으로 바꾸고, 좁은 타일과 달리 여기서는 자릿수를 접지 않는다
     (`formatPopulationRange`는 자릿수 그대로, 타일용은 `formatPopulationRangeCompact`로
     갈랐다). hover가 없는 터치 기기를 위해 탭으로도 열리고, 막대를 `<button>`으로 바꿔
     키보드 포커스로도 열린다 — 커서를 못 쓰는 사용자를 위해 같은 내용을 `aria-label`로도
     붙였다. 곁가지로 시각 라벨을 세 칸에 하나만 적게 솎았다. 12슬롯을 다 적으면 칸 폭보다
     글자가 길어 `20…`으로 잘렸는데, 정확한 시각은 이제 말풍선이 알려준다.
- 검증: 실 API로 강남역(상권 있음)·경복궁(상권 없음) 양쪽을 매퍼→요약→카드까지
  통과시켜 확인했다 — 강남역은 12/12 예측 슬롯에 인구 수가 있어 세로축이 서고,
  경복궁은 상권 구획이 사라진다. 백엔드 `pytest` 4,091 passed·37 skipped,
  `ruff check` 클린. 프론트 `vitest` 65개 파일 552건 통과(요약 블록 6건·세로축 4건·
  단계 칩 3건 신규), `tsc --noEmit`·`eslint`(신규 경고 없음)·`vite build` 클린.
  시각 확인은 빌드된 CSS로 컴포넌트를 렌더해 Playwright 스크린샷으로 했다 — 위
  9번의 세 가지는 테스트가 아니라 이 스크린샷에서 드러났다.
- 채택하지 않은 것:
  - **모든 INFO 답변에 공통 템플릿으로 붙이는 안**(원안). 나머지 INFO는 서울시
    데이터를 아예 조회하지 않아 INFO 턴마다 호출이 한 번씩 늘어난다. 응답은 140~180ms로
    가볍고 지역 코드가 121개로 고정이라 TTL 캐시로 감당할 수 있다고 봤지만, 사용자가
    "우선 추가 호출 없는 범위에서"로 정했다. 넓히려면 지역 코드별 짧은 TTL 캐시를
    함께 넣는 게 전제다.
  - **"오늘의 인기 시간대"**(참고 이미지에 있던 항목). 서울시 API가 미래 12시간
    예측만 주고 과거 추이를 주지 않는다 — D-090에서 확인한 사실을 이번에 재확인했다.
    대신 지금 가진 예측만으로 정직하게 말할 수 있는 "가장 붐빌 시간대"로 이름을 바꿔
    달았다.
- 상세: `backend/app/providers/seoul_citydata.py`, `backend/app/domain/models.py`,
  `backend/app/agent_context/info_schemas.py`, `backend/app/agent_context/service.py`,
  `backend/app/schemas.py`, `backend/app/services/runtime/info_response_transform.py`,
  `frontend/src/utils/seoulRealtimeDisplay.ts`(신규),
  `frontend/src/components/chat/SeoulRealtimeSummarySection.tsx`(신규),
  `frontend/src/components/chat/CongestionForecastBars.tsx`,
  `frontend/src/components/chat/PlaceInfoCard.tsx`,
  `frontend/src/components/chat/RecommendationDetailPreviewModal.tsx`,
  `frontend/src/types.ts`

### D-121 — 근처 공중화장실은 별도 유형으로 적재해 답한다

- 상태: `Accepted` — 구현·테스트 완료. 마이그레이션 적용과 첫 적재 실행이 남아 있다.
- 배경: "급한데 근처에 화장실 있어?"를 기존 `facility`가 받고 있었다. 그런데
  `facility`가 답하는 것은 그 관광지의 편의시설 안내문("화장실: 있음")이고, 이
  질문이 알고 싶은 것은 **어디로 가면 되는지**다. 답할 데이터가 없는 질문을 답할 수
  있는 질문처럼 처리하고 있었다 — `info_field_rules.py`가 무장애 값을 `facility`에
  합칠 때 남긴 주석이 예견한 경계 문제다.
  서울시 공중화장실 위치정보(`mgisToiletPoi`, OA-22586)가 4,447곳의 좌표·개방시간·
  장애인화장실 유무를 준다. 실측(2026-09-05): 좌표 결측 0건, 공공개방 3,054곳 ·
  민간개방 1,379곳, 인사동 반경 1km 안 101곳.
- 결정:
  1. `public_toilet`을 새 `question_type`으로 만든다. 경계는 `parking` vs
     `realtime_parking`이 이미 쓰는 방식을 그대로 따른다 — 대상이 한 곳으로 명확하면
     `facility`("경복궁 화장실 있어?"), 여러 곳 중 갈 데를 찾으면 `public_toilet`
     ("경복궁 근처 화장실 어디야?"). eval 36케이스 정확도 1.0(경계 케이스 포함).
  2. **전량을 `public_toilets`에 적재하고 조회는 그 표에서 한다.** API에 지역·좌표
     필터가 없어(경로에 구 이름을 덧붙여도 무시하고 항상 4,447건을 준다) 요청마다
     부를 수 없다. 적재주기가 "비정기(자료 변경 시)"라 최신성 손실도 없다. 좌표가
     원본에 있어 공영주차장(D-101)과 달리 지오코딩 단계가 없다.
  3. **개방시간은 정규화하지 않고 원문을 보관하고, 해석은 조회 시점에 한다.**
     자치구 담당자가 손으로 적은 값이라 형식이 제각각이다 — 실측 88.8%만 시각으로
     환산되고 나머지 11.2%는 대부분 `정시(영업시작~종료)`(398건)로 시각 자체가 없다.
     해석 실패는 열림/닫힘으로 단정하지 않고 "개방시간 확인 필요"로 표시하고 원문을
     보여준다. 급한 사용자를 닫힌 문 앞으로 보내지 않기 위한 것이고, 파서를 고쳐도
     재적재가 필요 없다는 이점이 따라온다.
  4. 정렬은 **"지금 열린 곳 → 가까운 곳"**이다. 거리만 쓰면 20m 더 가까운 닫힌
     화장실이 1순위가 되는데(새벽 2시 인사동 실측: 50m 쌈지길은 10:30~20:30) 급한
     질문에 그 답은 쓸모없다. 개방 여부를 모르는 곳은 열린 곳 뒤·닫힌 곳 앞에 둔다.
  5. 노출은 **두 곳**이다. 급한 사람에게 목록을 길게 주면 고르는 게 일이 된다.
     반경은 1km — 그 이상은 "근처"가 아니다.
  6. **지명이 없어도 되묻지 않는다.** 기기 GPS를 기준점으로 쓴다. INFO의 다른 유형은
     사용자가 말한 지명을 지오코딩해 쓰지만, 급한 상황에 "어디 근처요?"를 되묻는 건
     답을 안 준 것과 같다. `InfoContextRequest.origin_coordinates`를 새로 실어 보낸다.
  7. 카드를 누르면 **네이버지도 도보 길찾기**로 이어진다. `openNaverDirections`에
     `mode`를 추가해 `route/walk`를 쓴다(기존 호출부는 대중교통 기본값 유지).
     항목별 좌표가 필요해 `RealtimeInfoDetailItem`에 `latitude`/`longitude`를 넓혔다.
- 채택하지 않은 것:
  - 전국공중화장실표준데이터(data.go.kr). 우리 TourAPI 키로는
    `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`라 별도 활용신청이 필요하고, 서울시 것만
    쓰면 이미 가진 `SEOUL_OPEN_DATA_API_KEY`로 신청 없이 된다.
  - `facility`에 위치 목록을 얹는 방식. "그 장소에 있는지"와 "어디로 가면 되는지"는
    출처와 응답 형태가 달라 한 유형에 두면 경계만 흐려진다.
  - 거리 조회를 DB(PostGIS/RPC)로 옮기는 방식. 좌표 바운딩 박스로 1차로 추리고
    파이썬 `haversine_km`으로 정렬하는 것이 이 기능군의 확립된 방식이다(D-101).
  - 새 provider 스위치·API 키. `SEOUL_CITYDATA_PROVIDER`와
    `SEOUL_OPEN_DATA_API_KEY`를 재사용한다(공영주차장이 택한 길과 같다).
- 곁가지로 고친 것(이 기능이 드러낸 기존 버그):
  1. **애매한 지명 폴백이 500이었다.** `fetch_info_context`가
     `_fetch_realtime_citydata_by_coords_only`에 메타데이터를 한 겹 더 감싸
     넘기는데, 받는 handler들은 평탄한 `tuple[ProviderMetadata, ...]`을 기대한다.
     "인사동 주차장 자리 있어?"처럼 후보가 갈리는 지명이 들어오면
     `realtime_public_parking`·`realtime_bus`·`realtime_commercial`이 모두
     `AttributeError`로 터졌다(2026-09-05 실측). 화장실을 이 폴백에 등록하면서
     드러났고, 호출부의 감싸기를 없애 네 유형이 함께 고쳐졌다.
  2. 화장실 조회 상한을 60 → 500으로 올렸다. 저장소 질의에 거리 정렬이 없어
     상한에 걸리면 임의의 일부만 오는데, 인사동에서 55m 24시간 화장실이 빠지고
     230m가 1순위로 나왔다(박스 안 114곳 중 60곳만 와서). 1km 박스 안 화장실은
     실측 중앙값 39곳·최대 126곳이라 그 4배로 뒀고, 상한에 닿으면 경고를 남긴다.
  3. 지명이 있을 때 기기 GPS가 이기던 순서를 바로잡았다. 그대로 두면 강남에서
     "인사동 화장실 어디야?"에 강남 화장실을 인사동이라고 답한다.
- 상세: `backend/app/providers/public_toilet.py`,
  `backend/app/public_toilet_hours.py`, `backend/app/tools/public_toilet.py`,
  `backend/app/repositories/public_toilet.py`,
  `backend/app/agent_context/service.py`,
  `supabase/migrations/202609050001_create_public_toilets.sql`,
  `supabase/data_dictionary/public_toilets.md`,
  `frontend/src/components/chat/PlaceInfoCard.tsx`,
  `frontend/src/utils/naverDirections.ts`

### D-123 — 실시간 도로소통 카드에 돌발상황 4분류 진행 건수를 함께 싣는다

- 상태: `Accepted` — 구현·테스트 완료.
- 배경: 사용자가 서울시 실시간 지도 화면(참고 이미지: "도로소통 / 서행" 아래
  "사고/고장 0건 · 공사/집회 0건 · 기상/화재 0건 · 기타 0건" 4분류)을 보여주며
  같은 항목을 우리 `realtime_traffic` 카드에도 붙여 달라고 요청. `citydata`
  응답에 이미 `ACDNT_CNTRL_STTS`(사고통제현황) 배열이 있는지부터 조사했다.
- 조사:
  1. 서울시 공식 매뉴얼(`실시간 도시데이터 매뉴얼 V8.5`, 2026-04)을 PDF로 직접
     읽어(pypdf + poppler로 렌더링) 확인 — `ACDNT_CNTRL_STTS`는 "사고발생일시,
     사고통제유형 등 9개 항목"으로 우리가 매번 부르는 `citydata` 통합 응답 안에
     이미 있다. 별도 API 연동이 필요 없다.
  2. **돌발 유형의 공식 코드표(`서울시 돌발 유형 코드 정보`, OA-13312)는
     서비스 종료 상태다** — data.seoul.go.kr에서 직접 확인. 그래서 전체 값
     목록을 코드표로 조회할 방법이 없다.
  3. 121곳 전수 실측(2026-09-06 18:01)으로 실제 원문값을 확인 — 그 시점에
     진행 중이던 사건 5건이 `ACDNT_TYPE`: "공사" 2건, "집회및행사" 2건,
     "기타" 1건이었다(명동 관광특구 도로보수 공사, 청계천로 차 없는 거리
     행사 등). "사고"·"고장"·"기상"·"화재" 값은 그 순간엔 없었다 — 드문
     사건이라 없었을 뿐 스키마에 없는 값이라는 뜻은 아니다.
- 결정:
  1. 사용자가 보여준 4분류(사고/고장·공사/집회·기상/화재·기타)로 원문
     `ACDNT_TYPE`/`ACDNT_DTYPE`을 키워드 매칭해 묶는다(`_road_incident_category`,
     `app/providers/seoul_citydata.py`) — 공식 코드표가 없으니 ITS
     국가교통정보센터가 쓰는 표준 돌발유형 체계(사고/고장차량/공사/행사/
     기상통제/기타)를 참고해 그 4분류로 합쳤다. 어느 키워드에도 안 걸리면
     "기타"로 떨어진다.
  2. **4분류를 항상 전부 채운다(0건 포함)** — 서울시 지도 화면과 같은 방식이다.
     값이 없는 분류를 조용히 감추면 "이 지역엔 그 유형이 없다"는 뜻으로
     잘못 읽힌다.
  3. `RoadTrafficStatus`(도메인)·`RealtimeCityInfoResult`(C 계약)·`InfoPlaceCard`
     (A 계약)에 `road_incident_counts`를 새로 달아 그대로 흘려보낸다 — 이미
     매번 받아오는 응답을 파싱만 하는 것이라 **추가 호출이 0**이다.
  4. 프론트 `RoadTrafficStatusSection`에 2x2 그리드 카드를 추가한다
     (`RoadIncidentCountGrid`). 값이 0인 분류는 평범한 잉크색, 1건 이상인
     분류만 rust로 강조한다 — 참고 이미지는 전부 0건이라 단조로운 흑백이었지만,
     실제로 사건이 있을 때는 무엇이 진행 중인지 눈에 띄어야 한다. 서울시 인구·
     상권 요약 타일(D-122)과 같은 카드 스타일(`bg-chip`, `rounded-xl`)을 써서
     같은 카드 안에서 시각적으로 통일된다.
- 검증: 명동 관광특구 실측(공사 1건 + 집회및행사 1건)으로 매퍼→계약→카드까지
  통과시켜 "공사/집회: 2건"이 정확히 나오는 것을 확인했다. 사고·고장·기상·화재
  키워드는 그 순간 실제 사건이 없어 합성 데이터로 분류 로직만 검증했다.
  백엔드 `pytest` 4,056 passed·37 skipped, `ruff check` 클린(신규 테스트 5건 —
  실측 조합 재현, 미실측 키워드 분류, 미분류 기타 폴백, 항상 4분류 채움, 카드
  전달). 프론트 `vitest` 65개 파일 551건 통과(그리드 렌더·강조색·미제공 시
  숨김 신규 2건), `tsc --noEmit`·`eslint`(신규 경고 없음)·`vite build` 클린.
  빌드된 CSS로 렌더해 Playwright 스크린샷으로 실제 카드 모양을 확인했다.
- 채택하지 않은 것:
  - 서울시 공식 돌발 유형 코드 API를 직접 호출하는 안 — 서비스 종료라
    애초에 불가능했다.
  - 개별 사건 상세(공사 위치·기간·안내문구 등, `ACDNT_INFO`)를 카드에
    노출하는 안 — 사용자가 보여준 참고 화면은 건수만 보여줬고, 상세까지
    넣으려면 지도·좌표 연동이 필요한 별도 스코프라 이번엔 건수만 뺐다.
- 상세: `backend/app/providers/seoul_citydata.py`, `backend/app/domain/models.py`,
  `backend/app/agent_context/info_schemas.py`, `backend/app/agent_context/service.py`,
  `backend/app/schemas.py`, `backend/app/services/runtime/info_response_transform.py`,
  `frontend/src/components/chat/CongestionForecastBars.tsx`, `frontend/src/types.ts`



### D-125 — 일정 편성은 "지금" 기준으로만 짜고, 시작 시각·경유 출발지는 조건으로 받지 않는다 (B)

- 상태: `Accepted` — 안내 문구는 후속.
- 배경: 실사용에서 두 발화의 조건이 조용히 버려진다.
  - `토요일 오후 2시부터 5시간 코스 짜줘` — "오후 2시부터"가 어디에도 담기지
    않는다. 조건 스키마에 시작 시각 필드가 없고 추출 프롬프트에 뽑는 규칙도 없다.
    편성기에는 `SchedulePlanningRequest.visit_datetime`이 있지만 **호출부 세
    곳이 전부 `None`을 넘겨** `planner`가 `now_kst()`로 떨어진다.
  - `홍대입구에서 인사동으로 이동하는 5시간 코스` — 홍대 쪽 장소가 하나도 안
    나오고 인사동 반경 100m 안 세 곳만 나온다. 위치 칸이 `current_location`
    (현재 위치를 직접 밝힐 때만) · `search_center`(한 곳) · `travel_origin`
    (이동시간을 말한 요청에서 기준점이 출발점이기도 함을 표시하는 스위치, 장소를
    담는 칸이 아니다) 셋뿐이라 "홍대입구에서"가 갈 데가 없고,
    `prompts/recommend/location_rules.md`가 그런 경우는 null로 두라고 명시한다.
- 결정:
  1. **시작 시각을 조건으로 받지 않는다.** `visit_datetime`은 운영 경로에서 계속
     `None`이고, 편성 기준 시각은 언제나 `now_kst()`다.
  2. **경유 출발지도 받지 않는다.** `search_center` 한 점 전제를 유지한다.
  3. 대신 **말한 조건이 반영되지 않았다는 사실을 사용자에게 알린다**(후속). 지금은
     `basis_note`가 "N시 M분 기준으로 짰다"고만 말해서, 사용자가 자기가 정한
     시각이 무시된 것을 알 수 없다.
  4. `visit_datetime` 필드는 **지우지 않는다.** 테스트 89곳이 여기에 고정 시각을
     넣어 타임라인 단정을 결정적으로 만드는 seam이다 — 지우면 그 단정이 실행
     시각에 따라 흔들린다. 왜 항상 `None`인지는 필드 주석에 적었다.
- 근거: **후보를 고르는 기준이 "지금"으로 고정돼 있다.** `agent_runtime`이
  `visit_at = now_kst()`로 그 턴의 기준 시각을 잡고, 그 값이 폐점 필터
  (`domain/scoring.py::_is_closed`)와 날씨 조회로 들어간다. 시작 시각만 받아
  타임라인에 쓰면 **후보는 지금 열린 곳으로 걸러졌는데 화면은 미래 시각을 적는**
  상태가 된다 — 토요일 오후 2시에 문 닫는 곳이 일정에 들어가고, 지금 닫혔지만
  토요일엔 여는 곳은 후보에서 아예 빠진다. 그 시각에 문 여는지 아무도 확인하지
  않은 일정에 그 시각을 적어 넣는 셈이다.

  **반쯤 지원하는 것이 아예 안 하는 것보다 나쁘다**는 판단이다(사용자 결정,
  2026-09-08: "거짓 정보를 주는 게 기능 제공을 안 하는 것보다 더 최악"). 지금도
  `basis_note`가 "실제로 가시는 시간에는 운영시간이나 날씨가 달라질 수 있어요"로
  면책하고 있는데, 사용자가 시각을 직접 정하면 그 면책을 읽지 않고 "내가 정한
  시각 기준이겠지"로 믿는다.

  말풍선과 화면이 같은 사실을 다른 조건으로 내보내면 반드시 어긋나는 조합이
  생긴다 — TP-243에서 "전부 한 묶음이면 타임라인 표시를 끈다"를 넣었다가
  되돌린 것과 같은 이유다.
- 채택하지 않은 것:
  - **타임라인만 그 시각으로 옮기기** — 위 근거의 거짓 정보를 만든다. 원래
    이번 작업의 계획이었고, `visit_at = now_kst()`를 확인하고 철회했다.
  - **폐점 필터·날씨·혼잡도까지 그 시각 기준으로 옮기기** — 정확하지만 후보
    수집·채점 전반을 건드린다. 날씨는 예보 조회에 `visit_at`이 이미 있어
    가능하고 혼잡도도 예측 슬롯이 있지만, 폐점 판정과 후보 수집이 남는다.
    막바지에 다른 패키지 소유 전제를 건드릴 작업이 아니라고 봤다.
  - **경유 코스를 실제로 지원하기** — `search_center`를 참조하는 곳이 백엔드
    28개 파일 112곳이고, 그중 후보 수집 반경·거리 채점·구 단위 조회(D-119)·
    되묻기 버튼·이동시간 기준점(D-071)이 "한 점"을 전제로 판단한다. 필드를
    추가하는 것 자체는 기본값 null이면 안전하지만, 그 값을 후보 수집이 읽기
    시작하는 순간 다섯 곳의 전제를 각각 다시 정해야 한다.
  - **발화 텍스트에서 코드가 시각·출발지를 찾아내기** — 계약 전제 위반이다
    ("B는 자연어의 의미를 해석하지 않는다"). 안내를 붙이려면 추출 쪽이 신호를
    줘야 한다.
- 남은 것: 안내 문구(어느 필드로 신호를 받을지 포함)는 추출 쪽 협의가 필요하다 —
  추출 출력에 필드를 더하는 일이라 소유가 넘어간다. 그리고 그 스키마 확장은
  D-126의 모델 결정이 먼저다(구조화 출력이 약한 모델에 칸을 더하면 악화될 수 있다).
- 상세: `backend/app/schedule/schemas.py`(`visit_datetime` 주석),
  `backend/app/services/runtime/agent_runtime.py`(호출부 세 곳),
  `backend/app/prompts/recommend/location_rules.md`

### D-126 — 조건 페이로드가 비어 오는 것을 해석 단계의 실패로 남긴다 (B)

- 상태: `Accepted` — 관측은 구현 완료. 모델 결정은 열려 있다.
- 배경: `llm_output.recommend`가 `None`이면 `state_transform`이 조건 병합을
  통째로 건너뛴다. **오류도 로그도 재시도도 없다.** 조건이 하나도 없는 채로
  추천·편성이 돌아가고 사용자에게는 말한 조건이 무시된 결과가 나간다.

  이 침묵이 실제 장애를 열흘 넘게 가렸다. 2026-09-02에 "골든셋 하네스와 프론트가
  다른 결과를 낸다 — `/api/chat`에서 SCHEDULE 턴의 `recommend`가 통째로 null,
  `condition_version: 0`"이 관측됐고 원인 미확정으로 남았다.
- 근거: `scripts/verify_schedule_condition_extraction.py`로 실측해 확정했다
  (2026-09-08, 18케이스 × 3회).

  | 주 모델 | 흔들린 케이스 | 기대 불일치 | 지연 중앙값 |
  | --- | --- | --- | --- |
  | `gemini-3.5-flash-lite` (운영값) | 6/18 | 9/18 | 1,564ms |
  | `gemini-3.5-flash` | **0/18** | **0/18** | 2,146ms |

  같은 프롬프트·같은 발화·같은 반복 횟수다. **모델만 바꿨다.**

  **두 번 독립 실행에서 같은 결론이 나왔다** — 반복 3회와 2회로 따로 돌렸고
  flash-lite는 두 번 다 6/18·9/18, flash는 두 번 다 0/18·0/18이었다.

  **다만 어느 발화가 흔들리는지는 재현되지 않는다.** 두 실행에서 흔들린 목록의
  겹침이 6건 중 3건뿐이다(광화문 하루 종일·오후 내내·두세 시간). 총량만
  재현되고 대상은 매번 갈린다 — **특정 발화의 문제가 아니라 모델 전반의
  불안정이다.** "이 발화만 고치면 된다"로 가면 안 된다.

  - `경복궁 코스 짜줘` · `경복궁 일정 짜줘` 두 건은 flash-lite에서 **두 실행 모두
    고정으로** 백지였고 flash에서는 둘 다 정상이다. 흔들림이 아니라 결정적
    실패라 재현이 가장 쉽다 — 조사를 시작할 발화로 이 둘을 쓴다.
    (`광화문 4시간 일정 짜줘`도 1차에서 고정 실패였지만 2차에서 흔들렸다.
    고정이라고 단정할 수 있는 것은 위 둘이다)
  - 추천 발화 대조군 2건은 flash-lite에서도 고정 통과했다 — 2026-09-02 관측의
    "RECOMMEND는 정상이었다"와 일치한다
  - **"엔드포인트 차이"가 원인이 아니다.** 이 하네스는 라우트를 타지 않는데도
    같은 증상이 난다. 두 엔드포인트가 다른 결과를 낸 것은 각각 한 번씩
    돌렸는데 값이 회차마다 흔들렸기 때문이다
  - **프롬프트 문구가 원인도 아니다.** `prompts/recommend/extract.md`가 자기를
    "RECOMMEND 조건 추출기"로 선언하고 일정·코스를 한 줄도 언급하지 않아 이것을
    원인으로 의심했지만, 문구를 그대로 두고 모델만 올리니 전부 통과했다.
    flash-lite가 그 선언을 문자 그대로 받아 일정 발화를 버린 것으로 보인다
  - 주 추출 모델이 flash-lite가 된 것은 2026-08-18(`89b5bdf`)이다. 그 묶음을
    8개 작업이 함께 쓴다(인텐트 분류, 추출 6종, 마감 규칙) — 조건 추출만
    영향받았다고 볼 근거는 없다
- 결정:
  1. 조건을 나르는 턴(RECOMMEND·SCHEDULE)인데 `recommend`가 없으면
     `llm_interpret` trace의 `error_type`에 `condition_payload_missing`을
     남기고 경고 로그를 찍는다.
  2. **`metrics`가 아니라 `error_type`을 쓴다.** TP-242가 도메인 지표를 기존
     단계에 얹지 않기로 정했고 그 분리를 잠그는 테스트가 있다. 이건 지표가
     아니라 이 단계가 제 일을 못 했다는 사실이라 계약 2절의 `error_type`이 맞는
     자리다 — 단계 이름이 `llm_interpret`, 즉 전송이 아니라 해석이라는 점이
     근거다. HTTP 호출은 성공했지만 해석은 실패했다.
  3. **조건이 전부 null인 것은 실패로 세지 않는다.** "일정 짜줘"처럼 조건을
     하나도 말하지 않은 발화에서는 그게 옳은 결과다. 페이로드 자체가 없는 것과
     뜻이 다르다.
  4. **모델을 바꾸는 것은 이 결정에 넣지 않는다.** 소유가 다르다.
- 채택하지 않은 것:
  - **별도 trace step으로 지표를 남기기** — 처음 그렇게 만들었다가 되돌렸다.
    턴마다 행이 하나씩 늘고 `latency_ms`가 의미 없는 값이 되며, 기존 trace
    테스트 세 건이 깨진다. `error_type`은 행을 늘리지 않고 오류 화면에 바로
    보인다.
  - **예외를 던져 턴을 죽이기** — 조건이 없어도 답을 낼 수는 있다. 사용자
    응답을 막는 대신 관측 가능하게 만드는 쪽을 골랐다.
- 남은 것: **주 추출 모델을 무엇으로 둘지는 열려 있다.** 후보는 셋이다 —
  (a) `LLM_FAST_MODEL_NAME`을 flash로 올리고 폴백을 lite로 (환경변수 두 줄,
  8개 작업 전부 느려지고 비싸짐), (b) 추출용 모델 설정을 따로 두기
  (`place_mood_rerank_model_name`·`gemini_audio_model_name`의 선례가 있다),
  (c) 프롬프트를 lite가 알아듣게 고치기(흔들림이 다 닫히는지 불확실).
  **지연은 실측했다** — flash가 추출 1회당 중앙값 1,564ms → 2,146ms로
  **약 +580ms**다(2026-09-08, `verify_schedule_condition_extraction.py`의 응답
  시간 출력을 켜서 측정. 값은 이미 모으고 있었고 출력만 없었다).

  체감 구간은 그 두 배로 본다 — 한 턴이 `classify_intent`와 `extract_*`를
  **순차로 두 번** 부르므로 (a)를 택하면 해석 구간이 약 +1.2초다. 다만
  그 구간은 D-106에서 SSE 하트비트(4초 간격)로 감싸 뒀으므로 무응답으로
  보이지는 않는다.

  **답변 생성과 일정 편성은 영향이 없다.** 그쪽은 generation 묶음이고
  (`generate_schedule_plan`·`stream_*`·`generate_*_summary`), fast 묶음을 쓰는
  것은 분류 1개 + 추출 5개 + 후속질문 + 마감규칙 8개다. 후속질문은 D-102
  후속으로 `done` 뒤에 도는 별도 이벤트라 체감 지연에 들어가지 않는다.

  **토큰 단가는 재지 않았다.** 지연만으로 정하지 말 것.

  **후보를 하나 더 적어둔다 — (d) 빈손일 때만 폴백을 강제로 일으킨다.**
  lite로 부르고, 위 결정 1의 판정이 걸리면 flash로 한 번 더 부른다. 정상 회차는
  비용이 지금과 같고 실패 회차만 비싸다. **폴백이 이걸 알아서 해주지 않는다는
  것이 요점이다** — 폴백은 타임아웃과 429·5xx가 재시도 소진됐을 때만 걸리고,
  `recommend: null`은 HTTP 200에 스키마도 통과해(`recommend`가 `| None`이라 null이
  유효한 값이다) `ok=True, status="ok"`로 기록되고 그대로 반환된다. 실측이 그것을
  보여준다 — 빈손 6건이 있었는데 응답 모델이 바뀐 케이스는 0/18이다. 전송 실패는
  폴백이 다룰 수 있지만 **의미 실패는 폴백 계층이 볼 수 없다.**

  **단가(2026-09-08 확인).** `gemini-3.5-flash`는 입력 $1.50 / 출력 $9.00,
  `flash-lite`는 입력 $0.30 / 출력 $2.50 (100만 토큰). **입력 5배 · 출력 3.6배**다.
  FAST 쌍이 매 턴 보내는 프롬프트는 인텐트 분류 8,417자 + 조건 추출 8,056자 =
  16,473자이고 절반이 한글이라, 비용의 거의 전부가 입력 프롬프트다. 대략적인
  턴당 추정으로 (a)는 지금의 약 4.5배, (d)는 약 1.8배다.

  **8월에 같은 비교를 이미 했다 — 다시 재기 전에 반드시 읽을 것.**
  `backend/test_results/intent_experiments_2026-08.md` §3·§4가
  **인텐트 분류**에서 flash 대 lite를 재고 **기각** 판정했다(2.5 세대).

  | | flash `b=0` | lite `b=0` | lite `b=512` |
  | --- | --- | --- | --- |
  | 정확도 (64건) | 63/64 | 56/64 | 59/64 |
  | 지연 중앙값 | 1,588ms | 1,027ms | 2,118ms |

  하락 7건이 5회 반복 5/5로 재현되고 **6건이 대화 맥락에 따라 intent가 갈리는
  케이스**다. 그 문서가 남긴 표현이 지금 증상과 같다 — "첫 발화는 멀쩡하다.
  대화가 이어진 뒤가 무너진다." 그리고 lite + thinking은 flash보다 **느리고 덜
  정확했다**(2,118ms · 59/64). 세대가 2.5라 3.5에 그대로 쓸 수는 없지만, 같은
  실험을 다시 설계하기 전에 읽어야 한다. 측정 도구도 그대로 있다
  (`scripts/measure_intent_thinking.py`).

  **모델 선택은 팀이 별도 문서로 정리하기로 했다(2026-09-08).** 답변·일정 생성을
  lite로 내려도 되는지까지 함께 볼 범위라 이 결정에 넣지 않는다. 위 후보 넷은
  그 문서의 출발점으로 남긴다 — 어느 쪽이 되든 결정 1의 관측은 그대로 쓴다.
- 상세: `backend/app/services/runtime/agent_runtime.py`
  (`_condition_intake_error`), `backend/scripts/verify_schedule_condition_extraction.py`,
  `backend/app/services/interpret/state_transform.py`

## 변경 이력

### D-124 — 일정 편성의 시간 예산은 허용 오차 30분으로 판정하고, 개수 상한은 LLM이 고른 분류로 다시 잰다

- 상태: `Accepted` — 구현·테스트 완료. 브라우저 실측 확인.
- 배경: "3시간 코스 짜줘"에 4시간 26분짜리 일정이 나간다는 민원에서 시작해
  TP-238(체류시간 배분)·TP-239(개수를 예산에서 유도)·TP-242(품질 지표)·
  TP-244(표시 단위)를 넣었다. 그 과정에서 값을 하나 정해야 했는데(허용 오차)
  결정으로 올리지 않았고, 민원이 닫힌 줄 알았는데 2026-09-07 실측에서 같은
  모양이 다시 나왔다(3시간 요청 → 276분, 초과 96분).
- 조사:
  1. **허용 오차 30분은 곳 수를 가르는 문턱이다.** 관광지 최소 체류 60분·구간
     이동 15분 기준으로 2시간 2곳은 15분, **3시간 3곳은 30분**, 4시간 4곳은
     45분, 5시간 5곳은 60분이 필요하다. 30분은 "3시간에 3곳"이 통과하는
     최소값이면서 동시에 4곳 이상을 막는 밀도 상한을 겸한다.
  2. **재현된 민원의 기전.** `derive_item_range()`는 후보들의 체류 최소값을
     **작은 것부터** n개 써서 상한을 정한다(의도된 하한 가정 — 상한을 너무
     깎지 않으려는 것). 후보 풀에 쇼핑(최소 30분)이 섞이면 3시간에 3곳이
     통과하는데, LLM이 문화시설(최소 90분) 세 곳을 고르면 270분 + 이동이
     되고 `fit_durations_to_budget()`은 정책 최소값에서 멈춰 되돌릴 여유가
     0이다. 실측 그대로다 — 국립현대미술관·국제갤러리·세계장신구박물관 90분씩,
     도보 4분·2분, 총 276분.
  3. **TP-242 지표 13턴 판독(2026-09-07).** 시간을 말한 8턴은 전부 `within`,
     `over`·`under` 0건이었다. 그러나 표본이 한 사람의 사흘치 수동 테스트이고
     세 행이 값까지 동일한 반복이라 분포가 아니었다 — 브라우저 실측 첫 턴이
     바로 `under`였다. **작은 표본의 0건을 "없다"로 읽지 않는다.**
- 결정:
  1. **허용 오차는 30분이고 상수는 `budget.SCHEDULE_TIME_TOLERANCE_MIN` 하나다.**
     키우면 "짧게 머물며 많이 넣기"로 샌다. 오차 안이면 아무것도 하지 않는다 —
     밴드 안에서 체류를 늘리는 것은 "말한 시간을 꽉 채워 다니겠다는 뜻"이라고
     가정하는 것이고 팀이 합의한 기록이 없다.
  2. **개수 상한을 LLM 응답 뒤에 다시 잰다**(`budget.cap_item_count_to_budget()`).
     유도값은 프롬프트에 주는 목표로 남기고, 응답이 온 뒤 **실제로 고른 항목의
     분류**로 같은 부등식을 다시 풀어 초과분을 자른다. 개수 상한을 지시가 아니라
     자르기로 보장한 TP-239와 같은 철학이다("프롬프트 지시는 부탁이고 검증이
     계약이다").
  3. **상한 계산 자체를 비싼 조합 가정으로 바꾸지 않는다.** 어느 분류가 뽑힐지는
     LLM이 고르기 전까지 알 수 없고, 가장 비싼 조합을 미리 가정하면 대부분의
     요청에서 곳 수가 실제보다 줄어든다.
  4. **상한이 줄면 보관함 판정도 다시 한다.** `over_capacity_place_names`가 처음
     상한으로 계산된 값이면 줄어든 자리 때문에 못 들어간 보관함 장소가 아무 안내
     없이 사라진다. `_resolve_must_include()`를 다시 불러 두 값이 같은 상한을
     보게 한다.
  5. **`under` 판정에도 문장을 붙인다.** 판정이 셋인데 말풍선에는 `over` 문장만
     있어서, 3시간 요청에 1곳 120분이 나오면 "2시간 코스를 짜봤어요"라고만 하고
     왜 짧은지 말하지 않았다. `over`와 대칭이지만 사용자가 손쓸 데가 없으므로
     되묻지 않고 사실만 말한다. **원인은 자리를 다 썼는지로 가른다** — 항목 수가
     상한과 같을 때만 "근처에서 넣을 만한 곳을 더 찾지 못했다"고 말할 수 있다.
     자리가 남았는데 짧은 것은 LLM이 덜 고른 것이라 원인을 단정하지 않는다.
  6. **판정은 언제나 정확값으로 내린다.** 표시용으로 10분 올린 값으로 판정하면
     205분/175분 요청 같은 경계에서 `within`이 `over`로 뒤집혀, 화면이 요청
     시간 대신 실제값과 초과 안내를 말한다(TP-244).
- 기각한 것:
  - **초과 시 장소를 사후에 빼기** — TP-216 결정을 유지한다. 총 소요시간은 추정
    이동시간 위에서 계산될 때가 있어(`travel_to_next_measured`) 넘었다는 이유로
    빼면 추정 오차로 멀쩡한 일정이 깎인다. 결정 2는 이것과 다르다 — "총합이
    넘었으니 뺀다"가 아니라 "상한을 계산한 전제가 틀렸으니 상한을 다시 잰다"이고,
    자르는 자리도 이미 개수 상한이 작동하던 `_cap_item_count()`다.
  - **예산 미달일 때 카드를 만들지 않는다는 판단** — 지표에서 `under`가 0건이라
    한 번 접었는데 같은 날 실측으로 뒤집혔다. 존재 여부는 코드에 그 분기가
    있는지로 판단하고, 빈도는 우선순위를 정할 때만 쓴다.
- 후속 결정(2026-09-07, 같은 날): **시간을 말하지 않은 턴에도 기본 예산
  240분을 쓴다.** 그전에는 `derive_item_range()`가 상수 `(3, 5)`를 돌려주고
  배분·판정·안내가 모두 건너뛰어져, 실측에서 258~420분 일정이 안내 없이
  나갔다(13턴 중 5턴).
  1. **새 값을 정한 것이 아니다.** 프롬프트가 시간이 없을 때 이미 "3~4시간
     내외로 구성하세요"라고 안내하고(`gemini_prompts.py`), 없앤 상수 `(3, 5)`의
     주석이 그 문구를 근거로 만든 값이라고 밝히고 있었고, D가 "반나절"을 같은
     폴백에 맞춰 240으로 확정한 이력이 있다(`prompts/recommend/HISTORY.md`
     2.5.0). **개수만 그 기본값을 따르고 시간은 아무도 따르지 않았던 것이
     문제였다.**
  2. **개수 상한과 체류 배분에만 쓴다.** `classify_budget()`은 None을 그대로
     None으로 둔다 — 가정한 예산으로 "4시간으로 말씀하셨는데"라고 되묻는 것은
     안 한 말을 한 것으로 만든다. 말풍선은 시간 미지정 턴에 여전히 조용하다.
  3. **넘칠 때만 줄이고 모자라면 늘리지 않는다**(`fit_durations_to_budget(
     shrink_only=True)`). 가정한 예산에 맞춰 체류를 늘리는 것은 예산과 "꽉 채워
     다니겠다는 의사"를 둘 다 지어내는 것이다. 말한 시간에 대해서도 팀이 그렇게
     합의한 적이 없다(결정 1).
  4. **후보 부족 가드(3곳)는 개수 범위와 분리했다**(`required_candidate_count()`).
     한 상수가 둘을 겸하면 상한이 예산에서 나오는 순간 프롬프트에 "3개 이상
     2개 이하"라는 모순된 범위가 실린다. 가드 자체는 SCHEDULE-07 동작을
     유지한다 — 풀면 "후보가 부족해요" 대신 1곳짜리 일정이 나가기 시작하고
     그건 별개의 제품 판단이다.
- 남은 것 1: **시간 미지정 턴의 실효 천장이 270분("4시간 30분")이다** — 가정
  240 + 허용 오차 30. 프롬프트가 안내하는 "3~4시간 내외"보다 30분 길다. 실측에서
  267분이 나와 그 천장에 붙었다(2026-09-07). 허용 오차는 "말한 시간을 얼마나
  벗어나도 지킨 것으로 볼까"의 값인데 **말하지 않은 시간에는 벗어날 기준이
  없다**는 점에서, 가정한 예산에는 오차를 적용하지 않는 것이 논리적으로 더
  맞다. 다만 상한이 조여져 곳 수가 한 곳 줄어드는 경우가 생기므로 지금은
  그대로 두고 기록만 남긴다(사용자 판단, 2026-09-07).
- 남은 것 2: 후보가 1곳만 오는 경우가 관측됐다(경복궁 0.11~0.24km 세 곳을 놓치고
  2km 밖 한 곳). 폐점 필터(`closed_excluded`가 빈 배열)·추천 이력(`recommended`도
  0) 둘 다 배제돼 후보 수집 단계 원인이 미확정이다.

| 날짜 | 변경 |
| --- | --- |
| 2026-07-23 | Phase 1-A 실개발 시작용 최초 통합 의사결정 로그 작성 |
| 2026-07-23 | D-008 Scoring v1 엔진 구현 반영 (Feature/가중치/tie-break 확정) |
| 2026-07-23 | D-008 Scoring v1에서 카테고리 Feature 제외, 남은 영업시간 → 운영 유무로 단순화 (날씨 0.40/운영 유무 0.40/거리 0.20) |
| 2026-07-23 | Provider 공통 metadata의 source/status/retrieved_at 계약 확정 |
| 2026-07-23 | Tool 공통 오류와 no_data/unavailable 판정 기준 확정 |
| 2026-07-23 | Provider별 Blocker와 P0~P3 해결 기준 확정 |
| 2026-07-23 | Backend Python/JSON snake_case 공통 규칙 확정 |
| 2026-07-23 | ProviderMetadata와 ProviderError 분리 및 오류 시각 계약 확정 |
| 2026-07-23 | 방문 예정 시각의 초단기예보 우선 정책 확정 |
| 2026-07-23 | 종로구 `resolve_location` 범위·fallback·재질문 정책 구현 반영 |
| 2026-07-23 | D-008 재설계: 운영 유무를 가중치에서 제외하고 `now`/`OperatingHours` 기반 최종 하드 필터로 이동, 가중치 Feature를 남은 운영시간(분 정규화)으로 교체 (날씨 0.40/남은 운영시간 0.40/거리 0.20), `weights_used`를 후보별로 노출하도록 변경 |
| 2026-07-23 | Weather Tool v1의 KST·방문시각·예보 범위 정책 구현 반영 |
| 2026-07-24 | D-027 추천 Evidence·평가 Fixture v1 구현 반영 (Feature 기여도 모델, 고정 Fixture v1, 결정성 검증) |
| 2026-07-24 | D-028 추천 파이프라인 1차 E2E 통합 구현 반영 (응답에 score/feature_scores/weights_used 노출, 날씨 유무·결정성 E2E 테스트, 재사용 가능한 파이프라인 Fixture) |
| 2026-07-27 | D-029 Recommendation Explainability Layer v1(Rule 기반) 구현 반영 (초안, A 담당과 API Contract 협의 전) |
| 2026-07-27 | D-030 날씨 결측·임계값 미달로 explanations가 비는 두 케이스에 warning 커버리지 보완 |
| 2026-07-27 | D-029 A 담당과 API Contract 협의 반영 완료, Explanation Rule 정의 문서(`docs/design/recommendation-explainability.md`) 추가 |
| 2026-07-27 | D-031 Explanation 문장을 고정 텍스트에서 거리/남은 운영시간/날씨·환경 계산값 기반 사실 문장으로 구체화 |
| 2026-07-28 | D-032 A 요청으로 `run_recommendation_pipeline_from_context()` 신규 진입점 추가, 기존 Tool 기반 파이프라인과 공존(이후 D-034에서 공존 종료, 완전 삭제) |
| 2026-07-28 | D-033 Agent Runtime `RecommendationProvider`에 `RealRecommendationProvider` 연결, `run_agent()` 기본 provider를 Fake에서 실제 구현으로 교체 |
| 2026-07-28 | D-034 `run_recommendation_pipeline()`(Tool 직접 호출) 완전 삭제, `/api/recommendations` 라우터를 `run_recommendation_pipeline_from_context()` 기반으로 마이그레이션 |
| 2026-07-28 | D-035 develop 재병합 시 발견된 `RealRecommendationProvider` 중복 구현 정리, mintee의 `real_recommendation_provider.py`로 통합 |
| 2026-07-31 | D-038 날씨 warning을 IGNORE(미언급)와 조회 실패로 분리, §10 불일치·날씨 조회 경로 이원화를 TODO로 기록 |
| 2026-08-02 | D-039 되묻기 답변을 기존 요청의 연속으로 처리하고 조건 유지·플래그 저장 및 소비 규칙을 기록 |
| 2026-08-02 | D-040 혼잡도 2차 Scoring 구현(안 B 채택), `rerank_with_concentration()` 신규 인터페이스와 concentration Feature를 Evidence/Explanation에 추가, A에 Protocol `context` 파라미터 추가 요청 |
| 2026-08-03 | D-042 Real Provider 실패 시 Fake 자동 전환을 하지 않는 공통 정책을 명시 |
| 2026-08-04 | D-043 혼잡도 장소 결정 순서(이름 우선·좌표 최후)와 `tAtsNm` 공백 제약·검색어 추출 규칙 기록 |
| 2026-08-04 | D-043 구현 완료 — `concentration_search_key` 컬럼 추가, 매핑 101건 적재, 저장소 이름 조회를 부기·별칭까지 확장 |
| 2026-08-04 | D-044 지원 지역 밖 위치를 해석 단계에서 `unsupported`로 끊도록 구현, 종로구 경계 폴리곤 리소스 추가 |
| 2026-08-04 | D-045 같은 역의 노선별 후보를 한 장소로 묶어 환승역 재질문을 없앰, `haversine_km`을 `app/geo.py`로 통합 |
| 2026-08-04 | D-046 environment_type을 TourAPI 중분류(lcls_systm2) 기반으로 세분화 구현 반영(indoor 19/outdoor 16/unknown 13), 판정표 고정 테스트 추가 |
| 2026-08-05 | D-047 D-040 리뷰 후 A에 `rerank_with_concentration()` 시그니처 축소(`RecommendationContext` → `WeatherCondition`) 역제안, D 확인 필요 사항으로 필터 기준 명시 |
| 2026-08-05 | B 리뷰 반영: `_serialize()` int→str 버그·RECOMMEND `exclude_tags`/`special_requirements` Update→Add 수정, `api_context.api_weather` 죽은 코드 제거, `PROMPT_VERSION` 신설(record_trace·StateApplyRequest 양쪽 연결) 및 D-040의 `SCORING_VERSION` 최초 연결. D-048로 MODIFY 경로의 동일 계약 위반은 제안만 기록 |
| 2026-08-05 | D-049 `conditions.weather`(발화 기반 5단계 값)가 C/D 어디서도 안 읽히는 죽은 필드로 보인다는 발견을 기록(코드 미변경, 원 설계 의도 확인 필요) |
| 2026-08-05 | 혼잡도 실서버 E2E 검증 문서(`concentration-e2e-verification.md`) 작성. D-050으로 혼잡도 2차 Scoring 결과의 순서만 B에 저장되고 점수/등급 값은 저장 안 됨을 기록(코드 미변경) |
| 2026-08-05 | D-051 날씨 사실/판정 분리 — C의 사실 전달과 `NO_MENTION` 수용 구현, 판정 이관은 D 확인 대기 |
| 2026-08-05 | D-052 Gemini 동일 벤더 내 모델 fallback(`LLM_FALLBACK_MODEL_NAMES`) 구현 + AppError 전파 경로 로깅 추가(무로그 502 문제 해결) |
| 2026-08-05 | D-051 판정 이관 구현 완료 — `weather_judgment.py` 신설(사실/발화 기반 판정 + 의도 재해석), `recommendation_pipeline.py`가 PR #102의 `conditions` 파라미터를 받아 사실 우선·발화 폴백으로 배선, `resolve_weather_condition()` public 전환(2차 Scoring 재사용용), `weather_ignored` 판별을 IGNORE 전용으로 정정. 2차 Scoring 배선 통일과 `condition` 필드 제거는 남은 것으로 기록 |
| 2026-08-05 | D-051 근거 문장 정확도 수정 — 판정 함수가 `WeatherReason`(rain/snow/heat/cold)을 함께 반환하도록 바꾸고 `scoring.py`/`evidence.py`/`explanation.py`까지 관통시켜, "폭염인데 비 예보"·"ENJOY로 GOOD인데 맑은 날씨"라고 말하던 사실-근거 불일치를 해소 |
| 2026-08-05 | D-051 기온 판정을 기상청 주의보/경보 2단계(33·35°C, -12·-15°C)로 재설계 — 주의보~경보 사이를 NEUTRAL로 두어 근거 있는 완충 구간 확보. 30~32°C 등 주의보 미만 구간은 의도적으로 미해결로 남김 |
| 2026-08-06 | D-051 `condition` 전면 제거 — `WeatherForecastSlot.condition`(D 소유)부터 `SelectedWeatherForecast.condition`, `map_sky_pty_to_condition()`, `tool_intelligence` 계약, `fake_weather_condition` 설정까지 걷어냄. `tool-intelligence-contract-v1.md`의 `TI-09`를 `Superseded`로, §9 `api_weather` 매핑을 D-038 무효로 반영 |
| 2026-08-07 | D-055 신설 — INFO `event` 지원(C). `sigunguCode` 필터가 응답 다수를 탈락시키는 것을 실측으로 확인해 법정동 코드로 전환하고, 장소명 매칭 대신 좌표 근접으로 "근처 진행 중 행사"를 답하도록 결정. `EventInfoResult` 신설 |
| 2026-08-07 | D-054 신설 — INFO `question_type` 8종으로 확장(C). 상세 질의는 Supabase 캐시에 데이터가 없어 TourAPI를 직접 조회하도록 결정하고 `GetPlaceDetailTool`·`info_field_rules` 신설. `event`는 `unsupported`, A 배선 3건은 별도 카드 |
| 2026-08-06 | D-053 신설 — TP-67(PR #113) 후속으로 위치 변경 판정에서 지명 단독을 제외해 `INFO`(정보 조회) 흐름을 지키고, `environment` 미언급(`ANY`)이 되묻기 답변에서 앞 턴의 `indoor`를 덮어쓰던 갭을 프롬프트 규칙 + 보존 목록으로 막음. `PROMPT_VERSION` 1.0.2 |
| 2026-08-07 | D-054 트리비 페르소나와 `GENERAL(service_identity)`, RECOMMEND/MODIFY 추천 결과 요약 LLM 추가. 요약 입력에서 내부 scoring 필드는 제외 |
| 2026-08-08 | D-056 신설 — TourAPI 주차·요금·이미지 원문을 `places` 컬럼 6개로 추가(C). 별도 테이블 분리 대신 컬럼 추가를 택한 근거와 축제 `usetimefestival` 함정, 커버리지 한계(주차 95%/요금 24%)를 기록 |
| 2026-08-08 | D-057 신설 — 집중률 검색어를 단일 값에서 공백 토큰 전부로 바꾸고, 코퍼스 등장 빈도 오름차순으로 조회하도록 결정하고 구현(C). 손으로 쓴 불용어 목록 대신 희소성 기준을 쓰는 근거를 실측(종로구 113개 이름)으로 기록하고, 대조 유사도 임계값 0.9를 유사도 매칭 금지 방침의 조건부 예외로 명시 |
| 2026-08-08 | D-058 신설 — 운영시간 요일 파싱 수정(C). `매주 월요일~화요일`의 물결표 요일 범위 전개와 요일별 운영시간 분리를 구현하고, 요일을 열거한 원문에서 빠진 요일을 정기 휴무로 유도하도록 결정. 유도 근거는 활성 844건 중 39건 실측(38건이 휴무 원문과 일치). 활성 97건의 운영 판정이 달라짐. `OPERATING_PARSER_VERSION` 1.2.0 |
| 2026-08-08 | D-059 신설 — SCHEDULE 되묻기 답변이 MODIFY로 오분류되던 문제 수정(A). `classify_intent()`에 `pending_clarification`/`last_intent` 컨텍스트를 전달하고 프롬프트 규칙만 추가(상태 레벨 override 채택 안 함, D-053과 같은 방향). 되묻기 플래그 소비 화이트리스트에 SCHEDULE 누락도 함께 수정. `PROMPT_VERSION` 1.0.7 |
| 2026-08-10 | D-060 신설 — INFO 상세 질의의 요금·주차·편의시설을 `raw_intro` 원본 키 조회에서 `PlaceDetails` 정규화 필드로 이관하고 옛 경로를 삭제(C). 전화번호 출처를 `detailCommon2` `tel`(축제만 5/5)이 아니라 `detailIntro2` 안내처(33건 중 32건)로 확정하고 `places`에 `info_center_raw` + 편의시설 컬럼 4개를 신설(편의시설은 대상 유형 55건 중 22건, 쇼핑은 카드결제·화장실 12/12). 캐시가 `question_type` 전부를 덮게 되어 INFO 상세 출처는 하이브리드로 단일화하고 선택 설정을 두지 않는다 — 외부 호출 3회 → 1회. D-054 대체 |
| 2026-08-18 | D-061 신설 — 상세 카드에서 요일별 운영시간 원문이 한 줄로 노출되는 문제를 기록. C가 `OperatingSchedule` 기반 표시 전용 구조를 제공하는 후속 작업을 제안하고, A/프론트는 계약 확정 전까지 원문 폴백을 유지 |
| 2026-08-19 | D-062 신설 — 정식 로그인 도입 전까지의 게스트 로그인 설계. 게스트를 Supabase 익명 사용자로 발급해 승계를 uid 유지로 해결하고, 신원(uid)과 세션(`sess_...`)을 분리. `Authorization` 헤더 전용 계약, optional 인증에서 토큰 없음(통과+경고)과 검증 실패(401)를 구분, `agent_states`/`recommendation_histories`에 `user_id` 추가. 승계가 익명성도 함께 무너뜨린다는 점과 대화 로그 서버 저장을 미루는 근거, LLM 전송의 티어·국외이전 쟁점과 프롬프트 식별자 금지 규칙을 개인정보 절로 기록. JWT 검증은 공개키 로컬 검증으로 확정하고 알고리즘 혼동 방어 항목을 함께 남김. `docs/design/guest-auth-design.md` 신설 |
| 2026-08-19 | D-063 신설 — D-062 Phase 3(신원을 세션에 저장) 착수 전 B 확인이 필요한 네 항목을 정리. `STATE_STORE_BACKEND` 전환 시점(Phase 3 제외 권장), 소유권 검증 위치(Phase 4 권장), 빈 `user_id` 채우기 규칙(덮어쓰기 금지), `auth.users` FK(걸지 않기 권장). 마이그레이션 초안은 소유자 확인 전까지 `supabase/migrations/`에 두지 않고 설계 문서 6-1절에만 둔다 |
| 2026-08-19 | D-064 신설 — 인텐트별 프롬프트 라이브러리 이관 완료 후, `meta.yaml`을 런타임 설정값으로 승격하지 않고 사람이 읽는 선언 + CI 검사 입력으로만 두기로 결정(YAGNI, 강의 25-6). `template`·`bundle`은 실제 조합과의 일치를 CI가 강제하고, 소비처가 없는 `version`·`owner`·`evals` 3건과 미조합 공통 규칙 3건(`factuality`/`safety`/`service_scope`)은 필요해질 때 착수하도록 미룸. 인텐트별 평가를 채우더라도 머지 게이트는 `agent_quality`의 다중 턴 전수 실행을 유지한다(강의 27-4) |
| 2026-08-19 | D-065 신설 — D-064의 "미룬 것 1"을 즉시 착수해 해소. `registry.py`가 `meta.yaml`을 파싱해 슬롯 버전을 읽고, `record_trace(prompt_version=...)`에 그 턴이 실제로 쓴 슬롯 버전을 남긴다(`agent-interpret-prompts-1.0.16` → `router.classify@1+info.extract@1`). 슬롯 선택은 `INTENT_SLOTS` 라우팅 테이블(강의 89-3), 과거 기준선 실행 시 `+variant:<ID>` 접미. 프롬프트 출력은 불변(스냅샷 27건 0바이트 차이), B 계약 변경 없음. 슬롯 이름 불일치·미등록 인텐트를 잡는 안전장치 3건 추가. D-064의 `owner`·`evals`·미조합 공통 규칙은 계속 `Deferred` |
| 2026-08-20 | D-066 신설 — gemini-3.5-flash 전환 뒤 미해결로 남아 있던 답변·요약 계열(GENERAL/RECOMMEND/COMPARE/INFO 답변 5곳)에 thinking_budget=0 적용. 실사용에서 "안녕" 응답 6~7초 TTFT, COMPARE류 후속 질문 18초+ 소요(45초 타임아웃 근접, 실제로 48초 stream_inactive 오류 재현)를 확인 후 `scripts/compare_answer_thinking_budget.py`로 5개 케이스 × 3회 실측 — 평균 3.9배 개선, 답변 품질(페르소나·자기소개·문장 수 규칙) 유지 확인. 5곳 모두 SCHEDULE과 같은 방식으로 내부 고정(공개 API 미노출). 실사용 재현으로 "안녕" 9.35초→5.06초(TTFT 6.5초→0.85초), COMPARE 분류+추출 18.8초→2.8초 확인. 회귀 테스트 6건 추가. 나머지 미최적화 추출 4곳과 분류·추출 단계 하트비트 부재는 범위 밖 |
| 2026-08-21 | D-067 신설 — 랭킹 기준점을 검색 기준점에서 사용자 위치로 이관(D·A). 후보 수집 중심은 검색 기준점 그대로 두고 거리·경로 origin·근거 문장 기준점 이름만 옮겼다. 검색 기준점에서 등거리인 두 후보를 타겟 기준으로는 구분할 수 없다는 것이 근거. 거리 점수 분모는 이동시간을 말한 요청에서는 그대로 두고(시간 약속은 원점이 없다), 말하지 않은 요청에만 사용자→기준점 거리를 더해 0점 전멸을 막는다. TP-109·TP-112의 `distance_km` 기준점 유지 방침을 대체한다 |
| 2026-08-21 | D-068 신설 — 피드백을 남긴 턴에 한해서만 질문·답변 원문(`user_input`/`assistant_message`)을 `response_feedback`에 저장하기로 결정. 대화 전체 로그 저장(guest-auth-design.md 9절이 위험도 "높음"으로 분류)과는 다르며, 사용자가 AskUserQuestion으로 "피드백 남긴 턴만"을 직접 선택. 두 컬럼 모두 nullable |
| 2026-08-21 | D-069 신설 — D-068 후속. `intent`(assistant_text 메시지 값 그대로 복사)와 `comment`(싫어요 사유 자유 텍스트) 필드 추가. comment는 append-only 중복 방지를 위해 클릭 즉시 전송하지 않고 인라인 입력창(제출/건너뛰기)에서 한 번에 전송. NOT NULL 전면 적용과 `is_clarification`/`clarification_code`(되묻기 메시지엔 피드백 버튼이 아예 없어 채울 수 없음)는 채택하지 않음 |
| 2026-08-21 | D-070 신설 — 팀원 PR #214(`reason_code` 구조화된 싫어요 사유)를 B 관점에서 검토·반영. FeedbackReasonCode 7값이 DB CHECK와 일치함을 확인, intent/user_input/assistant_message 캡처는 render-time `findTurnText` 방식을 그대로 유지(PR #214가 시도한 reducer-embedding 방식은 미채택). 마이그레이션 `202608210006` 적용 중 `response_feedback_kst` 뷰 컬럼 순서 버그(PostgreSQL 42P16) 발견·수정 |
| 2026-08-22 | D-071 신설 — "안국역에서 10분"(출발점=안국역)과 "안국역 근처에 10분"(출발점=사용자 위치)을 구분하는 `travel_origin` 필드 신설(D·A). 조사("~에서/까지")로 출발점이 확정되는 발화만 `search_center`로 채우고 그 외(근처/주변, 조사 없는 소수 발화)는 null로 두어 D-067 기본값이 그대로 적용되게 한다. `resolve_ranking_origin()`·`_distance_denominator_offset_km()`·soft reset의 search_center 복원 로직에 함께 배선. `recommend.extract` 2.2.0 → 2.3.0 |
| 2026-08-24 | D-044 확장 — 지원 지역 폴리곤을 종로구 1개에서 종로구·중구·용산구·성동구 4개로 늘림(TP-125). 판정 방식(폴리곤 순회)과 D-044 결정 자체는 그대로이고 폴리곤 개수만 바뀐다. 경계 파일은 구별로 두지 않고 서울 25개 구를 `seoul.geojson` 한 장에 담아, 이후 구 확장이 `SUPPORTED_DISTRICTS` 한 줄로 끝나게 했다(214KB·파싱 2.5ms). 파일에 있는 구를 전부 지원하는 방식과 환경변수 지정은 기각. 경계 추출은 `scripts/extract_district_boundaries.py`로 재현 가능. 활성 2,570건 실측 결과 폴리곤 밖 4건(0.16%). 25개 구 대표점 전수 테스트로 지원 여부 판정을 고정. 장소 검색의 종로구 고정 해제는 범위 밖(TP-126) |
| 2026-08-24 | D-025 개정 — 장소 검색의 종로구 고정을 해제(TP-126). 요청은 `lDongRegnCd=11`까지만 좁히고 지원 구 판정은 응답의 `lDongSignguCd`로 한다. 좌표로 구를 판정하는 안은 기각 — 서울역 부속 시설 72건처럼 등록 구와 좌표가 어긋나는 장소가 빠진다. 구마다 호출하는 안도 기각(호출 수가 구 수만큼 증가). `PLACE_SEARCH_LDONG_DISTRICT_CODE` 상수 제거, 지원 구는 `SUPPORTED_DISTRICTS` 하나가 정하도록 통일(좌표 폴리곤과 같은 출처). 축제 조회와 Supabase `places` 필터도 같은 집합을 쓴다. 구 코드가 빈 응답은 버리되 경고 로그를 남긴다 |
| 2026-08-24 | D-072 신설 — 인텐트 라우팅과 추천 파이프라인을 LangGraph 그래프 2개(조기 반환 / 추천 파이프라인)로 이관. 인텐트 분류와 조건 병합은 B 계약 소유라 그래프 밖에 남겼고, SSE는 sink를 `RunnableConfig`로 주입해 노드가 직접 호출하는 방식을 택했다(`astream_events`는 `message_delta`가 노드 내부에서 나와 재현 불가). checkpointer는 채택하지 않음 — `StateStore`(도메인 저장소)와 역할이 다르고, `MemorySaver`는 실측상 이전 턴 값 유출과 메모리 누적만 남겼다. `run_agent_flow()` 1,227줄 → 640줄. 프론트 변경 0줄, pytest 2,323건 통과, 차등 비교 18케이스 전부 일치, 오버헤드 약 1ms. 롤백용 기능 플래그 2개는 한동안 유지 후 기존 경로와 함께 제거 예정 |
| 2026-08-24 | D-073 신설 — `session_id`만으로 남의 세션에 접근 가능하던 문제를 닫음(D-063 결정 2 후속). Phase 4(인증 필수화) 전면 도입을 기다리지 않고, `Principal`이 있는 요청에 한해 `session.verify_ownership()`으로 저장된 `user_id`와 대조 — 다르면 403(`session_ownership_mismatch`). `apply()`/`get_session_context()`/`delete_session()`/`ensure_current_context()`와 각 라우트까지 배선. `routes/state.py`가 `principal`을 선언만 하고 쓰지 않던 배선 공백을 함께 닫음 |
| 2026-08-24 | D-074 신설 — 만료된 익명 세션·이력 정리(TP-134). `agent_states.last_active_at` 기준 30일(조정 가능) 이상 미사용 세션을 `agent_states`/`recommendation_histories`/`condition_change_logs`/`trace_records` 네 테이블에서 함께 삭제. append-only 두 테이블에 세션 단위 일괄 삭제(`delete_change_logs`/`delete_traces`)를 처음 추가 — 개별 레코드 수정·선택 삭제는 여전히 불가. 삭제 순서는 자식 테이블 먼저, `agent_states` 마지막(중간 실패해도 다음 실행이 재시도 가능하도록). 실행은 Supabase pg_cron 대신 `scripts/cleanup_expired_sessions.py` 수동/외부 스케줄 스크립트로 구현(`--dry-run` 지원). `auth.users` 익명 계정 정리는 FK가 없어(D-063 결정 4) 독립적으로 실행 가능하다고 보고 이번 범위에서 제외 |
| 2026-08-24 | D-075 신설 — LangGraph 노드가 별도 asyncio 태스크에서 도는 탓에 `llm_execution` ContextVar를 값 교체로 갱신하면 노드 안 기록이 유실되던 문제를 수정. 리스트를 하나 두고 `append`하는 방식으로 바꿔 태스크 경계를 넘게 했다. 유실 지점 3곳(조기 반환 정상 응답, 노드 안 LLM 실패의 502 본문, 파이프라인 앞 노드→뒤 노드)이 한꺼번에 해소. 감사 패널의 "LLM 폴백"이 빈칸이 아니라 **틀린 "없음"**을 찍고 있었던 것이 이 문제를 무시하기 어렵게 만든 지점이다. 검토 문서가 제안한 `default` 제거는 채택하지 않음 — 전역 AppError 핸들러가 reset 없는 문맥에서 이 값을 읽어 502 계약이 500으로 깨진다. 기록하는 LLM 더블로 회귀 테스트 9건 추가(Fake는 `record_llm_call()`을 부르지 않아 수정 전에도 통과한다). pytest 2,332건 통과 |
| 2026-08-24 | D-076 신설 — `thinking_budget=0`을 거부하는 모델에 thinking 설정을 아예 싣지 않던 방어를 제거하고, `0`을 항상 `thinking_level=MINIMAL`로 바꿔 보내도록 정리. 2026-08-18에 fast 모델이 그 목록에 있는 `gemini-3.5-flash-lite`로 바뀌면서 분류·조건 추출의 thinking 끄기가 조용히 무효화돼 있었고, 코드가 아니라 모델만 바뀐 것이라 6일간 아무도 몰랐다. 실 API 전수 측정(모델 5개 × 설정 4개 × 3회)으로 거부되는 것은 숫자 `0`뿐이고(`512`는 전부 성공) `MINIMAL`은 실제로 생각 토큰이 0임을 확인했다. 거부 모델 목록과 `gemini-2.5-flash-lite` 512 보정은 실측 근거라 지우지 않고, 목록은 불변식 테스트가 직접 읽는다. **지연 이득은 없다** — 6회에서 -17%까지 나왔지만 15회로 늘리면 -0.9%로 사라진다(표본 부족으로 없는 효과를 읽은 사례). 근거는 속도가 아니라 모델 교체 시 최적화가 조용히 사라지는 구조의 제거다. 폐지된 `LLM_MODEL_NAME`을 현행으로 안내하던 문서 2곳도 함께 정정 |
| 2026-08-25 | D-077 신설 — 무장애 여행 정보(`KorWithService2`) 적재. places 컬럼이 아니라 전용 테이블 `place_barrier_free`로 나누고, 응답 28필드 중 채움률 5% 이상인 15개만 담는다(4개 구 427건 실측). 컬럼 이름은 응답 키가 아니라 의미로 짓는다 — `wheelchair`는 출입이 아니라 대여, `exit`는 출구가 아니라 주출입구라 키를 그대로 믿으면 뜻이 뒤집힌다. 무장애 정보가 있는 장소는 places의 19%뿐이라 구별 목록 1회로 대상을 좁히고(종로구 842회 → 182회), 목록을 먼저 부르고 거기 있는 장소만 행으로 만든다 — 반대 순서로 하면 종로구 첫 적재 754행 중 590행이 "목록에 없더라"는 빈 행이 된다. 값이 전부 빈 행은 남긴다(4개 구 60건, 전부 쇼핑몰 입점 매장이라 레코드만 만들어지고 항목이 미입력이다). 대상은 상세조회 대상이 아니라 TTL로 고른다 — 변경분만 따라가면 이미 DB에 있던 2,600여 건이 영영 대상이 되지 않는다. 숙박(32)과 그 전용 필드 `room`은 제외. `place_enrichments.official_facts`에 담는 안은 채택하지 않았다(사람이 검증한 값의 계보가 무너진다) |
| 2026-08-24 | D-078 신설 — 만료된 익명 계정(`auth.users`) 정리(D-074 후속, [B] auth.users 정리). `created_at` 기준 30일(조정 가능) 이상 지난 익명 계정(`is_anonymous=true`)을 Supabase Auth Admin API로 조회·삭제. PostgREST가 아니라 GoTrue Admin API(`apikey`+`Authorization: Bearer` 둘 다 필요)를 쓰는 별도의 작은 `AuthAdminClient` 신설. `backend/scripts/cleanup_anonymous_users.py`(`--days`, `--dry-run`)로 구현, D-074의 세션 정리 스크립트와는 완전히 독립적으로 실행(FK 없음, D-063 결정 4) |
| 2026-08-25 | D-079 신설 — 피드백 통계를 dev-ops 패널에서 볼 수 있게 함(TP-146). `GET /feedback/stats` 신규 — rating별 건수, reason_code별 건수(dislike만, 표준 7개 + `unclassified`), intent별 건수(상위 N + 롱테일 `other_intent_count` + `missing_intent_count`)를 반환. 집계는 PostgREST group-by가 아니라 Python에서 하며, `StateStore.list_feedback_for_stats(since, until)`을 신설(rating 안 가리고 limit 없이 전량 반환). 프론트는 `api/feedback.ts`에 `fetchFeedbackStats()`, `FeedbackStatsPanel`을 신설해 기존 ApiUsagePanel/PlaceSyncPanel/DbStatusPanel과 같은 패턴으로 `DeveloperOpsPage`에 배선 — API만 추가하고 화면을 안 붙이면 "쌓이는데 아무도 안 본다"는 이번 카드의 문제의식을 반복하게 되어 백엔드+프론트를 한 카드로 묶었다. LLMOps Trace 조회 API는 다른 도메인이라 별도 카드로 분리 |
| 2026-08-25 | D-080 신설 — LLMOps Trace 조회를 dev-ops 패널에서 볼 수 있게 함(TP-157, D-079 후속). `GET /trace/stats` 신규 — 등장한 step만 담는 step별 집계(건수, 평균/최대 latency_ms, 에러 건수)와 최근 에러 목록(상위 N건)을 반환. 집계는 D-079와 동일하게 Python에서 하며, `StateStore.list_traces_for_stats(since, until)`을 신설(세션을 가리지 않고 전체 테이블 대상). 프론트는 `api/trace.ts`에 `fetchTraceStats()`, `TracePanel`을 신설해 기존 패널들과 같은 패턴으로 `DeveloperOpsPage`에 다섯 번째 패널로 배선 |
| 2026-08-25 | D-081 신설 — TP-157 브라우저 테스트 중 발견한 버그 수정. `list_traces_for_stats`/`list_feedback_for_stats`가 PostgREST 기본 1000행 응답 상한에 걸려 있던 문제를 `_fetch_all_rows()` 페이지네이션 헬퍼로 해결(limit/offset 반복 조회). "전체 실행"이 정확히 1000으로 뜨는 것이 단서였다 |
| 2026-08-25 | D-082 신설 — Package D 소유 테이블 `place_embeddings`의 HNSW 인덱스가 프로덕션 DB에서 누락된 것을 발견해 마이그레이션으로 복구. 2026-08-20 중구 RAG 확장 실험 당시 statement_timeout 우회를 위해 지운 뒤 재생성하지 않은 것으로 추정 |
| 2026-08-25 | D-083 신설 — 서비스 지원 지역을 4개 구(종로·중·용산·성동)에서 12개 구로 확장(PR #224 후속). Supabase `places`에 이미 적재돼 있던 광진·동대문·중랑·성북·강북·도봉·노원·은평 8개 구를 `SUPPORTED_DISTRICTS`에 추가 — district_code는 실제 주소와 대조해 확인, 경계 파일은 이미 25개 구를 다 담고 있어 손댈 필요 없음. 활성 장소 1,103건 폴리곤 대조로 밖 7건(0.63%) 확인, 그중 3건은 서로 다른 구에서 정확히 같은 깨진 좌표(19.694, 117.993) — 결측치 대체값으로 추정. `_LOCATION_REQUIRED_QUICK_PICKS`가 여전히 "종로구 한정" 전제로 남아 있는 것은 확인만 하고 범위 밖으로 남김 |
| 2026-08-26 | D-084 신설 — 서울시 실시간 지역 목록을 JSON으로 옮기고 조회 경로별로 맞는 목록에 연결(TP-141). "경복궁 붐벼?"에 북촌한옥마을이 대신 나가던 문제를 서울시 공식 매뉴얼로 재조사한 결과, "82곳 목록이 낡은 것"이 아니라 "인구 조회에 상권 전용 82개 목록을 잘못 가져다 쓴 것"이었다 — 인구 API(`citydata`/`citydata_ppltn`)는 처음부터 121곳, 상권 API(`citydata_cmrcl`)는 가맹점 수가 적은 39곳(공원 33곳 등)을 구조적으로 제외한 82곳만 지원한다(매뉴얼 36p). 두 파일(`population_areas_121.json`/`commercial_areas_82.json`, 서울 열린데이터광장 공식 파일 기반)로 분리하고 로더가 매뉴얼 표와 카테고리 개수까지 대조 검증한다. 인구 혼잡도·citydata 통합 조회는 121개를, 상권 조회는 82개를 쓴다. 121개 목록도 서울시가 계속 확대할 예정이라(매뉴얼 48p) 최근접 대체 시 실제 이름을 한 번 더 조회하는 낡음 감지 probe를 추가 — 응답은 안 바꾸고 개발자 화면 배너로만 알린다. TP-141 원안(82곳 유지, 121 확장은 A로 이관)에서 매뉴얼 근거로 벗어난 부분은 A 리뷰를 요청한다 |
| 2026-08-26 | D-085 신설 — 서비스 지역 밖 안내에서 구 목록을 본문과 분리해 각주로 뺀다. D-083으로 지원 구가 12개로 늘며 "종로구·중구·...·은평구의 장소 추천만 가능해요" 문구가 길어진 문제 — `AgentResponse.message_footnote` 필드를 신설해 본문은 "이 위치는 지금 서비스 지역이 아니에요"로 짧게 고정하고, 구 목록은 화면이 작고 옅은 글씨로 따로 보여준다. `compose_chat_message()` 시그니처는 손대지 않고 `AgentResponse` 조립 지점(agent_runtime.py) 2곳에서 각주만 계산해 끼워 넣었다 |
| 2026-08-26 | D-086 신설 — 서비스 지원 지역을 12개 구에서 16개 구로 확장. 2026-08-26 place-sync로 새로 적재한 서대문·마포·양천·강서구를 `SUPPORTED_DISTRICTS`에 추가 — D-083과 같은 구조로 한 줄씩만 늘렸다. 공식 면적 대조(위키백과 infobox)로 폴리곤 오차 1% 이내 확인, 활성 장소 1,019건 중 밖으로 나온 4건 중 3건은 D-083에서 발견한 것과 같은 깨진 좌표(19.694, 117.993) — 누적 여섯 번째로 재현. 망원역·마포구를 "지원 밖" 예시로 쓰던 기존 테스트 3건을 영등포구 예시로 교체. 구로·금천·영등포구는 아직 place-sync 전이라 이번 확장에서 제외(데이터 없는 구를 지원 목록에 넣으면 추천이 항상 0건). 은평구 141건·강서구 19건 상세정보 백필은 TourAPI 일일 한도 소진으로 다음 실행으로 미룸 |
| 2026-08-26 | D-087 신설 — 장소 사진으로 "분위기가 비슷한 곳"을 찾는 이미지 임베딩 도입(TP-162·TP-163, C). `google/siglip2-base-patch16-224`로 장소 631곳·사진 2,263장을 임베딩해 테이블 둘(`place_image_embeddings`·`place_mood_vectors`)에 나눠 담고, 분위기 축 여덟 중 다섯을 켰다 |
| 2026-08-26 | D-088 신설 — TourAPI "관광지별 연관 관광지 정보"를 종로구·중구 파일럿으로 수집·매칭·적재(패키지 경계 밖 실험). `place_concentration_mappings`(D-043/D-057)와 동일한 보수적 매칭 원칙 재사용, `place_associations` 테이블 신설(월별 스냅샷 이력 보존). PostgREST 1000행 상한 버그가 두 번째로 재발(D-081과 동일 패턴)한 것을 발견해 수정. 원본 2,300건 → 매칭 354/1,086건 → 엣지 698건 실제 적재까지 확인 |
| 2026-08-26 | D-088 확장 — 서비스 지원 12개 구(D-083) 전체로 수집·매칭·적재 범위 확대. 코드 변경 없이 `--districts`만 넓혀 같은 base_ym으로 재실행, 기존 종로구·중구 행은 upsert로 덮어쓰고 나머지 10개 구 신규 추가. 원본 5,511건 → 매칭 666/2,344건 → 엣지 1,612건 실제 적재. 매칭률(29.3%)이 파일럿(30.3%)과 거의 동일하게 유지됨을 확인 |
| 2026-08-26 | D-089 신설 — "성수동"처럼 지역 검색에 상호명만 잡히는 동 이름은 Geocoding으로 폴백한다. "성수동 카페 추천해줘"가 종로구 랜드마크 되묻기로 빠지던 버그 — 지역 검색이 뭔가(애매한 결과라도)를 돌려주면 그 아래 별칭/Geocoding 폴백 사다리가 아예 실행되지 않던 게 원인. `_lookup_local_search()`에서 역/명소 후보도 정확히 같은 이름의 후보도 없을 때만 `None`을 반환해 execute()의 기존 Geocoding 사다리로 넘긴다. 실제 Naver API 호출로 "성수동" 지역 검색은 카페·식당 상호명뿐임을, Geocoding은 좌표로 정상 해석됨을, 그 좌표가 기존 기본 검색 반경(2.0km) 안에 성수역·주변 카페를 다 포함함을 확인 — 검토했던 "가까운 지하철역 버튼"(역 데이터 없음)과 "구 전체로 넓혀 검색"(실측 결과 불필요)은 기각 |
| 2026-08-26 | D-090 신설 — 실시간 혼잡도 카드에 단계별 색상·게이지·전망 인사이트 추가. 인구/집중률 예측 막대그래프가 항상 단색이던 것을 레벨별 4단계 팔레트(emerald→amber→orange→red)로 바꾸고, 현재 단계를 보여주는 `CongestionLevelGauge`를 신설. 공용 컴포넌트(`CongestionForecastBars.tsx`)로 분리해 요약 카드뿐 아니라 상세 모달(`RecommendationDetailPreviewModal`)에도 처음으로 노출 — 기존엔 모달에 이 그래프가 아예 없었다. 향후 예측에서 가장 붐비는 시간대를 "N시간 후 가장 붐빌 예정" 한 줄로 요약하는 `_summarize_population_peak()`을 추가해 `population_peak_forecast_summary`로 새로 내려줌 — 관측·예측 시각을 실제 파싱해 시간 차를 구하고(인덱스 가정 안 함), 채팅 말풍선 텍스트는 기존 회귀 테스트 보호를 위해 그대로 둠. 과거 추이·현재 인구 수 실측치는 서울시 API 미제공/참고 이미지 미노출로 이번 스코프에서 제외. (후속) 예측 그래프가 "현재 시각부터만" 보여 기준점이 안 보인다는 지적에, 실제 과거 데이터 폴링 파이프라인 구축(큰 작업)은 보류하고 대신 예측 막대 맨 앞에 점선 구분선·강조 테두리를 준 "현재" 막대를 추가해 시각적 기준점만 뒀다 |
| 2026-08-26 | D-091 신설 — SCHEDULE에 place_associations "함께 방문된 이력"을 opt-in으로 연결(B 단독 구현). `co_visited_fetcher` 키워드 인자 미지정 시 기존 동작과 바이트 단위로 동일. agent_runtime.py(A) 배선, SchedulePartialFillRequest 연동까지 이어서 완료. RECOMMEND 2차 스코어링 연동은 범위 밖 — 별도 카드(D-092)로 분리 |
| 2026-08-26 | D-092 신설 — RECOMMEND 2차 스코어링에 place_associations "함께 방문된 이력"을 반영(D-040 `rerank_with_concentration()` 패턴 재사용). `scoring.OPTIONAL_FEATURES`에 `co_visited` 추가(taste+concentration과 동시 활성 시 정확히 설계 최대치 3개를 채움), `rerank_with_co_visited()`/`co_visited_score()`/`_co_visited_sentence()` 신설. 실제 `pytest` 전체 스위트를 로컬 shim으로 실행해 전부 통과 확인(StrEnum shim 보강으로 이전 세션에서 "3.11 전용 문법 때문에 실행 불가"로 남겼던 제약을 해소) |
| 2026-08-26 | D-093 신설 — 지하철 방향 충돌 버그 수정, 주차 공영/민영 그룹핑, 도로소통 신규 연결. 지하철 "종로구만 되는 것 같다"는 지역 제한이 아니라 요약 카드 `fields` 키가 "역이름 호선"뿐이라 같은 역의 상행/하행이 충돌해 지워지던 버그였다(방향까지 키에 포함해 수정). 주차 공영/민영 구분(`PRK_TYPE`: NW/NS=공영, BS/NP=민영)과 도로소통(`ROAD_TRAFFIC_STTS.AVG_ROAD_DATA`: 단계·속도·안내문구)은 새 API 연동 없이 이미 매번 호출하는 `citydata` 응답에 있던 걸 파싱만 추가 — 실측(교대역·강남역·홍대·이촌한강공원)으로 코드값·중복 레코드 패턴을 확인했다. 별도 데이터셋(`GetParkInfo`)도 실제 호출해봤지만 낡고 범위가 안 맞아 기각. `question_type_rules.md`를 v3.2.0으로 올려 주차 트리거를 시제 키워드 없이도 매칭되게 완화하고 `realtime_traffic` 유형을 신설 — 스키마(`InfoQuestionType`)에 값을 안 넣은 채로 프롬프트만 먼저 바꿨더니 신규 케이스가 전부 0%로 실패했다가, 스키마에 추가한 뒤 재실행하니 기존 21건 회귀 없이 신규 포함 23건 100%로 통과했다(실제 Gemini 호출) |
| 2026-08-26 | D-094 신설 — 분위기 임베딩을 조회 계층에 연결한다(D-087 후속, C). D-087이 범위 밖으로 미룬 서비스 배선 중 **조회까지**를 붙였다 — 재정렬과 발화에서 축을 고르는 단계는 D 패키지·A 패키지 소관이라 이번에도 손대지 않았다. 계약 `PlaceMoodRepository`는 취향 근거(`PlaceEvidenceRepository`)와 나눴다. 둘 다 768차원이지만 한쪽은 한국어 문장, 다른 쪽은 사진이 사는 공간이라 한 계약에 두면 호출부가 좌표계를 헷갈릴 수 있다. 경로가 둘이고 비용이 크게 다르다 — `find_mood_profiles`(발화)는 미리 계산된 `axis_scores`만 읽어 **임베딩 모델이 필요 없고**, `search_place_mood`(사진)만 SigLIP을 요구한다. 그래서 인코더가 없어도 Provider는 만들고 `photo_search_available`로 사진 경로만 막는다 — 인코더가 없을 때 0으로 채운 벡터를 넘기면 유사도가 전부 같아져 아무 장소나 순서대로 돌아오고 그게 추천으로 나간다(D-042). RPC `search_place_mood`는 `search_place_evidence`와 **후보 규칙이 다르다**. 저쪽은 40,389행이라 좁히지 않으면 6~9초가 걸려 좁힘을 강제하지만, `place_mood_vectors`는 장소당 한 행이라 지금 631행·서울 전체로 넓혀도 6,000~10,000행이다. 그래서 후보가 `null`이면 전체 검색을 허용하고("이 사진과 닮은 곳 아무데나"가 실제로 있을 수 있는 질문이다), **빈 배열은 `null`과 다르게 0건으로 끝낸다** — 후보를 좁히려다 전부 걸러진 호출이 전체 검색으로 둔갑하면 지역 필터를 통과하지 못한 장소가 추천에 섞인다. 후보를 넘길 때는 배열이 HNSW를 무력화하므로 저쪽과 같은 500건 상한을 둔다. **유사도 컷은 0.0으로 두고 순위만 쓴다** — 축 점수는 사람 정답표 77곳으로 AUC를 쟀지만(D-087) 사진끼리의 "이 정도면 닮았다" 경계는 표본이 없어서, 근거 없는 숫자를 코드에 남기지 않는다. 발화 경로 조회는 `embedding` 컬럼을 빼고 읽는다(768 float × 장소 수면 응답이 수 MB가 된다). 선택 의존성은 `[embeddings]`(취향, sentence-transformers)와 `[mood]`(사진, transformers·torch·pillow)로 나눴다 — 취향만 켜는 배포가 SigLIP까지 받을 이유가 없고, torch는 양쪽이 공유해 둘 다 깔아도 한 벌만 받는다. 스위치는 `PLACE_MOOD_ENABLED`(기본 off)이고, 켰는데 Supabase 설정이 비면 부팅을 막지 않고 경고만 남긴다 — 순위를 다듬는 축이라 없어도 추천은 동작한다. **분위기 벡터가 없는 장소가 정상이다** — 사진 임베딩은 종로구까지만 적재돼 있어(631곳) 다른 구 후보는 결측으로 빠지고, 0점으로 채우면 사진이 없는 장소가 "분위기가 안 맞는 곳"으로 잘못 밀린다. 커버리지는 `place_mood_coverage` 점수로 관측에 올려 적재 범위를 넓힐 시점을 숫자로 알 수 있게 했다 |
| 2026-08-26 | D-095 신설 — 집중률 조회의 구 고정을 해제한다. `enrichment_service`가 `JONGNO_CONCENTRATION_DISTRICT_CODE`("11110")를 장소와 무관하게 넘겨 모든 조회가 종로구로 나가고 있었다. 집중률 API는 `signguCd`로 엄격하게 거른다(실측: 명동성당·덕수궁은 종로구 0건/중구 30건, 경복궁은 반대) — 매핑이 전부 종로구였던 동안만 값이 맞았다. `places.district_code`를 `StoredPlaceLocation`·`ResolvedLocation`으로 이어 날라 대상 장소의 구로 조회한다(3자리 "140" → signguCd 5자리 "11140" 변환은 `concentration_policy.concentration_signgu_code()`). **구를 모르면 종로구로 대신 묻지 않고 조회 자체를 생략한다** — 다른 구 장소는 언제나 0건이라 틀린 조회가 "정보 없음"으로 위장되기 때문이며, D-042와 같은 판단이다. 매핑 적재보다 이 변경이 먼저다: 순서를 뒤집으면 경계 근처 중구 62곳이 지금 받는 값을 잃는다(더 가까운 중구 매핑이 대체 후보 상위 3곳을 차지하는데 셋 다 종로구로 조회돼 0건). `StoredPlaceLocation`이 `domain/models.py`에 있어 D 소유로 보였으나 정의 커밋 `d6ea941`·참조처가 전부 C임을 blame으로 확인해 C 소유로 판정했다(TP-127이 반대로 적어둔 것을 정정) |
| 2026-08-27 | D-096 신설 — 사진 검색을 인텐트·채점 밖의 독립 엔드포인트로 둔다(D-094 후속, TP-175, C). `POST /api/places/similar-by-photo` 신설. 사진은 발화가 아니라 목적이 확정된 입력이라 인텐트를 만들지 않고, 순위를 사진 유사도만으로 정해 `scoring.py`를 건드리지 않는다. `prepare()`까지만 불러 하드 필터는 태운다. 좌표계 일치를 실측으로 확인했고(로컬 CPU 재계산이 코랩 GPU 적재값과 소수점 넷째 자리까지 일치), 예열 뒤 응답 1.2초다. 후보가 최대 20곳이라는 한계가 남아 순서를 뒤집는 후속 작업이 필요하다 |
| 2026-08-27 | D-097 신설 — 오늘 혼잡 질문에서 저장소 장소가 위치 해석에서 탈락하지 않게 한다(TP-171). "명동성당 붐벼?"가 위치 해석 단계(REALTIME_CITYDATA가 저장소 조회를 건너뜀)에서 실패해 집중률 폴백까지 못 가던 문제 — 카드는 `current_population_candidate`를 REALTIME_CITYDATA 조건에서 빼자고 제안했지만, 그대로 구현하면 지원 16개 구 밖의 실시간 인구 허브(강남역·교대역·여의도 등, 실측: 121곳 중 49곳·82곳 중 32곳)가 지역 제한에 걸려 깨지는 회귀를 실측으로 발견했다. `ResolveLocationQuery`에 `enforce_service_area` 명시 오버라이드를 추가해 저장소 우선순위와 지역 제한을 분리하는 방식으로 수정했다. 위치 해석이 완전히 실패해도 집중률 매핑 이름과 정확히 하나만 일치하면 답하는 플랜 B 폴백도 함께 추가(사용자 결정). 실제 API 8개 장소 조회로 안전 조건과 회귀 없음을 확인 |
| 2026-08-27 | D-088 확장(2차) — place_associations 수집 범위를 서비스 지원 12개 구에서 16개 구로 확장(D-086으로 늘어난 서대문·마포·양천·강서 4개 구 반영). 코드 변경 없이 `--districts`만 넓혀 재실행, 기존 12개 구는 upsert로 유지되고 4개 구가 새로 추가됨. 원본 7,219건 → 매핑 CSV 751건 → 엣지 2,001건 실제 적재(미매칭 5,110 / 자기참조 15 / 중복 93 제외). 매칭률 27.7%로 파일럿·1차 확장과 같은 추세 유지 |
| 2026-08-27 | D-098 신설 — 사진 검색을 채팅창에 붙이고 대화가 잡은 위치를 이어받는다(D-096 후속, TP-175, C). 입력창 왼쪽 "+" 버튼 → 사진/갤러리. `session_id`로 B의 누적 조건에서 `search_center` → `current_location` 순으로 위치를 찾고 없으면 GPS로 떨어진다. 위치가 안 풀리던 원인이 둘이었다 — 세션을 안 본 것과 `ResolveLocationTool`에 지오코딩만 넘겨 "안국역" 같은 장소명을 못 푼 것. 사진은 320px data URL로 담고(object URL은 새로고침에 무효), 결과는 가로로 늘어놓아 분위기를 한눈에 견주게 한다. 유사도는 숫자로 보여주지 않고 카드를 눌러 상세로 확인한다. 후보가 10곳 상한이라 사진마다 결과가 같은 문제는 TP-176으로 남는다 |
| 2026-08-27 | D-099 신설 — 사진 검색은 순위를 먼저 매기고 상위 N곳만 상세를 확인한다(D-098 후속, TP-176, C). 후보를 모으고 줄 세우던 순서를 뒤집었다 — 사진 유사도는 DB 안에서 끝나 공짜이고 비싼 것은 TourAPI 상세 조회라, "어차피 보여줄 곳"에만 값을 치른다. `search_place_mood`에 좌표·반경 인자를 더하고(하버사인 직접 계산, PostGIS 없음) 상세는 `get_active_place_details`로 DB에서 읽는다. 하드 필터는 `prepare_recommendation_from_context()`를 그대로 재사용해 추천과 판정이 갈리지 않게 했다. 안국역 반경 2km 실측으로 후보 7 → 40곳, 응답 1.2 → 0.65초, TourAPI 10 → 0회. 사진마다 다른 결과가 나온다(상가·공원·한옥건물로 갈림) |
| 2026-08-27 | D-100 신설 — INFO 장소 되묻기에 편집거리 매칭·후보 버튼·상태 저장 이어받기 추가. "성수 카페거리 주차장 정보"가 INFO에서 버튼 없이 되묻던 버그 — 지역 검색이 실제로 "성수동카페거리"(명소 카테고리)를 찾아주는데 "동" 한 글자 차이로 후보를 못 좁혔고(→ 역/명소류만 편집거리 1 이내면 채택하는 4번째 매칭 단계 추가), `agent_context/service.py`가 찾은 후보를 `candidates=[]`로 항상 버리고 있었다(→ 실제 후보를 담고, question_type별 가용성(집중률 매핑/실시간 인구·상권 반경/저장소 존재)으로 필터링). INFO도 RECOMMEND처럼 `NEEDS_CLARIFICATION` + 버튼으로 뜨게 했고, INFO는 question_type 등 원래 질문을 세션에 저장하지 않아 버튼 클릭 시 재분류되던 문제를 `AgentState.pending_info_context` 신설로 해결(사용자 결정: 재분류 대신 상태 저장). Supabase `agent_states`에 `pending_info_context` 컬럼 마이그레이션 포함 |
| 2026-08-27 | D-101 신설 — 공영/시영주차장을 명시한 질문을 `realtime_public_parking`으로 분리. `GetParkingInfo`의 구 단위 최신 주차 대수와 한 번 지오코딩한 좌표 카탈로그를 연결하고, 일반 근처 주차장(`realtime_parking`)은 기존 도시데이터의 공영·민영 혼합 목록을 유지 |

| 2026-08-27 | D-102 신설 — 답변 뒤에 후속 질문을 버튼으로 제안한다(C). 턴이 끝난 뒤 `follow_up.suggest` 프롬프트로 다음 발화 후보 0~3개를 받아 `AgentResponse.suggested_follow_ups`에 싣고, 버튼을 누르면 **그 문구를 그대로 `user_input`으로 재전송**한다 — 되묻기 버튼(D-053 계열, `clarification_choice`로 Intent를 못 박음)과 반대 방향이라 같은 메커니즘을 쓰지 않는다. 답변 생성 LLM 호출에 필드를 얹지 않고 별도 호출로 뺀 이유는 답변 경로가 인텐트마다 다르고(GENERAL/INFO/COMPARE만 LLM 자유 생성, RECOMMEND는 고정 wrapper, SCHEDULE·OUT_OF_SCOPE는 템플릿) 그중 셋이 텍스트를 스트리밍하기 때문 — 같은 호출에서 JSON을 받으려면 스트리밍을 포기해야 한다. SSE 경로에서는 답변이 이미 화면에 다 뜬 뒤에 도는 호출이라 체감 지연이 늘지 않는다. 모델이 없는 기능을 권하지 않도록 `follow_up/capability_rules.md`에 실제 처리 가능한 요청 목록을 싣고, 개수·길이·중복·직전 발화 반복은 `follow_up_suggester.py`가 코드로 다시 검사한다(프롬프트 지시는 부탁, 코드 검사가 계약). 되묻기 턴과 OUT_OF_SCOPE 턴은 제안하지 않고, 호출이 실패하면 빈 목록으로 낮춘다 — 이미 확정된 답변을 버튼 때문에 실패시키지 않는다 |
| 2026-08-27 | D-102 후속 — 후속 질문을 `done` 뒤 별도 SSE 이벤트로 뺀다. Runtime 안에서 만들면 그 호출이 끝날 때까지 `done`이 안 나가는데, 그 시간에는 답변과 카드가 이미 화면에 다 떠 있어서 그 아래 로딩 말풍선이 한 번 더 뜬 것처럼 보였다(실사용 지적). `run_agent(generate_follow_ups=False)`로 SSE 경로만 생성을 끄고, 라우트가 `done`을 먼저 내보내 턴을 끝낸 뒤 `follow_ups` 이벤트를 이어 보낸다 — 화면은 로딩을 감추고 입력창을 푼 상태에서 버튼만 늦게 받는다. 답변 LLM 호출에 필드를 얹는 안은 버렸다: GENERAL·INFO·COMPARE는 답변을 스트리밍해서 같은 호출로 JSON을 받으려면 스트리밍을 포기해야 하고, RECOMMEND·SCHEDULE은 답변이 고정 문구·템플릿이라 얹을 호출이 아예 없다. 단발 `POST /api/chat`은 나눠 보낼 스트림이 없어 지금처럼 응답 안에 담는다. 후속 질문 입력은 번역 전 한국어 사본(`runtime_response`)을 쓰고 결과만 다시 영어로 옮긴다 |
| 2026-08-27 | D-103 신설 — Gemini 타임아웃을 전송 계층과 무관하게 잡는다. INFO 답변 스트림이 타임아웃하자 모델 폴백도, `AppError` 변환도 못 하고 턴 전체가 죽었다(C가 이미 가져온 장소 정보까지 함께 버려졌다). 원인은 `except httpx.TimeoutException`이 **죽은 코드**였다는 것 — google-genai는 `aiohttp`를 임포트할 수 있으면 그쪽으로 요청을 보내는데(`_use_aiohttp()`), aiohttp는 이 프로젝트의 의존성이 아니라 환경에 따라 다른 패키지(kubernetes, langchain-community)가 딸려 들여올 뿐이라 **어느 전송 계층으로 나가는지가 머신마다 다르다.** aiohttp는 `asyncio.TimeoutError`를, httpx는 `httpx.TimeoutException`을 던지고 둘은 상속 관계가 없다. aiohttp가 없는 환경에서는 같은 코드가 멀쩡히 돌아서 테스트로도 CI로도 드러나지 않았다. `_TIMEOUT_ERRORS = (httpx.TimeoutException, TimeoutError)`로 두 곳(`_stream_text`, `_run_attempts`)과 음성 전사(`gemini_audio.py`)를 함께 고치고, 테스트를 전송 계층별로 파라미터화해 aiohttp 예외에서 실제로 재시도·폴백이 도는지 잠갔다(수정 전 `[aiohttp]` 4건 실패·`[httpx]` 4건 통과로 확인). 공유 httpx 클라이언트를 쓰는 나머지 15곳의 handler는 그대로 둔다 — 그쪽은 전송 계층이 확정돼 있다. **남은 문제 2건은 이번 범위 밖:** (a) SDK가 `HttpOptions(timeout=)`을 aiohttp `ClientTimeout(total=)`로 넣어 10초가 응답 전체에 걸린다 — 정상 스트리밍도 10초를 넘기면 중간에 끊긴다. (b) 스트림 도중 실패 시 이미 내보낸 텍스트 뒤에 고정 안내문이 통째로 덧붙는다(기존 APIError 경로에서도 일어나던 동작) |
| 2026-08-27 | D-104 신설 — 숫자 없는 시간 표현을 `time_available` 분 단위로 고정 환산(TP-177, B). 같은 "반나절"이 발화에 지명이 있으면 240, 없으면 360으로 갈리고 "하루 종일"은 null로 떨어지던 것을 `recommend.extract` 2.4.0 → 2.5.0으로 닫았다 — 반나절/오전·오후 내내 240, 하루 종일 480, 잠깐 120, 범위 표현은 하한, 목록 밖은 null. 값은 `build_schedule_planning_instruction()`의 미지정 폴백("3~4시간 내외")과 일관되게 맞췄다. 흔들림·응답 모델·기대 일치를 따로 재는 `verify_schedule_condition_extraction.py`를 먼저 만들어 기준선을 확정한 뒤 프롬프트를 고쳤고, 그 과정에서 모델 폴백·모델 티어·`location_rules` 미커버·조건 병합 유실 네 가설을 모두 기각했다. 전후 비교 14/14 고정·기대 일치. SCHEDULE 전용 추출 슬롯은 신설하지 않았다("반나절 = 4시간"은 RECOMMEND에도 맞는 해석) |
| 2026-08-27 | D-105 신설 — 끝나지 않던 장소 되묻기를 고친다(C). "운현궁 주차장 있어?"는 답이 나오는데 이어진 "근처 공영 주차장 자리 있어?"가 `여러 장소 중 어느 곳을 말씀하시는 건가요?`로 끝나고, "운현궁"이라고 답하면 같은 되묻기가 무한히 반복됐다. 원인은 두 겹이다 — (1) 네이버 지역검색이 "운현궁"에 **이름이 완전히 같은 후보를 3건**(중식당·궁궐·한식당) 돌려주는데, `_select_local_search_candidate`의 정확 일치 단계가 2건 이상이면 무조건 재질문으로 떨어뜨렸다. 식당·상점은 위치 후보로 쓰지 않는다는 규칙(`_is_location_pickable`)이 이미 있었지만 **되묻기 목록을 만들 때만 쓰이고 고르는 단계에서는 안 쓰였다.** (2) 그래서 뜬 되묻기는 버튼이 "운현궁" 하나뿐이었고, 그걸 누르면 같은 문자열로 같은 조회가 다시 돌아 같은 화면이 나왔다 — **답이 질문과 같아 입력이 하나도 바뀌지 않는다.** 정확 일치가 여러 건이면 pickable로 한 번 걸러 하나만 남을 때 그것을 고르고(걸러도 2건 이상이면 그대로 재질문), 후보 하나의 이름이 질의와 같으면 되묻지 않고 그것으로 해결한다. **후보 이름이 질의와 다르면 그대로 되묻는다** — "종각역"에 후보가 "종각역 1호선" 하나뿐인 경우는 답이 질의와 달라 다음 턴에 풀리므로, 첫 후보를 임의로 고르지 않는다는 이 파일의 원칙을 지킨다(넓게 고쳤다가 기존 테스트 2건이 깨져 좁혔다). 첫 질문만 됐던 이유는 `question_type`에 따라 목적이 갈려서다 — `parking`은 PLACE_IDENTITY라 저장소에서 `서울 운현궁`을 찾지만, `realtime_public_parking`은 REALTIME_CITYDATA라 저장소 조회를 건너뛴다(execute():385). 그 건너뛰기 자체는 그대로 두었다. **남은 문제:** 이름이 같은 서로 다른 장소가 둘 이상이면 여전히 되묻고 그 되묻기는 여전히 반복될 수 있다 — 저장소에도 제목이 같은 행이 20건 넘게 있다(`익선동 한옥거리`, `커먼그라운드` 등). "같은 이름의 다른 장소를 무엇으로 가를 것인가"가 정해져야 풀리는 별개 문제다 |
| 2026-08-28 | D-106 신설 — 인텐트 분류·조건 추출 구간에 SSE 하트비트를 붙이고, 나머지 추출 4곳에 thinking_budget=0을 맞춘다(TP-179). "가끔 답변이 매우 느릴 때가 있다"는 체감 보고를 조사한 결과, D-066이 범위 밖으로 남긴 4곳(`extract_modify_conditions`/`extract_info_query`/`extract_compare_request`/`extract_general_request`)에 `thinking_budget=0`을 걸어도 실측상 지연 차이가 거의 없었다(fast 모델은 설정 없이도 이미 가벼움, D-076과 같은 패턴) — 진짜 원인은 `classify_intent()`+`extract_*()`(순차 LLM 호출 최대 2번)가 SCHEDULE과 달리 SSE 하트비트로 감싸지지 않아, 외부 API 꼬리 지연이 걸리면 그 구간이 무응답으로 멈춘 것처럼 보이는 것이었다. `build_interpretation()` 호출을 `_await_with_heartbeat()`로 감싸고(4초 간격), 4곳은 나머지 6곳과 통일해 `thinking_budget=0`을 마저 적용했다(지연 개선이 아니라 D-076류 사고 예방 목적) |
| 2026-08-28 | D-107 신설 — 새 SCHEDULE 턴에서 직전에 보여준 장소를 후보로 되살린다(TP-180, B). 사용자 문의로 발견 — 추천 직후 "이 장소들로 일정 짜줘"가 그 장소를 한 곳도 넣지 않았다. 제외 목록(`recommended ∪ rejected ∪ closed_excluded`)에 직전 추천분이 들어 있는데 SCHEDULE이 후보를 새로 채점하며 그 목록을 그대로 적용해, "이 장소들로"가 "이 장소들만 빼고"로 동작했다. 새 SCHEDULE 턴에 한해 마지막 run의 노출분을 제외 목록에서 빼되, 재조정 턴(REJECT_ALL·REJECT_SPECIFIC)은 `llm_output.modify`로 걸러 거절 이력을 지킨다. 조회·채점 두 단계와 그래프·구 경로 두 호출부 모두에 적용. 실사용 재현에서 추천 5곳 중 2곳 포함(수정 전 0곳), "다른 곳 보여줘"는 0곳 겹침 유지 |
| 2026-08-29 | D-108 신설 — 서비스 지원 지역을 16개 구에서 22개 구로 확장하고(구로·금천·영등포·동작·관악·서초, 활성 1,516건) 그 구의 집중률 매핑을 함께 채웠다(391건 → 504건, 22개 구 전부 조회 성공률 100%). district_code는 순서로 짐작하면 여섯 곳 전부 틀려(530이 영등포가 아니라 구로) 표본 주소로 대조해 확인했다. 폴리곤 면적 오차 0.33% 이내, 활성 장소 밖 0.26%. `DISTRICT_LANDMARKS`의 임포트 시점 assert가 구만 늘리면 앱이 뜨지 않게 막아 바로 드러났다(TP-160 장치). `_OUTSIDE`의 강남역 좌표 (37.4979, 127.0276)가 실제로는 서초구 땅이라 이름과 달리 서초구를 검사하고 있었고, 서초구가 지원에 들어오자 깨져 삼성역·선릉역으로 교체했다. 집중률 API의 "과학전시관"이 개명 전 이름임이 세 구에서 확인돼(→ 융합과학교육원, 분관 → 분원) 2026-08-26에 뺐던 중구 남산분원을 되살렸다. 유사도만 높고 주소가 다른 16건은 붙이지 않았다. D-083·D-086과 달리 지원 확장과 매핑을 한 PR에 담아 "추천에는 나오는데 혼잡도는 답 못 하는" 반쪽 상태를 만들지 않았다 |
| 2026-08-29 | D-109 신설 — 서울 25개 구 전체를 지원 범위로 하고(강남·송파·강동 추가, 활성 1,806건) 집중률 매핑 76건을 채웠다(504건 → 580건). 경계 파일의 25개 구와 지원 목록이 같아졌지만 "파일에 있는 구를 전부 지원"으로 바꾸지 않는다 — 우연히 일치한 것이지 규칙이 바뀐 것이 아니다(D-083에서 두 번 기각). 서울 안에 "밖" 표본이 하나도 남지 않아 `_OUTSIDE`를 인접 경기 시로 방향별(동·서·남·북) 하나씩 다시 짰다. 강남구는 상세 조회가 322/1,133건에서 멈춘 채로 넣는다 — 상세가 없어도 추천·혼잡도는 동작하고 지원 목록에 없으면 후보로 아예 안 나온다. TourAPI 한도가 오퍼레이션 단위임을 재확인했다(오늘 `detailIntro2` 996회 소진으로 강남구 동기화가 `TOUR_DETAIL_QUOTA_EXCEEDED`, 집중률은 전 구 검증 580건으로 별도 소진). 전 구 조회 검증은 415 success·165 unavailable로 미완이며 `no_data`는 0건이다 — 검증이 매핑 건수만큼 호출하므로 이제 전 구 실행은 한도의 절반을 넘게 쓴다 |
| 2026-08-31 | D-110 신설 — 장소 보관함을 추천 이력과 분리된 별도 엔티티(`SavedPlaceList` / `saved_place_lists`)로 두고 담기·빼기 전용 REST를 낸다(SCHEDULE-12, B). 요청은 "추천에서 장바구니 담듯이 저장해서 그 장소들로 일정 짜기"이고, INT-07 "알려진 갭"의 미해소분이다 — D-107이 직전 턴 노출분 복귀까지 닫았지만 `shown_place_ids`는 마지막 run만 담고 "보여준 것"과 "사용자가 고른 것"을 구분하지 않는다. `RecommendationHistory`에 컬럼을 더하지 않은 이유가 핵심이다: 이력은 append-only인데 보관함은 가변이고, 무엇보다 `clear_recommended()`(계약 5.5절 history reset)가 `recommended`·`closed_excluded`를 비워 **"다른 곳 보여줘" 한 번에 담아둔 장소가 함께 날아간다** — 그 함수 계약에 예외 분기를 더하는 쪽이 테이블 하나를 더 두는 쪽보다 나중에 더 비싸다고 봤다. 담기·빼기는 인텐트 분류를 거치지 않는다(버튼 클릭은 해석할 여지가 없고, `/api/chat`을 통하면 오분류·LLM 지연이 붙는다 — `clarification_choice`와 같은 이유). 담을 수 있는 것은 그 세션 노출 이력에 있는 place_id뿐이지만 `get_shown_place_ids()`처럼 마지막 run으로 좁히지 않고 누적 전체를 본다(`find_recommended_item()` 신설) — 화면에 이전 턴 카드가 남아 있어 스크롤을 올려 3턴 전 카드를 담는 것이 정상 동작이고, 좁히면 그 경로가 400으로 막힌다. 담기·빼기는 멱등이며(`changed=False`) 이력의 중복 허용 정책과 달리 항목을 늘리지 않는다 — 보관함은 누적 기록이 아니라 현재 상태라 같은 장소가 두 줄이면 버그다. `items` 순서는 담은 순서이고 의미를 갖는다: 후속 카드에서 보관함이 일정 항목 수 상한을 넘을 때 이 순서로 자른다(점수 순이면 왜 빠졌는지 설명할 수 없다). `name`은 "B는 place_id만 저장한다" 원칙의 예외를 하나 더 쓴다 — SCHEDULE-09에서 같은 근거(지명 재검색 불안정)로 예외가 됐고, 보관함은 여러 턴 뒤에 쓰이는 것이 정상이라 재검색 실패 확률이 `recommended`보다 높다. 세션 삭제와 만료 정리 스크립트가 보관함도 지운다. 후보 복귀·배치 보장·위경도 스냅샷·프론트는 후속 카드로 남는다 |
| 2026-08-31 | D-111 신설 — 추천 후보 상한을 20 → 30, 기본값을 10 → 30으로 올리고 `PLACE_DETAILS_SOURCE` 기본값을 tour_api → supabase로 바꿨다(C). 발화를 바꿔도 같은 곳이 나오는 원인이 후보 폭이었다 — 안국역 기준 후보 10곳은 반경 179m다. D-099가 상향을 기각한 근거 두 개(A/D 공유 정책, TourAPI 속도)가 모두 무너졌다: 소유는 추천 쪽이고, 속도 전제는 후보마다 상세를 부른다는 가정인데 supabase면 배치 1회다(후보 30곳 기준 외부 호출 2회 대 61회). 신선도는 활성 8,007곳 전량이 상세 30일 이내였다. 밤 시간대에 결과 5곳을 못 채우던 것이 관측돼(경복궁 21시 통과 1곳) 상향 근거가 됐다. `MAX_PLACE_PROVIDER_ROWS`는 100 → 300 — 100의 근거 주석('한 페이지 최대')이 틀렸고 실측으로 2000행까지 받는다. tour_api + 높은 한도 조합은 부팅에서 막는다(D-042와 같은 이유, 일일 한도 33요청 소진). SCHEDULE의 D 반환 수가 후보 상한을 따라 10 → 30으로 딸려 올라가 `SCHEDULE_RECOMMENDATION_LIMIT`으로 분리했다 — 그 10은 D 협의값이다. 곁가지로 도보 실측 조회도 후보 수에 정비례함이 드러났다(카카오 7~13건 → 25~35건). 2단 채점으로 분리해야 하며 별도 작업이다 |
| 2026-08-31 | D-112 신설 — 후보 보충 조회가 Context 전체를 다시 부르지 않게 한다(C). 보충 1회가 fetch_context를 통째로 불러 외부 호출 7건이었는데, 그중 6건(날씨·공휴일·위치 해석)은 A가 병합에서 버리는 값이었다. AgentContextRequest에 resolved_search_center를 더해 A가 첫 조회의 기준점을 넘기면 C가 장소만 다시 준다. 위치 해석까지 건너뛰려고 좌표를 넘긴다 — 그 3건이 제일 크고 같은 턴이라 기준점이 바뀌지 않는다. 기존 ToolExecutionPlan에 places_only를 더해 얹었다. 실측 7건 → 2건, 보충 2회가 도는 요청은 21건 → 11건. 첫 요청을 크게 잡아 보충을 없애는 안은 C가 하드 필터 손실을 예측할 수 없어 기각했다 |
| 2026-08-31 | D-113 신설 — 도보 실측을 1차 채점 상위 10곳에만 조회한다(C, TP-103 후속). 후보 전량에 붙이던 것을 1차(직선거리) → 상위 10곳 실측 → 2차(실측 반영) 순서로 바꿨다. 2차 대상을 실측 받은 후보로 한정하는 것이 핵심이다 — _consistent_routes()가 하나라도 실측이 없으면 전부 직선거리로 채점하므로, 좁히지 않으면 실측이 통째로 버려진다. 10인 이유는 5면 실측이 선택에 관여하지 못해서다: 6개 조합 중 3개에서 최종 5곳의 집합이 바뀌었고 안국역 14시는 3곳이 갈렸다. 우회 계수가 1.07~1.71배로 벌어져 직선 기준 순위가 실측에서 뒤집힌다. domain/scoring.py는 안 건드리고 부르는 순서만 바꿨다. 도보 호출 25~35건 → 10건 |
| 2026-08-31 | D-114 신설 — 보관함에 담은 장소를 후보로 되살리고 배치까지 구조적으로 보장한다(D-110 후속, SCHEDULE-12, B). D-110으로 담을 수는 있게 됐지만 담아도 일정에 반영되지 않았다: D-107이 되살리는 `shown_place_ids`는 마지막 run만 담아 3턴 전에 담은 장소는 제외 목록에 남고, 후보 풀에 들어가는 것과 배치되는 것은 다르다(채점에서 밀리면 그대로 빠진다). `_revivable_place_ids()`가 보관함을 합집합으로 더하고, 직전 노출분은 새 SCHEDULE 턴에만·**보관함은 재조정 턴에도** 되살린다 — 사용자가 명시적으로 담아둔 것이라 "두 번째는 별로야"가 나머지까지 뺄 이유가 없다. 거절과의 충돌은 `record_rejected()`가 같은 place_id를 보관함에서 빼서 `saved ∩ rejected = ∅`을 구조적으로 보장하는 쪽으로 닫았다 — 이 처리를 service.py가 아니라 history.py에 둔 이유는 호출부가 두 번 부르는 것을 잊으면 불변식이 조용히 깨지기 때문이고, 덕분에 후보 복귀가 두 목록의 시간 순서를 비교할 필요가 없어졌다(TP-180에서 테스트 4건이 깨졌던 지점이 애초에 안 생긴다). `RecommendedItem`·`SavedPlaceItem`에 위경도를 실어 "place_id만 저장한다" 원칙의 네 번째 예외를 썼다 — 보관함은 담고 나서 여러 턴 뒤에 쓰이는 것이 정상이라 이번 턴 검색 반경 밖일 확률이 recommended보다 높고, 그러면 `_build_pairwise_distances_km()`가 좌표를 못 찾아 강남 장소가 종로 일정 2번째에 꽂혀도 막을 수 없다(C 응답이 있으면 그쪽 우선). `_finalize_recommendation_response()`에 `tool_context` 인자가 붙었다. 배치는 `SchedulePlanningRequest.must_include_place_ids` + 프롬프트(`schedule.plan` 1.2.0, `[반드시 포함]`) + `plan_schedule()`의 하드 검증으로 보장하고, 누락 시 1회 재시도 후에도 빠지면 **결과를 살린다** — `plan_partial_schedule()`의 하드 실패와 다른 선택이며, 저쪽은 유지해야 할 기존 일정이 걸려 있지만 장바구니는 부분 성공이 전체 실패보다 낫다. 대신 `ScheduleResult.omitted_saved_place_names`로 말풍선에 알린다. 개수 충돌은 `target_item_range()` 상한까지 **담은 순서로** 자른다(점수 순이면 왜 빠졌는지 설명할 수 없다). 부분 재편성에는 `must_include`를 넘기지 않는다 — pinned_items가 이미 자리를 붙들고 있다 |
| 2026-08-31 | D-115 신설 — 사진 검색만 조회 시점에 전체 평균 벡터를 빼기로 했다(C, TP-197). 한 번 채택했다 취소한 결정을 뒤집는 것이라 근거가 핵심이다: 취소 근거였던 종로 631곳 평균 순위 15.2위 → 18.8위는 leave-one-out으로 잰 값인데, 그 과제는 "같은 장소의 다른 사진을 찾는" 일이라 공통 성분이 도움이 되고 분위기 맞추기는 그 공통 성분이 방해가 되는 과제다 — 다른 과제에서 잰 값으로 이 경로를 정했던 셈이다. 같은 질의 32장으로 사람이 눈가림 채점하니 48.2% → 53.2%, 직접 찍은 사진에서 44.3% → 51.9%로 개선이 더 컸다. 첫 측정은 채점 회차와 섞여 있어(빠진 곳은 옛 채점, 들어온 곳은 새 채점) 교체된 80칸을 한 회차에서 다시 매겼고, 겉보기 효과의 절반쯤이 회차였다 — 교체된 자리 27.5% 대 45.0%(p = 0.0812). 유의성이 0.05 언저리라 세 시험 중 하나만 통과하지만(0.0481 / 0.0730 / 0.0812) 방향이 모두 같고 나빠진 지표가 없으며 되돌리는 비용이 설정 하나라 채택한다. 덤으로 때깔 격차가 9.0%p → 3.1%p로 줄었다: 홍보 사진과 폰 사진을 가르던 것이 바로 그 공통 성분이었다. 허브는 늘지 않았고(1,500칸을 채운 장소 1,159 → 1,212곳) 사진 한 장짜리가 결과에 들어오기 시작했다(5.0% → 15.0%, DB 비율은 56%). 구현은 중심 벡터를 저장해 두고 뺀다 — 매번 avg()면 1,314ms, 저장하면 184ms(지금 60ms). 미리 뺀 컬럼을 따로 두지 않은 것은 원본과 두 벌이 되어 적재 때마다 어긋나기 때문이고, 응답이 문제가 되면 그때 옮긴다. 발화 경로(축 점수)와 적재된 벡터는 건드리지 않는다. 1위는 한 칸도 바뀌지 않았다 |
| 2026-09-01 | D-116 신설 — 보관함 장소를 채점 이전 단계의 후보 컨텍스트에 주입한다(D-114 결함 수정, SCHEDULE-14, B). D-114가 하드 검증 + 1회 재시도로 배치를 보장한다고 적었지만 담아둔 장소가 그 턴 후보 목록에 없으면 `planner._resolve_must_include()`가 조용히 버리고 `_missing_must_include()`는 이미 줄어든 목록만 보므로 통과한다 — 보장이 통째로 무력했다. 좌표 스냅샷은 거리 계산 보정용이지 후보를 만들지 않고 `_revivable_place_ids()`는 제외 목록에서 빼줄 뿐이라 둘 다 이걸 막지 못한다. 인사동에서 4곳을 담고 1곳만 들어간 실사용으로 드러났다. `_saved_places_context()`가 `get_active_place_details()`로 상세를 받아 `tool_context.places`에 주입한다 — `schedule_candidates`에 직접 꽂지 않은 이유는 `score`·`recommendation_reason`을 지어내야 하고 그 값이 편성 프롬프트에 그대로 들어가기 때문이다(`photo_similar.py`가 쓰는 패턴을 한 곳 더 적용한 것). 주입은 보충 조회 루프 뒤에 보충 배치와 같은 방식으로 붙이고(루프 안이면 후보 중복·소진 판정 어긋남), 주입한 개수만큼 `recommendation_limit`을 올린다. `ScheduleResult.absent_saved_place_names`를 신설해 "후보에 아예 없었다"와 "시간이 모자랐다"를 갈랐다 — 해결책이 정반대인데 한 필드에 섞여 전자에게도 재시도를 권하고 있었다. 배선은 `run_agent_flow()`와 `graph/nodes/pipeline.py`의 `PipelineDeps`·`scoring_node` 둘 다 필요했다(실제로 도는 것은 그래프 쪽). 주입은 Supabase `places`에 상세 행이 있는 장소에 한하고, "반드시 배치된다"는 fake 스텁이 `must_include`를 보지 않으므로 real LLM 검증이 남는다 |
| 2026-09-01 | D-116 결정 3 정정 — 주입한 개수만큼 `recommendation_limit`을 올리는 방어를 걷어냈다(B). 후보 풀이 상한보다 크면 주입분이 하위권에 깔려 그대로 잘린다 — 보관함의 주력 유스케이스가 구 간 이동이라 거리 점수 0으로 가장 확실하게 잘리는 것이 하필 보관함 장소다. 상한을 올리면 `_score_with_measured_routes()`의 `shortlist_limit`이 따라 커져 도보 실측 조회까지 늘어나는 부작용도 있었다(D-113 역행). 대신 채점 후 `_missing_place_ids()`로 빠진 것을 찾아 `_narrow_prepared()`로 좁혀 다시 채점하고 목록 뒤에 덧붙인다 — 점수는 D가 실제로 매기므로 결정 2의 원칙은 유지된다. staged·비-staged 두 분기 모두 배선했고, 후보 풀이 상한보다 큰 상황을 만드는 재현 테스트를 추가했다(기존 주입 테스트들은 더블이 후보를 몇 곳만 줘서 자르기 자체가 일어나지 않아 못 잡았다) |
| 2026-09-02 | D-117 신설 — 사진 검색 순위를 VLM(Gemini)에게 다시 매기게 한다, 기본 끔(C, TP-214). 임베딩 안에서 개선하려는 시도 아홉 번 중 통한 것은 평균 빼기(D-115)와 이것뿐이다 — 축 섞기·최고점·모델 교체·차원 무게 학습·PCA 상위축 제거·축만·문구 100개는 전부 같은 벡터를 다르게 읽는 방식이었고 사람 눈가림 채점에서 잡음 바닥을 못 넘었다. 다른 판단자를 붙이자 31.6% → 38.5%(p = 0.171) · 41.0%(p = 0.025)로 올랐다. 모델은 flash급 고정 — flash-lite로 내리면 +6.8%p가 +0.9%p로 사라진다(응답은 5.5배 빨라지지만 그 손잡이가 곧 품질을 없애는 손잡이였다). 응답 스키마로 점수 칸 수를 못 박는데, 프롬프트로는 안 고쳐지고(flash-lite가 후보 12곳에서 14번 중 12번을 한 칸 모자라게 답했다) 형식을 고정하면 같은 조건에서 32.6초 → 13.1초로 2.5배 빨라진다. 1위 유사도가 문턱 미만이면 부르지 않는다 — 방향이 직관과 반대로 임베딩이 잘 찾았을 때 부르는 것이 맞다(DB에 닮은 곳이 없으면 순서를 바꿔도 나아질 게 없고 오히려 흐트러뜨린다). 자르기 전에 재랭킹해야 뒤쪽 후보를 끌어올릴 수 있다. 실패는 예외 없이 임베딩 순서로 떨어지되 Fake LLM 모드에서는 재랭커를 만들지 않는다(가짜 응답으로 순서를 뒤집으면 오류 없이 결과만 틀어진다 — D-042와 같은 성격). 기본 끔인 이유는 검색당 16~47원이기 때문이고, 단가는 실험 253회로 크레딧 10,000원을 태워 입력 100만 토큰당 약 3,300원으로 역산한 실측이다. 토큰은 사진 장수에만 붙고 해상도와 무관하다. 보여줄 5곳보다 넉넉한 10곳을 보내고, 1위 유사도 0.525 이상일 때만 부른다. 보내는 수와 보여주는 수가 같으면 이득이 0이다 — 재랭커는 순서만 바꾸는데 쓰는 지표는 순서와 무관하고, 실측으로 뽑힌 곳이 그대로였던 질의 10장의 이득이 정확히 +0.0%였다. **상위 5곳 기준 실측**(질의 39장, 후보 수만 변경): 6곳 +2.1%p(p = 0.128, 겹침 4.6/5) · 8곳 +4.1%p(p = 0.050) · 10곳 +5.7%p(p = 0.006, 겹침 3.3/5). 12곳은 늘릴 근거가 없다 — 상위 3곳으로 네 판을 견주면 순서가 뒤죽박죽이고(6곳 +7.7 · 8곳 +6.0 · 10곳 +10.3 · 12곳 +6.8) 유일한 12곳 실측이 10곳보다 낮았다. 상위 3곳은 한 칸이 33%p라 눈금이 굵어 같은 조건인 판들끼리 4.3%p 벌어진다. 문턱은 0.525가 0.50과 성적이 같으면서 14% 싸고(둘 다 +4.1%p, p = 0.008, 검색당 26원 대 30원) 0.55부터는 유의성을 잃는다. **후보를 글로 옮겨 벡터로 비교하는 길은 닫혔다** — 오픈 VLM이 쓴 분위기 설명글끼리 비교하니 이득이 정확히 +0.0%(p = 0.977)였고, 서로 다른 두 공연장이 첫 문장까지 같은 글을 받았다. 표현을 글로 압축하면 잃는다는 결과가 이것으로 세 번째다(축 5개 무작위 수준, 문구 100개 벡터의 60%). 이 재측정에 API 4,169원과 채점 372칸을 썼다. 잡음 바닥은 2.6%p이고, 같은 짝 180건 재채점에서 회차 차이가 −0.12(p = 0.0058)로 재려는 효과와 같은 크기라 후보를 미리 다 채점하고 VLM을 돌리는 순서로 막았다 |
| 2026-09-02 | D-118 신설 — 추천 실측 이동수단을 1차 채점 뒤 후보별로 고르고, 거리 점수의 시간 예산을 측정 수단에서 뗀다(C 구현, A·D 리뷰). 지금은 `max_travel_time`이 없으면 `transport`가 무엇이든 도보로 재고(자동차라고 말해도), 이동수단을 말하지 않고 시간만 말하면 아예 재지 않는다. 판정을 `_score_with_measured_routes()`의 상위 10곳 추린 직후로 옮겨 도보/자동차 명시는 그대로 가고 나머지는 직선 0.85km를 경계로 도보 단독 또는 도보·대중교통 양쪽 조회 후 빠른 쪽을 쓴다 — `estimate_schedule_travel_edges()`의 `_select_mode()`와 같은 판정이라 두 기능이 "이 정도는 걸어요"를 다르게 말하지 않는다. **예산 변경이 없으면 수단 전환은 역효과다**: 예산은 `반경 ÷ 속도`인데 기본 반경 2.0km는 도보 속도로 만든 값이라 대중교통 속도를 넣으면 6.0분이 되고, 반경 안의 대중교통 실측 10~19분이 `_clamp`에 전부 0으로 잘려 거리 가중치 0.20을 전환된 후보만 잃는다. 그래서 `_travel_minutes_budget()`이 측정 수단이 아니라 **반경을 만든 속도**로 나눈다 — 이동시간을 말한 요청은 값이 그대로고(반경 = 시간 × 속도), 말하지 않은 요청의 비도보 후보만 6.0분에서 28.6분이 된다. 임계 0.85km는 "도보 20분 초과"(`schedule_walk_transfer_threshold_min`과 같은 값)를 직선거리로 환산할 때 우회 계수 1.65배(2026-08-19 종로 5개 지점 실측)를 반영한 값이다 — 보정 없이 1.4km로 두면 규칙에는 20분이라 적고 실제로는 33분짜리부터 전환한다. 양쪽 조회라 낮게 잡는 비용이 호출 수뿐이라는 점도 낮은 쪽을 택한 근거다. 근거리 조회 실패 우려는 실측으로 기각됐다(2026-09-02, 안국역·홍대입구역 후보 40곳: 0.85km 위쪽 30/30 성공, 실패 2건은 홍대 0.41km). 실측 중 도보·대중교통을 `asyncio.gather`로 함께 돌리자(각 동시성 5 = 동시 10건) 카카오가 `API limit has been exceeded.`로 대부분 거절해, 두 Provider가 세마포어를 공유하도록 함께 고친다. 남은 것: 도보 엔드포인트 일일 1,000건을 RECOMMEND(턴당 10)와 SCHEDULE(턴당 최대 12)이 서로 모른 채 나눠 쓴다, `summarize_fanout()`·`route_measured_ratio`가 한 턴에 두 번 찍힌다, `_TRAVEL_MODE_PHRASES`에 대중교통 문구가 없다 |
| 2026-09-03 | D-119 신설 — 구 이름으로 들어온 추천은 반경이 아니라 그 구 전체를 후보로 삼는다(C 구현, D 리뷰). 위치 해석이 언제나 좌표 한 쌍을 돌려줘 "강남구"도 대표점 하나로 풀렸고, 반경 2km 원(12.6km²)이 강남구 39.5km²를 못 덮는 데다 후보가 거리순 상위 30곳이라 실제 선택지가 대표점 주변 수백 미터였다 — 마포구는 후보 30곳 중 25곳이 홍대 매장, 강남구는 관광지가 1곳이었다. Supabase `places`를 구 코드로 읽어 구 전량을 모집단으로 삼고, `district_selection.py`가 분류 몫(관7·문7·음6·쇼6·레2·축2)과 4x4 격자 분산으로 30곳을 고른다. 대표점 여러 개·반경 확대는 기각했다(전자는 거리를 안 쓰기로 한 이상 호출만 늘고, 후자는 넓힌 반경이 거리순 상위 30곳에 안 잡힌다). D-025와 충돌하지 않는다 — 그 결정은 TourAPI 반경 검색 전제라 근거 두 개가 저장소 조회에 성립하지 않는다. 무작위는 원본 분포를 따라가 쇼핑이 17~18곳이 되어 기각했다(강남구 쇼핑 713곳의 실체가 성형외과·약국이다). 소진율 상한 60%는 얇은 구의 바닥 긁기를 막고(없으면 금천·동작·강동 문화시설이 100% 소진) 쇼핑 절대 상한 6은 넘친 몫이 전부 쇼핑으로 흘러가는 것을 막는다(상한만 걸면 금천구 쇼핑 15곳). 25개 구 중 셋이 30을 못 채우는데(중랑 29·도봉 28·금천 21) 그대로 넘긴다 — 금천구는 251곳 중 224곳이 쇼핑이라 쓸 만한 것이 27곳뿐이다. 더 보기는 누적 소진율 + 넘기기 첫 턴만 + 15곳 미만이면 소진(없으면 금천구가 쇼핑 6곳을 무한 반복). 좌표가 깨진 활성 12건은 폴리곤이 아니라 서울 bbox로 거른다 — 셋 다 12건을 똑같이 잡지만 정상 장소를 잃는 수가 0곳·4곳·82곳으로 갈리고, 폴리곤이 잃는 넷은 경기와 맞닿은 산(아차산성·망우산·청계산·서울둘레길)이라 아깝다. 거리 축은 C가 판정하지 않고 `RecommendationContext.district_scope`로 사실만 넘긴다(D-051) — `distance`가 기본 3축이라 키만 빼면 합이 0.80이 되고, 결측 재분배와 "존재하지 않는 Feature"를 가르는 것이 D의 결정이다. 곁가지로 Fake 지오코더가 구 이름을 풀게 했고(실제 Naver는 푸는데 Fake만 못 풀어 이 경로가 로컬에서 아예 안 돌았다) 브라우저 허용 출처를 설정으로 뺐다. 남은 것: 거리 재정규화가 없으면 후보만 넓어지고 채점이 다시 좁힌다(스모크에서 결과 5곳이 전부 중심점 1.3km 안), "강남구 카페"류 분류 조건 테스트 |
| 2026-09-03 | D-118 후속 — 판정이 고른 이동수단이 속도 비교를 이긴다(C, TP-227). D-118은 양쪽을 조회하고 `_fastest_routes()`가 빠른 쪽을 쓰게 했는데, 구간 이동수단을 LLM이 정하게 되면서(TP-227) 그 규칙이 판정을 덮어버린다 — 유모차 사용자에게 판정이 대중교통을 골라도 도보가 9.6분 대 11.2분으로 빨라 화면에는 "걸어서 10분"이 나가고, 같은 700m를 일정은 "대중교통 20분"이라고 말한다. 한 앱이 두 말을 하는 셈이고, 유모차를 밀고 턱을 넘는 10분은 애초에 10분이 아니다. `_route_priority()`에 판정 일치를 넣되 **실측 등급 뒤에** 둔다 — 앞에 두면 추정 도보가 실측 대중교통을 이겨 `_applied_travel_route()`가 그 값을 버리고 `_consistent_routes()`가 회차 전체를 직선거리로 내린다. 도보는 계속 조회하므로 대중교통 조회가 실패해도 안전망이 남는다(카카오 한도 소진과 동시 10건 400 이력이 있어 이 안전망이 필요하다). 규칙으로 정한 회차는 판정 목록이 비어 있어 D-118의 "빠른 쪽"이 그대로 유지된다. 비용은 대중교통이 느린 구간에서 거리 점수가 약 0.011점 깎이는 것뿐이다 — 예산이 반경을 만든 속도로 계산되므로(D-118 본문) 11.2분도 28.6분 예산 안에 넉넉히 들어와 0으로 잘리지 않는다 |
| 2026-09-04 | D-120 신설 — 축제공연행사(15)를 추천 후보에서 뺀다(C, TP-201). `places`에 행사 시작일·종료일 컬럼이 없어 축제의 `operating_schedule`에 하루 영업시간(`playtime`)만 들어가고, `_is_closed()`가 그 스케줄만 보므로 작년에 끝난 축제가 "지금 영업 중"으로 하드 필터를 통과한다. 활성 축제 189건 중 진행 중은 17건뿐이고, 반경 2km 조회를 네 지점에서 돌리자 걸린 축제 10건이 전부 종료된 행사였다 — TP-201이 제목 연도로 잡은 12건은 실제의 14분의 1이었다. `_UNSUPPORTED_CONTENT_TYPE_IDS`에 `"15"`를 넣으면 `resolve_place_category()`를 공유하는 네 경로(반경·구 단위·무장애·fake)가 한 번에 따라오고, 구 단위 분류 몫의 축제 2자리는 관광지·문화시설에 1씩 얹어 8·8로 올렸다(합 30 유지). 기간 컬럼을 만드는 근본 처리는 기각했다 — `detailIntro2`가 기간을 이미 돌려주는 것은 확인했지만(응답 키 22개에 `eventstartdate`/`eventenddate`, `playtime`이 `operating_hours_raw`와 일치) 후보의 91%가 유효하지 않은 유형을 살리려고 마이그레이션·재적재를 늘릴 값어치가 지금은 없다. 행사 발화는 INFO가 그대로 답한다 — `event`는 searchFestival2가, `realtime_event`는 서울시 실시간 도시데이터가 각각 기간을 보고 진행 중인 것만 준다. 반경 경로가 만날 수 있는 축제 56건은 전부 searchFestival2 193건 안에 있어 경로만 바뀐다. **제외만 하면 "축제 추천해줘"가 빈 결과가 되므로 라우팅을 함께 고쳤다**(TP-237) — 추천 프롬프트에 축제 설명이 없는데도 구조화 출력 스키마가 `PlaceType.FESTIVAL`을 허용 값으로 제시해 모델이 그 값을 내기 때문이다(실측 6발화 전부 RECOMMEND). 찾는 대상이 장소냐 행사냐로 갈라 router.classify 2.5.0 · info.extract 3.5.0 · recommend.extract 2.8.0을 함께 올렸고(A·D 소유 슬롯 포함, @kiminlim·@rayquaza410 리뷰), 실측 13발화가 행사 7건 INFO · 장소 6건 RECOMMEND로 갈린다. `realtime_event`가 비면 TourAPI로 한 번 더 보는 폴백도 넣었다 — 두 출처의 진행 중 행사가 95건·21건인데 겹치는 것이 3건뿐이라 한쪽만 보고 "없다"고 답하면 다른 쪽을 통째로 놓친다. 회귀는 단일 턴 30건 전부 정확·안정, 다중 턴 dev에서 `RECOMMEND × INFO` 0 유지다(실패 2건은 MODIFY 축이고 7회 재현으로 흔들림임을 확인했다). 프롬프트는 Langfuse에 올려야 운영에 반영된다 |
| 2026-09-05 | D-122 신설 — 서울시 실시간 카드에 인구 수 세로축과 인구·상권 요약을 함께 싣는다(C+프론트). 혼잡도 그래프가 막대 높이를 실제 인구가 아니라 단계별 고정값에서 뽑아 그려 같은 "붐빔"이면 3천 명이든 8만 명이든 높이가 같았다. 실 `citydata` 응답을 다시 열어보니 요청받은 항목이 "오늘의 인기 시간대" 하나만 빼고 전부 이미 받아오는 응답 안에 있었고 파싱만 안 하고 있었다(D-090이 "참고 이미지에 없는 값"이라며 뺀 `AREA_PPLTN_MIN/MAX` 포함). 새 API 연동 없이 인구 구간·연령대 8구간·최근 10분 결제 건수/금액·업종별 결제액을 파싱해 붙였다. 세로축은 구간 중간값 기준 실제 비율로 그리되 **막대 하나라도 인구 수가 비면 눈금을 세우지 않고 기존 단계 높이로 돌아간다** — 일부만 실측치인데 눈금이 붙으면 전부 실측치처럼 읽힌다. 요약 블록은 모든 INFO가 아니라 `concentration`·`realtime_commercial` 두 유형에만 붙여 **추가 호출이 0**이다(사용자 결정) — 두 경로가 받는 응답이 같은 한 덩이라 인구 카드에 상권 값을, 상권 카드에 인구 값을 추가 조회 없이 싣는다. 결제 Top 3는 서울시 원문 업종을 거르지 않는다(사용자 결정, 강남역 1위가 "의료 · 병원"으로 나오지만 걸러내면 우리가 만든 순위를 서울시 데이터인 것처럼 보여주게 된다). "가장 붐빌 시간대" 타일은 지금이 이미 예측 피크만큼 붐비면 비운다(강남역 실측이 현재 붐빔·예측 최고 약간 붐빔이었다) — 기존 요약 문장은 회귀 테스트가 문자열을 잠그고 있어 안 건드렸다. 상권은 서울시가 121곳 중 82곳에만 줘서 경복궁은 `LIVE_CMRCL_STTS`가 통째로 `null`이다 — 이때 상권 구획을 통째로 감춘다. "오늘의 인기 시간대"는 원본이 미래 12시간만 주고 과거 추이를 안 줘서 못 만든다(D-090 재확인), 대신 "가장 붐빌 시간대"로 이름을 바꿔 달았다. 모든 INFO로 넓히는 원안은 INFO 턴마다 호출이 한 번씩 늘어 이번 범위에서 뺐다 — 넓히려면 지역 코드별 짧은 TTL 캐시가 전제다. **후속으로 시인성을 올렸다** — 옅은 회색 캡션이던 단계를 색 칩으로, 글자 `1위`를 번호 뱃지로 바꾸고, 막대 팔레트가 `amber-500`·`orange-500`을 둘 다 gold(#e0a83e)로 재정의하고 있어 가운데 두 단계가 같은 색이던 것을 옅은/진한 앰버로 벌렸다. 빌드된 CSS로 렌더해 Playwright 스크린샷으로 확인하자 테스트가 못 잡은 셋이 드러났다 — 격자선이 막대 위로 그려짐(막대 행이 static), "현재" 테두리를 칸에 걸어 최고 눈금까지 차 보임, 인구 값이 타일에서 잘림. 그리고 "현재가 약간 붐빔인데 왜 붐빔 예측보다 막대가 기냐"는 지적은 우리 버그가 아니라 서울시가 현재 단계와 예측 단계를 따로 산출해 생기는 실제 역전이었다(난지한강공원 `약간 붐빔 3,250명` vs 18시 `붐빔 2,750명`) — 높이를 단계에 맞춰 보정하면 세로축 숫자가 거짓이 되므로 범례 한 줄로 밝히는 쪽을 골랐다. 서울시 지도 페이지와 노량진으로 값을 대조해 **현재+예측 13슬롯 중 12개가 정확히 일치**함을 확인했다(나머지 1개는 조회 시각 차이) — 다른 건 그 페이지의 과거 12시간뿐인데, 그건 공개 API가 아니라 내부 엔드포인트(`/SeoulRtd/api/ppltn_congest`, Referer 없으면 302)에서 오므로 쓰지 않기로 했다(사용자 결정). 직접 쌓는 안은 이제 폴링 없이 적재만으로 가능하다는 걸 확인했지만 콜드스타트 때문에 보류하고, 대신 막대 말풍선(시각·혼잡도·인구수, 터치·키보드 지원)을 넣고 잘리던 시각 라벨을 세 칸에 하나로 솎았다. 상권 금액은 기준이 안 보인다는 지적을 받아 업종 Top N 제목에 "최근 10분"을 넣고 신한카드 기준 안내를 제목 바로 밑으로 올렸다 — 업종 행이 지역 총액의 내역이 맞는지는 결제 건수가 정확히 일치하는 것으로 확인했다(강남역 328=328 등) |
| 2026-09-05 | D-121 신설 — 근처 공중화장실을 `public_toilet` 유형으로 분리해 별도 적재로 답한다(C, 프론트 카드 포함). `facility`가 받던 "급한데 근처에 화장실 있어?"는 그 관광지의 편의시설 안내문이 아니라 갈 곳을 물은 것이라 답할 데이터가 달랐다 — `info_field_rules.py`가 무장애 값을 facility에 합칠 때 예견한 경계다. 서울시 `mgisToiletPoi`(OA-22586) 4,447곳을 `public_toilets`에 전량 적재하고 조회는 그 표에서 한다(API에 지역·좌표 필터가 없어 경로에 구 이름을 덧붙여도 무시하고 매번 전량을 준다). 좌표가 원본에 있어 D-101과 달리 지오코딩이 없다. 개방시간은 손으로 적힌 값이라(실측 88.8%만 시각 환산, 나머지는 대부분 `정시(영업시작~종료)` 398건) 원문을 보관하고 조회 시점에 해석하며, 실패는 단정하지 않고 원문을 보여준다 — 파서를 고쳐도 재적재가 필요 없다. 정렬은 "지금 열린 곳 → 가까운 곳"이다(새벽 2시 인사동 실측에서 50m 쌈지길이 10:30~20:30이라 거리만 쓰면 닫힌 화장실이 1순위가 된다). 노출은 두 곳·반경 1km, 지명이 없으면 기기 GPS를 기준점으로 써 되묻지 않는다(`InfoContextRequest.origin_coordinates` 신설). 카드를 누르면 네이버지도 도보 길찾기로 이어지며 `openNaverDirections`에 `mode`를 추가했다(기존 호출부는 대중교통 기본값 유지). 전국표준데이터(data.go.kr)는 우리 키가 미등록이라 기각, 거리 조회를 PostGIS로 옮기는 것도 기각(바운딩 박스 + 파이썬 haversine이 D-101이 세운 방식). info.extract 3.6.0, eval 36케이스 정확도 1.0(경계 케이스 포함). 남은 것: 마이그레이션 적용과 첫 적재 실행. 곁가지로 이 기능이 드러낸 기존 버그 셋을 함께 고쳤다 — (a) 애매한 지명 폴백이 메타데이터를 이중 튜플로 넘겨 `realtime_public_parking`·`realtime_bus`·`realtime_commercial`이 모두 500이었다("인사동 주차장 자리 있어?"가 터졌다), (b) 조회 상한 60이 밀집 지역에서 가장 가까운 곳을 잘라냈다(인사동 55m 24시간 화장실이 빠지고 230m가 1순위), (c) 지명이 있어도 기기 GPS가 이겨 강남에서 물은 "인사동 화장실"에 강남 화장실을 인사동이라고 답할 수 있었다 |
| 2026-09-06 | D-123 신설 — 실시간 도로소통 카드에 돌발상황 4분류(사고/고장·공사/집회·기상/화재·기타) 진행 건수를 함께 싣는다(C+프론트). 사용자가 서울시 실시간 지도의 같은 항목을 보여주며 요청 — `citydata`에 이미 있는 `ACDNT_CNTRL_STTS`를 파싱만 하면 됐다(추가 호출 0). 공식 코드표 API(OA-13312)는 서비스 종료라 직접 조회할 수 없어, 실측(121곳 전수, 2026-09-06)으로 확인한 원문값("공사"·"집회및행사"·"기타")과 ITS 국가교통정보센터 표준 돌발유형 체계를 참고해 4분류로 키워드 매칭했다. 서울시 지도처럼 4분류를 항상 전부 채운다(0건 포함) — 조용히 감추면 "이 유형이 없다"는 뜻으로 잘못 읽힌다. 명동 관광특구 실측(공사 1건+집회및행사 1건 → 공사/집회 2건)으로 매퍼→카드까지 검증했다. 1건 이상인 분류만 rust로 강조, D-122 요약 타일과 같은 카드 스타일을 공유한다 |
| 2026-09-07 | D-124 신설 — 일정 편성의 시간 예산은 허용 오차 30분으로 판정하고, 개수 상한은 LLM이 고른 분류로 다시 잰다(B, TP-238·239·242·244 후속). 허용 오차 30분은 "3시간에 3곳"이 통과하는 최소값이면서 4곳 이상을 막는 밀도 상한을 겸한다(관광지 최소 60분·이동 15분 기준 문턱: 2시간 2곳 15분 / 3시간 3곳 30분 / 4시간 4곳 45분). 민원이 TP-238·239로 닫힌 줄 알았는데 2026-09-07 실측에서 3시간 요청에 276분이 다시 나왔다 — `derive_item_range()`가 후보 체류 최소값을 작은 것부터 세는 하한 가정이라 쇼핑(30분)이 섞인 풀에서 3곳을 허용하는데, LLM이 문화시설(최소 90분) 세 곳을 고르면 `fit_durations_to_budget()`이 정책 최소값에서 멈춰 되돌릴 여유가 0이다. 유도값은 프롬프트 목표로 남기고 응답 뒤 **실제로 고른 분류**로 같은 부등식을 다시 풀어 자른다(`cap_item_count_to_budget()`) — 개수를 지시가 아니라 자르기로 보장한 TP-239와 같은 철학이다. 상한 계산을 비싼 조합 가정으로 바꾸는 안은 기각했다(어느 분류가 뽑힐지 미리 알 수 없고 대부분의 요청에서 곳 수가 줄어든다). 상한이 줄면 `_resolve_must_include()`를 다시 불러 보관함 판정도 같은 상한을 보게 한다 — 안 하면 줄어든 자리 때문에 빠진 보관함 장소가 안내 없이 사라진다. `under` 판정에도 문장을 붙였다(판정이 셋인데 `over` 문장만 있어 3시간 요청에 1곳 120분이 나오면 왜 짧은지 말하지 않았다) — 원인은 자리를 다 썼는지로만 가르고, 자리가 남았는데 짧으면 단정하지 않는다. 판정은 언제나 정확값으로 내린다(표시용 10분 올림으로 판정하면 205분/175분 경계에서 `within`이 `over`로 뒤집힌다). TP-216의 "초과 시 사후에 빼지 않는다"는 유지한다 — 이번 것은 총합이 넘어서 빼는 것이 아니라 상한 계산의 전제가 틀려 상한을 다시 재는 것이다. 지표 13턴에서 `under`가 0건이라 미달 안내를 한 번 접었는데 같은 날 실측으로 뒤집혔다 — 존재 여부는 코드에 분기가 있는지로 판단하고 빈도는 우선순위에만 쓴다. 남은 것: 시간을 말하지 않은 턴은 이 설계가 통째로 꺼져 있다(상수 `(3, 5)` + 배분·판정·안내 건너뛰기, 실측 258~420분), 후보가 1곳만 오는 경우의 원인 |
| 2026-09-08 | D-125 신설 — 일정 편성은 "지금" 기준으로만 짜고 시작 시각·경유 출발지를 조건으로 받지 않는다(B). 후보를 고르는 기준이 `visit_at = now_kst()`로 고정돼 폐점 필터와 날씨 조회에 그 값이 들어가는데, 시작 시각만 받아 타임라인에 쓰면 후보는 지금 열린 곳으로 걸러졌는데 화면은 미래 시각을 적는 상태가 된다 — 그 시각에 문 여는지 확인하지 않은 일정에 그 시각을 적어 넣는 셈이다. 경유 출발지도 같은 이유로 받지 않는다: `search_center` 한 점 전제를 후보 수집 반경·거리 채점·구 단위 조회(D-119)·되묻기 버튼·이동시간 기준점(D-071)이 각각 다르게 읽고 있어 참조 112곳의 해석을 하나씩 다시 정해야 한다. `visit_datetime` 필드는 테스트 89곳이 시각을 고정하는 seam이라 지우지 않고 주석으로 사유를 박았다. 말한 조건이 반영되지 않았음을 알리는 안내는 후속이고, 추출 출력에 신호 필드를 더하는 일이라 D-126의 모델 결정이 먼저다 |
| 2026-09-08 | D-126 신설 — 조건 페이로드가 비어 오는 것을 해석 단계의 실패로 남긴다(B). `llm_output.recommend`가 None이면 `state_transform`이 조건 병합을 통째로 건너뛰는데 오류도 로그도 없어, 조건 0개로 추천·편성이 도는 것이 열흘 넘게 원인 미확정으로 남았다(2026-09-02 "엔드포인트마다 결과가 다르다" 관측). 실측으로 원인을 확정했다 — 같은 프롬프트·같은 발화에 주 모델만 바꾸니 `gemini-3.5-flash-lite`는 흔들림 6/18·불일치 9/18, `gemini-3.5-flash`는 0/18·0/18이다(반복 3회·2회 두 번 독립 실행에서 같은 결론. 단 흔들리는 발화 목록은 재현되지 않아 6건 중 3건만 겹친다 — 특정 발화가 아니라 모델 전반의 불안정이다). 지연은 추출 1회당 중앙값 1,564ms → 2,146ms로 약 +580ms이고, 한 턴이 분류·추출을 순차로 두 번 부르므로 해석 구간은 약 +1.2초다(그 구간은 D-106의 SSE 하트비트로 감싸져 있다). 답변 생성·일정 편성은 generation 묶음이라 영향이 없다. 토큰 단가는 재지 않았다. 엔드포인트도 프롬프트 문구도 원인이 아니었다(하네스는 라우트를 안 타고, 문구를 그대로 두고 모델만 올리니 전부 통과했다). RECOMMEND·SCHEDULE 턴에 페이로드가 없으면 `llm_interpret` trace의 `error_type`에 `condition_payload_missing`을 남긴다 — 별도 step으로 지표를 남기는 안은 턴마다 행이 늘고 TP-242의 분리를 잠근 테스트가 깨져 철회했다. 조건이 전부 null인 것은 실패로 세지 않는다(조건을 안 말한 발화에서는 옳은 결과다). 주 추출 모델을 무엇으로 둘지는 **팀이 별도 문서로 정리하기로 해** 이 결정에 넣지 않는다 — 답변·일정 생성을 lite로 내려도 되는지까지 함께 볼 범위다. 후보 넷(전부 flash / 추출만 flash / 프롬프트를 lite에 맞추기 / **빈손일 때만 폴백을 강제로 일으키기**)과 단가(flash가 입력 5배·출력 3.6배), 그리고 8월에 인텐트 분류로 같은 비교를 하고 기각한 기록(`intent_experiments_2026-08.md`, lite가 64건 중 7건 하락·5/5 재현, lite+thinking은 flash보다 느리고 덜 정확)을 "남은 것"에 적어 뒀다. **폴백이 알아서 해주지 않는다는 것이 요점이다** — `recommend: null`은 HTTP 200에 스키마도 통과해 성공으로 기록되고 그대로 반환되며, 실측에서 빈손 6건에 폴백 0/18이었다 |
| 2026-09-14 | D-127 신설 — 장소 하나를 콕 집어 묻는 후기 질문을 `review_opinion` 유형으로 신설하고, 쓸 근거인지는 유사도가 아니라 LLM이 판정한다(A+C, 로드맵 21번). **유사도로는 못 가른다는 것이 이 설계의 출발점이다** — 질문 24개를 실측하니 답할 수 있는 질문의 top1 평균이 0.650, 없는 질문이 0.481인데 0.45~0.70에서 겹쳐 어떤 컷을 잡아도 한쪽을 잃는다("북촌한옥마을 뭐가 맛있대?"는 답이 될 근거가 없는데 근처 만둣국집 후기가 0.683으로 1위, "경복궁 아이와 가기 좋대?"는 0.453). 그래서 컷은 0.25로 낮춰 후보 8건을 넉넉히 가져오고 `filter_review_evidence`가 "그 장소 이야기이면서 질문에 답이 되는가"를 골라낸 뒤 남은 것만 답변 생성에 넘긴다 — 0건이면 LLM을 아예 부르지 않고 "후기에서 확인하지 못했다"로 끝낸다. 판정을 나눈 이유는 출처 링크 때문이다: 한 번에 답까지 쓰게 하면 어느 근거를 실제로 썼는지 알 수 없어 링크를 정확히 걸 수 없다. **근거 오염이 가장 큰 위험이다** — 경복궁은 근거 59건 중 약 2/3가 근처 맛집·카페 후기이고(봉평막국수 18건·더바움 16건), 남산서울타워·서울숲·청계천도 같은 문제다. 장소명 포함 여부로는 안 갈린다(경복궁 44%, 정상인 광장시장 50%). 적재 쪽 수정 제안은 `docs/근거-장소연결-오염-점검-20260914.md`로 전달했고, 그 전까지는 선별이 임시 방어선이다(사용자 결정: 유형을 좁히지 않고 전체 적용). 검색은 `search_place_evidence`를 후보 1건짜리 배열로 그대로 쓴다 — 글 단위 중복 제거가 개수 상한보다 먼저 걸려 관광 안내문(장소당 2청크)이 결과를 독식할 수 없고, 장소당 서로 다른 글이 29~31개라 8건이면 여덟 글에서 한 문장씩 온다(새 RPC 기각). **질의는 `specific_question` 원문 그대로다** — 장소명·말끝을 다듬은 정규화 문장도 함께 쟀는데 유사도 절대값만 높고 선별 통과 건수는 원문이 낫거나 같았다(창경궁 야간관람 8건 대 7건, 국립중앙박물관 아이 동반 7건 대 6건). 동반자 질문은 말투로 가른다(사용자 결정) — "아이랑 가기 어때?"는 기존대로 `facility`, "아이와 가기 좋대?"처럼 전해 듣는 말투면 `review_opinion`이다. 라우터도 함께 고쳤다(router.classify 2.7.0) — INFO 정의가 "API 조회 가능 사실 정보"로 못 박혀 있어 "광장시장 뭐가 맛있대?"가 RECOMMEND로 갔다. 장소 이름이 찾는 조건이냐 질문의 대상이냐로 가른다. 곁가지로 `PlaceEvidenceProvider`의 동기 인코딩을 `asyncio.to_thread`로 감쌌다 — 추천은 턴당 한 번이라 넘어갔지만 후기 답변은 채팅 스트리밍 중에 일어나 이벤트 루프를 막으면 동시 접속자의 토큰 전송까지 멈춘다. 기능 스위치 `review_answer_enabled`가 꺼져 있거나 문장 인코더가 없으면 기존 상세 조회로 흘려보낸다("못 찾았어요"로 퇴보시키지 않는다). 남은 것: 선별이 "그 장소를 배경으로만 언급한 문장"을 드물게 남긴다(청계천 식당 글·남산 리스트 글), 적재 개선 전까지 경복궁류 대표 장소는 "확인하지 못했다"가 자주 나온다 |

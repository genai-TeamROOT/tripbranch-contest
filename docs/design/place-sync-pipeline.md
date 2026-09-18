# 장소 동기화 파이프라인 설계 v0.1

## 1. 목표와 범위

TourAPI의 지역 기반 장소 목록과 장소별 운영정보를 수집해 Supabase의 `places`에
저장하고, 실행 결과를 `place_sync_runs`에 기록한다.

최초 실행 대상은 서울특별시 종로구다.

```text
area_code=11
district_code=110
```

구조와 실행 인터페이스에는 지역 코드를 인자로 전달해 이후 다른 시·군·구를 같은
방식으로 추가할 수 있게 한다.

이번 구현 범위:

- `areaBasedList2` 전체 페이지 조회
- `detailIntro2` 운영시간·휴무일 조회
- 기존 운영정보 파서 적용
- Supabase `places` upsert
- `place_sync_runs` 실행 상태와 건수 기록
- 변경·TTL·실패 기반 증분 상세조회
- 완전한 목록에서 사라진 장소 비활성화
- 수동 실행 CLI와 단위·통합 테스트

이번 구현에서 제외:

- `place_enrichments` 자동 생성
- 추천 요청에서 DB 후보를 읽는 기능
- 관리자 화면
- 자동 스케줄러 배포
- 다른 지역의 실제 데이터 적재

## 2. 현재 코드와의 차이

현재 `RealPlaceProvider`는 다음 기능을 제공한다.

- `locationBasedList2`: 좌표 반경 검색
- `searchKeyword2`: 키워드 검색
- `detailCommon2` + `detailIntro2`: 장소 상세조회

장소 동기화에 기존 `get_details()`를 사용하면 장소마다 `detailCommon2`와
`detailIntro2`를 모두 호출한다. 장소명·주소·좌표·분류는 `areaBasedList2`에서
이미 얻을 수 있으므로 동기화에서는 `detailCommon2`를 호출하지 않는다.

새로 필요한 Provider 기능:

```text
areaBasedList2 페이지 조회
detailIntro2 운영정보 전용 조회
```

종로구 882건 기준 최초 예상 호출량:

```text
목록 9회 + 운영정보 상세 최대 882회 = 최대 891회
```

## 3. 구성요소와 책임

```text
scripts/sync_places.py
        │
        ▼
PlaceSyncService
   ├─ TourAreaPlaceProvider
   │    ├─ areaBasedList2
   │    └─ detailIntro2
   │
   ├─ OperatingHoursParser
   │    └─ normalize_operating_schedule()
   │
   └─ PlaceRepository
        └─ Supabase PostgREST
             ├─ places
             ├─ place_sync_runs
             └─ place_sync_locks
```

### `TourAreaPlaceProvider`

외부 TourAPI 호출과 원본 응답 검증만 담당한다.

```python
class TourAreaPlaceProvider(Protocol):
    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage: ...

    async def get_operating_details(
        self,
        content_id: str,
        content_type_id: str,
    ) -> PlaceOperatingDetails: ...
```

Provider는 DB 상태, TTL, 비활성화 여부를 판단하지 않는다.

### `PlaceRepository`

Supabase 테이블 읽기와 쓰기를 담당한다.

```python
class PlaceRepository(Protocol):
    async def create_sync_run(...) -> UUID: ...
    async def try_acquire_sync_lock(...) -> bool: ...
    async def release_sync_lock(...) -> bool: ...
    async def get_region_place_states(...) -> dict[str, StoredPlaceState]: ...
    async def upsert_place_list(...) -> None: ...
    async def update_operating_details(...) -> None: ...
    async def mark_detail_failed(...) -> None: ...
    async def reactivate_source_missing_places(...) -> int: ...
    async def deactivate_unseen_places(...) -> int: ...
    async def complete_sync_run(...) -> None: ...
```

Repository는 TourAPI를 호출하거나 운영정보를 해석하지 않는다.

### `PlaceSyncService`

Provider와 Repository를 조합하고 다음 정책을 결정한다.

- 전체 페이지 수집 및 완전성 검증
- 신규·변경·TTL·실패 장소 선택
- 상세조회 동시성 및 재시도
- 원문 정규화
- 부분 실패 허용
- 비활성화 안전장치
- 실행 건수 집계와 최종 상태 결정

## 4. 도메인 모델

### `TourPlaceRecord`

`areaBasedList2` 한 건을 동기화에 필요한 형태로 변환한다.

```python
@dataclass(frozen=True)
class TourPlaceRecord:
    content_id: str
    content_type_id: str
    title: str
    address: str | None
    latitude: float | None
    longitude: float | None
    area_code: str
    district_code: str
    lcls_systm1: str | None
    lcls_systm2: str | None
    lcls_systm3: str | None
    source_modified_at: datetime | None
```

좌표가 없더라도 장소 자체는 저장한다. 추천 후보 사용 가능 여부는 이후 조회
계층에서 판단한다.

### `TourPlacePage`

```python
@dataclass(frozen=True)
class TourPlacePage:
    page_no: int
    num_of_rows: int
    total_count: int
    places: tuple[TourPlaceRecord, ...]
```

### `PlaceOperatingDetails`

```python
@dataclass(frozen=True)
class PlaceOperatingDetails:
    content_id: str
    content_type_id: str
    operating_hours_raw: str | None
    rest_date_raw: str | None
    # D-056. 같은 detailIntro2 응답에서 오므로 추가 호출은 없다.
    parking_info_raw: str | None = None
    parking_fee_raw: str | None = None
    use_fee_raw: str | None = None
    discount_info_raw: str | None = None
```

주차·요금·할인 원문은 운영시간과 같은 `detailIntro2` 응답에서 읽는다(D-056).
`contenttypeid`별 필드명 차이와 커버리지 한계는
[장소 데이터베이스 설계 §5](./place-database-schema.md)를 본다. 대표 이미지
(`first_image_url`)와 썸네일(`thumbnail_url`)은 상세가 아니라 `areaBasedList2`
목록 응답에서 오므로 이 모델이 아니라 목록 처리 경로에서 저장한다.

### `StoredPlaceState`

상세조회 필요 여부를 판단할 최소 DB 상태다.

```python
@dataclass(frozen=True)
class StoredPlaceState:
    content_id: str
    source_modified_at: datetime | None
    detail_fetched_at: datetime | None
    detail_fetch_status: str
    operating_parser_version: str
    operating_hours_raw: str | None
    rest_date_raw: str | None
    is_active: bool
    inactive_reason: str | None
```

## 5. 환경 설정

백엔드 `Settings`에 다음 값을 추가한다.

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
PLACE_SYNC_PAGE_SIZE=100
PLACE_SYNC_DETAIL_CONCURRENCY=5
PLACE_SYNC_DETAIL_TTL_DAYS=30
PLACE_SYNC_AREA_CODE=11
PLACE_SYNC_DISTRICT_CODE=110
```

보안 규칙:

- Secret Key는 `repr=False`, `exclude=True`로 선언한다.
- 키는 프론트엔드 환경변수에 넣지 않는다.
- 요청 URL, HTTP 예외, 로그에 키를 출력하지 않는다.
- Repository는 `apikey` 헤더를 내부에서만 구성한다.

새 의존성을 추가하지 않고 기존 `httpx`로 Supabase PostgREST를 호출한다.

## 6. Supabase 저장 방식

### REST 기본 설정

```text
Base URL: {SUPABASE_URL}/rest/v1
Headers:
  apikey: {SUPABASE_SECRET_KEY}
  Content-Type: application/json
```

### 목록 upsert

- `content_id`를 충돌 기준으로 사용한다.
- 한 요청에 100건씩 묶어 upsert한다.
- 목록에서 받은 원본 필드와 동기화 시각만 갱신한다.
- 기존 운영정보와 수동 비활성화 상태는 목록 upsert로 덮어쓰지 않는다.
- 새 장소는 `detail_fetch_status=pending`, `is_active=true`로 생성한다.

PostgREST 요청 개념:

```text
POST /rest/v1/places?on_conflict=content_id
Prefer: resolution=merge-duplicates,return=minimal
```

기존 행과 신규 행의 필드 차이가 있으므로 Repository는 신규 ID와 기존 ID를
구분해 payload를 만든다.

### 상세정보 업데이트

성공:

```text
operating_hours_raw
rest_date_raw
parking_info_raw
parking_fee_raw
use_fee_raw
discount_info_raw
operating_schedule
operating_parse_status
operating_parser_version
detail_fetched_at
detail_fetch_status=success 또는 empty
detail_error_code=null
```

`operating_hours_raw`과 `rest_date_raw`가 모두 없으면 HTTP 요청은 성공했더라도
`detail_fetch_status=empty`로 기록한다.

**이 판정에는 주차·요금을 넣지 않는다(D-056).** 넣으면 운영시간이 없고 주차만
있는 장소가 `empty`에서 `success`로 바뀌어 재조회 주기가 달라진다. 이 컬럼은
"운영정보를 확보했는가"를 뜻하므로 기존 의미를 유지한다.

실패:

```text
detail_fetch_status=failed
detail_error_code=<내부 코드>
```

실패 시 기존 운영정보 원문, 정규화 결과, `detail_fetched_at`은 유지한다.

## 7. 동기화 알고리즘

### 7.1 실행 시작

1. `place_sync_runs`에 `running` 행을 생성한다.
2. `try_acquire_place_sync_lock` RPC로 해당 지역의 잠금을 요청한다.
3. 잠금 획득에 실패하면 새 실행을 `failed`로 종료하고 장소 처리를 시작하지 않는다.
4. 잠금 기본 TTL은 2시간으로 한다.
5. 실행 ID를 이후 모든 장소의 `last_sync_run_id`에 사용한다.
6. 실행 도중 예외가 발생해도 가능한 경우 실행 행을 `failed`로 종료한다.
7. 성공·부분 실패·실패 여부와 관계없이 `finally`에서 자신의 실행 ID로 잠금
   해제를 요청한다.

잠금은 `(area_code, district_code)` 복합 PK로 한 지역에 한 행만 허용한다.
획득 RPC는 유효한 기존 잠금을 덮어쓰지 않으며, 만료된 잠금만 원자적으로
교체한다. 해제 RPC는 `sync_run_id`까지 일치할 때만 행을 삭제한다.

### 7.2 전체 목록 수집

1. 첫 페이지를 `num_of_rows=100`으로 조회한다.
2. `total_count`로 전체 페이지 수를 계산한다.
3. 나머지 페이지를 순차 조회한다.
4. `content_id` 기준으로 중복을 검사한다.
5. 다음 조건을 모두 만족해야 목록을 완전한 것으로 판정한다.

```text
모든 페이지 성공
수집 건수 == total_count
고유 content_id 수 == total_count
각 장소의 content_id, content_type_id, title 존재
```

목록이 불완전하면:

- 실행 상태를 `failed`로 종료한다.
- 어떤 기존 장소도 비활성화하지 않는다.
- 상세조회 단계로 진행하지 않는다.

목록 페이지는 9회 정도이므로 v0.1에서는 순차 조회한다. 상세조회만 제한된
동시성을 사용한다.

### 7.3 기존 상태 조회와 목록 upsert

1. 해당 지역의 기존 `StoredPlaceState`를 한 번에 조회한다.
2. 목록 장소를 100건 단위로 upsert한다.
3. `last_sync_run_id`, `list_fetched_at`, `last_seen_at`을 현재 실행 값으로 갱신한다.
4. 이전 사유가 `missing_from_source`인 장소가 다시 나타나면 활성화한다.
5. `manual_exclusion`, `closed`, `moved_outside_area`, `invalid_data`는 자동으로
   활성화하지 않는다.

### 7.4 상세조회 대상 결정

다음 중 하나면 `detailIntro2`를 호출한다.

```text
신규 장소
detail_fetch_status가 pending 또는 failed
TourAPI source_modified_at 변경
detail_fetched_at이 없거나 30일 이상 경과
```

파서 버전만 다른 경우에는 TourAPI를 호출하지 않는다.

```text
operating_parser_version != operating-hours-1.0.0
→ DB에 저장된 원문으로 재파싱
→ 정규화 결과와 파서 버전만 갱신
```

### 7.5 상세조회 실행

- 기본 동시성: 5
- 장소별 Timeout: 기존 `EXTERNAL_API_TIMEOUT_SECONDS`
- 재시도 횟수: 기존 `EXTERNAL_API_RETRY_COUNT`
- 재시도 대상: Timeout, 연결 오류, HTTP 429, HTTP 5xx
- 재시도하지 않음: 유효하지 않은 content ID, 파싱 가능한 정상 빈 응답
- 재시도 간격: 지수 백오프에 작은 jitter 적용

한 장소 실패는 다른 장소의 처리를 중단하지 않는다.

API Key가 포함될 수 있는 전체 요청 URL이나 httpx 예외 문자열은 로그에 남기지
않고 내부 오류 코드만 저장한다.

권장 내부 오류 코드:

```text
TOUR_DETAIL_TIMEOUT
TOUR_DETAIL_RATE_LIMITED
TOUR_DETAIL_UNAVAILABLE
TOUR_DETAIL_INVALID_RESPONSE
TOUR_DETAIL_UNKNOWN
```

### 7.6 비활성화

목록 완전성 검증이 통과한 경우에만 실행한다.

해당 지역에서:

```text
is_active=true
last_sync_run_id != 현재 실행 ID
```

인 장소를 다음 상태로 바꾼다.

```text
is_active=false
inactive_reason=missing_from_source
inactive_at=현재 시각
```

이미 수동 사유로 비활성화된 장소는 변경하지 않는다.

### 7.7 실행 종료

```text
failed_count == 0
→ success

failed_count > 0이고 목록은 완전함
→ partial_failure

목록 불완전 또는 실행 자체 중단
→ failed
```

다음 건수를 `place_sync_runs`에 저장한다.

```text
api_total_count
processed_count
success_count
failed_count
new_count
updated_count
deactivated_count
```

`processed_count`는 목록에서 처리한 전체 장소 수다. 상세조회가 필요하지 않은
장소도 목록 upsert가 성공하면 `success_count`에 포함한다. 상세조회 대상의 조회가
최종 실패하면 해당 장소는 `failed_count`에 포함한다.

완전한 목록을 처리한 실행에서는 다음 관계를 만족한다.

```text
success_count + failed_count == processed_count
```

## 8. 실행 인터페이스

MVP에서는 외부 HTTP 관리자 API를 만들지 않고 로컬 CLI로 수동 실행한다.

예:

```bash
cd backend
python -m scripts.sync_places --area-code 11 --district-code 110
```

옵션:

```text
--area-code          필수, 기본 환경변수 허용
--district-code      필수, 기본 환경변수 허용
--dry-run            API 조회와 대상 계산만 수행하고 DB를 수정하지 않음
--details-limit N    개발 검증 시 상세조회 대상을 N건으로 제한
--force-details      modifiedtime과 TTL에 관계없이 상세정보 재조회
```

안전 규칙:

- 최초 실제 적재 전 `--details-limit N`으로 소규모 실제 실행을 한다. 상한이 걸린
  실행은 비활성화를 수행하지 않으므로, 목록이 잘못돼도 기존 장소를 끄지 않는다.
  `--dry-run`을 리허설로 쓰지 않는다 — 같은 호출 수를 쓰면서 결과를 남기지 않아
  한도를 두 번 쓰게 된다(중구 892건이면 리허설에 892회, 실제 적재에 892회).
- `--details-limit` 실행은 불완전한 운영 검증용이므로 비활성화를 수행하지 않는다.
- 정기 실행에서는 `--details-limit`을 사용하지 않는다.
- `--dry-run`은 DB 쓰기 경로를 아예 타지 않고 상세조회 응답만 확인할 때만 쓴다.
  개발자 Ops 패널에는 이 선택지가 없다 — 모르고 켜두면 한도만 쓰고 아무것도
  남지 않는다(2026-08-22 새벽에 세 구가 그렇게 돌았다).

자동화가 필요해지면 같은 `PlaceSyncService`를 GitHub Actions, cron 또는 Supabase
Scheduled Function에서 호출하고 동기화 로직을 복제하지 않는다.

## 9. 권장 파일 구조

```text
backend/
├─ app/
│  ├─ config.py
│  ├─ domain/
│  │  ├─ models.py
│  │  └─ operating_hours.py
│  ├─ providers/
│  │  ├─ protocols.py
│  │  └─ real_place.py
│  ├─ repositories/
│  │  ├─ protocols.py
│  │  └─ supabase_places.py
│  └─ services/
│     └─ place_sync.py
├─ scripts/
│  ├─ __init__.py
│  └─ sync_places.py
└─ tests/
   ├─ test_place_area_provider.py
   ├─ test_supabase_place_repository.py
   ├─ test_place_sync_service.py
   └─ test_sync_places_cli.py
```

## 10. 테스트 설계

### Provider 단위 테스트

- 목록 단일 페이지·여러 페이지 응답 변환
- `items=""`, 단일 객체, 배열 형태 처리
- `totalCount`, `pageNo`, `numOfRows` 파싱
- 필수 식별자 누락 감지
- TourAPI 시간 문자열 파싱
- content type별 운영시간·휴무 필드 선택
- Service Key가 예외와 로그에 노출되지 않음

### Repository 단위 테스트

`httpx.MockTransport`로 검증한다.

- Secret Key의 `apikey` 헤더 설정
- `content_id` 충돌 기준 upsert
- 100건 chunk 처리
- 실패 시 기존 운영정보를 payload에 포함하지 않음
- 수동 비활성 상태를 목록 upsert가 덮어쓰지 않음
- 실행 완료 상태와 건수 업데이트
- 잠금 획득·중복 거절·정확한 실행 ID 해제 RPC 호출

### Service 단위 테스트

Fake Provider와 Fake Repository를 사용한다.

- 전체 페이지 수집 및 중복 검증
- 신규·변경·TTL·실패 대상 선택
- 파서 버전만 다른 장소는 API 없이 재파싱
- 상세 한 건 실패 시 `partial_failure`
- 목록 불완전 시 상세조회·비활성화 금지
- 다시 나타난 `missing_from_source` 자동 활성화
- 수동 제외 장소 자동 활성화 금지
- `details_limit` 사용 시 비활성화 금지
- 잠금 획득 실패 시 Provider 호출 없이 실행 종료
- 성공·예외 경로 모두에서 자신의 잠금 해제

### 실제 연동 테스트

명시적인 환경변수가 있을 때만 실행하는 smoke marker를 사용한다.

1. TourAPI `--details-limit 3`
2. 테스트용 Supabase 프로젝트 또는 별도 테스트 지역에 제한 적재
3. 실행 건수와 DB 행 확인
4. Supabase Secret Key 및 TourAPI Key 로그 미노출 확인

운영 프로젝트에서 테스트용 가짜 행을 만들지 않는다.

## 11. 구현 순서

### 1단계 — Provider 확장

- 도메인 모델 추가
- `areaBasedList2` 페이지 조회 구현
- `detailIntro2` 전용 운영정보 조회 구현
- Provider 단위 테스트

완료 기준:

```text
종로구 첫 페이지를 공통 모델로 변환 가능
운영정보 전용 조회가 detailCommon2를 호출하지 않음
모든 단위 테스트 통과
```

### 2단계 — Supabase Repository

- 설정값 추가
- Repository Protocol 및 PostgREST 구현
- 실행 생성·목록 upsert·상세 갱신·종료 구현
- MockTransport 단위 테스트

완료 기준:

```text
실제 네트워크 없이 모든 REST 요청과 payload 검증
비밀값이 repr·로그·예외에 노출되지 않음
```

### 3단계 — 동기화 Service와 CLI

- 전체 알고리즘 구현
- 동시성·재시도·부분 실패 처리
- dry-run과 details-limit 구현
- Service 및 CLI 테스트

완료 기준:

```text
Fake 250건으로 3페이지 동기화 시나리오 통과
목록 불완전 시 비활성화가 발생하지 않음
동일 입력 재실행이 중복 행을 만들지 않음
동일 지역 동시 실행 두 건 중 한 건만 잠금을 획득함
```

### 4단계 — 최초 종로구 적재

1. read-only MCP 유지 상태에서 로컬 테스트 완료
2. `--dry-run --details-limit 3`
3. 결과 검토
4. Supabase 쓰기 권한으로 전체 실행
5. DB 건수와 상태 분포 검증
6. MCP read-only 복귀

완료 기준:

```text
places 고유 content_id 수 == 완전한 목록 totalCount
place_sync_runs에 실행 결과 기록
중복 content_id 없음
상세 성공·빈 값·실패·파싱 상태 분포 보고
anon/authenticated 직접 접근 불가 유지
```

## 12. 구현 전 확정값

| 항목 | v0.1 값 |
| --- | --- |
| 최초 지역 | 서울 종로구 (`11`, `110`) |
| 목록 페이지 크기 | 100 |
| 상세 동시성 | 5 |
| 상세 TTL | 30일 |
| 동기화 잠금 TTL | 2시간 |
| 정기 실행 주기 | 주 1회 |
| 파서 버전 | `operating-hours-1.0.0` |
| Supabase 접근 | 백엔드 Secret Key + PostgREST |
| 목록 조회 | 순차 |
| 상세조회 | 제한된 비동기 병렬 |
| 자동 삭제 | 하지 않음 |
| 목록 불완전 시 비활성화 | 하지 않음 |

## 13. 최초 종로구 전체 적재 결과

2026-07-24에 서울 종로구 법정동 코드 `11/110`을 대상으로 최초 전체 적재를
실행했다. 최종 성공 실행 ID는
`9fe817b4-a60d-4ea0-8f88-0623954b32f0`이다.

> **이 절은 2026-07-24 실행 시점의 기록이며 현재값이 아니다.** 이후 정기 동기화로
> 목록에서 사라진 장소가 비활성화되어 **2026-08-08 기준 활성 장소는 844건**이다
> (D-056·D-058의 실측 기준). 아래 수치를 현재 규모로 인용하지 않는다.

### 장소 및 실행 결과

| 항목 | 결과 |
| --- | ---: |
| TourAPI 전체 건수 | 882 |
| `places` 저장 건수 | 882 |
| 고유 `content_id` | 882 |
| 좌표 보유 장소 | 882 |
| 활성 장소 | 882 |
| 상세조회 실패 | 0 |
| 운영시간 원문 보유 | 847 |
| 휴무정보 원문 보유 | 642 |
| 최종 실행 상태 | `success` |

### 상세 조회 상태

| `detail_fetch_status` | 건수 | 의미 |
| --- | ---: | --- |
| `success` | 850 | 운영시간 또는 휴무정보 중 하나 이상을 정상 수신 |
| `empty` | 32 | API 호출은 성공했지만 운영시간과 휴무정보가 모두 없음 |
| `failed` | 0 | 상세조회 최종 실패 |

`empty`는 API 장애나 파싱 실패가 아니다. TourAPI가 해당 장소에 대해
운영시간과 휴무일을 모두 제공하지 않은 정상 빈 응답이며, 이후 TTL 또는 원본
수정 시각 정책에 따라 다시 조회할 수 있다.

### 운영정보 파싱 상태

| `operating_parse_status` | 건수 | 의미 |
| --- | ---: | --- |
| `parsed` | 431 | 운영·휴무 규칙을 구조화함 |
| `partial` | 418 | 원문 일부만 구조화했거나 복잡한 예외가 남아 있음 |
| `assumed` | 21 | 콘텐츠 유형별 명시적 가정 규칙을 적용함 |
| `unknown` | 12 | 구조화할 운영 규칙을 확정하지 못함 |

`partial`, `assumed`, `unknown`도 TourAPI 원문 수집 자체는 완료된 상태다.
`operating_hours_raw`과 `rest_date_raw`을 보존하므로 파서 개선 후 외부 API를
재호출하지 않고 다시 파싱할 수 있다. 파서 버전은 882건 모두
`operating-hours-1.0.0`으로 기록했다.

이 수치는 최초 적재 시점의 스냅샷이다. 주간 동기화, TourAPI 원본 변경 또는
파서 버전 변경 이후에는 상태별 건수가 달라질 수 있다.

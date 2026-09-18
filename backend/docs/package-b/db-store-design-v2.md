# Package B — DB 저장소(Supabase) 전환 설계안 v2

- 작성일: 2026-07-28 (v1 대비: 실제 구현 검토를 거쳐 스키마·클래스 구조·
  테스트·반영 순서까지 구체화)
- 범위: `StateStore` Protocol을 유지한 채, `InMemoryStateStore` 대신
  Supabase에 저장하는 구현체(`SupabaseStateStore`)를 추가한다. 인터페이스·
  계약(`AgentState`, `RecommendationHistory`, `ConditionChangeLog`,
  `TraceRecord`)은 전혀 바뀌지 않는다 — 저장 위치만 바뀐다.
- 방식: `httpx.Client`(동기)로 Supabase PostgREST를 직접 호출한다.
  `supabase_places.py`는 `httpx.AsyncClient`(비동기)를 쓰지만, `StateStore`
  Protocol 자체가 동기라 여기서는 동기로 간다. 같은 Supabase 프로젝트를
  쓰지만 클라이언트/테이블이 완전히 분리돼 있어 서로 간섭하지 않는다.

---

## 1. 왜 이렇게 짰는지 (설계 원칙)

1. **기존 인터페이스·호출부를 하나도 안 건드린다.** `agent_runtime.py`,
   `service.py`, `history.py`, `session.py`는 전부 무수정. `StateStore`
   Protocol의 메서드 10개 시그니처도 그대로 유지한다.
2. **B는 판단하지 않는 기억 장치라는 원칙을 저장소 계층에도 그대로
   적용한다.** DB 응답이 이상하면(파싱 실패 등) B가 값을 고치거나 추측하지
   않고 바로 에러로 실패시킨다.
3. **기존 검증된 패턴을 재사용한다.** HTTP 호출·에러 래핑·설정값은
   `supabase_places.py`와 `app/config.py`에 이미 있는 것을 그대로 가져다
   쓰고, 새로 발명하지 않는다.
4. **저장 방식은 기존 read/write 패턴을 그대로 따라간다.** 새로운 저장
   방식을 도입하는 게 아니라, `InMemoryStateStore`가 이미 하고 있는 두 가지
   패턴(통째로 읽고 쓰기 / append만 하기)을 DB 테이블 구조에 그대로
   반영한다 (2절).

## 2. 데이터 모델별 저장 방식

`InMemoryStateStore`를 보면 저장 방식이 이미 두 갈래로 나뉘어 있다. 이걸
그대로 테이블 설계에 반영했다.

### 2-1. 통째로 읽고 쓰는 것 (read-modify-write)

`get_state`/`save_state`, `get_history`/`save_history`가 여기 해당한다.
`history.py`의 `record_recommended()`가 `get_history()` → 메모리에서 리스트에
append → `save_history()`로 전체를 다시 저장하는 방식이라, DB에서도 행
하나를 통째로 upsert하는 구조로 맞춘다.

| 테이블 | 대응 모델 | PK | 저장 방식 |
|---|---|---|---|
| `agent_states` | `AgentState` | `session_id` | `user_conditions`, `api_context`를 jsonb로 통째 저장 (14개 필드를 컬럼화하지 않음 — B는 값 의미를 판단 안 하므로 정규화할 이유 없음). `POST` + `on_conflict=session_id`로 upsert |
| `recommendation_histories` | `RecommendationHistory` | `session_id` | `recommended`, `rejected`를 jsonb 배열로 통째 저장. 동일하게 upsert |

### 2-2. append만 하는 것 (append-only)

`append_change_logs`/`get_change_logs`, `append_traces`/`get_traces`가
여기 해당한다. 계약상 수정·삭제가 없으므로(3절 delete 메서드가 아예 없음)
읽어올 필요 없이 새 행만 추가한다.

| 테이블 | 대응 모델 | PK | 저장 방식 |
|---|---|---|---|
| `condition_change_logs` | `ConditionChangeLog` | `id` (bigserial) | 행 1개 = 기록 1건, `session_id, id` 인덱스로 조회·정렬 |
| `trace_records` | `TraceRecord` | `id` (bigserial) | 행 1개 = 기록 1건, `session_id, id` 인덱스로 조회·정렬 |

### 2-3. 의도적으로 안 넣은 것

- **테이블 간 FK 제약 없음.** `agent_states`와 나머지 3개 테이블을 참조
  관계로 묶지 않았다. `delete_state`/`delete_history`가 서로 독립적으로
  호출될 수 있어(계약상 각자 별도 reset 메서드), FK로 묶으면 오히려
  Protocol의 "서로 독립적" 특성과 어긋난다.
- **`agent_states`에 자동 `updated_at` 갱신 트리거 없음.** 이미
  애플리케이션이 필드별로 세밀하게 관리한다 — 예를 들어 `update_api_context()`는
  `last_active_at`만 갱신하고 `updated_at`은 의도적으로 안 건드린다(GPS·날씨
  갱신은 조건 변경이 아니므로). DB가 자동으로 `updated_at`을 덮어쓰면 이
  구분이 깨진다. (`places` 테이블 등 다른 곳의 트리거 패턴과는 의도적으로
  다르게 감)

## 3. 클래스 구조 (`app/state/supabase_store.py`)

```
SupabaseStateStore
├── __init__(supabase_url, secret_key, client, timeout_seconds=10.0)
│     supabase_places.py의 SupabasePlaceRepository.__init__()과 동일한 검증
│     (url/key 빈 문자열 방지) — 다만 client 타입만 AsyncClient → Client
│
├── (내부) _headers(), _request(), _json(), _one_or_none()
│     supabase_places.py의 동명 메서드를 그대로 복사 후 async만 제거.
│     에러는 전부 SupabaseRepositoryError(같은 클래스, 새로 안 만들고 import)로 통일
│
├── get_state / save_state / delete_state          → agent_states
├── get_history / save_history / delete_history     → recommendation_histories
├── append_change_logs / get_change_logs            → condition_change_logs
└── append_traces / get_traces                       → trace_records
```

핵심 구현 팁: Supabase가 돌려주는 JSON 행의 필드명이 pydantic 모델
필드명과 1:1로 같기 때문에, `AgentState.model_validate(row)`처럼 모델의
자동 파싱을 그대로 활용한다. `supabase_places.py`처럼 필드를 하나하나 수동
매핑할 필요가 없다 (그쪽은 원본 API 필드명과 모델 필드명이 달라서 수동
매핑이 필요했던 것 — B는 애초에 저장 스키마를 모델 그대로 설계했으므로
이 문제가 없음).

## 4. 테스트 전략 (`tests/state/test_supabase_store.py`)

- `tests/test_supabase_place_repository.py`와 동일하게 `httpx.MockTransport`로
  가짜 응답만 사용 — 실제 Supabase 연결·자격증명 없이 CI에서 바로 돈다.
- 검증 축:
  1. 없는 세션 조회 시 `None`/`[]` 반환
  2. 정상 행 → 모델 파싱 성공
  3. 요청 형태 검증 (`apikey` 헤더, `eq.` 필터, `on_conflict` 파라미터,
     `Prefer` 헤더)
  4. append 계열은 빈 리스트 입력 시 요청 자체를 안 보내는지
  5. HTTP 에러 응답 → `SupabaseRepositoryError`로 변환되는지
- 기존 `InMemoryStateStore` 테스트가 검증하는 계약(복사본 반환, append-only에
  delete 메서드 없음 등)은 Protocol 레벨 계약이라 `SupabaseStateStore`에도
  동일하게 적용되지만, 네트워크 모킹 특성상 "복사본 반환"은 별도로 검증할
  필요가 없다(매 호출이 이미 새 객체를 파싱해서 만들어내므로 원천적으로
  참조 공유가 불가능함).

## 5. 반영 순서

1. **마이그레이션 먼저**: `supabase/migrations/202607280001_create_agent_state_tables.sql`을
   Supabase 프로젝트에 반영 (CLI `db push` 또는 대시보드 SQL Editor).
   4개 테이블 생성 확인.
2. **코드 추가**: `app/state/supabase_store.py`, `tests/state/test_supabase_store.py`
   추가.
3. **검증**: `pytest tests/state/test_supabase_store.py` → 전체 `pytest` →
   `ruff check`.
4. **(이 설계 범위 밖)** `get_store()`의 기본 저장소를 `InMemoryStateStore`에서
   `SupabaseStateStore`로 실제 전환하는 시점·방법은 별도로 결정한다 — 예를
   들어 환경변수로 분기할지, Phase 2를 통째로 스위치오버할지는 A/전체 배포
   계획과 맞물려 있어 이 설계안에서 다루지 않는다.

## 6. 남은 질문 / 리스크

- **커넥션 재사용**: `httpx.Client`를 요청마다 새로 만들지, FastAPI 앱
  생명주기에 맞춰 하나를 재사용할지 아직 미정 (`supabase_places.py`는
  `async with httpx.AsyncClient()`로 요청 단위 생성 — 동기 버전도 동일하게
  갈지, 아니면 앱 시작 시 한 번만 만들지는 실제 전환 시점에 성능 테스트하며
  결정 필요).
- **재시도 정책**: `SupabaseRepositoryError(retryable=True)`로 표시는 되지만
  실제 재시도 로직은 아직 없음 — 상위 계층(FastAPI 에러 핸들러 등)이
  이 플래그를 보고 재시도할지는 B 범위 밖.
- **RLS 정책**: 지금은 anon/authenticated 완전 차단만 해뒀고, 세부 정책은
  안 만듦 — server는 secret key(service role)로 RLS를 우회하므로 당장은
  문제 없지만, 프론트에서 Supabase에 직접 접근할 계획이 생기면 별도 정책
  설계가 필요.
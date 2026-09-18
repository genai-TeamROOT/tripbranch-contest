# Package B — DB 저장소(Supabase) 전환 설계안 v1

- 작성일: 2026-07-28
- 범위: `StateStore` Protocol을 유지한 채, `InMemoryStateStore` 대신 Supabase에
  저장하는 구현체를 추가한다. 인터페이스·계약(`AgentState`, `RecommendationHistory`,
  `ConditionChangeLog`, `TraceRecord`)은 전혀 바뀌지 않는다 — 저장 위치만 바뀐다.
- 방식: `httpx.Client`(동기)로 Supabase PostgREST를 직접 호출한다.
  `supabase_places.py`처럼 async가 아니다 — `StateStore`가 동기 Protocol이라
  기존 호출부(`agent_runtime.py` 등)를 하나도 안 건드리기 위함.

---

## 1. 테이블 설계

기존 `get_state`/`save_state`, `get_history`/`save_history`가 항상 "전체를
읽고 통째로 다시 쓰는" 방식이라(개별 필드 upsert 아님), 그 패턴을 그대로
따라간다. 반면 ChangeLog/Trace는 이미 append 전용이라 행 단위 insert로 간다.

| 테이블 | 대응 모델 | 키 | 비고 |
|---|---|---|---|
| `agent_states` | `AgentState` | `session_id` (PK) | `user_conditions`, `api_context`는 jsonb로 통째 저장 (14개 필드를 컬럼화하지 않음 — B는 값 의미를 판단 안 하므로 굳이 정규화할 이유 없음) |
| `recommendation_histories` | `RecommendationHistory` | `session_id` (PK) | `recommended`, `rejected`를 jsonb 배열로 통째 저장. read-modify-write 기존 패턴 유지 |
| `condition_change_logs` | `ConditionChangeLog` | `id` (bigserial) + `session_id` 인덱스 | append-only, 행 1개 = 기록 1건 |
| `trace_records` | `TraceRecord` | `id` (bigserial) + `session_id` 인덱스 | append-only, 행 1개 = 기록 1건 |

## 2. 구현체

- 새 파일: `app/state/supabase_store.py`
- 클래스 `SupabaseStateStore`가 `StateStore` Protocol의 10개 메서드를 전부 동기로 구현
- 에러 처리: `supabase_places.py`의 `SupabaseRepositoryError(AppError)` 패턴을 그대로 재사용(같은 예외 클래스 import)
- 설정: 기존 `settings.supabase_url` / `settings.supabase_secret_key` 그대로 사용 (신규 설정 없음)
- 등록: `get_store()`가 반환하는 `_default_store`를 교체하는 시점은 별도 결정 사항으로 둔다 — 이 설계안은 "구현체 추가"까지만 다룬다. Phase 1 인메모리에서 실제로 전환하는 시점/방법은 이후 논의

## 3. 테스트

- `tests/test_supabase_place_repository.py`와 동일한 방식: `httpx.MockTransport`로 실제 네트워크 없이 검증
- 새 파일: `tests/state/test_supabase_store.py`
- 기존 `InMemoryStateStore` 테스트가 검증하는 계약(복사본 반환, append-only에 delete 없음 등)을 동일하게 적용

## 4. 이번 전환으로 안 바뀌는 것

- `StateStore` Protocol 정의
- `service.py`, `history.py`, `session.py`, `agent_runtime.py` — 전부 무수정
- 기존 계약 문서(`agent-state-contract-v1.md`, `llmops-trace-contract-v1.md`)
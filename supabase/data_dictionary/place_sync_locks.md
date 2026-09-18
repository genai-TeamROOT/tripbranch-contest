# place_sync_locks 데이터 딕셔너리

## 개요

`public.place_sync_locks`는 같은 시·군·구의 장소 동기화가 겹쳐 돌지 않게 막는 잠금 테이블입니다. PostgREST 요청은 DB 세션을 계속 유지하지 않아 세션 기반 advisory lock을 쓸 수 없으므로, 지역별로 잠금 행을 하나 두고 그 행의 존재와 만료 시각으로 배타 실행을 보장합니다.

잠금 확인과 획득은 `try_acquire_place_sync_lock(area_code, district_code, sync_run_id, lock_ttl)` 함수가 `INSERT ... ON CONFLICT`의 조건부 `UPDATE`로 한 트랜잭션에서 처리하고, 해제는 `release_place_sync_lock(area_code, district_code, sync_run_id)`이 합니다. TTL 기본값은 2시간입니다.

동기화가 끝나면 행을 지우므로, 아무 실행도 돌지 않는 평시에는 0행입니다(2026-08-25 기준 0행).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `area_code` | text | 아니오 | 잠금을 건 지역코드입니다. `district_code`와 묶어 PK를 이룹니다. 공백만 있는 값은 제약으로 막습니다. | `11` | 지역·구 단위로 잠금이 하나만 존재하도록 강제합니다. |
| `district_code` | text | 아니오 | 잠금을 건 시·군·구 코드입니다. `area_code`와 함께 PK입니다. | `110` | 다른 구의 동기화는 서로 막지 않고 동시에 돌 수 있게 합니다. |
| `sync_run_id` | uuid | 아니오 | 이 잠금을 쥔 실행의 ID입니다. `place_sync_runs.id`를 참조하며 유니크 제약이 걸려 있어 한 실행이 두 지역을 동시에 잠글 수 없습니다. 실행 이력이 지워지면 잠금도 함께 지워집니다(`on delete cascade`). | `58251948-794a-4981-a44a-fe3cd6ef4f60` | 해제 요청이 정말 그 실행에서 온 것인지 확인합니다. |
| `acquired_at` | timestamptz | 아니오 | 잠금을 획득한 시각입니다. 기본값은 `now()`입니다. | `2026-08-25T16:43:04+09:00` | 잠금이 얼마나 오래 유지되고 있는지 봅니다. |
| `expires_at` | timestamptz | 아니오 | 잠금 만료 시각입니다. 반드시 `acquired_at`보다 뒤여야 합니다. | `2026-08-25T18:43:04+09:00` | 죽은 프로세스가 남긴 잠금을 다음 실행이 가져갈 수 있게 하는 기준입니다. |

## 사용 시 유의사항

- 활성 잠금은 덮어쓰지 않습니다. 획득 함수의 조건부 `UPDATE`는 만료된 잠금이거나 같은 실행의 갱신 요청일 때만 통과합니다.
- 만료된 잠금을 새 실행이 가져가면, 그 잠금을 쥐고 있던 이전 실행이 `running` 상태로 남아 있는 경우 `place_sync_runs`에서 `failed`로 바뀌고 `error_summary`에 `STALE_SYNC_LOCK_REPLACED`가 기록됩니다. 끝나지 않은 실행이 영원히 `running`으로 남지 않게 하는 장치입니다.
- 해제는 `sync_run_id`까지 일치할 때만 삭제합니다. 늦게 종료된 이전 프로세스가 새 실행의 잠금을 실수로 풀어버리는 것을 막기 위해서입니다.
- 이 테이블이 비어 있다고 해서 동기화가 실패한 것은 아닙니다. 정상 종료 시 행을 지우므로 0행이 평시 상태입니다. 반대로 오래된 `expires_at`을 가진 행이 남아 있으면 중단된 실행을 의심합니다.
- TTL을 0 이하로 주면 함수가 예외를 냅니다.
- RLS가 켜져 있고 두 함수의 실행 권한도 `service_role`에만 부여돼 있습니다. `anon`·`authenticated`로는 테이블도 함수도 쓸 수 없습니다.

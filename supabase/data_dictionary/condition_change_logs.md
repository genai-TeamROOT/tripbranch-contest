# condition_change_logs 데이터 딕셔너리

## 개요

`public.condition_change_logs`는 `agent_states.user_conditions`가 바뀔 때마다 남는 변경 기록입니다. Package B 소유이며, append-only 테이블입니다 — 기존 행을 수정하거나 삭제하는 경로가 없고, 새 행만 계속 쌓입니다. `id`(bigserial)가 PK이고 `(session_id, id)` 인덱스로 세션별 시간순 조회를 지원합니다.

사용자 원문 발화와 LLM 원문 응답은 이 테이블에 기록하지 않습니다 — 조건이 "무엇에서 무엇으로" 바뀌었는지만 남깁니다(agent-state-contract-v1.md 2.8절, 5.5절).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | bigserial | 아니오 | 행 고유 식별자. PK. 생성 순서를 그대로 반영합니다. | `10482` | `(session_id, id)` 인덱스로 시간순 조회의 정렬 기준으로 사용합니다. |
| `session_id` | text | 아니오 | 변경이 발생한 세션. `agent_states.session_id`와 같은 값 체계를 씁니다. | `sess_1755840000000a1b2c3d4e5f6a` | 이 세션의 조건 변경 이력 전체를 조회합니다. |
| `run_id` | text | 아니오 | 변경이 발생한 요청의 run_id. | `run_1755840005000b2c3d4e5f6a7b` | 어떤 한 번의 요청이 몇 개의 조건을 동시에 바꿨는지 묶어서 봅니다. |
| `seq` | integer | 아니오 | 같은 run_id 안에서 여러 변경이 한 번에 들어올 때의 순서 번호. | `0` | 한 요청 안에서 조건이 적용된 순서를 재현합니다. |
| `op` | text | 아니오 | 변경 방식. `Add`/`Update`/`Remove`/`Reset` 중 하나(자유 문자열이며 DB 레벨 CHECK 제약은 없습니다). | `Update` | 필드가 "새로 추가"됐는지 "통째로 교체"됐는지 등 변경 성격을 구분합니다. |
| `field` | text | 예 | 변경된 `UserConditions` 필드 이름. `Reset`인 경우 특정 필드가 아니라 세션 전체/부분이 초기화된 것이라 `null`입니다. | `current_location` | 특정 필드의 변경 이력만 추적할 때 필터로 사용합니다. |
| `before_value` | jsonb | 예 | 변경 전 값. 타입은 필드마다 다릅니다(문자열, 배열 등 — `Any`). | `"경복궁"` | 롤백이나 "왜 이렇게 바뀌었는지" 디버깅에 사용합니다. |
| `after_value` | jsonb | 예 | 변경 후 값. | `"북촌한옥마을"` | 현재 값이 어떤 경로로 만들어졌는지 재현합니다. |
| `reset_scope` | text | 예 | `Reset` 작업일 때만 채워지는 초기화 범위(예: `soft`/`history`/`full`, session.py의 3가지 초기화 종류). | `soft` | 대화 초기화가 조건만 지웠는지, 이력까지 지웠는지 구분합니다. |
| `applied_at` | timestamptz | 아니오(기본값 `now()`) | 이 변경이 실제로 적용된 시각. | `2026-08-25T09:05:00+09:00` | 시간순 정렬, 특정 시점 이후 변경만 조회할 때 사용합니다. |

## 사용 시 유의사항

- `before_value`/`after_value`는 필드마다 타입이 다른 자유 형식(JSONB)입니다 — `place_types`처럼 배열 필드는 배열이, `current_location`처럼 문자열 필드는 문자열이 그대로 담깁니다.
- 여러 변경이 한 번에 들어와도 그중 하나라도 유효하지 않으면 전체를 적용하기 전에 미리 걸러냅니다 — 즉 이 테이블에 남은 행들은 전부 "성공적으로 적용된" 변경입니다. 거부된 변경 시도 자체는 별도로 기록되지 않습니다.
- append-only이므로 `UPDATE`/`DELETE`로 지우는 애플리케이션 경로가 없습니다. 유일한 삭제 경로는 만료 세션 정리 스크립트(`delete_change_logs`, D-074)의 세션 단위 일괄 삭제뿐이며, 개별 행을 골라 지우는 기능은 없습니다.
- `op`/`field`/`reset_scope`는 DB 레벨 CHECK 제약이 없는 자유 텍스트입니다 — 실제 허용값은 애플리케이션 코드(`state/service.py`)가 정의합니다.

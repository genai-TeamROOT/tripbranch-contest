# recommendation_histories 데이터 딕셔너리

## 개요

`public.recommendation_histories`는 세션 1개(`session_id`)의 추천·거절·폐점 제외 이력을 담는 테이블입니다. Package B 소유이며, `session_id`가 PK입니다. `agent_states`와 마찬가지로 애플리케이션이 통째로 읽고(`get_or_create`) 통째로 다시 쓰는(`save_history`) 방식으로 갱신됩니다.

세 이력 컬럼(`recommended`/`rejected`/`closed_excluded`)은 각각 JSONB **배열**이며, append-only 원칙을 애플리케이션 레벨에서 지킵니다(과거 항목을 지우거나 고치지 않고 새 항목만 뒤에 추가). 배열 안 항목의 구조는 "recommended 항목 필드" 등 하위 표를 참고하세요.

장소 상세 정보(이름·주소·좌표 등)는 원칙적으로 저장하지 않습니다 — 그건 Package C가 매 요청마다 최신값을 주므로 중복으로 들고 있을 필요가 없습니다. `name`/`distance_km`류 필드는 이 원칙의 명시적 예외입니다(아래 하위 표 설명 참고).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `session_id` | text | 아니오 | 세션 식별자. PK이며 `agent_states.session_id`와 같은 값 체계를 씁니다. | `sess_1755840000000a1b2c3d4e5f6a` | `agent_states`와 조인해 세션의 현재 조건과 이력을 함께 조회합니다. |
| `user_id` | uuid | 예 | `agent_states.user_id`와 동일한 규칙(비어 있을 때만 채우고 덮어쓰지 않음, FK 없음). | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | "이 사용자가 과거에 추천받은 장소" 같은 사용자 단위 분석에 사용합니다. |
| `recommended` | jsonb array | 아니오(기본값 `[]`) | 노출된 장소 이력. 하위 필드는 "recommended 항목 필드" 표 참고. | `[{"place_id":"2824887","run_id":"run_...","rank":1,"shown_at":"2026-08-25T09:00:00+09:00"}]` | 다음 추천 요청 시 제외 목록(이미 보여준 장소) 계산에 사용합니다. |
| `rejected` | jsonb array | 아니오(기본값 `[]`) | 사용자가 명시적으로 거절한 장소 이력. 하위 필드는 "rejected 항목 필드" 표 참고. | `[{"place_id":"1234567","run_id":"run_...","reason_code":"too_far","rejected_at":"2026-08-25T09:01:00+09:00"}]` | "확실히 싫다고 한 장소"를 대화 리셋 후에도 계속 제외합니다. |
| `closed_excluded` | jsonb array | 아니오(기본값 `[]`) | D의 하드 필터가 폐점이라 걸러낸 후보 이력(TP-82). `recommended`/`rejected`와 분리된 별도 배열입니다 — "노출했다"로 잘못 취급되면 COMPARE의 "첫 번째"가 실제로 안 보여준 장소를 가리키게 됩니다. `clear_recommended()` 호출 시 함께 비워집니다(운영시간은 시각에 따라 바뀌므로 영구 보관하지 않음). | `[{"place_id":"9876543","run_id":"run_...","excluded_at":"2026-08-25T21:00:00+09:00"}]` | 밤 시간대 등 폐점 비율이 높을 때 같은 후보가 매번 재수집되는 것을 방지합니다. |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 이력이 마지막으로 갱신된(항목이 추가된) 시각입니다. | `2026-08-25T09:01:00+09:00` | 이력이 최근에 갱신됐는지 확인합니다. |

### recommended 항목 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `place_id` | string | 장소 식별자(`places.content_id`). |
| `run_id` | string | 이 장소를 노출한 요청의 run_id. |
| `rank` | int | 노출 순위. 방문 순서(`ScheduleItem.order`)도 겸하므로 별도 순서 필드는 없습니다. |
| `shown_at` | datetime | 노출된 시각. |
| `name` | string \| null | 장소 이름. SCHEDULE-09 2단계 전용 — 지명 검색이 호출마다 다른 좌표로 resolve되는 문제(Naver local search fallback) 때문에, 매 턴 재검색 대신 이 값을 그대로 씁니다(2026-08-11 실사용 재현). |
| `estimated_arrival` | string \| null | SCHEDULE 전용. 예상 도착 시각. |
| `estimated_duration_min` | int \| null | SCHEDULE 전용. 예상 체류 시간(분). |
| `travel_to_next_min` | int \| null | SCHEDULE 전용. 다음 장소까지 예상 이동 시간(분). |
| `reason` | string \| null | SCHEDULE 전용. 선정 이유. |
| `distance_km` | float \| null | COMPARE 전용. 추천 시점에 계산된 거리 스냅샷 — 최신값이 아니라 이 스냅샷을 그대로 써야 "그때 비교한 데이터"가 유지됩니다(int-04-compare.md §13). |
| `remaining_minutes` | int \| null | COMPARE 전용. 남은 운영시간(분) 스냅샷. |
| `environment_type` | string \| null | COMPARE 전용. 실내/실외 등 환경 유형 스냅샷. |

### rejected 항목 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `place_id` | string | 장소 식별자. |
| `run_id` | string | 거절이 발생한 요청의 run_id. |
| `reason_code` | string \| null | 거절 사유 코드. Package A가 해석한 값을 그대로 저장하며 B는 검증하지 않습니다. |
| `rejected_at` | datetime | 거절된 시각. |

### closed_excluded 항목 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `place_id` | string | 장소 식별자. |
| `run_id` | string | 이 후보가 하드 필터로 제외된 요청의 run_id. |
| `excluded_at` | datetime | 제외된 시각. |

## 사용 시 유의사항

- 세 배열 모두 애플리케이션이 append-only 원칙을 지키는 것이지, DB 제약(트리거 등)으로 강제하고 있지는 않습니다 — 배열 타입(`jsonb_typeof = 'array'`) 체크만 걸려 있습니다.
- `recommended`/`rejected`는 영구 보관되지만 `closed_excluded`는 `clear_recommended()` 호출 시(부분 초기화 포함) 함께 비워집니다 — 운영시간은 시각에 따라 바뀌므로(닫혀 있던 곳이 다음 날 열림) 영구 보관하면 안 됩니다.
- `name`/`distance_km`류 COMPARE·SCHEDULE 전용 필드는 RECOMMEND-only 흐름에서는 전부 `null`로 남습니다 — 값이 없다고 이상한 게 아닙니다.
- `session_id` 삭제 시(만료 세션 정리, D-074) 이 테이블도 함께 삭제 대상입니다.

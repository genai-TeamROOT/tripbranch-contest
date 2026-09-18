# agent_states 데이터 딕셔너리

## 개요

`public.agent_states`는 대화 세션(`session_id`) 1개의 현재 상태를 담는 테이블입니다. Package B(Agent State/Memory) 소유이며, `session_id`가 PK입니다. 행 단위 부분 갱신이 아니라 애플리케이션이 상태를 통째로 읽고(`get_state`) 통째로 다시 쓰는(`save_state`) 방식으로 갱신됩니다. 클라이언트(anon/authenticated)는 직접 접근할 수 없고 FastAPI 서버(secret key)를 통해서만 사용합니다.

**이 테이블에는 상태에 있는 필드가 그대로 들어오지 않습니다** — `store.for_persistence(state)`가 저장 직전에 사본을 만들어 기기 좌표 세 필드를 뺍니다(2026-09-08, `6df2e24`). 아래 "기기 좌표는 저장되지 않는다" 절을 먼저 보세요.

`user_conditions`/`api_context`는 여러 하위 값을 한 번에 담는 JSONB 객체 컬럼입니다 — 하위 필드는 이 문서의 "user_conditions 하위 필드"/"api_context 하위 필드" 표를 참고하세요.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `session_id` | text | 아니오 | 세션 식별자. PK입니다. 생성 시각을 앞에 둬 문자열 정렬만으로 시간순이 됩니다. | `sess_1755840000000a1b2c3d4e5f6a` | 이 세션의 조건·이력·실행기록·피드백을 전부 조회하는 조인 키로 사용합니다. |
| `user_id` | uuid | 예 | 검증된 게스트/회원 신원(`Principal.user_id`)이 연결되면 채워집니다. 값이 비어 있을 때만 채우고, 이미 있으면 절대 덮어쓰지 않습니다(D-063 결정 3). `auth.users`로 FK를 걸지 않습니다(D-063 결정 4 — 익명 계정 정리와 충돌 방지). | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | 세션 소유권 검증(`session.verify_ownership()`, D-073), "이 사용자의 세션 목록" 조회에 사용합니다. |
| `user_conditions` | jsonb object | 아니오(기본값 `{}`) | 사용자 발화에서 추출된 조건 15개+를 담는 객체입니다. B는 각 하위 값의 허용 범위를 검증하지 않습니다(Package A 책임). | `{"current_location":"경복궁","transport":"walk"}` | 다음 추천 요청 시 조건을 그대로 이어받아 재사용합니다. |
| `api_context` | jsonb object | 아니오(기본값 `{}`) | 외부 API로 확보한 GPS·날씨 데이터를 담는 객체입니다. `condition_version` 증가 판정에서 제외됩니다(조건 변경이 아니라 배경 데이터 갱신이라서). | `{"gps_location":"37.5,127.0","gps_location_updated_at":"2026-08-25T09:00:00+09:00"}` | 위치 재확인 UX(30분 경과 판정), 날씨 조건 판정에 사용합니다. |
| `condition_version` | integer | 아니오(기본값 `0`) | 조건이 몇 번 바뀌었는지 세는 카운터입니다. 0 이상이어야 합니다. | `4` | 클라이언트가 마지막으로 본 버전과 비교해 조건이 바뀐 걸 감지합니다. |
| `last_run_id` | text | 예 | 이 세션에서 마지막으로 처리한 요청의 run_id입니다. | `run_1755840005000b2c3d4e5f6a7b` | 직전 응답을 다시 참조하거나 재조정할 때 기준으로 씁니다. |
| `last_intent` | text | 예 | 마지막으로 분류된 인텐트입니다(예: `RECOMMEND`, `SCHEDULE`). SCHEDULE 재조정 시 relabel 직후 `set_last_intent()`로 재동기화됩니다. | `RECOMMEND` | 다음 발화가 이전 인텐트의 연속인지 판단하는 데 참고합니다. |
| `recent_turns` | jsonb array | 아니오(기본값 `[]`) | 최근 완료된 대화 턴 최대 5개를 시간순으로 저장합니다. 각 원소는 `user_input`, `intent`, `question_type`, `place_names`, `offered_action`, `at`으로 구성됩니다. | `[{"user_input":"다리 다쳤어","intent":"GENERAL","offered_action":"recommend_nearby_pharmacy"}]` | 다음 턴의 "응", "그 근처" 같은 문맥을 해석할 때 사용합니다. Gemini 호출 시 이전 사용자 발화는 `user`, 구조화된 처리 요약은 `model` 역할로 전달합니다. |
| `situation_state` | jsonb object | 예 | 상황형 대화에서만 쓰는 단기 상태입니다. `current_situation`, `recent_constraints`, `rejected_actions`, `pending_offer`를 담습니다. | `{"current_situation":"minor_injury","rejected_actions":["recommend_nearby_pharmacy"],"pending_offer":null}` | 다침·날씨·동행 불편 등 상황을 이어가고, 사용자가 거절한 제안을 반복하지 않으며, 직전 제안에 대한 짧은 수락/거절을 해석합니다. |
| `pending_clarification` | text | 예 | 직전 턴이 되묻기로 끝났다면 그 사유 코드입니다(예: `location_required`). B는 판단하지 않고 A가 준 값을 보관만 합니다. | `location_required` | 사용자의 다음 답변을 "새 요청"이 아니라 "되묻기 답변"으로 처리할지 판단합니다. |
| `pending_info_context` | jsonb object | 예 | INFO의 장소 후보 되묻기(`pending_clarification = "place_ambiguous"`)에서 원래 질문의 문맥을 보관합니다. `question_type`, `place_context`는 필수이고 `specific_question`, `visit_time`은 선택입니다. Package B는 값을 해석하지 않고 저장만 하며, 허용값 정의는 Package A의 책임입니다(D-100). | `{"question_type":"parking","place_context":"explicit","specific_question":"종각 주차장 정보"}` | 사용자가 후보 버튼을 누르면 장소명만으로 다시 분류하지 않고, 원래의 주차·혼잡도 등 질문 유형을 그대로 복원해 이어서 조회합니다. |
| `ignore_operating_hours_until` | timestamptz | 예 | "운영 중이 아닌 곳도 볼게요"를 선택하면, 이 시각까지는 매 턴 다시 묻지 않고 폐점 후보도 포함합니다. | `2026-08-25T15:00:00+09:00` | 하드 필터(영업시간)를 일시적으로 완화할지 판단합니다. |
| `status` | text | 아니오(기본값 `'active'`) | 세션 상태입니다. `active` 또는 `expired`만 허용됩니다. 만료 판정은 조회 시점에만 일어나는 lazy 방식이라, 실제로 30분간 활동이 없어도 이 컬럼 값이 즉시 `expired`로 바뀌진 않습니다. | `active` | 만료된 세션에 새 요청이 오면 오류 없이 새 세션을 자동 발급하도록 분기합니다. |
| `created_at` | timestamptz | 아니오(기본값 `now()`) | 세션이 처음 생성된 시각입니다. | `2026-08-20T09:00:00+09:00` | 세션 생애주기 분석, 만료 정리 스크립트의 기준일 계산 등에 사용합니다. |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 조건이 바뀌는 등 상태가 갱신된 시각입니다. GPS 갱신처럼 `last_active_at`만 건드리고 이 컬럼은 안 건드리는 경우도 있어, 자동 갱신 트리거를 달지 않고 애플리케이션이 필드별로 다르게 관리합니다. | `2026-08-25T09:05:00+09:00` | 조건이 실제로 바뀐 마지막 시점을 확인합니다. |
| `last_active_at` | timestamptz | 아니오(기본값 `now()`) | 이 세션이 마지막으로 활동한 시각입니다(조건 변경뿐 아니라 GPS 갱신 등도 포함). 30분 세션 TTL 판정과 만료 세션 정리 스크립트(`cleanup_expired_sessions.py`, D-074, 30일 기준)의 기준 컬럼입니다. | `2026-08-25T09:10:00+09:00` | 세션 만료 여부 판정, 만료 세션 정리 대상 선별에 사용합니다. |

| `title` | text | 예 | 대화 목록에 보여줄 이름(마이그레이션 `202609030002_add_agent_states_title.sql`). 기존 행은 `recent_turns[0].user_input`의 앞 200자로 채워졌습니다. `(user_id, last_active_at desc)` 부분 인덱스가 함께 만들어졌습니다(`user_id is not null`). | `홍대 반나절 코스 짜줘` | 사이드바 채팅 히스토리 목록에 표시합니다. |
| `location` | text | 예 | 대화 목록에 보여줄 장소(마이그레이션 `202609030004_add_agent_states_location.sql`). 기존 행은 `user_conditions.search_center`의 앞 200자로 채워졌습니다. | `마포구 홍대입구역` | 목록 한 줄에 어디 얘기였는지 함께 보여줍니다. |

### user_conditions 하위 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `current_location` | string \| null | 현재 위치(지명 또는 좌표 문자열). |
| `search_center` | string \| null | 검색 기준점. 조사("~에서/까지")로 출발점이 명시된 발화만 채워지며, 그 외에는 null로 두어 사용자 위치 기준 거리 계산이 적용되게 합니다(D-067, D-071). |
| `place_types` | string[] | 장소 유형 목록(예: `["카페","공원"]`). 기본값 빈 배열. |
| `place_tags` | string[] | 장소 태그 목록. 기본값 빈 배열. |
| `weather` | string \| null | 사용자가 언급한 날씨 조건. |
| `weather_intent` | string \| null | 날씨에 대한 태도(예: 더위를 피하고 싶다). |
| `concentration_intent` | string \| null | 혼잡도 선호. |
| `transport` | string \| null | 이동수단. |
| `max_travel_time` | int \| null | 이동 가능 시간(분). |
| `time_available` | int \| null | 머무를 수 있는 시간(분). |
| `environment` | string \| null | 실내/실외 등 환경 선호. |
| `companion` | string \| null | 동행 정보. |
| `budget` | string \| null | 예산. |
| `exclude_tags` | string[] | 제외할 태그. 추가/삭제만 허용되고 통째로 교체는 안 됩니다. 기본값 빈 배열. |
| `special_requirements` | string[] | 기타 특수 요구사항. 기본값 빈 배열. |
| `taste_query` | string \| null | 취향 근거 검색용 자유 텍스트 질의(Package D의 RAG 파이프라인 입력). |
| `travel_origin` | string \| null | 이동시간 기준점 판정. `user_location` 또는 `search_center` 중 하나(B는 값을 검증하지 않음, D-071). |
| `accessibility_needs` | list[string] | 무장애 요구(2026-09-02 신설, `14fb1d3`). A의 추출 프롬프트가 채웁니다. 복수 필드이므로 기본값은 빈 배열입니다. |

### api_context 하위 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `gps_location` | string \| null | 마지막으로 확보한 GPS 좌표 문자열. **DB에 저장되지 않습니다** — 아래 절 참고. |
| `api_weather` | string \| null | 외부 날씨 API로 확보한 원문 값. |
| `gps_location_updated_at` | datetime \| null | GPS 값이 갱신된 시각(기술적 TTL 판정용, 1시간). **DB에 저장되지 않습니다.** |
| `api_weather_updated_at` | datetime \| null | 날씨 값이 갱신된 시각. |
| `gps_location_confirmed_at` | datetime \| null | 사용자가 "현재 위치 다시 가져오기"로 실제 재확인한 시각(PR #188). `gps_location_updated_at`과 별개 — "N분 전 위치로 계속"을 선택하면 이 값은 갱신되지 않습니다. 기존 세션은 null(최초 재확인 대상). **DB에 저장되지 않습니다 — 채우는 코드도 읽는 코드도 없습니다.** |

### 기기 좌표는 저장되지 않는다 (2026-09-08, `6df2e24`)

`api_context`의 세 필드는 **상태에는 있고 이 테이블에는 없습니다.**

```
gps_location              저장 안 함
gps_location_updated_at   저장 안 함
gps_location_confirmed_at 저장 안 함
api_weather               저장함
api_weather_updated_at    저장함
```

`store.for_persistence(state)`가 저장 직전에 사본을 만들어 셋을 `null`로 비웁니다. **필드를 스키마에서 없앤 것이 아니라 DB에 적지 않는 것**이라, 한 요청을 처리하는 동안에는 그대로 씁니다 — 그래서 원본을 건드리지 않고 사본을 만들어 돌려줍니다.

- **저장소 두 구현이 모두 이 함수를 거칩니다**(`store.py`의 인메모리, `supabase_store.py`). 인메모리만 값을 계속 들고 있으면 저장이 사라져 깨지는 경로를 테스트가 통과시킵니다
- 개인정보가 이유입니다. 로그아웃해도 남고 세션마다 한 벌씩 쌓여 2026-09-07 기준 1,800건 이상이었습니다
- 뺄 수 있는 근거는 **화면이 매 턴 좌표를 실어 보낸다**는 점입니다. 서버 사본은 "요청에 없을 때를 위한 여벌"이었는데 실제로는 매번 옵니다. 그 여벌을 읽던 자리는 둘(Runtime의 도구 조회 GPS, INFO 도보시간)이고 둘 다 없으면 이번 턴 값만 씁니다. 30분 재확인은 화면이 `sessionStorage`의 시각으로 판정합니다(`utils/locationRefresh.ts`)
- **장소 이름(`user_conditions.current_location` · `search_center`)은 남습니다.** 함께 빼려다 되돌렸습니다 — 되묻기 버튼이 세션 조건을 베껴 재실행하는데 이름이 사라지면 위치가 빈 채로 돌아 또 되묻기로 끝납니다. 이름까지 빼려면 그 재실행 경로를 먼저 요청값 기준으로 고쳐야 합니다(TP-256)

**이 테이블을 조회해 좌표를 기대하는 쿼리는 이제 전부 `null`을 받습니다.** 1.4절의 유효 기간 규칙(1시간 TTL)은 한 요청 안에서만 의미가 있습니다. 계약 문서는 `agent-state-contract-v1.md` 5.6절 "저장 경계"를 보세요.

### recent_turns 원소 / situation_state 하위 필드

`recent_turns`는 원문 대화 로그 테이블이 아닙니다. 세션 문맥 유지에 필요한 최근 5턴만 남기며, 만료 세션 정리 시 `agent_states` 행과 함께 삭제됩니다. 응답 전문은 저장하지 않고, 다음 턴 해석에 필요한 구조화된 요약만 보관합니다.

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `recent_turns[].user_input` | string | 해당 턴의 사용자 발화입니다. 저장 시 최대 300자로 제한합니다. |
| `recent_turns[].intent` | string \| null | 해당 턴에서 최종 분류된 인텐트입니다. |
| `recent_turns[].question_type` | string \| null | 정보 조회·추천 등의 세부 질문 유형입니다. |
| `recent_turns[].place_names` | string[] | 응답에서 식별된 장소명 목록입니다. |
| `recent_turns[].offered_action` | string \| null | 사용자에게 제안한 다음 행동/도구 코드입니다. |
| `recent_turns[].at` | datetime | 턴이 완료된 시각입니다. |
| `situation_state.current_situation` | string \| null | 현재 상황 코드(예: `minor_injury`, `rain`, `closed_place`)입니다. |
| `situation_state.recent_constraints` | string[] | 상황에서 확인된 단기 제약(예: `walk_difficult`)입니다. |
| `situation_state.rejected_actions` | string[] | 이번 세션에서 사용자가 거절한 제안/도구 코드입니다. 반복 제안을 막는 데 사용합니다. |
| `situation_state.pending_offer` | string \| null | 다음 사용자 턴의 수락/거절을 기다리는 제안/도구 코드입니다. 처리 후 `null`로 비웁니다. |

## 사용 시 유의사항

- `user_conditions`/`api_context`는 애플리케이션이 통째로 읽고 통째로 다시 쓰는 JSONB 객체입니다 — 특정 하위 키만 부분 갱신(`jsonb_set` 등)하는 별도 경로는 없습니다.
- `pending_info_context`는 `pending_clarification = "place_ambiguous"`일 때만 유효합니다. 다른 되묻기 코드로 바뀌거나 되묻기가 해제되면 애플리케이션이 함께 `NULL`로 비웁니다. 이 값만 남아 있다고 해서 활성 INFO 되묻기 상태라는 뜻은 아닙니다.
- `recent_turns`와 `situation_state`는 `pending_clarification`을 대체하지 않습니다. 전자는 대화 문맥, 후자는 상황형 제안 상태이고, 후자는 명시적 되묻기 사유 코드와 별도로 관리합니다.
- `recent_turns[].user_input`은 외부 입력입니다. LLM에 보낼 때도 시스템 지시문에 합치지 않고 역할이 분리된 대화 이력으로만 전달합니다.
- `status`가 `active`라고 해서 세션이 진짜로 살아있다는 보장은 없습니다 — 만료 판정이 조회 시점에만 일어나는 lazy 방식이라, `last_active_at` 기준 30분이 지났는데도 이 컬럼 값은 그대로 `active`로 남아 있을 수 있습니다.
- `user_id`가 비어 있는 것은 정상입니다 — 게스트가 아직 신원 발급을 안 받았거나, `Authorization` 헤더 없이 온 요청일 수 있습니다.
- 30일 이상 미사용 세션은 `scripts/cleanup_expired_sessions.py`가 `condition_change_logs`/`trace_records`/`recommendation_histories`를 먼저 지우고 마지막으로 이 테이블 행을 지웁니다(D-074) — 삭제된 `session_id`로의 조회는 "세션 없음"으로 처리됩니다.

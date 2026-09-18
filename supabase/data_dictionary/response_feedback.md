# response_feedback 데이터 딕셔너리

## 개요

`public.response_feedback`은 챗봇의 응답 1건(`run_id` 단위)에 대한 사용자의 좋아요/싫어요 반응을 담는 테이블입니다(AF-12/LLMOps, roadmap.md 14번). Package B 소유이며 append-only입니다. `id`(bigserial)가 PK이고, `session_id`/`run_id`에 각각 인덱스가 있습니다.

`trace_records`처럼 개별 실행 단계(`trace_id`) 단위가 아니라 `run_id`(그 턴의 최종 응답) 단위로 붙습니다 — 사용자는 "이 답변"에 반응하는 것이지, 그 답변을 만든 개별 단계에 반응하는 게 아닙니다. `run_id`로 `trace_records`와 조인하면 "이 반응이 어떤 `prompt_version`/`scoring_version`에서 나온 응답에 대한 것인지" 추적할 수 있습니다.

조회 편의를 위해 `recorded_at`을 KST로 함께 보여주는 뷰 `public.response_feedback_kst`가 있습니다(저장은 그대로 UTC, 뷰만 KST 컬럼을 추가 노출).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | bigserial | 아니오 | 행 고유 식별자. PK. | `3021` | `(session_id, id)` 인덱스로 시간순 조회의 정렬 기준으로 사용합니다. |
| `session_id` | text | 아니오 | 반응이 발생한 세션. | `sess_1755840000000a1b2c3d4e5f6a` | 세션 단위 피드백 조회(`get_feedback`)에 사용합니다. |
| `run_id` | text | 아니오 | 반응이 달린 응답의 run_id. | `run_1755840005000b2c3d4e5f6a7b` | `trace_records.run_id`와 조인해 어떤 버전이 만든 응답인지 찾습니다(`get_dislike_feedback`). |
| `rating` | text | 아니오 | 반응 값. `like` 또는 `dislike`만 허용(DB CHECK 제약). 화면 버튼이 만드는 고정값이라 `step` 등과 달리 검증합니다. | `dislike` | 좋아요/싫어요 전체 건수 집계(`GET /feedback/stats`)에 사용합니다. |
| `comment` | text | 예 | "싫어요" 클릭 시 사용자가 선택적으로 남기는 짧은 자유 사유(500자 이하 CHECK 제약). `like`에는 입력창이 없어 사실상 dislike 전용이지만, 스키마에서 rating으로 강제하지는 않습니다. | `장소가 너무 멀어요` | 표준 사유(`reason_code`)로 못 담는 구체적인 불만을 검토할 때 사용합니다. |
| `user_input` | text | 예 | 피드백을 남긴 턴의 사용자 발화 원문. 피드백 남긴 턴에 한해서만 저장하며(대화 전체 로그 아님), 프론트가 텍스트를 못 찾거나 안 보내면 `null`. | `경복궁 근처 카페 추천해줘` | "이 반응이 무엇에 대한 것인지" 검토할 근거로 사용합니다. |
| `assistant_message` | text | 예 | 피드백을 남긴 턴의 챗봇 응답 원문. `user_input`과 동일한 저장 범위·이유. | `이런 곳들을 찾아봤어요.` | `user_input`과 함께 반응의 맥락을 재현합니다. |
| `intent` | text | 예 | 그 턴의 assistant_text 메시지가 이미 들고 있던 인텐트 값을 그대로 복사한 것(예: `RECOMMEND`, `INFO`). B는 검증하지 않습니다. | `RECOMMEND` | "어떤 인텐트가 싫어요를 많이 받는지" 필터링·집계(top_intents)에 사용합니다. |
| `reason_code` | text | 예 | 개선 집계용 표준 싫어요 사유. 7개 값 중 하나만 허용(DB CHECK 제약): `intent_mismatch`/`clarification_unhelpful`/`context_not_preserved`/`location_misunderstood`/`conditions_not_applied`/`recommendation_not_suitable`/`other`. `like` 행이나 사유를 안 고른 `dislike` 행은 `null`. | `recommendation_not_suitable` | 사유별 건수 집계(`reason_code_counts`, `unclassified` 포함 8개 키)에 사용합니다. |
| `recorded_at` | timestamptz | 아니오(기본값 `now()`) | 이 반응이 기록된 시각(UTC로 저장). | `2026-08-25T00:00:00Z` | 기간 필터(`since`/`until`) 조회, 최근순 정렬에 사용합니다. |

## 사용 시 유의사항

- `comment`/`user_input`/`assistant_message`는 전부 자유 텍스트라 개인정보·민감정보가 섞일 수 있습니다 — 보관기간 정책은 아직 없고 지금은 개발/테스트 단계 전제입니다. 실서비스 공개 전에는 `guest-auth-design.md` 9-3절(보관기간·자동삭제·동의 지점)을 이 컬럼들에도 적용할지 다시 결정해야 합니다.
- `reason_code`가 `null`이라고 사유가 없다는 뜻은 아닙니다 — `comment`에 자유 텍스트로만 남겼을 수 있습니다. 집계 시(`GET /feedback/stats`) 이런 행은 `unclassified`로 잡힙니다.
- `user_input`/`assistant_message`/`intent`/`comment`/`reason_code`는 모두 2026-08-21 이후 순차적으로 추가된 컬럼입니다 — 그 이전에 쌓인 행(있다면)은 이 컬럼들이 전부 `null`입니다.
- `response_feedback_kst` 뷰는 `id`/`session_id`/`run_id`/`rating`/`user_input`/`assistant_message`/`intent`/`comment`/`recorded_at`/`recorded_at_kst`/`reason_code` 순서로 컬럼을 노출합니다 — PostgreSQL이 `create or replace view`에서 기존 컬럼 순서 변경을 허용하지 않아(42P16), 새 컬럼은 항상 뒤에만 추가됩니다.
- append-only이므로 개별 행을 골라 지우는 기능은 없습니다. `agent_states`/`recommendation_histories`/`condition_change_logs`/`trace_records`와 달리 세션 만료 정리(D-074) 대상에서도 제외됩니다 — 세션 생애주기와 무관한 별도 분석 데이터라는 판단입니다.

# trace_records 데이터 딕셔너리

## 개요

`public.trace_records`는 대화 한 턴을 처리하는 동안 거치는 실행 단계(step) 1건을 기록하는 테이블입니다(AF-12/LLMOps). Package B 소유이며 append-only입니다 — 기존 행을 수정·삭제하는 경로가 없습니다. `id`(bigserial)가 PK이고 `(session_id, id)` 인덱스로 세션별 시간순 조회를 지원합니다.

`step`/`prompt_version`/`scoring_version`/`variant_id`/`error_type`은 호출자(A/C/D)가 해석한 값을 그대로 저장하며, B는 그 의미를 판단하거나 값의 허용 목록을 강제하지 않습니다(경계 원칙, llmops-trace-contract-v1.md 2절).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | bigserial | 아니오 | 행 고유 식별자. PK. | `58213` | `(session_id, id)` 인덱스로 시간순 조회의 정렬 기준으로 사용합니다. |
| `session_id` | text | 아니오 | 이 실행이 속한 세션. | `sess_1755840000000a1b2c3d4e5f6a` | 세션 단위 실행 이력 조회(`get_traces`)에 사용합니다. |
| `run_id` | text | 아니오 | 이 실행이 속한 요청(턴)의 run_id. | `run_1755840005000b2c3d4e5f6a7b` | `response_feedback.run_id`와 조인해 "이 반응이 어떤 버전에서 나왔는지" 추적합니다. |
| `trace_id` | text | 아니오 | 이 실행 단계 자체의 고유 식별자. B가 기록 시점에 발급합니다. | `trace_1755840005100c3d4e5f6a7b8c` | 개별 단계를 유일하게 식별할 때 사용합니다. |
| `step` | text | 아니오 | 실행 단계 이름. 값 집합이 고정돼 있지 않고 호출자가 자유롭게 정합니다(예: `llm_interpret`, `tool_fetch`, `scoring`). | `llm_interpret` | step별로 묶어 평균 지연시간·에러율을 집계합니다(`GET /trace/stats`, TP-157). |
| `prompt_version` | text | 예 | 이 단계가 사용한 프롬프트 슬롯 버전(예: `router.classify@1`). LLM을 호출하지 않는 단계는 `null`. | `intent_v1.2` | 프롬프트 버전별 품질·지연시간 비교에 사용합니다. |
| `scoring_version` | text | 예 | 이 단계가 사용한 점수 계산 로직 버전. Scoring 단계가 아니면 `null`. | `score_v0.3` | 스코어링 버전 변경 전후 결과 비교에 사용합니다. |
| `variant_id` | text | 예 | A/B 실험 실험군 식별자. 현재 실험 자체가 없어 항상 `null`입니다(당장 쓰지 않는 기능을 미리 만들지 않는다는 원칙 — YAGNI). | `null` | (아직 실사용 없음) 향후 실험군별 결과 비교에 사용될 예정입니다. |
| `latency_ms` | integer | 예 | 이 단계 소요 시간(밀리초). 0 이상이어야 합니다. | `220` | 느린 단계를 찾아 성능 튜닝 대상을 정합니다(`avg_latency_ms`/`max_latency_ms` 집계). |
| `token_usage` | integer | 예 | 이 단계에서 사용한 토큰 수. 0 이상이어야 합니다. LLM을 호출하지 않는 단계는 `null`. | `350` | 비용 추적, 프롬프트 길이 최적화 판단에 사용합니다. |
| `error_type` | text | 예 | 이 단계에서 발생한 오류 종류(예: `timeout`, `no_data`). 정상 완료면 `null`. | `timeout` | 최근 에러 목록·step별 에러 건수 집계(`GET /trace/stats`)에 사용합니다. |
| `recorded_at` | timestamptz | 아니오(기본값 `now()`) | 이 기록이 저장된 시각. | `2026-08-25T09:00:00+09:00` | 시간순 정렬, 기간 필터(`since`/`until`) 조회에 사용합니다. |

## 사용 시 유의사항

- `step` 값은 고정된 목록이 아닙니다 — A/C/D가 자유롭게 새 step 이름을 붙일 수 있어, 이 문서에 없는 값이 나와도 정상입니다.
- 기록 자체가 실패해도(예: DB 접속 문제) 그 오류가 사용자 응답 흐름까지 끊지 않도록 안전장치가 있습니다 — 즉 이 테이블에 빠진 실행이 있을 수 있고, "기록이 없다"가 "실행이 없었다"를 보장하지 않습니다.
- 세션 단위 조회(`get_traces(session_id)`)는 구현돼 있지만 API로 노출되지 않았습니다(TP-157 기준 미사용) — 세션을 가리지 않는 전체 통계 조회(`GET /trace/stats`)만 dev-ops 패널에 연결돼 있습니다.
- append-only이므로 개별 행을 골라 지우는 기능은 없습니다. 유일한 삭제 경로는 만료 세션 정리 스크립트(`delete_traces`, D-074)의 세션 단위 일괄 삭제입니다.

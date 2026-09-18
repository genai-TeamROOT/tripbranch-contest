# LLMOps Trace Contract v1 (Package B)

- 작성자: 이태화
- 작성일: 2026-07-28
- 상태: Implemented (2026-08-05, B-07 반영 완료 — Q1~Q3 해결, 7절 참고)
- 협의 대상: Package A, Package D
- 적용 범위: AF-12 중 Trace·버전 기록만 (A/B 집계·재현성은 범위 밖 — 7절 참고)

## 이 문서의 목적

`agent-state-contract-v1.md` 4절(식별자)과 5.6절(저장 범위)에 이미 예고돼 있던
`prompt_version`/`scoring_version`/`variant_id`, 실행 메타데이터(지연 시간·토큰
사용량·오류 유형), `trace_id` 발급을 실제로 구현하기 위한 계약이다.

이 문서는 새로운 약속을 만드는 게 아니라, **B-01이 이미 한 약속을 지키는 문서**다.

## 전제

- B-01 계약을 상속한다. 여기 없는 내용(세션·조건·이력 등)은 `agent-state-contract-v1.md`를 따른다.
- Phase 1 범위는 "기록"까지다. A/B 실행 결과 집계, 동일 조건 재현 기능은 다음 단계로
  미룬다 — 기록이 먼저 쌓여야 집계할 게 생기기 때문이다.
- B는 여기서도 해석하지 않는다. `prompt_version` 같은 값이 무엇을 의미하는지는 몰라도
  되고, 그냥 받아서 `trace_id`와 묶어 저장한다.

---

## 1. 왜 지금 시작하나

B-01 5.6절 "저장한다" 목록에 이미 있던 항목인데, B-02/B-03 어디서도 구현되지 않았다.

```
- 실행 메타데이터 (지연 시간, 토큰 사용량, 오류 유형)
- 버전 정보 (prompt_version, scoring_version, variant_id)
```

B-01 4.5절도 `trace_id`를 "정의만, 발급은 Agent Runtime 연결 이후"라고 이미 표시해뒀다.

**현재 상태 (코드로 확인)**

```
StateApplyRequest.prompt_version   존재하지만 항상 None (state_transform.py에 TODO만 있음)
new_trace_id()                     존재하지만 어디에도 저장 안 됨 —
                                    agent_runtime.py에서 C 호출 request_id로
                                    1회성으로 쓰이고 버려짐
scoring_version / variant_id       필드 자체가 없음
latency / token / error_type       필드 자체가 없음
```

즉 AF-12는 완전히 새로 설계하는 게 아니라, B-01이 남겨둔 빈칸을 채우는 작업이다.

---

## 2. 무엇을 기록하나

| 필드 | 타입 | 의미 | 채우는 주체 |
| --- | --- | --- | --- |
| `trace_id` | string | run 내부 한 단계(LLM 호출·Tool 호출·Scoring 등) | B가 발급 |
| `run_id` | string | 이 trace가 속한 요청 | 기존과 동일 |
| `session_id` | string | 이 trace가 속한 대화 | 기존과 동일 |
| `step` | string | 어느 단계인지 (`"llm_interpret"`, `"tool_fetch"`, `"scoring"` 등) | 호출자(A/C/D) |
| `prompt_version` | string \| null | 이 단계에 쓰인 Prompt 버전 | 호출자(A) |
| `scoring_version` | string \| null | 이 단계에 쓰인 Scoring 버전 | 호출자(D) |
| `variant_id` | string \| null | A/B 실험 variant | 호출자 |
| `latency_ms` | int \| null | 소요 시간 | 호출자 |
| `token_usage` | int \| null | 토큰 사용량 (LLM 단계만 해당) | 호출자 |
| `error_type` | string \| null | 실패 시 오류 분류 | 호출자 |
| `metrics` | object \| null | 그 단계의 도메인 지표 (TP-242) | 호출자 |
| `recorded_at` | datetime | 기록 시각 | B가 채움 |

B는 이 값들의 의미를 모른다. `step`이 `"llm_interpret"`이든 `"scoring"`이든 그냥
문자열로 받아서 저장할 뿐이고, 어떤 버전이 "더 나은" 버전인지는 판단하지 않는다 —
B-01의 경계 원칙(1절)을 그대로 따른다.

`metrics`도 같은 원칙을 따른다(TP-242). 키와 값을 검증하지 않는다 — **무엇을 셀지는
그 기능을 아는 쪽이 정한다.** B가 지표 목록을 들고 있으면 지표를 하나 늘릴 때마다 B를
고쳐야 하고, 그러면 세는 것을 미루게 된다. 컬럼을 하나씩 늘리지 않고 `jsonb` 하나로
둔 것도 같은 이유다.

**두 가지는 지킨다.**

- **사용자 텍스트를 담지 않는다.** 장소 이름·발화는 이 저장소의 것이 아니다.
  `trace_records`가 대화 삭제 때 지워지지 않는 근거가 "사용자 텍스트가 없다"는
  것이므로(수명 규칙, 6절), 이름을 실으면 그 근거가 무너지고 보관 규칙까지 다시
  봐야 한다. 이름을 세야 하면 개수만 남긴다.
- **비율을 담지 않는다.** 분자와 분모를 각각 남기고 나누는 것은 집계 쪽에 맡긴다.
  비율을 기록해 두면 나중에 분모를 바꿀 수 없다.

지표를 싣지 않는 단계는 `null`로 남는다. 빈 객체로 채우면 "지표가 없는 단계"와
"지표가 비어 있는 턴"을 구분할 수 없다.

---

## 3. 언제 발급·기록되나

B-01 4.4절 "처리 순서"를 그대로 확장한다.

```
run_id 발급 (조건 병합 이전, 기존과 동일)
  → 각 단계(LLM 호출 / Tool 호출 / Scoring)마다 trace_id 발급
  → 그 단계가 끝나면 record_trace() 호출 (호출자가 결과를 들고 옴)
```

`trace_id`는 **단계 시작 시점**에 발급하고, 기록은 **단계가 끝난 시점**에 한다 —
진행 중에는 `latency_ms`를 알 수 없기 때문이다.

---

## 4. 계약 (신규 진입점 1종)

```
record_trace(session_id, run_id, step, *, prompt_version=None, scoring_version=None,
             variant_id=None, latency_ms=None, token_usage=None, error_type=None,
             metrics=None)
  → trace_id 발급 + 저장, TraceRecordResponse 반환
```

B-01의 4계약(조회 / 적용 / 기록 / api 갱신) 패턴을 그대로 따라 5번째 계약으로 추가한다.

조회 전용 함수(`get_traces(run_id)`)는 필요해지면 그때 추가한다 — 지금은 "쌓기"만
하고 "꺼내 쓰는 쪽"은 아직 아무도 없다(YAGNI. B-02에서 검증/적용을 분리했던 것과
같은 이유로, 안 쓰는 기능을 미리 만들지 않는다).

---

## 5. 저장 정책

B-01 5.6절과 동일한 원칙을 따른다.

**저장한다**: 위 2절 필드 전부 (구조화된 값만)

**저장하지 않는다**: LLM 원문 응답, Prompt 원문 텍스트, Chain-of-Thought — 계약
문서 5.6절과 동일한 이유(원문은 AF-11 평가 Fixture 영역이지 B의 역할이 아님)

**append-only**: `ChangeLog`와 동일하게 trace 기록도 삭제 메서드를 만들지 않는다
(B-02 3-5절 "실수를 막는 장치"와 동일한 원칙 — 실행 이력은 지울 이유가 없다).

---

## 6. 이번 범위 밖 (다음 단계)

```
- A/B 실행 결과 집계 (variant별 성공률·평균 latency 비교)
- 동일 입력·Context·버전으로 재현 실행
- Version Registry (버전 자체를 등록·조회하는 기능) — 지금은 버전 "값"만 기록하고,
  그 버전이 뭘 의미하는지 등록하는 건 범위 밖
```

이 셋은 전부 "기록이 먼저 쌓인 뒤에" 의미가 생기는 기능이라, 지금 만들면 가짜
데이터로 설계하게 된다. 기록 장치부터 완성하고 실제로 값이 쌓이는 걸 본 다음에
설계하는 게 안전하다.

### 6.1 Version Registry 미착수 상태에서 실제로 쓰고 있던 임시 대체 방식 (2026-08-14 추가)

기본프로젝트 최종 발표에서 "프롬프트 개선이 실제로 몇에서 몇으로 나아졌는지 수치로
보여야 한다"는 피드백을 받았다. Version Registry(정식 조회·비교 기능)는 여전히
범위 밖이지만, `PROMPT_VERSION`(`app.providers.gemini_prompts`)이 이미 07-28 이후
`1.0.12`까지 12차례 올랐는데도 그 각각의 변경 이유·효과를 한 곳에 모아 추적하는
장치가 없다는 게 이번에 드러난 실제 공백이다.

SCHEDULE 작업 중 이미 아래 패턴을 반복 사용했다는 걸 재확인했다 — 이건 Version
Registry의 정식 기능은 아니지만, 그 축소판 역할을 사실상 해왔다.

```
1. 변경 전/후로 같은 시나리오 세트를 실 Gemini로 반복 실행하는 벤치마크 스크립트를 작성한다
   (예: scripts/compare_schedule_thinking_budget.py,
        scripts/compare_classify_extract_thinking_budget.py)
2. 소요 시간뿐 아니라 정확도(케이스별 기대값 일치 여부)도 함께 측정한다
3. 결과를 CSV로 남긴다 (test_results/*.csv)
4. PR 본문에 "변경 전 X → 변경 후 Y" 형태로 수치를 명시한다
```

**앞으로는 이 패턴을 판별·추출·편성 프롬프트를 고칠 때마다 의도적으로 반복하고,
PROMPT_VERSION이 오를 때마다 아래 표에 한 줄씩 남기기로 한다.** 정식 Version
Registry(조회 API 등)는 여전히 범위 밖이지만, 최소한의 변경 이력은 지금부터
파일로 누적한다 — 값이 쌓여야 나중에 "Registry가 정말 필요한가"도 근거를 갖고
판단할 수 있다.

**PROMPT_VERSION 변경 이력 (소급 가능한 범위만 기록, 이전 버전은 소급 기록 없음)**

| 버전 | 일자 | 변경 내용 | 측정한 수치 |
| --- | --- | --- | --- |
| 1.0.6 → 1.0.7 | (SCHEDULE-06 무렵) | 직전 턴 Intent(`last_intent`)를 프롬프트에 노출, SCHEDULE 되묻기 이어가기 규칙 추가 (D-059) | 회귀 테스트 통과 여부만 확인, 정확도 수치 없음 — 소급 측정 불가 |
| (버전 미상) | 08-12 | SCHEDULE 직후 순수 추천 요청 오분류 수정 — 프롬프트에 예외 규칙 추가(Fix A) | pytest 신규 5건, 실 Gemini 재현 시나리오 확인 — 정확도 수치 없음 |
| (버전 미상) | 08-13 | `time_available`/`max_travel_time` 조건 추출 프롬프트·Field description에 분 단위 규칙 명시(단위 환산 버그 수정) | "가용 시간=300 → 병합 후 300" 단발 재현 확인 — 여러 케이스에 걸친 정확도 수치는 없음 |
| 1.0.12 | 08-13 이후 | (표 신설 시점 기준 최신, 변경 내용 소급 기록 없음) | — |
| 1.0.12 → 1.0.13 (현재) | 08-18 | SCHEDULE 두 system instruction에 후보별 운영시간 참고 규칙 + `warnings` 필드는 시스템이 채운다는 지시 추가(폐점 스탑 감지, int-07-schedule.md v2.2 참고) | 프롬프트 힌트는 구조적 후처리(`planner._finalize_items`)와 함께 적용 — 회귀 테스트(폐점/운영중/미확인/24시간 4개 케이스)로 후처리 동작만 확인, 프롬프트 힌트 자체의 효과는 수치화하지 않음 |

**정직하게 남겨야 할 것**: 이 표의 앞 세 줄도 "수치로 개선을 증명"하지 못한다 —
재현 확인이나 회귀 테스트 통과 여부만 있고, 여러 케이스에 걸친 정확도 비교는
없다. 유일하게 체계적으로 수치화된 사례는 `thinking_budget` 벤치마크(속도 +
정확도 동시 측정, 위 패턴 1~4 전부 적용)뿐이다. 앞으로 프롬프트 내용 자체를
바꾸는 변경에도 이 벤치마크 패턴을 동일하게 적용하는 것이 이번 피드백에 대한
실질적인 답이다.

---

## 7. 확인 필요 (A·D에게) — 2026-08-05 해결됨

```
Q1  step 이름을 누가 정하나 — B가 enum으로 강제할지, 호출자가 임의 문자열로
    넘기게 할지. (B-01 경계 원칙상 B가 값의 의미를 모르므로 자유 문자열이
    맞다고 보는데, 그러면 오타로 같은 단계가 다른 이름으로 쌓일 위험이 있어
    A/D와 미리 이름을 맞춰두는 게 나을 수 있음)
    → 해결: A 확인 완료. "llm_interpret"/"tool_fetch"/"scoring" 그대로 사용.
      바꾸고 싶은 이름 없음(A 회신). enum 강제 없이 자유 문자열로 확정.

Q2  prompt_version/scoring_version/variant_id를 실제로 누가 언제 채워서
    보낼 준비가 됐는지 — A는 Prompt 버전을 어떻게 관리 중인지, D는 Scoring
    버전을 어떻게 매기고 있는지
    → 해결(prompt_version/scoring_version): A가 `app.providers.gemini_prompts.
      PROMPT_VERSION`("agent-interpret-prompts-1.0.0"), D가 `app.domain.
      scoring.SCORING_VERSION`("recommendation-scoring-1.0.0")을 각각 모듈-semver
      패턴으로 신설. agent_runtime.py의 llm_interpret/scoring 단계 호출부에
      연결 완료(커밋 9ef8295, PR #92).
    → 미해결(variant_id): 아직 아무도 값을 채울 준비가 안 됨. A/B 실험이
      실제로 필요해지기 전까지 None으로 유지(6절 "이번 범위 밖"과 동일한
      YAGNI 판단 — 실험 설계 없이 값만 미리 채우지 않는다).

Q3  이 trace 기록을 실제 HTTP 흐름 어디에 꽂을지 — run_agent_flow() 안에
    자연스러운 자리(LLM 호출 직후, Tool 호출 직후, Scoring 직후)가 있어
    보이는데, run_agent()가 아직 라우터에 안 물려 있는 상태(B-03 참고)라
    지금 넣어도 실제로 쌓이진 않음. A와 연결 시점 조율 필요
    → 해결: B-07 착수 시점엔 run_agent()가 이미 라우터에 연결돼 있어서
      더 이상 유효한 블로커가 아니었음(B-03에서 해소됨). run_agent_flow()를
      직접 읽어 LLM(2단계)/Tool(5단계)/Scoring(6단계) 호출 지점이 코드
      구조상 이미 명확히 분리돼 있음을 확인하고 B가 직접 판단해 배선함 —
      A/D 확인 없이도 코드로 풀리는 질문이었음.
```

---

## 8. 응답 피드백 (roadmap.md 14번, 2026-08-21 추가)

이 문서 원래 적용 범위(맨 위 "적용 범위" 항목)는 "Trace·버전 기록만"이었지만,
같은 append-only 저장 패턴을 그대로 재사용하는 인접 기록이라 이 문서에
같이 남긴다.

**무엇이 다른가**: TraceRecord는 `trace_id`(run 내부 한 단계) 단위로 붙지만,
FeedbackRecord는 `run_id`(그 턴의 최종 응답) 단위로 붙는다. 사용자는 "이
답변"에 좋아요/싫어요를 누르는 것이지, 그 답변을 만든 개별 단계(LLM 호출/
Tool 호출/Scoring)에 반응하는 게 아니기 때문이다.

**계약(신규 진입점 1종)**:

```
record_feedback(session_id, run_id, rating)  # rating: "like" | "dislike"
  → 저장, RecordFeedbackResponse(recorded_at) 반환
```

**B가 검증하는 예외적인 필드**: `step`/`prompt_version` 등 대부분의 Trace
필드는 호출자가 넘긴 자유 문자열을 그대로 저장하고 B는 의미를 판단하지
않는다(1절 경계 원칙). `rating`은 예외다 — 화면 버튼이 만드는 고정된 두
값이라(호출자가 임의로 새 값을 정의할 여지가 없음) Literal["like",
"dislike"]로 검증하고, 잘못된 값은 요청 단계에서 거부한다.

**조회·분석**: `feedback.list_dislikes(limit)`가 "싫어요"만 최근순으로
모은다 — 다른 조회 메서드와 달리 세션 범위가 아니라 테이블 전체가
대상이다("나쁜 답변 찾기"는 특정 세션이 아니라 서비스 전체 응답 중에서
찾는 분석 작업이라 session_id로 좁힐 수 없다). `service.get_dislike_feedback()`가
이 목록의 각 `run_id`로 같은 세션의 `get_traces()`를 다시 불러 조인해서
`prompt_version`/`scoring_version`을 채운 뒤 `GET /api/feedback/dislikes`로
노출한다. `run_id` 단위 trace 조회(`get_traces(run_id)`) 자체는 여전히
만들지 않았다 — FeedbackRecord가 이미 `session_id`를 들고 있어서 세션
단위 `get_traces(session_id)`를 그대로 재사용하면 되기 때문이다.

**저장소**: `response_feedback` 테이블(append-only, `trace_records`와 동일한
구조 — 마이그레이션 `202608210001_create_response_feedback.sql`).

**8-1. 피드백 턴 원문 저장 (D-068, 2026-08-21 추가)**

계약이 `record_feedback(session_id, run_id, rating, user_input=None,
assistant_message=None)`로 확장됐다. 두 필드 모두 nullable — 없어도
`rating` 기록은 그대로 유효하다.

이 프로젝트의 기본 원칙(`docs/design/guest-auth-design.md` 9절)은 대화
원문을 저장하지 않는 것이다 — `ConditionChangeLog` 독스트링에도 "사용자
원문 발화와 LLM 원문 응답은 기록하지 않는다"고 명시돼 있다. `user_input`/
`assistant_message`는 이 원칙에 대한 **좁은 예외**다: 테스트 중 피드백을
검토할 때 "이 반응이 뭐에 대한 것인지" 확인할 방법이 없어서, 사용자가
좋아요/싫어요를 실제로 남긴 턴에 한해서만 그 턴의 질문·답변 원문을 함께
저장하기로 했다(전체 대화 로그가 아님 — D-068 참고). `rating`과 달리 이
필드들은 자유 문자열이라 B가 내용을 검증·해석하지 않는다(1절 경계
원칙 그대로 적용) — 다만 "저장 대상 데이터의 새 카테고리"라는 점에서
`rating`의 검증 예외와는 성격이 다르다.

프론트는 `session_id`/`run_id`가 이미 있는 결과 카드(recommendation_result/
schedule_result) 렌더링 시점에, 그 카드 바로 앞의 `user_text`/
`assistant_text` 메시지를 거슬러 올라가며 찾아서(`findTurnText`,
`frontend/src/utils/turnText.ts`) 좋아요/싫어요 클릭 시 함께 보낸다 —
reducer 단계에서 미리 박아두지 않는 이유는 스트리밍 답변이 카드보다 늦게
완성될 수 있어(SSE), 클릭 시점 기준 최신 텍스트를 반영하기 위해서다.

마이그레이션: `202608210002_add_feedback_turn_text.sql` (nullable text
컬럼 2개 추가).

**8-2. intent/comment 확장 (D-069, 2026-08-21 추가)**

계약이 `record_feedback(..., intent=None, comment=None)`로 더 확장됐다.
둘 다 nullable.

`intent`는 그 턴의 `assistant_text` 메시지가 이미 들고 있는 값을 그대로
옮겨오는 것뿐이라(개발자 뷰 인텐트 배지에 쓰는 값과 동일 소스) B는
검증하지 않는다 — "어떤 인텐트가 싫어요를 많이 받는지" 필터링용.

`comment`는 "싫어요" 사유로 사용자가 직접 남기는 자유 텍스트(선택)다.
user_input/assistant_message와 같은 위험 등급(자유 텍스트)으로 취급하고
같은 이유로 nullable이다. 프론트는 "별로예요" 클릭을 즉시 전송하지 않고
인라인 입력창(제출/건너뛰기)을 먼저 띄운 뒤 하나의 레코드로 함께
보낸다 — append-only 구조에서 사유 없이 먼저 보내고 나중에 사유 있는
레코드를 또 쌓으면 같은 run_id의 dislike가 `list_dislikes()`에서 중복
집계되기 때문이다. DB 레벨에서도 500자 CHECK 제약이 걸려 있다(아래 참고).

검토했지만 채택하지 않은 것(D-069 참고): 전 컬럼 NOT NULL(텍스트를 못
찾는 예외 상황에서 rating 기록 자체가 실패하게 됨), `is_clarification`/
`clarification_code`(되묻기 메시지에는 피드백 버튼 자체가 없어 채울
방법이 없음).

**병합 후기**: 같은 날 develop에 팀원이 독립적으로 구현한 comment 기능
PR이 먼저 merge됐다 — 같은 목적을 다른 방식으로 구현한 우연한 충돌.
`comment` 컬럼·500자 DB CHECK 제약·`response_feedback_kst` 조회 뷰는
develop의 마이그레이션(`202608210002_add_response_feedback_kst_view.sql`,
`202608210003_add_comment_to_response_feedback.sql`)을 그대로 채택했고,
FeedbackButtons UI도 아이콘 기반의 develop 버전(더 완성도 높음)을
채택했다. intent만 이쪽 추가로 남았다. 마이그레이션 번호가 둘 다
202608210002/003을 써서 겹쳐, 이쪽 파일은 004/005로 재번호했다.

마이그레이션: `202608210004_add_feedback_turn_text.sql`(user_input/
assistant_message 2개), `202608210005_add_feedback_intent.sql`(intent 1개 +
조회 뷰에 새 컬럼들 반영).

**8-3. reason_code 확장 (D-070, 2026-08-21 추가)**

계약이 `record_feedback(..., reason_code=None)`로 더 확장됐다. nullable —
기존 클라이언트(reason_code 없이 dislike만 보내는)와 과거 행 모두 그대로
읽고 쓸 수 있다.

`reason_code`는 "싫어요"를 집계 가능한 표준 사유로 분류하기 위한 값이다.
`comment`(자유 텍스트, D-069)와는 성격이 다르다 — `rating`처럼 B가 값을
검증하는 예외적인 필드로, `FeedbackReasonCode` 7값(intent_mismatch/
clarification_unhelpful/context_not_preserved/location_misunderstood/
conditions_not_applied/recommendation_not_suitable/other) Literal과 DB
CHECK 제약이 이중으로 값을 제한한다. "좋아요"에는 reason_code/comment
둘 다 붙일 수 없다 — `RecordFeedbackRequest`의 `model_validator`가 요청
단계에서 거부한다.

이 기능은 팀원이 독립적으로 구현해 PR #214로 develop에 먼저 merge했다
(D-069의 comment 기능과 같은 패턴 — 다만 이번엔 충돌 없이 순수 추가만
있었다). B는 구현하지 않고 상태·저장 계약 검토와 마이그레이션 적용만
맡았다. intent/user_input/assistant_message 캡처 아키텍처(render-time
`findTurnText`, 8-1절)는 PR #214가 시도했던 reducer 시점 임베딩 방식으로
바뀌지 않고 그대로 유지됐다.

**마이그레이션 버그와 수정**: `202608210006` 초안이 `response_feedback_kst`
뷰를 재정의하며 `intent` 컬럼 위치를 `rating` 바로 뒤로 옮기려다
PostgreSQL의 `create or replace view` 제약(기존 컬럼 이름·순서 변경
불가, 42P16 `cannot change name of view column`)에 걸려 실서비스 적용이
실패했다. 기존 컬럼 순서(`id, session_id, run_id, rating, user_input,
assistant_message, intent, comment, recorded_at, recorded_at_kst`)를
그대로 두고 `reason_code`를 맨 뒤에 추가하도록 수정했다 —
`create or replace view`로 컬럼을 새로 노출하려면 항상 기존 순서 뒤에만
추가해야 한다는 제약을 이번에 확인했다.

마이그레이션: `202608210006_add_feedback_reason_code.sql`(reason_code 컬럼
+ CHECK 제약 + dislike 전용 부분 인덱스 + 조회 뷰 컬럼 추가).

---

## 9. 갱신 이력

| 일자 | 변경 |
| --- | --- |
| 07-28 | 초안 작성 (AF-12 시작) |
| 08-05 | B-07 완료 반영: record_trace()를 run_agent_flow() 3단계(llm_interpret/tool_fetch/scoring)에 배선. 7절 Q1(step 이름)·Q2(prompt_version/scoring_version, variant_id는 미해결로 유지)·Q3(연결 지점) 해결 상태 반영. 상태 Draft → Implemented |
| 08-14 | 6.1절 신설 — 기본프로젝트 발표 피드백(프롬프트 개선 수치화 필요) 반영. Version Registry는 여전히 범위 밖이지만, 기존에 쓰던 벤치마크 스크립트+CSV+PR 수치 기록 패턴을 표준 절차로 명시하고 PROMPT_VERSION 변경 이력 표 신설(소급 가능한 범위만) |
| 08-18 | PROMPT_VERSION 변경 이력 표에 1.0.13 행 추가(SCHEDULE 폐점 스탑 감지 프롬프트 힌트, int-07-schedule.md v2.2) |
| 08-21 | 8절 신설 — 응답 피드백(좋아요/싫어요) 저장 기능(roadmap.md 14번) 추가. `record_feedback()` 신규 진입점, `response_feedback` 테이블, POST /api/feedback 엔드포인트. 이어서 조회·분석 기능(`list_dislikes()`, `get_dislike_feedback()`, GET /api/feedback/dislikes)도 같은 날 추가 — 저장만 하고 꺼내 쓰는 곳이 없으면 죽은 기능이라는 지적 반영 |
| 08-21 | 8-1절 신설 — 피드백을 남긴 턴에 한해 질문·답변 원문(`user_input`/`assistant_message`)을 함께 저장(D-068). 테스트 중 피드백 검토 편의 목적, 전체 대화 로그 저장과는 다름. 마이그레이션 `202608210004_add_feedback_turn_text.sql`(develop merge로 002→004 재번호) |
| 08-21 | 8-2절 신설 — `intent`(assistant_text 메시지 값 복사) 확장(D-069). comment는 같은 날 develop에 먼저 merge된 팀원 PR을 그대로 채택(DB 500자 CHECK 제약 포함), FeedbackButtons UI도 develop 버전 채택. 마이그레이션 `202608210005_add_feedback_intent.sql`(develop merge로 003→005 재번호) |
| 08-21 | 8-3절 신설 — `reason_code`(구조화된 싫어요 사유) 확장(D-070). 팀원 PR #214 구현을 B가 검토·반영, intent/user_input/assistant_message 캡처는 render-time `findTurnText` 방식 유지(reducer-embedding 방식 미채택). 마이그레이션 `202608210006_add_feedback_reason_code.sql` 적용 중 `response_feedback_kst` 뷰 컬럼 순서 버그(PostgreSQL 42P16) 발견·수정 |
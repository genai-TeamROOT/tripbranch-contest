# Agent 최종 응답(Chat Message) 생성 설계 — 초안

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1.1 |
| 상태 | §6 1차·2차 구현 완료(2026-07-28). 2026-08-07 트리비 페르소나와 RECOMMEND/MODIFY 요약 LLM 추가. 3차(INFO/COMPARE)는 별도 트랙, 미착수 |
| 최종 수정 | 2026-08-07 |
| 관련 코드 | `backend/app/services/runtime/response_composer.py`(`compose_chat_message()`), `backend/app/providers/protocols.py`/`gemini.py`/`gemini_prompts.py`(`generate_general_answer`), `backend/app/schemas.py`(`AgentResponse.message`), `backend/tests/test_response_composer.py` |
| 관련 문서 | `docs/design/recommendation-explainability.md`(D-06, §1/§6 "Response Generator(LLM, TBD)"), `docs/api-contracts.md`(§3 목표 Chat API, TBD) |

`compose_recommendation_message()`(장소 카드 1건에 들어갈 문장)는 이 문서의 대상이
**아니다** — 그대로 유지한다. 이 문서가 다루는 건 그 카드들을 감싸는 **챗봇 말풍선
텍스트**(`AgentResponse.message`)와, RECOMMEND가 아닌 나머지 Intent의 답변을
어떻게 만들지다. §5의 5가지 질문은 팀 전체 확인 없이 **잠정 결정**으로 확정하고
바로 구현했다(아래 §5 참고) — 필요하면 나중에 팀 피드백을 받아 조정한다.

---

## 1. 배경

`docs/design/recommendation-explainability.md`(D-06)가 이미 이 경계를 명시해뒀다.

> §1 범위 제외: "여러 문장을 자연스러운 한 문단으로 잇거나 평가·어투를 더하는 것은
> Response Generator(LLM, `TBD`) 영역이다"
> §6 알려진 제한사항: "여러 문장을 자연스럽게 이어붙이거나 평가·어투를 더하는 것은
> Response Generator(LLM, `TBD`)의 몫이다"

즉 "여러 장소를 하나의 자연스러운 답변으로 묶는 건 A(Runtime) 책임"이라는 합의는
이미 있지만, **구체적인 설계는 아직 정하지 않은 완전히 새로운 영역**이다.

`docs/api-contracts.md` §3(목표 공개 Chat API, `TBD`)에 이미 목표 형태가 스케치돼
있다:

```ts
type ChatResponse = {
  chat_session_id: string;
  recommendation_run_id?: string;
  intent: Intent;
  message: string;                    // ← 이 문서가 다루는 대상
  recommendations?: RecommendationResult[];  // ← 카드. compose_recommendation_message()가 이미 담당
  clarification?: ClarificationRequest;
  warnings?: string[];
  debug?: ChatDebugInfo;
};
```

`message: string`이 바로 이 설계가 채워야 할 필드다.

---

## 2. 두 계층 모델: 카드 vs 챗봇 메시지

| 계층 | 내용 | 담당 함수 | 상태 |
| --- | --- | --- | --- |
| 카드(장소별) | "마감까지 약 4시간 49분 남았어요. 무난한 날씨에 적합한 실내 장소예요. 현재 위치에서 직선거리 약 260m예요." | `compose_recommendation_message(item)` | **완료, 계속 사용** |
| 챗봇 메시지(전체) | 카드들을 소개하는 한두 문장, 또는 카드가 아예 없는 Intent(INFO/GENERAL/OUT_OF_SCOPE 등)의 유일한 답변 | `compose_chat_message(llm_output, ...)` | **완료**(`AgentResponse.message`) |

카드 내용을 챗봇 메시지 안에 길게 다시 풀어쓰지 않는다 — 중복이고, D-06 §3.2("문장
내용 자체를 LLM으로 재작문하지 않고 그대로 노출하는 것을 권장")의 취지에도 맞지 않는다.
챗봇 메시지는 카드를 "소개"만 하고, 상세는 카드가 담당한다. 다만 2026-08-07부터
고정 한 줄 대신 LLM이 추천 카드의 공개 필드만 보고 1~2문장 요약을 생성한다.

## 2.1 챗봇 페르소나

챗봇 이름은 **트리비**다.

트리비는 TripBranch의 국내 여행 챗봇이다. 사용자의 현재 위치나 발화에서 추출한
검색 중심지를 기준으로 날씨, 운영시간, 거리, 혼잡도 선호를 함께 고려해 갈 만한
장소를 안내한다. 말투는 친근한 존댓말을 쓰되, 내부 점수·가중치·시스템 구현 사정은
사용자에게 설명하지 않는다. 확인되지 않은 정보는 단정하지 않고, 아직 지원하지 않는
기능은 솔직하게 안내한다.

서비스 정체성 질문("넌 누구야?", "이름이 뭐야?", "뭘 할 수 있어?")은
`build_interpretation()`에서 LLM 1차 분류 전에 `GENERAL(service_identity)`로 선처리하고,
`generate_general_answer()`에서 트리비 소개로 답한다. Gemini가 이 질문을
`role_request`/`OUT_OF_SCOPE`로 오분류할 수 있어, 이 유형은 프롬프트만 믿지 않는다.

---

## 3. Intent/상태별 현황 (구현 완료 반영)

| Intent / 상태 | 답변 텍스트 | LLM 필요? | 상태 |
| --- | --- | --- | --- |
| RECOMMEND/MODIFY, 추천 있음 | 추천 카드의 공개 필드만 사용한 트리비 말투의 1~2문장 요약 | 예 | **완료** |
| RECOMMEND/MODIFY, `no_data`(후보 0건) | int-03-modify.md §11 정책 문구 그대로("검색 범위를 넓혀볼까요? 다른 종류의 장소도 포함할까요? 운영시간을 확인할 수 없는 장소도 볼까요?") | 아니오 | **완료** |
| LLM 단계 `needs_clarification` | `LLMOutput.clarification.message` 그대로 사용 | 완료(추출 단계에서 이미 생성됨) | **완료** |
| C 단계 `needs_clarification` | `code`별 A 초안 템플릿(4종) | 아니오 | **완료**(팀 피드백은 나중에) |
| C 단계 `unsupported`/`unavailable` | 고정 안내 문구(각각 1개) | 아니오 | **완료**(§5에서 추가로 다룬 범위) |
| GENERAL | 실제 Gemini 호출로 배경지식 답변 생성 | **예 — 실제 신규 LLM 호출** | **완료** |
| OUT_OF_SCOPE | `category`별 고정 거절 템플릿(4종) | 아니오 | **완료** |
| INFO | 없음 — 실제 장소 상세 데이터 연동이 먼저 필요(A-C 계약 §3: INFO Context는 "후속 협의 대상") | 별도 트랙 | **미착수**(임시 안내 문구만 반환) |
| COMPARE | 없음 — 비교 대상 장소의 상세 데이터/로직이 먼저 필요 | 별도 트랙 | **미착수**(임시 안내 문구만 반환) |

INFO/COMPARE는 `compose_chat_message()`가 호출되면 일단 "죄송해요, 이 기능은 아직
준비 중이에요."를 반환한다 — 실제 답변 생성은 C/D와 Context 계약이 정리된 뒤 별도
트랙에서 진행한다(§6 3차).

---

## 4. 구현된 아키텍처

`app/services/runtime/response_composer.py::compose_chat_message()`가 단일 진입점이다.
`app/services/runtime/agent_runtime.py::run_agent_flow()`의 4개 반환 지점 전부에서
호출해 `AgentResponse.message`를 채운다.

```
compose_chat_message(llm_output, *, recommendations=None, tool_status=None,
                      tool_clarification=None, llm) 분기:

├─ llm_output.status == needs_clarification (LLM 단계)
│    → llm_output.clarification.message 그대로 반환
├─ intent == OUT_OF_SCOPE
│    → out_of_scope.category → 템플릿 매핑표(4종, 규칙 기반)
├─ intent == GENERAL
│    → llm.generate_general_answer(topic, original_question) 실제 호출
├─ intent in (RECOMMEND, MODIFY):
│    ├─ tool_status가 needs_clarification/unsupported/unavailable
│    │    → 각각 clarification 템플릿(code별) / unsupported 문구 / unavailable 문구
│    └─ recommendations가 비어 있으면 no_data 템플릿, 있으면
│       고정 wrapper(`"이런 곳들을 찾아봤어요:"`)를 즉시 반환
└─ intent in (INFO, COMPARE)
     → "아직 준비 중" 임시 안내(§3, §6 3차 — 별도 트랙)
```

RECOMMEND/MODIFY 성공 경로는 2026-08-12부터 추천 요약 LLM을 호출하지 않는다.
추천 카드가 이미 D의 결과·근거를 완결해 제공하므로, 한두 문장의 wrapper를 위해 추가로
Gemini를 기다리지 않는다. 이로써 추천 응답의 마지막 LLM 대기(실측 약 3~10초)를 없앤다.
긴 자연어 설명의 가치가 있는 GENERAL·INFO·COMPARE만 LLM 생성 경로를 유지한다.

---

## 5. 잠정 결정 (팀 전체 확인 없이 확정, 2026-07-28)

1. **RECOMMEND/MODIFY wrapper**: 고정 한 줄(`"이런 곳들을 찾아봤어요:"`)을 즉시
   표시한다. 추천 카드가 상세 정보와 근거를 제공하므로 별도 LLM 요약은 만들지 않는다.
2. **GENERAL**: 실제 LLM 호출로 연동. `LLMProvider.generate_general_answer()` 신규
   추가, `RealGeminiProvider`에 구현.
3. **C 단계 clarification 템플릿**: A가 초안 작성해서 바로 구현(`_CLARIFICATION_
   TEMPLATES`, `response_composer.py`). 팀 공유는 나중에 피드백 받는 방식으로.
4. **`no_data` 조건 완화**: int-03-modify.md §11의 기존 정책 문구를 그대로 재사용
   (조건을 시스템이 임의로 완화하지 않고, "검색 범위/장소 종류/운영시간 미확인" 3개
   선택지를 사용자에게 되묻는 문구). 트리거 조건은 "추천 결과가 0건"으로 단순화했다
   (§11 원문의 "< 3개" 임계값은 지금 파이프라인에 없는 개념이라 적용하지 않음 —
   필요하면 후속 조정).
5. **`ChatResponse` TBD**: 외부 공개 API 계약 확정은 별도 트랙. 지금은 A 내부
   설계(`AgentResponse.message` 필드)만 구현했다.

---

## 6. 단계별 진행 현황

| 순서 | 내용 | LLM 신규 호출 | 상태 |
| --- | --- | --- | --- |
| 1차 | RECOMMEND/MODIFY wrapper, `no_data` 템플릿, C 단계 clarification/unsupported/unavailable 템플릿, OUT_OF_SCOPE 템플릿 | 없음 | **완료** |
| 2차 | GENERAL 답변 생성 | 있음 | **완료** |
| 2.5차 | RECOMMEND/MODIFY 추천 결과 요약 생성 | 있음 | **종료** — 2026-08-12 고정 wrapper로 전환 |
| 3차(별도 트랙) | INFO/COMPARE 실제 답변 | TBD | 미착수 — C/D와 Context 계약 재협의 먼저 필요(A-C 계약 §3, §7) |

**FakeLLMProvider도 갱신했다** — `service_identity` 분류/답변과
`generate_recommendation_summary()`는 호환을 위해 Provider 표면에는 남아 있지만,
RECOMMEND/MODIFY 런타임 경로에서는 호출하지 않는다.

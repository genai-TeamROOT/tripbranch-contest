/*
 * 역할: recommendation_result/schedule_result 카드가 뜬 턴의 질문·답변 원문(+intent)을 찾는다.
 * 입력: 렌더링 중인 메시지 배열과 결과 메시지의 인덱스.
 * 출력: 그 턴의 user_text/assistant_text/intent (없으면 undefined).
 * 호출 시점: ChatMessageList가 FeedbackButtons에 넘길 props를 계산할 때 호출된다.
 *
 * recommendation_result/schedule_result 메시지 자체에는 텍스트가 없어서, 렌더링
 * 시점에 바로 앞 메시지들을 거슬러 올라가며 찾는다 — reducer 단계에서 미리
 * 박아두지 않는 이유는 스트리밍 답변이 카드보다 늦게 완성될 수 있어(SSE),
 * 클릭 시점 기준 state.messages에서 찾는 편이 항상 최신 텍스트를 반영하기
 * 때문이다. user_text 경계를 넘어가지 않는다 — 그 전 턴의 답변을 잘못
 * 집어오지 않기 위해서다.
 *
 * intent도 같은 방식으로 찾는다 — assistant_text 메시지 자체가 이미 intent를
 * 들고 있어서(개발자 뷰 인텐트 배지에 쓰는 값과 동일), 별도 조회 없이 그
 * 메시지를 찾을 때 같이 꺼낸다. reducer 최상위 state.user_input 등을 직접
 * 쓰지 않는 이유도 동일하다 — 그 필드들은 세션에 하나뿐이라 새 턴이 오면
 * 덮어써지므로, 화면을 스크롤해 예전 카드에 피드백을 남기면 엉뚱한(최신) 턴의
 * 값이 잘못 붙는다.
 *
 * 답변 자리에 assistant_text 대신 clarification 메시지가 오는 경우도 답변으로
 * 인정한다. clarification 메시지에는 intent가 없어 그 경우 intent는 계속
 * undefined다.
 *
 * **되묻기만 한 턴에는 이제 "feedback" 메시지가 붙지 않는다**(2026-09-08,
 * agentMessages의 isClarificationOnlyTurn). 그래도 이 폴백은 남는다 — 되묻기와
 * 결과 카드가 **함께** 온 턴에는 피드백이 그대로 붙고, 그 턴의 답변 자리에는
 * clarification 메시지가 온다.
 */

import type { ChatMessage } from "../types";

export function findTurnText(
  renderedMessages: readonly ChatMessage[],
  resultIndex: number,
): { userInput?: string; assistantMessage?: string; intent?: string } {
  let userInput: string | undefined;
  for (let i = resultIndex - 1; i >= 0; i--) {
    const candidate = renderedMessages[i];
    if (candidate.type === "user_text") {
      userInput = candidate.text;
      break;
    }
  }

  let assistantMessage: string | undefined;
  let intent: string | undefined;
  for (let i = resultIndex - 1; i >= 0; i--) {
    const candidate = renderedMessages[i];
    if (candidate.type === "user_text") break;
    if (candidate.type === "assistant_text") {
      assistantMessage = candidate.text;
      intent = candidate.intent;
      break;
    }
    if (candidate.type === "clarification") {
      assistantMessage = candidate.text;
      break;
    }
  }

  return { userInput, assistantMessage, intent };
}

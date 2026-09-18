/*
 * 역할: 아직 글자가 오는 중인 말풍선을 찾고, 필요할 때 확정한다.
 * 입력: 화면 메시지 목록.
 * 출력: 스트리밍 말풍선의 위치, 또는 그것을 확정한 새 목록.
 * 호출 시점: TripContext의 스트리밍 관련 리듀서들.
 *
 * 별도 모듈인 이유는 freezeStreamingMessage의 규칙이 화면을 띄우지 않고는
 * 확인하기 어려운 자리에 있어서다 — 이 함수가 필요한 상황(앞 요청이 새 요청에
 * 밀려남)은 컴포저가 응답 중에 잠겨 있어 UI로 재현하기 까다롭다.
 */

import type { ChatMessage } from "../types";

/** 마지막으로 나온 스트리밍 말풍선의 위치. 없으면 -1. */
export function findStreamingMessageIndex(messages: ChatMessage[]): number {
  return messages.reduce(
    (foundIndex, message, index) =>
      message.type === "assistant_text" && message.streaming ? index : foundIndex,
    -1,
  );
}

/*
 * 이번 턴의 캡션(recommendation_caption) 바로 앞자리를 찾는다 — 캡션이 없으면
 * 카드(recommendation_result) 앞자리로 물러난다.
 *
 * 추천 팁은 이제 맨 위에서 실시간으로 채워진다(화면 순서: 팁 → 캡션 → 카드,
 * 2026-09-09 사용자 피드백으로 캡션·카드보다 위로 확정). 배열 끝에 append하면
 * 캡션·카드 아래로 가버리므로, 새 스트리밍 말풍선을 이 자리에 끼워 넣는다.
 * "마지막 user_text 이후" 구간으로만 찾는 이유는 이전 턴의 캡션 앞에 잘못
 * 꽂히는 걸 막기 위해서다 — 캡션도 카드도 없으면(RECOMMEND가 아닌 턴) 기존처럼
 * 배열 끝에 붙인다.
 */
export function findStreamInsertionIndex(messages: ChatMessage[]): number {
  let turnStart = 0;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].type === "user_text") {
      turnStart = index + 1;
      break;
    }
  }
  let fallback = -1;
  for (let index = messages.length - 1; index >= turnStart; index -= 1) {
    if (messages[index].type === "recommendation_caption") return index;
    if (messages[index].type === "recommendation_result") fallback = index;
  }
  return fallback >= 0 ? fallback : messages.length;
}

/*
 * 아직 streaming 표시가 붙어 있는 말풍선을 확정한다.
 *
 * 앞 요청이 새 요청에 밀려나면(beginChatRequest) 그 요청의 뒷정리는 일어나지
 * 않는다 — 화면에는 이미 다음 턴이 그려지고 있어서 건드리면 안 되기 때문이다.
 * 그러면 앞 턴의 말풍선이 streaming인 채로 영영 남아, 글자가 온 적 없는 것은
 * "…"로만 보이고 오던 중이던 것은 커서가 계속 깜빡인다.
 *
 * 새 턴이 시작된다는 것은 앞 턴이 어떤 식으로든 끝났다는 뜻이므로 여기서
 * 정리한다 — 온 데까지는 남기고, 한 글자도 못 받았으면 지운다(CANCEL_CHAT_TURN과
 * 같은 규칙).
 */
export function freezeStreamingMessage(messages: ChatMessage[]): ChatMessage[] {
  const index = findStreamingMessageIndex(messages);
  const streaming = messages[index];
  if (streaming?.type !== "assistant_text" || !streaming.streaming) return messages;
  if (streaming.text === "…") return messages.filter((_, at) => at !== index);
  return messages.map((message, at) =>
    at === index ? { ...streaming, streaming: false } : message,
  );
}

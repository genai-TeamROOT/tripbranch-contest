/* 직전 INFO 상세 카드의 장소명을 다음 INFO 지시어("여기/이곳") 해소에 재사용한다. */

import type { ChatMessage } from "../types";

export function getLatestConversationPlaceName(messages: readonly ChatMessage[]): string | null {
  for (const message of [...messages].reverse()) {
    if (message.type !== "place_info_result") continue;
    const placeName = message.card.place_name?.trim();
    if (placeName) return placeName;
  }
  return null;
}

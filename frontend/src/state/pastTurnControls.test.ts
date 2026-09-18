/*
 * 새 턴에서 무엇을 걷어내고 무엇을 남기는지 못박는다.
 *
 * 걷어낼 것을 빠뜨리면 지난 턴의 버튼이 남아 그때 기준의 요청이 지금 맥락으로
 * 나가고, 남길 것을 잘못 걷어내면 대화를 위로 올렸을 때 그때 무엇을 받았는지가
 * 사라진다. 둘 다 조용히 일어나므로 목록을 여기서 잠근다.
 */

import { isPastTurnControl } from "./TripContext";
import type { ChatMessage } from "../types";

const 걷어낼_것: ChatMessage[] = [
  { id: "a", type: "follow_up_suggestions", suggestions: ["더 보기"] },
  { id: "b", type: "recommendation_actions", has_no_results: false },
  { id: "c", type: "schedule_actions", has_no_schedule: false },
];

const 남길_것: ChatMessage[] = [
  {
    id: "d",
    type: "recommendation_result",
    recommendations: [],
    unverified_recommendations: [],
    elapsed_ms: 0,
    server_elapsed_ms: 0,
  },
  { id: "e", type: "preference_tag_summary", items: [] },
  { id: "f", type: "user_text", text: "안국역 근처" },
  /* 되묻기는 걷어내지 않는다 — 문구가 그 턴의 답변이라 기록으로 남기고,
     선택지만 비운다(withoutPastTurnControls). */
  { id: "g", type: "clarification", text: "실내와 실외 중 어디가 좋으세요?", options: [] },
];

it.each(걷어낼_것)("$type 은 새 턴에서 걷어낸다", (message) => {
  expect(isPastTurnControl(message)).toBe(true);
});

it.each(남길_것)("$type 은 기록이라 남긴다", (message) => {
  expect(isPastTurnControl(message)).toBe(false);
});

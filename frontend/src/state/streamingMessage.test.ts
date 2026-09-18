/*
 * 역할: 밀려난 턴의 스트리밍 말풍선이 정리되는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * 이 규칙이 필요한 상황(앞 요청이 새 요청에 밀려남)은 컴포저가 응답 중에 잠겨
 * 있어 화면으로 재현하기 까다롭다. 그래서 규칙 자체를 여기서 본다.
 */

import { expect, test } from "vitest";
import type { ChatMessage } from "../types";
import { findStreamingMessageIndex, freezeStreamingMessage } from "./streamingMessage";

const DONE: ChatMessage = { id: "done", type: "assistant_text", text: "지난 답변" };

test("글자가 오던 말풍선은 온 데까지 남기고 확정한다", () => {
  const frozen = freezeStreamingMessage([
    DONE,
    { id: "a", type: "assistant_text", text: "여기까지 왔어", streaming: true },
  ]);

  expect(frozen[1]).toMatchObject({ text: "여기까지 왔어", streaming: false });
});

/* 한 글자도 못 받은 말풍선은 "…"만 남아 로딩처럼 보인다 — 지우는 편이 맞다. */
test("한 글자도 못 받은 말풍선은 지운다", () => {
  const frozen = freezeStreamingMessage([
    DONE,
    { id: "a", type: "assistant_text", text: "…", streaming: true },
  ]);

  expect(frozen.map((message) => message.id)).toEqual(["done"]);
});

test("스트리밍 중인 말풍선이 없으면 목록을 그대로 돌려준다", () => {
  const messages = [DONE];

  expect(freezeStreamingMessage(messages)).toBe(messages);
});

/* 말풍선이 여러 개면 마지막 것이 지금 오는 중인 것이다. */
test("스트리밍 말풍선이 여럿이면 마지막을 찾는다", () => {
  const index = findStreamingMessageIndex([
    { id: "a", type: "assistant_text", text: "앞", streaming: true },
    DONE,
    { id: "b", type: "assistant_text", text: "뒤", streaming: true },
  ]);

  expect(index).toBe(2);
});

test("스트리밍 중인 말풍선이 없으면 -1이다", () => {
  expect(findStreamingMessageIndex([DONE])).toBe(-1);
});

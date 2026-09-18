/*
 * 역할: 채팅 요청 취소용 모듈 싱글턴 컨트롤러의 동작을 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { expect, test } from "vitest";
import {
  beginChatRequest,
  cancelChatRequest,
  detachChatRequest,
  isDetachedRequest,
  endChatRequest,
  wasCancelledByUser,
} from "./chatAbortController";

test("cancelChatRequest는 마지막으로 시작한 요청을 중단시킨다", () => {
  const controller = beginChatRequest();

  cancelChatRequest();

  expect(controller.signal.aborted).toBe(true);
});

test("새 요청을 시작하면 아직 끝나지 않은 이전 요청을 먼저 중단한다", () => {
  const first = beginChatRequest();
  const second = beginChatRequest();

  expect(first.signal.aborted).toBe(true);
  expect(second.signal.aborted).toBe(false);
});

test("endChatRequest 이후에는 cancelChatRequest가 그 요청에 영향을 주지 않는다", () => {
  const controller = beginChatRequest();
  endChatRequest(controller);

  cancelChatRequest();

  expect(controller.signal.aborted).toBe(false);
});

test("이미 다른 요청이 시작된 뒤에는 예전 컨트롤러로 endChatRequest를 불러도 새 요청을 건드리지 않는다", () => {
  const first = beginChatRequest();
  const second = beginChatRequest();

  endChatRequest(first);
  cancelChatRequest();

  expect(second.signal.aborted).toBe(true);
});

/*
 * 중단에는 두 종류가 있고 뒷정리가 다르다. "중단" 버튼은 오던 말풍선을 거기까지
 * 얼려 남기고, 화면을 떠나 밀려난 요청은 아무것도 건드리면 안 된다 — 화면에는
 * 이미 다른 대화가 그려져 있기 때문이다.
 */
test("중단 버튼으로 끊긴 요청만 사용자 취소로 표시된다", () => {
  const controller = beginChatRequest();

  cancelChatRequest();

  expect(wasCancelledByUser(controller)).toBe(true);
});

/*
 * 떼어낸 요청을 끊으면 서버가 실행을 취소해 턴이 저장되지 않는다 — 방금 한
 * 질문이 히스토리에서 통째로 사라진다. 화면에만 안 그리고 요청은 끝까지 둔다.
 */
test("화면에서 떼어낸 요청은 끊지 않는다", () => {
  const controller = beginChatRequest();

  detachChatRequest();

  expect(controller.signal.aborted).toBe(false);
  expect(isDetachedRequest(controller.signal)).toBe(true);
  expect(wasCancelledByUser(controller)).toBe(false);
});

test("새 요청에 밀려난 요청도 사용자 취소가 아니다", () => {
  const first = beginChatRequest();

  beginChatRequest();

  expect(first.signal.aborted).toBe(true);
  expect(wasCancelledByUser(first)).toBe(false);
});

test("떼어낸 뒤에는 중단 버튼이 그 요청을 건드리지 않는다", () => {
  const controller = beginChatRequest();
  detachChatRequest();

  cancelChatRequest();

  expect(controller.signal.aborted).toBe(false);
  expect(wasCancelledByUser(controller)).toBe(false);
});

test("떼어내지 않은 요청은 화면에 그대로 흘린다", () => {
  const controller = beginChatRequest();

  expect(isDetachedRequest(controller.signal)).toBe(false);
});

/*
 * 역할: useAutoScrollToBottom의 "리사이즈 때 바닥까지 따라간다" / "사용자가 위로
 * 스크롤하면 안 따라간다" 두 분기를 검증한다.
 *
 * jsdom에는 ResizeObserver가 없어(test/setup.ts가 빈 스텁으로 막아둔다) 실제
 * 리사이즈를 재현할 수 없다 — 이 테스트에서만 콜백을 손으로 실행할 수 있는
 * 가짜 ResizeObserver로 바꿔치기해서 검증한다.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { useAutoScrollToBottom } from "./useAutoScrollToBottom";

let triggerResize: () => void;
let originalResizeObserver: typeof ResizeObserver;

beforeEach(() => {
  originalResizeObserver = window.ResizeObserver;
  class FakeResizeObserver {
    constructor(callback: ResizeObserverCallback) {
      triggerResize = () => callback([], this as unknown as ResizeObserver);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
});

afterEach(() => {
  window.ResizeObserver = originalResizeObserver;
});

function setUpShellAndContainer() {
  const shell = document.createElement("div");
  // findScrollableAncestor는 클래스명이 아니라 실제 overflow-y 계산값으로 찾는다.
  shell.style.overflowY = "auto";
  Object.defineProperty(shell, "scrollHeight", { value: 2000, configurable: true });
  Object.defineProperty(shell, "clientHeight", { value: 500, configurable: true });
  shell.scrollTop = 0;

  const container = document.createElement("div");
  shell.appendChild(container);
  document.body.appendChild(shell);

  return { shell, container };
}

it("스트리밍 중 내용이 자라면(리사이즈) 스크롤 조상을 바닥까지 스크롤한다", () => {
  const { shell, container } = setUpShellAndContainer();

  renderHook(() => useAutoScrollToBottom({ current: container }, true));
  triggerResize();

  expect(shell.scrollTop).toBe(2000);

  document.body.removeChild(shell);
});

it("active가 false면 리사이즈가 나도 스크롤하지 않는다", () => {
  const { shell, container } = setUpShellAndContainer();

  renderHook(() => useAutoScrollToBottom({ current: container }, false));

  expect(shell.scrollTop).toBe(0);

  document.body.removeChild(shell);
});

it("사용자가 위로 스크롤해 두면(바닥에서 멀어지면) 리사이즈가 나도 따라가지 않는다", () => {
  const { shell, container } = setUpShellAndContainer();

  renderHook(() => useAutoScrollToBottom({ current: container }, true));

  // 바닥(2000)에서 훨씬 위로 스크롤했다 — "가까움" 문턱(80px)을 넘어선다.
  shell.scrollTop = 200;
  shell.dispatchEvent(new Event("scroll"));

  triggerResize();

  expect(shell.scrollTop).toBe(200);

  document.body.removeChild(shell);
});

it("새 턴이 시작되면(active가 다시 true) 이전에 스크롤을 올렸어도 다시 바닥을 따라간다", () => {
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { rerender } = renderHook(({ active }) => useAutoScrollToBottom(containerRef, active), {
    initialProps: { active: true },
  });

  shell.scrollTop = 200;
  shell.dispatchEvent(new Event("scroll"));
  rerender({ active: false });

  rerender({ active: true });
  triggerResize();

  expect(shell.scrollTop).toBe(2000);

  document.body.removeChild(shell);
});

/*
 * 역할: useScrollEdgeButton의 "움직인 방향 판정"과 "스크롤할 내용이 있는지 판정",
 * scrollToTop/scrollToBottom 동작을 검증한다.
 *
 * jsdom에는 ResizeObserver가 없어(test/setup.ts가 빈 스텁으로 막아둔다) 실제
 * 리사이즈를 재현할 수 없다 — 이 테스트에서만 콜백을 손으로 실행할 수 있는
 * 가짜 ResizeObserver로 바꿔치기해서 검증한다.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { resetProgrammaticScroll, smoothScrollTo } from "./useAutoScrollToBottom";
import { useScrollEdgeButton } from "./useScrollEdgeButton";

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
  /* smoothScrollTo가 남기는 "프로그램이 옮겼다" 표식은 모듈 수준이라 테스트
     사이로 샌다 — 700ms 안에 도는 다음 테스트의 스크롤이 무시된다. */
  resetProgrammaticScroll();
});

afterEach(() => {
  window.ResizeObserver = originalResizeObserver;
  vi.useRealTimers();
});

/** 스크롤 한 번. jsdom은 scrollTop을 바꿔도 이벤트를 안 쏘므로 직접 쏜다. */
function scrollTo(shell: HTMLElement, top: number) {
  act(() => {
    shell.scrollTop = top;
    shell.dispatchEvent(new Event("scroll"));
  });
}

function setUpShellAndContainer() {
  const shell = document.createElement("div");
  shell.style.overflowY = "auto";
  Object.defineProperty(shell, "scrollHeight", { value: 2000, configurable: true });
  Object.defineProperty(shell, "clientHeight", { value: 500, configurable: true });
  shell.scrollTop = 0;

  const container = document.createElement("div");
  shell.appendChild(container);
  document.body.appendChild(shell);

  return { shell, container };
}

it("스크롤할 내용이 있으면 isScrollable이 true가 되고, 가만히 있으면 방향은 없다", () => {
  const { shell, container } = setUpShellAndContainer();
  // ref 객체를 매 렌더마다 새로 만들면(예: 렌더 콜백 안에서 리터럴로 넘기면) effect의
  // 의존성이 매번 "바뀐" 것으로 보여 update()의 setState → 재렌더 → effect 재실행이
  // 무한 반복된다. 실제 화면에서는 useRef가 안정적인 참조를 주지만, 테스트에서는
  // 직접 안정적인 참조를 만들어 넘겨야 한다.
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  expect(result.current.isScrollable).toBe(true);
  /* 버튼은 움직일 때만 뜬다 — 열자마자는 안 보인다(2026-09-08). */
  expect(result.current.isVisible).toBe(false);

  document.body.removeChild(shell);
});

it("내리면 down, 올리면 up으로 방향이 바뀐다", () => {
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  scrollTo(shell, 1000);
  expect(result.current.isVisible).toBe(true);
  expect(result.current.direction).toBe("down");

  scrollTo(shell, 400);
  expect(result.current.isVisible).toBe(true);
  expect(result.current.direction).toBe("up");

  document.body.removeChild(shell);
});

it("몇 px 요동으로는 방향이 뒤집히지 않는다", () => {
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  scrollTo(shell, 1000);
  expect(result.current.direction).toBe("down");

  /* 관성 스크롤의 마지막 몇 px, 레이아웃이 자라며 생기는 1px 요동. */
  scrollTo(shell, 998);
  expect(result.current.direction).toBe("down");

  document.body.removeChild(shell);
});

it("손을 멈추면 안 보이게 되지만 방향은 그대로 남는다", () => {
  vi.useFakeTimers();
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  scrollTo(shell, 1000);
  expect(result.current.isVisible).toBe(true);

  act(() => void vi.advanceTimersByTime(1300));
  expect(result.current.isVisible).toBe(false);
  /* 사라지는 애니메이션이 도는 동안 아이콘이 바뀌지 않게 방향은 유지한다. */
  expect(result.current.direction).toBe("down");

  document.body.removeChild(shell);
});

it("프로그램이 옮긴 스크롤은 방향으로 세지 않는다", () => {
  /* 스트리밍 중 자동 바닥 붙임이 스크롤을 계속 옮긴다 — 그것까지 세면 답변마다
     버튼이 떠 있다(useAutoScrollToBottom의 isProgrammaticScroll). */
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  /* smoothScrollTo가 표식을 남긴 뒤의 스크롤 이벤트다. */
  act(() => smoothScrollTo(shell, 1500));
  act(() => void shell.dispatchEvent(new Event("scroll")));

  expect(result.current.isVisible).toBe(false);

  document.body.removeChild(shell);
});

it("부드러운 스크롤이 목표에 닿기 전까지는 사용자 스크롤로 세지 않는다", () => {
  /* 시간창(700ms)으로 끝을 판정하던 때의 버그다 — 실측으로 3,879px 거리의
     부드러운 스크롤이 1,439ms 동안 이벤트를 쏘았고, 창이 닫힌 뒤의 45개가
     사용자 스크롤로 읽혔다. 지금은 목표 도달로 끝을 본다. */
  const { shell, container } = setUpShellAndContainer();
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  /* 목표는 1500인데 아직 중간(700)까지만 갔다 — 프로그램 스크롤이다. */
  act(() => smoothScrollTo(shell, 1500));
  shell.scrollTop = 700;
  act(() => void shell.dispatchEvent(new Event("scroll")));
  expect(result.current.isVisible).toBe(false);

  /* 목표에 닿았다. 그 이벤트까지가 프로그램 것이다. */
  shell.scrollTop = 1500;
  act(() => void shell.dispatchEvent(new Event("scroll")));
  expect(result.current.isVisible).toBe(false);

  /* 그 뒤의 움직임은 사용자 것이다. */
  shell.scrollTop = 1200;
  act(() => void shell.dispatchEvent(new Event("scroll")));
  expect(result.current.isVisible).toBe(true);
  expect(result.current.direction).toBe("up");

  document.body.removeChild(shell);
});

it("scrollToBottom은 스크롤 조상을 바닥까지, scrollToTop은 꼭대기까지 옮긴다", () => {
  const { shell, container } = setUpShellAndContainer();
  shell.scrollTop = 500;
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));

  act(() => result.current.scrollToBottom());
  expect(shell.scrollTop).toBe(2000);

  act(() => result.current.scrollToTop());
  expect(shell.scrollTop).toBe(0);

  document.body.removeChild(shell);
});

it("내용이 한 화면을 안 넘으면 isScrollable이 false로 남는다", () => {
  const shell = document.createElement("div");
  shell.style.overflowY = "auto";
  Object.defineProperty(shell, "scrollHeight", { value: 400, configurable: true });
  Object.defineProperty(shell, "clientHeight", { value: 500, configurable: true });
  const container = document.createElement("div");
  shell.appendChild(container);
  document.body.appendChild(shell);
  const containerRef = { current: container };

  const { result } = renderHook(() => useScrollEdgeButton(containerRef));
  act(() => triggerResize());

  expect(result.current.isScrollable).toBe(false);

  document.body.removeChild(shell);
});

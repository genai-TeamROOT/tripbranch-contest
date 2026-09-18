/*
 * 역할: 왼쪽 가장자리 가로 스와이프만 막고, 탭·세로 스크롤·가운데 스와이프는
 *   그대로 두는지 검증한다.
 *
 * jsdom 에는 TouchEvent 가 없다 — 여기서만 필요한 필드(touches, cancelable)를
 * 가진 가짜 이벤트를 만들어 document 에 쏜다. **브라우저가 실제로 뒤로 가기를
 * 그만두는지는 이걸로 알 수 없다.** 검증하는 것은 "어떤 제스처에 preventDefault
 * 를 부르는가"까지고, 실제 차단 여부는 실기기에서 본다.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useEdgeSwipeBackGuard } from "./useEdgeSwipeBackGuard";

/** 가장자리 판정 폭(28px)보다 안쪽 / 바깥쪽 시작점. */
const AT_EDGE = 10;
const AWAY_FROM_EDGE = 200;

function touch(type: string, x: number, y: number): Event & { defaultPrevented: boolean } {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "touches", {
    value: [{ clientX: x, clientY: y }],
  });
  return event as Event & { defaultPrevented: boolean };
}

/** touchstart → touchmove 를 흘리고, 이동이 막혔는지 돌려준다. */
function swipe(from: [number, number], to: [number, number]): boolean {
  document.dispatchEvent(touch("touchstart", from[0], from[1]));
  const move = touch("touchmove", to[0], to[1]);
  document.dispatchEvent(move);
  document.dispatchEvent(touch("touchend", to[0], to[1]));
  return move.defaultPrevented;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("useEdgeSwipeBackGuard", () => {
  it("왼쪽 가장자리에서 오른쪽으로 쓸면 막는다 — 브라우저 뒤로 가기 자리다", () => {
    renderHook(() => useEdgeSwipeBackGuard());

    expect(swipe([AT_EDGE, 300], [AT_EDGE + 80, 305])).toBe(true);
  });

  it("가장자리에서 곧게 세로로 움직이면 두고 본다 — 스크롤을 죽이면 안 된다", () => {
    renderHook(() => useEdgeSwipeBackGuard());

    /* 가로 흔들림 2px 까지는 세로 스크롤로 본다(MIN_HORIZONTAL_PX). */
    expect(swipe([AT_EDGE, 300], [AT_EDGE + 2, 200])).toBe(false);
  });

  it("비스듬히 빠르게 쓸어도 막는다 — 세로가 더 커도 뒤로 가기는 뒤로 가기다", () => {
    /* 전에는 "가로 > 세로"를 요구해서 이 경우가 새어 나갔다(2026-09-09 실기기). */
    renderHook(() => useEdgeSwipeBackGuard());

    expect(swipe([AT_EDGE, 300], [AT_EDGE + 30, 380])).toBe(true);
  });

  it("한 번 막기로 한 제스처는 도중에 세로로 꺾여도 계속 막는다", () => {
    renderHook(() => useEdgeSwipeBackGuard());

    document.dispatchEvent(touch("touchstart", AT_EDGE, 300));
    document.dispatchEvent(touch("touchmove", AT_EDGE + 40, 300));
    const turned = touch("touchmove", AT_EDGE + 40, 500);
    document.dispatchEvent(turned);

    expect(turned.defaultPrevented).toBe(true);
  });

  it("가장자리에서 왼쪽으로 쓰는 것은 뒤로 가기가 아니라 그대로 둔다", () => {
    renderHook(() => useEdgeSwipeBackGuard());

    expect(swipe([AT_EDGE, 300], [AT_EDGE - 8, 300])).toBe(false);
  });

  it("화면 가운데에서 시작한 가로 스와이프는 앱 몫이라 건드리지 않는다", () => {
    /* 상세 모달의 사진 넘기기가 여기 해당한다. */
    renderHook(() => useEdgeSwipeBackGuard());

    expect(swipe([AWAY_FROM_EDGE, 300], [AWAY_FROM_EDGE + 80, 300])).toBe(false);
  });

  it("손가락이 둘이면(확대·축소) 건드리지 않는다", () => {
    renderHook(() => useEdgeSwipeBackGuard());

    const start = new Event("touchstart", { bubbles: true, cancelable: true });
    Object.defineProperty(start, "touches", {
      value: [
        { clientX: AT_EDGE, clientY: 300 },
        { clientX: 120, clientY: 320 },
      ],
    });
    document.dispatchEvent(start);
    const move = touch("touchmove", AT_EDGE + 80, 300);
    document.dispatchEvent(move);

    expect(move.defaultPrevented).toBe(false);
  });

  it("언마운트하면 더 이상 막지 않는다", () => {
    const { unmount } = renderHook(() => useEdgeSwipeBackGuard());
    expect(swipe([AT_EDGE, 300], [AT_EDGE + 80, 300])).toBe(true);

    unmount();

    expect(swipe([AT_EDGE, 300], [AT_EDGE + 80, 300])).toBe(false);
  });
});

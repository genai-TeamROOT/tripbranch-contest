/*
 * 역할: useKeyboardInset이 <html>에 쓰는 값(--tb-kb, --tb-vv-top)과 "언제
 *   개입하고 언제 손을 떼는가", 그리고 CSS 쪽이 그 값을 읽는지를 검증한다.
 *
 * 여기 쓰는 수치는 **실기기(iPhone 사파리) 표본에서 가져왔다**(2026-09-09,
 * 표본 586+116개). 키보드가 뜨면 보이는 창이 647 → 346 으로 줄고 offsetTop 이
 * 0 → 301 이 되며, 둘의 합은 언제나 레이아웃 높이 647 이었다.
 *
 * jsdom에는 window.visualViewport도 소프트 키보드도 없다 — 여기서만 손으로
 * 이벤트를 쏠 수 있는 가짜 VisualViewport를 채워 넣는다. **그래서 이 테스트가
 * 통과해도 실기기에서 맞는다는 뜻은 아니다.** 검증하는 것은 "주어진 수치를
 * 규칙대로 옮겨 쓰는가"까지다.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KEYBOARD_RESTORE_MS, useKeyboardInset } from "./useKeyboardInset";

/** 키보드가 없을 때 보이는 창 높이(실측). */
const REST_HEIGHT = 647;
/** 키보드가 떴을 때의 창 높이와 밀린 양(실측). 둘의 합이 647이다. */
const KEYBOARD_HEIGHT = 346;
const KEYBOARD_OFFSET_TOP = 301;
/** 그래서 가려진 높이는 이만큼이다. */
const COVERED = REST_HEIGHT - KEYBOARD_HEIGHT;

let emit: () => void;
let fakeViewport: { height: number; offsetTop: number };
const originalVisualViewport = window.visualViewport;

function property(name: string): string {
  return document.documentElement.style.getPropertyValue(name);
}

/** 실제 입력칸에 포커스를 준다 — 훅이 activeElement의 종류로 키보드를 가려낸다. */
function focusInput(): HTMLInputElement {
  const input = document.createElement("input");
  document.body.append(input);
  input.focus();
  return input;
}

/** 키보드가 다 올라온 상태로 만든다. */
function raiseKeyboard() {
  fakeViewport.height = KEYBOARD_HEIGHT;
  fakeViewport.offsetTop = KEYBOARD_OFFSET_TOP;
  act(() => emit());
}

/** 키보드가 다 내려간 상태로 만든다. */
function lowerKeyboard() {
  fakeViewport.height = REST_HEIGHT;
  fakeViewport.offsetTop = 0;
  act(() => emit());
}

beforeEach(() => {
  fakeViewport = { height: REST_HEIGHT, offsetTop: 0 };
  const listeners: Array<() => void> = [];
  window.visualViewport = {
    get height() {
      return fakeViewport.height;
    },
    get offsetTop() {
      return fakeViewport.offsetTop;
    },
    addEventListener: (_event: string, listener: () => void) => {
      listeners.push(listener);
    },
    removeEventListener: (_event: string, listener: () => void) => {
      const index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    },
  } as unknown as VisualViewport;
  emit = () => listeners.forEach((listener) => listener());
});

afterEach(() => {
  window.visualViewport = originalVisualViewport;
  document.body.innerHTML = "";
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-tb-kb");
});

describe("useKeyboardInset", () => {
  it("평소에는 아무 값도 쓰지 않는다 — 화면이 지금과 똑같이 동작한다", () => {
    renderHook(() => useKeyboardInset());

    expect(property("--tb-kb")).toBe("");
    expect(property("--tb-vv-top")).toBe("");
  });

  it("키보드가 뜨면 가린 높이와 밀린 양을 쓰고 open 표식을 남긴다", () => {
    renderHook(() => useKeyboardInset());
    focusInput();
    raiseKeyboard();

    expect(property("--tb-kb")).toBe(`${COVERED}px`);
    expect(property("--tb-vv-top")).toBe(`${KEYBOARD_OFFSET_TOP}px`);
    expect(document.documentElement.getAttribute("data-tb-kb")).toBe("open");
  });

  it("평소에는 표식을 안 남긴다 — 이게 없어야 CSS가 이동 변환을 아예 안 건다", () => {
    /* 값이 0이어도 translate 속성이 붙어 있으면 그 요소는 이동 변환이 있는 것으로
       취급돼, 안쪽 backdrop-filter 가 뒤를 못 읽고 흰 박스로 칠해진다(실기기
       확인). sticky 도 같은 이유로 갱신을 멈춘다. */
    renderHook(() => useKeyboardInset());
    focusInput();
    act(() => emit());

    expect(document.documentElement.getAttribute("data-tb-kb")).toBeNull();
  });

  it("innerHeight를 안 본다 — iOS에서 그 값은 시각 뷰포트라 늘 0이 나온다", () => {
    /* 실기기 표본 586개에서 innerHeight가 visualViewport.height와 한 번도
       벌어지지 않았다. innerHeight로 계산하면 가린 높이가 항상 0 이하가 된다. */
    Object.defineProperty(window, "innerHeight", {
      value: KEYBOARD_HEIGHT,
      configurable: true,
    });
    renderHook(() => useKeyboardInset());
    focusInput();
    raiseKeyboard();

    expect(property("--tb-kb")).toBe(`${COVERED}px`);
  });

  it("포커스가 입력칸이 아니면 창이 줄어도 개입하지 않는다", () => {
    renderHook(() => useKeyboardInset());

    /* 주소창이 접히고 펴지는 것도 보이는 높이를 바꾼다 — 그것까지 키보드로
       세면 스크롤할 때마다 컴포저가 들썩인다. */
    raiseKeyboard();

    expect(property("--tb-kb")).toBe("");
  });

  it("포커스만 가고 창이 그대로면 개입하지 않는다 — 데스크톱·하드웨어 키보드", () => {
    renderHook(() => useKeyboardInset());
    focusInput();
    act(() => emit());

    expect(property("--tb-kb")).toBe("");
    expect(property("--tb-vv-top")).toBe("");
  });

  it("40px 이하로 줄어든 것은 키보드로 세지 않는다", () => {
    renderHook(() => useKeyboardInset());
    focusInput();

    fakeViewport.height = REST_HEIGHT - 30;
    act(() => emit());

    expect(property("--tb-kb")).toBe("");
  });

  it("입력칸에서 입력칸으로 옮겨도 기준 높이를 다시 잡지 않는다", () => {
    renderHook(() => useKeyboardInset());
    focusInput();
    raiseKeyboard();

    /* 여기서 기준을 지금 높이로 갱신해 버리면 가린 높이가 0이 되어 컴포저가
       키보드 뒤로 내려간다. */
    focusInput();
    act(() => emit());

    expect(property("--tb-kb")).toBe(`${COVERED}px`);
  });

  it("고무줄 스크롤의 음수 offsetTop은 0으로 막는다", () => {
    renderHook(() => useKeyboardInset());
    focusInput();
    raiseKeyboard();

    /* 실측에서 -26 이 나왔다. 그대로 쓰면 셸이 위로 올라가 아래에 빈 띠가 생긴다. */
    fakeViewport.offsetTop = -26;
    act(() => emit());

    expect(property("--tb-vv-top")).toBe("0px");
  });

  it("포커스가 풀려도 키보드가 내려가는 중에는 계속 따라간다", () => {
    renderHook(() => useKeyboardInset());
    const input = focusInput();
    raiseKeyboard();

    /* 여기서 손을 떼면 화면이 튄다 — 브라우저는 이 시점에 아직 화면을 밀어 둔
       상태다. */
    act(() => input.blur());
    fakeViewport.height = 500;
    fakeViewport.offsetTop = 147;
    act(() => emit());

    expect(property("--tb-kb")).toBe(`${REST_HEIGHT - 500}px`);
    expect(property("--tb-vv-top")).toBe("147px");
  });

  it("키보드가 다 내려가면 값을 걷어내고, 따라 내려가는 전환을 그때만 건다", () => {
    renderHook(() => useKeyboardInset());
    const input = focusInput();
    raiseKeyboard();
    expect(document.documentElement.getAttribute("data-tb-kb")).toBe("open");

    act(() => input.blur());
    lowerKeyboard();

    expect(property("--tb-kb")).toBe("");
    expect(property("--tb-vv-top")).toBe("");
    expect(document.documentElement.getAttribute("data-tb-kb")).toBe("closing");
  });

  it("내려가던 중에 다시 포커스가 오면 전환을 걷어낸다 — 올라가는 건 즉시여야 한다", () => {
    renderHook(() => useKeyboardInset());
    const input = focusInput();
    raiseKeyboard();
    act(() => input.blur());
    lowerKeyboard();
    expect(document.documentElement.getAttribute("data-tb-kb")).toBe("closing");

    focusInput();
    raiseKeyboard();

    expect(document.documentElement.getAttribute("data-tb-kb")).toBe("open");
  });

  it("전환이 끝나면 표식을 뗀다 — 남으면 주소창이 접힐 때마다 컴포저가 미끄러진다", () => {
    vi.useFakeTimers();
    try {
      renderHook(() => useKeyboardInset());
      const input = focusInput();
      raiseKeyboard();
      act(() => input.blur());
      lowerKeyboard();

      act(() => vi.advanceTimersByTime(KEYBOARD_RESTORE_MS + 100));

      expect(document.documentElement.getAttribute("data-tb-kb")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("visualViewport가 없는 브라우저에서는 아무 값도 쓰지 않는다", () => {
    window.visualViewport = undefined as unknown as VisualViewport;

    renderHook(() => useKeyboardInset());

    expect(property("--tb-kb")).toBe("");
    expect(property("--tb-vv-top")).toBe("");
  });

  it("언마운트하면 쓴 값과 표식을 걷어낸다", () => {
    const { unmount } = renderHook(() => useKeyboardInset());
    focusInput();
    raiseKeyboard();
    expect(property("--tb-kb")).toBe(`${COVERED}px`);

    unmount();

    expect(property("--tb-kb")).toBe("");
    expect(document.documentElement.getAttribute("data-tb-kb")).toBeNull();
  });
});

/*
 * 훅이 값을 잘 써도 CSS가 그 값을 안 읽으면 화면에서는 아무 일도 안 일어난다.
 * jsdom 은 index.css 를 로드조차 하지 않으므로 픽셀 대신 **규칙이 남아 있는지**를
 * 본다 — 되돌릴 때 반드시 건드리게 되는 지점들이다(SideDrawer.test.tsx 와 같은 방식).
 */
describe("값을 읽는 쪽(index.css)", () => {
  const cssSource = readFileSync(resolve(process.cwd(), "src/index.css"), "utf-8");
  /* 줄머리에 고정해서 찾는다 — 안 그러면 `.tb-shell` 이
     `:root[data-tb-kb] .tb-shell` 안에도 걸려 둘을 구분할 수 없다. */
  const rule = (selector: string) =>
    new RegExp(`\\n {2}${selector}\\s*\\{[\\s\\S]*?\\n {2}\\}`).exec(cssSource)?.[0] ?? "";

  it("이동 변환은 키보드가 떠 있을 때만 걸린다", () => {
    /* 값이 0이어도 translate 속성이 붙어 있으면 그 요소는 이동 변환이 있는 것으로
       취급돼, 안쪽 backdrop-filter 가 뒤를 못 읽고 컴포저가 흰 박스로 칠해진다
       (실기기 확인, 2026-09-09). sticky 도 같은 이유로 갱신을 멈춘다. */
    expect(rule("\\.tb-shell")).not.toContain("translate:");
    expect(rule("\\.tb-keyboard-lift")).toBe("");

    expect(rule(":root\\[data-tb-kb\\] \\.tb-shell > \\.tb-page-enter")).toContain(
      "translate: 0 var(--tb-vv-top, 0px)",
    );
    expect(rule(":root\\[data-tb-kb\\] \\.tb-keyboard-lift")).toContain(
      "translate: 0 calc(-1 * var(--tb-kb, 0px))",
    );
  });

  it("보정은 셸 직계 자식 한 겹에만 건다 — 중첩된 전환 래퍼에 겹치면 두 배가 된다", () => {
    /* .tb-page-enter 는 화면 안에서 또 쓰인다(일정 화면의 목록↔상세). 자손
       선택자로 잡으면 중첩된 만큼 보정이 겹친다(2026-09-09에 실제로 그랬다). */
    expect(cssSource).not.toMatch(/:root\[data-tb-kb\]\s+\.tb-page-enter\s*\{/);
  });

  it("셸 자체는 움직이지 않는다 — 움직이면 그 자리로 드로어가 드러난다", () => {
    /* 셸을 통째로 내렸더니 위쪽에 빈 자리가 생겨 뒤의 사이드바가 비쳤다
       (실기기 확인, 2026-09-09). 셸은 제자리에서 화면을 덮고, 그 안의 화면만
       내려가야 한다. */
    expect(cssSource).not.toMatch(/:root\[data-tb-kb\]\s+\.tb-shell\s*\{/);
  });

  it("셸 높이는 건드리지 않는다", () => {
    /* 높이를 줄이면 본문이 재배치되고 스크롤 위치가 흔들린다 — 요구사항은
       "메인 화면은 그대로, 입력창만 이동"이다. */
    const shell = rule("\\.tb-shell");
    expect(shell).toContain("max-width: var(--tb-shell-max)");
    expect(shell).toContain("height: 100dvh");
    expect(shell).not.toContain("--tb-kb");
  });

  it("밀린 양을 transform 이 아니라 translate 로 되돌린다", () => {
    /* 이 요소에는 드로어를 밀 때 쓰는 transition: transform 이 걸려 있다.
       transform 으로 되돌리면 키보드 보정이 0.28초에 걸쳐 미끄러진다. */
    const shell = rule("\\.tb-shell");
    expect(shell).toContain("transition: transform");
    expect(shell).not.toMatch(/transform:\s*translateY/);
  });

  it("Safe Area 여백은 키보드가 가린 만큼 접힌다", () => {
    /* 키보드가 떠 있으면 홈 인디케이터 자리를 키보드가 덮고 있어 그 여백은
       의미가 없다 — 빼면 음수가 되어 max() 가 1rem 을 고른다. */
    expect(rule("\\.tb-composer-dock")).toContain(
      "max(1rem, calc(env(safe-area-inset-bottom) - var(--tb-kb, 0px)))",
    );
  });

  it("터치 기기 입력칸은 16px 아래로 안 내려간다 — 포커스할 때 화면이 확대된다", () => {
    /* iOS 사파리는 16px 미만인 입력칸에 포커스가 가면 화면을 확대하고 되돌려
       주지 않는다(2026-09-09 실기기 — 일정 검색·즐겨찾기 이름 바꾸기). 개별
       입력칸마다 클래스를 지키는 대신 여기서 한 번에 막는다. */
    /* **레이어 밖에 있어야 한다.** 캐스케이드는 특이도보다 레이어 순서를 먼저
       따져서, @layer base 안에 두면 utilities 의 `.text-sm` 에 무조건 진다 —
       처음에 base 안에 넣었다가 그대로 확대됐다(2026-09-09). 레이어 밖 규칙은
       들여쓰기가 없다는 것으로 가려낸다. */
    const coarse = /\n@media \(pointer: coarse\) \{[\s\S]*?\n\}/.exec(cssSource)?.[0] ?? "";
    expect(coarse).toContain("font-size: 16px");
    expect(coarse).toContain("textarea");
    /* maximum-scale 로 확대 자체를 막으면 손으로 키우는 길까지 막힌다 —
       그쪽으로 되돌리지 않았는지 viewport meta 로 확인한다. */
    const html = readFileSync(resolve(process.cwd(), "index.html"), "utf-8");
    const viewport = /<meta name="viewport"[^>]*>/.exec(html)?.[0] ?? "";
    expect(viewport).not.toContain("maximum-scale");
    expect(viewport).not.toContain("user-scalable");
  });

  it("따라 내려가는 전환은 내려갈 때만 걸리고, 길이가 훅 상수와 같다", () => {
    const closing = rule(':root\\[data-tb-kb="closing"\\] \\.tb-keyboard-lift');
    expect(closing).toContain(`translate ${KEYBOARD_RESTORE_MS}ms`);
  });
});

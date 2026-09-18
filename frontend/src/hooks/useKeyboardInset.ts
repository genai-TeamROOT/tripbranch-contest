/*
 * 역할: 모바일 소프트 키보드가 화면을 얼마나 가리는지, 브라우저가 화면을 얼마나
 *   밀어 올렸는지를 CSS 커스텀 속성으로 흘려보낸다.
 * 입력: 없음 — `window.visualViewport`와 지금 포커스된 요소를 직접 본다.
 * 출력: 없음(반환값이 없다). 키보드가 떠 있는 동안만 `<html>`에 세 가지를 쓴다.
 *   - `--tb-kb` — 키보드가 아래에서 가린 높이. 컴포저를 이만큼 올린다.
 *   - `--tb-vv-top` — 브라우저가 화면을 밀어 올린 양. 셸을 이만큼 내려 되돌린다.
 *   - `data-tb-kb="closing"` — 키보드가 내려가는 동안만. 컴포저가 따라 내려가는
 *     전환을 그때만 건다(index.css).
 * 호출 시점: AppShell이 앱 전체에 딱 한 번 건다. 화면마다 걸지 않는다 — 홈과
 *   채팅이 같은 값을 봐야 하고, 두 번 걸면 같은 속성에 두 벌이 쓴다.
 *
 * ── 레이아웃을 건드리지 않는다 ────────────────────────────────────────
 * 화면 높이를 줄이지 않고 **컴포저만 옮긴다.** 높이를 줄이면 그 안의 flex 배치가
 * 다시 계산돼 본문이 재배치되고, 넘치는 화면에서는 스크롤 위치까지 흔들린다.
 * `translate`는 자리를 그대로 둔 채 그림만 옮기므로 레이아웃이 한 번도 다시
 * 잡히지 않는다.
 *
 * ── iOS 실측이 정한 것 (2026-09-09, 표본 586+116개) ───────────────────
 * 1. **`window.innerHeight`를 쓰면 안 된다.** iOS 사파리에서 이 값은 레이아웃
 *    뷰포트가 아니라 시각 뷰포트다 — `visualViewport.height`와 한 번도 벌어지지
 *    않았다. "가린 높이 = innerHeight − vv.height"는 늘 0이 나온다. 그래서
 *    **포커스가 오기 직전의 창 높이를 기준으로 잡아** 거기서 뺀다.
 * 2. **iOS는 보이는 창을 문서 아래쪽으로 내려붙인다** — 키보드가 뜨면
 *    `offsetTop`이 0 → 301이 되고 `offsetTop + vv.height`가 레이아웃 높이와
 *    같았다. 이래서 화면 전체가 위로 뜬 것처럼 보인다. 그 값만큼 셸을 도로
 *    내리면 본문이 제자리에 남는다.
 * 3. **`resize`는 방향당 딱 한 번 온다.** 키보드가 내려가는 0.25초 동안 위치를
 *    알려주는 신호가 전혀 없다. 그래서 내려갈 때만 짧은 전환을 걸어 흉내 낸다.
 *
 * ── Android Chrome ───────────────────────────────────────────────────
 * 기본값(`interactive-widget=resizes-visual`)에서는 레이아웃 뷰포트가 그대로고
 * 시각 뷰포트만 줄어든다. 아래 식은 그 경우 `offsetTop`이 0이고 가린 높이만
 * 잡히므로 iOS와 같은 코드로 풀린다. **viewport meta에 `resizes-content`를 넣지
 * 않는 이유가 이것이다** — 그걸 켜면 레이아웃 뷰포트가 줄어 화면이 통째로
 * 재배치된다(이 훅이 막으려는 바로 그 동작이다).
 */

import { useEffect } from "react";

/** 키보드가 가린 높이. 컴포저를 이만큼 올린다. */
const KEYBOARD_INSET_PROPERTY = "--tb-kb";
/** 브라우저가 밀어 올린 양. 셸을 이만큼 내려 되돌린다. */
const VIEWPORT_OFFSET_PROPERTY = "--tb-vv-top";
/*
 * 지금 상태를 알리는 표식. `"open"`(키보드가 떠 있음)과 `"closing"`(내려가는 중)만
 * 있고, 평소에는 아예 없다.
 *
 * **없을 때 CSS가 아무 이동 변환도 걸지 않는 것이 중요하다**(index.css). 값이
 * 0이어도 `translate` 속성이 붙어 있으면 그 요소는 이동 변환이 있는 것으로
 * 취급돼서, 안쪽 `backdrop-filter` 가 뒤를 못 읽고 컴포저의 유리 배경이 흰
 * 박스로 칠해진다(실기기 확인, 2026-09-09). `position: sticky` 도 같은 이유로
 * 갱신을 멈춘다.
 */
const STATE_ATTRIBUTE = "data-tb-kb";

/**
 * 컴포저가 키보드를 따라 내려가는 데 걸리는 시간.
 *
 * **재서 고른 값이 아니라 판단이다.** 브라우저가 내려가는 키보드의 위치를 전혀
 * 알려주지 않아서(위 3번) 맞출 근거 자체가 없다. iOS 키보드 애니메이션이 0.25초
 * 안팎인데, 끝까지 끌면 "느리게 따라온다"고 느껴져 그보다 짧게 잡았다.
 * **index.css의 `[data-tb-kb="closing"]` 규칙과 같아야 한다**(테스트가 지킨다).
 */
export const KEYBOARD_RESTORE_MS = 180;
/** 전환이 끝나기 전에 표식을 떼면 도중에 끊긴다. 그만큼 여유를 둔다. */
const RESTORE_MARGIN_MS = 60;
/*
 * 이보다 적게 줄어든 것은 키보드로 보지 않는다. 모바일 브라우저는 스크롤할 때
 * 주소창을 접었다 폈다 하면서 같은 값을 흔든다.
 */
const MIN_KEYBOARD_INSET_PX = 40;

function isEditable(element: Element | null): boolean {
  if (!(element instanceof HTMLElement)) return false;
  if (element.isContentEditable) return true;
  return element.tagName === "INPUT" || element.tagName === "TEXTAREA";
}

export function useKeyboardInset(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    /* 지원하지 않는 브라우저에서는 아무것도 쓰지 않는다 — CSS 쪽 var() 기본값
       0px이 그대로 남아 지금까지와 똑같이 동작한다(데스크톱 포함). */
    if (!viewport) return;

    /*
     * 개입 중인지. 키보드가 실제로 올라온 뒤에만 켜고, 완전히 내려간 뒤에 끈다.
     * **포커스가 풀리는 즉시 끄지 않는 것이 중요하다** — 키보드가 내려가는
     * 동안 브라우저는 아직 화면을 밀어 둔 상태라, 그때 손을 떼면 화면이 튄다.
     */
    let engaged = false;
    /** 키보드가 없을 때의 창 높이. 얼마나 가렸는지 재는 기준이다(위 1번). */
    let restHeight = viewport.height;
    let restoreTimer: ReturnType<typeof setTimeout> | undefined;

    const clearTimer = () => {
      if (restoreTimer !== undefined) clearTimeout(restoreTimer);
      restoreTimer = undefined;
    };

    const update = () => {
      const focused = isEditable(document.activeElement);
      const height = viewport.height;
      /* 고무줄 스크롤에서 음수가 나온다(실측 -26). 그대로 쓰면 셸이 위로 올라가
         아래쪽에 빈 띠가 생긴다. */
      const offsetTop = Math.max(0, Math.round(viewport.offsetTop));
      const inset = Math.max(0, Math.round(restHeight - height));
      const covered = inset > MIN_KEYBOARD_INSET_PX;

      if (!engaged) {
        if (!focused) {
          /* 포커스가 없을 때만 기준을 갱신한다. 입력칸에서 입력칸으로 옮길 때
             갱신하면 이미 줄어든 높이가 기준이 되어 컴포저가 도로 내려간다. */
          restHeight = height;
          return;
        }
        /* 포커스만으로는 아직 아무것도 안 한다 — 하드웨어 키보드나 데스크톱처럼
           창이 그대로인 경우에는 개입할 이유가 없다(요구사항 8). */
        if (!covered && offsetTop === 0) return;
        engaged = true;
        /* 내려가던 중에 다시 포커스가 오면 전환을 걷어낸다 — 올라가는 것은
           즉시여야 컴포저가 키보드보다 먼저 제자리에 선다. */
        clearTimer();
      }

      if (!focused && !covered && offsetTop === 0) {
        engaged = false;
        restHeight = height;
        root.style.removeProperty(KEYBOARD_INSET_PROPERTY);
        root.style.removeProperty(VIEWPORT_OFFSET_PROPERTY);
        /* 값이 0으로 돌아가는 그 순간에만 전환을 걸어, 컴포저가 키보드를 따라
           내려가는 것처럼 보이게 한다. 계속 붙여 두면 주소창이 접히고 펴질
           때마다 컴포저가 미끄러진다. */
        root.setAttribute(STATE_ATTRIBUTE, "closing");
        restoreTimer = setTimeout(() => {
          restoreTimer = undefined;
          root.removeAttribute(STATE_ATTRIBUTE);
        }, KEYBOARD_RESTORE_MS + RESTORE_MARGIN_MS);
        return;
      }

      root.setAttribute(STATE_ATTRIBUTE, "open");
      root.style.setProperty(KEYBOARD_INSET_PROPERTY, `${inset}px`);
      root.style.setProperty(VIEWPORT_OFFSET_PROPERTY, `${offsetTop}px`);
    };

    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", update);
      clearTimer();
      root.removeAttribute(STATE_ATTRIBUTE);
      root.style.removeProperty(KEYBOARD_INSET_PROPERTY);
      root.style.removeProperty(VIEWPORT_OFFSET_PROPERTY);
    };
  }, []);
}

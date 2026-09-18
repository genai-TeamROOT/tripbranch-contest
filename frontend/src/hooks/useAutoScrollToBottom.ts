/*
 * 역할: 스트리밍 중 새로 들어오는 답변을 따라 대화창을 부드럽게 바닥으로 붙인다.
 * 입력: 내용이 자라는 컨테이너의 ref, 지금 스트리밍 중인지(active).
 * 출력: 없음(부수효과로 스크롤만 옮긴다).
 * 호출 시점: ChatPage가 메시지 목록을 감싼 div에 붙인다.
 *
 * 실제로 스크롤되는 요소는 이 컨테이너의 부모가 아니라, overflow-y가 걸린
 * 가장 가까운 조상이다(페이지마다 그 조상이 다르다 — 자기 <main>일 수도,
 * 더 위의 .tb-shell일 수도 있다. 특정 클래스명에 기대지 않고 computed style로
 * 직접 찾는다). ChatMessageList가 답변을 한 글자씩 보여주는 타자기 효과(내부
 * setInterval)는 TripContext 상태 변화 없이 레이아웃만 자라므로, React 렌더
 * 이벤트로는 못 잡는다 — ResizeObserver로 실제 높이 변화를 직접 관찰한다.
 */

import { useEffect, useRef } from "react";

const NEAR_BOTTOM_THRESHOLD_PX = 80;

export function findScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
  let node = element?.parentElement ?? null;
  while (node) {
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return node;
    node = node.parentElement;
  }
  return null;
}

/*
 * **프로그램이 옮긴 스크롤을 사용자 스크롤과 구분하는 표식.**
 *
 * 스크롤 이벤트만 보면 둘을 가릴 수 없다. 그런데 가려야 하는 곳이 있다 —
 * useScrollEdgeButton은 "사용자가 화면을 움직일 때만" 버튼을 띄우는데, 이
 * 파일의 자동 바닥 붙임이 스트리밍 내내 스크롤을 옮기므로 그것까지 세면
 * 답변마다 버튼이 떠 있게 된다.
 *
 * **시간창이 아니라 목표 도달로 끝을 판정한다.** 처음에는 700ms 창으로 했는데
 * 실측하니 3,879px 거리의 `behavior: "smooth"`가 **1,439ms 동안** 이벤트를
 * 86개 쏘았다 — 창이 닫힌 뒤의 45개가 사용자 스크롤로 읽혔다. 거리가 길수록
 * 더 걸리므로 시간으로는 맞출 수 없다. `scrollend`로 끝을 받을 수 있으면
 * 좋지만 Safari에 없다.
 *
 * 마감(deadline)은 안전망이다 — 목표에 못 닿고 끝나는 경우(사용자가 중간에
 * 손으로 잡아채거나 콘텐츠가 줄어 목표가 사라지는 경우)에 표식이 영원히 켜져
 * 있지 않게 한다.
 */
const PROGRAMMATIC_DEADLINE_MS = 3000;
/** 목표에 이만큼 들어오면 도달로 본다. 부드러운 스크롤은 정확히 안 멈춘다. */
const REACHED_EPSILON_PX = 2;

let programmatic: { scroller: HTMLElement; top: number; deadline: number } | null = null;

export function isProgrammaticScroll(scroller: HTMLElement): boolean {
  if (!programmatic || programmatic.scroller !== scroller) return false;
  if (Date.now() > programmatic.deadline) {
    programmatic = null;
    return false;
  }
  /* 목표는 요청값이 아니라 실제로 갈 수 있는 위치다 — scrollHeight를 그대로
     주는 호출부가 있는데(자동 바닥 붙임) 그 값은 최대 scrollTop보다 크다. */
  const reachable = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  const target = Math.min(programmatic.top, reachable);
  if (Math.abs(scroller.scrollTop - target) <= REACHED_EPSILON_PX) {
    /* 이 이벤트까지가 프로그램 것이다. 다음 것은 사용자 것으로 본다. */
    programmatic = null;
  }
  return true;
}

/**
 * 표식을 비운다. **테스트가 서로 새지 않게 하는 용도다** — 모듈 수준 값이라
 * 한 테스트에서 smoothScrollTo를 부르면 다음 테스트의 스크롤이 "프로그램이 옮긴
 * 것"으로 읽힌다(favoritesSync의 resetFavoritesSync와 같은 취지).
 */
export function resetProgrammaticScroll(): void {
  programmatic = null;
}

export function smoothScrollTo(scroller: HTMLElement, top: number) {
  programmatic = { scroller, top, deadline: Date.now() + PROGRAMMATIC_DEADLINE_MS };
  if (typeof scroller.scrollTo === "function") {
    scroller.scrollTo({ top, behavior: "smooth" });
    return;
  }
  scroller.scrollTop = top;
}

export function useAutoScrollToBottom(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
) {
  // 사용자가 위로 스크롤해 이전 메시지를 읽고 있으면, 스트리밍 중이라도
  // 억지로 바닥까지 끌어내리지 않는다.
  const shouldFollowRef = useRef(true);

  useEffect(() => {
    const scroller = findScrollableAncestor(containerRef.current);
    if (!scroller) return;

    const handleScroll = () => {
      const distanceFromBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      shouldFollowRef.current = distanceFromBottom <= NEAR_BOTTOM_THRESHOLD_PX;
    };
    scroller.addEventListener("scroll", handleScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", handleScroll);
  }, [containerRef]);

  useEffect(() => {
    const container = containerRef.current;
    const scroller = findScrollableAncestor(container);
    if (!container || !scroller || !active) return;

    // 새 턴이 시작되는 시점에는 항상 바닥부터 다시 따라간다.
    shouldFollowRef.current = true;

    const observer = new ResizeObserver(() => {
      if (!shouldFollowRef.current) return;
      smoothScrollTo(scroller, scroller.scrollHeight);
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [containerRef, active]);
}

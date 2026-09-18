/*
 * 역할: 채팅 화면의 스크롤 이동 버튼에 필요한 상태와 동작을 만든다.
 * 입력: 내용이 자라는 컨테이너의 ref(useAutoScrollToBottom과 같은 컨테이너를 준다).
 * 출력: 버튼을 보일지(isVisible), 어느 쪽으로 가는 버튼인지(direction),
 *   스크롤할 내용이 있는지(isScrollable), 맨 위/맨 아래로 옮기는 함수.
 * 호출 시점: ChatPage가 컴포저 위 버튼에 붙인다.
 *
 * **위치가 아니라 움직임으로 정한다**(2026-09-08). 전에는 늘 떠 있으면서 "맨 위
 * 근처면 아래로, 아니면 위로"였다 — 버튼이 상시로 본문을 가리고, 방향이 지금
 * 어디 있는지로 정해져서 "내가 올리는 중인데 아래로 버튼이 보이는" 경우가 있었다.
 * 지금은 사용자가 올리면 위로 가는 버튼, 내리면 아래로 가는 버튼이 뜨고, 손을
 * 멈추면 사라진다.
 *
 * **보임과 방향을 따로 둔다.** 사라지는 애니메이션이 도는 동안에도 아이콘이
 * 그대로여야 한다 — 방향까지 null로 만들면 페이드 아웃 중에 아이콘이 바뀐다.
 *
 * **프로그램이 옮긴 스크롤은 세지 않는다.** 스트리밍 중 자동 바닥 붙임이
 * 스크롤을 계속 옮기는데 그것까지 "움직임"으로 보면 답변마다 버튼이 떠 있다
 * (useAutoScrollToBottom의 isProgrammaticScroll).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  findScrollableAncestor,
  isProgrammaticScroll,
  smoothScrollTo,
} from "./useAutoScrollToBottom";

/** 이만큼은 자라야 "스크롤할 내용이 있다"고 본다. */
const SCROLLABLE_THRESHOLD_PX = 80;
/*
 * 손을 멈춘 뒤 버튼이 사라지기까지. **재서 고른 값이 아니라 판단이다** — 짧으면
 * 스크롤을 끊어 하는 동안 깜빡이고 길면 다 읽고 나서도 남을 것이라 보고 그 사이로
 * 잡았다. 실제로 손으로 써 보고 조정할 값이다.
 */
const IDLE_HIDE_MS = 1200;
/*
 * 이보다 작게 움직인 것은 방향으로 세지 않는다. 관성 스크롤의 마지막 몇 px과
 * 레이아웃이 자라며 생기는 1px 요동으로 방향이 뒤집히는 것을 막는다.
 */
const DIRECTION_THRESHOLD_PX = 4;

export type ScrollDirection = "up" | "down";

export function useScrollEdgeButton(containerRef: React.RefObject<HTMLElement | null>) {
  const [isScrollable, setIsScrollable] = useState(false);
  /* 처음 방향이 "down"인 것은 아이콘 초기값일 뿐이다 — isVisible이 false라
     화면에는 안 보인다. */
  const [state, setState] = useState<{ isVisible: boolean; direction: ScrollDirection }>({
    isVisible: false,
    direction: "down",
  });
  const lastScrollTopRef = useRef(0);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleHide = useCallback(() => {
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => {
      setState((previous) => ({ ...previous, isVisible: false }));
    }, IDLE_HIDE_MS);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    const scroller = findScrollableAncestor(container);
    if (!container || !scroller) return;

    const updateScrollable = () => {
      setIsScrollable(scroller.scrollHeight > scroller.clientHeight + SCROLLABLE_THRESHOLD_PX);
    };

    const handleScroll = () => {
      updateScrollable();
      const previous = lastScrollTopRef.current;
      const current = scroller.scrollTop;
      lastScrollTopRef.current = current;

      /* 표식이 켜져 있으면 위치만 따라가고 방향·타이머는 건드리지 않는다.
         버튼이 자기 스크롤로 사라지지 않는 것도 이 덕분이다 — 누를 때 잡아 둔
         타이머가 살아 있어 이동이 끝날 때까지 보인다. */
      if (isProgrammaticScroll(scroller)) return;

      const delta = current - previous;
      if (Math.abs(delta) < DIRECTION_THRESHOLD_PX) return;
      setState({ isVisible: true, direction: delta > 0 ? "down" : "up" });
      scheduleHide();
    };

    lastScrollTopRef.current = scroller.scrollTop;
    updateScrollable();
    scroller.addEventListener("scroll", handleScroll, { passive: true });
    // 메시지가 새로 쌓이거나 스트리밍으로 길어지면 스크롤 가능 여부 자체가
    // 바뀐다 — scroll 이벤트만으로는 안 잡힌다.
    const observer = new ResizeObserver(updateScrollable);
    observer.observe(container);
    return () => {
      scroller.removeEventListener("scroll", handleScroll);
      observer.disconnect();
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    };
  }, [containerRef, scheduleHide]);

  const move = useCallback(
    (to: "top" | "bottom") => {
      /* 누른 순간에도 사라지는 시계를 다시 감는다 — 이동이 끝나기 전에 버튼이
         손 밑에서 없어지지 않게. */
      scheduleHide();
      const scroller = findScrollableAncestor(containerRef.current);
      if (!scroller) return;
      smoothScrollTo(scroller, to === "top" ? 0 : scroller.scrollHeight);
    },
    [containerRef, scheduleHide],
  );

  return {
    isVisible: state.isVisible,
    direction: state.direction,
    isScrollable,
    scrollToTop: () => move("top"),
    scrollToBottom: () => move("bottom"),
  };
}

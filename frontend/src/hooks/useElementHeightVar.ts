/*
 * 역할: 어떤 요소의 실제 높이를 가장 가까운 main/section 에 CSS 커스텀 속성으로
 *   알려준다.
 * 입력: 잴 요소의 ref, 쓸 속성 이름(`--tb-header-h` 처럼).
 * 출력: 없음. 호스트 요소의 인라인 스타일에 `<이름>: <높이>px` 을 쓴다.
 * 호출 시점: 스크롤 영역 **위에 겹치는** 것들이 쓴다 — 헤더(AppHeader)와
 *   입력창(ChatComposer).
 *
 * **왜 재야 하나.** 겹치는 것은 흐름에서 자리를 차지하지 않으므로, 스크롤 영역이
 * 그만큼 여백을 두지 않으면 맨 위/맨 아래 내용이 영영 가린다. 그 여백을 숫자로
 * 박아 두면 헤더가 접히거나(위치 칩 유무) 입력창이 여러 줄로 자랄 때 어긋난다.
 *
 * 붙이는 곳이 `<html>`이 아니라 가장 가까운 main/section 인 이유는, 화면 전환
 * 도중 두 화면이 잠깐 함께 떠 있을 때 서로의 값을 덮어쓰지 않게 하기 위해서다.
 */

import { useLayoutEffect } from "react";

export function useElementHeightVar(
  ref: React.RefObject<HTMLElement | null>,
  property: string,
): void {
  useLayoutEffect(() => {
    const element = ref.current;
    const host = element?.closest("main, section");
    if (!element || !(host instanceof HTMLElement)) return;

    const publish = () => {
      host.style.setProperty(property, `${Math.round(element.offsetHeight)}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(element);
    return () => {
      observer.disconnect();
      host.style.removeProperty(property);
    };
  }, [ref, property]);
}

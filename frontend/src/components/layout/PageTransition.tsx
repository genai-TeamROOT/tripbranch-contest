/*
 * 역할: 화면이 바뀔 때 새 화면이 서서히 나타나게 감싼다.
 * 입력: 애니메이션을 다시 재생할 기준이 되는 pathKey, 감쌀 화면.
 * 출력: 진입 애니메이션(.tb-page-enter)이 걸린 래퍼 한 겹.
 * 호출 시점: AppShell이 기반 화면을, App이 인증 화면들을 이걸로 감싼다.
 *
 * **들어올 때만 그린다.** 나가는 화면까지 애니메이션하려면 AnimatePresence로
 * 두 화면을 동시에 들고 있어야 하는데, 그러면 새 화면이 뜨기까지 나가는
 * 시간만큼 늦어진다(mode="wait") — 이동이 느려진 것처럼 느껴진다. 대신 새
 * 화면이 즉시 그려지면서 180ms 만에 또렷해진다.
 *
 * **떠오르거나 내려앉는 동작은 없다**(2026-09-20). 위아래로 움직이면 셸 안에서
 * 넘침이 생기거나 화면이 내려앉는 것이 눈에 걸렸고, 움직임만
 * `prefers-reduced-motion`에 걸려 있어 맥과 윈도우가 다르게 보였다. 근거는
 * index.css의 `.tb-page-enter` 주석에 있다.
 *
 * 그래서 framer-motion이 아니라 CSS 애니메이션이다 — 나갈 때 움직이지 않으므로
 * 라우팅마다 JS 애니메이션을 새로 태울 이유가 없다.
 *
 * pathKey가 바뀌면 래퍼 자체가 교체되면서 애니메이션이 다시 재생된다. 같은 키면
 * 재생되지 않는다 — 같은 화면에서 쿼리만 바뀔 때(일정의 ?saved=…) 화면이 다시
 * 깜빡이면 안 된다.
 *
 * 움직임이 없으므로 `prefers-reduced-motion`에 따라 갈리는 것도 없다 — 어떤
 * 환경이든 같은 전환을 본다.
 */

import type { ReactNode } from "react";

interface PageTransitionProps {
  /** 이 값이 바뀔 때만 애니메이션을 다시 재생한다. 보통 화면의 경로다. */
  pathKey: string;
  /**
   * 셸(.tb-shell) 안 화면이면 켠다. 셸은 height가 고정이고 안쪽 화면은 h-full로
   * 그 높이를 물려받으므로, 래퍼가 높이를 이어주지 않으면 화면이 접힌다
   * (DESIGN_SYSTEM.md §5 "높이 체인 주의"). 반대로 인증 화면은 셸 밖에서
   * min-h-dvh로 스스로 늘어나므로 켜지 않는다.
   */
  fullHeight?: boolean;
  children: ReactNode;
}

export function PageTransition({ pathKey, fullHeight = false, children }: PageTransitionProps) {
  return (
    <div key={pathKey} className={fullHeight ? "tb-page-enter h-full" : "tb-page-enter"}>
      {children}
    </div>
  );
}

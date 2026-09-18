/*
 * 역할: 화면 왼쪽 가장자리에서 시작하는 가로 스와이프를 막는다 — 모바일 브라우저의
 *   "뒤로 가기" 제스처가 앱 안에서 발동하지 않게.
 * 입력: 없음.
 * 출력: 없음. document 에 터치 리스너를 건다.
 * 호출 시점: AppShell 이 앱 전체에 한 번 건다.
 *
 * **왜 필요한가.** 왼쪽에서 오른쪽으로 쓸면 브라우저가 뒤로 간다. 그런데 이 앱은
 * 셸 뒤에 드로어를 늘 깔아 두는 푸시 구조라, 그 제스처가 도는 동안 뒤의 사이드바가
 * 드러나 화면이 무너져 보인다(실기기 확인, 2026-09-09). 스와이프로 드로어를 여는
 * 기능은 애초에 없다 — 여닫이는 햄거버 버튼 하나가 갖는다(AppHeader).
 *
 * **`touchstart` 가 아니라 `touchmove` 에서 막는다.** touchstart 에서 막으면 그
 * 자리의 탭과 세로 스크롤까지 함께 죽는다 — 햄버거 버튼이 왼쪽 16px 에서 시작해서
 * 바로 걸린다. 방향이 가로로 정해진 뒤에 막으면 세로 스크롤과 탭은 그대로 산다.
 *
 * **`passive: false` 여야 한다.** 브라우저는 문서 수준 터치 리스너를 기본으로
 * passive 로 잡고, 그러면 preventDefault 가 무시된다.
 *
 * 남는 것: 왼쪽 가장자리 28px 안에서 **시작하는** 가로 스와이프는 앱 안에서도
 * 안 먹는다(상세 모달의 사진 넘기기 등). 그 자리에서 시작하는 경우가 드물어
 * 받아들였다. 화면 오른쪽에서 시작하는 "앞으로 가기" 제스처는 손대지 않았다 —
 * 앞으로 갈 기록이 있을 때만 도는데다, 오른쪽 끝은 전송 버튼 자리다.
 */

import { useEffect } from "react";

/*
 * 이 안에서 시작한 스와이프만 본다. iOS 의 가장자리 판정 폭이 20~30px 대인데
 * 손가락이 닿는 자리가 그보다 안쪽일 수 있어 여유를 뒀다 — **재서 고른 값이
 * 아니라 판단이다.**
 */
const EDGE_PX = 32;
/*
 * 이만큼 오른쪽으로 가면 가로 스와이프로 본다.
 *
 * 처음에는 "가로가 세로보다 많이 갔을 때"로 뒀는데 **새어 나갔다** — 비스듬히
 * 빠르게 쓸면 첫 이동이 세로가 더 커서 그냥 지나갔고, 그 사이 브라우저가 제스처를
 * 가져가면 그 뒤로는 event.cancelable 이 false 라 손쓸 수 없다(2026-09-09 실기기).
 * 첫 이동에서 판정이 끝나야 해서, 세로와 견주지 않고 오른쪽으로 갔는지만 본다.
 *
 * 3px 인 이유는 세로 스크롤의 가로 흔들림과 가르기 위해서다. 낮을수록 뒤로 가기를
 * 확실히 막고, 높을수록 왼쪽 끝에서 시작하는 세로 스크롤이 잘 산다. 새어 나가는
 * 쪽이 더 나빠서 낮게 잡았다.
 */
const MIN_HORIZONTAL_PX = 3;

export function useEdgeSwipeBackGuard(): void {
  useEffect(() => {
    let startX: number | null = null;
    /* 한 번 막기로 한 제스처는 끝까지 막는다 — 도중에 세로로 꺾였다고 놓아주면
       브라우저가 그때 제스처를 가져간다. */
    let blocking = false;

    const handleStart = (event: TouchEvent) => {
      /* 손가락이 둘 이상이면 확대·축소다. 건드리지 않는다. */
      if (event.touches.length !== 1) {
        startX = null;
        return;
      }
      const touch = event.touches[0];
      startX = touch.clientX <= EDGE_PX ? touch.clientX : null;
      blocking = false;
    };

    const handleMove = (event: TouchEvent) => {
      if (startX === null || event.touches.length !== 1) return;
      const touch = event.touches[0];
      const movedRight = touch.clientX - startX;
      if (!blocking && movedRight >= MIN_HORIZONTAL_PX) blocking = true;
      if (blocking && event.cancelable) event.preventDefault();
    };

    const handleEnd = () => {
      startX = null;
      blocking = false;
    };

    document.addEventListener("touchstart", handleStart, { passive: true });
    document.addEventListener("touchmove", handleMove, { passive: false });
    document.addEventListener("touchend", handleEnd, { passive: true });
    document.addEventListener("touchcancel", handleEnd, { passive: true });
    return () => {
      document.removeEventListener("touchstart", handleStart);
      document.removeEventListener("touchmove", handleMove);
      document.removeEventListener("touchend", handleEnd);
      document.removeEventListener("touchcancel", handleEnd);
    };
  }, []);
}

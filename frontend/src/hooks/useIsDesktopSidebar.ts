/*
 * 역할: 데스크톱 사이드바가 상시 보이는 폭(768px 이상)인지 판정한다.
 * 근거: package_D/DESIGN_SYSTEM.md §4 — .tb-sidebar가 768px부터 나타난다.
 * 위치·일정을 태블릿·데스크톱에서 시트 대신 전체 페이지로 렌더링할 때(§8) 이
 * 값으로 분기한다.
 */

import { useEffect, useState } from "react";

const DESKTOP_SIDEBAR_QUERY = "(min-width: 768px)";

export function useIsDesktopSidebar(): boolean {
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia(DESKTOP_SIDEBAR_QUERY).matches,
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia(DESKTOP_SIDEBAR_QUERY);
    const handleChange = () => setIsDesktop(mediaQuery.matches);
    handleChange();
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  return isDesktop;
}

/*
 * 역할: 모든 화면을 감싸는 최상위 레이아웃 — 데스크톱 사이드바 + 모바일 푸시 드로어 +
 *   본문 셸(.tb-shell).
 * 입력: 없음(현재 URL을 useLocation으로 직접 읽는다).
 * 출력: 사이드바 접힘 상태(localStorage에 남긴다), 드로어 열림 상태, 현재 화면.
 * 호출 시점: App.tsx가 신원이 필요한 라우트(path="*")의 element로 이걸 직접 쓴다.
 * 근거: package_D/DESIGN_SYSTEM.md §4(레이아웃 셸).
 *
 * **바텀시트 스택은 걷어냈다**(2026-09-07). 위치·일정이 취향 설정과 같은 전체
 * 페이지가 되면서 시트로 여는 화면이 하나도 남지 않았고, 그 뒤로는 스택 계산과
 * BottomSheetLayer가 늘 빈 배열만 돌려 아무것도 그리지 않는 코드가 됐다.
 */

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useEdgeSwipeBackGuard } from "../../hooks/useEdgeSwipeBackGuard";
import { useKeyboardInset } from "../../hooks/useKeyboardInset";
import { AppRoutes } from "./AppRoutes";
import { AppShellProvider, useAppShell } from "./AppShellContext";
import { PageTransition } from "./PageTransition";
import { DesktopSidebar } from "./DesktopSidebar";
import { SideDrawer } from "./SideDrawer";

const COLLAPSED_KEY = "tb_sidebar_collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function AppShellInner() {
  const { drawerOpen, closeDrawer } = useAppShell();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const location = useLocation();

  /* 소프트 키보드가 가린 높이를 CSS 변수로 흘려보낸다. 화면마다 걸지 않고
     여기 한 번만 거는 이유는 훅 주석에 있다. 셸과 컴포저가 그 값을 읽는다. */
  useKeyboardInset();

  /* 왼쪽 가장자리 스와이프로 브라우저가 뒤로 가지 않게 막는다 — 그 제스처가 도는
     동안 셸 뒤의 드로어가 드러난다. 스와이프로 드로어를 여는 기능은 없다. */
  useEdgeSwipeBackGuard();

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      /* 저장 실패해도 화면 동작에는 영향 없다. */
    }
  }, [collapsed]);

  return (
    <div className="tb-app-root">
      <DesktopSidebar collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)} />
      <SideDrawer open={drawerOpen} onNavigate={closeDrawer} />
      <div
        className={`tb-shell ${drawerOpen ? "tb-shell--pushed" : ""}`}
        // 드로어가 열려 본문이 오른쪽으로 밀려난 상태에서, 밀려난 본문 아무 곳이나
        // 누르면 바깥을 누른 것으로 보고 드로어를 닫는다(탭-투-클로즈).
        //
        // **햄버거만 건너뛴다.** 그 버튼도 셸 안에 있어서, 캡처가 먼저 닫고 버튼이
        // 다시 여는 바람에 열린 채로 다시 눌러도 닫히지 않았다(2026-09-07 실측).
        // 여닫이는 버튼 하나가 온전히 갖는다.
        onClickCapture={
          drawerOpen
            ? (event) => {
                if ((event.target as HTMLElement).closest("[data-drawer-toggle]")) return;
                closeDrawer();
              }
            : undefined
        }
      >
        {/* 키를 경로로 잡는다 — 같은 화면 안에서 쿼리만 바뀔 때(일정의
            ?saved=…)까지 다시 마운트되면 스크롤과 화면 상태를 잃는다. */}
        <PageTransition pathKey={location.pathname} fullHeight>
          <AppRoutes />
        </PageTransition>
      </div>
    </div>
  );
}

export function AppShell() {
  return (
    <AppShellProvider>
      <AppShellInner />
    </AppShellProvider>
  );
}

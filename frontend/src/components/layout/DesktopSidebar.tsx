/*
 * 역할: 768px 이상에서 본문 왼쪽에 상시 붙는 사이드바. 펼침 272px / 접힘 72px 레일.
 * 입력: 접힘 여부(부모가 보관), 현재 라우트.
 * 출력: 펼침/접힘 토글, 접힘 상태에서의 라우트 이동.
 * 호출 시점: AppShell이 셸 왼쪽에 렌더한다. 모바일에서는 CSS(.tb-sidebar)로 숨는다.
 * 근거: DESIGN_SYSTEM.md 4장(셸) · 6.17(사이드바).
 */

import { useLocation, useNavigate } from "react-router-dom";
import { Home, MapPin, PanelLeftClose, PanelLeftOpen, Route, Sparkles } from "lucide-react";
import { useTripDispatch, useTripState } from "../../state/TripContext";
import { SideDrawerContent } from "./SideDrawerContent";
import { SidebarAccount } from "./SidebarAccount";

interface DesktopSidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

const RAIL_ICON_CLASS = "flex h-10 w-10 items-center justify-center rounded-full transition-colors";

export function DesktopSidebar({ collapsed, onToggle }: DesktopSidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const dispatch = useTripDispatch();
  const state = useTripState();
  /* 레일 라벨은 title·aria-label로만 남아 화면에 글자가 없다 — 그래서 영어 작업
     (PR #367)에서 통째로 빠져 있었다. 문구는 펼침 사이드바와 같게 맞춘다:
     같은 버튼이 접힘/펼침에 따라 다른 이름을 가지면 안 된다. */
  const isEn = state.language === "en";

  const hasConversation = state.messages.length > 0;

  /*
   * **"새 채팅"만 판정과 동작이 다르다** — 대화가 남아 있으면 비활성으로 그리고,
   * 누르면 세션을 지운다(6.17). 나머지는 단순 이동이다.
   */
  const railItems: Array<{
    key: string;
    label: string;
    icon: typeof Home;
    active: boolean;
    onClick: () => void;
  }> = [
    {
      key: "home",
      label: isEn ? "New chat" : "새 채팅",
      icon: Home,
      active: location.pathname === "/" && !hasConversation,
      onClick: () => {
        dispatch({ type: "RESET" });
        navigate("/");
      },
    },
    {
      key: "preferences",
      label: isEn ? "Preferences" : "취향 설정",
      icon: Sparkles,
      active: location.pathname === "/preferences",
      onClick: () => navigate("/preferences"),
    },
    {
      key: "location",
      label: isEn ? "Location" : "위치 설정",
      icon: MapPin,
      active: location.pathname === "/location",
      /* 취향 설정과 같은 전체 페이지다(2026-09-07) — 예전에는 모바일에서 시트로
         떴는데, 시트만 다른 취급을 받을 이유가 없어 셋 다 똑같이 navigate 한다. */
      onClick: () => navigate("/location"),
    },
    {
      key: "schedule",
      label: isEn ? "Schedule" : "일정",
      icon: Route,
      active: location.pathname === "/schedule",
      onClick: () => navigate("/schedule"),
    },
  ];

  return (
    <aside className={`tb-sidebar ${collapsed ? "tb-sidebar--collapsed" : ""}`}>
      {collapsed ? (
        /* flex-1 이라야 계정 자리의 mt-auto 가 밀어낼 높이를 갖는다 — 내용만큼만
           높으면 바닥이 아니라 마지막 아이콘 바로 밑에 붙는다. */
        <div className="flex flex-1 flex-col items-center gap-2 py-5">
          <button
            type="button"
            onClick={onToggle}
            aria-label={isEn ? "Expand sidebar" : "사이드바 펼치기"}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink"
          >
            <PanelLeftOpen size={18} />
          </button>
          <div className="my-1 h-px w-8 bg-border" />
          {railItems.map((item) => (
            <button
              key={item.key}
              type="button"
              title={item.label}
              aria-label={item.label}
              onClick={item.onClick}
              className={`${RAIL_ICON_CLASS} ${
                item.active ? "bg-brand text-white" : "text-brand hover:bg-chip"
              }`}
            >
              <item.icon size={18} />
            </button>
          ))}
          {/*
           * 접었을 때도 계정에 닿아야 한다(2026-09-06). 예전에는 레일이
           * SideDrawerContent 를 아예 안 그려서, 접어 두면 로그인도 로그아웃도
           * 할 수 없었다 — 펴야만 보이는 동작이 있으면 접기가 기능을 감추는 셈이다.
           *
           * 펼친 사이드바와 **같은 컴포넌트**다. 레일용으로 한 벌 더 만들면
           * 로그아웃이 두 곳에 생기고, 한쪽만 고쳐지면 접었을 때와 폈을 때가 갈린다.
           */}
          <SidebarAccount compact />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between px-5 py-5">
            <span className="text-base font-bold text-ink">TripBranch</span>
            <button
              type="button"
              onClick={onToggle}
              aria-label={isEn ? "Collapse sidebar" : "사이드바 접기"}
              aria-expanded={true}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-chip hover:text-ink"
            >
              <PanelLeftClose size={18} />
            </button>
          </div>
          <SideDrawerContent />
        </>
      )}
    </aside>
  );
}

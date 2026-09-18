/* eslint-disable react-refresh/only-export-components */
/*
 * 역할: 모바일 푸시 드로어의 열림 상태를 셸 트리 전체에 공유한다.
 * 입력: 없음(내부 useState).
 * 출력: drawerOpen, openDrawer, closeDrawer.
 * 호출 시점: AppShell이 최상위에서 감싸고, AppHeader(햄버거)·SideDrawer가 읽는다.
 */

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

interface AppShellContextValue {
  drawerOpen: boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
}

const AppShellContext = createContext<AppShellContextValue | null>(null);

export function AppShellProvider({
  children,
  value,
}: {
  children: ReactNode;
  /** 테스트에서 초기 상태나 spy를 주입할 때만 넘긴다. */
  value?: AppShellContextValue;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const defaultValue = useMemo<AppShellContextValue>(
    () => ({
      drawerOpen,
      openDrawer: () => setDrawerOpen(true),
      closeDrawer: () => setDrawerOpen(false),
    }),
    [drawerOpen],
  );

  return (
    <AppShellContext.Provider value={value ?? defaultValue}>{children}</AppShellContext.Provider>
  );
}

export function useAppShell(): AppShellContextValue {
  const context = useContext(AppShellContext);
  if (!context) {
    throw new Error("useAppShell은 AppShellProvider 안에서만 쓸 수 있다.");
  }
  return context;
}

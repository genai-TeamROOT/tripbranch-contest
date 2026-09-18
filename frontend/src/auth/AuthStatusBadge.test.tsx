/*
 * 역할: AuthStatusBadge의 계정 메뉴(게스트 라벨·세션 해제 확인)를 검증한다.
 * 입력: mock Supabase auth 상태, 배지 클릭.
 * 출력: 게스트/계정 라벨 표시, 메뉴 열기·확인·Esc 취소에 대한 assertion.
 * 호출 시점: vitest 실행 시 인증 배지 회귀 테스트로 호출된다.
 *
 * 이 컴포넌트는 더 이상 HomePage/ChatPage 헤더에 없다 — 사이드바(SideDrawerContent)가
 * 항상 떠 있어 헤더 쪽은 중복이었다. 지금은 사이드바가 없는 DeveloperChatPage에서만
 * 쓰이므로, 특정 페이지를 통하지 않고 컴포넌트 자체를 직접 렌더링해 검증한다.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { resetSupabaseClient } from "./supabaseClient";
import { AuthProvider } from "./AuthContext";
import { AuthStatusBadge } from "./AuthStatusBadge";
import { loadPreferences, savePreferences } from "../state/preferenceStorage";
import { loadLocationSettings, setLocationCenter } from "../state/locationSettings";
import { loadFavorites, saveFavorites } from "../state/sidebarStorage";
import { TripProvider } from "../state/TripContext";
import { GUEST_SESSION, setMockSession } from "../test/supabaseMock";

afterEach(() => {
  resetSupabaseClient();
});

function renderBadge() {
  render(
    <AuthProvider>
      <TripProvider>
        <AuthStatusBadge />
      </TripProvider>
    </AuthProvider>,
  );
}

test("게스트 세션이면 게스트 라벨을 보여준다", async () => {
  renderBadge();

  expect(await screen.findByRole("button", { name: "게스트로 이용 중" })).toBeInTheDocument();
});

/* 계정 연결 후에는 같은 uid로 is_anonymous만 false가 된다(D-062 2절).
   표시 분기가 그 전환을 따라가는지 고정한다. */
test("계정으로 승격되면 계정 라벨을 보여준다", async () => {
  setMockSession({
    ...GUEST_SESSION,
    user: { ...GUEST_SESSION.user, is_anonymous: false, email: "trip@example.com" },
  } as typeof GUEST_SESSION);

  renderBadge();

  expect(await screen.findByRole("button", { name: "trip@example.com" })).toBeInTheDocument();
});

test("배지를 누르면 계정 메뉴가 열리고 한 번 더 확인해야 해제된다", async () => {
  const user = userEvent.setup();
  renderBadge();

  await user.click(await screen.findByRole("button", { name: "게스트로 이용 중" }));
  expect(screen.getByRole("menu")).toBeInTheDocument();

  /* 한 번 누른 것만으로는 해제되지 않는다 — 확인 단계가 남아 있다. */
  await user.click(screen.getByRole("button", { name: "세션 해제" }));
  expect(screen.getByText(/돌아올 수 없어요/)).toBeInTheDocument();
});

test("해제를 확인하면 세션이 사라지고 배지도 함께 사라진다", async () => {
  const user = userEvent.setup();
  renderBadge();

  await user.click(await screen.findByRole("button", { name: "게스트로 이용 중" }));
  await user.click(screen.getByRole("button", { name: "세션 해제" }));
  await user.click(screen.getByRole("button", { name: "해제" }));

  expect(screen.queryByRole("button", { name: /이용 중/ })).not.toBeInTheDocument();
});

test("해제하면 이 기기에 남은 취향·즐겨찾기·검색 위치도 함께 지운다", async () => {
  /* 지우지 않으면 같은 브라우저에서 다음 사람이 앞사람의 값을 그대로 이어받는다.
     화면이 clearLocalUserData()를 실제로 부르는지 여기서 못 박는다. */
  const user = userEvent.setup();
  savePreferences([{ label: "조용한 곳", source: "preference", codes: ["quiet"] }]);
  saveFavorites([{ id: "fav-1", label: "회사 (역삼동)" }]);
  setLocationCenter("안국역");
  renderBadge();

  await user.click(await screen.findByRole("button", { name: "게스트로 이용 중" }));
  await user.click(screen.getByRole("button", { name: "세션 해제" }));
  await user.click(screen.getByRole("button", { name: "해제" }));

  await waitFor(() => expect(loadPreferences()).toEqual([]));
  expect(loadFavorites()).toEqual([]);
  expect(loadLocationSettings().center).toBeNull();
});

test("메뉴는 Esc로 닫히고 확인 단계도 함께 되돌아간다", async () => {
  const user = userEvent.setup();
  renderBadge();

  await user.click(await screen.findByRole("button", { name: "게스트로 이용 중" }));
  await user.click(screen.getByRole("button", { name: "세션 해제" }));
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("menu")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "게스트로 이용 중" }));
  expect(screen.getByRole("button", { name: "세션 해제" })).toBeInTheDocument();
  expect(screen.queryByText(/돌아올 수 없어요/)).not.toBeInTheDocument();
});

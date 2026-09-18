/*
 * 역할: 계정 팝업의 **회원 탈퇴**를 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * 탈퇴는 되돌릴 수 없는 유일한 동작이라 이 파일이 지키는 것은 셋이다.
 *   1. 메뉴를 누르는 것만으로는 지워지지 않는다(확인 단계를 거친다)
 *   2. 서버에서 지운 **뒤에** 로그아웃과 로컬 정리가 따라온다
 *   3. 실패하면 아무것도 정리하지 않고 화면에 남는다
 *
 * 2번이 특히 중요하다. 토큰 검증은 서명과 만료만 보고 계정이 살아 있는지는 묻지
 * 않아서, 로그아웃하지 않으면 남은 토큰으로 요청이 나가 방금 지운 user_id로 행이
 * 다시 생긴다.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AuthProvider } from "../../auth/AuthContext";
import { SidebarAccount } from "./SidebarAccount";
import { TripProvider } from "../../state/TripContext";
import { resetSupabaseMock, setMockSession, GUEST_SESSION } from "../../test/supabaseMock";
import { resetSupabaseClient } from "../../auth/supabaseClient";
import { deleteAccount } from "../../api/trip";

vi.mock("../../api/trip", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/trip")>()),
  deleteAccount: vi.fn(),
}));

const ACCOUNT_SESSION = {
  ...GUEST_SESSION,
  user: {
    ...GUEST_SESSION.user,
    is_anonymous: false,
    email: "trip@example.com",
    user_metadata: { name: "나종원" },
  },
} as typeof GUEST_SESSION;

beforeEach(() => {
  localStorage.clear();
  vi.mocked(deleteAccount).mockResolvedValue({ deleted_sessions: 0, deleted_schedules: 0 });
  setMockSession(ACCOUNT_SESSION);
});

afterEach(() => {
  resetSupabaseMock();
  resetSupabaseClient();
  vi.clearAllMocks();
});

function renderAccount() {
  return render(
    <AuthProvider>
      <TripProvider>
        <MemoryRouter>
          <SidebarAccount />
        </MemoryRouter>
      </TripProvider>
    </AuthProvider>,
  );
}

async function openMenu() {
  const trigger = await screen.findByRole("button", { expanded: false });
  await userEvent.click(trigger);
}

/* 이 파일에서 가장 중요한 테스트다. */
test("메뉴를 누르는 것만으로는 탈퇴하지 않는다", async () => {
  renderAccount();
  await openMenu();

  await userEvent.click(await screen.findByRole("menuitem", { name: "회원 탈퇴" }));

  /* 확인 문구가 뜰 뿐 요청은 아직 나가지 않는다. */
  expect(screen.getByText(/되돌릴 수 없어요/)).toBeInTheDocument();
  expect(deleteAccount).not.toHaveBeenCalled();
});

test("취소하면 요청을 보내지 않고 메뉴로 돌아간다", async () => {
  renderAccount();
  await openMenu();
  await userEvent.click(await screen.findByRole("menuitem", { name: "회원 탈퇴" }));

  await userEvent.click(screen.getByRole("button", { name: "취소" }));

  expect(deleteAccount).not.toHaveBeenCalled();
  expect(await screen.findByRole("menuitem", { name: "회원 탈퇴" })).toBeInTheDocument();
});

test("확인을 눌러야 탈퇴 요청이 나간다", async () => {
  renderAccount();
  await openMenu();
  await userEvent.click(await screen.findByRole("menuitem", { name: "회원 탈퇴" }));

  await userEvent.click(screen.getByRole("button", { name: "탈퇴하기" }));

  await waitFor(() => expect(deleteAccount).toHaveBeenCalledTimes(1));
});

/*
 * 계정을 지워도 이 브라우저의 토큰은 만료까지 통한다 — 그 사이 요청이 한 번이라도
 * 나가면 방금 지운 user_id로 행이 되살아난다.
 */
test("탈퇴한 뒤에는 세션이 남지 않는다", async () => {
  renderAccount();
  await openMenu();
  await userEvent.click(await screen.findByRole("menuitem", { name: "회원 탈퇴" }));

  await userEvent.click(screen.getByRole("button", { name: "탈퇴하기" }));

  /* 세션이 사라지면 이 컴포넌트는 아무것도 그리지 않는다(status/session 가드). */
  await waitFor(() =>
    expect(screen.queryByRole("menuitem", { name: "회원 탈퇴" })).not.toBeInTheDocument(),
  );
});

/*
 * 서버는 데이터를 먼저 지우고 계정을 마지막에 지운다. 여기서 실패했다면 계정은
 * 살아 있으므로, 화면도 로그아웃시키지 않고 남겨야 다시 눌러 마칠 수 있다.
 */
test("실패하면 로그아웃하지 않고 이유를 보여준다", async () => {
  vi.mocked(deleteAccount).mockRejectedValue(new Error("탈퇴 처리를 마치지 못했어요."));
  renderAccount();
  await openMenu();
  await userEvent.click(await screen.findByRole("menuitem", { name: "회원 탈퇴" }));

  await userEvent.click(screen.getByRole("button", { name: "탈퇴하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("탈퇴 처리를 마치지 못했어요");
  /* 확인 버튼이 그대로 있어야 다시 시도할 수 있다. */
  expect(screen.getByRole("button", { name: "탈퇴하기" })).toBeInTheDocument();
});

/* 게스트에게는 지울 계정이 없다 — 팝업 자체가 뜨지 않는다. */
test("게스트에게는 탈퇴가 보이지 않는다", async () => {
  setMockSession(GUEST_SESSION);
  renderAccount();

  expect(await screen.findByRole("button", { name: /로그인/ })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "회원 탈퇴" })).not.toBeInTheDocument();
});

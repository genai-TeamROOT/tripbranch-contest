/*
 * 역할: 재설정 화면이 **누구에게 폼을 여는지**를 가르는 규칙과, 새 비밀번호를 실제로
 *   저장하는 흐름을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **세션이 있다고 폼을 열면 안 된다.** 평범하게 로그인한 사람이 이 주소를 치기만
 * 해도 현재 비밀번호 없이 새 비밀번호를 정할 수 있게 되기 때문이다. 재설정 링크가
 * 세운 세션(PASSWORD_RECOVERY)인지까지 봐야 한다.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { NewPasswordPage } from "./NewPasswordPage";
import {
  emailAuthCalls,
  emitMockAuthEvent,
  resetSupabaseMock,
  setMockEmailAuthError,
  setMockSession,
} from "../test/supabaseMock";
import { isPasswordRecoveryActive, resetSupabaseClient } from "../auth/supabaseClient";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  resetSupabaseMock();
  resetSupabaseClient();
  window.location.hash = "";
});

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/reset-password/new"]}>
        <Routes>
          <Route path="/reset-password/new" element={<NewPasswordPage />} />
          <Route path="/" element={<p>홈 화면</p>} />
          <Route path="/reset-password" element={<p>재설정 요청 화면</p>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

/* 재설정 링크를 눌러 들어온 상태. Supabase가 링크의 토큰을 서버에 확인시킨 뒤에만
   쏘는 이벤트라, 주소창을 손으로 고쳐서는 만들 수 없는 상태다. */
function renderAfterResetLink() {
  const result = renderPage();
  act(() => emitMockAuthEvent("PASSWORD_RECOVERY"));
  return result;
}

async function fill(password: string, confirm = password) {
  await userEvent.type(await screen.findByLabelText("새 비밀번호"), password);
  await userEvent.type(screen.getByLabelText("새 비밀번호 확인"), confirm);
  await userEvent.click(screen.getByRole("button", { name: "비밀번호 바꾸기" }));
}

/* 이 파일에서 가장 중요한 테스트다. */
test("그냥 로그인만 되어 있으면 폼을 열지 않는다", async () => {
  renderPage(); // mock 기본값은 세션 있음 — 다만 재설정 링크로 온 세션이 아니다

  expect(await screen.findByText(/링크가 만료되었거나/)).toBeInTheDocument();
  expect(screen.queryByLabelText("새 비밀번호")).not.toBeInTheDocument();
});

test("링크 없이 들어오면 폼 대신 만료 안내를 보여준다", async () => {
  setMockSession(null);
  renderPage();

  expect(await screen.findByText(/링크가 만료되었거나/)).toBeInTheDocument();
  expect(screen.queryByLabelText("새 비밀번호")).not.toBeInTheDocument();
});

test("만료 안내에서 재설정 요청 화면으로 돌아갈 수 있다", async () => {
  setMockSession(null);
  renderPage();

  await userEvent.click(await screen.findByRole("button", { name: "재설정 링크 다시 받기" }));

  expect(await screen.findByText("재설정 요청 화면")).toBeInTheDocument();
});

test("재설정 링크로 들어오면 폼을 연다", async () => {
  renderAfterResetLink();

  expect(await screen.findByLabelText("새 비밀번호")).toBeInTheDocument();
});

test("새 비밀번호를 저장하고 홈으로 간다", async () => {
  renderAfterResetLink();
  await fill("Ab3!xyzw");

  await waitFor(() => expect(emailAuthCalls()).toHaveLength(1));
  const call = emailAuthCalls()[0];
  expect(call.method).toBe("updateUser");
  expect(call.password).toBe("Ab3!xyzw");

  expect(await screen.findByText("홈 화면")).toBeInTheDocument();
});

/* 링크 한 번에 변경 한 번이다. 신호가 남아 있으면 같은 탭에서 그 주소로 다시 들어가
   비밀번호를 계속 바꿀 수 있다. */
test("비밀번호를 바꾸고 나면 재설정 신호가 꺼진다", async () => {
  renderAfterResetLink();
  expect(isPasswordRecoveryActive()).toBe(true);

  await fill("Ab3!xyzw");

  await waitFor(() => expect(isPasswordRecoveryActive()).toBe(false));
});

test("두 번 입력한 비밀번호가 다르면 요청을 보내지 않는다", async () => {
  renderAfterResetLink();
  await fill("Ab3!xyzw", "Ab3!xyzz");

  expect(await screen.findByRole("alert")).toHaveTextContent("서로 달라요");
  expect(emailAuthCalls()).toHaveLength(0);
});

/* 정책 위반은 가입 화면과 같은 문구로 나와야 한다 — 두 화면이 다른 말을 하면 안 된다. */
test("서버가 거부한 비밀번호 사유를 그대로 보여준다", async () => {
  setMockEmailAuthError({ message: "weak", code: "weak_password" });
  renderAfterResetLink();
  await fill("Ab3!xyzw");

  expect(await screen.findByRole("alert")).toHaveTextContent("비밀번호");
  expect(screen.queryByText("홈 화면")).not.toBeInTheDocument();
});

test("회원가입 화면과 같은 비밀번호 조건을 안내한다", async () => {
  renderAfterResetLink();

  expect(
    await screen.findByText("8자 이상으로 입력해주세요. 이미 유출된 비밀번호는 쓸 수 없어요."),
  ).toBeInTheDocument();
});

/* 뭉뚱그린 "만료되었거나 올바르지 않아요"보다 서버가 알려준 이유가 낫다. */
test("서버가 만료라고 알려주면 그 이유를 보여준다", async () => {
  window.location.hash = "#error=access_denied&error_code=otp_expired";
  setMockSession(null);

  renderPage();

  expect(await screen.findByText(/링크가 만료됐어요/)).toBeInTheDocument();
  expect(screen.queryByLabelText("새 비밀번호")).not.toBeInTheDocument();
});

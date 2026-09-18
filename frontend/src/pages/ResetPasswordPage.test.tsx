/*
 * 역할: 비밀번호 찾기 화면이 실제로 재설정 메일을 요청하는지, 그리고 **가입 여부를
 *   흘리지 않는지** 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { ResetPasswordPage } from "./ResetPasswordPage";
import {
  emailAuthCalls,
  resetSupabaseMock,
  setMockEmailAuthError,
  setMockSession,
} from "../test/supabaseMock";
import { resetSupabaseClient } from "../auth/supabaseClient";

beforeEach(() => {
  localStorage.clear();
  setMockSession(null);
});

afterEach(() => {
  resetSupabaseMock();
  resetSupabaseClient();
});

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/reset-password"]}>
        <ResetPasswordPage />
      </MemoryRouter>
    </AuthProvider>,
  );
}

test("재설정 링크를 요청하면 새 비밀번호 화면으로 돌아오게 지정한다", async () => {
  renderPage();

  await userEvent.type(screen.getByLabelText("이메일"), "trip@example.com");
  await userEvent.click(screen.getByRole("button", { name: "재설정 링크 보내기" }));

  await waitFor(() => expect(emailAuthCalls()).toHaveLength(1));
  const call = emailAuthCalls()[0];
  expect(call.method).toBe("resetPasswordForEmail");
  expect(call.email).toBe("trip@example.com");
  /* 이 주소가 Supabase Redirect URLs에 없으면 링크가 엉뚱한 데로 간다. */
  expect(call.redirectTo).toContain("/reset-password/new");
});

test("이메일이 비어 있으면 요청을 보내지 않는다", async () => {
  renderPage();

  await userEvent.click(screen.getByRole("button", { name: "재설정 링크 보내기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("이메일을 입력해주세요");
  expect(emailAuthCalls()).toHaveLength(0);
});

/*
 * 이 파일에서 가장 중요한 테스트다. 가입된 주소와 아닌 주소의 화면이 달라지면,
 * 주소를 하나씩 넣어 가입 여부를 캐낼 수 있다(계정 열거). Supabase가 두 경우 모두
 * 성공으로 돌려주는 보호를 화면이 벗기지 않는지 고정한다.
 */
test("가입 여부를 드러내지 않는 문구로 안내한다", async () => {
  renderPage();

  await userEvent.type(screen.getByLabelText("이메일"), "nobody@example.com");
  await userEvent.click(screen.getByRole("button", { name: "재설정 링크 보내기" }));

  const notice = await screen.findByRole("status");
  expect(notice).toHaveTextContent("가입된 주소라면");
  for (const 흘리는_표현 of [/가입되지 않/, /없는 이메일/, /등록되지 않/, /존재하지 않/]) {
    expect(notice.textContent).not.toMatch(흘리는_표현);
  }
});

/* 발송 한도 같은 실제 실패는 드러내야 한다 — 가입 여부와 무관한 정보다. */
test("발송 한도에 걸리면 사유를 알려준다", async () => {
  setMockEmailAuthError({
    message: "email rate limit exceeded",
    code: "over_email_send_rate_limit",
  });
  renderPage();

  await userEvent.type(screen.getByLabelText("이메일"), "trip@example.com");
  await userEvent.click(screen.getByRole("button", { name: "재설정 링크 보내기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("잠시 후");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

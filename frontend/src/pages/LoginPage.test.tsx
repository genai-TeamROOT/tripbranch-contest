/*
 * 역할: 로그인 화면이 실제로 로그인 요청을 보내고, 실패 사유를 구분해서 보여주는지
 *   검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **실패를 뭉뚱그리지 않는 것이 핵심이다.** 비밀번호가 틀린 것과 이메일 확인이
 * 안 끝난 것은 사용자가 해야 할 일이 완전히 다르다 — 뭉치면 확인 메일을 안 누른
 * 사람이 비밀번호만 계속 다시 친다.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "./LoginPage";
import {
  emailAuthCalls,
  GUEST_SESSION,
  resetSupabaseMock,
  setMockEmailAuthError,
  setMockSession,
} from "../test/supabaseMock";
import { resetSupabaseClient } from "../auth/supabaseClient";

beforeEach(() => {
  localStorage.clear();
  /* 세션이 있으면 화면이 곧바로 리다이렉트한다 — 폼을 보려면 신원이 없어야 한다. */
  setMockSession(null);
});

afterEach(() => {
  resetSupabaseMock();
  resetSupabaseClient();
  window.location.hash = "";
});

function renderLogin() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>홈 화면</p>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

async function fillAndSubmit(email = "trip@example.com", password = "Ab3!xyzw") {
  if (email) await userEvent.type(screen.getByLabelText("이메일"), email);
  if (password) await userEvent.type(screen.getByLabelText("비밀번호"), password);
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));
}

test("이메일과 비밀번호로 로그인하면 홈으로 간다", async () => {
  renderLogin();
  await fillAndSubmit();

  await waitFor(() => expect(emailAuthCalls()).toHaveLength(1));
  const call = emailAuthCalls()[0];
  expect(call.method).toBe("signInWithPassword");
  expect(call.email).toBe("trip@example.com");

  expect(await screen.findByText("홈 화면")).toBeInTheDocument();
});

/* 안 채운 것과 틀린 것은 다른 상태다. 빈 값을 서버로 보내면 invalid_credentials가
   돌아와 "비밀번호가 틀렸나" 싶어진다. */
test("빈 값으로는 요청을 보내지 않는다", async () => {
  renderLogin();

  await userEvent.click(screen.getByRole("button", { name: "로그인" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("모두 입력해주세요");
  expect(emailAuthCalls()).toHaveLength(0);
});

test("비밀번호가 틀리면 그 사유를 보여준다", async () => {
  setMockEmailAuthError({ message: "Invalid login credentials", code: "invalid_credentials" });
  renderLogin();
  await fillAndSubmit();

  expect(await screen.findByRole("alert")).toHaveTextContent("이메일 또는 비밀번호가 맞지 않아요");
  /* 홈으로 넘어가지 않는다. */
  expect(screen.queryByText("홈 화면")).not.toBeInTheDocument();
});

/* 이 파일에서 가장 중요한 테스트다. */
test("이메일 확인이 안 끝난 계정은 메일을 누르라고 안내한다", async () => {
  setMockEmailAuthError({ message: "Email not confirmed", code: "email_not_confirmed" });
  renderLogin();
  await fillAndSubmit();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("이메일 확인");
  expect(alert).toHaveTextContent("링크");
  /* 비밀번호 문제로 읽히면 안 된다. */
  expect(alert).not.toHaveTextContent("비밀번호가 맞지 않");
});

test("게스트 시작은 그대로 동작한다", async () => {
  renderLogin();

  await userEvent.click(screen.getByRole("button", { name: "게스트로 시작하기" }));

  expect(await screen.findByText("홈 화면")).toBeInTheDocument();
  /* 게스트 경로는 이메일 인증을 거치지 않는다. */
  expect(emailAuthCalls()).toHaveLength(0);
});

test("이미 계정으로 로그인돼 있으면 폼을 보여주지 않는다", async () => {
  setMockSession({
    ...GUEST_SESSION,
    user: { ...GUEST_SESSION.user, is_anonymous: false, email: "trip@example.com" },
  } as typeof GUEST_SESSION);
  renderLogin();

  expect(await screen.findByText("홈 화면")).toBeInTheDocument();
  expect(screen.queryByLabelText("이메일")).not.toBeInTheDocument();
});

/*
 * **게스트는 세션이 있어도 통과시킨다**(2026-09-06). 진입이 게스트로 자동으로
 * 열리게 바뀌면서(RequireUser) 이 화면에 오는 사람은 전부 게스트 세션을 달고
 * 온다 — 예전처럼 "세션이 있으면 되돌려보내기"로 두면 사이드바의 로그인 버튼이
 * 아무 데도 못 간다.
 */
test("게스트로 쓰던 중이면 로그인 폼을 보여준다", async () => {
  resetSupabaseMock(); // 기본값이 게스트 세션 있음
  renderLogin();

  expect(await screen.findByLabelText("이메일")).toBeInTheDocument();
  expect(screen.queryByText("홈 화면")).not.toBeInTheDocument();
});

/*
 * 게스트에게 "게스트로 시작하기"를 다시 내밀면 안 된다 — signInAnonymously는
 * **새 익명 계정을 만들어** 지금까지 쌓은 대화가 달린 uid를 잃게 한다. 그 자리에는
 * 마음을 바꿨을 때 돌아갈 길만 둔다.
 */
test("게스트에게는 새 게스트 발급 대신 돌아갈 길을 준다", async () => {
  resetSupabaseMock();
  renderLogin();

  await screen.findByLabelText("이메일");
  expect(screen.queryByRole("button", { name: "게스트로 시작하기" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "로그인 없이 둘러보기" }));

  expect(await screen.findByText("홈 화면")).toBeInTheDocument();
  /* 새 신원을 만들지 않았다 — 만들었다면 인증 호출이 남는다. */
  expect(emailAuthCalls()).toHaveLength(0);
});

/*
 * 가입 확인 메일의 링크가 이 화면으로 돌아온다. 실패하면 Supabase가 오류를 조각에
 * 실어 보내는데, 그동안 아무도 안 읽어서 화면에 아무 말도 안 나왔다 — 사용자는
 * 링크를 눌렀는데 그냥 로그인 화면이 뜬 것으로만 보였다.
 */
test("만료된 확인 링크로 돌아오면 이유를 보여준다", async () => {
  window.location.hash =
    "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired";

  renderLogin();

  expect(await screen.findByRole("alert")).toHaveTextContent("만료");
});

/* 지나간 링크 오류가 지금 하려는 동작의 실패를 가리면 안 된다. */
test("로그인 실패 문구가 링크 오류보다 앞선다", async () => {
  window.location.hash = "#error=access_denied&error_code=otp_expired";
  setMockEmailAuthError({ message: "bad", code: "invalid_credentials" });
  renderLogin();

  await userEvent.type(screen.getByLabelText("이메일"), "trip@example.com");
  await userEvent.type(screen.getByLabelText("비밀번호"), "Ab3!xyzw");
  await userEvent.click(screen.getByRole("button", { name: "로그인" }));

  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("이메일 또는 비밀번호가 맞지 않아요"),
  );
});

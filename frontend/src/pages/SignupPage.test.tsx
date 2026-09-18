/*
 * 역할: 회원가입 화면이 실제로 가입 요청을 보내는지, 그리고 **가입 직후 로그인된
 *   것처럼 굴지 않는지** 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * 이메일 확인이 켜져 있어 signUp은 세션을 주지 않는다. 화면이 그 사실을 지키는지가
 * 이 파일의 핵심이다 — 홈으로 보내 버리면 사용자는 메일을 확인하지 않고 떠난다.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { SignupPage } from "./SignupPage";
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
  /* 이 화면은 세션과 무관하다 — 관문에서 처음부터 가입하는 경로다. */
  setMockSession(null);
});

afterEach(() => {
  resetSupabaseMock();
  resetSupabaseClient();
});

function renderSignup() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/signup"]}>
        <SignupPage />
      </MemoryRouter>
    </AuthProvider>,
  );
}

async function fill(overrides?: Partial<Record<"이름" | "이메일" | "비밀번호" | "확인", string>>) {
  const values = {
    이름: "나종원",
    이메일: "trip@example.com",
    비밀번호: "Ab3!xyzw",
    확인: "Ab3!xyzw",
    ...overrides,
  };
  await userEvent.type(screen.getByLabelText("이름"), values.이름);
  await userEvent.type(screen.getByLabelText("이메일"), values.이메일);
  await userEvent.type(screen.getByLabelText("비밀번호"), values.비밀번호);
  await userEvent.type(screen.getByLabelText("비밀번호 확인"), values.확인);
  await userEvent.click(screen.getByRole("checkbox"));
}

test("약관에 동의하기 전에는 제출할 수 없다", async () => {
  renderSignup();

  expect(screen.getByRole("button", { name: "가입하고 시작하기" })).toBeDisabled();

  await userEvent.click(screen.getByRole("checkbox"));

  expect(screen.getByRole("button", { name: "가입하고 시작하기" })).toBeEnabled();
});

test("가입하면 이름·이메일·비밀번호가 그대로 나간다", async () => {
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  await waitFor(() => expect(emailAuthCalls()).toHaveLength(1));
  const call = emailAuthCalls()[0];
  expect(call.method).toBe("signUp");
  expect(call.email).toBe("trip@example.com");
  expect(call.password).toBe("Ab3!xyzw");
  /* 이름은 user_metadata.name으로 간다 — identityLabel이 그 값을 읽는다. */
  expect(call.name).toBe("나종원");
});

/* 이 파일에서 가장 중요한 테스트다. */
test("가입 직후 로그인된 것처럼 굴지 않고 메일 확인을 안내한다", async () => {
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByText(/확인 메일을 보냈어요/)).toBeInTheDocument();
  expect(screen.getByText("trip@example.com")).toBeInTheDocument();
  /* 폼이 남아 있으면 다시 눌러 확인 메일이 또 나간다(발송 한도). */
  expect(screen.queryByRole("button", { name: "가입하고 시작하기" })).not.toBeInTheDocument();
});

test("두 번 입력한 비밀번호가 다르면 요청을 보내지 않는다", async () => {
  renderSignup();
  await fill({ 확인: "Ab3!xyzz" });

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("서로 달라요");
  expect(emailAuthCalls()).toHaveLength(0);
});

test("이름이 비어 있으면 요청을 보내지 않는다", async () => {
  renderSignup();
  await fill({ 이름: " " });

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("이름을 입력해주세요");
  expect(emailAuthCalls()).toHaveLength(0);
});

/*
 * 길이·문자 종류·유출 여부는 화면이 아니라 Supabase 정책이 정본이다. 화면에서 또
 * 검사하면 대시보드 설정을 바꾸는 순간 두 곳이 갈린다 — 서버 판정을 그대로 보여준다.
 */
test("서버가 거부한 비밀번호 사유를 그대로 보여준다", async () => {
  setMockEmailAuthError({ message: "weak", code: "weak_password" });
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("비밀번호");
  /* 요청은 실제로 나갔다 — 화면이 미리 막은 게 아니다. */
  expect(emailAuthCalls()).toHaveLength(1);
});

test("약관 보기를 누르면 약관 모달이 열린다", async () => {
  renderSignup();

  await userEvent.click(screen.getByRole("button", { name: "보기" }));

  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveAccessibleName("이용약관 및 개인정보처리방침");
  /* 껍데기가 아니라 본문이 실려 있는지 본다. 조별 검증은 TermsModal.test.tsx 몫이다. */
  expect(dialog).toHaveTextContent("제1조 (서비스의 내용)");
});

/* 모달을 읽었다고 동의가 켜지면 안 된다 — 동의는 사용자가 직접 눌러야 한다. */
test("약관을 읽어도 동의 체크는 켜지지 않는다", async () => {
  renderSignup();

  await userEvent.click(screen.getByRole("button", { name: "보기" }));
  await userEvent.click(await screen.findByRole("button", { name: "확인했어요" }));

  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "가입하고 시작하기" })).toBeDisabled();
});

/*
 * 게스트 승계(D-062 8절). 아래 세 건이 이 카드의 계약이다 — **uid가 유지되는 것**이
 * 핵심이라, 요청이 나갔는지만 보는 것으로는 부족하고 어느 uid에서 나갔는지를 본다.
 */

test("게스트가 가입하면 새 계정을 만들지 않고 지금 uid에 이메일을 붙인다", async () => {
  setMockSession(GUEST_SESSION);
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  await waitFor(() => expect(emailAuthCalls()).toHaveLength(1));
  const call = emailAuthCalls()[0];
  /* signUp이면 uid가 갈려 게스트로 쌓은 대화 목록이 통째로 빈다. */
  expect(call.method).toBe("updateUser");
  expect(call.userId).toBe(GUEST_SESSION.user.id);
  expect(call.email).toBe("trip@example.com");
  expect(call.password).toBe("Ab3!xyzw");
  expect(call.name).toBe("나종원");
});

/* 승계도 이메일 확인을 거친다. 링크를 눌러야 이메일이 붙으므로 화면은 가입과 똑같이
   중간 상태에 머물러야 한다 — 여기서 홈으로 보내면 확인하지 않고 떠난다. */
test("게스트 승계도 바로 계정이 된 것처럼 굴지 않는다", async () => {
  setMockSession(GUEST_SESSION);
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByText(/확인 메일을 보냈어요/)).toBeInTheDocument();
});

/* 가입 경로는 계정 열거를 막으려고 이미 있는 이메일도 성공처럼 처리하지만, 승계는
   게스트 세션이 있어야 닿는 경로라 사유를 알려준다(authErrors.ts의 email_exists 주석).
   뭉개면 게스트 상태 그대로인데 화면만 성공으로 보여 사용자가 할 일을 알 수 없다. */
test("이미 가입된 이메일로 승계하면 무엇을 해야 하는지 알려준다", async () => {
  setMockSession(GUEST_SESSION);
  setMockEmailAuthError({ message: "email address already registered", code: "email_exists" });
  renderSignup();
  await fill();

  await userEvent.click(screen.getByRole("button", { name: "가입하고 시작하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("이미 가입된 이메일");
  expect(screen.queryByText(/확인 메일을 보냈어요/)).not.toBeInTheDocument();
});

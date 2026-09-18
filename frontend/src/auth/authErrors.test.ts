/*
 * 역할: Supabase auth 오류가 한국어 문구로 바뀌는지, 그리고 **계정 존재 여부를
 *   흘리지 않는지** 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { expect, test } from "vitest";
import { authErrorMessage, authLinkErrorMessage } from "./authErrors";

test("code로 문구를 고른다", () => {
  expect(authErrorMessage({ code: "invalid_credentials" })).toBe(
    "이메일 또는 비밀번호가 맞지 않아요.",
  );
  expect(authErrorMessage({ code: "email_not_confirmed" })).toContain("이메일 확인");
});

/* Supabase가 code를 안 채우는 경로가 남아 있다. message만 와도 문구가 나와야 한다. */
test("code가 없으면 message 원문으로 맞춘다", () => {
  expect(authErrorMessage({ message: "Invalid login credentials" })).toBe(
    "이메일 또는 비밀번호가 맞지 않아요.",
  );
  expect(authErrorMessage({ message: "Password should be at least 8 characters" })).toContain(
    "비밀번호가 너무 쉬워요",
  );
});

/*
 * 뭉뚱그리면 사용자가 무엇을 고쳐야 하는지 모른다. 서버가 주는 reasons로 갈리는지
 * 고정한다. `characters`는 2026-09-15에 대시보드에서 껐지만 번역은 남겨 둔다 —
 * 다시 켜면 그 사유가 또 오므로 여기서도 계속 검증한다(authErrors 주석 참고).
 */
test("비밀번호 거부 사유를 갈라서 알려준다", () => {
  const 문자종류 = authErrorMessage({
    code: "weak_password",
    weak_password: { reasons: ["characters"] },
  });
  expect(문자종류).toContain("대문자");
  expect(문자종류).toContain("기호");

  const 유출 = authErrorMessage({ code: "weak_password", reasons: ["pwned"] });
  expect(유출).toContain("유출");
});

/* 최소 길이는 대시보드 설정이라 바뀔 수 있다. 서버가 알려준 숫자를 그대로 쓴다. */
test("길이 사유는 서버가 알려준 숫자를 쓴다", () => {
  expect(
    authErrorMessage({
      code: "weak_password",
      message: "Password should be at least 8 characters.",
      weak_password: { reasons: ["length"] },
    }),
  ).toContain("8자 이상");

  expect(
    authErrorMessage({
      code: "weak_password",
      message: "Password should be at least 12 characters.",
      weak_password: { reasons: ["length"] },
    }),
  ).toContain("12자 이상");
});

test("사유가 여러 개면 모두 알려준다", () => {
  const message = authErrorMessage({
    code: "weak_password",
    message: "Password should be at least 8 characters.",
    weak_password: { reasons: ["length", "characters"] },
  });
  expect(message).toContain("8자 이상");
  expect(message).toContain("대문자");
});

/* 우리가 모르는 사유만 오면 조용히 빈 문구를 내지 말고 뭉뚱그린 안내로 돌아간다. */
test("모르는 사유만 오면 뭉뚱그린 문구로 돌아간다", () => {
  const message = authErrorMessage({
    code: "weak_password",
    weak_password: { reasons: ["무엇인지_모를_사유"] },
  });
  expect(message).toBe(
    "비밀번호가 너무 쉬워요. 8자 이상으로, 다른 곳에서 쓰지 않는 값으로 정해주세요.",
  );
});

/*
 * 실제로 부딪힌 오류다(2026-09-02 브라우저 검증). Supabase 기본 SMTP는 시간당
 * 발송 한도가 낮아서, 가입을 몇 번 시도하면 바로 429가 온다. 사용자에게는
 * "가입에 실패했다"가 아니라 "잠시 후 다시"로 읽혀야 한다.
 */
test("메일 발송 한도는 잠시 후 재시도로 안내한다", () => {
  const message = authErrorMessage({
    code: "over_email_send_rate_limit",
    message: "email rate limit exceeded",
    status: 429,
  });
  expect(message).toContain("잠시 후");
  /* 원문을 그대로 노출하지 않는다. */
  expect(message).not.toContain("rate limit");
});

test("429는 code가 없어도 요청 과다로 읽는다", () => {
  expect(authErrorMessage({ status: 429, message: "..." })).toContain("잠시 후");
});

test("모르는 오류는 원문을 노출하지 않는다", () => {
  const message = authErrorMessage({ message: "some internal detail leaked here" });
  expect(message).not.toContain("internal");
  expect(message).toBe("요청을 처리하지 못했어요. 잠시 후 다시 시도해주세요.");
});

test("오류가 없어도 빈 문자열을 내지 않는다", () => {
  expect(authErrorMessage(null)).toBeTruthy();
  expect(authErrorMessage(undefined)).toBeTruthy();
});

/*
 * 이 파일에서 가장 중요한 테스트다. "가입되지 않은 이메일이에요" 같은 문구를
 * 만들면 주소를 하나씩 넣어 가입 여부를 캐낼 수 있다(계정 열거). Supabase가
 * 로그인 실패를 하나로 뭉쳐 주는 보호를 우리가 풀지 않는지 고정한다.
 */
test("계정이 있는지 없는지 알려주는 문구를 만들지 않는다", () => {
  const 흘리는_표현 = [/가입되지 않/, /없는 이메일/, /등록되지 않/, /이미 가입/, /존재하지 않/];
  const 모든_입력 = [
    { code: "invalid_credentials" },
    { code: "email_not_confirmed" },
    { code: "weak_password" },
    { code: "validation_failed" },
    { message: "Invalid login credentials" },
    { message: "User already registered" },
    { message: "무엇인지 모를 오류" },
    { status: 429 },
    null,
  ];

  for (const input of 모든_입력) {
    const message = authErrorMessage(input);
    for (const pattern of 흘리는_표현) {
      expect(message).not.toMatch(pattern);
    }
  }
});

/*
 * 메일 링크 실패는 성격이 다르다 — 방금 누른 버튼의 실패가 아니라 이미 지나간
 * 링크의 실패다. 그래서 "다시 시도"가 아니라 "메일을 다시 받아라"가 돼야 한다.
 */
test("만료된 링크는 메일을 다시 받으라고 안내한다", () => {
  const message = authLinkErrorMessage({ kind: "access_denied", code: "otp_expired" });

  expect(message).toContain("만료");
  expect(message).toContain("메일을 다시 받아");
});

test("사유를 모르는 access_denied도 다음에 할 일은 알려준다", () => {
  const message = authLinkErrorMessage({ kind: "access_denied", code: "" });

  expect(message).toContain("메일을 다시 받아");
});

test("처음 보는 코드여도 영어 원문을 띄우지 않는다", () => {
  const message = authLinkErrorMessage({ kind: "", code: "some_new_code" });

  expect(message).toContain("링크를 확인하지 못했어요");
  expect(message).not.toMatch(/[a-z]{4,}/);
});

/* 우리가 건 15초 타임아웃(supabaseClient.fetchWithTimeout)이 이 모양으로 온다 —
   auth-js가 abort를 AuthRetryableFetchError(status 0)로 감싼다. */
test("서버에 못 닿은 것과 입력이 틀린 것을 다르게 말한다", () => {
  const message = authErrorMessage({ message: "Aborted", status: 0 });

  expect(message).toContain("서버에 닿지 못했어요");
  expect(message).not.toContain("비밀번호");
});

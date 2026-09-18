/*
 * 역할: 진입 시 신원 처리(자동 게스트 발급)와 실패 표시를 검증한다.
 * 입력: mock Supabase auth 상태.
 * 출력: 자동 게스트 발급·설정 오류·발급 실패 표시에 대한 assertion.
 * 호출 시점: vitest 실행 시 인증 회귀 테스트로 호출된다.
 * TODO: 정식 로그인이 들어오면 계정 연결(linkIdentity) 경로도 여기서 검증한다.
 */

import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../App";
import {
  GUEST_SESSION,
  hangMockGetSession,
  setMockSession,
  setMockSignInError,
} from "../test/supabaseMock";
import { resetSupabaseClient } from "./supabaseClient";

/* 데스크톱 사이드바(항상 렌더)와 모바일 드로어가 같은 SideDrawerContent를 각자
   렌더하므로, 신원 라벨 같은 공통 텍스트를 screen에서 그냥 찾으면 두 곳 모두
   걸려 "여러 개 발견" 에러가 난다 — 데스크톱 사이드바(<aside>, role=complementary)
   안으로 좁혀서 찾는다. */
function sidebar() {
  return screen.getByRole("complementary");
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
  sessionStorage.clear();
});

afterEach(() => {
  resetSupabaseClient();
});

/*
 * **첫 진입이 로그인 화면이 아니다**(2026-09-06). 예전에는 신원이 없으면 관문으로
 * 되돌려보내 서비스를 보기도 전에 계정을 정하게 했다. 지금은 게스트 신원을 그
 * 자리에서 발급받아 곧장 메인을 연다 — 이 테스트가 그 전환을 잠근다.
 */
test("신원이 없어도 로그인 화면 없이 메인이 열린다", async () => {
  setMockSession(null);

  render(<App />);

  expect(await screen.findByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
  /* 관문을 거치지 않는다 — 거쳤다면 이 버튼이 먼저 보였을 것이다. */
  expect(screen.queryByRole("button", { name: "게스트로 시작하기" })).not.toBeInTheDocument();
});

/*
 * 발급이 실패하면 앱이 열릴 수 없다. 대기 문구에 머무르면 "느린 것"과 구분되지
 * 않아 사용자는 영영 기다린다 — 사유를 내고 다시 시도할 길을 준다.
 */
test("게스트 발급이 실패하면 사유를 알리고 다시 시도할 수 있다", async () => {
  setMockSession(null);
  setMockSignInError("anonymous_provider_disabled");

  render(<App />);

  expect(await screen.findByText("anonymous_provider_disabled")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "추천 시작하기" })).not.toBeInTheDocument();
});

test("이미 신원이 있으면 그대로 홈으로 간다", async () => {
  render(<App />);

  expect(await screen.findByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "게스트로 시작하기" })).not.toBeInTheDocument();
});

/*
 * 로그인 안 한 상태의 사이드바 바닥은 **신원 표시가 아니라 로그인 입구**다. 게스트에게
 * 보여줄 것은 "게스트 / 게스트로 이용 중"뿐이라 자리를 차지하고도 아무것도 알려주지
 * 못한다 — 그 자리에는 할 수 있는 동작이 온다.
 */
test("로그인 안 한 상태면 사이드바에 로그인 입구가 있다", async () => {
  setMockSession(null);

  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });

  expect(within(sidebar()).getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(within(sidebar()).queryByText("게스트로 이용 중")).not.toBeInTheDocument();
});

/* 계정 연결 후에는 같은 uid로 is_anonymous만 false가 된다(D-062 2절).
   표시 분기가 그 전환을 따라가는지 고정한다. */
test("계정으로 승격되면 게스트 표시가 사라진다", async () => {
  setMockSession({
    ...GUEST_SESSION,
    user: { ...GUEST_SESSION.user, is_anonymous: false, email: "trip@example.com" },
  } as typeof GUEST_SESSION);

  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });

  expect(within(sidebar()).getByText("trip@example.com")).toBeInTheDocument();
  expect(within(sidebar()).queryByText("게스트로 이용 중")).not.toBeInTheDocument();
});

/* 배지의 계정 메뉴(라벨 클릭 → 확인 → 해제, Esc 취소)는 AuthStatusBadge 자체의
   동작이라 AuthStatusBadge.test.tsx로 옮겼다 — 그 배지는 사이드바가 없는
   DeveloperChatPage에서만 쓰인다.

   사이드바에도 2026-09-04부터 계정 팝업이 있지만 **별개다.** 표시를 두 줄
   (이름 + 부제)로 내고, 담는 함수도 다르다(`identityDisplay` vs `identityLabel`).
   그쪽 동작은 SideDrawerContent.test.tsx가 잠근다. 여기서는 이 파일의 관심사인
   **신원 표시가 세션 전환을 따라가는지**만 본다. */

/* 조용한 통과 금지(D-042와 같은 방향). 설정이 없을 때 "비로그인 상태"로 넘어가면
   프론트가 토큰 없이 도는 걸 아무도 모른 채 계속 쓰게 된다. */
test("Supabase 설정이 없으면 통과시키지 않고 설정 오류를 드러낸다", async () => {
  resetSupabaseClient();
  vi.stubEnv("VITE_SUPABASE_URL", "");

  render(<App />);

  expect(await screen.findByText(/인증 설정이 없어요/)).toBeInTheDocument();
  expect(screen.getByText(/VITE_SUPABASE_URL/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "추천 시작하기" })).not.toBeInTheDocument();

  vi.stubEnv("VITE_SUPABASE_URL", "https://test.supabase.co");
});

/*
 * TP-240. getSession()이 안 끝나면 status가 loading에 머물러 "불러오는 중이에요…"가
 * 영영 남는다. 새로고침해도 같은 자리에서 또 멈춘다.
 *
 * 시한이 지나면 status가 "ready"(세션 null)로 확정되고, 그다음은 이 화면에
 * 로그인 관문이 없으므로(2026-09-06) RequireUser가 곧장 게스트로 자동
 * 로그인한다 — signInAsGuest는 signInAnonymously만 부르고 getSession을
 * 다시 부르지 않아 이 매달림과 무관하게 끝난다. 그래서 여기서 확인할 것은
 * "게스트로 시작하기" 버튼이 아니라 메인 화면이 실제로 열리는지다.
 */
test("저장된 세션 확인이 응답하지 않아도 화면이 멈추지 않는다", async () => {
  vi.useFakeTimers();
  hangMockGetSession();

  render(<App />);

  expect(screen.getByText("불러오는 중이에요…")).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(20_000);
  });

  /* findByRole 내부 폴링(waitFor)이 setTimeout을 쓴다 — 가짜 타이머를 켠 채로
     두면 더 이상 진행하지 않고 그대로 멎는다. 뒤이은 게스트 자동 로그인은
     타이머가 아니라 순수 Promise라 실제 타이머로 돌려도 놓치지 않는다. */
  vi.useRealTimers();
  expect(await screen.findByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
});

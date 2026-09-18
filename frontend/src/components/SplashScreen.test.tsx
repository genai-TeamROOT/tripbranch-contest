/*
 * 역할: 진입 스플래시가 "언제 걷히는지"와 "메인을 가로막지 않는지"를 검증한다.
 *
 * 이 화면의 값어치는 전부 타이밍에 있다. 준비되는 대로 걷으면 재방문자에게는
 * 한두 프레임만 번쩍이고, 시간만 재고 걷으면 신원 확인이 느린 날 관문의 로딩
 * 문구가 드러난다 — 두 조건을 **모두** 봐야 한다는 것이 잠글 대상이다.
 *
 * 신원 상태는 AuthContext를 갈아 끼워 직접 정한다. 진짜 AuthProvider로는
 * "확인이 아직 안 끝난 상태"를 붙잡아 둘 수 없어서(mock getSession이 즉시
 * 응답한다) 이 파일의 관심사인 분기를 만들지 못한다.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AuthStatus } from "../auth/AuthContext";
import { SplashScreen } from "./SplashScreen";

let authStatus: AuthStatus = "ready";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ status: authStatus }),
}));

/* 컴포넌트의 상수와 같은 값이다. 여기서 다시 적는 이유는, 상수를 import해 쓰면
   그 값을 잘못 바꿔도 테스트가 함께 따라가 아무것도 못 잡기 때문이다. */
const MIN_VISIBLE_MS = 900;
const FADE_MS = 420;

function splash() {
  return document.querySelector(".tb-splash");
}

beforeEach(() => {
  authStatus = "ready";
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("준비가 끝나 있어도 곧바로 걷히지 않는다", () => {
  render(<SplashScreen />);

  expect(screen.getByText("TripBranch")).toBeInTheDocument();

  act(() => {
    vi.advanceTimersByTime(MIN_VISIBLE_MS - 50);
  });

  /* 아직 최소 노출 시간 안이다. 여기서 사라지면 재방문자에게는 화면이 번쩍인다. */
  expect(splash()).not.toHaveClass("tb-splash--leaving");
});

test("최소 노출 시간이 지나면 걷히고 DOM에서 빠진다", () => {
  render(<SplashScreen />);

  act(() => {
    vi.advanceTimersByTime(MIN_VISIBLE_MS);
  });
  /* 걷히는 중에는 아직 남아 있어야 한다 — 애니메이션 도중에 지우면 반쯤 투명한
     상태에서 툭 사라진다. */
  expect(splash()).toHaveClass("tb-splash--leaving");

  act(() => {
    vi.advanceTimersByTime(FADE_MS);
  });
  expect(splash()).toBeNull();
});

/*
 * 시간만 보고 걷으면 이 경우에 관문(RequireUser)의 "불러오는 중이에요…"가 드러난다.
 * 스플래시를 두는 이유가 바로 그 순간을 감추는 것이라 앞뒤가 맞지 않게 된다.
 */
test("신원 확인이 안 끝났으면 최소 시간이 지나도 걷히지 않는다", () => {
  authStatus = "loading";
  const { rerender } = render(<SplashScreen />);

  act(() => {
    vi.advanceTimersByTime(MIN_VISIBLE_MS + FADE_MS * 2);
  });
  expect(splash()).not.toHaveClass("tb-splash--leaving");

  /* 확인이 끝나면 그때 걷힌다 — 최소 시간은 이미 지났으므로 더 기다리지 않는다. */
  authStatus = "ready";
  act(() => {
    rerender(<SplashScreen />);
  });
  expect(splash()).toHaveClass("tb-splash--leaving");
});

/*
 * 설정이 없으면 앱은 열리지 않지만 **안내는 보여야 한다.** 스플래시가 계속 덮고
 * 있으면 사용자는 원인을 못 보고 영영 안 열린다고만 본다.
 */
test("인증 설정이 없는 경우에도 스플래시는 걷힌다", () => {
  authStatus = "unconfigured";
  render(<SplashScreen />);

  /* 두 번에 나눠 진행시킨다 — 걷는 타이머는 최소 시간이 지난 뒤의 렌더에서야
     걸리므로, 한 번에 몰아 넣으면 그 타이머가 아직 없던 시점만 흘러간다. */
  act(() => {
    vi.advanceTimersByTime(MIN_VISIBLE_MS);
  });
  act(() => {
    vi.advanceTimersByTime(FADE_MS);
  });

  expect(splash()).toBeNull();
});

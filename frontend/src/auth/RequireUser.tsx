/*
 * 역할: 신원 없이 들어온 접근에 게스트 신원을 붙여 앱을 열어 준다.
 * 입력: AuthContext의 session/status/error.
 * 출력: 자식 화면, 신원을 만드는 동안의 대기 표시, 설정·발급 실패 표시.
 * 호출 시점: App의 Routes에서 신원이 필요한 화면을 감쌀 때 호출된다.
 *
 * **예전에는 /login으로 되돌려보냈다**(2026-09-06에 바꿨다). 첫 진입이 로그인
 * 화면이라 서비스를 보기도 전에 계정을 정해야 했다. 지금은 스플래시가 떠 있는
 * 동안 게스트 신원을 발급받고 곧장 메인을 연다 — 로그인은 사이드바 왼쪽 아래
 * 버튼으로 언제든 할 수 있는 선택이 됐다(SideDrawerContent §6).
 *
 * 세션을 아예 만들지 않고 여는 길도 있었지만 택하지 않았다. 백엔드가 모든 요청에
 * 토큰을 요구해서 대화·일정·취향이 전부 401이 되고, 화면마다 "로그인해야 되는
 * 기능"을 따로 그려야 한다. 게스트 세션은 이미 있던 경로라(LoginPage의 "게스트로
 * 시작하기") 백엔드를 손대지 않는다.
 *
 * 대신 **익명 계정이 방문자 수만큼 쌓인다** — 버튼을 눌러야 생기던 것이 들어오기만
 * 해도 생긴다. 재방문은 Supabase가 보관한 세션을 그대로 쓰므로 같은 uid다.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useAuth } from "./AuthContext";

function Notice({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-3 px-4 text-center">
      {children}
    </main>
  );
}

export function RequireUser({ children }: { children: ReactNode }) {
  const { session, status, error, signInAsGuest } = useAuth();
  const [guestError, setGuestError] = useState<string | null>(null);

  /*
   * **보내는 중인지**를 기억한다 — "한 번이라도 보냈는지"가 아니다. StrictMode가
   * 개발에서 effect를 두 번 돌려도 두 번째는 아직 응답 전이라 걸러지고(익명 계정이
   * 두 개 생기지 않는다), 나중에 계정 사용자가 로그아웃해 세션이 없어졌을 때는
   * 다시 발급받을 수 있다. "한 번이라도 보냈는지"로 두면 그 경우에 아무도 신원을
   * 만들지 않아 대기 문구에서 영영 멈춘다.
   *
   * 상태가 아니라 ref인 이유는 StrictMode의 두 번째 실행이 첫 렌더 값을 보기
   * 때문이다 — 상태로는 막히지 않는다.
   */
  const requesting = useRef(false);

  const requestGuest = useCallback(() => {
    requesting.current = true;
    setGuestError(null);
    void signInAsGuest()
      .catch((caught: unknown) => {
        /* 실패를 삼키고 대기 화면에 머무르면 영영 안 열리는 화면이 된다. */
        setGuestError(caught instanceof Error ? caught.message : "게스트로 시작하지 못했어요.");
      })
      .finally(() => {
        requesting.current = false;
      });
  }, [signInAsGuest]);

  useEffect(() => {
    if (status !== "ready" || session || requesting.current || guestError) return;
    requestGuest();
  }, [status, session, guestError, requestGuest]);

  /* 설정 누락은 다시 눌러도 같은 지점에서 실패한다. 원인을 그대로 드러낸다. */
  if (status === "unconfigured") {
    return (
      <Notice>
        <h1 className="text-lg font-bold text-ink">인증 설정이 없어요</h1>
        <p className="text-sm text-muted">{error}</p>
        <p className="text-sm text-muted">
          frontend/.env에 값을 채우고 개발 서버를 다시 시작해주세요.
        </p>
      </Notice>
    );
  }

  if (guestError) {
    return (
      <Notice>
        <h1 className="text-lg font-bold text-ink">앱을 열지 못했어요</h1>
        <p className="text-sm text-muted">{guestError}</p>
        <div>
          <button
            type="button"
            onClick={requestGuest}
            className="rounded-xl bg-brand px-4 py-2 text-sm font-medium text-white"
          >
            다시 시도
          </button>
        </div>
      </Notice>
    );
  }

  /* 신원 확인 중이거나, 게스트 발급이 아직 안 끝났다. 진입 순간이면 스플래시가
     이 위를 덮고 있다 — 문구가 보이는 것은 로그아웃 직후처럼 앱이 이미 떠 있는
     경우다. */
  if (status === "loading" || !session) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl items-center justify-center px-4">
        <p className="text-sm text-muted">불러오는 중이에요…</p>
      </main>
    );
  }

  return <>{children}</>;
}

/*
 * 역할: 서비스 진입 관문. 이메일 로그인과 게스트 시작을 함께 제공한다.
 * 입력: 이메일·비밀번호, 게스트 시작 버튼, 리다이렉트로 넘어온 원래 목적지.
 * 출력: 세션 발급 후 원래 목적지로 이동, 실패 시 오류 문구.
 * 호출 시점: 신원 없이 보호 라우트에 접근했거나 /login으로 직접 들어올 때 호출된다.
 *
 * **아직 확인하지 않은 계정은 로그인할 수 없다.** 이메일 확인이 켜져 있어서
 * 가입만 하고 메일 링크를 누르지 않으면 `email_not_confirmed`로 막힌다 —
 * authErrors가 "받은 메일의 링크를 눌러주세요"로 풀어 준다. 실패를 뭉뚱그리면
 * 사용자는 비밀번호가 틀린 줄 알고 계속 다시 친다.
 *
 * Figma와 다른 세 곳(2026-09-02 사용자 결정):
 * - Figma 하단은 "Google로 계속하기"인데 여기서는 **게스트 시작**이다. Google OAuth와
 *   "자동 로그인" 체크박스는 백엔드가 필요해 이번 범위에서 뺐다. 게스트 시작은 지금
 *   **유일하게 동작하는 진입 경로**라, Figma에 없다고 지우면 아무도 앱에 못 들어온다.
 * - 게스트 안내 문단은 뺐다("덜 눈에 띄게"). 고지 내용은 회원가입 도입 시 약관 화면으로
 *   옮긴다(D-062 9-3절).
 * - **아이디 찾기 링크를 뺐다.** 회원가입이 이름·이메일·비밀번호만 받으므로 아이디가
 *   곧 이메일이고, 찾을 대상이 따로 없다. 화면도 함께 지웠다.
 */

import { useState, type FormEvent } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { isGuestSession } from "../auth/identityLabel";
import { AuthLayout } from "../auth/AuthLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function LoginPage() {
  const { session, status, error: authError, linkError, signInAsGuest, signInWithEmail } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  /* 사이드바에서 왔으면 그 화면으로 돌려보낸다. 직접 들어온 경우엔 홈이다. */
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  /*
   * 이미 게스트로 앱을 쓰고 있는 사람이 로그인하러 온 경우다. 이때 하단 버튼을
   * "게스트로 시작하기"로 두면 안 된다 — signInAnonymously는 **새 익명 계정을
   * 만들기 때문에** 지금까지 쌓은 대화가 달린 uid를 잃는다. 그 자리에는 마음을
   * 바꿨을 때 돌아갈 길만 있으면 된다.
   */
  const alreadyBrowsing = status === "ready" && session !== null;

  /*
   * **게스트는 통과시킨다**(2026-09-06). 진입이 게스트로 자동으로 열리게 바뀌면서
   * (`RequireUser`) 이 화면에 오는 사람은 전부 게스트 세션을 달고 온다 — 예전처럼
   * "세션이 있으면 되돌려보내기"로 두면 사이드바의 로그인 버튼이 아무 데도 못 간다.
   *
   * 계정 사용자는 그대로 되돌려보낸다. 이미 로그인한 사람에게 로그인 화면은
   * 할 일이 없는 화면이다.
   */
  if (status === "ready" && session && !isGuestSession(session)) {
    return <Navigate to={from} replace />;
  }

  async function handleLoginSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading) return;

    /* 빈 값으로 보내면 서버가 invalid_credentials로 돌려주는데, 그러면 "비밀번호가
       틀렸나" 싶어진다. 안 채운 것과 틀린 것은 다른 상태다. */
    if (!email.trim() || !password) {
      setErrorMessage("이메일과 비밀번호를 모두 입력해주세요.");
      return;
    }

    setIsLoading(true);
    setErrorMessage(null);
    try {
      await signInWithEmail(email.trim(), password);
      navigate(from, { replace: true });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "로그인하지 못했어요.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleGuestStart() {
    if (isLoading) return;
    setIsLoading(true);
    setErrorMessage(null);
    try {
      await signInAsGuest();
      navigate(from, { replace: true });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "게스트로 시작하지 못했어요.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <AuthLayout
      title="TripBranch"
      description="로그인하고 지금 상황에 맞는 장소를 찾아보세요"
      footer={
        <>
          {/* 폼 밖에 있으므로 form 속성으로 연결한다 — Figma에서 버튼이 하단 바에 있다. */}
          <Button type="submit" form="login-form" size="lg" disabled={isLoading}>
            {isLoading ? "로그인하는 중이에요…" : "로그인"}
          </Button>

          <div className="flex items-center gap-3 text-xs text-muted">
            <span className="h-px flex-1 bg-border" />
            또는
            <span className="h-px flex-1 bg-border" />
          </div>

          <Button
            type="button"
            variant="outline"
            size="lg"
            disabled={isLoading || status !== "ready"}
            onClick={() =>
              alreadyBrowsing ? navigate(from, { replace: true }) : void handleGuestStart()
            }
          >
            {alreadyBrowsing
              ? "로그인 없이 둘러보기"
              : isLoading
                ? "시작하는 중이에요…"
                : "게스트로 시작하기"}
          </Button>
        </>
      }
    >
      {/* Figma는 Brand와 이 묶음 사이가 32, 묶음 안 요소 사이가 20이다. */}
      <div className="flex flex-col gap-5">
        {status === "unconfigured" ? (
          <section className="rounded-xl bg-chip p-3 text-sm text-ink">
            <p className="font-bold">인증 설정이 없어요</p>
            <p className="mt-1 text-muted">{authError}</p>
            <p className="mt-1 text-muted">frontend/.env를 채우고 개발 서버를 다시 시작해주세요.</p>
          </section>
        ) : null}

        {/* 방금 누른 버튼의 실패가 먼저다. 링크 오류는 그 아래로 밀린다 —
            이미 지나간 일이라 지금 하려는 동작을 가리면 안 된다. */}
        {(errorMessage ?? linkError) ? (
          <ErrorBanner message={errorMessage ?? linkError ?? ""} />
        ) : null}

        <form
          id="login-form"
          onSubmit={handleLoginSubmit}
          className="flex flex-col gap-4"
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-email">이메일</Label>
            <Input
              id="login-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="example@email.com"
              autoComplete="email"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-password">비밀번호</Label>
            {/*
             * 눈 아이콘을 입력칸 안 오른쪽에 겹친다(Figma 27:33). 입력칸 자체에
             * 오른쪽 여백을 줘서 긴 값이 아이콘 밑으로 들어가지 않게 한다.
             */}
            <div className="relative">
              <Input
                id="login-password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="비밀번호를 입력하세요"
                autoComplete="current-password"
                className="pr-11"
              />
              <button
                type="button"
                onClick={() => setShowPassword((shown) => !shown)}
                aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 보기"}
                aria-pressed={showPassword}
                className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-muted transition-colors hover:text-ink"
              >
                {showPassword ? <EyeOff size={17} aria-hidden /> : <Eye size={17} aria-hidden />}
              </button>
            </div>
          </div>
        </form>

        <div className="flex justify-center gap-3 text-xs text-muted">
          <Link to="/reset-password" className="hover:text-brand">
            비밀번호 찾기
          </Link>
          <span aria-hidden="true">·</span>
          <Link to="/signup" className="hover:text-brand">
            회원가입
          </Link>
        </div>
      </div>
    </AuthLayout>
  );
}

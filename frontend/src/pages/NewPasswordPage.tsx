/*
 * 역할: 재설정 메일 링크로 돌아온 사람에게 새 비밀번호를 받는다.
 * 입력: 새 비밀번호와 확인. 신원은 URL이 아니라 세션에서 온다.
 * 출력: 비밀번호 변경 후 홈으로, 실패 시 오류 문구.
 * 호출 시점: /reset-password/new. 메일의 링크가 이 경로로 돌아온다
 *   (AuthContext의 sendPasswordReset이 redirectTo로 지정한 주소다).
 *
 * 골격은 다른 인증 화면과 같다 — AuthLayout 헤더형(뒤로가기 + 제목) + 하단 버튼.
 *
 * **토큰을 직접 다루지 않는다.** Supabase 클라이언트가 링크의 조각(#access_token=…)을
 * 읽어 세션을 세우고 PASSWORD_RECOVERY 이벤트를 쏜다(supabaseClient의
 * detectSessionInUrl). URL에서 토큰을 꺼내 손으로 처리하면 이미 검증된 경로를 다시
 * 만드는 셈이고, 그 과정에서 토큰이 히스토리나 로그에 남을 여지가 생긴다.
 *
 * **세션이 있는 것만으로는 폼을 열지 않는다.** 그렇게 두면 평범하게 로그인한 사람이
 * 이 주소를 치기만 해도 현재 비밀번호 없이 새 비밀번호를 정할 수 있다 — 잠깐 기기를
 * 만진 사람이 계정을 가져가는 길이다. 그래서 "재설정 링크가 세운 세션"인지
 * (isPasswordRecovery)까지 본다. 그 값은 주소창이 아니라 Supabase가 토큰을 서버에
 * 확인시킨 뒤 쏘는 이벤트에서 온다.
 *
 * 링크 없이 들어왔거나 링크가 만료됐으면 폼 대신 안내를 보여준다. 폼을 띄우고
 * 제출 시점에 실패시키면 사용자는 비밀번호를 다 입력한 뒤에야 헛수고였음을 안다.
 */

import { useState, type FormEvent } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AuthLayout } from "../auth/AuthLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function NewPasswordPage() {
  const { session, status, isPasswordRecovery, linkError, updatePassword } = useAuth();
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    /* 서버가 판단할 수 없는 것만 여기서 본다(SignupPage와 같은 기준). 길이·문자
       종류·유출 여부는 Supabase 정책이 정본이다. */
    if (!password) {
      setErrorMessage("새 비밀번호를 입력해주세요.");
      return;
    }
    if (password !== passwordConfirm) {
      setErrorMessage("두 번 입력한 비밀번호가 서로 달라요.");
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      await updatePassword(password);
      /* 비밀번호를 바꾸면 이미 로그인된 상태다 — 다시 로그인시키지 않는다. */
      navigate("/", { replace: true });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "비밀번호를 바꾸지 못했어요.");
    } finally {
      setIsSubmitting(false);
    }
  }

  /* 세션을 세우는 중이다. 이 짧은 순간에 "링크가 만료됐다"고 말하면 안 된다. */
  if (status === "loading") {
    return (
      <AuthLayout title="비밀번호 재설정" backTo="/login" footer={null}>
        <p className="text-sm text-muted">링크를 확인하고 있어요…</p>
      </AuthLayout>
    );
  }

  if (!session || !isPasswordRecovery) {
    return (
      <AuthLayout
        title="비밀번호 재설정"
        backTo="/login"
        footer={
          <Button type="button" size="lg" onClick={() => navigate("/reset-password")}>
            재설정 링크 다시 받기
          </Button>
        }
      >
        {/* 서버가 이유를 알려줬으면 그걸 쓴다. 뭉뚱그린 문구보다 "만료됐다"가 낫다. */}
        <p role="status" className="rounded-xl bg-chip p-4 text-sm leading-relaxed text-ink">
          {linkError ?? "링크가 만료되었거나 올바르지 않아요. 재설정 메일을 다시 받아 새 링크로 들어와주세요."}
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="새 비밀번호"
      description="앞으로 이 비밀번호로 로그인해요."
      backTo="/login"
      footer={
        <Button type="submit" form="new-password-form" size="lg" disabled={isSubmitting}>
          {isSubmitting ? "바꾸는 중이에요…" : "비밀번호 바꾸기"}
        </Button>
      }
    >
      <form
        id="new-password-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        {errorMessage ? <ErrorBanner message={errorMessage} /> : null}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-password">새 비밀번호</Label>
          {/* 눈 아이콘은 로그인·회원가입 화면과 같은 방식이다(Figma 27:72). */}
          <div className="relative">
            <Input
              id="new-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="8자 이상 입력하세요"
              autoComplete="new-password"
              aria-describedby="new-password-help"
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
          {/* 회원가입 화면과 같은 조건이다 — 한쪽만 적으면 두 화면이 다른 말을 한다. */}
          <p id="new-password-help" className="text-xs leading-relaxed text-muted">
            8자 이상으로 입력해주세요. 이미 유출된 비밀번호는 쓸 수 없어요.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="new-password-confirm">새 비밀번호 확인</Label>
          <Input
            id="new-password-confirm"
            type="password"
            value={passwordConfirm}
            onChange={(event) => setPasswordConfirm(event.target.value)}
            placeholder="비밀번호를 한 번 더 입력하세요"
            autoComplete="new-password"
          />
        </div>

        <p className="text-center text-xs text-muted">
          <Link to="/login" className="hover:text-brand">
            로그인 화면으로 돌아가기
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}

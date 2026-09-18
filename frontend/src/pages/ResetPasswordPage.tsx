/*
 * 역할: 비밀번호 찾기 화면. Figma의 ResetPassword 프레임(27:109)을 옮긴 것이다.
 * 입력: 가입한 이메일.
 * 출력: 재설정 메일 발송 요청 → "보냈어요" 안내.
 * 호출 시점: /reset-password. 로그인 화면의 "비밀번호 찾기" 링크로 들어온다.
 *
 * **가입되지 않은 주소여도 똑같이 "보냈어요"라고 말한다.** Supabase가 그 경우에도
 * 오류를 내지 않는데(계정 열거 방지), 화면에서 성공·실패를 갈라 보여주면 그 자체가
 * "이 주소는 가입돼 있다"는 신호가 된다. 그 보호를 우리가 벗기지 않는다.
 *
 * 메일 링크는 /reset-password/new(NewPasswordPage)로 돌아온다. 그 주소가 Supabase
 * 대시보드의 Redirect URLs에 없으면 조용히 Site URL로 대신 보내진다 — 링크를 눌러도
 * 아무 일이 없는 것처럼 보이는 증상이 그것이다.
 */

import { useState, type FormEvent } from "react";
import { Mail } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { AuthLayout } from "../auth/AuthLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function ResetPasswordPage() {
  const { sendPasswordReset } = useAuth();

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    if (!email.trim()) {
      setErrorMessage("이메일을 입력해주세요.");
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      await sendPasswordReset(email.trim());
      setSubmitted(true);
    } catch (error) {
      /* 발송 한도 같은 실제 실패만 여기로 온다. 가입 여부는 오류로 오지 않는다. */
      setErrorMessage(error instanceof Error ? error.message : "메일을 보내지 못했어요.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="비밀번호 찾기"
      description="가입한 이메일로 비밀번호 재설정 링크를 보내드려요."
      backTo="/login"
      footer={
        <Button type="submit" form="reset-password-form" size="lg" disabled={isSubmitting}>
          {isSubmitting ? "보내는 중이에요…" : "재설정 링크 보내기"}
        </Button>
      }
    >
      <form
        id="reset-password-form"
        onSubmit={handleSubmit}
        className="flex flex-col gap-4"
        noValidate
      >
        {errorMessage ? <ErrorBanner message={errorMessage} /> : null}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="reset-password-email">이메일</Label>
          <Input
            id="reset-password-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="example@email.com"
            autoComplete="email"
          />
        </div>

        {/*
         * 결과 박스(Figma 27:126). 주소 뒤에 조사를 붙이지 않는다 — 받침 유무로
         * "로/으로"가 갈리는데 이메일 끝 글자는 사람마다 다르다.
         */}
        {submitted && (
          <p
            role="status"
            className="flex items-start gap-2.5 rounded-xl bg-sky-light p-4 text-sm leading-relaxed text-brand-deep"
          >
            <Mail size={18} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              가입된 주소라면 재설정 링크를 보냈어요. 메일이 안 보이면 스팸함도 확인해주세요.
            </span>
          </p>
        )}
      </form>
    </AuthLayout>
  );
}

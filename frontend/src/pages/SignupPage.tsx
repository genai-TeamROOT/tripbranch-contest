/*
 * 역할: 회원가입 화면. Figma의 Signup 프레임(27:52)을 옮긴 것이다.
 * 입력: 이름·이메일·비밀번호·비밀번호 확인·약관 동의.
 * 출력: 가입 요청 → "메일을 보냈어요" 안내, 실패 시 오류 문구.
 * 호출 시점: /signup 라우트. 로그인 화면의 "회원가입" 링크로 들어온다.
 *
 * **가입해도 바로 로그인되지 않는다.** 이메일 확인이 켜져 있어(Supabase 대시보드,
 * `mailer_autoconfirm: false`) 메일의 링크를 눌러야 계정이 열린다. 그래서 성공하면
 * 홈으로 보내지 않고 이 화면에서 안내로 바꾼다 — 로그인된 것처럼 굴면 사용자는
 * 메일을 확인하지 않고 떠난다.
 *
 * 약관 "보기"는 TermsModal을 연다(Figma 64:2). 본문은 2026-09-15에 채웠다 — 코드로
 * 확인한 사실만 적었고, 못 채운 조는 지우지 않고 왜 못 적는지를 그 자리에 남겼다
 * (TermsModal 서두 참고). 동의 체크박스는 그대로 필수다.
 */

import { useState, type FormEvent } from "react";
import { Eye, EyeOff, MailCheck } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { AuthLayout } from "../auth/AuthLayout";
import { ErrorBanner } from "../components/ErrorBanner";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { TermsModal } from "../components/layout/TermsModal";

export function SignupPage() {
  const { signUpWithEmail } = useAuth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [agreed, setAgreed] = useState(false);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  /*
   * 화면에서 거르는 것은 **서버가 판단할 수 없는 것뿐이다.** 두 번 입력한 값이
   * 같은지는 서버로 가지 않으므로 여기서 봐야 하고, 길이·문자 종류·유출 여부는
   * Supabase 정책이 정본이라 서버에 맡긴다(authErrors가 사유별로 풀어 준다).
   * 여기에 길이 검사를 또 두면 대시보드 설정을 바꾸는 순간 두 곳이 갈린다.
   */
  function localError(): string | null {
    if (!name.trim()) return "이름을 입력해주세요.";
    if (!email.trim()) return "이메일을 입력해주세요.";
    if (!password) return "비밀번호를 입력해주세요.";
    if (password !== passwordConfirm) return "두 번 입력한 비밀번호가 서로 달라요.";
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    const invalid = localError();
    if (invalid) {
      setErrorMessage(invalid);
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      await signUpWithEmail({ name: name.trim(), email: email.trim(), password });
      setSentTo(email.trim());
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "가입하지 못했어요.");
    } finally {
      setIsSubmitting(false);
    }
  }

  /*
   * 메일을 보낸 뒤에는 폼을 치운다. 같은 화면에 폼이 남아 있으면 "한 번 더 눌러야
   * 하나" 싶어 다시 제출하게 되고, 그때마다 확인 메일이 또 나간다(발송 한도에 걸린다).
   */
  if (sentTo) {
    return (
      <AuthLayout
        title="메일을 확인해주세요"
        backTo="/login"
        footer={
          <Button type="button" size="lg" onClick={() => setSentTo(null)}>
            다른 이메일로 가입하기
          </Button>
        }
      >
        <p
          role="status"
          className="flex items-start gap-2.5 rounded-xl bg-sky-light p-4 text-sm leading-relaxed text-brand-deep"
        >
          <MailCheck size={18} className="mt-0.5 shrink-0" aria-hidden />
          {/*
           * 이메일 뒤에 조사를 붙이지 않는다. 받침이 있느냐에 따라 "로/으로"가
           * 갈리는데 주소 끝 글자는 사용자마다 달라서 어느 쪽을 고정해도 절반은
           * 틀린다("…co.kr 으로"처럼). "이 주소로"로 받아 조사를 주소에서 떼어낸다.
           */}
          <span>
            <strong className="font-bold">{sentTo}</strong>
            <br />이 주소로 확인 메일을 보냈어요. 메일의 링크를 눌러야 가입이 끝나요.
            <br />
            메일이 안 보이면 스팸함도 확인해주세요.
          </span>
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="회원가입"
      backTo="/login"
      footer={
        <Button type="submit" form="signup-form" size="lg" disabled={!agreed || isSubmitting}>
          {isSubmitting ? "가입하는 중이에요…" : "가입하고 시작하기"}
        </Button>
      }
    >
      <form id="signup-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {errorMessage ? <ErrorBanner message={errorMessage} /> : null}

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="signup-name">이름</Label>
          <Input
            id="signup-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="이름을 입력하세요"
            autoComplete="name"
            aria-describedby="signup-name-help"
          />
          {/*
           * 이름을 왜 받는지 그 자리에서 밝힌다. 인증에 쓰이는 값이 아니라
           * 대화에서 부르는 호칭이라, 묻는 이유를 적어 두지 않으면 굳이 왜
           * 필요한지 알 수 없다. aria-describedby로 묶어 스크린 리더도 입력칸을
           * 읽을 때 함께 듣는다.
           */}
          <p id="signup-name-help" className="text-xs leading-relaxed text-muted">
            AI가 추천할 때 이 이름으로 불러드려요.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="signup-email">이메일</Label>
          <Input
            id="signup-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="example@email.com"
            autoComplete="email"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="signup-password">비밀번호</Label>
          {/* 눈 아이콘은 로그인 화면과 같은 방식이다(Figma 27:72). */}
          <div className="relative">
            <Input
              id="signup-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="8자 이상 입력하세요"
              autoComplete="new-password"
              aria-describedby="signup-password-help"
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
          {/*
           * 서버가 실제로 요구하는 조건을 미리 밝힌다. **문자 종류 4종 조건은
           * 2026-09-15에 껐다** — 조합 규칙은 사용자를 괴롭히는 만큼의 효과가 없다.
           * 같은 날 실제 가입 요청으로 재확인한 정책은 둘이다: 길이 8 이상,
           * 유출 목록에 없을 것(`reasons: ["pwned"]`).
           *
           * 유출 여부는 화면이 알 수 없어 미리 걸러 줄 수 없다. 그래서 "쓸 수 없다"만
           * 알려 두고, 실제로 걸린 사람에게는 authErrors가 사유를 풀어 준다.
           */}
          <p id="signup-password-help" className="text-xs leading-relaxed text-muted">
            8자 이상으로 입력해주세요. 이미 유출된 비밀번호는 쓸 수 없어요.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="signup-password-confirm">비밀번호 확인</Label>
          <Input
            id="signup-password-confirm"
            type="password"
            value={passwordConfirm}
            onChange={(event) => setPasswordConfirm(event.target.value)}
            placeholder="비밀번호를 한 번 더 입력하세요"
            autoComplete="new-password"
          />
        </div>

        {/* Figma 27:83 — 체크박스와 문구는 왼쪽, "보기"는 오른쪽 끝에 붙는다. */}
        <div className="flex items-center gap-2 text-sm text-ink">
          <Checkbox
            id="signup-agree"
            checked={agreed}
            onCheckedChange={(checked) => setAgreed(checked === true)}
          />
          <Label htmlFor="signup-agree" className="flex-1 font-normal">
            이용약관 및 개인정보처리방침에 동의합니다
          </Label>
          <button
            type="button"
            onClick={() => setShowTerms(true)}
            className="shrink-0 text-xs text-muted transition-colors hover:text-brand"
          >
            보기
          </button>
        </div>
      </form>

      {showTerms && <TermsModal onClose={() => setShowTerms(false)} />}
    </AuthLayout>
  );
}

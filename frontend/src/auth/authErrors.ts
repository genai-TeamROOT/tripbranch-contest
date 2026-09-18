/*
 * 역할: Supabase auth 오류를 사용자에게 보여줄 한국어 문구로 바꾼다.
 * 입력: signUp/signInWithPassword/resetPasswordForEmail이 돌려준 오류.
 * 출력: 화면에 그대로 띄울 한 문장.
 * 호출 시점: AuthContext의 인증 함수들이 오류를 던지기 직전에 호출한다.
 *
 * 세 화면(로그인·회원가입·비밀번호 재설정)이 같은 문구를 써야 해서 한곳에 모은다.
 * Supabase 원문은 영어이고, 그대로 띄우면 "Invalid login credentials" 같은 문장이
 * 화면에 나온다.
 *
 * **아이디 존재 여부를 알려주는 문구를 만들지 않는다.** "가입되지 않은 이메일이에요"
 * 같은 문장은 친절해 보이지만, 이메일을 하나씩 넣어 보면 어떤 주소가 가입돼 있는지
 * 알아낼 수 있는 통로가 된다(계정 열거). Supabase가 로그인 실패를 이메일·비밀번호
 * 구분 없이 하나로 뭉쳐 돌려주는 것도 같은 이유다 — 그 뭉침을 여기서 풀지 않는다.
 */

/** Supabase 오류에서 우리가 실제로 읽는 부분만. AuthError 전체를 끌고 오지 않는다. */
export interface SupabaseAuthErrorLike {
  message?: string;
  code?: string;
  status?: number;
  /**
   * weak_password일 때만 온다. 같은 자리가 두 모양으로 오므로 둘 다 본다 —
   * supabase-js의 AuthWeakPasswordError는 `reasons`를 객체에 바로 달고,
   * REST 응답 본문은 `weak_password.reasons`로 감싼다.
   */
  reasons?: string[];
  weak_password?: { reasons?: string[] };
}

const FALLBACK = "요청을 처리하지 못했어요. 잠시 후 다시 시도해주세요.";

/*
 * 비밀번호 정책은 Supabase 대시보드에 있고 코드에 없다. 2026-09-15 실측 기준
 * **길이 8 이상 · 유출 목록에 없을 것** 둘이 걸려 있다(실제 가입 요청 3건으로
 * 확인: 7자 → `["length"]`, 8자 소문자만 유출값 → `["pwned"]`).
 * 문자 종류 4종 조건은 같은 날 껐다.
 *
 * 둘을 하나로 뭉쳐 "비밀번호가 너무 쉬워요"라고만 말하면 사용자는 무엇을 고쳐야
 * 하는지 모른 채 같은 값을 다시 넣는다. 다행히 서버가 사유를 배열로 준다
 * (`reasons: ["length", "pwned"]`). 영어 문장을 정규식으로 긁지 않고 이 배열로
 * 가른다 — 문구가 바뀌어도 안 깨진다.
 *
 * `characters` 번역은 정책이 꺼진 뒤에도 남긴다. 이 표는 정책이 아니라 **서버가
 * 보내는 사유의 번역**이고, 대시보드를 다시 켜는 순간 그 사유가 또 온다. 지우면
 * 그때 사용자는 무엇을 고칠지 모르는 뭉뚱그린 문구를 받는다.
 */
const WEAK_PASSWORD_REASON: Record<string, string> = {
  characters: "대문자·소문자·숫자·기호를 각각 하나 이상 넣어주세요.",
  pwned: "이미 유출된 적이 있는 비밀번호예요. 다른 값으로 정해주세요.",
};

/* 최소 길이는 대시보드 설정이라 언제든 바뀐다. 서버가 알려준 숫자를 그대로 쓰고,
   못 읽으면 지금 설정값인 8로 적는다. 여기에 숫자를 박아두면 설정을 바꾼 날부터
   화면이 거짓말을 시작한다. */
function lengthReason(message: string): string {
  const found = /at least (\d+) characters/i.exec(message);
  return `${found ? found[1] : "8"}자 이상으로 입력해주세요.`;
}

function weakPasswordMessage(error: SupabaseAuthErrorLike): string | null {
  const reasons = error.reasons ?? error.weak_password?.reasons;
  if (!reasons?.length) return null;

  const parts = reasons
    .map((reason) =>
      reason === "length" ? lengthReason(error.message ?? "") : WEAK_PASSWORD_REASON[reason],
    )
    .filter((part): part is string => Boolean(part));

  /* 사유가 왔는데 우리가 모르는 값뿐이면 뭉뚱그린 문구로 돌아간다. */
  if (parts.length === 0) return null;
  return `비밀번호를 다시 정해주세요. ${parts.join(" ")}`;
}

/*
 * code로 먼저 맞춰 본다. Supabase가 code를 안 채우는 경로가 남아 있어서
 * message 조각까지 함께 본다 — code만 보면 조용히 FALLBACK으로 새어 나간다.
 */
const BY_CODE: Record<string, string> = {
  invalid_credentials: "이메일 또는 비밀번호가 맞지 않아요.",
  email_not_confirmed: "아직 이메일 확인이 끝나지 않았어요. 받은 메일의 링크를 눌러주세요.",
  weak_password: "비밀번호가 너무 쉬워요. 8자 이상으로, 다른 곳에서 쓰지 않는 값으로 정해주세요.",
  over_email_send_rate_limit: "메일을 너무 자주 보냈어요. 잠시 후에 다시 시도해주세요.",
  over_request_rate_limit: "요청이 너무 잦아요. 잠시 후에 다시 시도해주세요.",
  validation_failed: "입력한 값을 다시 확인해주세요.",
  email_address_invalid: "이메일 형식이 올바르지 않아요.",
  signup_disabled: "지금은 회원가입을 받고 있지 않아요.",
  email_provider_disabled: "지금은 이메일로 가입할 수 없어요.",
  same_password: "지금 쓰고 있는 비밀번호와 달라야 해요.",
  /*
   * **위 계정 열거 원칙의 유일한 예외다.** 이 오류는 게스트가 자기 계정을 만드는
   * 경로(updateUser)에서만 온다 — 가입(signUp)은 Supabase가 이미 뭉개서 돌려주므로
   * 여기까지 오지 않는다.
   *
   * 예외를 두는 이유는 두 가지다. 첫째, 이 경로는 게스트 세션이 있어야 도달하므로
   * 주소를 하나씩 넣어 캐내려면 매번 세션을 새로 발급해야 한다 — 익명 가입이 그
   * 자체로 한도에 걸린다. 둘째, 여기서 뭉개면 사용자가 무엇을 해야 할지 알 수 없다:
   * 가입은 실패했는데 게스트 상태는 그대로라 화면상 아무 일도 안 일어난 것처럼
   * 보인다. guest-auth-design.md 8절이 "무엇을 잃는지 알려주고 끊는다"로 정한 지점이다.
   */
  email_exists: "이미 가입된 이메일이에요. 그 계정으로 로그인해주세요.",
};

/* message 원문 조각 → 문구. code가 비어 있을 때만 쓴다. */
const BY_MESSAGE: Array<[RegExp, string]> = [
  [/invalid login credentials/i, BY_CODE.invalid_credentials],
  [/email not confirmed/i, BY_CODE.email_not_confirmed],
  [/password should be at least/i, BY_CODE.weak_password],
  [/rate limit|too many requests/i, BY_CODE.over_request_rate_limit],
  [/unable to validate email|invalid format/i, BY_CODE.email_address_invalid],
  [/signups not allowed|signup is disabled/i, BY_CODE.signup_disabled],
  [/email address (is )?already (been )?registered|already exists/i, BY_CODE.email_exists],
];

/*
 * 메일 링크가 실패했을 때 주소창에 실려 오는 오류다. 위 오류들과 성격이 다르다 —
 * 사용자가 방금 누른 버튼의 실패가 아니라 **이미 지나간 링크**의 실패라서, 문구도
 * "다시 시도"가 아니라 "메일을 다시 받아라"가 돼야 한다.
 *
 * `error_description`은 영어 원문이라 그대로 띄우지 않는다.
 */
const BY_LINK_ERROR_CODE: Record<string, string> = {
  otp_expired: "링크가 만료됐어요. 메일을 다시 받아 새 링크로 들어와주세요.",
  server_error: "서버가 링크를 처리하지 못했어요. 잠시 후 메일을 다시 받아주세요.",
  unexpected_failure: "링크를 처리하는 중에 문제가 생겼어요. 메일을 다시 받아주세요.",
};

export function authLinkErrorMessage(error: { kind: string; code: string }): string {
  if (BY_LINK_ERROR_CODE[error.code]) return BY_LINK_ERROR_CODE[error.code];
  /* 링크가 한 번 쓰였거나 취소된 경우가 여기로 온다. 만료와 구분해 말할 근거가
     없으므로 뭉뚱그리되, 다음에 할 일은 똑같이 알려준다. */
  if (error.kind === "access_denied") {
    return "링크가 더 이상 쓸 수 없는 상태예요. 메일을 다시 받아 새 링크로 들어와주세요.";
  }
  return "링크를 확인하지 못했어요. 메일을 다시 받아 새 링크로 들어와주세요.";
}

export function authErrorMessage(error: SupabaseAuthErrorLike | null | undefined): string {
  if (!error) return FALLBACK;

  /* 사유가 붙어 온 비밀번호 오류가 가장 구체적이다. code 매핑보다 먼저 본다. */
  const weak = weakPasswordMessage(error);
  if (weak) return weak;

  if (error.code && BY_CODE[error.code]) return BY_CODE[error.code];

  const message = error.message ?? "";
  for (const [pattern, text] of BY_MESSAGE) {
    if (pattern.test(message)) return text;
  }

  /* 429는 code가 없어도 의미가 분명하다. */
  if (error.status === 429) return BY_CODE.over_request_rate_limit;

  /*
   * status 0은 응답을 못 받았다는 뜻이다(AuthRetryableFetchError). 우리가 건 15초
   * 타임아웃이 여기로 온다(supabaseClient.fetchWithTimeout, TP-240). 입력이 틀린
   * 것과 서버에 못 닿은 것은 다음에 할 일이 달라서 뭉뚱그리지 않는다.
   */
  if (error.status === 0) return "서버에 닿지 못했어요. 잠시 후 다시 시도해주세요.";

  return FALLBACK;
}

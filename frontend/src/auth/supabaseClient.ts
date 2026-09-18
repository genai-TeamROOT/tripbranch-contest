/*
 * 역할: 인증용 Supabase 클라이언트를 지연 생성하고, "재설정 링크로 들어왔다"는
 *   사실을 기억한다.
 * 입력: VITE_SUPABASE_URL, VITE_SUPABASE_PUBLISHABLE_KEY 환경변수.
 * 출력: SupabaseClient 싱글턴 또는 SupabaseConfigError, 그리고 비밀번호 재설정 신호.
 * 호출 시점: AuthProvider가 세션을 확인하거나 게스트·이메일 인증을 요청할 때.
 * TODO: 소셜 provider 연동(D-062 Phase 5)도 이 클라이언트를 쓴다.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/* 설정 누락을 "로그인 안 된 상태"와 구분하기 위한 전용 오류. AuthProvider가 이걸
   보고 화면에 설정 오류를 드러낸다 — 조용히 비로그인으로 넘기지 않는다(D-042). */
export class SupabaseConfigError extends Error {
  constructor(public readonly missing: string[]) {
    super(`Supabase 설정이 없어요: ${missing.join(", ")}`);
    this.name = "SupabaseConfigError";
  }
}

let cached: SupabaseClient | null = null;

/*
 * 링크가 실패하면 Supabase는 세션 대신 오류를 조각에 실어 돌려보낸다.
 *
 *   /login#error=access_denied&error_code=otp_expired&error_description=…
 *
 * 지금까지 이 값은 아무도 안 읽어서 화면에 아무 말도 안 나왔다. 사용자는 링크를
 * 눌렀는데 그냥 로그인 화면이 뜬 것으로만 보인다.
 *
 * **토큰은 읽지 않는다.** 여기서 보는 것은 error·error_code·error_description
 * 셋뿐이고, 이 값들로 신원을 판단하지도 않는다 — 화면에 뭐라고 적을지만 고른다.
 */
export interface AuthLinkError {
  /** 예: access_denied */
  kind: string;
  /** 예: otp_expired */
  code: string;
  /** 서버가 준 영어 원문. 화면에 그대로 띄우지 않는다. */
  description: string;
}

/* undefined는 "아직 안 읽음", null은 "읽었고 오류가 없었음"이다. 둘을 구분해야
   StrictMode가 effect를 두 번 돌려도 같은 답이 나온다. */
let linkErrorSnapshot: AuthLinkError | null | undefined;

function parseLinkError(raw: string): AuthLinkError | null {
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const kind = params.get("error");
  const code = params.get("error_code");
  if (!kind && !code) return null;
  return {
    kind: kind ?? "",
    code: code ?? "",
    description: params.get("error_description") ?? "",
  };
}

/*
 * 주소창에 실려 온 링크 오류를 돌려준다. **클라이언트를 만들기 전에 불러야 한다** —
 * auth-js가 주소를 정리하고 나면 사라진다.
 *
 * 여러 번 불러도 같은 값을 준다. 읽고 나면 주소창에서 조각을 지우는데, 새로고침할
 * 때마다 지난 오류가 되살아나지 않게 하기 위해서다. 오류가 실려 있을 때만 지우므로
 * 정상 링크의 토큰을 건드릴 일은 없다.
 */
export function authLinkError(): AuthLinkError | null {
  if (linkErrorSnapshot !== undefined) return linkErrorSnapshot;
  if (typeof window === "undefined") {
    linkErrorSnapshot = null;
    return null;
  }

  linkErrorSnapshot =
    parseLinkError(window.location.hash.replace(/^#/, "")) ??
    parseLinkError(window.location.search.replace(/^\?/, ""));

  if (linkErrorSnapshot) {
    window.history.replaceState(window.history.state, "", window.location.pathname);
  }
  return linkErrorSnapshot;
}

/*
 * 재설정 링크로 들어온 세션인지를 기억한다.
 *
 * **왜 모듈에 두는가.** PASSWORD_RECOVERY는 클라이언트가 처음 초기화되면서 URL
 * 조각을 읽을 때 딱 한 번 나온다. 컴포넌트 안에서만 구독하면 놓칠 수 있다 —
 * StrictMode는 effect를 두 번 돌리는데 두 번째에는 클라이언트가 이미 캐시돼 있어
 * 초기화가 다시 일어나지 않는다. 그래서 클라이언트를 만드는 그 자리에서(초기화가
 * 끝나기 전에) 붙잡아 모듈에 남긴다.
 *
 * **주소창을 우리가 읽지 않는 것이 요점이다.** #type=recovery는 아무나 손으로 붙일
 * 수 있다. 그걸 근거로 삼으면 로그인된 기기에서 주소만 고쳐 비밀번호를 바꿀 수
 * 있다. 이 이벤트는 Supabase가 링크의 토큰을 서버에 확인시킨 뒤에만 나온다.
 */
let passwordRecovery = false;
const recoveryListeners = new Set<(active: boolean) => void>();

function setPasswordRecovery(next: boolean): void {
  if (passwordRecovery === next) return;
  passwordRecovery = next;
  for (const listener of recoveryListeners) listener(next);
}

/** 지금 화면이 재설정 링크가 세운 세션 위에 있는지. */
export function isPasswordRecoveryActive(): boolean {
  return passwordRecovery;
}

/** 비밀번호를 실제로 바꾼 뒤에 끈다 — 링크 한 번에 변경 한 번이다. */
export function clearPasswordRecovery(): void {
  setPasswordRecovery(false);
}

export function onPasswordRecoveryChange(listener: (active: boolean) => void): () => void {
  recoveryListeners.add(listener);
  return () => {
    recoveryListeners.delete(listener);
  };
}

/*
 * auth-js에는 타임아웃이 없다(TP-240).
 *
 * 설치본 2.112.3의 `lib/fetch.js`에 AbortController·AbortSignal·setTimeout이 한 건도
 * 없다. 그래서 로그인·회원가입·비밀번호 변경·getSession()의 토큰 갱신이 응답을 못
 * 받으면 영영 매달리고, 그 위 화면의 버튼은 `finally`가 안 돌아 잠긴 채로 남는다.
 * 요청 하나하나를 감싸는 대신 클라이언트가 쓰는 fetch를 여기서 한 번 갈아 끼운다.
 *
 * **세션을 지우지는 않는다.** abort는 auth-js에서 네트워크 오류로 취급돼
 * AuthRetryableFetchError가 되고, `_callRefreshToken`은 그 오류에서 `_removeSession()`
 * 분기를 건너뛴다. 즉 여기서 끊겨도 로그인이 풀리지 않는다.
 */
const AUTH_FETCH_TIMEOUT_MS = 15_000;

function fetchWithTimeout(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AUTH_FETCH_TIMEOUT_MS);

  /* auth-js가 자기 신호를 주는 경우(예: signOut)를 덮어쓰지 않는다. */
  const external = init?.signal;
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", () => controller.abort(), { once: true });
  }

  return fetch(input, { ...init, signal: controller.signal }).finally(() => {
    clearTimeout(timer);
  });
}

export function getSupabaseClient(): SupabaseClient {
  if (cached) return cached;

  const url = import.meta.env.VITE_SUPABASE_URL;
  const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

  const missing: string[] = [];
  if (!url) missing.push("VITE_SUPABASE_URL");
  if (!publishableKey) missing.push("VITE_SUPABASE_PUBLISHABLE_KEY");
  if (missing.length > 0) throw new SupabaseConfigError(missing);

  cached = createClient(url, publishableKey, {
    auth: {
      // 게스트 신원은 기기에 남아야 나중에 계정으로 승계할 수 있다(D-062 3절).
      // 대화 상태(sessionStorage)와 달리 탭을 닫아도 유지된다.
      persistSession: true,
      autoRefreshToken: true,
      // 메일 링크(#access_token=…&type=recovery|signup)를 읽어 세션을 세운다.
      // 끄면 비밀번호 재설정 링크가 아무 일도 하지 않는다 — 세션이 서지 않아
      // /reset-password/new가 항상 "만료된 링크"로 보인다.
      detectSessionInUrl: true,
    },
    global: { fetch: fetchWithTimeout },
  });

  /* createClient 바로 뒤, 같은 tick에 붙인다. 초기화는 비동기라 이 시점에는 아직
     끝나지 않았고, 따라서 PASSWORD_RECOVERY를 놓치지 않는다. */
  cached.auth.onAuthStateChange((event) => {
    if (event === "PASSWORD_RECOVERY") setPasswordRecovery(true);
    else if (event === "SIGNED_OUT") setPasswordRecovery(false);
  });

  return cached;
}

/* 테스트가 환경변수를 바꿔 끼울 수 있게 캐시를 비운다. 재설정 신호도 같이 끈다 —
   테스트 하나가 켠 값이 다음 테스트로 새면 안 된다. */
export function resetSupabaseClient(): void {
  cached = null;
  passwordRecovery = false;
  linkErrorSnapshot = undefined;
}

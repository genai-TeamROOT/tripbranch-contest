/*
 * 역할: 테스트에서 Supabase auth를 대신하는 인메모리 가짜 클라이언트.
 * 입력: setMockSession/setMockSignInError로 지정한 세션·오류 상태.
 * 출력: createClient 대체 함수와 상태 제어 헬퍼.
 * 호출 시점: src/test/setup.ts가 @supabase/supabase-js를 이 모듈로 mock할 때 로드된다.
 * TODO: 소셜 승계(linkIdentity)가 들어오면 여기에 추가한다. 이메일 승계는 updateUser로 되어 있다.
 */

import type { Session, SupabaseClient } from "@supabase/supabase-js";

/* 기본값을 "게스트 세션이 이미 있는 상태"로 둔다. 로그인 관문이 생기기 전에
   작성된 기존 통합 테스트가 그대로 홈에서 시작할 수 있어야 하기 때문이다. */
export const GUEST_SESSION = {
  access_token: "test_access_token",
  refresh_token: "test_refresh_token",
  expires_in: 3600,
  token_type: "bearer",
  user: {
    id: "00000000-0000-0000-0000-000000000001",
    is_anonymous: true,
    aud: "authenticated",
    app_metadata: {},
    user_metadata: {},
    created_at: "2026-08-19T00:00:00.000Z",
  },
} as unknown as Session;

type AuthListener = (event: string, session: Session | null) => void;

let currentSession: Session | null = GUEST_SESSION;
let signInError: string | null = null;
let listeners: AuthListener[] = [];

/* 이메일 인증 호출을 기록한다. 이메일 확인이 켜져 있어 signUp은 세션을 만들지
   않으므로, 세션 상태만 봐서는 "가입 요청이 나갔는지"를 알 수 없다. */
interface AuthCall {
  method: "signUp" | "signInWithPassword" | "resetPasswordForEmail" | "updateUser";
  email: string;
  password?: string;
  name?: string;
  redirectTo?: string;
  /* 호출 시점의 uid. 게스트 승계가 uid를 지키는지 보려면 이 값이 있어야 한다 —
     승계 전후를 세션만 비교하면 "새 계정이 만들어졌다"와 구분되지 않는다. */
  userId?: string;
}
let authCalls: AuthCall[] = [];
let emailAuthError: { message: string; code?: string } | null = null;

/* 저장된 세션 확인이 영영 안 끝나는 상황. getSession()은 fetch가 아니라 브라우저
   lock에서도 매달릴 수 있어서, 클라이언트에 끼운 fetch 타임아웃으로는 안 덮인다.
   resetSupabaseMock()이 매 테스트 앞에서 끈다. */
let getSessionHangs = false;

export function hangMockGetSession(): void {
  getSessionHangs = true;
}

export function setMockSession(session: Session | null): void {
  currentSession = session;
  listeners.forEach((listener) => listener("SIGNED_IN", session));
}

/*
 * auth 이벤트를 손으로 쏜다. 재설정 링크가 도착하는 상황은 세션만 세워서는 흉내
 * 낼 수 없다 — "세션이 있다"와 "재설정 링크로 왔다"를 구분하는 것이 이 화면의
 * 보호 장치라서, 테스트도 그 둘을 따로 만들 수 있어야 한다.
 */
export function emitMockAuthEvent(event: string, session: Session | null = currentSession): void {
  listeners.forEach((listener) => listener(event, session));
}

export function setMockSignInError(message: string | null): void {
  signInError = message;
}

/** 다음 이메일 인증 호출이 낼 오류. authErrors의 code 매핑을 태우려면 code를 준다. */
export function setMockEmailAuthError(error: { message: string; code?: string } | null): void {
  emailAuthError = error;
}

/** 지금까지 나간 이메일 인증 호출. resetSupabaseMock()이 비운다. */
export function emailAuthCalls(): readonly AuthCall[] {
  return authCalls;
}

export function resetSupabaseMock(): void {
  currentSession = GUEST_SESSION;
  signInError = null;
  listeners = [];
  authCalls = [];
  emailAuthError = null;
  getSessionHangs = false;
}

/* 이메일 확인이 켜져 있을 때 실제 Supabase가 돌려주는 모양이다 — user는 있고
   session은 null이다. 이걸 틀리게 흉내 내면 "가입하자마자 로그인됨"이라는
   존재하지 않는 흐름을 테스트가 통과시킨다. */
const CONFIRMATION_PENDING = { user: { id: "pending", identities: [] }, session: null };

export function createMockSupabaseClient(): SupabaseClient {
  return {
    auth: {
      getSession: async () => {
        if (getSessionHangs) return new Promise<never>(() => {});
        return { data: { session: currentSession }, error: null };
      },
      onAuthStateChange: (listener: AuthListener) => {
        listeners.push(listener);
        return {
          data: {
            subscription: {
              unsubscribe: () => {
                listeners = listeners.filter((entry) => entry !== listener);
              },
            },
          },
        };
      },
      signOut: async () => {
        currentSession = null;
        listeners.forEach((listener) => listener("SIGNED_OUT", null));
        return { error: null };
      },
      signUp: async (input: {
        email: string;
        password: string;
        options?: { data?: { name?: string }; emailRedirectTo?: string };
      }) => {
        authCalls.push({
          method: "signUp",
          email: input.email,
          password: input.password,
          name: input.options?.data?.name,
          redirectTo: input.options?.emailRedirectTo,
        });
        if (emailAuthError) return { data: { user: null, session: null }, error: emailAuthError };
        return { data: CONFIRMATION_PENDING, error: null };
      },
      signInWithPassword: async (input: { email: string; password: string }) => {
        authCalls.push({
          method: "signInWithPassword",
          email: input.email,
          password: input.password,
        });
        if (emailAuthError) return { data: { user: null, session: null }, error: emailAuthError };
        const account = {
          ...GUEST_SESSION,
          user: { ...GUEST_SESSION.user, is_anonymous: false, email: input.email },
        } as Session;
        currentSession = account;
        listeners.forEach((listener) => listener("SIGNED_IN", account));
        return { data: { session: account, user: account.user }, error: null };
      },
      /*
       * 세 곳이 쓴다 — 비밀번호 재설정(password만), 게스트 승계(email·password·data),
       * 닉네임 변경(data만). 승계 쪽은 이메일 확인이 켜져 있어 **여기서
       * is_anonymous가 바로 false가 되지 않는다.** 링크를 눌러야 바뀐다. 그걸
       * 흉내 내지 않고 세션을 바로 승격시키면 "가입하자마자 계정이 됨"이라는
       * 존재하지 않는 흐름을 테스트가 통과시킨다.
       *
       * **email·password 없이 data만 오면 그 자리에서 바로 반영한다** — 실제
       * Supabase도 user_metadata 변경은 확인 절차 없이 즉시 적용한다(이메일·
       * 전화번호 변경만 확인이 필요하다). 여기서 세션을 안 바꾸면 닉네임을
       * 바꿔도 화면에 있는 이름이 그대로 남는 거짓 통과가 나온다.
       */
      updateUser: async (
        input: { email?: string; password?: string; data?: { name?: string } },
        options?: { emailRedirectTo?: string },
      ) => {
        authCalls.push({
          method: "updateUser",
          email: input.email ?? currentSession?.user?.email ?? "",
          password: input.password,
          name: input.data?.name,
          redirectTo: options?.emailRedirectTo,
          userId: currentSession?.user?.id,
        });
        if (emailAuthError) return { data: { user: null }, error: emailAuthError };
        if (input.data && !input.email && !input.password && currentSession) {
          currentSession = {
            ...currentSession,
            user: {
              ...currentSession.user,
              user_metadata: { ...currentSession.user.user_metadata, ...input.data },
            },
          } as Session;
          listeners.forEach((listener) => listener("USER_UPDATED", currentSession));
        }
        return { data: { user: currentSession?.user ?? null }, error: null };
      },
      resetPasswordForEmail: async (email: string, options?: { redirectTo?: string }) => {
        authCalls.push({ method: "resetPasswordForEmail", email, redirectTo: options?.redirectTo });
        if (emailAuthError) return { data: null, error: emailAuthError };
        return { data: {}, error: null };
      },
      signInAnonymously: async () => {
        if (signInError) {
          return { data: { session: null, user: null }, error: { message: signInError } };
        }
        currentSession = GUEST_SESSION;
        listeners.forEach((listener) => listener("SIGNED_IN", currentSession));
        return { data: { session: currentSession, user: GUEST_SESSION.user }, error: null };
      },
    },
  } as unknown as SupabaseClient;
}

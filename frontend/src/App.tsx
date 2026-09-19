/*
 * 역할: TripBranch 프론트엔드의 관문/홈/채팅 라우팅을 구성한다.
 * 입력: 브라우저 URL, AuthProvider가 확보한 신원.
 * 출력: LoginPage, 신원이 있어야 들어갈 수 있는 화면들(AppShell로 감싼 홈·채팅·
 *   취향 설정·위치 설정·일정), 이전 URL 호환 리다이렉트.
 * 호출 시점: main.tsx가 앱을 렌더링할 때 최상위 컴포넌트로 호출된다.
 * TODO: 실제 세션 라우트가 생기면 /chat/:sessionId를 별도 보호 라우트로 추가한다.
 *
 * 셸 안 화면 표(홈·채팅·취향 설정·위치 설정·일정)는 AppShell이 감싸는
 * AppRoutes에 있다.
 */

import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireUser } from "./auth/RequireUser";
import { TripProvider } from "./state/TripContext";
import { FeatureFlagsProvider } from "./state/FeatureFlagsContext";
import { AppShell } from "./components/layout/AppShell";
import { PageTransition } from "./components/layout/PageTransition";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { SplashScreen } from "./components/SplashScreen";

/*
 * 개발자 화면과 인증 화면은 따로 받아온다.
 *
 * - 개발자 화면(/dev-chat·/dev-ops)은 components/dev/ 17개를 끌고 오는데, 소스만
 *   227KB로 앱에서 가장 큰 덩어리다. 사용자는 이 경로에 오지 않으므로 첫 화면에
 *   실릴 이유가 없다.
 * - 인증 화면 4종은 로그인한 사용자가 다시 볼 일이 없다.
 *
 * AppShell(홈·채팅·취향·위치·일정)은 그대로 즉시 로딩한다 — 첫 화면이고,
 * 바텀시트로 뜨는 화면들이라 나중에 받아오면 시트가 빈 채로 올라온다.
 */
const LoginPage = lazy(() => import("./pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const SignupPage = lazy(() =>
  import("./pages/SignupPage").then((m) => ({ default: m.SignupPage })),
);
const ResetPasswordPage = lazy(() =>
  import("./pages/ResetPasswordPage").then((m) => ({ default: m.ResetPasswordPage })),
);
const NewPasswordPage = lazy(() =>
  import("./pages/NewPasswordPage").then((m) => ({ default: m.NewPasswordPage })),
);
const DeveloperChatPage = lazy(() =>
  import("./pages/DeveloperChatPage").then((m) => ({ default: m.DeveloperChatPage })),
);
const DeveloperOpsPage = lazy(() =>
  import("./pages/DeveloperOpsPage").then((m) => ({ default: m.DeveloperOpsPage })),
);

/* RequireUser의 로딩 표시와 같은 문구를 쓴다 — 화면 전환 중 문구가 바뀌지 않게.
   AuthScreen과 같은 `bg-bg`를 깐다 — 이 층에 배경이 없으면 청크를 받는 동안
   body의 `--color-chip`이 비쳐서 화면이 한 번 번쩍인다. 높이는 다른 전체 화면과
   같은 dvh로 맞춘다(100vh는 모바일 주소창 높이만큼 어긋난다). */
function RouteFallback() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg">
      <p className="text-sm text-gray-600 dark:text-gray-400">불러오는 중이에요…</p>
    </div>
  );
}

/*
 * 인증 화면 한 겹을 더 감싸 **불투명한 배경**을 깐다(2026-09-20).
 *
 * PageTransition은 나가는 화면을 즉시 지우고 들어오는 화면을 opacity 0에서
 * 올린다. 인증 화면(AuthLayout)은 `bg-bg`(흰색)인데 그 뒤 body는
 * `--color-chip`(#EEF1F8)이라, 페이드가 도는 220ms 동안 흰색 -> 회청색 -> 흰색으로
 * 번쩍였다(로그인 <-> 회원가입 <-> 비밀번호 찾기에서 재현). 배경은 이 바깥
 * 층이 들고 있어야 한다 — 애니메이션이 걸린 요소에 배경을 주면 그 배경까지
 * 같이 투명해져서 소용이 없다.
 */
function AuthScreen({ path, children }: { path: string; children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-bg">
      <PageTransition pathKey={path}>{children}</PageTransition>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      {/* 기능 스위치는 로그인과 무관한 서버 설정이라 관문(RequireUser)보다 바깥에서
          한 번만 받는다. 라우트 표(/preferences)와 사이드바가 함께 읽는다. */}
      <FeatureFlagsProvider>
        <TripProvider>
          <BrowserRouter>
            {/* 청크를 못 받으면 흰 화면 대신 안내가 뜬다 — Suspense 바깥이어야 잡는다. */}
            <RouteErrorBoundary>
              <Suspense fallback={<RouteFallback />}>
                <Routes>
                  {/*
                   * 인증 화면들은 서로 오가는 흐름이라(로그인 -> 회원가입 ->
                   * 되돌아오기) 전환이 특히 눈에 띈다. pathKey를 경로 문자열로
                   * 직접 주는 이유는, 같은 PageTransition 자리에 다른 화면이
                   * 들어오면 React가 래퍼를 재사용해 애니메이션이 다시 재생되지
                   * 않기 때문이다.
                   *
                   * 셸 밖이라 fullHeight는 켜지 않는다 — 각 화면이 min-h-dvh로
                   * 스스로 높이를 잡는다. 감싸는 AuthScreen이 그 전환 동안
                   * 비쳐 보일 배경까지 들고 있다(위 주석).
                   */}
                  <Route
                    path="/login"
                    element={
                      <AuthScreen path="/login">
                        <LoginPage />
                      </AuthScreen>
                    }
                  />
                  {/* 회원가입·아이디찾기·비밀번호찾기는 아직 백엔드가 없는 UI 목업이다(D-062 Phase 5). */}
                  <Route
                    path="/signup"
                    element={
                      <AuthScreen path="/signup">
                        <SignupPage />
                      </AuthScreen>
                    }
                  />
                  <Route
                    path="/reset-password"
                    element={
                      <AuthScreen path="/reset-password">
                        <ResetPasswordPage />
                      </AuthScreen>
                    }
                  />
                  {/* 재설정 메일의 링크가 돌아오는 자리. Supabase 대시보드의
                    Redirect URLs에 이 주소가 있어야 실제로 여기로 온다. */}
                  <Route
                    path="/reset-password/new"
                    element={
                      <AuthScreen path="/reset-password/new">
                        <NewPasswordPage />
                      </AuthScreen>
                    }
                  />
                  {/* 로컬 개발 서버에서만 이 라우트를 등록한다. RequireUser는 로그인
                    여부만 보지 신원 종류는 안 보므로, 로그인한 사용자라면 누구나
                    URL을 직접 쳐서 들어올 수 있었다 — 홈 화면의 진입 칩만 숨기는
                    것으로는 막히지 않는다. 백엔드도 이 화면이 쓰는 감사 API
                    (api/dev.ts의 fetchExchanges 등)를 APP_ENV=local일 때만
                    등록해 배포 환경에서 404를 돌려주고 있어 같은 원칙이다.
                    Routes의 자식은 React가 순회하므로 조건부 렌더링이 그대로
                    동작한다(react-router v6 공식 패턴). */}
                  {import.meta.env.DEV && (
                    <Route
                      path="/dev-chat"
                      element={
                        <RequireUser>
                          <DeveloperChatPage />
                        </RequireUser>
                      }
                    />
                  )}
                  {/* 운영 점검 화면도 개발 빌드에서만 낸다.
                    사용자 신원과 무관한 내부 도구라 관문(RequireUser) 밖에 두는데,
                    그 상태로 배포하면 URL을 아는 누구나 들어올 수 있다. 실제로
                    2026-09-19 공개 배포 점검에서 /dev-ops가 그대로 열려 있었다 —
                    백엔드가 APP_ENV=local일 때만 감사 API를 등록해서 데이터는
                    안 나왔지만, "TripBranch Ops · 호출량 · DB 상태 · 동기화"라는
                    내부 화면이 깨진 채로 노출됐다.
                    DEV 가드를 걸면 라우터에서 경로가 사라져 /dev-ops로 들어와도
                    catch-all로 떨어진다(빌드 산출물에 "/dev-ops" 문자열이 남지
                    않는 것으로 확인). 다만 페이지 청크 파일(102KB) 자체는 계속
                    생성된다 — Vite가 동적 import 표현식을 보고 청크를 내기
                    때문이다. 아무도 로드하지 않으므로 사용자는 받지 않지만,
                    "번들에서 통째로 사라진다"고 오해하지 않도록 적어둔다. */}
                  {import.meta.env.DEV && (
                    <Route path="/dev-ops" element={<DeveloperOpsPage />} />
                  )}
                  <Route path="/confirm" element={<Navigate to="/chat" replace />} />
                  <Route path="/results" element={<Navigate to="/chat" replace />} />
                  {/*
                   * 신원이 필요한 화면 전부(/, /chat, /preferences, /location, /schedule,
                   * 그리고 알 수 없는 경로)를 여기서 한 번에 받는다. AppShell 안의 AppRoutes가
                   * 실제 화면을 고른다 — RequireUser를 화면마다 반복하지 않기 위해서다.
                   */}
                  <Route
                    path="*"
                    element={
                      <RequireUser>
                        <AppShell />
                      </RequireUser>
                    }
                  />
                </Routes>
              </Suspense>
            </RouteErrorBoundary>
          </BrowserRouter>
        </TripProvider>
      </FeatureFlagsProvider>
      {/*
       * 라우터 **밖·뒤**에 둔다. 밖인 이유는 특정 화면의 것이 아니라 앱이 뜨는
       * 순간을 덮는 층이기 때문이고, 뒤인 이유는 DOM 순서만으로도 위에 오게
       * 해서 z-index 하나에만 기대지 않기 위해서다.
       */}
      <SplashScreen />
    </AuthProvider>
  );
}

export default App;

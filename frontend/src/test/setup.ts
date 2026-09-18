/*
 * 역할: Vitest에서 React Testing Library matcher와 Supabase auth mock을 전역 등록한다.
 * 입력: Vitest setupFiles 실행 컨텍스트.
 * 출력: jest-dom matcher 사용 가능 상태, 게스트 세션이 있는 기본 auth 환경.
 * 호출 시점: vitest가 테스트 파일을 실행하기 전에 자동으로 로드한다.
 * TODO: 공통 fetch mock이 많아지면 여기 또는 별도 test utils로 옮긴다.
 */

import { beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { resetSupabaseMock } from "./supabaseMock";

/* 실제 Supabase 네트워크 호출 없이 인증 상태만 흉내 낸다. 기본값은 게스트 세션이
   있는 상태라, 로그인 관문(D-062)이 생기기 전에 작성된 테스트도 그대로 돈다.
   미인증 상태를 검증하는 테스트는 setMockSession(null)로 개별 지정한다. */
vi.mock("@supabase/supabase-js", async () => {
  const mock = await import("./supabaseMock");
  return { createClient: mock.createMockSupabaseClient };
});

/* getSupabaseClient()는 설정이 없으면 예외를 던진다(조용한 비로그인 진행 금지).
   테스트에서는 값이 있는 것으로 두고, 없는 경우는 전용 테스트에서 따로 만든다. */
vi.stubEnv("VITE_SUPABASE_URL", "https://test.supabase.co");
vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");

/* jsdom은 matchMedia를 구현하지 않는다(useIsDesktopSidebar가 쓴다). 항상
   "일치하지 않음"으로 응답해 기본적으로 모바일 레이아웃 기준으로 렌더링되게
   한다 — 데스크톱 분기를 검증하는 테스트는 개별적으로 matches:true를 덮어쓴다. */
window.matchMedia ??= (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;

/* jsdom은 ResizeObserver도 구현하지 않는다(useAutoScrollToBottom이 쓴다).
   실제 리사이즈 감지는 필요 없고, 생성자가 없어서 렌더가 죽는 것만 막으면
   된다 — 콜백은 아무 때도 부르지 않는 빈 구현이면 충분하다. */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

beforeEach(() => {
  resetSupabaseMock();
  /* 취향 동기화 캐시(state/preferenceSync.ts)는 여기서 비우지 않는다 — 이 파일이
     그 모듈을 import하면 테스트 파일의 vi.mock보다 먼저 api/trip이 묶여, 취향
     API를 갈아끼울 수 없게 된다. 필요한 테스트 파일이 직접 resetPreferenceSync()를
     부른다. */
});

/*
 * 역할: Vite, React, Tailwind, Vitest 실행 환경을 설정한다.
 * 입력: 개발 서버/빌드/테스트 명령과 환경 설정.
 * 출력: 프론트엔드 번들링, dev proxy, 테스트 환경 구성.
 * 호출 시점: npm scripts가 vite 또는 vitest를 실행할 때 로드된다.
 * TODO: 배포 환경별 API base URL과 프록시 정책이 생기면 mode별로 분기한다.
 */

/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    /*
     * PWA — 홈 화면에 설치하고 정적 자산은 오프라인에서도 뜨게 한다.
     *
     * **API 응답은 캐시하지 않는다.** 추천·위치·일정은 지금 시각과 지금 위치가
     * 근거라, 캐시된 답이 되살아나면 사용자는 그것이 낡은 값인지 알 방법이 없다.
     * 인증 토큰이 붙는 요청이기도 해서 다음 사람이 앞사람의 응답을 받는 사고로
     * 이어진다. precache 대상은 빌드 산출물뿐이고, navigateFallback도 /api를
     * 비켜 간다.
     *
     * registerType은 autoUpdate다. prompt로 두면 "새 버전이 있어요" 배너를
     * 만들어야 하는데, 지금 이 앱에는 사용자가 버전을 고를 이유가 없다 —
     * 서비스워커가 낡은 화면을 붙잡고 있는 사고가 그보다 훨씬 비싸다.
     */
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "apple-touch-icon-180x180.png"],
      manifest: {
        name: "TripBranch",
        short_name: "TripBranch",
        description: "지금 시각과 위치에 맞는 여행지를 찾아 하루 동선을 짜 주는 AI 여행 도우미",
        lang: "ko",
        start_url: "/",
        scope: "/",
        display: "standalone",
        /* 상태바 색이다. 앱 상단이 흰 바탕이라 여기만 브랜드색을 쓰면 홈 화면에서
           띄웠을 때 머리 위에 파란 띠가 하나 더 생긴 것처럼 보인다. */
        theme_color: "#ffffff",
        /* 설치 후 첫 프레임(스플래시) 바탕. 앱의 진입 스플래시(.tb-splash)가
           시작하는 색과 맞춰, 두 스플래시가 이어 보이게 한다(index.css). */
        background_color: "#e6edfd",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          {
            src: "maskable-icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        /* SPA라 어떤 경로로 들어와도 index.html이 답이다. 단 /api는 백엔드 몫이라
           여기서 가로채면 오프라인일 때 JSON 자리에 HTML이 돌아간다. */
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/api\//],
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        /* 개발자 화면(/dev/*)은 미리 받아 둘 이유가 없다 — 일반 사용자에게는
           열 길조차 없는데 설치할 때마다 157 kB를 함께 내려받는다. 빼도 온라인에서
           평소처럼 열린다(그때 네트워크로 받는다). */
        globIgnores: ["**/Developer*.js", "**/dev-*.js"],
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        /*
         * 자주 바뀌는 앱 코드와 거의 안 바뀌는 라이브러리를 갈라 둔다.
         *
         * 이유는 두 가지다. ① 한 덩어리로 두면 앱 코드 한 줄만 고쳐도 사용자가
         * 라이브러리 전부를 다시 받는다. ② 통짜 청크가 Vite의 500 kB 경고를
         * 넘고 있었다.
         *
         * 실측(2026-09-02): react 225 kB · supabase 208 kB · framer-motion 125 kB ·
         * 앱 코드 187 kB · lucide 16 kB. supabase가 인증에만 쓰이는데도 큰 편인데,
         * AuthProvider가 앱을 통째로 감싸 부팅 경로에 있어 지금은 나중에 받아올 수 없다.
         *
         * 총 바이트는 줄지 않는다 — 줄어드는 것은 "다시 받는 양"이다.
         */
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/@supabase/")) return "vendor-supabase";
          if (id.includes("/framer-motion/")) return "vendor-motion";
          if (id.includes("/react-dom/") || id.includes("/react-router") || id.includes("/react/"))
            return "vendor-react";
          return undefined;
        },
      },
    },
  },
  server: {
    // 모바일 테스트용 cloudflared 터널(https://*.trycloudflare.com)에서 접속할 때
    // Vite의 호스트 검사에 막히지 않게 허용한다. 로컬 개발 서버 한정 설정이다.
    allowedHosts: [".trycloudflare.com"],
    // PORT가 지정되면 그대로 따른다 — 안 지키면 5173이 이미 쓰이고 있을 때 Vite가
    // 조용히 다른 포트로 넘어가버려서, 이 포트를 기대하는 프리뷰 도구와 어긋난다.
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    strictPort: Boolean(process.env.PORT),
    proxy: {
      "/api": {
        // 백엔드 주소도 PORT와 같은 이유로 환경변수를 따른다 — 워크트리에서
        // 다른 포트로 띄운 백엔드를 보게 하려면 이 값이 필요하다. 여기가 고정이면
        // 프론트만 포트를 옮겨도 요청은 8000의 다른 백엔드로 간다.
        target: process.env.API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  /*
   * 배포 빌드를 그대로 폰에서 확인할 때 쓰는 서버다(`npm run preview`).
   *
   * 개발 서버와 달리 import.meta.env.DEV가 false라 개발자용 버튼과 /dev-chat이
   * 산출물에서 아예 빠진다 — 팀원·외부에게 보여줄 때 이쪽으로 띄우는 이유다.
   * 다만 preview는 server.proxy를 쓰지 않으므로, /api를 백엔드로 넘기는 설정을
   * 여기에 한 번 더 둔다. 없으면 화면은 뜨는데 요청이 전부 404로 떨어진다.
   */
  preview: {
    port: process.env.PREVIEW_PORT ? Number(process.env.PREVIEW_PORT) : 4173,
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },

  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});

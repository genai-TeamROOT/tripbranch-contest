/*
 * 역할: React 애플리케이션을 브라우저 DOM에 마운트하는 진입점.
 * 입력: index.html의 #root 엘리먼트.
 * 출력: TripBranch React 앱 렌더링 결과.
 * 호출 시점: Vite가 번들을 로드할 때 최초 1회 실행된다.
 * TODO: 전역 모니터링이나 feature flag provider가 필요하면 여기에서 감싼다.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

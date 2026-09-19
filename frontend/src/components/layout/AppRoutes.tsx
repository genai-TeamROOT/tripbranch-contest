/*
 * 역할: 신원이 필요한 화면(홈·채팅·취향 설정·위치 설정·일정)의 라우트 표를 정의한다.
 * 입력: 없음 — 지금 URL을 그대로 쓴다.
 * 출력: 현재 위치에 맞는 페이지.
 * 호출 시점: AppShell이 셸 본문 안에서 한 번 호출한다.
 *
 * 예전에는 매칭에 쓸 location을 밖에서 받았다 — 같은 라우트 표를 배경 화면과
 * 그 위에 쌓인 시트에 각각 한 번씩 쓰기 위해서였다. 시트를 걷어낸 뒤로는
 * 부르는 곳이 한 곳뿐이라 <Routes>가 현재 위치를 직접 읽게 뒀다(2026-09-07).
 */

import { Navigate, Route, Routes } from "react-router-dom";
import { ChatPage } from "../../pages/ChatPage";
import { HomePage } from "../../pages/HomePage";
import { LocationPage } from "../../pages/LocationPage";
import { PreferencesPage } from "../../pages/PreferencesPage";
import { SchedulePage } from "../../pages/SchedulePage";
import { useFeatureFlagsLoaded, useTasteEnabled } from "../../state/FeatureFlagsContext";

export function AppRoutes() {
  const tasteEnabled = useTasteEnabled();
  const flagsLoaded = useFeatureFlagsLoaded();
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/chat" element={<ChatPage />} />
      {/* 취향이 꺼진 서버에서는 주소를 직접 쳐도 홈으로 보낸다. 메뉴만 숨기면
          북마크·뒤로 가기로 들어올 수 있다. 저장된 취향은 건드리지 않는다 —
          다시 켜면 그대로 보인다. 스위치를 받기 전에는 화면을 비워 둔다 —
          그때 돌려보내면 켜진 서버에서도 이 화면을 새로고침할 때마다 홈으로
          튕긴다(FeatureFlagsContext). */}
      <Route
        path="/preferences"
        element={
          tasteEnabled ? <PreferencesPage /> : flagsLoaded ? <Navigate to="/" replace /> : null
        }
      />
      <Route path="/location" element={<LocationPage />} />
      <Route path="/schedule" element={<SchedulePage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

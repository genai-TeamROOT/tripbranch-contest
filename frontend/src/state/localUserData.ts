/*
 * 역할: 로그아웃할 때 이 기기에 남은 사용자 데이터를 지운다.
 * 입력: 없음.
 * 출력: 없음(저장소 side effect).
 * 호출 시점: 로그아웃 처리(SideDrawerContent, AuthStatusBadge).
 *
 * **지우지 않으면 다음 사람이 이어받는다.** 로그아웃은 대화(TripState)만 비우고
 * 있었기 때문에, 같은 브라우저에서 다른 아이디로 들어오면 앞사람의 취향과
 * 즐겨찾기가 그대로 보였다. 취향은 한술 더 떠서, 새 계정이 한 번도 저장한 적이
 * 없으면 이 기기의 값을 그 계정으로 올린다(preferenceSync).
 *
 * **localStorage.clear()를 쓰지 않는다.** 같은 저장소에 Supabase 인증 키(sb-…)가
 * 들어 있어 통째로 비우면 로그인 흐름이 깨진다. 저장소별 모듈이 자기 키만 지운다.
 *
 * **저장소만 지우면 부족하다.** 취향(preferenceSync)·지난 대화 목록(chatSessions)·
 * 저장한 일정(savedSchedules)·즐겨찾기(favoritesSync)는 "페이지 로드당 한 번만 받아온다"고 모듈 변수에
 * 담아 두는데, 이 변수는 로그아웃을 넘어 살아남는다. 저장소를 비워도 다음 사람
 * 화면에 앞사람의 취향·대화 제목·일정이 그대로 뜨고, 취향은 그 상태로 새 계정에
 * 저장될 수도 있다. 캐시도 함께 비운다.
 *
 * 계정에서 받아오는 목록을 새로 만들면 여기에 그 캐시 비우기를 더해야 한다.
 *
 * 즐겨찾기도 이제 계정에 저장되므로(favoritesSync), 취향과 마찬가지로 같은
 * 계정으로 다시 로그인하면 되받아온다 — 이 기기에서 지우는 것은 사본이다.
 */

import { resetChatSessionsCache } from "./chatSessions";
import { resetFavoritesSync } from "./favoritesSync";
import { clearRecentSearches } from "./recentSearchesStorage";
import { resetSavedSchedulesCache } from "./savedSchedules";
import { clearPreferences } from "./preferenceStorage";
import { resetPreferenceSync } from "./preferenceSync";
import { clearLocationSettings } from "./locationSettings";
import { clearScheduleProgress } from "./scheduleProgress";
import { clearFavorites } from "./sidebarStorage";
import { clearState } from "./storage";

export function clearLocalUserData(): void {
  /* 대화·상태. RESET도 같은 일을 하지만, 이 함수만 불러도 남는 것이 없어야 한다. */
  clearState();
  clearPreferences();
  clearFavorites();
  clearLocationSettings();
  clearRecentSearches();
  /* 일정에서 "다녀왔어요"로 체크한 것(state/scheduleProgress.ts). 일정마다 키가
     하나씩 쌓이므로 접두어로 훑어 지운다. */
  clearScheduleProgress();
  /* 화면이 이미 받아 둔 사본. 저장소를 지워도 이게 남아 있으면 다음 사람이 본다. */
  resetPreferenceSync();
  resetChatSessionsCache();
  resetSavedSchedulesCache();
  resetFavoritesSync();
}

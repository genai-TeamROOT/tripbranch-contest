/*
 * 역할: 로그아웃이 이 기기의 사용자 데이터를 남기지 않는지 검증한다.
 * 입력: 저장해 둔 대화·취향·즐겨찾기·검색 위치, 그리고 Supabase 인증 키를 흉내낸 값.
 * 출력: 우리 키만 비고 인증 키는 남는지 확인.
 * 호출 시점: vitest 실행 시.
 */

import { beforeEach, expect, test } from "vitest";

import { clearLocalUserData } from "./localUserData";
import { loadPreferences, savePreferences } from "./preferenceStorage";
import { syncPreferences } from "./preferenceSync";
import { loadRecentSearches, rememberRecentSearch } from "./recentSearchesStorage";
import { loadLocationSettings, setLocationCenter } from "./locationSettings";
import { loadVisitedIndices, saveVisitedIndices } from "./scheduleProgress";
import { loadFavorites, saveFavorites } from "./sidebarStorage";
import { clearState } from "./storage";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

function seedEverything() {
  savePreferences([{ label: "조용한 곳", source: "preference", codes: ["quiet"] }]);
  saveFavorites([{ id: "fav-1", label: "회사 (역삼동)" }]);
  setLocationCenter("안국역");
  rememberRecentSearch("안국역");
  saveVisitedIndices("sched-1", [0, 2]);
  sessionStorage.setItem("tripbranch_state", JSON.stringify({ version: 6, state: {} }));
}

test("로그아웃하면 취향·즐겨찾기·검색 위치·대화가 모두 사라진다", () => {
  seedEverything();

  clearLocalUserData();

  expect(loadPreferences()).toEqual([]);
  expect(loadFavorites()).toEqual([]);
  expect(loadLocationSettings().center).toBeNull();
  expect(loadRecentSearches()).toEqual([]);
  expect(sessionStorage.getItem("tripbranch_state")).toBeNull();
});

/*
 * 일정 체크는 일정마다 키가 하나씩 생긴다(tripbranch_schedule_progress:<id>).
 * 이름이 고정된 다른 저장소와 달리 접두어로 훑어 지워야 해서 따로 잠근다 —
 * 안 지우면 다음 사람 화면에 앞사람 체크가 남고, 본 일정마다 키가 쌓인다.
 */
test("로그아웃하면 일정마다 쌓인 체크 기록도 남지 않는다", () => {
  saveVisitedIndices("sched-1", [0, 2]);
  saveVisitedIndices("sched-2", [1]);
  saveVisitedIndices("current", [0]);

  clearLocalUserData();

  expect(loadVisitedIndices("sched-1")).toEqual([]);
  expect(loadVisitedIndices("sched-2")).toEqual([]);
  expect(loadVisitedIndices("current")).toEqual([]);
  /* 접두어로 훑으므로 우리 키가 하나도 안 남아야 한다. */
  expect(
    Object.keys(localStorage).filter((key) => key.startsWith("tripbranch_schedule_progress:")),
  ).toEqual([]);
});

/*
 * 저장소를 지워도 화면이 이미 받아 둔 사본이 남으면 다음 사람이 그걸 본다.
 * 취향과 지난 대화 목록은 "페이지 로드당 한 번"만 받아오려고 모듈 변수에 담기는데,
 * 그 변수는 로그아웃을 넘어 살아남는다.
 */
test("화면이 받아 둔 취향 사본도 함께 비운다", async () => {
  const previousUser = [{ label: "조용한 곳", source: "preference", codes: ["quiet"] } as const];
  savePreferences(previousUser);
  /* 서버에 닿지 못하면 이 기기의 값으로 도는데, 그 결과가 캐시에 남는다. */
  expect(await syncPreferences()).toEqual(previousUser);

  clearLocalUserData();

  /* 캐시가 비었으니 다시 읽는다. 저장소도 비었으므로 앞사람 취향이 나오면 안 된다. */
  expect(await syncPreferences()).toEqual([]);
});

/*
 * 사이드바 "홈"과 로그아웃이 모두 TripState를 비우지만(clearState), 검색 위치는
 * 새 대화에도 남아야 한다 — 대화가 아니라 설정이라서다. 실제로 이 구분이 없어
 * "위치를 골라도 홈으로 가면 무시되는" 문제가 있었다.
 */
test("새 대화로 대화를 비워도 검색 위치는 남는다", () => {
  seedEverything();

  clearState();

  expect(sessionStorage.getItem("tripbranch_state")).toBeNull();
  expect(loadLocationSettings().center).toBe("안국역");
});

/*
 * Supabase 세션이 같은 localStorage에 들어 있다. localStorage.clear()로 지우면
 * 로그인 흐름이 깨지므로, 우리 키만 이름으로 지우는지 여기서 못 박는다.
 */
test("인증 키는 건드리지 않는다", () => {
  seedEverything();
  localStorage.setItem("sb-abcdefgh-auth-token", '{"access_token":"…"}');

  clearLocalUserData();

  expect(localStorage.getItem("sb-abcdefgh-auth-token")).not.toBeNull();
});

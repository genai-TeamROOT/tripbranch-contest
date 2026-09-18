/*
 * 역할: 위치 설정 화면에서 친 검색어를 최근 순서로 sessionStorage에 남긴다.
 * 입력: 사용자가 검색한 문자열.
 * 출력: 최근 검색어 목록(최신순).
 * 호출 시점: LocationPage가 검색을 보낼 때, 최근 목록을 그릴 때.
 *
 * **고른 장소가 아니라 친 검색어를 남긴다.** 검색은 했는데 마음에 드는 후보가
 * 없어 아무것도 안 고른 경우가 오히려 다시 찾게 되는 경우다 — 고른 것만 남기면
 * 그 검색어가 사라진다. 목록에서 누르면 그 검색어로 다시 검색한다.
 *
 * 검색 위치(searchCenterStorage)와 같은 sessionStorage에 둔다 — 수명이 같아야
 * 하고, 로그아웃 정리(localUserData)에도 함께 걸려야 한다.
 */

const RECENT_SEARCHES_KEY = "tb_recent_searches";

/* 다섯 개를 넘기면 화면에서 즐겨찾기·검색 결과를 밀어낸다. 최근이라는 말이
   무색해지기도 한다 — 스무 개짜리 목록은 이미 기록이지 최근이 아니다. */
const MAX_RECENT_SEARCHES = 5;

export function loadRecentSearches(): string[] {
  try {
    const raw = sessionStorage.getItem(RECENT_SEARCHES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
  } catch {
    /* 저장소가 막혀 있거나(시크릿 모드 등) 값이 깨졌어도 화면은 계속 동작해야 한다. */
    return [];
  }
}

/**
 * 방금 친 검색어를 목록 맨 앞에 올린다.
 *
 * 같은 검색어가 이미 있으면 그것을 빼고 앞에 다시 넣는다 — 같은 말을 두 번 찾으면
 * 줄이 두 개 생기는 것이 아니라 순서만 최신으로 바뀐다.
 */
export function rememberRecentSearch(query: string): string[] {
  const trimmed = query.trim();
  if (!trimmed) return loadRecentSearches();
  const next = [trimmed, ...loadRecentSearches().filter((item) => item !== trimmed)].slice(
    0,
    MAX_RECENT_SEARCHES,
  );
  try {
    sessionStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next));
  } catch {
    /* 저장하지 못해도 이번 화면의 목록은 그대로 보여준다. */
  }
  return next;
}

/**
 * 목록에서 검색어 하나를 뺀다.
 *
 * 전체를 지우는 clearRecentSearches와 따로 두는 이유는 **지우는 단위가 다르기**
 * 때문이다 — 이건 화면에서 줄 하나를 지우는 것이고, clear는 로그아웃 정리
 * (localUserData)가 통째로 비울 때 쓴다. 남는 것이 없으면 키까지 지워
 * clearRecentSearches를 부른 것과 같은 상태로 둔다.
 */
export function forgetRecentSearch(query: string): string[] {
  const next = loadRecentSearches().filter((item) => item !== query);
  try {
    if (next.length === 0) {
      sessionStorage.removeItem(RECENT_SEARCHES_KEY);
    } else {
      sessionStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next));
    }
  } catch {
    /* 저장소가 막혀 있어도 화면의 목록은 지워진 상태로 계속 보여준다. */
  }
  return next;
}

export function clearRecentSearches(): void {
  try {
    sessionStorage.removeItem(RECENT_SEARCHES_KEY);
  } catch {
    /* 위와 같다. */
  }
}

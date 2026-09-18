/*
 * 역할: 계정에 저장된 즐겨찾기와 이 기기의 localStorage를 맞춘다.
 * 입력: 서버 응답(GET /api/favorites)과 sidebarStorage의 로컬 값.
 * 출력: 화면이 그릴 즐겨찾기 목록.
 * 호출 시점: 즐겨찾기를 보여주는 화면이 마운트될 때 한 번, 담거나 지울 때.
 *
 * **preferenceSync와 같은 판단을 따른다.** 두 값 모두 세션이 아니라 사람에게
 * 붙고, 서버로 옮기면서 같은 함정을 만난다.
 *
 * **localStorage를 없애지 않고 거울로 남긴다.** 서버로만 옮기면 지금보다
 * 나빠지는 경우가 생기기 때문이다.
 *
 *   게스트로 즐겨찾기를 담음 → 게스트 uid로 서버에 저장
 *   → 회원가입 → uid가 바뀜(승계는 2차 범위) → 서버가 비어 있음 → 즐겨찾기 사라짐
 *
 * 지금은 localStorage라 같은 기기면 가입해도 남아 있다. 서버로 옮기면서 이걸
 * 잃으면 개선이 아니라 후퇴다. 그래서 "서버가 비어 있으면 로컬 값을 올린다".
 *
 * **"비어 있다"의 판정에 items.length를 쓰지 않는다.** 다른 기기에서 즐겨찾기를
 * 전부 지운 사람은 서버가 정상적으로 빈 목록인데, 그걸 "저장한 적 없음"으로
 * 읽으면 이 기기의 낡은 값이 되살아난다. 서버가 주는 updated_at이 null인지로
 * 가른다 — 한 번도 저장한 적 없을 때만 null이다.
 */

import { fetchFavorites, replaceFavorites } from "../api/trip";
import { loadFavorites, saveFavorites, type FavoritePlace } from "./sidebarStorage";
import type { FavoritePlaceItem } from "../types";

/*
 * 페이지 로드당 한 번만 맞춘다. 사이드바와 위치 설정 화면이 각각 마운트될 때마다
 * 서버를 부르면 화면을 오갈 때 요청이 계속 쌓인다 — 즐겨찾기는 자주 바뀌는 값이
 * 아니다(chatSessions·preferenceSync와 같은 이유).
 */
let syncPromise: Promise<FavoritePlace[]> | null = null;

/* 서버 표현과 화면 표현은 필드 이름만 다르다. 화면 쪽은 카멜, 계약은 스네이크다. */
function toLocal(item: FavoritePlaceItem): FavoritePlace {
  return {
    id: item.id,
    label: item.label,
    ...(item.search_center_name ? { searchCenterName: item.search_center_name } : {}),
    ...(item.address ? { address: item.address } : {}),
  };
}

function toRemote(favorite: FavoritePlace): FavoritePlaceItem {
  return {
    id: favorite.id,
    label: favorite.label,
    search_center_name: favorite.searchCenterName ?? null,
    address: favorite.address ?? null,
  };
}

async function runSync(): Promise<FavoritePlace[]> {
  const local = loadFavorites();

  try {
    const remote = await fetchFavorites();

    /* 한 번도 저장한 적 없는 계정이다. 이 기기에 값이 있으면 그것이 이 사람의
       즐겨찾기이므로 계정으로 올린다 — 가입 직후 게스트 때 담은 값이 여기서 넘어간다. */
    if (remote.updated_at === null) {
      if (local.length === 0) return [];
      await replaceFavorites(local.map(toRemote));
      return local;
    }

    /* 서버가 정본이다. 빈 목록이어도 그대로 따른다(다른 기기에서 전부 지운 경우). */
    const items = remote.items.map(toLocal);
    saveFavorites(items);
    return items;
  } catch {
    /* 토큰이 없거나(401) 서버에 못 닿는 경우다. 화면이 멈추면 안 되므로 이
       기기의 값으로 계속 돈다 — 다음 기회에 다시 맞춘다. */
    return local;
  }
}

/** 서버와 맞춘 즐겨찾기. 페이지 로드당 한 번만 실제로 요청한다. */
export function syncFavorites(): Promise<FavoritePlace[]> {
  syncPromise ??= runSync();
  return syncPromise;
}

/**
 * 즐겨찾기를 저장한다. **로컬에 먼저 쓴다** — 서버 호출이 실패해도 사용자가 방금
 * 담은 값이 사라지지 않게 하기 위해서다.
 *
 * 서버 실패를 삼킨다. 취향은 "저장" 버튼을 눌러 실패를 알려줄 자리가 있지만,
 * 즐겨찾기는 별을 누르는 즉시 반영되는 흐름이라 되돌릴 화면이 없다 — 다음
 * 페이지 로드의 sync가 서버 값으로 맞춘다.
 */
export async function pushFavorites(items: readonly FavoritePlace[]): Promise<void> {
  const next = [...items];
  saveFavorites(next);
  /* 이번에 저장한 값이 곧 최신이다. 다음 sync가 서버를 다시 읽어 덮어쓰지
     않도록 결과를 바꿔 둔다. */
  syncPromise = Promise.resolve(next);
  try {
    await replaceFavorites(next.map(toRemote));
  } catch {
    /* 위 설명 참고. 로컬에는 남아 있고 화면도 그대로 돈다. */
  }
}

/** 로그아웃과 테스트가 페이지 로드 경계를 흉내 낼 수 있게 캐시를 비운다. */
export function resetFavoritesSync(): void {
  syncPromise = null;
}

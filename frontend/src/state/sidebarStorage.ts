/*
 * 역할: 사이드바의 즐겨찾기를 localStorage에 저장·복원한다.
 * 입력: 화면에서 만든 FavoritePlace 배열.
 * 출력: 저장된 값 또는 없으면 빈 배열.
 * 호출 시점: SideDrawerContent가 마운트/변경 시 호출한다.
 *
 * **채팅 히스토리는 여기서 빠졌다**(TP-222 후속). 계정에서 받아오므로
 * state/chatSessions.ts가 맡는다. ChatHistoryEntry 타입만 남겨 둔 것은 화면이
 * 쓰는 모양이 그대로이기 때문이다 — 서버 응답을 이 모양으로 바꿔 넣는다.
 *
 * TODO: 즐겨찾기도 계정으로 옮긴다. 채팅 화면(SavedPlacesBar)은 이미
 *   /state/{session_id}/saved-places를 쓰는데 사이드바만 아직 이 기기에 남는다 —
 *   같은 개념이 두 곳에 따로 있는 상태다(DESIGN_SYSTEM.md §10.6).
 */

const FAVORITES_KEY = "tb_favorites";

export interface FavoritePlace {
  id: string;
  label: string;
  /**
   * 검색 위치로 보낼 장소 이름. 위치 설정 화면의 검색 결과에서 담은 항목만 가진다.
   *
   * label과 나눠 두는 이유는 사이드바에서 자유 입력으로 만든 즐겨찾기 때문이다 —
   * "회사 (역삼동)" 같은 라벨을 그대로 검색 위치로 보내면 엉뚱한 곳으로 풀린다.
   * 이 값이 없는 항목은 label로 떨어지므로, 예전에 저장된 즐겨찾기도 그대로 읽힌다.
   */
  searchCenterName?: string;
  /**
   * 담을 때의 도로명주소. 목록에서 이름만으로는 어디인지 알기 어려워 함께 보여준다
   * ("스타벅스 종로점"이 여러 개일 때 특히). 자유 입력으로 만든 항목에는 없다.
   */
  address?: string | null;
}

export interface ChatHistoryEntry {
  id: string;
  label: string;
  date: string;
  /** 그 대화의 위치. 장소 이름이 아니라 "어디 얘기였는지"다. */
  location: string | null;
}

export function createId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function readJsonArray<T>(key: string): T[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function writeJsonArray<T>(key: string, value: T[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* localStorage가 막혀 있어도(시크릿 모드 등) 화면 동작 자체는 계속돼야 한다. */
  }
}

export function loadFavorites(): FavoritePlace[] {
  return readJsonArray<FavoritePlace>(FAVORITES_KEY);
}

/*
 * 목록이 바뀌면 알려준다. 사이드바와 위치 설정 화면이 함께 구독한다.
 *
 * 저장소는 값이 바뀌어도 React에 알려주지 않는다. 두 화면이 각자 사본을 들고 있어,
 * 한쪽에서 담아도 다른 쪽은 새로고침해야 보였다 — 같은 목록이 두 군데서 다르게
 * 보이는 셈이다(searchCenter를 구독으로 바꾼 것과 같은 이유).
 */
type Listener = (favorites: FavoritePlace[]) => void;
const listeners = new Set<Listener>();

export function subscribeFavorites(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/* 로그아웃에서 이 기기의 즐겨찾기를 비운다(state/localUserData.ts). 서버에 사본이
   없어 되돌릴 수 없는 삭제다 — 신원이 바뀔 때만 부른다. */
export function clearFavorites(): void {
  try {
    localStorage.removeItem(FAVORITES_KEY);
  } catch {
    /* 저장소가 막혀 있어도(시크릿 모드 등) 로그아웃 자체는 끝나야 한다. */
  }
  listeners.forEach((listener) => listener([]));
}

/* 빈 목록은 키 자체를 지운다. "[]"를 남기면 로그아웃 직후 화면이 다시 마운트하면서
   방금 지운 키가 되살아나, 저장소만 보고는 비워졌는지 알 수 없다(값은 비어 있어도
   눈에는 남아 있는 것으로 보인다). 없는 키는 읽을 때 빈 목록으로 취급된다. */
export function saveFavorites(favorites: FavoritePlace[]): void {
  if (favorites.length === 0) {
    clearFavorites();
    return;
  }
  writeJsonArray(FAVORITES_KEY, favorites);
  listeners.forEach((listener) => listener(favorites));
}

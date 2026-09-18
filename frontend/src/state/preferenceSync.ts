/*
 * 역할: 계정에 저장된 취향과 이 기기의 localStorage를 맞춘다.
 * 입력: 서버 응답(GET /api/preferences)과 preferenceStorage의 로컬 값.
 * 출력: 화면이 그릴 취향 목록.
 * 호출 시점: 홈·취향 화면이 마운트될 때 한 번, 취향을 저장할 때.
 *
 * **localStorage를 없애지 않고 거울로 남긴다.** 서버로만 옮기면 지금보다
 * 나빠지는 경우가 생기기 때문이다.
 *
 *   게스트로 취향을 고름 → 게스트 uid로 서버에 저장
 *   → 회원가입 → uid가 바뀜(승계는 2차 범위) → 서버가 비어 있음 → 취향 사라짐
 *
 * 지금은 localStorage라 같은 기기면 가입해도 남아 있다. 서버로 옮기면서 이걸
 * 잃으면 개선이 아니라 후퇴다. 그래서 "서버가 비어 있으면 로컬 값을 올린다".
 *
 * **"비어 있다"의 판정에 items.length를 쓰지 않는다.** 다른 기기에서 취향을
 * 전부 해제한 사람은 서버가 정상적으로 빈 목록인데, 그걸 "저장한 적 없음"으로
 * 읽으면 이 기기의 낡은 값이 되살아난다. 서버가 주는 updated_at이 null인지로
 * 가른다 — 한 번도 저장한 적 없을 때만 null이다.
 */

import { fetchPreferences, replacePreferences } from "../api/trip";
import { loadPreferences, savePreferences, type SavedPreference } from "./preferenceStorage";

/*
 * 페이지 로드당 한 번만 맞춘다. 홈과 취향 화면이 각각 마운트될 때마다 서버를
 * 부르면 화면을 오갈 때 요청이 계속 쌓인다 — 취향은 자주 바뀌는 값이 아니다.
 */
let syncPromise: Promise<SavedPreference[]> | null = null;

async function runSync(): Promise<SavedPreference[]> {
  const local = loadPreferences();

  try {
    const remote = await fetchPreferences();

    /* 한 번도 저장한 적 없는 계정이다. 이 기기에 값이 있으면 그것이 이 사람의
       취향이므로 계정으로 올린다 — 가입 직후 게스트 때 고른 값이 여기서 넘어간다. */
    if (remote.updated_at === null) {
      if (local.length === 0) return [];
      await replacePreferences(local);
      return local;
    }

    /* 서버가 정본이다. 빈 목록이어도 그대로 따른다(다른 기기에서 전부 해제한 경우). */
    savePreferences(remote.items);
    return remote.items;
  } catch {
    /* 토큰이 없거나(401) 서버에 못 닿는 경우다. 화면이 멈추면 안 되므로 이
       기기의 값으로 계속 돈다 — 다음 기회에 다시 맞춘다. */
    return local;
  }
}

/** 서버와 맞춘 취향. 페이지 로드당 한 번만 실제로 요청한다. */
export function syncPreferences(): Promise<SavedPreference[]> {
  syncPromise ??= runSync();
  return syncPromise;
}

/**
 * 취향을 저장한다. **로컬에 먼저 쓴다** — 서버 호출이 실패해도 사용자가 방금
 * 고른 값이 사라지지 않게 하기 위해서다. 서버 실패는 삼키지 않고 던진다.
 */
export async function pushPreferences(items: readonly SavedPreference[]): Promise<void> {
  savePreferences(items);
  /* 이번에 저장한 값이 곧 최신이다. 다음 sync가 서버를 다시 읽어 덮어쓰지
     않도록 결과를 바꿔 둔다. */
  syncPromise = Promise.resolve([...items]);
  await replacePreferences(items);
}

/** 테스트가 페이지 로드 경계를 흉내 낼 수 있게 캐시를 비운다. */
export function resetPreferenceSync(): void {
  syncPromise = null;
}

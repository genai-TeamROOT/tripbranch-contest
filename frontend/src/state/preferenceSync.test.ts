/*
 * 역할: 계정 취향과 이 기기 localStorage를 맞추는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **이 파일의 핵심은 "언제 로컬 값을 올리는가"다.** 잘못 정하면 두 방향으로
 * 망가진다 — 안 올리면 게스트가 가입하는 순간 취향을 잃고, 너무 올리면 다른
 * 기기에서 전부 해제한 사람의 계정에 낡은 값이 되살아난다.
 */

import { beforeEach, expect, test, vi } from "vitest";
import { loadPreferences, savePreferences } from "./preferenceStorage";
import { pushPreferences, resetPreferenceSync, syncPreferences } from "./preferenceSync";

const server = vi.hoisted(() => ({
  items: [] as { label: string; source: string; codes: readonly string[] }[],
  updatedAt: null as string | null,
  fail: false,
  putCount: 0,
}));

vi.mock("../api/trip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/trip")>();
  return {
    ...actual,
    fetchPreferences: async () => {
      if (server.fail) throw new Error("서버에 못 닿음");
      return { items: server.items, updated_at: server.updatedAt };
    },
    replacePreferences: async (items: typeof server.items) => {
      server.putCount += 1;
      if (server.fail) throw new Error("서버에 못 닿음");
      server.items = [...items];
      server.updatedAt = "2026-09-03T00:00:00+09:00";
      return { items: server.items, updated_at: server.updatedAt };
    },
  };
});

const QUIET = { label: "조용한 곳", source: "preference", codes: ["quiet"] } as const;
const CAFE = { label: "카페", source: "place_tag", codes: ["카페"] } as const;

beforeEach(() => {
  localStorage.clear();
  resetPreferenceSync();
  server.items = [];
  server.updatedAt = null;
  server.fail = false;
  server.putCount = 0;
});

/* 이 파일에서 가장 중요한 테스트다. 게스트로 고른 취향이 가입하면서 사라지는 것을
   막는 장치다 — 가입하면 uid가 바뀌어 계정 쪽은 비어 있다. */
test("계정에 한 번도 저장한 적 없으면 이 기기 값을 올린다", async () => {
  savePreferences([QUIET, CAFE]);

  const synced = await syncPreferences();

  expect(server.items.map((item) => item.label)).toEqual(["조용한 곳", "카페"]);
  expect(synced).toHaveLength(2);
});

/*
 * 두 번째로 중요한 테스트. "비어 있다"를 items.length로 판정하면 이 경우가
 * 깨진다 — 다른 기기에서 전부 해제했는데 이 기기의 낡은 값이 되살아난다.
 */
test("계정이 빈 목록을 저장한 상태면 이 기기 값을 올리지 않는다", async () => {
  savePreferences([QUIET, CAFE]);
  server.items = [];
  server.updatedAt = "2026-09-03T00:00:00+09:00"; // 전부 해제하고 저장한 계정

  const synced = await syncPreferences();

  expect(synced).toEqual([]);
  expect(server.putCount).toBe(0);
  /* 이 기기에도 반영한다 — 안 그러면 새로고침마다 낡은 값이 잠깐 보인다. */
  expect(loadPreferences()).toEqual([]);
});

test("계정 값이 이 기기 값을 덮어쓴다", async () => {
  savePreferences([QUIET]);
  server.items = [CAFE];
  server.updatedAt = "2026-09-03T00:00:00+09:00";

  const synced = await syncPreferences();

  expect(synced.map((item) => item.label)).toEqual(["카페"]);
  expect(loadPreferences().map((item) => item.label)).toEqual(["카페"]);
});

test("둘 다 비었으면 아무것도 올리지 않는다", async () => {
  const synced = await syncPreferences();

  expect(synced).toEqual([]);
  expect(server.putCount).toBe(0);
});

/* 서버에 못 닿아도 화면은 돌아야 한다. 토큰이 없어 401이 나는 경우도 여기로 온다. */
test("서버에 못 닿으면 이 기기 값으로 계속 돈다", async () => {
  savePreferences([QUIET]);
  server.fail = true;

  const synced = await syncPreferences();

  expect(synced.map((item) => item.label)).toEqual(["조용한 곳"]);
});

test("페이지 로드당 한 번만 서버를 부른다", async () => {
  savePreferences([QUIET]);

  await syncPreferences();
  await syncPreferences();
  await syncPreferences();

  expect(server.putCount).toBe(1);
});

/* 저장은 로컬을 먼저 쓴다 — 서버가 실패해도 방금 고른 값이 사라지면 안 된다. */
test("계정 저장이 실패해도 이 기기에는 남는다", async () => {
  server.fail = true;

  await expect(pushPreferences([QUIET, CAFE])).rejects.toThrow();

  expect(loadPreferences().map((item) => item.label)).toEqual(["조용한 곳", "카페"]);
});

test("저장한 값이 곧바로 다음 조회에 반영된다", async () => {
  await pushPreferences([QUIET]);

  const synced = await syncPreferences();

  expect(synced.map((item) => item.label)).toEqual(["조용한 곳"]);
});

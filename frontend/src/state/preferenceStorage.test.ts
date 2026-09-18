/*
 * 역할: 취향 저장·복원이 깨진 값에도 버티는지 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { beforeEach, expect, test } from "vitest";
import {
  clearPreferences,
  loadPreferences,
  savePreferences,
  type SavedPreference,
} from "./preferenceStorage";

const SAMPLE: SavedPreference[] = [
  { label: "조용한 곳", source: "preference", codes: ["quiet"] },
  { label: "카페", source: "place_tag", codes: ["카페", "찻집"] },
  { label: "조용한 서점", source: "custom", codes: [] },
];

beforeEach(() => {
  localStorage.clear();
});

test("저장한 취향을 그대로 되읽는다", () => {
  savePreferences(SAMPLE);
  expect(loadPreferences()).toEqual(SAMPLE);
});

test("저장한 적이 없으면 빈 배열이다", () => {
  expect(loadPreferences()).toEqual([]);
});

test("초기화하면 저장값이 사라진다", () => {
  savePreferences(SAMPLE);
  clearPreferences();
  expect(loadPreferences()).toEqual([]);
});

test("JSON이 깨져 있어도 화면을 막지 않는다", () => {
  localStorage.setItem("tb_preferences", "{망가진 값");
  expect(loadPreferences()).toEqual([]);
});

test("배열이 아닌 값이 들어 있으면 무시한다", () => {
  localStorage.setItem("tb_preferences", JSON.stringify({ label: "조용한 곳" }));
  expect(loadPreferences()).toEqual([]);
});

test("형태가 어긋난 항목만 버리고 나머지는 살린다", () => {
  // 하나가 깨졌다고 나머지까지 잃으면, 저장 형식이 바뀔 때 사용자가 전부 다시 골라야 한다.
  localStorage.setItem(
    "tb_preferences",
    JSON.stringify([
      { label: "조용한 곳", source: "preference", codes: ["quiet"] },
      { label: "", source: "preference", codes: [] }, // 빈 라벨
      { label: "카페", source: "이상한값", codes: [] }, // 알 수 없는 source
      { label: "야경 명소", source: "preference", codes: [1, 2] }, // 코드가 문자열이 아님
      { label: "데이트 코스", source: "preference", codes: ["date"] },
    ]),
  );

  expect(loadPreferences()).toEqual([
    { label: "조용한 곳", source: "preference", codes: ["quiet"] },
    { label: "데이트 코스", source: "preference", codes: ["date"] },
  ]);
});

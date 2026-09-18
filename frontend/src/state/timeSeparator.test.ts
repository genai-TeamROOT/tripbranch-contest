/*
 * 역할: 새 발화 위에 시각 구분선을 넣을지 정하는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * 화면으로 재현하려면 시계를 조작해야 하는 판정이라 여기서 직접 본다.
 */

import { expect, test } from "vitest";
import { hasTimeGap, TIME_SEPARATOR_GAP_MS } from "./timeSeparator";

const NOW = "2026-09-03T14:00:00.000Z";
const before = (ms: number) => new Date(Date.parse(NOW) - ms).toISOString();

/* 자리를 비웠다가 돌아와 이어 묻는 경우다 — 위쪽 지난 대화와 갈라 보여야 한다. */
test("30분보다 오래 비웠으면 넣는다", () => {
  expect(hasTimeGap(before(TIME_SEPARATOR_GAP_MS + 1000), NOW)).toBe(true);
});

/* 바로 이어지는 발화다. 몇 분 간격의 줄이 계속 끼어들면 대화가 끊겨 보인다. */
test("바로 이어 물으면 넣지 않는다", () => {
  expect(hasTimeGap(before(60 * 1000), NOW)).toBe(false);
});

test("정확히 30분이면 넣지 않는다", () => {
  expect(hasTimeGap(before(TIME_SEPARATOR_GAP_MS), NOW)).toBe(false);
});

/* 대화의 첫 발화 — 위에 갈라 보일 것이 없다. */
test("앞 턴이 없으면 넣지 않는다", () => {
  expect(hasTimeGap(null, NOW)).toBe(false);
});

test("시각을 못 읽으면 넣지 않는다", () => {
  expect(hasTimeGap("어제쯤", NOW)).toBe(false);
});

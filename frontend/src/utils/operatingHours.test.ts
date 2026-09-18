/*
 * 역할: 상시 개방 판정이 서버가 실제로 보내는 표기들을 받아내는지 확인한다.
 * 호출 시점: 로컬 테스트와 CI.
 */

import { describe, expect, it } from "vitest";
import { isAlwaysOpen } from "./operatingHours";

describe("isAlwaysOpen", () => {
  it("상시 개방을 뜻하는 표기를 받아낸다", () => {
    // "24시간"이 서버가 실제로 보내는 값이고, 나머지는 원문이 그대로 실려 오는 경우다.
    expect(isAlwaysOpen("24시간")).toBe(true);
    expect(isAlwaysOpen("상시 개방")).toBe(true);
    expect(isAlwaysOpen("연중무휴")).toBe(true);
    expect(isAlwaysOpen("00:00~24:00")).toBe(true);
  });

  it("시간 구간이 있는 곳은 상시가 아니다", () => {
    expect(isAlwaysOpen("09:00~18:00")).toBe(false);
    expect(isAlwaysOpen("00:00~23:00")).toBe(false);
    // 24시간이 아니라 24일이다 — 붙은 숫자에 걸리지 않는지 본다.
    expect(isAlwaysOpen("매월 24일 휴무")).toBe(false);
  });
});

import { describe, expect, test } from "vitest";
import {
  LOCATION_RECONFIRM_AFTER_MS,
  getLocationAgeMinutes,
  isLocationRefreshDue,
} from "./locationRefresh";

const JONGNO_LOCATION = "37.5796,126.9769";
const NOW = 1_786_000_000_000;

describe("location refresh timing", () => {
  test("위치가 없으면 재확인 질문을 띄우지 않는다", () => {
    expect(isLocationRefreshDue(null, null, null, NOW)).toBe(false);
  });

  test("마지막 확인 후 30분 전까지는 기존 위치를 계속 사용한다", () => {
    const capturedAt = NOW - LOCATION_RECONFIRM_AFTER_MS + 1;

    expect(isLocationRefreshDue(JONGNO_LOCATION, capturedAt, null, NOW)).toBe(false);
  });

  test("마지막 확인 후 정확히 30분이 지나면 재확인을 묻는다", () => {
    const capturedAt = NOW - LOCATION_RECONFIRM_AFTER_MS;

    expect(isLocationRefreshDue(JONGNO_LOCATION, capturedAt, null, NOW)).toBe(true);
    expect(getLocationAgeMinutes(capturedAt, NOW)).toBe(30);
  });

  test("현재 위치를 다시 가져오면 그 시각부터 새 30분을 계산한다", () => {
    const refreshedAt = NOW;

    expect(
      isLocationRefreshDue(JONGNO_LOCATION, refreshedAt, null, NOW + LOCATION_RECONFIRM_AFTER_MS - 1),
    ).toBe(false);
    expect(
      isLocationRefreshDue(JONGNO_LOCATION, refreshedAt, null, NOW + LOCATION_RECONFIRM_AFTER_MS),
    ).toBe(true);
  });
});

describe("location refresh snooze", () => {
  // "N분 전 위치로 계속"을 누른 뒤 실제 GPS는 다시 안 받았는데도 재확인이
  // 다음 턴마다 반복되던 버그의 회귀 테스트 — capturedAt과 snoozedUntil을
  // 분리해서 재질문 억제와 나이 표시를 독립적으로 맞게 유지한다.
  const staleCapturedAt = NOW - 3 * LOCATION_RECONFIRM_AFTER_MS;

  test("스누즈 마감 전에는 위치가 오래됐어도 다시 묻지 않는다", () => {
    const snoozedUntil = NOW + LOCATION_RECONFIRM_AFTER_MS;

    expect(isLocationRefreshDue(JONGNO_LOCATION, staleCapturedAt, snoozedUntil, NOW)).toBe(false);
  });

  test("스누즈 마감이 지나면 다시 묻고, 위치 나이는 실제 경과 시간 그대로다", () => {
    const snoozedUntil = NOW - 1;

    expect(isLocationRefreshDue(JONGNO_LOCATION, staleCapturedAt, snoozedUntil, NOW)).toBe(true);
    // 스누즈가 나이 표시를 리셋하지 않는다 — capturedAt 기준 실제 90분 경과.
    expect(getLocationAgeMinutes(staleCapturedAt, NOW)).toBe(90);
  });

  test("snoozedUntil이 null이면 스누즈가 없었던 것처럼 판정한다", () => {
    expect(isLocationRefreshDue(JONGNO_LOCATION, staleCapturedAt, null, NOW)).toBe(true);
  });
});

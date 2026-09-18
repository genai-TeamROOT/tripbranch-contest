/*
 * 역할: 응답에 실려 온 위치 조건을 위치 설정에 되돌려 쓰는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **이 파일의 핵심은 "null을 어떻게 볼 것인가"다.** null은 "이 설정을 지우라"가
 * 아니라 "서버도 위치를 모른다"는 뜻인데, 둘을 하나로 합치면 양쪽으로 망가진다 —
 * 지우면 사용자가 위치 설정 화면에서 손으로 고른 값이 발화 하나로 사라지고,
 * 채워진 값까지 무시하면 대화로 옮긴 위치가 화면에 영영 안 보인다. 그래서
 * "채워져 오면 덮어쓰고, null이면 그대로 둔다"를 여기서 못 박는다.
 */

import { beforeEach, expect, test, vi } from "vitest";
import {
  loadLocationSettings,
  setLocationCenter,
  setLocationOrigin,
  subscribeLocationSettings,
  syncLocationSettingsFromConditions,
} from "./locationSettings";

/* 실제 UserConditions는 필드가 많지만 이 함수가 보는 것은 둘뿐이다. 나머지를 다
   채우면 계약이 늘 때마다 이 파일이 같이 깨진다. */
function conditions(current_location: string | null, search_center: string | null) {
  return { current_location, search_center };
}

beforeEach(() => {
  sessionStorage.clear();
});

test("발화가 정한 두 위치가 설정을 모두 덮어쓴다", () => {
  /* "지금 안국역인데 광화문역 근처 알려줘" — 발화가 둘 다 말했으니 둘 다 바뀐다. */
  setLocationOrigin("서대문역");
  setLocationCenter("서대문역");

  syncLocationSettingsFromConditions(conditions("안국역", "광화문역"));

  expect(loadLocationSettings()).toEqual({ origin: "안국역", center: "광화문역" });
});

test("발화가 검색 기준만 말하면 출발지는 설정해 둔 값이 남는다", () => {
  /* 두 값은 다른 질문의 답이라 따로 판단한다(D-067). "광화문역 근처 알려줘"는
     어디를 찾을지만 바꾼 것이지 사용자가 옮겨간 것이 아니다. */
  setLocationOrigin("서대문역");
  setLocationCenter("서대문역");

  syncLocationSettingsFromConditions(conditions(null, "광화문역"));

  expect(loadLocationSettings()).toEqual({ origin: "서대문역", center: "광화문역" });
});

test("조건이 둘 다 null이면 설정을 지우지 않는다", () => {
  /* null은 "이 설정을 지우라"가 아니라 "서버도 위치를 모른다"는 뜻이다. 세션에
     아직 RECOMMEND 조건이 없는 INFO 턴이 대표적인데, 위치를 정해 두고 정보
     질문부터 던졌다고 그 설정이 사라져야 할 이유는 없다 — 푸는 것은 칩의 ✕다. */
  setLocationOrigin("서대문역");
  setLocationCenter("서대문역");

  syncLocationSettingsFromConditions(conditions(null, null));

  expect(loadLocationSettings()).toEqual({ origin: "서대문역", center: "서대문역" });
});

test("조건 자체가 없는 응답도 그냥 지나간다", () => {
  /* 스트림 콜백 안에서 불리므로 여기서 던지면 턴 전체가 오류로 끝난다. */
  setLocationCenter("서대문역");

  expect(() => syncLocationSettingsFromConditions(null)).not.toThrow();
  expect(loadLocationSettings()).toEqual({ origin: null, center: "서대문역" });
});

test("값이 그대로면 구독자에게 알리지 않는다", () => {
  /* 위치를 말하지 않은 턴에도 세션에 남은 직전 값이 그대로 실려 온다. 그때마다
     알리면 값이 안 바뀌었는데도 헤더가 매 턴 다시 그려진다. */
  setLocationCenter("안국역");
  const listener = vi.fn();
  const unsubscribe = subscribeLocationSettings(listener);

  syncLocationSettingsFromConditions(conditions(null, "안국역"));

  expect(listener).not.toHaveBeenCalled();
  unsubscribe();
});

test("빈 문자열은 값으로 치지 않는다", () => {
  /* normalize()가 걸러야 하는 자리다. 통과시키면 헤더 pill이 이름 없는 빈 칸이
     된다 — "현재 위치" 같은 폴백까지 내려가지 못하기 때문이다. */
  setLocationCenter("서대문역");

  syncLocationSettingsFromConditions(conditions("   ", ""));

  expect(loadLocationSettings()).toEqual({ origin: null, center: "서대문역" });
});

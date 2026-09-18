/*
 * 역할: 일정 구간 표기 규칙을 검증한다. (TP-216)
 *
 * 예전에는 이 자리가 "도보 이동 약 N분"으로 고정돼 있었고, 편성이 긴 구간을
 * 대중교통으로 전환하기 시작하면서 4.3km 61분 구간이 화면에는 도보로 떴다.
 *
 * 이 규칙을 쓰는 화면이 ScheduleRoute 하나로 합쳐졌지만(2026-09-09), 문구 규칙
 * 자체는 화면과 무관한 순수 함수라 여기서 따로 잠근다 — 예전에는 사라진
 * ScheduleTravelSegment 의 테스트 파일에 얹혀 있었다.
 */

import { expect, test } from "vitest";
import {
  clusterBadgeLabel,
  scheduleTravelLabel,
  SCHEDULE_CLUSTER_WALK_MIN,
} from "./scheduleTravel";

test("실측 구간은 이동수단과 시간만 말한다", () => {
  expect(scheduleTravelLabel(20, "transit", true)).toBe("대중교통으로 20분");
  expect(scheduleTravelLabel(9, "walking", true)).toBe("걸어서 9분");
  expect(scheduleTravelLabel(14, "driving", true)).toBe("차로 14분");
});

test("추정 구간은 값을 보여주고 추정임을 밝힌다", () => {
  // 추천 카드와 규칙이 다르다 — 일정은 이동시간 없이 성립하지 않으므로 숨기지 않는다.
  //
  // **"약"으로 담는다.** 예전에는 "도보 이동 33분 · 추정"이었는데 "이동"(수단)과
  // "추정"(그 숫자의 근거)이 층위가 달라 점으로 나열하니 셋이 대등하게 읽혔다.
  expect(scheduleTravelLabel(33, "walking", false)).toBe("걸어서 약 33분");
});

test("이동수단을 모르는 구간은 수단을 말하지 않는다", () => {
  // 서버가 좌표를 못 구해 시간표 폴백값(15분)을 쓴 자리다.
  expect(scheduleTravelLabel(15, null, false)).toBe("이동 약 15분");
  expect(scheduleTravelLabel(15, undefined, undefined)).toBe("이동 약 15분");
});

test("묶음 배지는 화면이 말하는 기준 분과 곳 수를 함께 말한다", () => {
  /* 이 상수는 백엔드 budget.SCHEDULE_CLUSTER_WALK_MINUTES 와 같은 값이어야
     한다 — 화면이 말하는 기준과 편성이 쓴 기준이 갈리면 사용자가 읽는 숫자가
     거짓이 된다. 문구가 상수를 실제로 싣는지 여기서 잠근다. */
  expect(clusterBadgeLabel(3, false)).toBe(`걸어서 ${SCHEDULE_CLUSTER_WALK_MIN}분 안쪽인 3곳`);
  expect(clusterBadgeLabel(2, true)).toBe(`2 stops within a ${SCHEDULE_CLUSTER_WALK_MIN}-min walk`);
});

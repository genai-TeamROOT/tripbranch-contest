/*
 * 역할: 지난 추천을 말풍선과 짝짓는 규칙을 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * **핵심은 "추천이 턴보다 먼저 기록된다"는 사실이다.** 이걸 놓치면 시각 비교
 * 방향이 뒤집혀 모든 카드가 사라지거나 엉뚱한 말풍선에 붙는데, 화면상으로는
 * 그냥 "카드가 없네"로 보여 알아채기 어렵다.
 */

import { expect, test } from "vitest";
import type { PastRecommendation, StoredConversationTurn } from "../types";
import { attachRecommendationsToTurns } from "./pastRecommendations";

function turn(at: string, userInput = "질문"): StoredConversationTurn {
  return { user_input: userInput, assistant_message: "네", intent: "RECOMMEND", place_names: [], at };
}

function place(runId: string, name: string, shownAt: string): PastRecommendation {
  return {
    place_id: `place_${name}`,
    run_id: runId,
    name,
    rank: 1,
    distance_km: null,
    environment_type: null,
    reason: null,
    shown_at: shownAt,
  };
}

/* 이 파일에서 가장 중요한 테스트다. 실측 평균이 97초 먼저였다. */
test("턴보다 먼저 기록된 추천이 그 턴에 붙는다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00")];
  const places = [place("run_1", "국립중앙박물관", "2026-09-03T08:58:23+09:00")];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0]).toHaveLength(1);
  expect(attached[0][0].map((item) => item.name)).toEqual(["국립중앙박물관"]);
});

test("각 묶음이 자기 턴에 붙는다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00"), turn("2026-09-03T09:10:00+09:00")];
  const places = [
    place("run_1", "첫 턴의 장소", "2026-09-03T08:59:00+09:00"),
    place("run_2", "둘째 턴의 장소", "2026-09-03T09:09:00+09:00"),
  ];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0][0].map((item) => item.name)).toEqual(["첫 턴의 장소"]);
  expect(attached[1][0].map((item) => item.name)).toEqual(["둘째 턴의 장소"]);
});

test("같은 run_id는 한 묶음으로 함께 붙는다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00")];
  const places = [
    place("run_1", "가", "2026-09-03T08:58:00+09:00"),
    place("run_1", "나", "2026-09-03T08:58:01+09:00"),
  ];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0]).toHaveLength(1);
  expect(attached[0][0].map((item) => item.name)).toEqual(["가", "나"]);
});

test("같은 턴에서 두 번 추천했으면 묶음도 둘이다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00")];
  const places = [
    place("run_1", "처음 추천", "2026-09-03T08:57:00+09:00"),
    place("run_2", "다른 곳 보여줘", "2026-09-03T08:59:00+09:00"),
  ];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0]).toHaveLength(2);
});

/*
 * 두 번째로 중요한 테스트. recent_turns는 5개만 남는데 추천 이력에는 상한이
 * 없어, 화면에 없는 옛 턴의 추천도 규칙상 "남아 있는 첫 턴"에 걸린다. 그대로
 * 붙이면 하지도 않은 질문의 답으로 보인다.
 */
test("남아 있는 어느 턴과도 시간이 안 맞는 추천은 버린다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00")];
  const places = [place("run_old", "밀려난 턴의 장소", "2026-09-03T07:00:00+09:00")];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0]).toEqual([]);
});

test("턴보다 늦게 기록된 추천은 그 턴에 붙지 않는다", () => {
  const turns = [turn("2026-09-03T09:00:00+09:00")];
  const places = [place("run_1", "나중 장소", "2026-09-03T09:00:01+09:00")];

  const attached = attachRecommendationsToTurns(turns, places);

  expect(attached[0]).toEqual([]);
});

test("추천이 없으면 턴 수만큼 빈 목록이다", () => {
  const attached = attachRecommendationsToTurns(
    [turn("2026-09-03T09:00:00+09:00"), turn("2026-09-03T09:10:00+09:00")],
    [],
  );

  expect(attached).toEqual([[], []]);
});

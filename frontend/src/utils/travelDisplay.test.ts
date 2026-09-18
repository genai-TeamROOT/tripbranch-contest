import { expect, test } from "vitest";
import type { RecommendationItem } from "../types";
import { travelLabel, travelValue } from "./travelDisplay";

function item(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    place_id: "p1",
    name: "장소A",
    category: "cafe",
    distance_km: 1.4,
    remaining_minutes: 60,
    environment_type: "indoor",
    recommendation_reason: "테스트용",
    explanations: [],
    warnings: [],
    score: 0.5,
    feature_scores: {},
    weights_used: {},
    taste_evidence: [],
    ...overrides,
  };
}

test("실측 도보 시간이 있으면 서버 값을 그대로 쓴다", () => {
  const measured = item({
    travel_distance_m: 2300,
    travel_duration_seconds: 2460,
    travel_mode: "walking",
  });

  expect(travelLabel(measured)).toBe("도보 이동");
  // 41분: 서버 근거 문장과 같은 값이다. 직선거리 1.4km에 3.6km/h를 곱하던 옛 추정은
  // 24분을 표시해 한 카드에 두 개의 도보 시간이 떴다.
  expect(travelValue(measured)).toBe("약 41분");
});

test("실측이 없으면 시간을 만들지 않고 직선거리로 말한다", () => {
  const unmeasured = item();

  expect(travelLabel(unmeasured)).toBe("직선거리");
  expect(travelValue(unmeasured)).toBe("약 1.4km");
});

test("1km 미만 직선거리는 m로 표시한다", () => {
  expect(travelValue(item({ distance_km: 0.437 }))).toBe("약 437m");
});

test("한 시간을 넘는 실측은 시간과 분으로 나눠 말한다", () => {
  const measured = item({ travel_duration_seconds: 4500, travel_mode: "walking" });

  expect(travelValue(measured)).toBe("약 1시간 15분");
});

test("도보가 아닌 이동수단은 그 수단으로 표시한다", () => {
  const driving = item({ travel_duration_seconds: 600, travel_mode: "driving" });

  expect(travelLabel(driving)).toBe("자동차 이동");
  expect(travelValue(driving)).toBe("약 10분");
});

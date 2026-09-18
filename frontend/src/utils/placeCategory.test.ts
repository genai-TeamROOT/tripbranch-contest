/*
 * 역할: placeCategoryLabel이 어느 값을 먼저 쓰는지와, 없을 때 어디로 떨어지는지를
 *   잠근다. 중분류가 빠지는 경우가 실측으로는 0건이지만(조회 실패 시에만 생긴다)
 *   그때 화면이 비지 않아야 한다.
 */

import { expect, it } from "vitest";
import { PLACE_CATEGORY_LABELS, placeCategoryLabel } from "./placeCategory";

it("중분류 한글명이 있으면 그것을 쓴다", () => {
  expect(placeCategoryLabel({ category: "restaurant", category_label: "한식" })).toBe("한식");
});

it("중분류가 없으면 대분류를 한글로 옮긴다", () => {
  expect(placeCategoryLabel({ category: "shopping", category_label: null })).toBe("쇼핑");
  expect(placeCategoryLabel({ category: "cultural_facility" })).toBe("문화시설");
});

it("구분자 주변 공백을 다듬는다", () => {
  // 원본 데이터가 실제로 이렇게 들어 있다(2026-09-08 실측).
  expect(placeCategoryLabel({ category: "restaurant", category_label: "카페/ 찻집" })).toBe(
    "카페/찻집",
  );
});

it("빈 문자열은 값이 없는 것으로 본다", () => {
  expect(placeCategoryLabel({ category: "restaurant", category_label: "   " })).toBe("음식점");
  expect(placeCategoryLabel({ category: "", category_label: null })).toBeNull();
});

it("표에 없는 대분류 코드는 그대로 보여준다", () => {
  // 감추면 새 코드가 들어온 것을 화면에서 알아챌 수 없다.
  expect(placeCategoryLabel({ category: "unknown" })).toBe("unknown");
});

it("개발자 패널과 같은 6종을 담는다", () => {
  expect(Object.keys(PLACE_CATEGORY_LABELS).sort()).toEqual([
    "attraction",
    "cultural_facility",
    "festival",
    "leisure",
    "restaurant",
    "shopping",
  ]);
});

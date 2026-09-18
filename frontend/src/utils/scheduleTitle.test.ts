/*
 * 역할: 저장할 일정의 기본 제목 규칙을 고정한다.
 * 호출 시점: vitest 실행 시.
 */

import { describe, expect, test } from "vitest";
import { defaultScheduleTitle } from "./scheduleTitle";
import type { ScheduleItem } from "../types";

function item(placeName: string): ScheduleItem {
  return { place_name: placeName } as ScheduleItem;
}

describe("defaultScheduleTitle", () => {
  test("여러 곳이면 첫 장소와 나머지 개수로 줄인다", () => {
    expect(defaultScheduleTitle([item("경복궁"), item("북촌"), item("인사동")])).toBe(
      "경복궁 외 2곳",
    );
  });

  test("한 곳이면 그 이름만 쓴다", () => {
    expect(defaultScheduleTitle([item("경복궁")])).toBe("경복궁");
  });

  test("이름이 없는 항목은 세지 않는다", () => {
    expect(defaultScheduleTitle([item("경복궁"), item("  ")])).toBe("경복궁");
  });

  /* 제목은 DB에서 not null·not blank다. 빈 일정에도 보낼 값이 있어야 한다. */
  test("쓸 이름이 하나도 없어도 빈 제목을 만들지 않는다", () => {
    expect(defaultScheduleTitle([])).toBe("저장한 일정");
  });
});

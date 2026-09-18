/*
 * 역할: 일정 진행 게이지가 시작·종료 시각, 체크 수, 가장 멀리 체크한 위치까지의
 * 채움 비율을 옳게 보여주는지 검증한다.
 */

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { ScheduleItem } from "../../types";
import { ScheduleRibbon } from "./ScheduleRibbon";

function stop(arrival: string, stay: number, travel: number | null, name: string): ScheduleItem {
  return {
    order: 1,
    place_id: `p-${name}`,
    place_name: name,
    estimated_arrival: arrival,
    estimated_duration_min: stay,
    travel_to_next_min: travel,
    reason: "",
  };
}

const AFTERNOON = [
  stop("14:10", 90, 6, "국립현대미술관 서울"),
  stop("15:46", 45, 21, "서울공예박물관"),
  stop("16:52", 65, null, "광장시장"),
];

function gauge(): HTMLElement {
  return screen.getByRole("progressbar");
}

test("시작과 끝 시각을 적는다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn={false} />);

  expect(screen.getByText(/2:10/)).toBeInTheDocument();
  /* 끝은 마지막 도착 16:52 + 65분 = 17:57 이다. */
  expect(screen.getByText(/5:57/)).toBeInTheDocument();
});

test("도착 시각을 못 읽으면 게이지를 통째로 그리지 않는다", () => {
  const broken = [stop("어제 오후", 90, null, "어딘가")];
  const { container } = render(<ScheduleRibbon items={broken} isEn={false} />);

  expect(container).toBeEmptyDOMElement();
});

test("체크가 없으면 게이지가 비어 있다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn={false} />);

  expect(screen.getByText("0/3 다녀왔어요")).toBeInTheDocument();
  expect(gauge()).toHaveAttribute("aria-valuenow", "0");
});

test("체크 수는 그대로 세되, 게이지는 가장 멀리 체크한 위치까지 찬다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn={false} visited={new Set([0])} />);

  expect(screen.getByText("1/3 다녀왔어요")).toBeInTheDocument();
  expect(gauge()).toHaveAttribute("aria-valuenow", "33");
});

/*
 * 순서를 건너뛰어 체크해도(예: 두 번째만) 그 위치만큼 찬다 — 첫 번째를
 * 다녀온 것처럼 보이지 않는다(2026-09-07).
 */
test("두 번째만 체크하면 3분의 2까지 찬다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn={false} visited={new Set([1])} />);

  expect(screen.getByText("1/3 다녀왔어요")).toBeInTheDocument();
  expect(gauge()).toHaveAttribute("aria-valuenow", "67");
});

test("모두 체크하면 게이지가 가득 찬다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn={false} visited={new Set([0, 1, 2])} />);

  expect(screen.getByText("3/3 다녀왔어요")).toBeInTheDocument();
  expect(gauge()).toHaveAttribute("aria-valuenow", "100");
});

test("영어로 바꾸면 문구가 영어가 된다", () => {
  render(<ScheduleRibbon items={AFTERNOON} isEn visited={new Set([0])} />);

  expect(screen.getByText("1/3 visited")).toBeInTheDocument();
});

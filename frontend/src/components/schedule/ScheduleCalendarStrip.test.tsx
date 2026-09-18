/*
 * 역할: 달력 띠의 요일 표시·점·주 이동·월 표시·오늘 이동만 잠근다. 실제
 * 필터링은 SavedScheduleList.test.tsx가 통합으로 본다.
 */

import { expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ScheduleCalendarStrip } from "./ScheduleCalendarStrip";
import { startOfWeek } from "../../utils/scheduleDates";

/* 날짜 칸이 놓인 grid 자체가 스와이프를 받는다 — 역할·이름이 없어 클래스로 찾는다. */
function daysGrid(container: HTMLElement): HTMLElement {
  const grid = container.querySelector(".grid-cols-7");
  if (!grid) throw new Error("날짜 grid를 찾지 못했다");
  return grid as HTMLElement;
}

test("저장한 일정이 있는 날에는 점이 뜨고, 고르면 채워진 원으로 바뀐다", async () => {
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  const onSelectDate = vi.fn();
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={() => {}}
      selectedDateKey={null}
      onSelectDate={onSelectDate}
      markedDateKeys={new Set(["2026-08-31"])}
      isEn={false}
    />,
  );

  const marked = screen.getByRole("button", { name: "8월 31일, 저장한 일정 있음" });
  const unmarked = screen.getByRole("button", { name: "9월 1일" });
  expect(marked).toBeInTheDocument();
  expect(unmarked).toBeInTheDocument();

  await userEvent.click(marked);
  expect(onSelectDate).toHaveBeenCalledWith("2026-08-31");
});

test("좌우 화살표를 누르면 한 주씩 옮긴다", async () => {
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  const onWeekChange = vi.fn();
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={onWeekChange}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "다음 주" }));
  const forwardedTo = onWeekChange.mock.calls[0][0] as Date;
  expect(forwardedTo.getTime() - weekStart.getTime()).toBe(7 * 24 * 60 * 60 * 1000);

  await userEvent.click(screen.getByRole("button", { name: "지난 주" }));
  const backedTo = onWeekChange.mock.calls[1][0] as Date;
  expect(weekStart.getTime() - backedTo.getTime()).toBe(7 * 24 * 60 * 60 * 1000);
});

test("주가 두 달에 걸치면 두 달을 함께 적는다", () => {
  /* 2026-08-31(월)이 일요일 시작이라, 이 주는 8/31~9/6 — 월 경계에 걸친다. */
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={() => {}}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  expect(screen.getByText("8월 – 9월")).toBeInTheDocument();
});

test("주가 한 달 안에 있으면 그 달 하나만 적는다", () => {
  const weekStart = startOfWeek(new Date("2026-09-09T09:00:00+09:00"));
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={() => {}}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  expect(screen.getByText("2026년 9월")).toBeInTheDocument();
});

test("이미 오늘이 보이는 주면 오늘 버튼이 없다", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-08-31T09:00:00+09:00"));
  const weekStart = startOfWeek(new Date());
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={() => {}}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  expect(screen.queryByRole("button", { name: "오늘" })).not.toBeInTheDocument();
  vi.useRealTimers();
});

test("다른 주를 보고 있으면 오늘 버튼이 오늘이 있는 주로 되돌린다", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-10T09:00:00+09:00"));
  const today = startOfWeek(new Date());
  /* 오늘이 있는 주에서 두 주 전으로 옮겨온 상태를 흉내 낸다. */
  const weekStart = new Date(today);
  weekStart.setDate(weekStart.getDate() - 14);
  const onWeekChange = vi.fn();
  const user = userEvent.setup();
  render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={onWeekChange}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  await user.click(screen.getByRole("button", { name: "오늘" }));
  const forwardedTo = onWeekChange.mock.calls[0][0] as Date;
  expect(forwardedTo.getTime()).toBe(today.getTime());
  vi.useRealTimers();
});

test("왼쪽으로 스와이프하면 다음 주로 넘어간다", () => {
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  const onWeekChange = vi.fn();
  const { container } = render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={onWeekChange}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  const grid = daysGrid(container);
  fireEvent.touchStart(grid, { touches: [{ clientX: 200 }] });
  fireEvent.touchEnd(grid, { changedTouches: [{ clientX: 100 }] });

  expect(onWeekChange).toHaveBeenCalledTimes(1);
  const forwardedTo = onWeekChange.mock.calls[0][0] as Date;
  expect(forwardedTo.getTime() - weekStart.getTime()).toBe(7 * 24 * 60 * 60 * 1000);
});

test("오른쪽으로 스와이프하면 지난 주로 넘어간다", () => {
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  const onWeekChange = vi.fn();
  const { container } = render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={onWeekChange}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  const grid = daysGrid(container);
  fireEvent.touchStart(grid, { touches: [{ clientX: 100 }] });
  fireEvent.touchEnd(grid, { changedTouches: [{ clientX: 200 }] });

  expect(onWeekChange).toHaveBeenCalledTimes(1);
  const backedTo = onWeekChange.mock.calls[0][0] as Date;
  expect(weekStart.getTime() - backedTo.getTime()).toBe(7 * 24 * 60 * 60 * 1000);
});

test("살짝만 스치면 주가 안 바뀐다 — 날짜 탭과 헷갈리지 않는다", () => {
  const weekStart = startOfWeek(new Date("2026-08-31T09:00:00+09:00"));
  const onWeekChange = vi.fn();
  const { container } = render(
    <ScheduleCalendarStrip
      weekStart={weekStart}
      onWeekChange={onWeekChange}
      selectedDateKey={null}
      onSelectDate={() => {}}
      markedDateKeys={new Set()}
      isEn={false}
    />,
  );

  const grid = daysGrid(container);
  fireEvent.touchStart(grid, { touches: [{ clientX: 200 }] });
  fireEvent.touchEnd(grid, { changedTouches: [{ clientX: 190 }] });

  expect(onWeekChange).not.toHaveBeenCalled();
});

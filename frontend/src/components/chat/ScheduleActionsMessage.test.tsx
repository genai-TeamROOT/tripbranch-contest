/*
 * 일정 결과에 딸린 재편성 버튼 메시지. 일정 카드에서 갈라져 나오면서 이 파일이
 * 버튼 동작을 맡는다.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ScheduleActionsMessage } from "./ScheduleActionsMessage";

it("일정이 있으면 다른 코스 보기를 내고 범위 넓히기는 내지 않는다", () => {
  render(
    <ScheduleActionsMessage
      hasNoSchedule={false}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "다른 코스 보기" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /범위 넓혀서/ })).not.toBeInTheDocument();
});

it("일정을 못 짠 턴이면 범위 넓히기만 낸다", async () => {
  const user = userEvent.setup();
  const onRelaxRadius = vi.fn();
  render(
    <ScheduleActionsMessage
      hasNoSchedule
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={onRelaxRadius}
    />,
  );

  expect(screen.queryByRole("button", { name: "다른 코스 보기" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "검색 범위 넓혀서 다시 찾기" }));

  expect(onRelaxRadius).toHaveBeenCalled();
});

it("불러오는 중에는 버튼을 잠근다", () => {
  render(
    <ScheduleActionsMessage
      hasNoSchedule={false}
      isLoading
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "불러오는 중..." })).toBeDisabled();
});

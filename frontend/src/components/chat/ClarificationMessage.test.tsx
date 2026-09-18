/* 되묻기 버튼 렌더링/클릭/로딩 비활성화를 검증한다. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import type { ClarificationOption } from "../../types";
import { ClarificationMessage } from "./ClarificationMessage";

const options: ClarificationOption[] = [
  { id: "schedule_continue", label: "일정 다시 짜기", resolved_intent: "SCHEDULE" },
  { id: "recommend_only", label: "장소만 추천받기", resolved_intent: "RECOMMEND" },
];

it("문구와 버튼을 모두 렌더링한다", () => {
  render(
    <ClarificationMessage
      text="이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"
      options={options}
      isLoading={false}
      onSelectOption={vi.fn()}
    />,
  );

  expect(
    screen.getByText("이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "일정 다시 짜기" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "장소만 추천받기" })).toBeInTheDocument();
});

it("버튼 클릭 시 id와 label을 그대로 콜백에 넘긴다", async () => {
  const user = userEvent.setup();
  const onSelectOption = vi.fn();
  render(
    <ClarificationMessage
      text="이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"
      options={options}
      isLoading={false}
      onSelectOption={onSelectOption}
    />,
  );

  await user.click(screen.getByRole("button", { name: "장소만 추천받기" }));

  expect(onSelectOption).toHaveBeenCalledWith("recommend_only", "장소만 추천받기");
});

it("isLoading이면 버튼을 비활성화한다", () => {
  render(
    <ClarificationMessage
      text="이어서 일정을 다시 짜드릴까요, 아니면 장소만 추천해드릴까요?"
      options={options}
      isLoading
      onSelectOption={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "일정 다시 짜기" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "장소만 추천받기" })).toBeDisabled();
});

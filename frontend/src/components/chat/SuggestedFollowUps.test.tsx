/* 후속 질문 버튼의 렌더링·클릭·로딩 비활성화를 검증한다. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SuggestedFollowUps } from "./SuggestedFollowUps";

const suggestions = ["여기 주차되나요?", "이 근처 카페도 알려줘"];

it("제안 문구를 버튼으로 모두 렌더링한다", () => {
  render(
    <SuggestedFollowUps
      suggestions={suggestions}
      isLoading={false}
      onSelect={vi.fn()}
      language="ko"
    />,
  );

  expect(screen.getByRole("button", { name: "여기 주차되나요?" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "이 근처 카페도 알려줘" })).toBeInTheDocument();
});

it("클릭하면 문구를 그대로 콜백에 넘긴다", async () => {
  /* 되묻기 버튼과 달리 id가 없다 — 이 문구가 곧 사용자 발화가 된다. */
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(
    <SuggestedFollowUps
      suggestions={suggestions}
      isLoading={false}
      onSelect={onSelect}
      language="ko"
    />,
  );

  await user.click(screen.getByRole("button", { name: "여기 주차되나요?" }));

  expect(onSelect).toHaveBeenCalledWith("여기 주차되나요?");
});

it("제안이 없으면 아무것도 그리지 않는다", () => {
  const { container } = render(
    <SuggestedFollowUps suggestions={[]} isLoading={false} onSelect={vi.fn()} language="ko" />,
  );

  expect(container).toBeEmptyDOMElement();
});

it("응답을 기다리는 동안에는 버튼을 비활성화한다", () => {
  render(
    <SuggestedFollowUps suggestions={suggestions} isLoading onSelect={vi.fn()} language="ko" />,
  );

  expect(screen.getByRole("button", { name: "여기 주차되나요?" })).toBeDisabled();
});


it("제안을 한 줄에 하나씩 세로로 쌓는다", () => {
  /* 가로로 흘리면 문구 길이에 따라 두 개가 붙었다 떨어졌다 해서
     줄 수가 제안 개수와 어긋난다. */
  render(
    <SuggestedFollowUps
      suggestions={suggestions}
      isLoading={false}
      onSelect={vi.fn()}
      language="ko"
    />,
  );

  const group = screen.getByRole("group", { name: "이어서 물어볼 만한 질문" });

  expect(group).toHaveClass("flex-col");
  expect(group).not.toHaveClass("flex-wrap");
});

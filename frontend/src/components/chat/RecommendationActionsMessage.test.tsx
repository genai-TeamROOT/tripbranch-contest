/*
 * 추천 결과에 딸린 동작 버튼 메시지. 카드에서 갈라져 나온 뒤로 이 파일이 버튼
 * 동작을 맡는다(전환 버튼 테스트는 RecommendationResultMessage.test.tsx에서 옮겨왔다).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import type { TravelOriginToggle } from "../../types";
import { RecommendationActionsMessage } from "./RecommendationActionsMessage";

function toggle(overrides: Partial<TravelOriginToggle> = {}): TravelOriginToggle {
  return {
    alternative_origin: "search_center",
    alternative_origin_name: "안국역",
    ...overrides,
  };
}

it("결과가 있으면 다른 장소 보기를 내고 반경 확대는 내지 않는다", () => {
  render(
    <RecommendationActionsMessage
      hasNoResults={false}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: "다른 장소 보기" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /반경 넓혀서/ })).not.toBeInTheDocument();
});

it("결과가 없으면 반경 넓히기를 내고 다른 장소 보기는 내지 않는다", () => {
  render(
    <RecommendationActionsMessage
      hasNoResults
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
    />,
  );

  expect(
    screen.getByRole("button", { name: "검색 반경 넓혀서 다시 찾기 (+0.5km)" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "다른 장소 보기" })).not.toBeInTheDocument();
});

it("travelOriginToggle이 없으면 전환 버튼을 렌더링하지 않는다", () => {
  render(
    <RecommendationActionsMessage
      hasNoResults={false}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
    />,
  );

  expect(screen.queryByText(/기준으로 다시 보기/)).not.toBeInTheDocument();
});

it("travelOriginToggle이 있으면 대상 이름을 딴 전환 버튼을 렌더링하고 클릭 시 그대로 넘긴다", async () => {
  const user = userEvent.setup();
  const onToggleTravelOrigin = vi.fn();
  render(
    <RecommendationActionsMessage
      hasNoResults={false}
      travelOriginToggle={toggle()}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
      onToggleTravelOrigin={onToggleTravelOrigin}
    />,
  );

  await user.click(screen.getByRole("button", { name: "안국역 기준으로 다시 보기" }));

  expect(onToggleTravelOrigin).toHaveBeenCalledWith(toggle());
});

it("결과가 0건이어도 travelOriginToggle이 있으면 반경 확대 버튼과 함께 전환 버튼을 보여준다", () => {
  render(
    <RecommendationActionsMessage
      hasNoResults
      travelOriginToggle={toggle({ alternative_origin_name: "혜화역" })}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
      onToggleTravelOrigin={() => {}}
    />,
  );

  expect(
    screen.getByRole("button", { name: "검색 반경 넓혀서 다시 찾기 (+0.5km)" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "혜화역 기준으로 다시 보기" })).toBeInTheDocument();
});

it("영어 화면에서는 버튼 문구를 영어로 표시한다", () => {
  render(
    <RecommendationActionsMessage
      hasNoResults={false}
      travelOriginToggle={toggle({ alternative_origin_name: "Myeongdong" })}
      isLoading={false}
      onRequestMore={() => {}}
      onRelaxRadius={() => {}}
      onToggleTravelOrigin={() => {}}
      language="en"
    />,
  );

  expect(screen.getByRole("button", { name: "Show more places" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "View results based on Myeongdong" }),
  ).toBeInTheDocument();
});

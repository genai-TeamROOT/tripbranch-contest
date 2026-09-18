/*
 * 역할: 운영 패널 좌측 메뉴의 현재 탭 표시와 진행 표시를 검증한다.
 * 입력: 지금 탭과 갱신 진행 여부.
 * 출력: aria-current, 클릭 콜백, 진행 점에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OpsNav } from "./OpsNav";

describe("OpsNav", () => {
  it("지금 탭을 aria-current로 알린다", () => {
    render(<OpsNav tab="sync" syncRunning={false} onSelect={() => {}} />);

    expect(screen.getByRole("button", { name: /데이터 갱신/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("button", { name: /데이터 관찰/ })).not.toHaveAttribute("aria-current");
  });

  it("메뉴를 누르면 그 탭을 돌려준다", async () => {
    const onSelect = vi.fn();
    render(<OpsNav tab="observe" syncRunning={false} onSelect={onSelect} />);

    await userEvent.click(screen.getByRole("button", { name: /데이터 갱신/ }));

    expect(onSelect).toHaveBeenCalledWith("sync");
  });

  it("갱신이 도는 동안 다른 탭에 있어도 보이게 점을 찍는다", () => {
    // 전 구 순회는 25개 구를 하나씩 도느라 오래 걸려서, 탭을 옮겨두고 잊기 쉽다.
    const { rerender } = render(<OpsNav tab="observe" syncRunning={false} onSelect={() => {}} />);
    expect(screen.queryByLabelText("갱신 진행 중")).toBeNull();

    rerender(<OpsNav tab="observe" syncRunning onSelect={() => {}} />);
    expect(screen.getByLabelText("갱신 진행 중")).toBeInTheDocument();
  });
});

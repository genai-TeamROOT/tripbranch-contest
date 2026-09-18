/*
 * 역할: 대기 중 문구가 (1) 시간이 지나면 바뀌고 (2) 실제 progress 단계를 따라가는지
 * 검증한다. 카드·체크리스트·경과 시간을 걷어낸 뒤로는 "문구 한 줄"이 전부라,
 * 그 한 줄이 멈춰 있지 않은지가 이 컴포넌트의 전부다.
 */

import { render, screen } from "@testing-library/react";
import { act } from "react";
import { expect, it, vi } from "vitest";

import { AgentProgressMessage } from "./AgentProgressMessage";

function statusText() {
  return screen.getByRole("status").textContent;
}

it("시간이 지나면 안내 문구가 계속 바뀐다", () => {
  vi.useFakeTimers();
  render(<AgentProgressMessage />);

  const seen = new Set<string | null>([statusText()]);
  for (let i = 0; i < 5; i += 1) {
    act(() => {
      vi.advanceTimersByTime(1_800);
    });
    seen.add(statusText());
  }

  // 한 단계에 머무를 때도 문구가 돌아가야 "멈춘 화면"으로 보이지 않는다.
  expect(seen.size).toBeGreaterThan(1);
  vi.useRealTimers();
});

it("박스·체크리스트·초시계 없이 문구 한 줄만 보여준다", () => {
  render(<AgentProgressMessage />);

  const status = screen.getByRole("status");
  expect(status.tagName).toBe("P");
  expect(status.querySelector("ol")).toBeNull();
  expect(status.textContent).not.toMatch(/초 경과/);
});

it("실제 progress 이벤트가 있으면 그 단계의 문구를 보여준다", () => {
  vi.useFakeTimers();
  render(
    <AgentProgressMessage
      schedulePlanning
      progress={{
        stage: "scheduling",
        message: "장소 순서를 계산하고 있어요.",
        elapsed_ms: 12_000,
      }}
    />,
  );

  // 경과 시간(12초)만 보면 가상 회전은 이미 마지막 단계로 갔겠지만,
  // 실제 progress가 "scheduling"을 가리키면 그 단계 문구에 머문다.
  const schedulingLines = ["지도를 접었다 폈다 하는 중…", "몇 시에 어디 있을지 세어보는 중…"];
  expect(schedulingLines).toContain(statusText());

  act(() => {
    vi.advanceTimersByTime(1_800);
  });
  expect(schedulingLines).toContain(statusText());
  vi.useRealTimers();
});

it("영어 화면에서는 영어 문구가 나온다", () => {
  render(<AgentProgressMessage language="en" />);

  expect(statusText()).toMatch(/[A-Za-z]/);
  expect(statusText()).not.toMatch(/[가-힣]/);
});

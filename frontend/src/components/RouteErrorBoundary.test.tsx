/*
 * 역할: 화면 렌더가 실패했을 때 흰 화면 대신 안내와 새로고침이 나오는지 검증한다.
 * 입력: 렌더 중 던지는 자식 컴포넌트.
 * 출력: 안내 문구·새로고침 동작에 대한 assertion.
 *
 * 이 바운더리는 청크 로드 실패(React.lazy)를 잡으려고 있다. 실제 청크 실패를
 * jsdom에서 재현하기는 어려우므로, **렌더 중 던지는 것**으로 같은 경로를 태운다 —
 * React 입장에서 둘은 같은 오류다.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RouteErrorBoundary } from "./RouteErrorBoundary";

function Boom(): never {
  throw new Error("청크를 받지 못했다");
}

beforeEach(() => {
  // 바운더리가 componentDidCatch에서 콘솔에 남기고, React도 따로 경고를 찍는다.
  // 테스트 출력이 오류로 뒤덮이지 않게 이 파일에서만 조용히 시킨다.
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("자식이 정상이면 그대로 그린다", () => {
  render(
    <RouteErrorBoundary>
      <p>정상 화면</p>
    </RouteErrorBoundary>,
  );

  expect(screen.getByText("정상 화면")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("자식이 던지면 흰 화면 대신 안내를 보여준다", () => {
  render(
    <RouteErrorBoundary>
      <Boom />
    </RouteErrorBoundary>,
  );

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("화면을 불러오지 못했어요.");
  expect(screen.getByRole("button", { name: "새로고침" })).toBeInTheDocument();
});

test("새로고침을 누르면 페이지를 다시 불러온다", async () => {
  const user = userEvent.setup();
  const reload = vi.fn();
  // jsdom의 location.reload는 구현이 없다 — 호출 여부만 본다.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload },
  });

  render(
    <RouteErrorBoundary>
      <Boom />
    </RouteErrorBoundary>,
  );
  await user.click(screen.getByRole("button", { name: "새로고침" }));

  expect(reload).toHaveBeenCalledOnce();
});

test("오류 원인을 콘솔에 남긴다", () => {
  render(
    <RouteErrorBoundary>
      <Boom />
    </RouteErrorBoundary>,
  );

  // 안내만 보여주고 끝내면 무엇이 깨졌는지 알 방법이 없다.
  const logged = vi.mocked(console.error).mock.calls.flat();
  expect(logged.some((arg) => arg instanceof Error && arg.message === "청크를 받지 못했다")).toBe(
    true,
  );
});

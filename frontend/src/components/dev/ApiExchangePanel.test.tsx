/*
 * 역할: API 요청·응답 패널의 접기/펼치기와 캡처 토글을 검증한다.
 * 입력: 마스킹된 교환 스냅샷.
 * 출력: 접힘 기본값, 펼침 시 요청→응답 순서, 토글 호출에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ApiExchangeSnapshot } from "../../api/dev";
import { ApiExchangePanel } from "./ApiExchangePanel";

const snapshot: ApiExchangeSnapshot = {
  enabled: true,
  capacity: 50,
  max_body_bytes: 32768,
  items: [
    {
      id: "ex-1",
      started_at: "2026-08-09T14:23:07+09:00",
      provider: "tour_api",
      operation: "areaBasedList2",
      method: "GET",
      url: "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
      query: { serviceKey: "***", areaCd: "11", pageNo: "1" },
      request_headers: { accept: "application/json" },
      request_body: null,
      request_body_truncated: false,
      status: "200",
      ok: true,
      latency_ms: 412,
      response_headers: { "content-type": "application/json" },
      response_body: '{"response":{"body":{"totalCount":844}}}',
      response_body_truncated: false,
      response_bytes: 40,
      error: null,
    },
  ],
};

function renderPanel(overrides: Partial<ApiExchangeSnapshot> = {}, handlers = {}) {
  const props = {
    snapshot: { ...snapshot, ...overrides },
    error: null,
    onToggleCapture: vi.fn(),
    onClear: vi.fn(),
    onRefresh: vi.fn(),
    ...handlers,
  };
  render(<ApiExchangePanel {...props} />);
  return props;
}

it("기본은 접혀 있고 본문이 보이지 않는다", () => {
  renderPanel();

  expect(screen.getByText("areaBasedList2")).toBeInTheDocument();
  expect(screen.getByText("200")).toBeInTheDocument();
  expect(screen.queryByText(/totalCount/)).not.toBeInTheDocument();
});

it("카카오맵 길찾기 provider 라벨을 표시한다", () => {
  renderPanel({
    items: [
      {
        ...snapshot.items[0],
        provider: "kakao_map",
        operation: "walk",
      },
    ],
  });

  expect(screen.getByText("카카오맵 길찾기")).toBeInTheDocument();
  expect(screen.getByText("walk")).toBeInTheDocument();
});

it("누르면 요청 다음에 응답이 순서대로 펼쳐진다", async () => {
  const user = userEvent.setup();
  renderPanel();

  await user.click(screen.getByRole("button", { expanded: false }));

  const request = screen.getByRole("heading", { name: "요청" });
  const response = screen.getByRole("heading", { name: "응답" });
  // DOM 순서가 곧 화면 순서다.
  expect(
    request.compareDocumentPosition(response) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByText(/totalCount/)).toBeInTheDocument();
});

it("마스킹된 값을 서버가 준 그대로 보여준다", async () => {
  const user = userEvent.setup();
  renderPanel();

  await user.click(screen.getByRole("button", { expanded: false }));

  // 프론트가 다시 가리지 않는다 — 마스킹 지점이 둘이면 어느 쪽이 막는지 흐려진다.
  expect(screen.getAllByText("***").length).toBeGreaterThan(0);
  expect(screen.getByText("11")).toBeInTheDocument();
  // 쿼리가 붙은 원문 URL은 애초에 오지 않는다.
  expect(screen.queryByText(/serviceKey=/)).not.toBeInTheDocument();
});

it("캡처가 꺼져 있으면 켜기 전 호출은 없다는 걸 안내한다", () => {
  renderPanel({ enabled: false, items: [] });

  expect(screen.getByText(/켜기 전에 오간 호출은 남아 있지 않아요/)).toBeInTheDocument();
});

it("캡처 체크박스를 누르면 토글을 요청한다", async () => {
  const user = userEvent.setup();
  const props = renderPanel({ enabled: false, items: [] });

  await user.click(screen.getByRole("checkbox", { name: "캡처" }));

  expect(props.onToggleCapture).toHaveBeenCalledWith(true);
});

it("잘린 본문은 원본 크기와 함께 알린다", async () => {
  const user = userEvent.setup();
  renderPanel({
    items: [
      {
        ...snapshot.items[0],
        response_body: "x".repeat(100),
        response_body_truncated: true,
        response_bytes: 40960,
      },
    ],
  });

  await user.click(screen.getByRole("button", { expanded: false }));

  expect(screen.getByText(/앞부분만 보관했어요/)).toBeInTheDocument();
  // 응답 요약 줄과 잘림 안내 양쪽에 원본 크기가 나온다.
  expect(screen.getAllByText(/40\.0KB/).length).toBeGreaterThan(0);
});

it("응답을 못 받은 호출은 오류를 보여준다", async () => {
  const user = userEvent.setup();
  renderPanel({
    items: [
      {
        ...snapshot.items[0],
        ok: false,
        status: "ConnectTimeout",
        error: "ConnectTimeout",
        response_body: null,
        response_bytes: 0,
        response_headers: {},
      },
    ],
  });

  await user.click(screen.getByRole("button", { expanded: false }));

  const response = screen.getByRole("heading", { name: "응답" }).parentElement;
  expect(within(response as HTMLElement).getByText(/ConnectTimeout/)).toBeInTheDocument();
});

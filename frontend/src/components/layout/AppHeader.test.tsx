/*
 * 역할: AppHeader의 햄버거·위치 pill을 검증한다.
 * 입력: location prop(utils/locationChip 모델), 셸 드로어 컨텍스트.
 * 출력: 헤더(햄버거)가 항상 있고 pill은 모델이 있을 때만 그려진다. 출발지와 검색
 *   기준이 다르면 둘 다 그려진다. pill을 누르면 위치 설정으로 이동한다.
 *   뒤로가기 화살표는 폭에 상관없이 그리지 않는다(2026-09-07). keepStrip은
 *   헤더 띠를 접을지만 정한다.
 *
 * AppHeader는 useAppShell()로 드로어를 열기 때문에 Provider 밖에서 렌더하면
 * 던진다 — 모든 케이스를 AppShellProvider로 감싼다.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { buildLocationChipModel } from "../../utils/locationChip";
import { AppHeader } from "./AppHeader";
import { AppShellProvider } from "./AppShellContext";

/* AppShellContext는 값 타입을 export하지 않는다 — 테스트에 필요한 모양만 적는다. */
interface ShellValue {
  drawerOpen: boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
}

/* 화면이 넘기는 것과 같은 경로로 모델을 만든다 — 손으로 지어내면 조립 규칙이
   바뀌어도 이 테스트는 그대로 통과한다. null이면 pill 자체가 없는 경우다. */
function chipFor(
  settings: { origin: string | null; center: string | null } | null,
  hasDeviceLocation = true,
) {
  return settings === null ? null : buildLocationChipModel(settings, null, hasDeviceLocation);
}

/*
 * 폭 분기는 matchMedia로 판정한다(useIsDesktopSidebar). jsdom에는 레이아웃이 없어
 * 실제 창 크기로는 바뀌지 않으므로, 여기서 응답을 직접 정한다.
 */
function setSidebarVisible(visible: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: visible,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderHeader(
  settings: { origin: string | null; center: string | null } | null,
  shell?: Partial<ShellValue>,
  extra?: { routes?: ReactNode; keepStrip?: boolean; hasDeviceLocation?: boolean },
) {
  const value: ShellValue | undefined = shell
    ? { drawerOpen: false, openDrawer: vi.fn(), closeDrawer: vi.fn(), ...shell }
    : undefined;

  return render(
    <AppShellProvider value={value}>
      <MemoryRouter initialEntries={["/chat"]}>
        <AppHeader
          location={chipFor(settings, extra?.hasDeviceLocation ?? true)}
          keepStrip={extra?.keepStrip}
        />
        {extra?.routes}
      </MemoryRouter>
    </AppShellProvider>,
  );
}

test("위치 모델이 없어도 헤더(햄버거)는 그려진다", () => {
  renderHeader(null);

  expect(screen.getByRole("button", { name: "메뉴 열기" })).toBeInTheDocument();
  // "근처"는 라벨 문구에서 뺐다 — 모델이 없으면 pill 자체가 없다.
  expect(screen.queryByText(/근처/)).not.toBeInTheDocument();
});

test("위치 모델이 있으면 pill을 그린다", () => {
  renderHeader({ origin: null, center: "경복궁 근처" });

  expect(screen.getByText("경복궁 근처")).toBeInTheDocument();
});

test("출발지와 검색 기준이 다르면 둘 다 그린다", () => {
  /* 하나만 보여주면 카드의 이동시간을 어디서 쟀는지가 화면에서 사라진다(D-067). */
  renderHeader({ origin: "안국역", center: "광화문역" });

  expect(screen.getByText("안국역")).toBeInTheDocument();
  expect(screen.getByText("광화문역")).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "위치 설정으로 이동 (안국역에서 출발, 광화문역 주변에서 검색)",
    }),
  ).toBeInTheDocument();
});

test("둘이 같은 곳이면 한 번만 그린다", () => {
  /* "안국역 → 안국역"은 같은 이름을 두 번 쓰는 것이라 읽는 사람이 얻는 게 없다. */
  renderHeader({ origin: "안국역", center: "안국역" });

  expect(screen.getAllByText("안국역")).toHaveLength(1);
});

test("햄버거를 누르면 셸 드로어를 연다", async () => {
  const user = userEvent.setup();
  const openDrawer = vi.fn();
  renderHeader(null, { openDrawer });

  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));

  expect(openDrawer).toHaveBeenCalledOnce();
});

/*
 * **여닫이다**(2026-09-07). 예전에는 openDrawer 만 불러서 열어 둔 채로 다시
 * 누르면 아무 일도 안 났다.
 *
 * 두 상태를 짝으로 잠근다 — 여는 쪽만 보면 예전 코드로도 통과한다.
 */
test("열려 있을 때 햄버거를 누르면 닫는다", async () => {
  const user = userEvent.setup();
  const openDrawer = vi.fn();
  const closeDrawer = vi.fn();
  renderHeader(null, { drawerOpen: true, openDrawer, closeDrawer });

  await user.click(screen.getByRole("button", { name: "메뉴 닫기" }));

  expect(closeDrawer).toHaveBeenCalledOnce();
  /* 닫고 곧바로 다시 여는 일이 없어야 한다. */
  expect(openDrawer).not.toHaveBeenCalled();
});

/* 무엇이 열려 있는지 소리로도 알 수 있어야 한다. */
test("햄버거가 열림 여부를 알린다", () => {
  const { unmount } = renderHeader(null, { drawerOpen: false });
  expect(screen.getByRole("button", { name: "메뉴 열기" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  unmount();

  renderHeader(null, { drawerOpen: true });
  expect(screen.getByRole("button", { name: "메뉴 닫기" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

/*
 * pill은 단순 표시가 아니라 위치 설정으로 가는 입구다. **시트로 열지 않는다**
 * (2026-09-07) — 위치 설정이 취향 설정과 같은 전체 페이지로 바뀌면서
 * backgroundLocation을 실어 보낼 이유가 없어졌다. 예전에는 이 값이 "/chat"
 * (여는 시점의 배경 화면)이어야 했는데, 지금은 반대로 **없어야** 맞다 —
 * 남아 있으면 시트로 되돌아간 것이라 이 값으로 잠근다.
 */
test("위치 pill을 누르면 위치 설정으로 이동한다", async () => {
  const user = userEvent.setup();

  function LocationProbe() {
    const location = useLocation();
    const background = (location.state as { backgroundLocation?: { pathname: string } } | null)
      ?.backgroundLocation;
    return <div data-testid="probe">{background?.pathname ?? "no-background"}</div>;
  }

  renderHeader({ origin: null, center: "경복궁 근처" }, undefined, {
    routes: (
      <Routes>
        <Route path="/location" element={<LocationProbe />} />
        <Route path="*" element={null} />
      </Routes>
    ),
  });

  await user.click(
    screen.getByRole("button", {
      name: "위치 설정으로 이동 (현재 위치에서 출발, 경복궁 근처 주변에서 검색)",
    }),
  );

  expect(screen.getByTestId("probe")).toHaveTextContent("no-background");
});

/*
 * **뒤로가기 화살표는 폭에 상관없이 그리지 않는다**(2026-09-07). 예전에는
 * 사이드바가 없는 좁은 폭에서만 그렸는데, 화살표가 브라우저/제스처 뒤로가기와
 * 같은 일을 두 번 해서 뺐다.
 *
 * 두 폭을 짝으로 잠근다. 한쪽만 보면 좁은 폭에서 되살아나도 못 잡는다.
 */
test("띠를 남기는 화면에서도 뒤로가기 화살표는 그리지 않는다(좁은 폭)", () => {
  setSidebarVisible(false);
  renderHeader(null, undefined, { keepStrip: true });

  expect(screen.queryByRole("button", { name: "뒤로가기" })).not.toBeInTheDocument();
});

test("띠를 남기는 화면에서도 뒤로가기 화살표는 그리지 않는다(넓은 폭)", () => {
  setSidebarVisible(true);
  renderHeader(null, undefined, { keepStrip: true });

  expect(screen.queryByRole("button", { name: "뒤로가기" })).not.toBeInTheDocument();
});

/*
 * 그릴 버튼이 없어도 띠는 남는다. 접었더니 제목이 화면 맨 위에 붙어 위쪽 여백이
 * 사라졌었다(2026-09-06 사용자 확인) — 이 띠는 본문이 시작하기 전의 여백이다.
 *
 * 클래스로 단언하는 이유는 **접는 수단이 CSS(md:hidden)**여서다. jsdom에는
 * 레이아웃이 없어 엘리먼트는 어느 쪽이든 그려지고, 달라지는 것은 클래스뿐이다.
 * container의 첫 엘리먼트가 헤더의 바깥 div다(위 Provider들은 DOM을 만들지 않는다).
 */
test("keepStrip이면 그릴 버튼이 없어도 헤더 띠는 남는다", () => {
  setSidebarVisible(true);
  const { container } = renderHeader(null, undefined, { keepStrip: true });

  expect(container.firstElementChild).not.toHaveClass("md:hidden");
});

/* 위치 pill도 keepStrip도 없는 화면에서는 데스크톱에 그릴 것이 정말 없다. */
test("보여줄 것이 아무것도 없으면 데스크톱에서 헤더를 접는다", () => {
  setSidebarVisible(true);
  const { container } = renderHeader(null);

  expect(container.firstElementChild).toHaveClass("md:hidden");
});

/*
 * 좌표를 아직 못 받았을 때의 점.
 *
 * 깜빡이는 초록은 "지금 GPS를 쓰는 중"이라는 뜻이라, 좌표가 없는데 붙으면 화면이
 * 사실과 다른 말을 한다. 실제로 생기는 상태다 — 새 대화(RESET)는 좌표만 지우고
 * 출발지·검색지는 sessionStorage에 남는다.
 */
test("좌표를 못 받았으면 깜빡이는 초록 점을 붙이지 않는다", () => {
  const { container } = renderHeader({ origin: null, center: "광화문역" }, undefined, {
    hasDeviceLocation: false,
  });

  expect(container.querySelector(".animate-ping")).toBeNull();
  expect(container.querySelector(".bg-muted")).not.toBeNull();
});

test("좌표가 있으면 깜빡이는 초록 점을 붙인다", () => {
  const { container } = renderHeader({ origin: null, center: "광화문역" });

  expect(container.querySelector(".animate-ping")).not.toBeNull();
});

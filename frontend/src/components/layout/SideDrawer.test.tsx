/*
 * 역할: 모바일 푸시 드로어가 햄버거로 열리고, 본문 탭·내비게이션으로 닫히는지 검증한다.
 * 입력: 렌더된 App, 사용자 클릭.
 * 출력: aria-hidden/inert 토글과 닫힘 경로 두 가지에 대한 assertion.
 *
 * 이 드로어는 오버레이가 아니라 "푸시" 드로어다 — 항상 DOM에 있고 본문(.tb-shell)이
 * 오른쪽으로 밀려나며 드러난다. 그래서 닫기 ✕ 버튼이 따로 없고, 밀려난 본문을
 * 누르면 닫힌다(AppShell의 onClickCapture). 열림 여부는 DOM 존재가 아니라
 * aria-hidden/inert로만 드러나므로 그 속성으로 판정한다.
 *
 * jsdom은 미디어쿼리(md:hidden)를 적용하지 않아 데스크톱 사이드바와 모바일
 * 드로어가 항상 함께 DOM에 있다. 드로어 쪽은 aria-hidden 속성을 가진 루트로,
 * 데스크톱 사이드바(role=complementary)와 구분해서 찾는다.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";
import App from "../../App";
/* 맨 아래 규칙 가드가 쓴다. tsx 는 Vite 의 ?raw 로 받지만 **css 는 안 된다** —
   Vitest 가 CSS 임포트를 빈 문자열로 처리해서 ?raw 도 ''가 온다. 그래서 css 만
   파일에서 직접 읽는다(cwd 는 vite.config 가 있는 frontend/ 다). */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import drawerSource from "./SideDrawer.tsx?raw";

const cssSource = readFileSync(resolve(process.cwd(), "src/index.css"), "utf-8");

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  window.history.pushState({}, "", "/");
});

async function renderApp() {
  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });
}

/*
 * 드로어 루트는 SideDrawer가 그리는 유일한 [aria-hidden] 컨테이너다. 브랜드 표기를
 * 품고 있어 그것을 기준으로 거슬러 올라간다 — 클래스명에 기대지 않는다.
 */
function drawerRoot(): HTMLElement {
  const brands = screen.getAllByText("TripBranch");
  for (const brand of brands) {
    const root = brand.closest("[aria-hidden]");
    if (root) return root as HTMLElement;
  }
  throw new Error("드로어 루트를 찾지 못했다");
}

/** 밀려나는 본문 컨테이너(.tb-shell). 탭-투-클로즈 핸들러가 여기 붙는다. */
function shell(): HTMLElement {
  const node = document.querySelector(".tb-shell");
  if (!node) throw new Error("본문 셸을 찾지 못했다");
  return node as HTMLElement;
}

test("처음에는 드로어가 aria-hidden·inert 상태다", async () => {
  await renderApp();

  const root = drawerRoot();
  expect(root).toHaveAttribute("aria-hidden", "true");
  expect(root).toHaveAttribute("inert");
});

test("햄버거를 누르면 드로어가 열린다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));

  const root = drawerRoot();
  expect(root).toHaveAttribute("aria-hidden", "false");
  expect(root).not.toHaveAttribute("inert");
});

/*
 * **햄버거는 여닫이다**(2026-09-07). 열어 둔 채로 다시 눌러도 닫히지 않았다.
 *
 * 원인이 셸에 있어서 AppHeader 단위 테스트로는 안 잡힌다 — 그 버튼도 셸 안에
 * 있는데, 셸의 탭-투-클로즈(onClickCapture)가 버튼보다 **먼저** 돌아 닫고
 * 버튼이 다시 열었다. 둘이 서로를 무효화하는 것은 함께 그려야만 드러난다.
 */
test("햄버거를 다시 누르면 드로어가 닫힌다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));
  expect(drawerRoot()).toHaveAttribute("aria-hidden", "false");

  await user.click(screen.getByRole("button", { name: "메뉴 닫기" }));
  expect(drawerRoot()).toHaveAttribute("aria-hidden", "true");

  /* 한 번 더 — 닫은 뒤에도 다시 열려야 한다(한 방향으로만 굳지 않는다). */
  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));
  expect(drawerRoot()).toHaveAttribute("aria-hidden", "false");
});

/*
 * 이 드로어에는 닫기 ✕ 버튼이 없다. 밀려난 본문을 누르는 것이 유일한
 * "취소하고 돌아가기" 경로라, 이게 깨지면 모바일에서 드로어에 갇힌다.
 */
test("밀려난 본문을 누르면 드로어가 닫힌다", async () => {
  const user = userEvent.setup();
  await renderApp();
  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));
  expect(drawerRoot()).toHaveAttribute("aria-hidden", "false");

  /*
   * "추천 시작하기"는 입력이 비면 disabled라 클릭 이벤트가 아예 나지 않는다 —
   * 탭-투-클로즈를 확인하려면 활성 요소를 눌러야 한다. 컴포저 입력칸은 항상 활성이고
   * 누른다고 다른 일이 일어나지도 않는다.
   */
  await user.click(within(shell()).getByRole("textbox"));

  expect(drawerRoot()).toHaveAttribute("aria-hidden", "true");
});

test("드로어 안에서 메뉴를 누르면 이동하면서 닫힌다", async () => {
  const user = userEvent.setup();
  await renderApp();
  await user.click(screen.getByRole("button", { name: "메뉴 열기" }));

  const root = drawerRoot();
  const { getByRole } = within(root);
  await user.click(getByRole("button", { name: "취향 설정" }));

  expect(screen.getByText(/끌리시나요/)).toBeInTheDocument();
  expect(drawerRoot()).toHaveAttribute("aria-hidden", "true");
});

/*
 * 드로어의 왼쪽이 **창 끝이 아니라 본문 칼럼의 왼쪽**에 맞는지 지킨다.
 *
 * 증상: 641~767px 구간에서 닫혀 있는 드로어가 셸 왼쪽 여백으로 비쳤다. 셸은 그
 * 구간에서 최대 640px 짜리 가운데 칼럼인데 드로어는 `left: 0` 이라 둘의 왼쪽
 * 모서리가 어긋났다 — 767px 에서 63.5px 이 보였다(2026-09-06 실측, 고친 뒤 0px).
 *
 * **jsdom 으로는 이걸 잴 수 없다.** 레이아웃이 없어 getBoundingClientRect 가 전부
 * 0이고, index.css 는 테스트에서 로드조차 되지 않는다. 그래서 값 대신 **두 파일이
 * 한 규칙을 함께 쓰는지**를 본다 — 되돌릴 때 반드시 건드리게 되는 지점들이다.
 * 실제 픽셀은 브라우저에서 봐야 한다.
 */
test("드로어는 창 끝이 아니라 본문 칼럼 왼쪽에 붙는다", () => {

  /* left-0 을 되돌리면 다시 창 끝에 붙는다 — 그게 원래 버그였다. */
  expect(drawerSource).toContain("tb-drawer");
  expect(drawerSource).not.toMatch(/className="[^"]*\bleft-0\b/);

  /* 셸과 드로어가 같은 변수를 봐야 둘의 왼쪽이 함께 움직인다. 한쪽만 숫자로
     되돌리면 다시 어긋난다. */
  expect(cssSource).toContain("--tb-shell-max:");
  expect(cssSource).toMatch(/\.tb-shell\s*\{[^}]*max-width:\s*var\(--tb-shell-max\)/);
  expect(cssSource).toMatch(/\.tb-drawer\s*\{[^}]*var\(--tb-shell-max\)/);
});

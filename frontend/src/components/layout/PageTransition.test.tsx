/*
 * 역할: 화면 전환 래퍼가 애니메이션을 언제 다시 재생하는지 검증한다.
 * 호출 시점: vitest 실행 시.
 *
 * jsdom은 CSS 애니메이션을 실행하지 않는다. 그래서 "움직였는지"가 아니라
 * **애니메이션이 다시 걸릴 조건**(래퍼가 교체되는지)을 본다 — 실제로 어긋났던
 * 지점이 거기다. 키를 잘못 잡으면 시트를 열 때마다 뒤 화면이 다시 떠오르고
 * 상태까지 잃는다.
 */

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PageTransition } from "./PageTransition";

test("감싼 화면을 그대로 그리고 진입 애니메이션 클래스를 붙인다", () => {
  render(
    <PageTransition pathKey="/">
      <p>홈 화면</p>
    </PageTransition>,
  );

  const child = screen.getByText("홈 화면");
  expect(child.parentElement).toHaveClass("tb-page-enter");
});

/*
 * 셸 안 화면은 h-full로 셸 높이를 물려받는다. 래퍼가 높이를 이어주지 않으면
 * 화면이 접힌다(DESIGN_SYSTEM.md §5 "높이 체인 주의").
 */
test("셸 안 화면은 높이를 이어준다", () => {
  const { rerender } = render(
    <PageTransition pathKey="/" fullHeight>
      <p>셸 안</p>
    </PageTransition>,
  );
  expect(screen.getByText("셸 안").parentElement).toHaveClass("h-full");

  // 셸 밖(인증 화면)은 스스로 min-h-dvh로 늘어나므로 높이를 강제하지 않는다.
  rerender(
    <PageTransition pathKey="/login">
      <p>셸 밖</p>
    </PageTransition>,
  );
  expect(screen.getByText("셸 밖").parentElement).not.toHaveClass("h-full");
});

test("pathKey가 바뀌면 래퍼가 교체돼 애니메이션이 다시 재생된다", () => {
  const { rerender } = render(
    <PageTransition pathKey="/" fullHeight>
      <p data-testid="page">화면</p>
    </PageTransition>,
  );
  const first = screen.getByTestId("page").parentElement;

  rerender(
    <PageTransition pathKey="/chat" fullHeight>
      <p data-testid="page">화면</p>
    </PageTransition>,
  );

  expect(screen.getByTestId("page").parentElement).not.toBe(first);
});

/*
 * 시트가 열려도 기반 화면의 pathKey는 그대로다. 같은 키면 래퍼를 재사용해야
 * 한다 — 교체되면 뒤 화면이 다시 떠오르는 데다 다시 마운트되면서 스크롤
 * 위치와 화면 상태를 잃는다.
 */
test("pathKey가 그대로면 래퍼를 재사용한다", () => {
  const { rerender } = render(
    <PageTransition pathKey="/chat" fullHeight>
      <p data-testid="page">채팅</p>
    </PageTransition>,
  );
  const first = screen.getByTestId("page").parentElement;

  rerender(
    <PageTransition pathKey="/chat" fullHeight>
      <p data-testid="page">채팅</p>
    </PageTransition>,
  );

  expect(screen.getByTestId("page").parentElement).toBe(first);
});

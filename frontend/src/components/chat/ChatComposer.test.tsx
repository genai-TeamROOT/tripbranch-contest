/*
 * 역할: 응답 대기 중(disabled)의 중단 버튼과, 여러 줄 입력(Shift+Enter)을 검증한다.
 * 입력: disabled/onCancel prop 조합, 키 입력.
 * 출력: 중단 버튼 노출 여부·onCancel 호출, 전송 여부, 입력창 높이에 대한 assertion.
 * 근거: Figma node 28:235(Home — 응답 중 (중단 가능)).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ChatComposer } from "./ChatComposer";

/*
 * jsdom은 레이아웃을 하지 않아 scrollHeight가 언제나 0이다 — 그대로 두면 높이가
 * 늘어나는지 볼 수 없다. 실제 브라우저처럼 **줄 수에 비례하게** 흉내 낸다:
 * 한 줄 40px(버튼과 같은 높이), 줄이 늘 때마다 leading-6 만큼인 24px.
 */
const ONE_LINE = 40;
const LINE = 24;

beforeEach(() => {
  Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", {
    configurable: true,
    get(this: HTMLTextAreaElement) {
      const content = ONE_LINE + (this.value.split("\n").length - 1) * LINE;
      /*
       * **실제 scrollHeight는 지금 높이보다 작아지지 않는다** — 통이 크면 내용이
       * 짧아져도 통 높이가 그대로 나온다. 컴포저가 재기 전에 height를 auto로
       * 되돌리는 이유가 이것이다.
       *
       * 이 부분을 흉내 내지 않았더니 그 auto 되돌리기를 지워도 테스트가 전부
       * 통과했다(2026-09-06 되돌림 확인). 흉내가 부실하면 잠근 게 아니다.
       */
      const current = this.style.height.endsWith("px") ? Number.parseFloat(this.style.height) : 0;
      return Math.max(content, current);
    },
  });
});

afterEach(() => {
  Reflect.deleteProperty(HTMLTextAreaElement.prototype, "scrollHeight");
});

function composer() {
  return screen.getByRole("textbox") as HTMLTextAreaElement;
}

test("onCancel이 없으면 응답 대기 중에도 비활성화된 전송 버튼만 보인다", () => {
  render(<ChatComposer disabled onSubmit={vi.fn()} />);

  expect(screen.getByRole("button", { name: "보내기" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "중단" })).not.toBeInTheDocument();
});

test("onCancel이 있으면 응답 대기 중 전송 버튼 대신 중단 버튼이 뜬다", async () => {
  const user = userEvent.setup();
  const onCancel = vi.fn();
  render(<ChatComposer disabled onSubmit={vi.fn()} onCancel={onCancel} />);

  expect(screen.queryByRole("button", { name: "보내기" })).not.toBeInTheDocument();
  const cancelButton = screen.getByRole("button", { name: "중단" });
  await user.click(cancelButton);

  expect(onCancel).toHaveBeenCalledOnce();
});

test("대기 중이 아니면 onCancel이 있어도 전송 버튼이 그대로 보인다", () => {
  render(<ChatComposer disabled={false} onSubmit={vi.fn()} onCancel={vi.fn()} />);

  expect(screen.getByRole("button", { name: "보내기" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "중단" })).not.toBeInTheDocument();
});

/*
 * 줄바꿈은 Shift+Enter다. 그냥 Enter는 전송이라 — 두 줄짜리 요청을 쓰려던 사람이
 * 첫 줄만 보내게 된다.
 */
test("Shift+Enter는 전송하지 않고 줄을 바꾼다", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ChatComposer disabled={false} onSubmit={onSubmit} />);

  await user.type(composer(), "성수동 조용한 곳{Shift>}{Enter}{/Shift}비 와도 괜찮은 데로");

  expect(onSubmit).not.toHaveBeenCalled();
  expect(composer().value).toBe("성수동 조용한 곳\n비 와도 괜찮은 데로");
});

test("Enter는 그대로 전송한다", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ChatComposer disabled={false} onSubmit={onSubmit} />);

  await user.type(composer(), "성수동 조용한 곳{Enter}");

  expect(onSubmit).toHaveBeenCalledWith("성수동 조용한 곳");
});

test("줄을 바꿔 쓴 글은 줄바꿈까지 그대로 전송된다", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ChatComposer disabled={false} onSubmit={onSubmit} />);

  await user.type(composer(), "성수동{Shift>}{Enter}{/Shift}비 와도{Enter}");

  expect(onSubmit).toHaveBeenCalledWith("성수동\n비 와도");
});

/*
 * **한글을 치는 도중의 Enter는 전송이 아니다.** 입력기는 조합을 확정할 때 Enter를
 * 쓴다 — "안녕"의 마지막 글자를 확정하려고 누른 Enter가 전송이 되면 쓰던 중에
 * 글이 나가버린다.
 *
 * userEvent로는 조합을 흉내 낼 수 없어(입력기 이벤트를 만들지 않는다) 이벤트를
 * 직접 쏜다.
 */
test("한글 조합 중 Enter는 전송하지 않는다", () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ChatComposer disabled={false} onSubmit={onSubmit} />);

  fireEvent.change(composer(), { target: { value: "안녕" } });
  fireEvent.keyDown(composer(), { key: "Enter", isComposing: true });

  expect(onSubmit).not.toHaveBeenCalled();
});

/* 늘어난 입력창은 아래가 고정이므로 **위로** 자란다(sticky bottom-0). */
test("줄이 늘어나면 입력창이 그만큼 높아진다", async () => {
  const user = userEvent.setup();
  render(<ChatComposer disabled={false} onSubmit={vi.fn()} />);

  expect(composer().style.height).toBe(`${ONE_LINE}px`);

  await user.type(composer(), "한 줄{Shift>}{Enter}{/Shift}두 줄{Shift>}{Enter}{/Shift}세 줄");

  expect(composer().style.height).toBe(`${ONE_LINE + LINE * 2}px`);
});

/*
 * 보내고 나면 한 줄로 돌아와야 한다. **scrollHeight는 지금 높이보다 작아지지
 * 않아서**, 재는 앞에 height를 auto로 되돌리지 않으면 세 줄짜리 통이 빈 채로 남는다.
 */
test("보내고 나면 입력창이 한 줄로 돌아온다", async () => {
  const user = userEvent.setup();
  render(<ChatComposer disabled={false} onSubmit={vi.fn().mockResolvedValue(undefined)} />);

  await user.type(composer(), "한 줄{Shift>}{Enter}{/Shift}두 줄{Enter}");

  expect(composer().value).toBe("");
  expect(composer().style.height).toBe(`${ONE_LINE}px`);
});

/*
 * 최댓값은 CSS가 잡는다(max-h-40 = 160px = 6줄). jsdom에는 레이아웃이 없어 실제로
 * 멈추는지는 계산으로 확인할 수 없다 — 클래스가 붙어 있는 것까지만 잠근다.
 * 실제로 멈추는지는 화면에서 봐야 한다.
 */
test("입력창은 무한정 자라지 않도록 최대 높이와 스크롤을 갖는다", () => {
  render(<ChatComposer disabled={false} onSubmit={vi.fn()} />);

  expect(composer().className).toContain("max-h-40");
  expect(composer().className).toContain("overflow-y-auto");
});

/*
 * **한 번만 재면 모자란다.** 처음 잰 뒤에 글이 다시 접히면(웹폰트가 늦게 오거나
 * 폭이 바뀌면) 그 높이는 낡은 값이 되고, 넘친 만큼이 안쪽 스크롤로 남는다 —
 * 빈 입력창에 스크롤이 생기는 것이 그 모습이다.
 *
 * 폭 변화만 잠근다. 웹폰트 쪽(document.fonts.ready)은 jsdom 에 폰트 로딩이
 * 아예 없어 흉내가 진짜를 못 닮는다 — 없는 것을 흉내 내 통과시키느니 잠그지
 * 않는다. 그쪽은 실제 브라우저에서 확인했다.
 */
test("폭이 바뀌면 높이를 다시 잰다", async () => {
  const user = userEvent.setup();
  render(<ChatComposer disabled={false} onSubmit={vi.fn()} />);

  const box = screen.getByRole("textbox") as HTMLTextAreaElement;
  await user.type(box, "한 줄{Shift>}{Enter}{/Shift}두 줄{Shift>}{Enter}{/Shift}세 줄");
  expect(box.style.height).toBe(`${ONE_LINE + LINE * 2}px`);

  /* 좁아져서 줄이 늘어난 상황을 흉내 낸다 — 모의 scrollHeight 가 줄 수를 보므로
     값 자체를 늘려 "다시 재면 달라지는" 상태를 만든다. */
  box.value = "한 줄\n두 줄\n세 줄\n네 줄";
  window.dispatchEvent(new Event("resize"));

  expect(box.style.height).toBe(`${ONE_LINE + LINE * 3}px`);
});

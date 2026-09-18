/*
 * 역할: 약관 모달이 본문을 보여주고 닫히는지, 그리고 **지킬 수 없는 문장을 적지
 *   않는지** 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { TermsModal } from "./TermsModal";

test("두 문서의 조항을 보여준다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  expect(screen.getByRole("dialog")).toHaveAccessibleName("이용약관 및 개인정보처리방침");
  for (const 조항 of [
    "제1조 (서비스의 내용)",
    "제2조 (정보의 정확성)",
    "제5조 (책임의 제한)",
    "제1조 (수집하는 항목)",
    "제2조 (수집·이용 목적)",
    "제3조 (보유 및 이용 기간)",
    "제4조 (제3자 제공)",
    "제5조 (처리 위탁)",
    "제6조 (이용자의 권리)",
    "제7조 (개인정보 보호책임자와 문의)",
  ]) {
    expect(screen.getByText(조항)).toBeInTheDocument();
  }
});

/*
 * 비밀번호를 "안전하게 보관"처럼 뭉뚱그리지 않고 **어떻게 다루는지** 적는지 본다.
 * Supabase가 복호화할 수 없는 형태로 저장하므로 사실이며, 이 꼬리가 떨어지면
 * 수집 항목에 "비밀번호"만 남아 무엇을 하는지 알 수 없어진다.
 */
test("비밀번호를 어떻게 다루는지 적는다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  expect(screen.getByRole("dialog").textContent ?? "").toMatch(
    /비밀번호 — 되돌릴 수 없는 형태로 저장/,
  );
});

/*
 * 만 14세 미만 조는 방침 범위 밖이다. 다시 쓸 일이 생겨도 "만 14세 이상만"이라고
 * 적어서는 안 된다 — SignupPage에 나이 입력이 없어 막지 못하는 약속이기 때문이다.
 */
test("막지 못하는 나이 제한을 약속하지 않는다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  expect(screen.getByRole("dialog").textContent ?? "").not.toMatch(/만 14세 이상만/);
});

/*
 * 이 파일에서 가장 중요한 테스트다. Figma 시안에는 조항 본문이 채워져 있지만
 * 그 문장이 코드가 하는 일과 어긋난다. 지키지 못하는 문장을 약관에 적는 것이
 * 안 적는 것보다 나쁘다.
 *
 * 앞쪽은 시안이 실제로 적었던 문장이고, 뒤쪽은 **지금 코드가 보장하지 못하는
 * 약속**이다 — 국내 처리는 Gemini가 AI Studio 경로라 보장할 수 없다.
 *
 * 보관 기간을 금지 목록에서 뺐다(2026-09-15). 3개월을 적기로 정했기 때문이다.
 * 대신 아래 "보유 기간을 코드가 지울 수 있는 만큼만 적는다"가 그 자리를 맡는다.
 */
test("지킬 수 없는 문장을 적지 않는다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  const body = screen.getByRole("dialog").textContent ?? "";
  for (const 지킬수_없는_약속 of [
    /안전하게 보관/,
    /목적으로만 사용/,
    /관련 법령에 따라/,
    /국내에서만/,
  ]) {
    expect(body).not.toMatch(지킬수_없는_약속);
  }
});

/* 사용자에게 불리한 사실(추천 정보의 정확성 미보장)을 빼고 좋은 말만 남기지 않는다. */
test("불리한 사실도 그대로 적는다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  expect(screen.getByRole("dialog").textContent ?? "").toMatch(/실제와 다를 수 있/);
});

/*
 * 조 하나당 한 줄이 이 화면의 규칙이다(TermsModal 주석). 약관은 한 문장, 방침은
 * 용어구다. 어느 쪽이든 길어지면 아무도 읽지 않아서, 지키지도 않을 문장을 적어 둔 것과
 * 결과가 같아진다. 문장이 늘면 조를 쪼개라는 신호이므로 여기서 잡는다.
 */
test("조를 한 줄로 적는다", () => {
  render(<TermsModal onClose={vi.fn()} />);

  for (const 문단 of Array.from(screen.getByRole("dialog").querySelectorAll("p"))) {
    /* 마침표 뒤에 또 글이 이어지면 두 문장이다. */
    expect(문단.textContent ?? "").not.toMatch(/\.\s+\S/);
  }
});

test("닫기 버튼과 확인 버튼 모두 모달을 닫는다", async () => {
  const onClose = vi.fn();
  render(<TermsModal onClose={onClose} />);

  await userEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(onClose).toHaveBeenCalledTimes(1);

  await userEvent.click(screen.getAllByRole("button", { name: "닫기" })[0]);
  expect(onClose).toHaveBeenCalledTimes(2);
});

/* 본문이 길어지면 X 버튼이 스크롤 밖으로 밀린다 — 키보드로도 빠져나갈 수 있어야 한다. */
test("Escape로 닫힌다", async () => {
  const onClose = vi.fn();
  render(<TermsModal onClose={onClose} />);

  await userEvent.keyboard("{Escape}");

  expect(onClose).toHaveBeenCalled();
});

/* "읽었다"와 "동의한다"는 다르다. 모달이 동의를 대신 켜주면 안 된다. */
test("확인했어요가 동의를 대신 눌러주지 않는다", async () => {
  const onClose = vi.fn();
  render(<TermsModal onClose={onClose} />);

  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

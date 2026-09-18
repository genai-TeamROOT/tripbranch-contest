import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { LanguageSelector } from "./LanguageSelector";

it("언어 버튼을 누르면 선택 목록을 열고 선택 후 닫는다", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<LanguageSelector language="ko" onChange={onChange} />);

  await user.click(screen.getByRole("button", { name: "언어 선택" }));

  expect(screen.getByRole("menu", { name: "언어 목록" })).toBeInTheDocument();
  expect(screen.getByRole("menuitemradio", { name: "한국어" })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  await user.click(screen.getByRole("menuitemradio", { name: "English" }));

  expect(onChange).toHaveBeenCalledWith("en");
  expect(screen.queryByRole("menu", { name: "언어 목록" })).not.toBeInTheDocument();
});

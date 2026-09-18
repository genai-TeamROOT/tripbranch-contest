/*
 * 역할: "+" 버튼의 메뉴 열림/닫힘과 파일 선택·크기 제한을 검증한다.
 * 카메라와 갤러리는 capture 속성만 다른 같은 input이라, 둘 다 같은 핸들러를
 * 타는지까지 본다.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PhotoInputButton } from "./PhotoInputButton";

function makeFile(name = "a.jpg", size = 1_000) {
  const file = new File(["x"], name, { type: "image/jpeg" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("PhotoInputButton", () => {
  it("버튼을 누르면 카메라·갤러리 메뉴가 열린다", () => {
    render(<PhotoInputButton onSelect={vi.fn()} />);

    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "사진 추가" }));

    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "카메라" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "갤러리" })).toBeTruthy();
  });

  it("Esc를 누르면 메뉴가 닫힌다", () => {
    render(<PhotoInputButton onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "사진 추가" }));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("카메라와 갤러리 둘 다 같은 선택 핸들러를 탄다", () => {
    const onSelect = vi.fn();
    render(<PhotoInputButton onSelect={onSelect} />);

    for (const testId of ["photo-camera-input", "photo-gallery-input"]) {
      const input = screen.getByTestId(testId) as HTMLInputElement;
      fireEvent.change(input, { target: { files: [makeFile()] } });
    }

    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("10MB를 넘으면 올리지 않고 오류를 알린다", () => {
    const onSelect = vi.fn();
    const onError = vi.fn();
    render(<PhotoInputButton onSelect={onSelect} onError={onError} />);

    const input = screen.getByTestId("photo-gallery-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("big.jpg", 11 * 1024 * 1024)] } });

    expect(onSelect).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("10MB"));
  });

  it("파일을 고르지 않고 닫으면 아무 일도 없다", () => {
    const onSelect = vi.fn();
    render(<PhotoInputButton onSelect={onSelect} />);

    const input = screen.getByTestId("photo-gallery-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [] } });

    expect(onSelect).not.toHaveBeenCalled();
  });

  it("disabled면 버튼을 누를 수 없다", () => {
    render(<PhotoInputButton disabled onSelect={vi.fn()} />);

    const button = screen.getByRole("button", { name: "사진 추가" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});

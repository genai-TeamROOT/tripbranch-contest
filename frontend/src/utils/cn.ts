/*
 * 역할: 조건부 className 을 충돌 없이 병합한다.
 * 입력: clsx 가 받는 모든 형태(문자열·배열·객체·falsy).
 * 출력: tailwind-merge 로 later-wins 정리를 마친 className 문자열.
 *   cn("px-2", isWide && "px-6") → "px-6" (뒤가 이긴다)
 * 근거: package_D/DESIGN_SYSTEM.md §3.4
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

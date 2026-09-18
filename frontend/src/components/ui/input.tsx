/*
 * 역할: 인증 화면 4종이 쓰는 입력 필드 프리미티브(DESIGN_SYSTEM.md §6.16).
 * iOS 자동 확대 방지를 위해 폰트 크기를 16px(text-base) 밑으로 내리지 않는다(§8.1).
 */

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "../../utils/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-12 w-full rounded-xl border border-border bg-white px-3.5 text-base text-ink shadow-resting placeholder:text-muted focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

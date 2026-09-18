/*
 * 역할: 인증 화면 4종(로그인·회원가입·아이디찾기·비밀번호찾기)이 쓰는 버튼 프리미티브.
 * 근거: DESIGN_SYSTEM.md §6.16 — variant/size 표는 실측 기준.
 * 채팅 화면의 순수 <button>과는 비활성 처리 방식이 다르다(disabled:opacity-50이
 * 버튼 전체를 반투명하게 만든다) — 섞어 쓰지 않는다(§6.16 경고).
 */

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../utils/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-full text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-brand text-white hover:bg-brand-deep",
        outline: "border border-border bg-white text-ink hover:border-brand hover:text-brand",
        secondary: "bg-chip text-ink hover:bg-sky-light",
        ghost: "text-ink hover:bg-chip",
        destructive: "bg-rust text-white hover:bg-rust/90",
        link: "text-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "h-11 px-5",
        sm: "h-9 px-4",
        lg: "h-[52px] px-6 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
    );
  },
);
Button.displayName = "Button";

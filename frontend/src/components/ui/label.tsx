/*
 * 역할: 인증 화면 4종이 쓰는 폼 라벨 프리미티브(DESIGN_SYSTEM.md §6.16 — 12px Bold).
 */

import * as LabelPrimitive from "@radix-ui/react-label";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "../../utils/cn";

export const Label = forwardRef<
  ElementRef<typeof LabelPrimitive.Root>,
  ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn("text-xs font-bold text-label", className)}
    {...props}
  />
));
Label.displayName = LabelPrimitive.Root.displayName;

import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

/** 细线转圈：一段 1/4 圆弧，颜色继承 currentColor。 */
export function Spinner({ className, ...props }: ComponentProps<"svg">) {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden className={cn("size-4 animate-spin", className)} {...props}>
      <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeOpacity="0.2" strokeWidth="1.5" />
      <path d="M8 1.75a6.25 6.25 0 0 1 6.25 6.25" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

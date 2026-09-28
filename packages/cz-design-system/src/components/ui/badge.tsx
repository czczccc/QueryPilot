import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export const badgeVariants = cva(
  "inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-full border px-1.5 text-caption font-medium [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "border-line bg-sunken text-fg-muted",
        accent: "border-accent-line bg-accent-soft text-accent-fg",
        success: "border-success-line bg-success-soft text-success-fg",
        warning: "border-warning-line bg-warning-soft text-warning-fg",
        danger: "border-danger-line bg-danger-soft text-danger-fg",
        info: "border-info-line bg-info-soft text-info-fg",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

const dotTone = {
  neutral: "bg-fg-subtle",
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
} as const;

export function Badge({
  className,
  tone,
  dot,
  children,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { dot?: boolean }) {
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot && <span aria-hidden className={cn("size-1.5 rounded-full", dotTone[tone ?? "neutral"])} />}
      {children}
    </span>
  );
}

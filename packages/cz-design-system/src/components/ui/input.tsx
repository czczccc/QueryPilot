import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export const fieldClasses = [
  "w-full rounded-control border border-line bg-surface text-fg shadow-xs",
  "placeholder:text-fg-subtle transition duration-fast ease-standard",
  "hover:border-line-strong",
  "outline-none focus-visible:border-focus focus-visible:shadow-ring",
  "disabled:cursor-not-allowed disabled:bg-sunken disabled:text-fg-disabled",
  "aria-invalid:border-danger aria-invalid:focus-visible:shadow-none",
];

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(fieldClasses, "h-control-md px-3 text-body", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(fieldClasses, "min-h-20 resize-y px-3 py-2 text-body", className)} {...props} />;
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-footnote font-medium text-fg", className)} {...props} />;
}

/** 表单字段：标签 + 控件 + 说明/错误 */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: string;
  error?: string;
  htmlFor: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {(error || hint) && (
        <p className={cn("text-caption font-normal", error ? "text-danger-fg" : "text-fg-subtle")}>{error ?? hint}</p>
      )}
    </div>
  );
}

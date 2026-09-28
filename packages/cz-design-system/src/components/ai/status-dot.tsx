import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export type AgentState = "idle" | "running" | "waiting" | "success" | "error";

const tone: Record<AgentState, string> = {
  idle: "bg-fg-subtle",
  running: "bg-accent",
  waiting: "bg-warning",
  success: "bg-success",
  error: "bg-danger",
};

const label: Record<AgentState, string> = {
  idle: "空闲",
  running: "运行中",
  waiting: "等待确认",
  success: "已完成",
  error: "出错",
};

/** 状态点：运行中带一圈缓慢呼吸的光晕。 */
export function StatusDot({ state, className, ...props }: ComponentProps<"span"> & { state: AgentState }) {
  return (
    <span
      role="status"
      aria-label={label[state]}
      className={cn("relative inline-flex size-2 shrink-0", className)}
      {...props}
    >
      {(state === "running" || state === "waiting") && (
        <span aria-hidden className={cn("absolute inset-0 animate-halo rounded-full", tone[state])} />
      )}
      <span aria-hidden className={cn("relative size-2 rounded-full", tone[state])} />
    </span>
  );
}

/** “思考中”文字：一道光从左往右扫过，比三个跳动的点更安静。 */
export function ThinkingText({ className, ...props }: ComponentProps<"span">) {
  return <span className={cn("text-shimmer", className)} {...props} />;
}

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { rise, reduced, stagger, transition } from "../../motion";
import { duration, easing } from "../../tokens/motion";
import { Spinner } from "../ui/spinner";
import { ThinkingText } from "./status-dot";

export type StepStatus = "pending" | "running" | "done" | "error";

export type AgentStep = {
  id: string;
  title: ReactNode;
  status: StepStatus;
  /** 运行中或出错时展开显示的细节 */
  detail?: ReactNode;
  /** 右侧元信息，如耗时、条数 */
  meta?: ReactNode;
};

/** 完成对勾：线条从起点“画”出来。 */
function CheckMark() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden className="size-4 text-success">
      <circle cx="8" cy="8" r="7" className="fill-success-soft" />
      <motion.path
        d="M5 8.25 7.1 10.3 11 6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: duration.slow / 1000, ease: easing.enter }}
      />
    </svg>
  );
}

function StepIcon({ status }: { status: StepStatus }) {
  return (
    <span className="relative z-10 grid size-5 place-items-center bg-canvas">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={status}
          className="grid place-items-center"
          initial={{ opacity: 0, scale: 0.6 }}
          animate={{ opacity: 1, scale: 1, transition: transition.snappy }}
          exit={{ opacity: 0, scale: 0.6, transition: transition.exit }}
        >
          {status === "pending" && <span className="size-2 rounded-full border border-line-strong" />}
          {status === "running" && <Spinner className="text-accent" />}
          {status === "done" && <CheckMark />}
          {status === "error" && (
            <svg viewBox="0 0 16 16" fill="none" aria-hidden className="size-4 text-danger">
              <circle cx="8" cy="8" r="7" className="fill-danger-soft" />
              <path d="M6 6l4 4M10 6l-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          )}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

/**
 * Agent 执行步骤：把 Agent 的工作过程摊开给用户看。
 * 运行中的步骤标题带流光，细节展开；完成后对勾画出、细节收起。
 */
export function AgentSteps({ steps, className }: { steps: AgentStep[]; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.ol
      className={cn("flex flex-col", className)}
      variants={stagger(0.05)}
      initial="hidden"
      animate="visible"
    >
      {steps.map((step, i) => {
        const last = i === steps.length - 1;
        const open = (step.status === "running" || step.status === "error") && step.detail;
        return (
          <motion.li key={step.id} variants={reduce ? reduced(rise) : rise} className="relative flex gap-3 pb-3 last:pb-0">
            {!last && <span aria-hidden className="absolute top-6 bottom-1 left-2.5 w-px -translate-x-1/2 bg-line-subtle" />}
            <StepIcon status={step.status} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex min-h-5 items-center justify-between gap-3">
                <span
                  className={cn(
                    "truncate text-body",
                    step.status === "pending" && "text-fg-subtle",
                    step.status === "done" && "text-fg-muted",
                    step.status === "error" && "text-danger-fg",
                  )}
                >
                  {step.status === "running" ? <ThinkingText>{step.title}</ThinkingText> : step.title}
                </span>
                {step.meta && <span className="shrink-0 font-mono text-caption font-normal text-fg-subtle tabular-nums">{step.meta}</span>}
              </div>
              <AnimatePresence initial={false}>
                {open && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1, transition: transition.smooth }}
                    exit={{ height: 0, opacity: 0, transition: transition.exit }}
                    className="overflow-hidden"
                  >
                    <div className="pt-1.5 text-footnote text-fg-muted">{step.detail}</div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.li>
        );
      })}
    </motion.ol>
  );
}

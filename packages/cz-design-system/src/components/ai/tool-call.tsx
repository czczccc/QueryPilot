import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { transition } from "../../motion";
import { Badge } from "../ui/badge";
import { Spinner } from "../ui/spinner";

export type ToolCallStatus = "running" | "done" | "error";

const statusBadge = {
  running: { tone: "accent", text: "调用中" },
  done: { tone: "success", text: "完成" },
  error: { tone: "danger", text: "失败" },
} as const;

/** 工具调用卡片：默认折叠只显示工具名和状态，展开看参数和结果。 */
export function ToolCall({
  name,
  summary,
  status,
  input,
  output,
  defaultOpen = false,
  className,
}: {
  name: string;
  summary?: ReactNode;
  status: ToolCallStatus;
  input?: unknown;
  output?: unknown;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const badge = statusBadge[status];
  return (
    <div className={cn("overflow-hidden rounded-card border border-line-subtle bg-surface", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left transition duration-fast ease-standard",
          "outline-none hover:bg-hover focus-visible:shadow-ring",
        )}
      >
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={transition.snappy} className="text-fg-subtle">
          <ChevronRight className="size-3.5" />
        </motion.span>
        <code className="font-mono text-footnote text-fg">{name}</code>
        {summary && <span className="min-w-0 flex-1 truncate text-footnote text-fg-muted">{summary}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {status === "running" && <Spinner className="size-3.5 text-accent" />}
          <Badge tone={badge.tone}>{badge.text}</Badge>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={bodyId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1, transition: transition.smooth }}
            exit={{ height: 0, opacity: 0, transition: transition.exit }}
            className="overflow-hidden"
          >
            <div className="grid gap-2 border-t border-line-subtle bg-sunken p-3">
              {input !== undefined && <Payload label="参数" value={input} />}
              {output !== undefined && <Payload label="结果" value={output} />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Payload({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="grid gap-1">
      <span className="text-caption text-fg-subtle">{label}</span>
      <pre className="overflow-x-auto font-mono text-footnote text-fg-muted">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

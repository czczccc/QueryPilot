import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, Square } from "lucide-react";
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { transition } from "../../motion";
import { Button } from "../ui/button";

/**
 * 输入区：自动增高的文本框 + 底部工具栏。
 * Agent 运行中，发送按钮原地变成“停止”；Enter 发送，Shift+Enter 换行。
 */
export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  running = false,
  placeholder = "描述你想完成的事……",
  tools,
  className,
  id = "cz-composer",
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  running?: boolean;
  placeholder?: string;
  /** 左下角的附加操作，如附件、选择工具 */
  tools?: ReactNode;
  className?: string;
  id?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  const canSend = value.trim().length > 0;

  return (
    <form
      className={cn(
        "flex flex-col gap-1.5 rounded-dialog border border-line bg-surface p-2 shadow-sm",
        "transition duration-base ease-standard",
        "focus-within:border-line-strong focus-within:shadow-md",
        className,
      )}
      onSubmit={(e) => {
        e.preventDefault();
        if (running) onStop?.();
        else if (canSend) onSubmit();
      }}
    >
      <label htmlFor={id} className="sr-only">
        输入消息
      </label>
      <textarea
        id={id}
        ref={ref}
        rows={1}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
        className="max-h-60 min-h-11 w-full resize-none bg-transparent px-1.5 pt-1.5 text-body-lg text-fg outline-none placeholder:text-fg-subtle"
      />
      <div className="flex items-center gap-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-1">{tools}</div>
        <Button
          type="submit"
          size="icon"
          variant={running ? "secondary" : "primary"}
          disabled={!running && !canSend}
          aria-label={running ? "停止" : "发送"}
          className="rounded-full"
        >
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={running ? "stop" : "send"}
              className="grid place-items-center"
              initial={{ opacity: 0, scale: 0.5, rotate: -45 }}
              animate={{ opacity: 1, scale: 1, rotate: 0, transition: transition.snappy }}
              exit={{ opacity: 0, scale: 0.5, transition: transition.exit }}
            >
              {running ? <Square className="fill-current" /> : <ArrowUp />}
            </motion.span>
          </AnimatePresence>
        </Button>
      </div>
    </form>
  );
}

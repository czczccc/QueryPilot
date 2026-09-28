import { AnimatePresence, motion } from "framer-motion";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { transition } from "../../motion";
import { distance, scale } from "../../tokens/motion";

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <TooltipPrimitive.Provider delayDuration={400} skipDelayDuration={200}>
    {children}
  </TooltipPrimitive.Provider>
);

const offset = { top: [0, distance.nudge], bottom: [0, -distance.nudge], left: [distance.nudge, 0], right: [-distance.nudge, 0] } as const;

/** 提示气泡：反色底，出现时朝触发元素的反方向轻移 2px。 */
export function Tooltip({
  content,
  side = "top",
  shortcut,
  children,
  className,
}: {
  content: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  /** 可选快捷键，显示在文字右侧 */
  shortcut?: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [x, y] = offset[side];
  return (
    <TooltipPrimitive.Root open={open} onOpenChange={setOpen}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <AnimatePresence>
        {open && (
          <TooltipPrimitive.Portal forceMount>
            <TooltipPrimitive.Content side={side} sideOffset={6} asChild forceMount>
              <motion.div
                className={cn(
                  "z-50 flex items-center gap-2 rounded-sm bg-inverse px-2 py-1 text-caption text-fg-inverse shadow-md",
                  className,
                )}
                initial={{ opacity: 0, scale: scale.enter, x, y }}
                animate={{ opacity: 1, scale: 1, x: 0, y: 0, transition: transition.snappy }}
                exit={{ opacity: 0, transition: transition.exit }}
              >
                {content}
                {shortcut && <span className="font-mono opacity-60">{shortcut}</span>}
              </motion.div>
            </TooltipPrimitive.Content>
          </TooltipPrimitive.Portal>
        )}
      </AnimatePresence>
    </TooltipPrimitive.Root>
  );
}

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { createContext, useContext, useState, type ComponentProps, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { fade, pop, reduced } from "../../motion";
import { Button } from "./button";

const OpenContext = createContext(false);

/**
 * 对话框。Radix 负责焦点管理、Esc 关闭和无障碍属性，Framer Motion 负责进出场：
 * 遮罩淡入，内容从 96% 缩放并上浮 4px 落定；退出更快。
 */
export function Dialog({
  open: openProp,
  defaultOpen,
  onOpenChange,
  children,
}: {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [inner, setInner] = useState(defaultOpen ?? false);
  const open = openProp ?? inner;
  const setOpen = (next: boolean) => {
    if (openProp === undefined) setInner(next);
    onOpenChange?.(next);
  };
  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <OpenContext.Provider value={open}>{children}</OpenContext.Provider>
    </DialogPrimitive.Root>
  );
}

export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  hideClose,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content> & { hideClose?: boolean }) {
  const open = useContext(OpenContext);
  const reduce = useReducedMotion();
  return (
    <AnimatePresence>
      {open && (
        <DialogPrimitive.Portal forceMount>
          <DialogPrimitive.Overlay asChild forceMount>
            <motion.div
              className="fixed inset-0 z-50 bg-overlay backdrop-blur-xs"
              variants={fade}
              initial="hidden"
              animate="visible"
              exit="hidden"
            />
          </DialogPrimitive.Overlay>
          <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center p-4">
            <DialogPrimitive.Content asChild forceMount {...props}>
              <motion.div
                className={cn(
                  "pointer-events-auto relative flex w-full max-w-md flex-col gap-4",
                  "rounded-dialog border border-line-subtle bg-raised p-6 text-fg shadow-xl outline-none",
                  className,
                )}
                variants={reduce ? reduced(pop) : pop}
                initial="hidden"
                animate="visible"
                exit="hidden"
              >
                {children}
                {!hideClose && (
                  <DialogPrimitive.Close asChild>
                    <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3" aria-label="关闭">
                      <X />
                    </Button>
                  </DialogPrimitive.Close>
                )}
              </motion.div>
            </DialogPrimitive.Content>
          </div>
        </DialogPrimitive.Portal>
      )}
    </AnimatePresence>
  );
}

export function DialogHeader({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1.5 pr-6", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn("text-title text-fg", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn("text-body text-fg-muted", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...props} />;
}

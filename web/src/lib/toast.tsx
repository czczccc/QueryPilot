import { AnimatePresence, motion } from "framer-motion";
import { Check, CircleAlert } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { cn, transition } from "@cz/design-system";

type Kind = "ok" | "error";
type Toast = { id: number; text: string; kind: Kind };
type ToastFn = (text: string, kind?: Kind, ms?: number) => void;

const Ctx = createContext<ToastFn>(() => {});

/** 轻提示：底部居中，最多同时显示 3 条 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const toast = useCallback<ToastFn>((text, kind = "ok", ms) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-2), { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), ms || (kind === "error" ? 4000 : 2200));
  }, []);
  return (
    <Ctx.Provider value={toast}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 md:bottom-6">
        <AnimatePresence initial={false}>
          {items.map((t) => (
            <motion.div
              key={t.id}
              role={t.kind === "error" ? "alert" : "status"}
              initial={{ opacity: 0, y: 8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1, transition: transition.snappy }}
              exit={{ opacity: 0, y: 4, transition: transition.exit }}
              className={cn(
                "pointer-events-auto flex max-w-full items-center gap-2 rounded-control border border-line bg-raised px-3 py-2 text-footnote text-fg shadow-lg",
              )}
            >
              {t.kind === "error" ? <CircleAlert className="size-4 shrink-0 text-danger" /> : <Check className="size-4 shrink-0 text-success" />}
              <span className="min-w-0">{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);

import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import { blur, duration, easing } from "../../tokens/motion";

/** 按中文单字、英文单词和标点切分，保证中英文混排时流式节奏一致。 */
const segment = (text: string) => text.match(/[㐀-鿿＀-￯]|[^\s㐀-鿿＀-￯]+|\s+/g) ?? [];

/**
 * 流式文本：每个新出现的字从透明、轻微模糊淡入，而不是生硬地“跳”出来。
 * 传入 streaming=false 时直接显示全文。
 */
export function StreamingText({
  text,
  streaming = true,
  /** 每秒输出的片段数 */
  rate = 36,
  className,
  onDone,
}: {
  text: string;
  streaming?: boolean;
  rate?: number;
  className?: string;
  onDone?: () => void;
}) {
  const parts = useMemo(() => segment(text), [text]);
  const reduce = useReducedMotion();
  const [count, setCount] = useState(streaming ? 0 : parts.length);

  useEffect(() => {
    if (!streaming) {
      setCount(parts.length);
      return;
    }
    setCount(0);
    const timer = setInterval(() => {
      setCount((c) => {
        if (c + 1 >= parts.length) {
          clearInterval(timer);
          onDone?.();
          return parts.length;
        }
        return c + 1;
      });
    }, 1000 / rate);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, streaming, rate]);

  const done = count >= parts.length;
  return (
    <p className={cn("text-body-lg text-fg", className)}>
      {parts.slice(0, count).map((p, i) => (
        <motion.span
          key={i}
          initial={reduce ? { opacity: 0 } : { opacity: 0, filter: `blur(${blur.enter}px)` }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, filter: `blur(${blur.none}px)` }}
          transition={{ duration: duration.slow / 1000, ease: easing.enter }}
        >
          {p}
        </motion.span>
      ))}
      {!done && (
        <span aria-hidden className="ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 animate-breathe rounded-full bg-accent" />
      )}
    </p>
  );
}

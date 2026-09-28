/**
 * Framer Motion 预设：所有组件的动效都从这里取，时长、曲线、弹簧全部来自动效 token。
 */
import type { Transition, Variants } from "framer-motion";
import { distance, duration, easing, scale, spring } from "../tokens/motion";

const s = (ms: number) => ms / 1000;

export const transition = {
  fast: { duration: s(duration.fast), ease: easing.standard },
  base: { duration: s(duration.base), ease: easing.standard },
  enter: { duration: s(duration.base), ease: easing.enter },
  exit: { duration: s(duration.base * 0.7), ease: easing.exit },
  emphasized: { duration: s(duration.slow), ease: easing.emphasized },
  ...spring,
} as const satisfies Record<string, Transition>;

/** 淡入淡出：遮罩、提示 */
export const fade: Variants = {
  hidden: { opacity: 0, transition: transition.exit },
  visible: { opacity: 1, transition: transition.enter },
};

/** 上浮进入：列表项、消息、卡片 */
export const rise: Variants = {
  hidden: { opacity: 0, y: distance.rise, transition: transition.exit },
  visible: { opacity: 1, y: 0, transition: { ...spring.smooth, opacity: transition.enter } },
};

/** 缩放进入：对话框、弹出层 */
export const pop: Variants = {
  hidden: { opacity: 0, scale: scale.enter, y: distance.shift, transition: transition.exit },
  visible: { opacity: 1, scale: 1, y: 0, transition: { ...spring.smooth, opacity: transition.enter } },
};

/** 子元素依次出现 */
export const stagger = (step = 0.04, delay = 0): Variants => ({
  hidden: {},
  visible: { transition: { staggerChildren: step, delayChildren: delay } },
});

/** 可点击元素的按下反馈 */
export const press = { whileTap: { scale: scale.press }, transition: spring.snappy } as const;

/** 减少动态效果时：去掉位移和缩放，只保留透明度 */
export const reduced = (v: Variants): Variants =>
  Object.fromEntries(
    Object.entries(v).map(([key, value]) => {
      if (typeof value !== "object" || value === null) return [key, value];
      const { x: _x, y: _y, scale: _s, ...rest } = value as Record<string, unknown>;
      return [key, rest];
    }),
  ) as Variants;

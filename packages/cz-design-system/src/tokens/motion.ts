/**
 * 动效系统
 *
 * 原则：
 * 1. 快速响应：悬停、按下等直接反馈 ≤ 140ms。
 * 2. 进入减速、退出加速：退出时长约为进入的 70%，让界面“收得干净”。
 * 3. 空间变化用弹簧，颜色和透明度用补间：位移、缩放、布局切换走 spring，保持物理感。
 * 4. 位移幅度小：元素进场最多移动 8px，缩放从 0.96 开始，不做夸张的弹跳。
 * 5. 尊重“减少动态效果”：只保留透明度变化。
 */

export const duration = {
  instant: 80,
  fast: 140,
  base: 220,
  slow: 320,
  slower: 480,
  /** 呼吸、流光等持续性动效的周期 */
  ambient: 1800,
} as const;

export type DurationName = keyof typeof duration;

/** 贝塞尔曲线，[x1, y1, x2, y2] */
export const easing = {
  /** 通用：状态切换、颜色变化 */
  standard: [0.2, 0, 0, 1],
  /** 进入：快速出现，缓慢落定 */
  enter: [0.16, 1, 0.3, 1],
  /** 退出：缓慢起步，快速离开 */
  exit: [0.4, 0, 1, 1],
  /** 强调：稍带回弹，用于成功反馈等少数时刻 */
  emphasized: [0.3, 1.25, 0.4, 1],
  /** 线性：只用于流光、进度条等持续动画 */
  linear: [0, 0, 1, 1],
} as const satisfies Record<string, readonly [number, number, number, number]>;

export type EasingName = keyof typeof easing;

export const cubicBezier = (e: readonly number[]) => `cubic-bezier(${e.join(", ")})`;

/** Framer Motion 弹簧参数 */
export const spring = {
  /** 按钮、开关、小元素：干脆利落 */
  snappy: { type: "spring", stiffness: 520, damping: 38, mass: 0.8 },
  /** 弹层、卡片：平稳 */
  smooth: { type: "spring", stiffness: 340, damping: 34, mass: 1 },
  /** 大面板、页面切换：柔和 */
  gentle: { type: "spring", stiffness: 200, damping: 28, mass: 1 },
  /** 布局动画（layoutId、列表重排） */
  layout: { type: "spring", stiffness: 420, damping: 40, mass: 1 },
} as const;

export type SpringName = keyof typeof spring;

/** 位移与缩放幅度 */
export const distance = {
  nudge: 2,
  shift: 4,
  rise: 8,
} as const;

/** 模糊半径（px），用于流式文字等“由虚到实”的进场 */
export const blur = {
  none: 0,
  enter: 2,
} as const;

export const scale = {
  press: 0.97,
  enter: 0.96,
  hover: 1.02,
} as const;

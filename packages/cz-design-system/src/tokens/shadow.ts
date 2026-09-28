/**
 * 阴影系统
 *
 * 阴影表达“高度”，不是装饰。每一档由两层组成：一层贴近物体的接触阴影，一层柔和的环境阴影。
 * 浅色主题的阴影带一点石墨色相，避免发灰发脏；深色主题阴影几乎不可见，
 * 改用顶部 1px 高光描边表达层级。
 */

export const elevation = ["xs", "sm", "md", "lg", "xl"] as const;
export type Elevation = (typeof elevation)[number];

const tint = (a: number) => `oklch(0.24 0.012 170 / ${a})`;
const black = (a: number) => `oklch(0 0 0 / ${a})`;
const highlight = "inset 0 1px 0 0 oklch(1 0 0 / 0.05)";

export const shadow = {
  light: {
    xs: `0 1px 2px 0 ${tint(0.05)}`,
    sm: `0 1px 2px 0 ${tint(0.06)}, 0 1px 3px 0 ${tint(0.04)}`,
    md: `0 2px 4px -1px ${tint(0.05)}, 0 6px 16px -4px ${tint(0.09)}`,
    lg: `0 4px 8px -4px ${tint(0.06)}, 0 16px 36px -8px ${tint(0.14)}`,
    xl: `0 8px 16px -8px ${tint(0.08)}, 0 28px 64px -16px ${tint(0.22)}`,
  },
  dark: {
    xs: `0 1px 2px 0 ${black(0.3)}`,
    sm: `${highlight}, 0 1px 2px 0 ${black(0.35)}`,
    md: `${highlight}, 0 6px 16px -4px ${black(0.45)}`,
    lg: `${highlight}, 0 16px 36px -8px ${black(0.55)}`,
    xl: `${highlight}, 0 28px 64px -16px ${black(0.65)}`,
  },
} as const satisfies Record<"light" | "dark", Record<Elevation, string>>;

/** 焦点环：2px 画布色间隙 + 2px 焦点色，任何背景上都清晰。 */
export const focusRing = "0 0 0 2px var(--cz-color-canvas), 0 0 0 4px var(--cz-color-focus)";

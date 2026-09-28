/**
 * 阴影系统
 *
 * 阴影表达“高度”，不是装饰。每一档由两层组成：一层贴近物体的接触阴影，一层柔和的环境阴影。
 * 浅色主题的阴影带一点石墨色相，避免发灰发脏；深色主题阴影几乎不可见，
 * 改用顶部 1px 高光加一圈极淡描边勾出边缘，层级越高越亮。
 */

export const elevation = ["xs", "sm", "md", "lg", "xl"] as const;
export type Elevation = (typeof elevation)[number];

const tint = (a: number) => `oklch(0.24 0.012 170 / ${a})`;
const black = (a: number) => `oklch(0 0 0 / ${a})`;
/** 深色主题：顶部 1px 高光 + 一圈极淡的描边，替代看不见的阴影来勾出边缘 */
const edge = (top: number, ring: number) => `inset 0 1px 0 0 oklch(1 0 0 / ${top}), 0 0 0 1px oklch(1 0 0 / ${ring})`;

export const shadow = {
  light: {
    xs: `0 1px 2px 0 ${tint(0.08)}`,
    sm: `0 1px 2px 0 ${tint(0.08)}, 0 2px 6px 0 ${tint(0.06)}`,
    md: `0 2px 4px -1px ${tint(0.08)}, 0 8px 20px -4px ${tint(0.14)}`,
    lg: `0 4px 8px -4px ${tint(0.1)}, 0 18px 40px -8px ${tint(0.2)}`,
    xl: `0 10px 20px -8px ${tint(0.12)}, 0 32px 72px -16px ${tint(0.3)}`,
  },
  dark: {
    xs: `0 0 0 1px oklch(1 0 0 / 0.04), 0 1px 2px 0 ${black(0.5)}`,
    sm: `${edge(0.06, 0.05)}, 0 2px 4px 0 ${black(0.45)}`,
    md: `${edge(0.07, 0.06)}, 0 8px 20px -4px ${black(0.55)}`,
    lg: `${edge(0.08, 0.07)}, 0 18px 40px -8px ${black(0.65)}`,
    xl: `${edge(0.09, 0.08)}, 0 32px 72px -16px ${black(0.75)}`,
  },
} as const satisfies Record<"light" | "dark", Record<Elevation, string>>;

/** 焦点环：2px 画布色间隙 + 2px 焦点色，任何背景上都清晰。 */
export const focusRing = "0 0 0 2px var(--cz-color-canvas), 0 0 0 4px var(--cz-color-focus)";

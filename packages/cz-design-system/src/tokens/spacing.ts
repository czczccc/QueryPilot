/**
 * 间距系统
 *
 * 4px 网格：Tailwind 的数字间距直接基于 --spacing: 4px（p-1 = 4px，gap-3 = 12px）。
 * 下表是允许使用的档位，组件只从这里取值；调整 --spacing 可以整体改变信息密度。
 *
 * 注意：不要给间距起 sm / md / lg 这样的名字。Tailwind v4 里它们会和 max-w-md 等容器宽度冲突，
 * 让 max-w-lg 变成 16px。
 */

export const spacingBase = "0.25rem";

/** 键是 Tailwind 的间距档位（p-{key}），值是像素和典型用途。 */
export const spacing = {
  "0.5": { px: 2, usage: "图标与文字的最小间隙" },
  "1": { px: 4, usage: "紧凑元素内部" },
  "1.5": { px: 6, usage: "徽标内边距、标签与控件" },
  "2": { px: 8, usage: "控件内元素间距" },
  "3": { px: 12, usage: "控件水平内边距、列表项" },
  "4": { px: 16, usage: "卡片内边距、表单字段之间" },
  "6": { px: 24, usage: "对话框内边距、卡片之间" },
  "8": { px: 32, usage: "区块内分组" },
  "12": { px: 48, usage: "区块之间" },
  "16": { px: 64, usage: "页面大区块" },
  "24": { px: 96, usage: "首屏留白" },
} as const;

export type SpacingStep = keyof typeof spacing;

/** 控件高度：所有可点击控件共用同一套高度，保证横向对齐。 */
export const controlHeight = {
  sm: "1.75rem", // 28px
  md: "2.25rem", // 36px
  lg: "2.75rem", // 44px（移动端最小触控尺寸）
} as const;

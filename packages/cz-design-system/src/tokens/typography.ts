/**
 * 字体系统
 *
 * - 界面字体：Instrument Sans，字形紧凑、x 高度适中，中文回落到系统黑体（苹方 / 鸿蒙 / 思源）。
 * - 等宽字体：JetBrains Mono，用于代码、工具调用参数、数字对齐。
 * - 字号以 14px 为界面正文基准，标题逐级放大，大字号收紧字距。
 * - 只用 400 / 500 / 600 三种字重，靠字号和颜色建立层级，而不是靠加粗。
 */

export const fontFamily = {
  sans: [
    '"Instrument Sans"',
    '"PingFang SC"',
    '"HarmonyOS Sans SC"',
    '"Noto Sans SC"',
    '"Microsoft YaHei"',
    "system-ui",
    "-apple-system",
    "sans-serif",
  ].join(", "),
  mono: [
    '"JetBrains Mono"',
    "ui-monospace",
    '"SF Mono"',
    "Menlo",
    "Consolas",
    '"Noto Sans Mono CJK SC"',
    "monospace",
  ].join(", "),
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
} as const;

export type TextStyle = {
  /** 字号 */
  size: string;
  /** 行高 */
  lineHeight: string;
  /** 字距 */
  tracking: string;
  /** 默认字重 */
  weight: number;
  /** 用途说明（展示页用） */
  usage: string;
};

export const textStyles = {
  caption: { size: "0.75rem", lineHeight: "1rem", tracking: "0.01em", weight: 500, usage: "标签、时间戳、辅助信息" },
  footnote: { size: "0.8125rem", lineHeight: "1.125rem", tracking: "0", weight: 400, usage: "次要说明、表格" },
  body: { size: "0.875rem", lineHeight: "1.375rem", tracking: "-0.003em", weight: 400, usage: "界面正文（默认）" },
  "body-lg": { size: "1rem", lineHeight: "1.625rem", tracking: "-0.006em", weight: 400, usage: "阅读正文、AI 回复" },
  "title-sm": { size: "1.0625rem", lineHeight: "1.5rem", tracking: "-0.01em", weight: 600, usage: "卡片标题" },
  title: { size: "1.25rem", lineHeight: "1.75rem", tracking: "-0.014em", weight: 600, usage: "区块标题、对话框标题" },
  heading: { size: "1.5rem", lineHeight: "2rem", tracking: "-0.018em", weight: 600, usage: "页面标题" },
  "display-sm": { size: "1.875rem", lineHeight: "2.375rem", tracking: "-0.022em", weight: 600, usage: "空状态、引导页" },
  display: { size: "2.375rem", lineHeight: "2.875rem", tracking: "-0.026em", weight: 600, usage: "营销页标题" },
  "display-lg": { size: "3rem", lineHeight: "3.5rem", tracking: "-0.03em", weight: 600, usage: "首屏主标题" },
} as const satisfies Record<string, TextStyle>;

export type TextStyleName = keyof typeof textStyles;

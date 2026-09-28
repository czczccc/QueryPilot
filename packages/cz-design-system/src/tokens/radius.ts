/**
 * 圆角系统
 *
 * 圆角随元素尺寸增大：小元素用小圆角，容器用大圆角。
 * 嵌套时内层圆角 = 外层圆角 - 间距，保持同心（见 README）。
 */

export const radius = {
  none: "0",
  xs: "0.25rem", // 4px  徽标、Kbd、复选框
  sm: "0.375rem", // 6px 小按钮、标签
  md: "0.5rem", // 8px   按钮、输入框（控件默认）
  lg: "0.75rem", // 12px 卡片、下拉菜单
  xl: "1rem", // 16px    对话框、输入区
  "2xl": "1.375rem", // 22px 大面板、移动端底部抽屉
  full: "9999px", // 胶囊、头像、状态点
} as const;

export type RadiusName = keyof typeof radius;

/** 语义圆角：组件引用这些，而不是具体档位。 */
export const radiusRoles = {
  control: "md",
  card: "lg",
  popover: "lg",
  dialog: "xl",
  pill: "full",
} as const satisfies Record<string, RadiusName>;

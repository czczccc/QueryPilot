# CZ Design System

面向多个 AI Native 产品的设计系统。设计方向：克制的界面、精致的动效、专业的秩序感。

技术栈：React 19 · Tailwind CSS v4 · shadcn/ui 方式（Radix 原语 + 源码交付）· Framer Motion。

## 结构

```
src/tokens/        唯一的设计源头（TypeScript）
  color.ts         OKLCH 原始色阶 + 明暗两套语义色
  typography.ts    字体栈、10 级字号（字号/行高/字距/字重）
  spacing.ts       4px 网格与允许的间距档位、控件高度
  radius.ts        圆角档位与语义圆角（control / card / dialog …）
  shadow.ts        5 级阴影（明暗各一套）与焦点环
  motion.ts        时长、缓动曲线、弹簧、位移与缩放幅度
  css.ts           把 token 渲染成 CSS 变量和 Tailwind 主题
src/styles/        tokens.css、theme.css 由脚本生成；base.css 为基础样式
src/motion/        Framer Motion 预设（fade / rise / pop / stagger / press）
src/components/ui  基础组件：Button Input Textarea Field Badge Card Dialog Tooltip Tabs Switch Separator Skeleton Kbd Spinner
src/components/ai  AI 组件：AgentSteps ToolCall StreamingText Composer StatusDot ThinkingText
showcase/          展示页（npm run dev 本地预览，npm run build 输出单文件 dist/index.html）
tests/             对比度、色域、生成文件同步、禁止硬编码样式、组件渲染
```

## Token 分层

1. **原始 token**：色阶、字号、间距等具体数值，只在 `src/tokens` 里出现。
2. **语义 token**：`--cz-color-canvas`、`--cz-color-fg-muted`、`--cz-radius-card`、`--cz-shadow-md`……明暗主题只替换这一层。
3. **Tailwind 主题**：`theme.css` 用 `@theme inline` 把语义 token 映射成工具类（`bg-surface`、`text-fg-muted`、`rounded-control`、`shadow-lg`、`ease-enter`、`duration-fast`），并清空 Tailwind 自带的调色板、字号、圆角、阴影和缓动。

所以组件里写 `bg-zinc-100`、`text-sm`、`bg-primary` 这类默认类名不会生效；`tests/token-only.test.ts` 还会扫描组件源码，出现十六进制颜色、颜色函数、默认色板、shadcn 默认语义色、方括号任意值都会让测试失败。

## 使用

```css
/* 入口 CSS */
@import "@cz/design-system/styles.css";
```

```tsx
import { Button, Badge, AgentSteps } from "@cz/design-system";
```

主题：默认跟随系统；`<html data-theme="dark">` 强制深色；任意容器上加 `data-theme` 可做局部主题。

## 为新产品换品牌

改 `src/tokens/color.ts` 里的 `pine`（色相、峰值彩度），或把语义色 `accent*` 指到另一条色阶，然后：

```bash
npm run tokens   # 重新生成 tokens.css / theme.css
npm test         # 对比度不达标会直接失败
```

## 命令

```bash
npm install
npm run dev        # 展示页
npm run check      # 类型检查 + 测试 + 构建
```

该目录独立于 QueryPilot 后端，不进入 Docker 镜像，也不参与部署。

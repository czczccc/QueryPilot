import { SectionHeader } from "./SectionHeader";

const css = `/* app.css */
@import "@cz/design-system/styles.css";`;

const tsx = `import { Button, Badge, AgentSteps } from "@cz/design-system";

<Button variant="primary">新建订阅</Button>
<Badge tone="success" dot>已转存</Badge>
<div className="rounded-card bg-surface p-4 text-fg-muted shadow-sm">…</div>`;

const theme = `<!-- 跟随系统（默认）-->
<html>
<!-- 强制主题，或给任意容器单独设置 -->
<html data-theme="dark">
<section data-theme="light">…</section>`;

const brand = `// src/tokens/color.ts：换一个产品，只改这里
export const pine = ramp(182, chromatic(0.11)); // 色相, 彩度
// npm run tokens 重新生成 CSS，测试会检查对比度`;

export function UsageSection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="usage" source="packages/cz-design-system" title="接入">
        TypeScript 源文件是唯一来源，生成 CSS 变量（--cz-*）和 Tailwind 主题。新产品引入样式入口后，
        直接使用组件，或用 token 类名写自己的界面。
      </SectionHeader>
      <div className="grid gap-4 md:grid-cols-2">
        {[
          ["1. 引入样式", css],
          ["2. 使用组件和 token 类名", tsx],
          ["3. 主题", theme],
          ["4. 为新产品换品牌色", brand],
        ].map(([title, code]) => (
          <div key={title} className="flex min-w-0 flex-col gap-2">
            <span className="text-footnote font-medium text-fg">{title}</span>
            <pre className="overflow-x-auto rounded-card border border-line-subtle bg-sunken p-4 font-mono text-footnote text-fg-muted">
              {code}
            </pre>
          </div>
        ))}
      </div>
    </section>
  );
}

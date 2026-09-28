import type { ReactNode } from "react";
import { radius, radiusRoles, spacing, type SpacingStep } from "../../src";
import { SectionHeader } from "./SectionHeader";

const spaceClass: Record<SpacingStep, string> = {
  "0.5": "w-0.5",
  "1": "w-1",
  "1.5": "w-1.5",
  "2": "w-2",
  "3": "w-3",
  "4": "w-4",
  "6": "w-6",
  "8": "w-8",
  "12": "w-12",
  "16": "w-16",
  "24": "w-24",
};

export function SpacingSection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="spacing" source="src/tokens/spacing.ts" title="间距">
        4px 网格，全部基于一个 --cz-spacing 变量：p-4 是 16px，gap-2 是 8px。组件只使用下表的 11 个档位，调整基准值即可整体改变信息密度。
        所有可点击控件共用 28 / 36 / 44px 三档高度，横向排列时天然对齐。
      </SectionHeader>
      <div className="grid gap-8 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-2">
          {(Object.keys(spacing) as SpacingStep[]).sort((a, b) => spacing[a].px - spacing[b].px).map((k) => (
            <div key={k} className="grid grid-cols-[3rem_3rem_6rem_minmax(0,1fr)] items-center gap-3">
              <code className="font-mono text-footnote text-fg">{k}</code>
              <span className="font-mono text-caption font-normal text-fg-subtle tabular-nums">{spacing[k].px}px</span>
              <span className={`h-3 rounded-xs bg-accent ${spaceClass[k]}`} />
              <span className="truncate text-caption font-normal text-fg-subtle">{spacing[k].usage}</span>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-3">
          <span className="text-caption text-fg-subtle">控件高度</span>
          {[
            ["control-sm", "h-control-sm", "28px · 紧凑工具栏"],
            ["control-md", "h-control-md", "36px · 默认"],
            ["control-lg", "h-control-lg", "44px · 移动端触控"],
          ].map(([name, cls, note]) => (
            <div key={name} className="flex items-center gap-3">
              <span className={`${cls} flex w-28 shrink-0 items-center justify-center rounded-control border border-dashed border-accent-line bg-accent-soft font-mono text-caption font-normal text-accent-fg`}>
                {name}
              </span>
              <span className="text-footnote text-fg-muted">{note}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const radiusClass: Record<string, string> = {
  none: "rounded-none",
  xs: "rounded-xs",
  sm: "rounded-sm",
  md: "rounded-md",
  lg: "rounded-lg",
  xl: "rounded-xl",
  "2xl": "rounded-2xl",
  full: "rounded-full",
};

export function RadiusSection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="radius" source="src/tokens/radius.ts" title="圆角">
        圆角随尺寸增大：徽标 4px，控件 8px，卡片 12px，对话框 16px。组件引用语义圆角（rounded-control、rounded-card），
        嵌套时内层圆角等于外层圆角减去间距，保持同心。
      </SectionHeader>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
        {Object.entries(radius).map(([k, v]) => {
          const role = Object.entries(radiusRoles).find(([, r]) => r === k)?.[0];
          return (
            <div key={k} className="flex flex-col gap-2">
              <div className={`h-20 border border-line bg-surface shadow-xs ${radiusClass[k]}`} />
              <div className="flex items-baseline justify-between gap-2">
                <code className="font-mono text-footnote text-fg">{k}</code>
                <span className="font-mono text-caption font-normal text-fg-subtle">{v === "9999px" ? "∞" : `${parseFloat(v) * 16}px`}</span>
              </div>
              {role && <span className="-mt-1.5 text-caption font-normal text-accent-fg">rounded-{role}</span>}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-6">
        <div className="rounded-xl border border-line-subtle bg-surface p-2 shadow-sm">
          <div className="rounded-sm bg-accent-soft px-4 py-3 text-footnote text-accent-fg">外层 16px − 间距 8px = 内层 8px</div>
        </div>
        <span className="text-footnote text-fg-muted">同心圆角：外框和内块的弧线平行</span>
      </div>
    </section>
  );
}

/** 每一档阴影配一个真实场景的小样，放在凹陷底色上，让高度差一眼可见。 */
const elevationDemos: { name: string; use: string; demo: ReactNode }[] = [
  {
    name: "shadow-xs",
    use: "按钮、输入框",
    demo: (
      <div className="flex items-center gap-2">
        <span className="flex h-control-md items-center rounded-control border border-line bg-surface px-3 text-body shadow-xs">
          繁花
        </span>
        <span className="flex h-control-md items-center rounded-control border border-line bg-surface px-3 text-body font-medium shadow-xs">
          搜索
        </span>
      </div>
    ),
  },
  {
    name: "shadow-sm",
    use: "卡片",
    demo: (
      <div className="w-44 rounded-card border border-line-subtle bg-surface p-3 shadow-sm">
        <div className="text-body font-medium">繁花</div>
        <div className="text-caption font-normal text-fg-subtle">更新至第 30 集</div>
      </div>
    ),
  },
  {
    name: "shadow-md",
    use: "悬停的卡片",
    demo: (
      <div className="w-44 -translate-y-1 rounded-card border border-line bg-surface p-3 shadow-md">
        <div className="text-body font-medium">繁花</div>
        <div className="text-caption font-normal text-accent-fg">点击查看详情</div>
      </div>
    ),
  },
  {
    name: "shadow-lg",
    use: "下拉菜单、弹出层",
    demo: (
      <div className="flex w-40 flex-col rounded-popover border border-line-subtle bg-raised p-1 text-footnote shadow-lg">
        <span className="rounded-sm bg-hover px-2 py-1.5">打开网盘目录</span>
        <span className="px-2 py-1.5 text-fg-muted">暂停订阅</span>
        <span className="px-2 py-1.5 text-danger-fg">取消订阅</span>
      </div>
    ),
  },
  {
    name: "shadow-xl",
    use: "对话框",
    demo: (
      <div className="flex w-48 flex-col gap-2 rounded-dialog border border-line-subtle bg-raised p-3 shadow-xl">
        <div className="text-footnote font-medium">取消订阅《繁花》？</div>
        <div className="flex justify-end gap-1.5">
          <span className="rounded-sm border border-line px-2 py-0.5 text-caption">保留</span>
          <span className="rounded-sm bg-danger px-2 py-0.5 text-caption text-on-accent">取消</span>
        </div>
      </div>
    ),
  },
  {
    name: "shadow-ring",
    use: "键盘焦点",
    demo: (
      <span className="flex h-control-md w-44 items-center rounded-control border border-focus bg-surface px-3 text-body shadow-ring">
        剧名<span className="ml-0.5 h-4 w-px animate-breathe bg-fg" />
      </span>
    ),
  },
];

export function ElevationSection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="elevation" source="src/tokens/shadow.ts" title="阴影">
        阴影只表达高度，越往上的元素阴影越大越柔：按钮最低，卡片其次，菜单和对话框浮在最上面。每档两层，贴近物体的接触阴影加柔和的环境阴影，
        带一点石墨色相而不是纯黑。深色主题里阴影不明显，改用顶部 1px 高光加一圈淡描边勾出边缘，层级越高越亮。
      </SectionHeader>
      <div className="grid gap-4 md:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <div
            key={theme}
            data-theme={theme}
            className="flex flex-col gap-4 overflow-hidden rounded-dialog border border-line-subtle bg-canvas p-4"
          >
            <span className="text-caption text-fg-subtle">{theme === "light" ? "浅色主题" : "深色主题"}</span>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {elevationDemos.map((d) => (
                <div key={d.name} className="flex flex-col gap-2">
                  <div className="grid h-36 place-items-center rounded-card border border-line-subtle bg-sunken px-3">{d.demo}</div>
                  <div className="flex items-baseline justify-between gap-2 px-0.5">
                    <code className="font-mono text-footnote text-fg">{d.name}</code>
                    <span className="text-caption font-normal text-fg-subtle">{d.use}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { cn, Tabs, TabsList, TabsTrigger, TooltipProvider } from "../src";
import { AgentDemo } from "./sections/AgentDemo";
import { ColorSection } from "./sections/ColorSection";
import { ComponentsSection } from "./sections/ComponentsSection";
import { MotionSection } from "./sections/MotionSection";
import { ElevationSection, RadiusSection, SpacingSection } from "./sections/ShapeSections";
import { TypographySection } from "./sections/TypographySection";
import { UsageSection } from "./sections/UsageSection";

type ThemeChoice = "system" | "light" | "dark";

function useThemeChoice() {
  const [choice, setChoice] = useState<ThemeChoice>(() => {
    try {
      return (localStorage.getItem("cz-theme") as ThemeChoice) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    try {
      localStorage.setItem("cz-theme", choice);
    } catch {
      /* 无痕模式等场景下忽略 */
    }
  }, [choice]);
  return [choice, setChoice] as const;
}

const nav = [
  ["tokens", "Tokens"],
  ["color", "色彩"],
  ["type", "字体"],
  ["spacing", "间距"],
  ["radius", "圆角"],
  ["elevation", "阴影"],
  ["motion", "动效"],
  ["components", "组件"],
  ["usage", "接入"],
] as const;

export function App() {
  const [theme, setTheme] = useThemeChoice();
  return (
    <TooltipProvider>
      <header className="sticky top-0 z-40 border-b border-line-subtle bg-canvas/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <a href="#top" className="flex items-center gap-2 rounded-sm outline-none focus-visible:shadow-ring">
            <Mark />
            <span className="text-body font-semibold tracking-tight">CZ Design System</span>
            <span className="hidden font-mono text-caption font-normal text-fg-subtle sm:inline">v0.1</span>
          </a>
          <nav aria-label="章节" className="ml-auto hidden items-center gap-0.5 lg:flex">
            {nav.map(([id, label]) => (
              <a
                key={id}
                href={`#${id}`}
                className="rounded-sm px-2 py-1 text-footnote text-fg-muted transition hover:bg-hover hover:text-fg outline-none focus-visible:shadow-ring"
              >
                {label}
              </a>
            ))}
          </nav>
          <Tabs value={theme} onValueChange={(v) => setTheme(v as ThemeChoice)} className="ml-auto lg:ml-3">
            <TabsList aria-label="主题">
              <TabsTrigger value="system" aria-label="跟随系统" className="px-2">
                <Monitor />
              </TabsTrigger>
              <TabsTrigger value="light" aria-label="浅色" className="px-2">
                <Sun />
              </TabsTrigger>
              <TabsTrigger value="dark" aria-label="深色" className="px-2">
                <Moon />
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </header>

      <main id="top" className="mx-auto flex max-w-6xl flex-col gap-24 px-4 pt-12 pb-24">
        <Hero />
        <Principles />
        <ColorSection />
        <TypographySection />
        <SpacingSection />
        <RadiusSection />
        <ElevationSection />
        <MotionSection />
        <ComponentsSection />
        <UsageSection />
      </main>

      <footer className="border-t border-line-subtle">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-footnote text-fg-subtle">
          <span>CZ Design System · 面向 AI Native 产品</span>
          <span className="font-mono">React · Tailwind v4 · Radix · Framer Motion</span>
        </div>
      </footer>
    </TooltipProvider>
  );
}

/** 品牌标记：两段错开的圆弧，像一次“往返”的对话。 */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={cn("size-6", className)}>
      <rect width="24" height="24" rx="7" className="fill-accent" />
      <path d="M13.5 7.2a5 5 0 1 0 0 9.6" fill="none" strokeWidth="2" strokeLinecap="round" className="stroke-on-accent" />
      <circle cx="16.3" cy="12" r="1.6" className="fill-on-accent" />
    </svg>
  );
}

function Hero() {
  return (
    <section className="grid items-start gap-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="flex flex-col gap-6 pt-2">
        <span className="font-mono text-caption font-normal text-accent-fg">@cz/design-system</span>
        <h1 className="text-display-sm text-fg sm:text-display">
          为会思考、会动手的产品
          <br />
          设计的界面语言
        </h1>
        <p className="max-w-lg text-body-lg text-fg-muted">
          一套 token 驱动的设计系统。颜色、字体、间距、圆角、阴影、动效都从同一份源文件生成，组件只认 token，
          换一套 token 就是另一个产品的皮肤。旁边的 Agent 面板完全由系统组件拼成。
        </p>
        <dl className="grid grid-cols-3 gap-4 border-t border-line-subtle pt-4">
          {[
            ["7", "类基础 token"],
            ["15", "个组件"],
            ["AA", "明暗双主题对比度"],
          ].map(([n, label]) => (
            <div key={label} className="flex flex-col gap-1">
              <dt className="order-2 text-footnote text-fg-muted">{label}</dt>
              <dd className="text-heading tabular-nums text-fg">{n}</dd>
            </div>
          ))}
        </dl>
      </div>
      <AgentDemo />
    </section>
  );
}

function Principles() {
  const items = [
    ["克制", "界面退后，内容向前。一个强调色，三种字重，大面积留白；颜色只在需要注意时出现。"],
    ["精致的动效", "每个动效都在解释状态变化：进入减速、退出加速，空间变化用弹簧，位移不超过 8px。"],
    ["专业", "4px 网格、统一的控件高度、等宽数字。信息密度可调，但对齐永远精确。"],
  ];
  return (
    <section id="tokens" className="grid gap-6 sm:grid-cols-3">
      {items.map(([title, body]) => (
        <div key={title} className="flex flex-col gap-2 border-t border-line pt-4">
          <h2 className="text-title-sm">{title}</h2>
          <p className="text-body text-fg-muted">{body}</p>
        </div>
      ))}
    </section>
  );
}

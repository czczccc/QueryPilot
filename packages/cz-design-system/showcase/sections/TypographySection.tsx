import { fontFamily, textStyles, type TextStyleName } from "../../src";
import { SectionHeader } from "./SectionHeader";

const samples: Partial<Record<TextStyleName, string>> = {
  "display-lg": "让工作自己完成",
  display: "Agent 已接手这项任务",
  "display-sm": "还没有订阅，先搜一部剧试试",
  heading: "订阅与自动转存",
  title: "确认转存到网盘？",
  "title-sm": "繁花 · 第 30 集",
  "body-lg": "我找到了 3 个可用的来源，推荐第一个：画质最好，分享者历史可信度 94%。",
  body: "每 30 分钟检查一次更新，发现新集后自动转存并通知你。",
  footnote: "上次检查 12 分钟前 · 耗时 1.7 秒",
  caption: "2.1 GB · 2160p · HDR",
};

// 类名需要完整写出，Tailwind 才能扫描到
const textClass: Record<TextStyleName, string> = {
  caption: "text-caption",
  footnote: "text-footnote",
  body: "text-body",
  "body-lg": "text-body-lg",
  "title-sm": "text-title-sm",
  title: "text-title",
  heading: "text-heading",
  "display-sm": "text-display-sm",
  display: "text-display",
  "display-lg": "text-display-lg",
};

export function TypographySection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="type" source="src/tokens/typography.ts" title="字体">
        界面字体 Instrument Sans，中文回落到苹方、鸿蒙黑体、思源黑体；代码和数据用 JetBrains Mono。
        正文基准 14px，只用 400、500、600 三种字重，层级靠字号和颜色建立，大字号逐级收紧字距。
      </SectionHeader>

      <div className="flex flex-col">
        {(Object.keys(textStyles) as TextStyleName[]).map((name) => {
          const s = textStyles[name];
          return (
            <div
              key={name}
              className="grid gap-1.5 border-t border-line-subtle py-4 md:grid-cols-[11rem_minmax(0,1fr)] md:gap-6"
            >
              <div className="flex flex-col gap-0.5">
                <code className="font-mono text-footnote text-fg">text-{name}</code>
                <span className="font-mono text-caption font-normal text-fg-subtle tabular-nums">
                  {parseFloat(s.size) * 16}/{parseFloat(s.lineHeight) * 16} · {s.weight}
                </span>
                <span className="text-caption font-normal text-fg-subtle">{s.usage}</span>
              </div>
              <p className={`${textClass[name]} min-w-0 text-fg`}>{samples[name]}</p>
            </div>
          );
        })}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <FontCard name="Instrument Sans" role="界面 · font-sans" stack={fontFamily.sans} sample="Aa 永 0123" mono={false} />
        <FontCard name="JetBrains Mono" role="代码与数据 · font-mono" stack={fontFamily.mono} sample="{ } => 0123" mono />
      </div>
    </section>
  );
}

function FontCard({ name, role, stack, sample, mono }: { name: string; role: string; stack: string; sample: string; mono: boolean }) {
  return (
    <div className="flex flex-col gap-3 rounded-card border border-line-subtle bg-surface p-4">
      <span className={`${mono ? "font-mono" : "font-sans"} text-display text-fg`}>{sample}</span>
      <div className="flex flex-col gap-0.5">
        <span className="text-body font-medium">{name}</span>
        <span className="text-caption font-normal text-fg-subtle">{role}</span>
        <code className="font-mono text-caption font-normal break-all text-fg-subtle">{stack}</code>
      </div>
    </div>
  );
}

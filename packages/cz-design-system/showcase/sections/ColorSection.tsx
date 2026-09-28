import { oklch, primitives, semantic, type SemanticColor } from "../../src";
import { Panel, SectionHeader } from "./SectionHeader";

const rampInfo: Record<keyof typeof primitives, string> = {
  graphite: "石墨 · 中性色，带一点青绿偏色",
  pine: "松石 · 品牌强调色",
  moss: "苔绿 · 成功",
  amber: "琥珀 · 警告",
  rust: "赭红 · 危险",
  mist: "雾蓝 · 信息",
};

const groups: { title: string; keys: SemanticColor[] }[] = [
  { title: "背景", keys: ["canvas", "surface", "raised", "sunken", "hover", "selected", "inverse"] },
  { title: "文字", keys: ["fg", "fg-muted", "fg-subtle", "fg-disabled", "accent-fg"] },
  { title: "边框", keys: ["line-subtle", "line", "line-strong", "focus"] },
  { title: "强调", keys: ["accent", "accent-hover", "accent-soft", "accent-line", "on-accent"] },
  { title: "状态", keys: ["success", "warning", "danger", "info", "danger-soft", "danger-fg"] },
];

export function ColorSection() {
  return (
    <section className="flex flex-col gap-8">
      <SectionHeader id="color" source="src/tokens/color.ts" title="色彩">
        两层结构。原始色阶用 OKLCH 定义，同一档位在不同色相上明度一致，超出 sRGB 的彩度会自动收回；
        组件只使用语义色，明暗主题各自映射到原始色阶。所有文字组合都通过了 WCAG AA 对比度测试。
      </SectionHeader>

      <div className="flex flex-col gap-4">
        {(Object.keys(primitives) as (keyof typeof primitives)[]).map((name) => {
          const steps = Object.entries(primitives[name]);
          return (
            <div key={name} className="grid gap-2 md:grid-cols-[12rem_minmax(0,1fr)] md:items-center">
              <div className="flex flex-col">
                <span className="font-mono text-footnote text-fg">{name}</span>
                <span className="text-caption font-normal text-fg-subtle">{rampInfo[name]}</span>
              </div>
              <div className="overflow-x-auto">
                <div className="flex min-w-max gap-0.5 md:min-w-0">
                  {steps.map(([step, v]) => (
                    <div key={step} className="flex w-11 flex-col gap-1 md:w-auto md:flex-1">
                      <div
                        className="h-10 rounded-xs border border-line-subtle"
                        style={{ background: oklch(v) }}
                        title={`${name}.${step} ${oklch(v)}`}
                      />
                      <span className="text-center font-mono text-caption font-normal text-fg-subtle tabular-nums">
                        {step}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {(["light", "dark"] as const).map((theme) => (
          <div key={theme} data-theme={theme} className="rounded-dialog border border-line bg-canvas p-4">
            <div className="mb-4 flex items-baseline justify-between">
              <h3 className="text-title-sm">{theme === "light" ? "浅色主题" : "深色主题"}</h3>
              <span className="font-mono text-caption font-normal text-fg-subtle">[data-theme="{theme}"]</span>
            </div>
            <div className="flex flex-col gap-4">
              {groups.map((g) => (
                <div key={g.title} className="flex flex-col gap-1.5">
                  <span className="text-caption text-fg-subtle">{g.title}</span>
                  <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                    {g.keys.map((k) => {
                      const ref = semantic[theme][k];
                      return (
                        <div key={k} className="flex items-center gap-2">
                          <span
                            className="size-7 shrink-0 rounded-sm border border-line-subtle"
                            style={{ background: `var(--cz-color-${k})` }}
                          />
                          <span className="flex min-w-0 flex-col">
                            <span className="truncate font-mono text-caption font-normal text-fg">{k}</span>
                            <span className="truncate font-mono text-caption font-normal text-fg-subtle">
                              {ref[0]}.{ref[1]}
                              {ref[2] !== undefined ? ` / ${Math.round(ref[2] * 100)}%` : ""}
                            </span>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <Panel className="grid gap-4 p-4 sm:grid-cols-2">
        <Rule title="一个强调色" body="松石色只用于主操作、选中和焦点。同一屏里实心强调色最好只出现一次。" />
        <Rule title="状态色不是装饰" body="成功、警告、危险、信息只在表达状态时出现，并且总是搭配图标或文字，不靠颜色单独传达。" />
      </Panel>
    </section>
  );
}

function Rule({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-body font-medium">{title}</span>
      <span className="text-footnote text-fg-muted">{body}</span>
    </div>
  );
}

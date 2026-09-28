/**
 * 把 TypeScript token 渲染成 CSS 自定义属性（--cz-*）。
 * 由 scripts/build-tokens.ts 调用生成 src/styles/tokens.css；测试会校验生成结果与源码一致。
 */
import { oklch, resolve, semantic, type Theme } from "./color";
import { cubicBezier, duration, easing } from "./motion";
import { radius, radiusRoles } from "./radius";
import { focusRing, shadow } from "./shadow";
import { controlHeight, spacing, spacingBase } from "./spacing";
import { fontFamily, fontWeight, textStyles } from "./typography";

const decl = (name: string, value: string | number) => `  --cz-${name}: ${value};`;

const colorDecls = (theme: Theme) =>
  Object.entries(semantic[theme]).map(([name, ref]) => decl(`color-${name}`, oklch(resolve(ref))));

const shadowDecls = (theme: Theme) =>
  Object.entries(shadow[theme]).map(([name, value]) => decl(`shadow-${name}`, value));

const themeBlock = (theme: Theme) => [
  `  color-scheme: ${theme};`,
  ...colorDecls(theme),
  ...shadowDecls(theme),
];

const staticDecls = () => [
  decl("font-sans", fontFamily.sans),
  decl("font-mono", fontFamily.mono),
  ...Object.entries(fontWeight).map(([k, v]) => decl(`font-weight-${k}`, v)),
  ...Object.entries(textStyles).flatMap(([name, s]) => [
    decl(`text-${name}`, s.size),
    decl(`text-${name}-leading`, s.lineHeight),
    decl(`text-${name}-tracking`, s.tracking),
    decl(`text-${name}-weight`, s.weight),
  ]),
  decl("spacing", spacingBase),
  ...Object.entries(spacing).map(([k, v]) => decl(`space-${k.replace(".", "_")}`, `${v.px / 16}rem`)),
  ...Object.entries(controlHeight).map(([k, v]) => decl(`control-${k}`, v)),
  ...Object.entries(radius).map(([k, v]) => decl(`radius-${k}`, v)),
  ...Object.entries(radiusRoles).map(([k, v]) => decl(`radius-${k}`, `var(--cz-radius-${v})`)),
  ...Object.entries(duration).map(([k, v]) => decl(`duration-${k}`, `${v}ms`)),
  ...Object.entries(easing).map(([k, v]) => decl(`ease-${k}`, cubicBezier(v))),
  decl("ring", focusRing),
];

export function renderTokensCss(): string {
  const light = themeBlock("light");
  const dark = themeBlock("dark");
  return [
    "/* 由 scripts/build-tokens.ts 根据 src/tokens/*.ts 生成，请勿手改。运行 npm run tokens 重新生成。 */",
    "",
    ":root {",
    ...staticDecls(),
    ...light,
    "}",
    "",
    "/* 跟随系统：未显式指定浅色时，系统深色即启用深色主题 */",
    "@media (prefers-color-scheme: dark) {",
    "  :root:not([data-theme=\"light\"]) {",
    ...dark.map((l) => `  ${l}`),
    "  }",
    "}",
    "",
    "/* 显式指定：可以加在 <html> 上切换全局，也可以加在任意容器上做局部主题 */",
    "[data-theme=\"light\"] {",
    ...light,
    "}",
    "",
    "[data-theme=\"dark\"] {",
    ...dark,
    "}",
    "",
  ].join("\n");
}

/**
 * Tailwind v4 主题映射。先用 `initial` 清空 Tailwind 自带的调色板、字号、圆角、阴影、缓动，
 * 这样组件里写 bg-zinc-100、rounded-lg（默认值）之类的类名根本不会生效，只能使用 CZ token。
 * 使用 `@theme inline`，工具类直接引用 --cz-* 变量，局部 data-theme 切换可以正确继承。
 */
export function renderThemeCss(): string {
  const lines: string[] = [
    "/* 由 scripts/build-tokens.ts 生成，请勿手改。 */",
    "",
    "@theme inline {",
    "  --color-*: initial;",
    "  --font-*: initial;",
    "  --text-*: initial;",
    "  --font-weight-*: initial;",
    "  --tracking-*: initial;",
    "  --leading-*: initial;",
    "  --radius-*: initial;",
    "  --shadow-*: initial;",
    "  --inset-shadow-*: initial;",
    "  --drop-shadow-*: initial;",
    "  --ease-*: initial;",
    "  --animate-*: initial;",
    "",
    "  --spacing: var(--cz-spacing);",
    "  --color-transparent: transparent;",
    "  --color-current: currentColor;",
  ];
  for (const name of Object.keys(semantic.light)) lines.push(`  --color-${name}: var(--cz-color-${name});`);
  lines.push("", "  --font-sans: var(--cz-font-sans);", "  --font-mono: var(--cz-font-mono);");
  for (const k of Object.keys(fontWeight)) lines.push(`  --font-weight-${k}: var(--cz-font-weight-${k});`);
  for (const name of Object.keys(textStyles)) {
    lines.push(
      `  --text-${name}: var(--cz-text-${name});`,
      `  --text-${name}--line-height: var(--cz-text-${name}-leading);`,
      `  --text-${name}--letter-spacing: var(--cz-text-${name}-tracking);`,
      `  --text-${name}--font-weight: var(--cz-text-${name}-weight);`,
    );
  }
  lines.push("");
  for (const k of Object.keys(controlHeight)) lines.push(`  --spacing-control-${k}: var(--cz-control-${k});`);
  lines.push("");
  for (const k of [...Object.keys(radius), ...Object.keys(radiusRoles)])
    lines.push(`  --radius-${k}: var(--cz-radius-${k});`);
  lines.push("");
  for (const k of Object.keys(shadow.light)) lines.push(`  --shadow-${k}: var(--cz-shadow-${k});`);
  lines.push("  --shadow-ring: var(--cz-ring);", "");
  for (const k of Object.keys(easing)) lines.push(`  --ease-${k}: var(--cz-ease-${k});`);
  lines.push(
    "",
    "  --animate-shimmer: cz-shimmer var(--cz-duration-ambient) var(--cz-ease-linear) infinite;",
    "  --animate-breathe: cz-breathe var(--cz-duration-ambient) var(--cz-ease-standard) infinite;",
    "  --animate-halo: cz-halo var(--cz-duration-ambient) var(--cz-ease-exit) infinite;",
    "  --animate-spin: cz-spin 900ms var(--cz-ease-linear) infinite;",
    "",
    "  --default-transition-duration: var(--cz-duration-fast);",
    "  --default-transition-timing-function: var(--cz-ease-standard);",
    "}",
    "",
    "/* 时长工具类：duration-fast、duration-base…… */",
  );
  for (const k of Object.keys(duration))
    lines.push(`@utility duration-${k} {`, `  transition-duration: var(--cz-duration-${k});`, "}");
  lines.push("");
  return lines.join("\n");
}

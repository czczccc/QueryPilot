// @vitest-environment node
import { readFileSync } from "node:fs";
import { converter, displayable, wcagContrast } from "culori";
import { describe, expect, it } from "vitest";
import { primitives, resolve, semantic, type SemanticColor, type Theme } from "../src/tokens/color";
import { renderThemeCss, renderTokensCss } from "../src/tokens/css";

const toRgb = converter("rgb");
const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");

/** 把带透明度的颜色叠到背景上，得到实际看到的颜色 */
type Rgb = { mode: "rgb"; r: number; g: number; b: number };
function flatten(theme: Theme, name: SemanticColor, over: SemanticColor = "canvas"): Rgb {
  const c = resolve(semantic[theme][name]);
  const fg = toRgb({ mode: "oklch", l: c.l, c: c.c, h: c.h })!;
  const a = c.alpha ?? 1;
  if (a === 1) return fg;
  const b = flatten(theme, over);
  return { mode: "rgb" as const, r: fg.r * a + b.r * (1 - a), g: fg.g * a + b.g * (1 - a), b: fg.b * a + b.b * (1 - a) };
}

const contrast = (theme: Theme, fg: SemanticColor, bg: SemanticColor) =>
  wcagContrast(flatten(theme, fg, bg), flatten(theme, bg));

describe("颜色 token", () => {
  it("所有原始色阶都在 sRGB 色域内", () => {
    for (const [name, ramp] of Object.entries(primitives)) {
      for (const [step, v] of Object.entries(ramp)) {
        expect(displayable({ mode: "oklch", l: v.l, c: v.c, h: v.h }), `${name}.${step}`).toBe(true);
      }
    }
  });

  it("明暗两套主题定义了同一组语义色", () => {
    expect(Object.keys(semantic.dark).sort()).toEqual(Object.keys(semantic.light).sort());
  });

  // [前景, 背景, 最低对比度]；正文 4.5:1（WCAG AA），大字/图标/焦点 3:1
  const pairs: [SemanticColor, SemanticColor, number][] = [
    ["fg", "canvas", 12],
    ["fg", "surface", 12],
    ["fg", "raised", 12],
    ["fg-muted", "canvas", 4.5],
    ["fg-muted", "surface", 4.5],
    ["fg-subtle", "surface", 3],
    ["accent-fg", "canvas", 4.5],
    ["accent-fg", "accent-soft", 4.5],
    ["on-accent", "accent", 4.5],
    ["on-accent", "accent-hover", 4.5],
    ["on-accent", "danger", 4.5],
    ["fg-inverse", "inverse", 7],
    ["success-fg", "success-soft", 4.5],
    ["warning-fg", "warning-soft", 4.5],
    ["danger-fg", "danger-soft", 4.5],
    ["info-fg", "info-soft", 4.5],
    ["focus", "canvas", 3],
    ["accent", "canvas", 3],
  ];

  for (const theme of ["light", "dark"] as const) {
    for (const [fg, bg, min] of pairs) {
      it(`${theme}: ${fg} 在 ${bg} 上对比度 ≥ ${min}`, () => {
        expect(contrast(theme, fg, bg)).toBeGreaterThanOrEqual(min);
      });
    }
  }
});

describe("生成的 CSS", () => {
  it("tokens.css 与 TypeScript 源一致（修改 token 后请运行 npm run tokens）", () => {
    expect(read("../src/styles/tokens.css")).toBe(renderTokensCss());
  });
  it("theme.css 与 TypeScript 源一致", () => {
    expect(read("../src/styles/theme.css")).toBe(renderThemeCss());
  });
});

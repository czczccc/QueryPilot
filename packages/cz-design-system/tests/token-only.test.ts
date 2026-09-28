// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * 守住“所有组件必须基于 token”这条规则：组件源码里不允许出现
 * 硬编码颜色、任意值尺寸、Tailwind 默认色板类名、shadcn 默认语义色类名。
 */
const dir = fileURLToPath(new URL("../src/components", import.meta.url));
const files = readdirSync(dir, { recursive: true, encoding: "utf8" })
  .filter((f) => f.endsWith(".tsx"))
  .map((f) => ({ name: f, text: stripAllowed(readFileSync(join(dir, f), "utf8")) }));

/** 去掉注释，以及 data-[state=x]: / aria-[x]: 这类属性选择器变体（它们不是样式值） */
function stripAllowed(src: string) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\b(?:group-|peer-)?(?:data|aria)-\[[^\]]+\]:/g, "");
}

const tailwindPalette =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black";

const rules: [string, RegExp][] = [
  ["十六进制颜色", /#[0-9a-fA-F]{3,8}\b/],
  ["rgb/hsl/oklch 颜色函数", /\b(rgba?|hsla?|oklch|oklab)\(/],
  ["Tailwind 默认色板", new RegExp(`\\b(bg|text|border|ring|fill|stroke|from|to|via|shadow|outline|divide)-(${tailwindPalette})(-\\d+)?\\b`)],
  [
    "shadcn 默认语义色",
    /\b(bg|text|border|ring)-(background|foreground|primary|secondary|muted|card|popover|destructive|input)(-foreground)?\b/,
  ],
  ["任意值（方括号）尺寸、颜色、阴影", /\b(?:[a-z]+-)+\[(?!&)[^\]]+\]/],
  ["px 单位的内联尺寸", /\b\d+px\b/],
];

describe("组件只使用 design token", () => {
  it("找到了组件文件", () => expect(files.length).toBeGreaterThan(10));
  for (const { name, text } of files) {
    for (const [label, re] of rules) {
      it(`${name} 不含${label}`, () => {
        const hit = text.match(re);
        expect(hit?.[0], hit ? `${name}: ${hit[0]}` : undefined).toBeUndefined();
      });
    }
  }
});

import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import { textStyles } from "../tokens/typography";

/**
 * tailwind-merge 默认不认识 CZ 的自定义字号（text-body、text-title……），
 * 会把它们当成颜色，和 text-fg 互相覆盖。这里显式注册。
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: Object.keys(textStyles) }],
      shadow: [{ shadow: ["xs", "sm", "md", "lg", "xl", "ring"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

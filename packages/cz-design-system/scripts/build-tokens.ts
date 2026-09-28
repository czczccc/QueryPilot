import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderThemeCss, renderTokensCss } from "../src/tokens/css";

const out = (p: string) => fileURLToPath(new URL(p, import.meta.url));

writeFileSync(out("../src/styles/tokens.css"), renderTokensCss());
writeFileSync(out("../src/styles/theme.css"), renderThemeCss());
console.log("已生成 src/styles/tokens.css 与 src/styles/theme.css");

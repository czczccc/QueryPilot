import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { viteSingleFile } from "vite-plugin-singlefile";

// 构建产物是展示页（单个 HTML 文件，方便预览和分享）；组件库本身以源码形式被引用。
export default defineConfig({
  root: "showcase",
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: { outDir: "../dist", emptyOutDir: true },
  test: {
    root: ".",
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});

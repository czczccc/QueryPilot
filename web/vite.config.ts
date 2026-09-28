import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

// 新前端挂在 /next/，构建产物放到 app/web，由 FastAPI 提供。
// 设计系统以源码引用；React、Framer Motion 等统一用本目录的依赖，避免打进两份。
export default defineConfig({
  base: "/next/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@cz/design-system": here("../packages/cz-design-system/src/index.ts") },
    dedupe: ["react", "react-dom", "framer-motion", "radix-ui", "lucide-react", "clsx", "tailwind-merge", "class-variance-authority"],
  },
  build: { outDir: here("../app/web"), emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
});

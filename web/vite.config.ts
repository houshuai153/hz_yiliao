/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发时 /api 经同源代理转发到 FastAPI：浏览器只和 Vite（同源）通信，会话 Cookie 与 CSRF 的行为与部署时一致。
// API_TOKEN 不出现在任何前端文件或构建产物中。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: process.env.API_ORIGIN ?? "http://127.0.0.1:8000", changeOrigin: false } } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});

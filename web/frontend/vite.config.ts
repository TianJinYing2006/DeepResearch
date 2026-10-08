/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// W9 最小骨架：开发期 Vite(5173) 反代后端(8000)，生产由 FastAPI 托管 dist/。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  // 单测范围**必须**显式限定到 src：vitest 默认 include 是 `**/*.{test,spec}.?(c|m)[jt]s?(x)`，
  // 会把 Playwright 的 `e2e/*.spec.ts` 一并吞进来 —— 两套 runner 的 `test` 全局不同源，
  // 结果是 vitest 报 9 个「Playwright Test did not expect test.describe() to be called here」。
  test: {
    include: ['src/**/*.test.ts'],
  },
})

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
})

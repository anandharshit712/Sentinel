import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev: vite (5173) proxies API + SSE to the Gateway (8000). Same relative paths in prod
// (Gateway serves the built SPA from static/) so fetch('/api/...') works in both (06 §11).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/healthz': 'http://localhost:8000',
    },
  },
})

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, the API (FastAPI on 8421) is proxied so the app and its SSE streams share one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8421', changeOrigin: false },
    },
  },
})

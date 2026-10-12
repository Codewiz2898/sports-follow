import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, the API (FastAPI on 8421) is proxied so the app and its SSE streams share one origin.
// SPORTS_FOLLOW_API points a second checkout's app at its own API.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: process.env.SPORTS_FOLLOW_API ?? 'http://127.0.0.1:8421', changeOrigin: false },
    },
  },
})

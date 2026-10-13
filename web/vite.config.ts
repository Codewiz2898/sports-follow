import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * Stamps the build into the service worker (public/sw.js, `const BUILD`): a hash of every other file
 * the build writes. A deploy that changes anything is then a new sw.js, which browsers install and the
 * app offers with Reload; a build that changes nothing keeps the worker it had.
 */
function stampServiceWorker(): Plugin {
  return {
    name: 'stamp-service-worker',
    apply: 'build',
    writeBundle({ dir }) {
      if (!dir) return
      const sw = join(dir, 'sw.js')
      const files = (d: string): string[] => readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(d, e.name)) : [join(d, e.name)]))
      const hash = createHash('sha256')
      for (const file of files(dir).filter((f) => f !== sw).sort()) hash.update(relative(dir, file)).update(readFileSync(file))
      writeFileSync(sw, readFileSync(sw, 'utf8').replace(/const BUILD = '[^']*'/, `const BUILD = '${hash.digest('hex').slice(0, 12)}'`))
    },
  }
}

// In dev, the API (FastAPI on 8421) is proxied so the app and its SSE streams share one origin.
// SPORTS_FOLLOW_API points a second checkout's app at its own API.
export default defineConfig({
  plugins: [react(), stampServiceWorker()],
  server: {
    proxy: {
      '/api': { target: process.env.SPORTS_FOLLOW_API ?? 'http://127.0.0.1:8421', changeOrigin: false },
    },
  },
})

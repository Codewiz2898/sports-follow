import { createHash } from 'node:crypto'
import { mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * The app's name as fans see it, written once in sports_follow/app.json (the API's config.APP_NAME).
 * The build fills it in wherever the web app says it: %APP_NAME% in index.html, the manifest and the
 * service worker, and APP_NAME in the app's code (src/app.ts). The Docker image's web stage copies that
 * one file in for this.
 */
function appName(): Plugin {
  const name: string = JSON.parse(readFileSync(new URL('../sports_follow/app.json', import.meta.url), 'utf8')).name
  // It goes in as written: into HTML, a JSON string and a quoted JS string (tests/test_app_name.py).
  if (!name || /['"`&<>\\]/.test(name)) throw new Error(`sports_follow/app.json: the name ${JSON.stringify(name)} is empty or has a quote, &, < or > in it`)
  const fill = (text: string) => text.replaceAll('%APP_NAME%', name)
  const filled = ['manifest.webmanifest', 'sw.js']
  return {
    name: 'app-name',
    config: () => ({ define: { __APP_NAME__: JSON.stringify(name) } }),
    transformIndexHtml: { order: 'pre', handler: fill },
    // In development the public files are served as they are: these two are served filled.
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const file = filled.find((f) => req.url?.split('?')[0] === `/${f}`)
        if (!file) return next()
        res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : 'application/manifest+json')
        res.end(fill(readFileSync(join(server.config.publicDir, file), 'utf8')))
      })
    },
    // Before stampServiceWorker hashes the build, so the name is part of it.
    writeBundle: {
      order: 'pre',
      handler({ dir }) {
        if (!dir) return
        for (const file of filled) writeFileSync(join(dir, file), fill(readFileSync(join(dir, file), 'utf8')))
      },
    },
  }
}

/**
 * Stamps the build into the service worker (public/sw.js): `const BUILD`, a hash of every other file the build
 * writes, and the build's files (/assets/), which the worker stores as it installs (`BUILD_FILES`), so the app opens
 * offline from a fan's first visit and right after a deploy (BUG-305, BUG-310); those over STORE_LATER_BYTES (the
 * chess engine's) are stored when first used (`BUILD_FILES_LATER`). A deploy that changes anything is then a new
 * sw.js, which browsers install and the app offers with Reload; a build that changes nothing keeps the worker it had.
 */
const STORE_LATER_BYTES = 1_000_000

function stampServiceWorker(): Plugin {
  return {
    name: 'stamp-service-worker',
    apply: 'build',
    writeBundle({ dir }) {
      if (!dir) return
      const sw = join(dir, 'sw.js')
      const files = (d: string): string[] => readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(d, e.name)) : [join(d, e.name)]))
      const built = files(dir).filter((f) => f !== sw).sort()
      const hash = createHash('sha256')
      for (const file of built) hash.update(relative(dir, file)).update(readFileSync(file))
      const address = (file: string) => `/${relative(dir, file).split(sep).join('/')}`
      const assets = built.filter((f) => address(f).startsWith('/assets/'))
      const later = (file: string) => statSync(file).size > STORE_LATER_BYTES
      const stamps: [RegExp, string][] = [
        [/const BUILD = '[^']*'/, `const BUILD = '${hash.digest('hex').slice(0, 12)}'`],
        [/const BUILD_FILES = \[\]/, `const BUILD_FILES = ${JSON.stringify(assets.filter((f) => !later(f)).map(address))}`],
        [/const BUILD_FILES_LATER = \[\]/, `const BUILD_FILES_LATER = ${JSON.stringify(assets.filter(later).map(address))}`],
      ]
      let text = readFileSync(sw, 'utf8')
      for (const [blank, stamp] of stamps) {
        if (!blank.test(text)) throw new Error(`public/sw.js: nothing to stamp at ${blank}`)
        text = text.replace(blank, stamp)
      }
      writeFileSync(sw, text)
    },
  }
}

/**
 * The build's source maps, for the server to read the app's error stacks with (sports_follow/web_errors.py):
 * "hidden" (the code doesn't point browsers at them), and moved from dist to web/sourcemaps, which the API
 * doesn't serve. Before stampServiceWorker hashes the build, so they aren't part of it.
 */
function sourceMapsAside(): Plugin {
  return {
    name: 'source-maps-aside',
    apply: 'build',
    writeBundle: {
      order: 'pre',
      handler({ dir }) {
        if (!dir) return
        const assets = join(dir, 'assets')
        const aside = join(dir, '..', 'sourcemaps')
        rmSync(aside, { recursive: true, force: true })
        mkdirSync(aside, { recursive: true })
        for (const file of readdirSync(assets).filter((f) => f.endsWith('.map'))) renameSync(join(assets, file), join(aside, file))
      },
    },
  }
}

// In dev, the API (FastAPI on 8421) is proxied so the app and its SSE streams share one origin.
// SPORTS_FOLLOW_API points a second checkout's app at its own API.
export default defineConfig({
  plugins: [react(), appName(), sourceMapsAside(), stampServiceWorker()],
  build: { sourcemap: 'hidden' },
  server: {
    proxy: {
      '/api': { target: process.env.SPORTS_FOLLOW_API ?? 'http://127.0.0.1:8421', changeOrigin: false },
    },
  },
})

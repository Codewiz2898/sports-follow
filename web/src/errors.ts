/**
 * What goes wrong in the app, sent to the server's log (POST /api/errors; sports_follow/web_errors.py),
 * where it's grouped with the API's and the worker's own errors: uncaught errors and promise rejections
 * (watchErrors), renders that failed (main.tsx's root) and API calls answered with a server error (api.ts).
 * A report has the page without its query string, this build's version and, for a call, its request id.
 * Reporting never throws, and a page sends each error once and at most MAX in all.
 */

// This build: the name of the file the app was bundled into ("index-B3kd8"), which changes whenever the
// app does (and only then); "dev" in development. The server reads stacks with the same build's source maps.
export const VERSION = import.meta.env.PROD ? (new URL(import.meta.url).pathname.split('/').pop() ?? '').replace(/\.js$/, '') : 'dev'

const MAX = 10
const sent = new Set<string>()
// Not the app's: a browser's own complaint, a script from another site that hides its error, an extension.
const IGNORED = /^(ResizeObserver loop|Script error\.?$)/
const EXTENSION = /(chrome|moz|safari(-web)?)-extension:\/\//

export interface Failure {
  kind: 'error' | 'rejection' | 'render' | 'api'
  call?: string  // a failed API call: "GET /api/players/7/card"
  status?: number
  requestId?: string | null
  componentStack?: string
}

export function report(failure: Failure, error: unknown): void {
  try {
    const e = error instanceof Error ? error : null
    const name = e?.name || 'Error'
    const message = e ? e.message : String(error ?? '')
    let stack = e?.stack ?? ''
    if (IGNORED.test(message) || EXTENSION.test(stack)) return
    if (failure.componentStack) stack += `\nComponent stack:${failure.componentStack}`
    const key = [failure.kind, name, message, failure.call ?? '', stack.split('\n', 3).join('\n')].join('\n')
    if (sent.has(key) || sent.size >= MAX) return
    sent.add(key)
    const body = {
      kind: failure.kind, name: name.slice(0, 120), message: message.slice(0, 2000), stack: stack.slice(0, 16000),
      page: location.pathname, version: VERSION, call: failure.call, status: failure.status, request_id: failure.requestId ?? undefined,
    }
    void fetch('/api/errors', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin', keepalive: true,
    }).catch(() => {})
  } catch { /* a report must never be what breaks the page */ }
}

/** Errors nothing caught, from now on. */
export function watchErrors(): void {
  window.addEventListener('error', (event) => {
    if (event instanceof ErrorEvent) report({ kind: 'error' }, event.error ?? event.message)
  })
  window.addEventListener('unhandledrejection', (event) => {
    // A refusal from the API nobody handled: api.ts reported it already if it was the server's fault.
    if ((event.reason as Error | undefined)?.name === 'ApiError') return
    report({ kind: 'rejection' }, event.reason)
  })
}

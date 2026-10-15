// The API client. Its types are in types.ts, shared with the Android app (mobile/).

import { endpoints } from './endpoints'
import { report } from './errors'
import type { StreamEvent } from './types'

export type * from './types'

/** A refusal from the API: its message for the fan, and the HTTP status. */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** A call to the API. A server error (5xx) is reported (errors.ts) with the request id the answer names. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch { /* not JSON */ }
    const error = new ApiError(message, res.status)
    if (res.status >= 500) report({ kind: 'api', call: `${init?.method ?? 'GET'} ${path}`, status: res.status, requestId: res.headers.get('x-request-id') }, error)
    throw error
  }
  return res.json() as Promise<T>
}

export const api = endpoints(request)

const RETRY_MS = 1000
const RETRY_MAX_MS = 30_000
const SILENCE_MS = 40_000  // the server pings every 15 s (server.py HEARTBEAT)

/**
 * Subscribe to a server-sent event stream; returns an unsubscribe function. Each stream opens with
 * what it covers as it is now, so every reconnect catches the app up. EventSource retries a dropped
 * connection on its own, but a non-200 answer (the proxy while the API restarts or deploys) closes it
 * for good: that reopens here after 1, 2, 4… up to 30 s. A connection that has gone quiet is dead even
 * if nothing said so (a proxy can keep it open after the API behind it is gone, a phone's can die
 * while it sleeps): that reopens, as does coming back to the app or back online.
 */
export function subscribe(path: string, onEvent: (e: StreamEvent) => void): () => void {
  let source: EventSource | null = null
  let failures = 0
  let retry: number | undefined
  let quiet: number | undefined

  const alive = () => {
    window.clearTimeout(quiet)
    quiet = window.setTimeout(open, SILENCE_MS)
  }
  const open = () => {
    window.clearTimeout(retry)
    source?.close()
    const es = new EventSource(path, { withCredentials: true })
    source = es
    alive()
    es.onopen = () => { failures = 0; alive() }
    es.addEventListener('ping', alive)
    es.onmessage = (msg) => {
      alive()
      try {
        onEvent(JSON.parse(msg.data) as StreamEvent)
      } catch { /* ignore malformed frames */ }
    }
    es.onerror = () => {
      if (es.readyState !== EventSource.CLOSED) return
      window.clearTimeout(quiet)
      // Somewhere in the second half of the delay, so a restart isn't met by every app at once.
      const delay = Math.min(RETRY_MS * 2 ** failures++, RETRY_MAX_MS)
      retry = window.setTimeout(open, delay / 2 + Math.random() * delay / 2)
    }
  }
  const wake = () => {
    if (document.visibilityState !== 'visible') return
    failures = 0
    open()
  }

  open()
  document.addEventListener('visibilitychange', wake)
  window.addEventListener('online', wake)
  return () => {
    window.clearTimeout(retry)
    window.clearTimeout(quiet)
    document.removeEventListener('visibilitychange', wake)
    window.removeEventListener('online', wake)
    source?.close()
  }
}

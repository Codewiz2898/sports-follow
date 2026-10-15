// The API client. Its types are in types.ts, shared with the Android app (mobile/).

import { report } from './errors'
import type { Account, ActivityItem, AlertLevel, AthletePreview, Card, Credits, FollowTarget, Form, GameDetail, LiveSport, Me, PushSettings, Research, ResultsPage, SearchResponse, ServerConfig, StarterSport, StreamEvent, WeekPoster, WireRow } from './types'

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

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  following: () => request<{ players: Card[] }>('/api/me/following'),
  follow: (target: FollowTarget) =>
    request<{ player_id: number; card: Card }>('/api/follows', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(target),
    }),
  unfollow: (playerId: number) => request<{ ok: boolean }>(`/api/follows/${playerId}`, { method: 'DELETE' }),
  card: (playerId: number) => request<Card>(`/api/players/${playerId}/card`),
  results: (playerId: number, before?: string | null) =>
    request<ResultsPage>(`/api/players/${playerId}/results${before ? `?before=${encodeURIComponent(before)}` : ''}`),
  result: (playerId: number, eventId: number) => request<GameDetail>(`/api/players/${playerId}/results/${eventId}`),
  form: (playerId: number, last: number) => request<Form>(`/api/players/${playerId}/form?last=${last}`),
  refresh: (playerId: number) => request<{ queued: boolean; reason?: string }>(`/api/players/${playerId}/refresh`, { method: 'POST' }),
  starters: () => request<{ sports: StarterSport[] }>('/api/starters'),
  live: () => request<{ sports: LiveSport[]; refreshing: boolean }>('/api/live'),
  search: (q: string, sport?: string | null, signal?: AbortSignal) =>
    request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}${sport ? `&sport=${encodeURIComponent(sport)}` : ''}`, { signal }),
  /** A search result before following: system "wikidata" with a qid and sport for registry athletes. */
  athlete: (system: string, id: string, params: Record<string, string | null | undefined>, signal?: AbortSignal) => {
    const query = new URLSearchParams(Object.entries(params).filter((e): e is [string, string] => Boolean(e[1]))).toString()
    return request<AthletePreview | { player_id: number }>(`/api/athletes/${encodeURIComponent(system)}/${encodeURIComponent(id)}${query ? `?${query}` : ''}`, { signal })
  },
  research: () => request<Research>('/api/me/research'),
  setAlerts: (playerId: number, level: AlertLevel) =>
    request<{ player_id: number; alerts: AlertLevel }>(`/api/follows/${playerId}/alerts`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ level }) }),
  pushInfo: () => request<{ enabled: boolean; public_key: string | null }>('/api/push'),
  pushSubscribe: (subscription: PushSubscriptionJSON, quiet: { start: string | null; end: string | null }) =>
    request<PushSettings>('/api/push/subscriptions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ subscription, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, quiet_start: quiet.start, quiet_end: quiet.end }),
    }),
  pushUnsubscribe: (endpoint: string) =>
    request<{ ok: boolean }>('/api/push/subscriptions', { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ endpoint }) }),
  pushTest: () => request<{ sent: number }>('/api/push/test', { method: 'POST' }),
  activity: (before?: number | null) => request<{ items: ActivityItem[]; next: number | null }>(`/api/me/activity${before ? `?before=${before}` : ''}`),
  week: () => request<{ posters: WeekPoster[]; wire: WireRow[] }>('/api/me/week'),
  mutes: () => request<{ event_ids: number[] }>('/api/me/mutes'),
  mute: (eventId: number, muted: boolean) => request<{ event_id: number; muted: boolean }>(`/api/me/mutes/${eventId}`, { method: muted ? 'PUT' : 'DELETE' }),
  config: () => request<ServerConfig>('/api/config'),
  account: () => request<Me>('/api/me/account'),
  emailStart: (email: string) => request<{ ok: boolean; resend_in: number }>('/api/auth/email/start', json('POST', { email })),
  emailVerify: (email: string, code: string) => request<Account>('/api/auth/email/verify', json('POST', { email, code })),
  signOut: (everywhere = false) => request<{ signed_in: false }>('/api/auth/signout', json('POST', { everywhere })),
  deleteAccount: () => request<{ deleted: boolean }>('/api/me/account', json('DELETE', { confirm: true })),
  credits: () => request<Credits>('/api/credits'),
}

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

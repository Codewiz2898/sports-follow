// Every call the API answers, by name: shared by the web app (api.ts, which asks the page's own origin)
// and the Android app (mobile/src/api.ts, which asks stann.in over the network), each with its own way of
// making a request. Types only and calls: nothing of a browser's.

import type { Account, ActivityItem, AlertLevel, AthletePreview, Card, Credits, FollowTarget, Form, GameDetail, League, LiveSport, Me, PushSettings, Research, ResultsPage, SearchResponse, ServerConfig, StarterSport, WeekPoster, WireRow } from './types'

/** Asks the API for a path ("/api/…") and answers its JSON, or throws the refusal. */
export type Request = <T>(path: string, init?: RequestInit) => Promise<T>

export const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export function endpoints(request: Request) {
  return {
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
    // The fan's UTC offset, so "since yesterday" is by their days; fresh: past any copy the browser keeps.
    league: (fresh = false) => request<League>(`/api/league?tz=${-new Date().getTimezoneOffset()}`, fresh ? { cache: 'no-store' } : undefined),
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
}

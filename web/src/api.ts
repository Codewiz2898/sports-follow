// Types mirror sports_follow/schema.py and the card built in sports_follow/pipeline.py.

export interface Stat { label: string; value: string }

export interface PlayerProfile {
  name: string
  sport: string
  status: 'active' | 'retired' | 'unknown'
  nationality?: string | null
  role?: string | null
  teams: string[]
  summary?: string
  disambiguation?: string | null
}

export interface LiveStatus {
  is_live: boolean
  event?: string | null
  score?: string | null
  clock?: string | null
  player_stats: Stat[]
  source_url?: string | null
  as_of?: string | null
}

export interface NewsItem { headline: string; summary: string; source: string; url: string; published?: string | null }

export interface UpcomingEvent {
  title: string
  competition: string
  team?: string | null
  start_utc?: string | null
  venue?: string | null
  notes?: string | null
}

export interface RecentResult { title: string; result: string; date?: string | null; player_contribution?: string | null }

export interface Card {
  player_id: number
  slug: string
  status: 'building' | 'ready' | 'failed'
  error?: string | null
  version: number
  built_at?: string
  player: PlayerProfile
  live: LiveStatus
  news: NewsItem[]
  upcoming: UpcomingEvent[]
  recent_results?: RecentResult[]
  season_stats?: Stat[]
  sources?: string[]
  freshness?: Partial<Record<'live' | 'fixtures' | 'stats' | 'news', string>>
  following?: boolean
}

export type StreamEvent =
  | { type: 'card'; player_id: number; card: Card }
  | { type: 'progress'; player_id: number; message: string }
  | { type: 'moved'; from: number; to: number }
  | { type: 'live_error'; player_id: number; message: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch { /* not JSON */ }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export const api = {
  following: () => request<{ players: Card[] }>('/api/me/following'),
  follow: (query: string) =>
    request<{ player_id: number; card: Card }>('/api/follows', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query }),
    }),
  unfollow: (playerId: number) => request<{ ok: boolean }>(`/api/follows/${playerId}`, { method: 'DELETE' }),
  card: (playerId: number) => request<Card>(`/api/players/${playerId}/card`),
  refresh: (playerId: number) => request<{ queued: boolean; reason?: string }>(`/api/players/${playerId}/refresh`, { method: 'POST' }),
  search: (q: string) =>
    request<{ players: { player_id: number; name: string; sport: string; status: string; teams: string[]; followers: number }[] }>(
      `/api/search?q=${encodeURIComponent(q)}`,
    ),
  config: () => request<{ model: string }>('/api/config'),
}

/** Subscribe to a server-sent event stream; returns an unsubscribe function. EventSource reconnects on its own. */
export function subscribe(path: string, onEvent: (e: StreamEvent) => void): () => void {
  const source = new EventSource(path, { withCredentials: true })
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as StreamEvent)
    } catch { /* ignore malformed frames */ }
  }
  return () => source.close()
}

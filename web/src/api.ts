// Types mirror sports_follow/schema.py and the card built in sports_follow/pipeline.py and structured.py.

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

export interface FootballSide { id: string; name: string; abbr?: string | null; score: string; scorers: string[] }
export interface FootballState { kind: 'football'; home: FootballSide; away: FootballSide; detail?: string | null }

export interface CricketInnings { team: string; abbr?: string | null; period?: number | null; runs: number; wickets: number; overs?: number | string | null; batting: boolean; target?: number | null }
export interface CricketState {
  kind: 'cricket'
  teams: { id: string; name: string; abbr?: string | null; score: string; winner?: boolean | null }[]
  innings: CricketInnings[]
  summary?: string
  format?: string | null
  player_of_match?: string | null
}

export interface BasketballSide { id: string; name: string; abbr?: string | null; score: string; periods: string[]; winner?: boolean | null }
export interface BasketballState { kind: 'basketball'; home: BasketballSide; away: BasketballSide; period?: number | null; clock?: string | null; detail?: string | null; note?: string | null }

export interface TennisSet { games: number; tiebreak?: number | null; won?: boolean | null }
export interface TennisPlayer { id: string; name: string; short: string; country?: string | null; seed?: number | null; serving: boolean; winner?: boolean | null; sets: TennisSet[] }
export interface TennisState { kind: 'tennis'; players: TennisPlayer[]; set?: number | null; round?: string | null; tournament?: string | null }

export interface ChessSide { id: string; name: string; title?: string | null; rating?: number | null; fed?: string | null; clock?: string | null }
export interface ChessState {
  kind: 'chess'
  round?: string | null
  tournament?: string | null
  white?: ChessSide
  black?: ChessSide
  fen?: string
  last_move?: string | null
  turn?: 'white' | 'black'
  move?: number | null
  result?: string | null
  match?: { games: number; score: string } | null  // knockout rounds: several games between the same two players
}

export interface Moment { clock: string; kind: string; text: string; athlete_ids: string[] }

export interface LiveStatus {
  is_live: boolean
  event?: string | null
  score?: string | null
  clock?: string | null
  player_stats: Stat[]
  source_url?: string | null
  as_of?: string | null
  // Set when the score comes from a structured source rather than the research agent.
  event_id?: number
  competition?: string | null
  headline?: string | null
  state?: FootballState | CricketState | BasketballState | TennisState | ChessState
  moments?: Moment[]
  source?: string
}

export interface Provenance { source: string; url?: string | null; at: string }

export interface NewsItem { headline: string; summary: string; source: string; url: string; published?: string | null }

export interface UpcomingEvent {
  title: string
  competition: string
  team?: string | null
  start_utc?: string | null
  venue?: string | null
  notes?: string | null
  status?: 'scheduled' | 'armed' | 'live' | 'postponed' | 'final'
  event_id?: number
}

export interface RecentResult {
  title: string
  result: string
  date?: string | null
  player_contribution?: string | null
  competition?: string | null
  source_url?: string | null
}

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
  season_stats_note?: string
  provenance?: Partial<Record<'upcoming' | 'recent_results' | 'season_stats', Provenance>>
  sources?: string[]
  freshness?: Partial<Record<'live' | 'fixtures' | 'stats' | 'news', string>>
  following?: boolean
  // Published from a live source before the research agent finished: what's still coming, or why it didn't.
  pending?: string[]
  pending_error?: string
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

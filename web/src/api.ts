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
  alerts?: AlertLevel | null  // the fan's notification level for this player, when following
  // Published from a live source before the research agent finished: what's still coming, or why it didn't.
  pending?: string[]
  pending_error?: string
}

/** One athlete in search results: an exact source id, so following it never guesses from the name. */
export interface SearchResult {
  key: string
  name: string
  sport: string
  detail: string
  system?: string | null
  athlete_id?: string | null
  league?: string | null
  player_id?: number | null  // already on Sports Follow
  following: boolean
  followers: number
  live_scores: boolean  // false: a sport only the research agent covers
  inactive: boolean
  exact: boolean
  status?: 'building' | 'ready' | 'failed' | null
  next?: { title?: string | null; start_utc?: string | null; competition?: string | null; live?: string } | null
  qid?: string | null  // the player registry's (Wikidata) id
  born?: number | null
}

export interface Research { used: number; limit: number; left: number; resets_at: string }

export interface SearchResponse {
  query: string
  sport: string | null
  results: SearchResult[]
  suggestions: SearchResult[]  // "did you mean", when nothing matches what was typed
  namesakes: number  // how many athletes have exactly the typed name, when more than one
  partial: string[]  // sources that didn't answer in time
  research: Research
}

export interface AthletePreview {
  system: string | null  // null: no live source has them; following builds the page with AI
  athlete_id: string | null
  league?: string | null
  name: string
  sport: string
  teams: string[]
  source: string
  source_url?: string | null
  next?: UpcomingEvent | null
  last?: RecentResult | null
  stats: Stat[]
  stats_note: string
  qid?: string | null
  born?: string | null
  country?: string | null
  live?: boolean
}

export interface Pick { system: string; athlete_id: string; league?: string | null; qid?: string | null }

export type AlertLevel = 'everything' | 'key' | 'results' | 'off'

/** Something worth telling a player's fans (sports_follow/moments.py). */
export interface PlayerMoment { id: number; title: string; body: string; url: string; player_id: number; kind: string; level: 'key' | 'minor' }

export interface PushSettings { ok: boolean; timezone: string; quiet_start: string | null; quiet_end: string | null }

export type StreamEvent =
  | { type: 'card'; player_id: number; card: Card }
  | { type: 'moment'; player_id: number; moment: PlayerMoment }
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

/** What to follow: a name for the research agent, a player already here, or an athlete picked in search. */
export type FollowTarget = { query: string } | { player_id: number } | Pick | { qid: string; sport: string }

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
  refresh: (playerId: number) => request<{ queued: boolean; reason?: string }>(`/api/players/${playerId}/refresh`, { method: 'POST' }),
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

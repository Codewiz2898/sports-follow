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

export interface EsportsTeam { id: string; name: string; abbr?: string | null; score: number; winner?: boolean | null }
export interface EsportsMap { n: number; status: string; winner?: string | null }  // status: finished | running | not_started
export interface EsportsState { kind: 'esports'; game?: string | null; teams: EsportsTeam[]; maps: EsportsMap[]; format?: string | null; stage?: string | null; forfeit?: boolean }

// A battle royale lobby: every team's standing, and who won each game ("chicken dinner", "Booyah").
export interface LobbyGame { n: number; place?: number | null; kills: number; points: number }
export interface LobbyTeam { id: string; name: string; place: number; points: number; kills: number; wins: number; games: LobbyGame[] }
export interface BattleRoyaleState {
  kind: 'battle_royale'
  game?: string | null
  win_word?: string
  stage?: string | null
  standings: LobbyTeam[]
  games: { n: number; map?: string | null; status: string; winner?: string | null }[]
  total?: number
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
  available?: boolean  // false: no source covers this sport yet, so there are no live scores
  // Set when the score comes from a structured source rather than the research agent.
  event_id?: number
  competition?: string | null
  headline?: string | null
  state?: FootballState | CricketState | BasketballState | TennisState | ChessState | EsportsState | BattleRoyaleState
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
  event_id?: number | null
  scorecard?: boolean | 'missing'  // the player's line has been read (history), or the source has none
}

/** A page of a player's results history, newest first (sports_follow/results.py). */
export interface ResultsPage {
  results: RecentResult[]
  next: string | null
  total: number
  filling: boolean  // older results or scorecards are still being read
  pages_left: number | null
  since: string | null
}

/** One finished game in full, from the player's side. */
export interface GameDetail extends RecentResult {
  venue?: string | null
  start_utc?: string | null
  score?: string | null
  clock?: string | null
  line?: { headline: string; stats: Stat[] } | null
}

export interface LatestResult extends RecentResult { player_id: number; player_name: string }

export interface Form { stats: Stat[]; note: string; count: number }

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
export interface PlayerMoment { id: number; title: string; body: string; url: string; player_id: number; event_id?: number | null; kind: string; level: 'key' | 'minor' }

/** A moment in the fan's activity list (GET /api/me/activity): every moment of their players, pushed or not. */
export interface ActivityItem extends PlayerMoment { priority: number; player: string; event: string | null; muted: boolean; at: string }

export interface PushSettings { ok: boolean; timezone: string; quiet_start: string | null; quiet_end: string | null }

export type StreamEvent =
  | { type: 'following'; players: Card[] }  // the fan stream's first frame on every connect: GET /api/me/following's players
  | { type: 'card'; player_id: number; card: Card }
  | { type: 'moment'; player_id: number; moment: PlayerMoment }
  | { type: 'progress'; player_id: number; message: string }
  | { type: 'moved'; from: number; to: number }
  | { type: 'live_error'; player_id: number; message: string }

/** One credit on the Credits page (sports_follow/credits.py). */
export interface Credit { name: string; url?: string; what?: string; licence?: string; licence_url?: string; note?: string }
export interface Credits { data: Credit[]; also: Credit[]; software: Credit[]; notices: string[] }

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
  results: (playerId: number, before?: string | null) =>
    request<ResultsPage>(`/api/players/${playerId}/results${before ? `?before=${encodeURIComponent(before)}` : ''}`),
  result: (playerId: number, eventId: number) => request<GameDetail>(`/api/players/${playerId}/results/${eventId}`),
  form: (playerId: number, last: number) => request<Form>(`/api/players/${playerId}/form?last=${last}`),
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
  activity: (before?: number | null) => request<{ items: ActivityItem[]; next: number | null }>(`/api/me/activity${before ? `?before=${before}` : ''}`),
  mutes: () => request<{ event_ids: number[] }>('/api/me/mutes'),
  mute: (eventId: number, muted: boolean) => request<{ event_id: number; muted: boolean }>(`/api/me/mutes/${eventId}`, { method: muted ? 'PUT' : 'DELETE' }),
  config: () => request<{ model: string }>('/api/config'),
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

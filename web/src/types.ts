// The API's types, shared by the web app (api.ts re-exports them) and the Android app (mobile/). They mirror
// sports_follow/schema.py and the card built in sports_follow/pipeline.py and structured.py. Types only: no
// imports and nothing that runs, so either app can use them.

export interface Stat { label: string; value: string }

/** A player's photo from Wikimedia Commons, served from our own copy (sports_follow/photos.py), and the
 * credit its licence asks for, shown with it: "Photo: <author>, <licence>, Wikimedia Commons, recoloured". */
export interface Photo {
  url: string
  credit: string
  page: string  // the file's page on Commons, where the credit links
  author: string
  licence: string
  licence_url?: string | null
  // The square around the face the profile shows, head and shoulders, when one was found (photos.framing):
  // fractions of the photo's width and height.
  crop?: { x: number; y: number; w: number; h: number }
}

export interface PlayerProfile {
  name: string
  sport: string
  status: 'active' | 'retired' | 'unknown'
  nationality?: string | null
  role?: string | null
  teams: string[]
  summary?: string
  disambiguation?: string | null
  photo?: Photo | null
}

// A live score's sport-specific state ("kind" says whose: football, cricket, battle_royale…): its fields
// are that sport's, drawn by its view (sports/).
export interface LiveState { kind: string; [field: string]: unknown }

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
  state?: LiveState
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
  date?: string | null  // the UTC day, or an agent's bare date
  start_utc?: string | null  // when the game started: the app shows the fan's local day of it
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
  aka?: string | null  // the alias it was found by, when the name doesn't start with what was typed
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
  researchable: boolean  // false: nothing to search or research by ("--", a name in another script)
}

export interface AthletePreview {
  system: string | null  // null: no live source has them; following builds the page with AI
  fixtures?: boolean  // false: the source's games are read once they're followed, so the preview shows stats only
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
  photo?: Photo | null
}

export interface Pick { system: string; athlete_id: string; league?: string | null; qid?: string | null }

export type AlertLevel = 'everything' | 'key' | 'results' | 'off'

/** Something worth telling a player's fans (sports_follow/moments.py). */
export interface PlayerMoment { id: number; title: string; body: string; url: string; player_id: number; event_id?: number | null; kind: string; level: 'key' | 'minor' }

/** A moment in the fan's activity list (GET /api/me/activity): every moment of their players, pushed or not. */
export interface ActivityItem extends PlayerMoment { priority: number; player: string; event: string | null; muted: boolean; at: string }

/**
 * One of the fan's players' big moments this week, as its sport words it for a poster on Today (GET /api/me/week):
 * read from a moment (its id), or off the result of a game that had none (id null; a poster per player and game).
 */
export interface WeekPoster { id: number | null; player_id: number; player: string; sport: string; event_id: number | null; word: string; line: string; url: string; at: string }

/** A row of Today's Wire (GET /api/me/week): a line-up, or a story about one of the fan's players by its own headline, with the site that ran it. */
export interface WireRow { id: number; player_id: number; player: string; sport: string; tag: string; text: string; source: string | null; url: string; at: string }

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
export interface PhotoCredit { name: string; credit: string; page: string; author: string; licence: string; licence_url?: string | null }
export interface Credits { data: Credit[]; also: Credit[]; photos?: PhotoCredit[]; software: Credit[]; notices: string[] }

/** Who this device is signed in as (GET /api/me/account; sports_follow/accounts.py). */
export interface Account { signed_in: true; email: string; google: boolean; created_at: string; players_added?: number }
export type Me = Account | { signed_in: false; reason?: 'signed_out' }
/** What this server offers (GET /api/config). */
// research: false when the server runs without the research agent: pages come from live sources alone.
export interface ServerConfig { name: string; model: string; research?: boolean; email_sign_in: boolean; google_sign_in?: boolean; contact_email: string }

/** A player offered to a new fan, and why (starters.py): pinned by the operator, playing (live, started by
 * the clock, or soon), among the most followed, or among the best known with a game soon. */
export type Starter = SearchResult & { why: 'pinned' | 'live' | 'started' | 'soon' | 'followed' | 'top' }

/** A sport's starters: the few players a new fan is offered there (GET /api/starters), as search results. */
export interface StarterSport {
  key: string
  label: string
  players: Starter[]
}

/** A game a source has on now, whoever follows it (GET /api/live, live.py): its live block shaped like a
 * card's, a few players to follow from it, best first, and the events it is here (a followed player's live
 * card names its game by one: Today shows it once, as theirs). */
export interface LiveGame {
  key: string
  title: string
  competition: string
  start_utc?: string | null
  live: LiveStatus
  players: SearchResult[]
  event_ids?: number[]
}

export interface LiveSport {
  key: string
  label: string
  games: LiveGame[]
}

/** A game in the competitions the sources cover, whoever follows it (GET /api/league): finished, or to come.
 * Its sport says how big it is (rank, why) and, once it's over, its big word and the line under it. */
export interface LeagueGame {
  key: string
  sport: string
  title: string
  competition: string
  detail?: string | null
  start_utc: string
  rank: number
  word: string  // "4–1", "By 6 wickets", a lobby's winner; "" for a game to come
  line: string  // "Arsenal beat Chelsea"; "" says the title
  why: string  // "1st v 3rd", "Final", "Tier S"
  event_ids: number[]  // the events it is here: a fan's player in one makes it theirs
}

export interface League {
  results: { day: string | null; games: LeagueGame[] }  // day: null for since yesterday
  coming: { games: LeagueGame[] }
  refreshing: boolean
}

/** What to follow: a name for the research agent, a player already here, or an athlete picked in search. */
export type FollowTarget = { query: string } | { player_id: number } | Pick | { qid: string; sport: string }

/** A sport as the server knows it (GET /api/sports, sports_follow/sports/): what it's called, which free-text
 * names are it, what its alerts say, whether this build runs it, and its colours (sports/naming.ts reads it). */
export interface SportInfo {
  key: string
  label: string
  on: boolean // this build runs it: a search filter, and searched
  live: boolean // a source covers it: live scores and alerts
  pattern: string
  exclude: string
  alerts: { key: [brief: string, hint: string]; more: [brief: string, hint: string] }
  stats_title: string // the Stats tab's first view: "Season" or "Overview"
  team_results: boolean // results are the team's: no player lines (esports)
  keeps_names: boolean // a player's sport is shown as written ("CS2")
  // The block colour, text on it, softer text on it, and the colour as text on ink; null: it wears ink. The web
  // app takes them from /api/sports.css instead; the Android app from here.
  colour: { sport: string; on: string; on_soft: string; ink: string } | null
}

export interface SportCatalog { sports: SportInfo[]; other: SportInfo }

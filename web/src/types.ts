// The API's types, shared by the web app (api.ts re-exports them) and the Android app (mobile/). They mirror
// sports_follow/schema.py and the card built in sports_follow/pipeline.py and structured.py. Types only: no
// imports and nothing that runs, so either app can use them.

export interface Stat { label: string; value: string }

/** A player's photo from Wikimedia Commons, served from our own copy (sports_follow/photos.py), and the
 * credit its licence asks for, shown with it: "Photo: <author>, <licence>, Wikimedia Commons, cropped". */
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

/** Where a player's About paragraph comes from, when it isn't the research agent's: their Wikipedia article,
 * shown under it with its licence (sports_follow/wiki.py). */
export interface AboutSource {
  source: string  // "Wikipedia"
  url?: string | null  // the article
  licence: string  // "CC BY-SA 4.0"
  licence_url: string
}

export interface PlayerProfile {
  name: string
  sport: string
  status: 'active' | 'retired' | 'unknown'
  nationality?: string | null
  role?: string | null
  teams: string[]
  summary?: string
  about?: AboutSource | null
  born?: string | null  // "1988-11-05", or "1988" when only the year is known
  honours?: string[]  // "Ballon d'Or ×8"
  // Which fields Wikidata and Wikipedia gave ({"born": "wikidata", "summary": "wikipedia"}); the rest are a
  // live source's or the research agent's.
  profile_from?: Record<string, string>
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
  match?: string | null  // its match page's address after /match/: "<source>/<game id>" (sports_follow/match.py)
  // The game it's one fixture of, where a game has several (a chess board is one of each player's): the same
  // under each, so Today shows it once; none where the event is the game (structured.game_key).
  game_key?: string | null
  competition?: string | null
  headline?: string | null
  state?: LiveState
  moments?: Moment[]
  source?: string
  // The source's own wording for how its scores come in and how late they can be (its adapter's live_note
  // and lag_note), where the app's general sentences don't fit.
  live_note?: string | null
  lag_note?: string | null
}

export interface Provenance { source: string; url?: string | null; at: string }

export interface NewsItem { headline: string; summary: string; source: string; url: string; published?: string | null; outlets?: number | null }  // outlets: how many ran the story

export interface UpcomingEvent {
  title: string
  competition: string
  team?: string | null
  start_utc?: string | null
  venue?: string | null
  notes?: string | null
  status?: 'scheduled' | 'armed' | 'live' | 'postponed' | 'final'
  event_id?: number
  match?: string | null  // its match page's address after /match/, where a source has the game
  game_key?: string | null  // the game it's one fixture of, as a live game's (LiveStatus): a chess board once it's paired
}

export interface RecentResult {
  title: string
  result: string
  date?: string | null  // the UTC day, or an agent's bare date
  start_utc?: string | null  // when the game started: the app shows the fan's local day of it
  ended_utc?: string | null  // when the server found it over: Today's since yesterday dates it so (format.ts: endedOn)
  player_contribution?: string | null
  competition?: string | null
  source_url?: string | null
  event_id?: number | null
  match?: string | null  // its match page's address after /match/, where a source has the game
  game_key?: string | null  // the game it's one fixture of, as a live game's (LiveStatus)
  scorecard?: boolean | 'missing'  // the player's line has been read (history), or the source has none
  // Won, drew or lost from the player's side, told by the server from the side they played for (null: it can't
  // say); missing on a result it didn't tell (format.ts: resultOutcome).
  outcome?: 'W' | 'D' | 'L' | null
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
  stories?: NewsItem[]  // the publishers' stories the curator served (sports_follow/rank.py cards): shown in place of news
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
  teams?: TeamSummary[]  // teams by that name fans can follow (sports_follow/teams.py), from the registry alone
}

/** A team fans can follow (sports_follow/teams.py), as search and Following list it: what it plays in and where
 * it's from (`detail`), how many follow it, whether this fan does, and its next game as last read. */
export interface TeamSummary {
  team_id: number
  name: string
  short: string  // its source's code for it: "ARS"
  sport: string  // a sport's key
  detail: string
  followers: number
  following: boolean
  next?: { title?: string | null; start_utc?: string | null; status?: string | null; match?: string | null; versus?: string | null } | null
}

/** A team the fan follows (GET /api/me/following's teams): with their level, its last result from its side, its
 * games to come, and its game on now as last polled (a live block like a card's), if any. */
export interface FollowedTeam extends TeamSummary {
  alerts: AlertLevel
  last?: (RecentResult & { versus?: string | null }) | null
  live?: LiveStatus | null
  upcoming?: (UpcomingEvent & { versus?: string | null })[]  // its games to come, as last read (Today's Coming up)
}

/** A team's page (GET /api/teams/<id>): who it is, whether this fan follows it and at what level, whether it can
 * be followed now (its source runs) and why not, its game on now, its games to come and last results from its
 * side, its form, and its squad as search results, each followed as one is. */
export interface TeamPage extends TeamSummary {
  alerts: AlertLevel
  can_follow: boolean
  note: string | null
  live?: LiveStatus | null
  upcoming: (UpcomingEvent & { versus?: string | null })[]
  recent: (RecentResult & { versus?: string | null })[]
  form: ('W' | 'D' | 'L')[]
  squad: SearchResult[]
  source: string
  fetched_at?: string | null
  refreshing: boolean  // its games are being read again: a newer page may come in a moment
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
/** A moment as the stream brings it: a player's, or (player_id null) a game's own, for the fans following the
 * game by itself (sports_follow/matches.py) or a team in it (sports_follow/teams.py). A game's own says which of
 * its teams fans follow (`teams`, by tag: "team:7"), so a fan's level for it is their team's; one told from one
 * team's side (its result: "Arsenal won 2–1 v Chelsea") names whose (`sides`), for that team's fans; the game's own
 * result says whose sides tell it too (`told_by`), so their fans get theirs instead. */
// `voice`: a chess board's moment is found from both sides, one under each player's fixture; this one's other side
// (that player's id) tells it, so a fan who follows them too sees theirs (sports_follow/alerts.py, voiced).
export interface PlayerMoment { id: number; title: string; body: string; url: string; player_id: number | null; event_id?: number | null; kind: string; level: 'key' | 'minor'; teams?: string[]; sides?: string[]; told_by?: string[]; voice?: number }

/** A moment in the fan's activity list (GET /api/me/activity): every moment of their players, and of the games
 * they follow by themselves, pushed or not. A game's own has no player, and says its sport. */
export interface ActivityItem extends PlayerMoment { priority: number; player: string | null; event: string | null; muted: boolean; at: string; sport?: string }

/**
 * One of the fan's players' big moments this week, as its sport words it for a poster on Today (GET /api/me/week):
 * read from a moment (its id), or off the result of a game that had none (id null; a poster per player and game).
 */
export interface WeekPoster { id: number | null; player_id: number; player: string; sport: string; event_id: number | null; word: string; line: string; url: string; at: string }

/**
 * A row of Today's Wire (GET /api/me/week): a fact a source gave about a player (who's out, who's back), a line-up,
 * or a story about one of the fan's players by its own headline, each with where it's from. `yours`: about one of
 * the fan's players; the rest are facts about the best-known players they don't follow, whose `player_id` is null
 * until someone follows them (`url` then opens the source's athlete page).
 */
export interface WireRow { id: string; player_id: number | null; player: string; sport: string; tag: string; text: string; source: string | null; url: string; yours: boolean; at: string }

/**
 * A player's Feed (GET /api/players/<id>/feed, sports_follow/feed.py): videos, posts and news about them, judged
 * before a fan sees it. A video plays in YouTube's no-cookie player once tapped; a post links to where it was
 * posted. `reason` is the one line under "Why this is here".
 */
export interface FeedItem {
  id: string
  kind: 'video' | 'post' | 'news'
  tag: string
  title: string
  url: string
  source: string
  published_at: string | null  // an instant, or a bare day for a story known only by its day
  reason: string
  summary?: string | null
  outlets?: number | null  // a story's: how many outlets ran it, when more than one
  // The card's picture, an https address loaded as served: a video's thumbnail, a story's image from the publisher's
  // feed. None: a story's headline on the sport's colour; a post is words only.
  picture?: string | null
  // upright: taller than it's wide (a Short), framed and played upright; missing from a server before BHA-358: wide.
  // mark: its source's icon, which the card shows linked to the video where it plays (feed.ts sourceMark).
  video?: { youtube_id: string; duration: number | null; views: number | null; upright?: boolean; mark?: VideoMark | null } | null
  post?: { platform: string; own: boolean } | null  // platform as fans call it: "Open on X"
}

/** The icon of the source a video is from, as the server sends it (sports_follow/feed.py Mark): its name, and its
 * drawing as the source draws it, paths painted in order in a width x height box, never drawn under min_height. */
export interface VideoMark { name: string; width: number; height: number; min_height: number; paths: { fill: string; d: string }[] }

/** Items around one of the player's moments (`match` its game's page), or "This week", "This month" and "Earlier". */
export interface FeedGroup { head: string; moment: boolean; when: string | null; match: string | null; items: FeedItem[] }

export interface Feed { player_id: number; groups: FeedGroup[]; evergreen: FeedItem[]; count: number }

/** Who an item of the fan's Feed is about: the card's head. */
export interface FeedWho { id: number; name: string; sport: string | null; photo: Photo | null }

/** A player across the top of the fan's Feed. `newest`: when the newest of theirs came in, which rings them while
 * it's after the fan last looked at them. */
export interface FeedCircle extends FeedWho { count: number; newest: string | null }

/** An item of the fan's Feed, under its day ("Today", "Yesterday", "This week", "This month", "Earlier"), with the
 * moment it's about when it is one (`match`: its game in the app). */
export interface FeedEntry { day: string; player: FeedWho; item: FeedItem; arrived: string | null; moment: string | null; match: string | null; following: boolean }

/** The fan's Feed's tabs: For you mixes in videos of players they don't follow (each entry's `following` says);
 * Following is their players alone. */
export type FeedTabKey = 'for_you' | 'following'

/** How the apps draw the fan's Feed, as the server says (feed.layout, the admin panel's live setting): posts edge to
 * edge, or one video a screen. */
export type FeedLayout = 'edge' | 'full'

/**
 * A page of the fan's Feed (GET /api/me/feed/items?cursor=&player=&limit=&tz=, sports_follow/feed.py `page`):
 * every followed player's items by day, players taking turns. `cursor` asks for the next page (null: the last);
 * `count` is every item (of the player picked); `newest` when the newest of anyone's came in (the tab's dot).
 */
export interface FeedPage { items: FeedEntry[]; cursor: string | null; count: number; newest: string | null; players: FeedCircle[]; tab: FeedTabKey; layout: FeedLayout }

/** What a fan can say about an item from its "…" sheet. */
export type FeedVerdict = 'not_interested' | 'not_about_player' | 'report'

export interface PushSettings { ok: boolean; timezone: string; quiet_start: string | null; quiet_end: string | null }

export type StreamEvent =
  // The fan stream's first frame on every connect: GET /api/me/following's players and teams, GET /api/me/matches's games,
  // and GET /api/me/sports's sports.
  | { type: 'following'; players: Card[]; matches?: FollowedMatch[]; teams?: FollowedTeam[]; sports?: string[] }
  | { type: 'card'; player_id: number; card: Card }
  | { type: 'match'; event_id: number; match: FollowedMatch }  // a game followed by itself, or a team's, as its poll found it
  | { type: 'moment'; player_id: number | null; moment: PlayerMoment }
  | { type: 'progress'; player_id: number; message: string }
  | { type: 'moved'; from: number; to: number }
  | { type: 'live_error'; player_id: number; message: string }
  // The fan stream's last word when this device's session has ended elsewhere (signed out everywhere, the account
  // deleted or blocked): who it is now is asked at once (BUG-162).
  | { type: 'signed_out' }

/** One credit on the Credits page (sports_follow/credits.py). */
export interface Credit { name: string; url?: string; what?: string; licence?: string; licence_url?: string; note?: string }
export interface PhotoCredit { name: string; credit: string; page: string; author: string; licence: string; licence_url?: string | null }
export interface Credits { data: Credit[]; also: Credit[]; photos?: PhotoCredit[]; software: Credit[]; notices: string[] }

/** Who this device is signed in as (GET /api/me/account; sports_follow/accounts.py). */
export interface Account { signed_in: true; email: string; google: boolean; created_at: string; players_added?: number; teams_dropped?: number; players_dropped?: number; games_dropped?: number }
export type Me = Account | { signed_in: false; reason?: 'signed_out' }
/** What this server offers (GET /api/config). */
// research: false when the server runs without the research agent: pages come from live sources alone.
export interface ServerConfig { name: string; model: string; research?: boolean; email_sign_in: boolean; google_sign_in?: boolean; contact_email: string; feed_layout?: FeedLayout }

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
  match?: string  // its match page's address after /match/ (MatchPage)
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
  group?: string // the group it's one of ("esports" for each game)
  games: LiveGame[]
}

/** A campaign page, /<slug> (GET /api/campaigns/<slug>, campaigns.py): a big event's players and whole teams
 * to follow with a tap, as result rows (following one is the same call as from search), its games and a few
 * lines of copy. Its games' times are said in its zone (India's: "IST"). */
export interface Campaign {
  slug: string
  sport: string
  title: string
  event: string | null
  lines: string[]
  players: SearchResult[]
  teams: CampaignTeam[]
  games: CampaignGame[]
  zone: { name: string; label: string }
  predictions?: Prediction[]  // its players' games to call, open first, then settled lately (sports_follow/predictions.py)
}

/** A whole team on a campaign page: following it follows every player on its roster, as its source lists them. */
export interface CampaignTeam {
  team_id: string
  name: string
  game: string | null
  players: SearchResult[]
}

export interface CampaignGame {
  start_utc: string
  label: string  // "Game 1", "Final": the campaign's own words
}

/** Something found for a player followed in a game (moments.py): a goal, a fifty, a map won. */
export interface MatchMoment { id: number; at: string; kind: string; level: 'key' | 'minor'; title: string; body: string; player_id: number }

/** A match page's Follow button (sports_follow/matches.py): whether this fan follows the game by itself and on
 * which event (a chess board's other address, maybe: stop it by that), whether its moments can reach them at
 * all, whether its alerts are paused for now (its source busy with followed players' games), and why. */
export interface MatchFollowState { following: boolean; event_id?: number | null; alerts: boolean; held?: boolean; note: string | null }

/** A game the fan follows by itself (GET /api/me/matches): its address, when, where it's at, and its board as
 * last polled, a live block like a card's (null before its first poll). Today shows it with the fan's games. */
export interface FollowedMatch {
  event_id: number
  key: string
  match: string  // its address after /match/
  addresses?: string[]  // every address it has (a chess board is one per player): a card's live game at any is this one
  held?: boolean  // its alerts are paused for now: its source is busy with followed players' games
  sport: string  // a sport's key
  title: string
  competition: string
  detail?: string | null
  start_utc: string | null
  status: 'scheduled' | 'armed' | 'live' | 'final' | 'postponed'
  live: LiveStatus | null
  alerts: AlertLevel
}

/** One side of a game and its players to follow, in the line-up's order; a game whose line-ups aren't in yet
 * offers the players on the app in it, as one side with no name. */
export interface MatchSide { name: string; players: SearchResult[] }

/** One of the fan's players in a game, with their line in it and, once it's over, their result from their side. */
export interface MatchPlayer { player_id: number; name: string; sport: string; result: string | null; headline: string | null; stats: Stat[] }

/** One game's match page (GET /api/match/<source>/<game id>, sports_follow/match.py), whoever follows anyone
 * in it: live, to come or over. Its board is a live block like a card's (null before a game has one), `page`
 * is what its sport shows under it (Sport.match_page: a timeline, a scorecard, maps, a chess game's moves),
 * drawn by the sport's view, `moments` what was found for the players followed in it, `yours` the fan's
 * players in it with their lines. */
/** One of a game's teams to follow, as its match page offers it (sports_follow/teams.py in_game): whether this
 * fan follows it, and whether it can be followed now (its source runs). */
export interface GameTeam { team_id: number; name: string; sport: string; following: boolean; can_follow: boolean }

export interface Match {
  key: string
  match: string  // its address after /match/
  sport: string  // a sport's key
  title: string | null
  competition: string | null
  detail?: string | null
  start_utc?: string | null
  status: 'scheduled' | 'armed' | 'live' | 'final' | 'postponed'
  live: LiveStatus | null
  verdict?: string  // a finished game's: who won, as its sport says it ("Arsenal won", "Draw"); "" where the board says it
  page: Record<string, unknown>
  moments: MatchMoment[]
  yours?: MatchPlayer[]
  sides: MatchSide[]
  teams?: GameTeam[]  // its sides as the team registry has them, to follow
  event_ids: number[]
  fixtures: string[]
  refreshing: boolean  // a newer read is on its way: look again in a moment
  follow?: MatchFollowState
  predictions?: Prediction[]  // what fans call before it starts (sports_follow/predictions.py); a server before them sends none
  streams?: MatchStream[]  // where it can be watched, in the order to offer them (sports_follow/watch.py); a server before them sends none
}

/** One broadcast of a game (sports_follow/watch.py): its page, the platform it's on and the language it's called in,
 * whether the event's organiser runs it (and it's their main one), and, where the app plays it inside the page,
 * which of its players and what that plays: "twitch" and a channel, "youtube" and a video. */
export interface MatchStream {
  url: string
  platform: string  // "Twitch", "Kick"
  language: string  // "en"; "" where its source doesn't say
  language_name: string  // "English"
  official: boolean
  main: boolean
  embed: { player: string; id: string } | null
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
  ended_utc?: string | null  // when it was found over, where it's an event here: a Test's last day (format.ts: endedOn)
  rank: number
  word: string  // "4–1", "By 6 wickets", a lobby's winner; "" for a game to come
  line: string  // "Arsenal beat Chelsea"; "" says the title
  why: string  // "1st v 3rd", "Final", "Tier S"
  event_ids: number[]  // the events it is here: a fan's player in one makes it theirs
  match?: string | null  // its match page's address after /match/, where it has one
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
  // The same for a game followed by itself (Sport.game_alerts); "" more adds nothing. A server before it sends none.
  game_alerts?: { key: [brief: string, hint: string]; more: [brief: string, hint: string] }
  stats_title: string // the Stats tab's first view: "Season" or "Overview"
  team_results: boolean // results are the team's: no player lines (esports)
  keeps_names: boolean // a player's sport is shown as written ("CS2")
  group?: string // the group it's one of and drawn as (its board, line art): "esports" for each game; "" for none
  // The block colour, text on it, softer text on it, and the colour as text on ink; null: it wears ink. The web
  // app takes them from /api/sports.css instead; the Android app from here.
  colour: { sport: string; on: string; on_soft: string; ink: string } | null
}

// sports: those the app lists, in order; groups: sports that stand for several of them and aren't listed (esports,
// every game), as a name no listed sport has ("Esports", "Call of Duty: Mobile") is drawn; other: any other sport.
export interface SportCatalog { sports: SportInfo[]; groups?: SportInfo[]; other: SportInfo }

/** One answer to a question, and how fans split on it: picks and share (whole percents adding up to 100) come only
 * once this fan has picked, or the question's closed, so the crowd doesn't pick for them. */
export interface PredictionOption { key: string; label: string; picks?: number; share?: number }

/** A question fans call before a game, or a map of a series, starts (sports_follow/predictions.py): "Who wins?" of
 * a chess game, "Who takes map 2?". Open until its game or map starts (locks_at, for the first one), then closed;
 * settled once its board says (answer: an option's key), or voided ("void": a map the series didn't need). A right
 * pick scores 100 less the share who picked the same, at least 10; worth is what this fan's would score now. */
export interface Prediction {
  id: number
  ask: string  // which question in its game: "result", "map2"
  title: string
  game: string  // its game's title: "Gukesh D vs Javokhir Sindarov"
  sport: string  // a sport's key
  match: string  // its game's address after /match/
  status: 'open' | 'closed' | 'settled' | 'void'
  locks_at: string | null
  answer: string | null
  picks: number | null  // how many fans picked; while it's open, as many as its split was last refreshed at (none before it shows)
  split_from: number  // picks before an open question's split shows: with fewer, it would tell a fan a friend's pick
  options: PredictionOption[]
  mine: string | null  // this fan's pick
  points: number | null  // what it scored, once settled
  worth: number | null  // what it would score, were it right, now
  friends?: { name: string; you: boolean; option?: string }[]  // on a league's page: who picked, and what once it's closed
  waiting?: string[]  // and who hasn't yet, while it's open
}

/** Today's calls (GET /api/me/predictions): open questions on the fan's games and Today's big games, soonest first, and their picks settled lately. */
export interface MyPredictions { open: Prediction[]; settled: Prediction[] }

/** A league of friends calling games together (needs an account to make or join): its invite code, its table (members'
 * points on questions settled since it was made; level on points, the same place) and its members' calls. */
export interface FriendLeague {
  code: string
  name: string
  created_at: string | null
  members: number
  table: { place: number; name: string; points: number; right: number; played: number; you: boolean }[]
  questions: Prediction[]
}

/** A league as the fan's list of theirs shows it (GET /api/me/leagues). */
export interface FriendLeagueSummary { code: string; name: string; members: number; place: number | null; points: number }

/** What an invite link shows anyone (GET /api/leagues/<code>/invite): who made it, by their name in it. */
export interface LeagueInvite { code: string; name: string; members: number; by: string | null; member: boolean }

/** The predictions numbers on the admin panel's Users tab. */
export interface PredictionNumbers { leagues: number; leagues_3: number; invites_shared: number; invites_opened: number; pickers: number; picks: number; game_day_returns: number }

/**
 * A short video published from the Mac app (the server's clips.py, GET /api/clips): a moment of a game, cut
 * with its replay and end card. `match` is its match page's address ("<source>/<game id>"), none for a fan's
 * own game; `line` is the moment in a line ("TY 21–18 GL · 26:00 · Game 2"); `published` is when it went live.
 */
export interface Clip {
  id: string
  video: string
  poster: string
  seconds: number
  width: number
  height: number
  caption: string
  line: string
  match: string | null
  sport: string | null
  competition: string | null
  game_title: string
  players: number[]
  published: string | null
}

/** Which clips: a game's (its feed), a competition's (the tournament feed), a player's, or everyone's. */
export interface ClipsOf {
  match?: string
  competition?: string
  player?: number
}

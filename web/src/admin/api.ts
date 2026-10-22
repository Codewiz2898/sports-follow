// The admin panel's API (sports_follow/admin.py, the /api/admin routes in server.py). Its session is a cookie
// of its own, sent only to /api/admin.

import { json, request } from '../api'

/** A sport: whether the server's settings let it run (SPORTS_FOLLOW_SPORTS), whether the panel has it on, its running
 * sources, and the group it's one of ("Esports" for each game; "" for none). */
export interface AdminSport { key: string; label: string; group: string; allowed: boolean; on: boolean; running: boolean; sources: string[]; offered: boolean }

/** A source this app knows, and why it does or doesn't run. */
export interface AdminSource {
  system: string
  name: string
  provider: string
  covers: string
  sport: string
  sport_label: string
  licensed: boolean
  token: boolean // a licensed source needs its token
  allowed: boolean // SPORTS_FOLLOW_SOURCES lists it (or lists none)
  sport_on: boolean
  on: boolean // the panel has it on
  running: boolean
  budgets: string[]
}

/** zone: the time zone its windows start in, where the source's day isn't UTC's (its source's, never the panel's to set). */
export interface Window { seconds: number; requests: number; margin: number; zone?: string | null }
/** What a source may spend: its allowance a window, a margin never spent, a reserve kept for live games and fans, the fans' share, the pause after a 429. */
export interface Limits { windows: Window[]; reserve: number; fan: number | null; pause: number }
export interface Standing {
  windows: { bucket: string; window: string; used: number; allowance: number }[]
  left: Record<string, number> // what the source says is left, by bucket
  paused: boolean
}
export interface LaneUse { lane: string; sent_h: number; sent_d: number; refused_h: number; refused_d: number }
/** One allowance (an API key's), the sources that spend it, as declared and as the panel has it, and what was spent. */
export interface Allowance {
  name: string
  sources: string[]
  running: boolean
  declared: Limits
  limits: Limits
  changed: boolean
  standing: Standing
  usage: LaneUse[]
}
export interface SourcesView { sources: AdminSource[]; budgets: Allowance[] }

/** Linear, where the project's work is tracked (sports_follow/linear.py): its plan and issues against the Free plan's limit.
 * Without a key on the server only `configured: false`; `error` is the last read that failed, shown with the reading before it. */
export interface LinearStatus {
  configured: boolean
  limit: number | null // the Free plan's issues; null on a paid plan
  error?: string | null
  failed_at?: string | null
  workspace?: string
  url?: string
  plan?: string | null // "free", "basic", "business", "enterprise"; null: the key can't read it
  trial_ends_at?: string | null
  issues?: number // not counting archived ones
  archived?: number
  state?: 'ok' | 'near' | 'full'
  read_at?: string
}

export interface Overview { accounts: number; active_week: number; blocked: number; anonymous: number; devices: number }
/** Predictions with friends (sports_follow/predictions.py numbers): leagues, invites, and fans who came back to call another game day. */
export interface PredictionNumbers { leagues: number; leagues_3: number; invites_shared: number; invites_opened: number; pickers: number; picks: number; game_day_returns: number }
export interface AccountRow {
  id: number
  email: string
  google: boolean
  created_at: string
  last_sign_in_at: string | null
  last_seen_at: string | null
  blocked_at: string | null
  players: number
  devices: number
}
export interface AccountDetail extends AccountRow {
  following: { id: number; name: string; sport: string; since: string }[]
  sessions: { created_at: string; last_seen_at: string; expires_at: string }[]
  alert_devices: { created_at: string; last_ok_at: string | null; timezone: string; failures: number }[]
}

/** What the Feed's judge said of a candidate (sports_follow/curate.py's Verdict). */
export interface FeedVerdict {
  about_player: 'subject' | 'team' | 'mention' | 'no'
  by: string
  kind: string
  interest: number
  risks: string[]
  sponsored: boolean
  reason: string
  stage: 'rules' | 'model'
}
export type Review = 'approved' | 'hidden' | 'pinned'
/** A video the curator found about a player, with the judge's verdict, its score (null: never served) and an admin's word. */
export interface FeedCandidateRow {
  id: number
  player: { id: number; name: string; sport: string }
  url: string
  video: { youtube_id: string } | null // how the app's player plays it (the source says); null: it can't
  title: string
  channel: string
  channel_id: string
  published: string | null
  fetched_at: string
  judged_at: string | null
  views: number | null
  likes: number | null
  seconds: number | null
  verdict: FeedVerdict | null
  passes: boolean
  score: number | null
  review: Review | null
  moment_id: number | null
  unplayable_at: string | null // when a fan's app said it won't play inside it (approving or pinning it says it does)
  served: boolean
}
/** A channel's last 30 days: uploads read, those naming a followed player, kept and served, and when it last posted (null: never seen). */
export interface ChannelYield {
  read: number
  matched: number
  kept: number
  served: number
  last_upload: string | null
}
/** A video channel: listed by a running sport or not, its trust as the panel set it (null: its default), and what fans said of its videos. */
export interface FeedChannel {
  channel_id: string
  name: string
  listed: boolean
  trust: number | null
  default: number
  effective: number
  candidates: number
  served: number
  feedback: number
  yield: ChannelYield
}
export type FeedShow = 'all' | 'unjudged' | 'served' | 'held' | 'reviewed'
export interface FeedPage {
  candidates: FeedCandidateRow[]
  next: number | null
  totals: Record<string, number>
  channels: FeedChannel[]
  layout: FeedLayoutView
}
/** How the apps draw the fan's Feed (sports_follow/feed.py `layout`): now, the server's setting, and the panel's word (null: none). */
export type AdminFeedLayout = 'edge' | 'full'
export interface FeedLayoutView { layout: AdminFeedLayout; default: string; set: AdminFeedLayout | null; choices: AdminFeedLayout[] }

const at = (path: string) => `/api/admin/${path}`

export const admin = {
  /** Signed in; and whether sign-in asks for an authenticator code. */
  me: () => request<{ admin: true; code: boolean }>(at('me')),
  login: (password: string, code: string) => request<{ admin: true }>(at('login'), json('POST', { password, code })),
  logout: () => request<{ admin: false }>(at('logout'), { method: 'POST' }),
  sports: () => request<{ sports: AdminSport[] }>(at('sports')),
  setSport: (key: string, on: boolean) => request<{ sports: AdminSport[] }>(at(`sports/${encodeURIComponent(key)}`), json('PUT', { on })),
  sources: () => request<SourcesView>(at('sources')),
  setSource: (system: string, on: boolean) => request<SourcesView>(at(`sources/${encodeURIComponent(system)}`), json('PUT', { on })),
  setLimits: (name: string, limits: Limits) => request<SourcesView>(at(`budgets/${encodeURIComponent(name)}`), json('PUT', limits)),
  resetLimits: (name: string) => request<SourcesView>(at(`budgets/${encodeURIComponent(name)}`), { method: 'DELETE' }),
  linear: () => request<LinearStatus>(at('linear')),
  feed: (show: FeedShow, offset = 0) => request<FeedPage>(at(`feed?show=${show}&offset=${offset}`)),
  review: (id: number, review: Review | null) => request<FeedCandidateRow>(at(`feed/${id}`), json('PUT', { review })),
  channelTrust: (channel: string, trust: number | null) =>
    request<{ channels: FeedChannel[] }>(at(`feed/channels/${encodeURIComponent(channel)}`), json('PUT', { trust })),
  feedLayout: (layout: AdminFeedLayout | null) => request<{ layout: FeedLayoutView }>(at('feed/layout'), json('PUT', { layout })),
  users: (q: string, offset = 0) =>
    request<{ overview: Overview; predictions?: PredictionNumbers; accounts: AccountRow[]; next: number | null }>(at(`users?q=${encodeURIComponent(q)}&offset=${offset}`)),
  user: (id: number) => request<AccountDetail>(at(`users/${id}`)),
  signOut: (id: number) => request<{ signed_out: number }>(at(`users/${id}/signout`), { method: 'POST' }),
  block: (id: number, blocked: boolean) => request<{ blocked: boolean; signed_out: number }>(at(`users/${id}/block`), json('PUT', { blocked })),
  remove: (id: number) => request<{ deleted: boolean }>(at(`users/${id}`), json('DELETE', { confirm: true })),
}

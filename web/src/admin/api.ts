// The admin panel's API (sports_follow/admin.py, the /api/admin routes in server.py). Its session is a cookie
// of its own, sent only to /api/admin.

import { ApiError, json, request } from '../api'

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
  saved: Limits | null // what the panel saved, as stored (null: nothing): a change made from this view sends it back as based_on
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
/** Predictions with friends (sports_follow/predictions.py numbers): leagues, invites, and fans who came back to call on another day (an IST day: BUG-193). */
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
  following: { id: number; name: string; sport: string; since: string }[] // players
  teams: { id: number; name: string; sport: string; since: string }[]
  games: { id: number; title: string; sport: string; start: string | null; since: string; over: boolean }[] // over: the follow lapsed with the game
  picks: number // calls made, predictions with friends
  leagues: number
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
  channel_id: string // a video channel's id, or a news feed's address
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
  channel_id: string // a video channel's id, or a news feed's address
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

/** A clip published from the Mac app (sports_follow/clips.py for_admin), whatever its status, with its reports. Its video and
 * poster are at their public address while it's live (a hidden one's are moved off it); without the bucket set up, only
 * its id and line. */
export interface AdminClip {
  id: string
  status: 'uploading' | 'live' | 'hidden' | 'failed' | 'removed'
  reports: number
  source: string
  by: string | null // the publishing key's name
  created: string
  line: string
  caption?: string
  game_title?: string
  competition?: string | null
  sport?: string | null
  match?: string | null
  poster?: string
  video?: string
  published?: string | null
}
/** A publishing key (its secret is shown once, when made, and kept nowhere). */
export interface ClipKey { id: number; name: string; created: string; last_used: string | null; revoked: string | null }
export type ClipShow = 'reported' | 'all' | 'live' | 'hidden'
export interface ClipsView { clips: AdminClip[]; keys: ClipKey[]; set_up: boolean }

const at = (path: string) => `/api/admin/${path}`

/** A call answered with no body (204), which request() would try to read as JSON. */
async function answered(path: string, init: RequestInit): Promise<void> {
  const res = await fetch(path, { credentials: 'same-origin', ...init })
  if (res.ok) return
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') message = body.detail
  } catch { /* not JSON */ }
  throw new ApiError(message, res.status)
}

export const admin = {
  /** Signed in; and whether sign-in asks for an authenticator code. */
  me: () => request<{ admin: true; code: boolean }>(at('me')),
  login: (password: string, code: string) => request<{ admin: true }>(at('login'), json('POST', { password, code })),
  logout: () => request<{ admin: false }>(at('logout'), { method: 'POST' }),
  sports: () => request<{ sports: AdminSport[] }>(at('sports')),
  setSport: (key: string, on: boolean) => request<{ sports: AdminSport[] }>(at(`sports/${encodeURIComponent(key)}`), json('PUT', { on })),
  sources: () => request<SourcesView>(at('sources')),
  setSource: (system: string, on: boolean) => request<SourcesView>(at(`sources/${encodeURIComponent(system)}`), json('PUT', { on })),
  /** basedOn: what the view the change was made from had saved; changed since (another tab), it's a 409. */
  setLimits: (name: string, limits: Limits, basedOn: Limits | null) =>
    request<SourcesView>(at(`budgets/${encodeURIComponent(name)}`), json('PUT', { ...limits, based_on: basedOn })),
  resetLimits: (name: string) => request<SourcesView>(at(`budgets/${encodeURIComponent(name)}`), { method: 'DELETE' }),
  linear: () => request<LinearStatus>(at('linear')),
  feed: (show: FeedShow, offset = 0) => request<FeedPage>(at(`feed?show=${show}&offset=${offset}`)),
  review: (id: number, review: Review | null) => request<FeedCandidateRow>(at(`feed/${id}`), json('PUT', { review })),
  /** A video channel's id or a news feed's address, in the body: an address doesn't fit in a path. */
  channelTrust: (channel: string, trust: number | null) =>
    request<{ channels: FeedChannel[] }>(at('feed/channels'), json('PUT', { channel, trust })),
  feedLayout: (layout: AdminFeedLayout | null) => request<{ layout: FeedLayoutView }>(at('feed/layout'), json('PUT', { layout })),
  clips: (show: ClipShow) => request<ClipsView>(at(`clips${show === 'all' ? '' : `?status=${show}`}`)),
  hideClip: (id: string, hidden: boolean) => request<{ id: string; status: string }>(at(`clips/${encodeURIComponent(id)}`), json('POST', { hidden })),
  removeClip: (id: string) => answered(at(`clips/${encodeURIComponent(id)}`), { method: 'DELETE' }),
  /** hide: its live clips are hidden too (a key that leaked). */
  revokeKey: (id: number, hide: boolean) => answered(at(`clip-keys/${id}${hide ? '?hide=true' : ''}`), { method: 'DELETE' }),
  users: (q: string, offset = 0) =>
    request<{ overview: Overview; predictions?: PredictionNumbers; accounts: AccountRow[]; next: number | null }>(at(`users?q=${encodeURIComponent(q)}&offset=${offset}`)),
  user: (id: number) => request<AccountDetail>(at(`users/${id}`)),
  signOut: (id: number) => request<{ signed_out: number }>(at(`users/${id}/signout`), { method: 'POST' }),
  block: (id: number, blocked: boolean) => request<{ blocked: boolean; signed_out: number }>(at(`users/${id}/block`), json('PUT', { blocked })),
  remove: (id: number) => request<{ deleted: boolean }>(at(`users/${id}`), json('DELETE', { confirm: true })),
}

// The admin panel's API (sports_follow/admin.py, the /api/admin routes in server.py). Its session is a cookie
// of its own, sent only to /api/admin.

import { json, request } from '../api'

/** A sport: whether the server's settings let it run (SPORTS_FOLLOW_SPORTS), whether the panel has it on, its running
 * sources, and the group it's one of ("Esports" for each game; "" for none). */
export interface AdminSport { key: string; label: string; group: string; allowed: boolean; on: boolean; running: boolean; sources: string[] }

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

export interface Window { seconds: number; requests: number; margin: number }
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

export interface Overview { accounts: number; active_week: number; blocked: number; anonymous: number; devices: number }
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
  users: (q: string, offset = 0) =>
    request<{ overview: Overview; accounts: AccountRow[]; next: number | null }>(at(`users?q=${encodeURIComponent(q)}&offset=${offset}`)),
  user: (id: number) => request<AccountDetail>(at(`users/${id}`)),
  signOut: (id: number) => request<{ signed_out: number }>(at(`users/${id}/signout`), { method: 'POST' }),
  block: (id: number, blocked: boolean) => request<{ blocked: boolean; signed_out: number }>(at(`users/${id}/block`), json('PUT', { blocked })),
  remove: (id: number) => request<{ deleted: boolean }>(at(`users/${id}`), json('DELETE', { confirm: true })),
}

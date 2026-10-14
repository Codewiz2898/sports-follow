// The sports this app knows, from the server (GET /api/sports, sports_follow/sports/): what each is called,
// which free-text names are it, what its alerts say, and whether this build runs it. Loaded once before the
// first render (main.tsx), from the last copy kept on this device when there is one; colours come from
// /api/sports.css, linked in index.html.

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
}

interface Catalog { sports: SportInfo[]; other: SportInfo }

const KEPT = 'sf-sports'
const OTHER: SportInfo = {
  key: 'other', label: '', on: true, live: false, pattern: '', exclude: '', stats_title: 'Season', team_results: false, keeps_names: false,
  alerts: { key: ['Big news', 'Injuries, transfers and retirements, from the news'], more: ['milestones', 'milestones in the news'] },
}

let catalog: Catalog = { sports: [], other: OTHER }
let matchers: { info: SportInfo; pattern: RegExp; exclude: RegExp | null }[] = []

function use(next: Catalog) {
  catalog = next
  matchers = next.sports.filter((s) => s.pattern).map((info) => ({ info, pattern: new RegExp(info.pattern), exclude: info.exclude ? new RegExp(info.exclude) : null }))
}

function kept(): Catalog | null {
  try {
    const raw = localStorage.getItem(KEPT)
    return raw ? (JSON.parse(raw) as Catalog) : null
  } catch {
    return null
  }
}

/** The catalog before the first render: the copy kept on this device at once (refreshed for next time),
 * else the server's answer (a few seconds at most; offline, the app starts without sport names). */
export async function loadSports(): Promise<void> {
  const fetched = fetch('/api/sports', { credentials: 'same-origin' })
    .then((res) => (res.ok ? (res.json() as Promise<Catalog>) : null))
    .then((data) => {
      if (data?.sports) {
        try { localStorage.setItem(KEPT, JSON.stringify(data)) } catch { /* private mode: fetched each time */ }
      }
      return data
    })
    .catch(() => null)
  const copy = kept()
  if (copy?.sports) return use(copy)
  const data = await Promise.race([fetched, new Promise<null>((done) => setTimeout(() => done(null), 4000))])
  if (data?.sports) use(data)
}

/** The sport a free-text sport name is ("Women's football", "CS2"), or "other". Where two match, the one
 * matching earliest in the name wins: "eFootball" is esports, "American football" is ruled out of football. */
export function sportInfo(name?: string | null): SportInfo {
  const s = (name ?? '').toLowerCase()
  let best: { info: SportInfo; at: number; length: number } | null = null
  for (const { info, pattern, exclude } of matchers) {
    const m = pattern.exec(s)
    if (!m || exclude?.test(s)) continue
    if (!best || m.index < best.at || (m.index === best.at && m[0].length > best.length)) best = { info, at: m.index, length: m[0].length }
  }
  return best?.info ?? catalog.other
}

/** Which colour a sport wears (/api/sports.css, [data-sport]). */
export function sportKey(name?: string | null): string {
  return sportInfo(name).key
}

/** Models report the sport in whatever case they like ("cricket", "CRICKET"); show it one way. A game's
 * own name stays as written ("CS2", "League of Legends", "PUBG Mobile"). */
export function sportName(sport?: string | null): string {
  if (!sport) return ''
  if (sportInfo(sport).keeps_names && /[A-Z]/.test(sport) && sport.toLowerCase() !== sportKey(sport)) return sport
  return sport.replace(/\S+/g, (w) => (w === w.toUpperCase() && w.length <= 4 ? w : w[0].toUpperCase() + w.slice(1).toLowerCase()))
}

/** The sports this build runs, in the order the app lists them: the search filters. */
export function runningSports(): SportInfo[] {
  return catalog.sports.filter((s) => s.on)
}

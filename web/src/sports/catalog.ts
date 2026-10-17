// The sports this app knows, from the server (GET /api/sports, sports_follow/sports/): what each is called,
// which free-text names are it, what its alerts say, and whether this build runs it. Loaded once before the
// first render (main.tsx), from the last copy kept on this device when there is one; colours come from
// /api/sports.css, linked in index.html. The matching itself is naming.ts, shared with the Android app.

import type { SportCatalog, SportInfo } from '../types'
import { NO_SPORTS, sportsOf } from './naming'

export type { SportInfo }

const KEPT = 'sf-sports'

let sports = sportsOf(NO_SPORTS)

function kept(): SportCatalog | null {
  try {
    const raw = localStorage.getItem(KEPT)
    return raw ? (JSON.parse(raw) as SportCatalog) : null
  } catch {
    return null
  }
}

/** The catalog before the first render: the copy kept on this device at once (refreshed for next time),
 * else the server's answer (a few seconds at most; offline, the app starts without sport names). */
export async function loadSports(): Promise<void> {
  const fetched = fetch('/api/sports', { credentials: 'same-origin' })
    .then((res) => (res.ok ? (res.json() as Promise<SportCatalog>) : null))
    .then((data) => {
      if (data?.sports) {
        try { localStorage.setItem(KEPT, JSON.stringify(data)) } catch { /* private mode: fetched each time */ }
      }
      return data
    })
    .catch(() => null)
  const copy = kept()
  if (copy?.sports) {
    sports = sportsOf(copy)
    return
  }
  const data = await Promise.race([fetched, new Promise<null>((done) => setTimeout(() => done(null), 4000))])
  if (data?.sports) sports = sportsOf(data)
}

/** The sport a free-text sport name is ("Women's football", "CS2"), or "other" (naming.ts). */
export function sportInfo(name?: string | null): SportInfo {
  return sports.info(name)
}

/** Which colour a sport wears (/api/sports.css, [data-sport]). */
export function sportKey(name?: string | null): string {
  return sportInfo(name).key
}

/** The key a sport is drawn as (its view: board, line art): "esports" for "CS2" (naming.ts). */
export function sportDrawn(name?: string | null): string {
  return sports.drawn(name)
}

/** A player's sport written one way: "Cricket" for "CRICKET", a game's own name as written (naming.ts). */
export function sportName(sport?: string | null): string {
  return sports.name(sport)
}

/** The sports this build runs, in the order the app lists them: the search filters. */
export function runningSports(): SportInfo[] {
  return sports.running()
}

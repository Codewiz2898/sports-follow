// The sports this app knows, from the server (GET /api/sports, sports_follow/sports/): what each is called,
// which free-text names are it, what its alerts say, and whether this build runs it. Loaded before the first
// render (main.tsx), from the last copy kept on this device when there is one; the server's answer replaces it
// as it comes, and the app draws again (onSports): a sport switched on or off in /admin since shows at once
// (BUG-166). Asked again when the app comes back to the front, at most every ten minutes. Colours come from
// /api/sports.css, linked in index.html. The matching itself is naming.ts, shared with the Android app.

import type { SportCatalog, SportInfo } from '../types'
import { NO_SPORTS, sportsOf } from './naming'

export type { SportInfo }

const KEPT = 'sf-sports'
const AGAIN_MS = 10 * 60_000  // back in the app this long after the server was last asked: asked again

let sports = sportsOf(NO_SPORTS)
let version = 0  // how many times the catalog has changed: what the app draws again on (useSports)
let asked = -Infinity  // when the server was last asked
// Whether the app has a catalog (the server's, or the copy kept on this device), is still waiting for its first, or
// that failed: an empty list of sports means none only once known (the welcome, which offers them).
let known: 'known' | 'asking' | 'failed' = 'asking'
const listeners = new Set<() => void>()

function kept(): SportCatalog | null {
  try {
    const raw = localStorage.getItem(KEPT)
    return raw ? (JSON.parse(raw) as SportCatalog) : null
  } catch {
    return null
  }
}

/** The server's catalog, applied as it comes and kept for the next start: past the browser's copy of it (kept five
 * minutes) and the service worker's (its last answer only offline). Null when it can't be had. */
function ask(): Promise<SportCatalog | null> {
  asked = Date.now()
  return fetch('/api/sports', { credentials: 'same-origin', cache: 'no-store' })
    .then((res) => (res.ok ? (res.json() as Promise<SportCatalog>) : null))
    .then((data) => {
      if (data?.sports) {
        apply(data)
        try { localStorage.setItem(KEPT, JSON.stringify(data)) } catch { /* private mode: fetched each time */ }
      }
      return data
    })
    .catch(() => null)
    .then((data) => {
      if (!data?.sports && known === 'asking') told('failed')
      return data
    })
}

/** Whether there's a catalog yet (catalogKnown), the app told when that changes. */
function told(state: typeof known) {
  if (known === state) return
  known = state
  version++
  for (const listener of listeners) listener()
}

/** Whether the app has the sports yet: 'known' (an empty list is none), 'asking' (none until the server answers), or
 * 'failed' (it didn't; asked again when the app comes back to the front, or askAgain). */
export function catalogKnown(): 'known' | 'asking' | 'failed' {
  return known
}

/** Ask the server for the sports now, after its first answer failed (the welcome's Try again). */
export function askAgain(): void {
  if (known === 'failed') told('asking')
  void ask()
}

/** A catalog in place of the one the app has, and the app told, unless it's the same. */
function apply(data: SportCatalog) {
  told('known')
  if (JSON.stringify(data) === JSON.stringify(sports.catalog)) return
  sports = sportsOf(data)
  version++
  for (const listener of listeners) listener()
}

/** The catalog before the first render: the copy kept on this device at once (the server's answer replaces it as
 * it comes), else the server's answer (a few seconds at most; offline, the app starts without sport names). */
export async function loadSports(): Promise<void> {
  const fetched = ask()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && Date.now() - asked >= AGAIN_MS) void ask()
    })
  }
  const copy = kept()
  if (copy?.sports) {
    sports = sportsOf(copy)
    known = 'known'
    return
  }
  await Promise.race([fetched, new Promise<null>((done) => setTimeout(() => done(null), 4000))])
}

/** Call `listener` whenever the catalog changes (a sport switched on or off); returns how to stop. */
export function onSports(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** How many times the catalog has changed: a snapshot for useSyncExternalStore (sports/index.tsx's useSports). */
export function sportsVersion(): number {
  return version
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

/** Every sport the app lists, in its order, then the groups that stand for several of them (an esports pro whose
 * game can't be told is esports'): Today's game chips, of the ones on it. */
export function listedSports(): SportInfo[] {
  return [...sports.catalog.sports, ...(sports.catalog.groups ?? [])]
}

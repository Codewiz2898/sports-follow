// How a team is drawn (sports_follow/logos.py): its logo from our own origin, or our badge (its short code) where
// there's none or its logos are off, and its two colours for its side of a game's band (BHA-665). Looks are asked for
// by the ids a game's board, its fixtures and Call it carry ("system:team id"), a few screens' worth at once, and kept
// for the page's life: a logo taken down stops being served at once, and a page loaded after shows our badge. Shared
// with the Android app: each app keeps its own, asked with its own api.looks, behind its useLooks (its api.ts).
import type { Look, TeamColours } from './types'

export type { Look, TeamColours }

const MOST = 80  // keys one ask carries (logos.LOOKS_MOST)
const WAIT_MS = 20  // keys asked for this close together go in one ask
const AGAIN_MS = 60_000  // after a failed ask, its keys are asked again this long after

/** A team's key for its look, from where its game's address says its source is ("sportmonks_football/19732648") and its id there. */
export function teamKey(match: string | null | undefined, id: string | number | null | undefined): string | null {
  const system = (match ?? '').split('/')[0]
  return system && id != null && String(id) !== '' ? `${system}:${id}` : null
}

/** The teams a screen wants looks for, once each: its keys with none missing. */
export function lookKeys(keys: (string | null | undefined)[]): string[] {
  return [...new Set(keys.filter((k): k is string => !!k))]
}

/** An app's looks, asked for its way (its api.looks), for its useLooks: some teams' as kept (undefined while asking,
 * null for a team the registry hasn't); asking for those not asked yet and those gone stale; and hearing of each
 * answer. */
export interface LookKeeper {
  looks: (keys: string[]) => Record<string, Look | null | undefined>
  ask: (keys: string[]) => void
  hear: (heard: () => void) => () => void
}

export function lookKeeper(asked: (keys: string[]) => Promise<{ looks: Record<string, Look> }>): LookKeeper {
  const kept = new Map<string, Look | null>()  // null: asked, and the registry hasn't the team
  const stale = new Set<string>()  // a minute after a failed ask: asked again by the screens that want it
  const asking = new Set<string>()
  const heard = new Set<() => void>()
  let queued: string[] = []
  let timer: ReturnType<typeof setTimeout> | null = null

  function flush() {
    timer = null
    const keys = queued
    queued = []
    for (let i = 0; i < keys.length; i += MOST) {
      const some = keys.slice(i, i + MOST)
      asked(some).then(
        (r) => { for (const k of some) kept.set(k, r.looks[k] ?? null) },
        () => {  // badges and the sport's band for now, kept while they're asked again a minute on
          for (const k of some) kept.set(k, null)
          setTimeout(() => {
            for (const k of some) if (kept.get(k) === null) stale.add(k)
            for (const fn of heard) fn()
          }, AGAIN_MS)
        },
      ).finally(() => {
        for (const k of some) asking.delete(k)
        for (const fn of heard) fn()
      })
    }
  }

  function ask(keys: string[]) {
    const fresh = keys.filter((k) => (!kept.has(k) || stale.has(k)) && !asking.has(k))
    if (!fresh.length) return
    for (const k of fresh) { asking.add(k); stale.delete(k) }
    queued.push(...fresh)
    if (!timer) timer = setTimeout(flush, WAIT_MS)
  }

  return {
    looks: (keys) => Object.fromEntries(keys.map((k) => [k, kept.get(k)])),
    ask,
    hear(fn) {
      heard.add(fn)
      return () => { heard.delete(fn) }
    },
  }
}

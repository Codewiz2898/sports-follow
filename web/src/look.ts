// How a team is drawn (sports_follow/logos.py): its logo from our own origin, or our badge (its short code) where
// there's none or its logos are off, and its two colours for its side of a game's band (BHA-665). Looks are asked for
// by the ids a game's board, its fixtures and Call it carry ("system:team id"), a few screens' worth at once, and kept
// for the page's life: a logo taken down stops being served at once, and a page loaded after shows our badge. Shared
// with the Android app: each app keeps its own, asked with its own api.looks (lookKeeper, in its api.ts).
import { useEffect, useMemo, useState } from 'react'
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

/** An app's looks, asked for its way (its api.looks): its useLooks. */
export function lookKeeper(asked: (keys: string[]) => Promise<{ looks: Record<string, Look> }>) {
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

  /** These teams' looks, by key: undefined while asking, null for a team the registry hasn't. */
  return function useLooks(keys: (string | null | undefined)[]): Record<string, Look | null | undefined> {
    const wanted = useMemo(() => [...new Set(keys.filter((k): k is string => !!k))], [keys.join('|')])  // eslint-disable-line react-hooks/exhaustive-deps
    const [, heardAgain] = useState(0)
    useEffect(() => {
      const again = () => heardAgain((n) => n + 1)
      heard.add(again)
      return () => { heard.delete(again) }
    }, [wanted])
    useEffect(() => { ask(wanted) })  // after every render: those not asked yet, and those gone stale
    return Object.fromEntries(wanted.map((k) => [k, kept.get(k)]))
  }
}

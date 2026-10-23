// How a team is drawn (sports_follow/logos.py): its logo from our own origin, or our badge (its short code) where
// there's none or its logos are off, and its two colours for its side of a game's band (BHA-665). Looks are asked for
// by the ids a game's board, its fixtures and Call it carry ("system:team id"), a few screens' worth at once, and kept
// for the page's life: a logo taken down stops being served at once, and a page loaded after shows our badge.
import { useEffect, useMemo, useState } from 'react'
import { request } from './api'

export interface TeamColours { fill: string; accent: string }
/** logo: its address on our origin, or null (our badge); colours: null for its sport's; tones: its logo's own colours, most used first. */
export interface Look { logo: string | null; short: string; colours: TeamColours | null; tones: string[]; set: boolean }

const MOST = 80  // keys one ask carries (logos.LOOKS_MOST)
const WAIT_MS = 20  // keys asked for this close together go in one ask
const AGAIN_MS = 60_000  // after a failed ask, its keys are asked again this long after
const kept = new Map<string, Look | null>()  // null: asked, and the registry hasn't the team
const asking = new Set<string>()
const heard = new Set<() => void>()
let queued: string[] = []
let timer: ReturnType<typeof setTimeout> | null = null

/** A team's key for its look, from where its game's address says its source is ("sportmonks_football/19732648") and its id there. */
export function teamKey(match: string | null | undefined, id: string | number | null | undefined): string | null {
  const system = (match ?? '').split('/')[0]
  return system && id != null && String(id) !== '' ? `${system}:${id}` : null
}

function flush() {
  timer = null
  const keys = queued
  queued = []
  for (let i = 0; i < keys.length; i += MOST) {
    const some = keys.slice(i, i + MOST)
    request<{ looks: Record<string, Look> }>(`/api/looks?teams=${some.map(encodeURIComponent).join(',')}`).then(
      (r) => { for (const k of some) kept.set(k, r.looks[k] ?? null) },
      () => {  // badges and the sport's band for now; asked again by a screen a minute on
        for (const k of some) kept.set(k, null)
        setTimeout(() => { for (const k of some) if (kept.get(k) === null) kept.delete(k) }, AGAIN_MS)
      },
    ).finally(() => {
      for (const k of some) asking.delete(k)
      for (const fn of heard) fn()
    })
  }
}

function ask(keys: string[]) {
  const fresh = keys.filter((k) => !kept.has(k) && !asking.has(k))
  if (!fresh.length) return
  for (const k of fresh) asking.add(k)
  queued.push(...fresh)
  if (!timer) timer = setTimeout(flush, WAIT_MS)
}

/** These teams' looks, by key: undefined while asking, null for a team the registry hasn't. */
export function useLooks(keys: (string | null | undefined)[]): Record<string, Look | null | undefined> {
  const wanted = useMemo(() => [...new Set(keys.filter((k): k is string => !!k))], [keys.join('|')])  // eslint-disable-line react-hooks/exhaustive-deps
  const [, heardAgain] = useState(0)
  useEffect(() => {
    const again = () => heardAgain((n) => n + 1)
    heard.add(again)
    ask(wanted)
    return () => { heard.delete(again) }
  }, [wanted])
  return Object.fromEntries(wanted.map((k) => [k, kept.get(k)]))
}

// ---------------------------------------------------------------- colours

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function lum(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** How far apart two colours are: the sum of their channels' differences. */
export function apart(a: string, b: string): number {
  const x = rgb(a), y = rgb(b)
  return Math.abs(x[0] - y[0]) + Math.abs(x[1] - y[1]) + Math.abs(x[2] - y[2])
}

/** The ink that reads on a colour: ink or white, whichever has the more contrast. */
export function inkOn(fill: string): string {
  const l = lum(fill)
  return (l + 0.05) / (lum('#0d0d0d') + 0.05) >= (1.05) / (l + 0.05) ? '#0d0d0d' : '#ffffff'
}

const NEAR = 90  // a logo's main colour this close to the fill under it would vanish: it goes in a disc
const CLASH = 120  // two sides' colours this close can't be told apart

/** The disc a logo sits in on a colour, where it would vanish on it (Gen.G's gold on gold): white, or ink for a light
 * logo; null where it shows as it is. */
export function discFor(look: Look | null | undefined, fill: string): string | null {
  const main = look?.tones[0]
  if (!look?.logo || !main || apart(main, fill) >= NEAR) return null
  return lum(main) > 0.5 ? '#0d0d0d' : '#ffffff'
}

/** A side of a game's band: its colours, the ink on them, and the disc its logo needs. */
export interface Side { fill: string; accent: string; ink: string; disc: string | null }

/** Both sides' colours for a game's band, home (left) first, or null where either team has none (its sport's band
 * then). Two sides in colours too close to tell apart: the away side wears its second (white, or its accent where
 * it's white already), as a team changes kit (Freiburg v Bayern). */
export function sidesOf(home: Look | null | undefined, away: Look | null | undefined): [Side, Side] | null {
  const h = home?.colours, a0 = away?.colours
  if (!h || !a0) return null
  // The away side's second colour: white, with its own as the stripe (Bayern at Freiburg), or its accent where it
  // plays in white already, or where white is as close to the home side's.
  const second = (c: string) => apart(h.fill, c) >= CLASH
  const a = apart(h.fill, a0.fill) >= CLASH ? a0
    : second('#ffffff') && apart(a0.fill, '#ffffff') >= CLASH ? { fill: '#ffffff', accent: a0.fill }
    : second(a0.accent) ? { fill: a0.accent, accent: a0.fill }
    : null
  if (!a) return null  // nothing tells them apart: the sport's band
  const side = (c: TeamColours, look: Look | null | undefined): Side => ({ fill: c.fill, accent: c.accent, ink: inkOn(c.fill), disc: discFor(look, c.fill) })
  return [side(h, home), side(a, away)]
}

/** A team's banner on its page (9c): its colour, its second past a slant, the ink on its colour, and the seam
 * between them. */
export interface Banner { fill: string; accent: string; ink: string; seam: string }

/** A team's banner, or null where it has no colours (its sport's, with an ink second). The seam is white, or ink where
 * either colour is near white already (Real Madrid's white and gold). */
export function bannerOf(look: Look | null | undefined): Banner | null {
  const c = look?.colours
  if (!c) return null
  const pale = (x: string) => apart(x, '#ffffff') < CLASH
  return { fill: c.fill, accent: c.accent, ink: inkOn(c.fill), seam: pale(c.fill) || pale(c.accent) ? '#0d0d0d' : '#ffffff' }
}

// How a team's colours are drawn (look.ts has its look): the ink that reads on a colour, whether its logo needs a disc on
// one, both sides of a game's band (7a) and a team's banner on its page (9c). Plain functions of colours, shared with
// the Android app.
import type { Look, TeamColours } from './types'

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

/** The disc a logo sits in on a colour (a card's, a pill's): white, or ink for a light logo. */
export function onColour(look: Look | null | undefined): string {
  return discFor(look, '#ffffff') ?? '#ffffff'
}

/** The disc a logo needs on white (a list, a board, a white card): none, or ink for a light logo. */
export function onWhite(look: Look | null | undefined): string | null {
  return discFor(look, '#ffffff')
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

// What both apps know about each sport beyond the server's catalog (catalog.ts): the scoreboard kinds it
// draws, its ground as line art, and how it words a player's line. Pure, with nothing of a browser's or of
// React Native's, so the web app (sports/<sport>.tsx) and the Android app (mobile/src/sports/) draw from one
// copy. A sport's look is its facts module here, one line in FACTS, and a board in each app.

import type { LiveStatus } from '../../types'
import { basketball } from './basketball'
import { chess } from './chess'
import { cricket } from './cricket'
import { esports } from './esports'
import { football } from './football'
import { tennis } from './tennis'

export interface SportFacts {
  key: string
  kinds: string[] // the scoreboard kinds it draws (the server's Snapshot.state["kind"])
  draws?: (state: never) => boolean // whether its board can draw this state; if not, the plain score shows
  // Its ground as line art (a pitch, a board): SVG shapes inside a 100×100 box, stroked in the card's ink.
  art?: string
  highlight?: (line?: string | null) => string | null // a line worth a word: "Triple-double", "Hundred"
  noLine?: string | null // a player with no line while live ("isn't in the matchday squad"); null: say nothing
  inHeadline?: string[] // stats its headline already says ("67* (71)" says Runs and Balls)
  short?: (state: never, live: LiveStatus) => string | null // the game in a few characters, for a live player's pill on Today ("1–0 · 67'"); without it, the pill says the player's headline
}

export const FACTS: SportFacts[] = [football, cricket, basketball, tennis, chess, esports]

/** The facts for a sport's key ("football"), or none. */
export function factsOf(key: string): SportFacts | undefined {
  return FACTS.find((f) => f.key === key)
}

/** The facts for a scoreboard of this kind ("battle_royale" is esports'), or none. */
export function factsOfKind(kind?: string | null): SportFacts | undefined {
  return kind ? FACTS.find((f) => f.kinds.includes(kind)) : undefined
}

/** Whether a sport's board draws this live state: a state it knows, in a shape it can draw. */
export function drawable(live: LiveStatus): SportFacts | undefined {
  const facts = factsOfKind(live.state?.kind)
  return facts && live.state && (facts.draws?.(live.state as never) ?? true) ? facts : undefined
}

/** A string's FNV-1a hash: the same game always gets the same turn of its art. */
function hash(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return h >>> 0
}

const TURNS = [-14, -6, 8, 16, 78, 90, 102, 180]

/** Where a game's line art sits on its card, so a row of cards from one sport doesn't repeat: turned
 * (degrees), centred somewhere in the card (x and y, percent of its width and height), and sized (times
 * the card's width, up to 320px: always cropped). */
export function artPlace(seed: string): { turn: number; x: number; y: number; size: number } {
  const h = hash(seed)
  return { turn: TURNS[h % TURNS.length], x: 20 + ((h >>> 3) % 61), y: 15 + ((h >>> 9) % 71), size: (120 + ((h >>> 15) % 61)) / 100 }
}

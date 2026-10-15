// What the app knows about sports: the catalog from the server (names, colours, alerts) and how each sport
// is drawn here (its view: live board, line art, line wording). Adding a sport's look is its view module plus
// one line in VIEWS; a sport without one still shows, with the plain score and no line art.

import type { CSSProperties } from 'react'
import { basketball } from './basketball'
import { chess } from './chess'
import { cricket } from './cricket'
import { esports } from './esports'
import { football } from './football'
import { tennis } from './tennis'
import type { SportView } from './view'

export { loadSports, runningSports, sportInfo, sportKey, sportName, type SportInfo } from './catalog'
export type { BoardProps, SportView } from './view'

const VIEWS: SportView[] = [football, cricket, basketball, tennis, chess, esports]

/** The view for a sport's key ("football"), or none. */
export function viewOf(key: string): SportView | undefined {
  return VIEWS.find((v) => v.key === key)
}

/** The view that draws a scoreboard of this kind ("battle_royale" is esports'), or none. */
export function viewOfKind(kind?: string | null): SportView | undefined {
  return kind ? VIEWS.find((v) => v.kinds.includes(kind)) : undefined
}

/** A string's FNV-1a hash: the same game always gets the same turn of its art. */
function hash(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193)
  return h >>> 0
}

const TURNS = [-14, -6, 8, 16, 78, 90, 102, 180]

/**
 * The sport's ground as faint line art behind a card, in the card's ink (currentColor); nothing for a sport
 * that draws none. Each game (its `seed`: an event id or key) turns, places and sizes it its own way, so a
 * row of cards from one sport doesn't repeat. The card is `position: relative; overflow: hidden`.
 */
export function Backdrop({ sport, seed }: { sport: string; seed: string }) {
  const art = viewOf(sport)?.backdrop
  if (!art) return null
  const h = hash(seed)
  const place = {
    '--bd-turn': `${TURNS[h % TURNS.length]}deg`,
    '--bd-x': `${20 + ((h >>> 3) % 61)}%`, // the art's centre, somewhere in the card
    '--bd-y': `${15 + ((h >>> 9) % 71)}%`,
    '--bd-size': (120 + ((h >>> 15) % 61)) / 100, // times the card's width (up to 320px): always cropped
  } as CSSProperties
  return (
    <svg className="backdrop" viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={place}>
      {art}
    </svg>
  )
}

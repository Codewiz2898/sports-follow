// What the app knows about sports: the catalog from the server (names, colours, alerts) and how each sport
// is drawn here (its view: its facts, shared with the Android app, and its live board). Adding a sport's look
// is its facts module (facts/), its view module, and one line in VIEWS; a sport without one still shows, with
// the plain score and no line art.

import type { CSSProperties } from 'react'
import { basketball } from './basketball'
import { chess } from './chess'
import { cricket } from './cricket'
import { esports } from './esports'
import { artPlace } from './facts'
import { football } from './football'
import { tennis } from './tennis'
import type { SportView } from './view'

export { loadSports, runningSports, sportInfo, sportKey, sportName, type SportInfo } from './catalog'
export type { BoardProps, MatchProps, SportView } from './view'

const VIEWS: SportView[] = [football, cricket, basketball, tennis, chess, esports]

/** The view for a sport's key ("football"), or none. */
export function viewOf(key: string): SportView | undefined {
  return VIEWS.find((v) => v.key === key)
}

/** The view that draws a scoreboard of this kind ("battle_royale" is esports'), or none. */
export function viewOfKind(kind?: string | null): SportView | undefined {
  return kind ? VIEWS.find((v) => v.kinds.includes(kind)) : undefined
}

/**
 * The sport's ground as faint line art behind a card, in the card's ink (currentColor); nothing for a sport
 * that draws none. Each game (its `seed`: an event id or key) turns, places and sizes it its own way
 * (facts.artPlace), so a row of cards from one sport doesn't repeat. The card is `position: relative;
 * overflow: hidden`. The art is the sport's own constant markup (facts/), never anything a source sent.
 */
export function Backdrop({ sport, seed }: { sport: string; seed: string }) {
  const art = viewOf(sport)?.art
  if (!art) return null
  const at = artPlace(seed)
  const place = { '--bd-turn': `${at.turn}deg`, '--bd-x': `${at.x}%`, '--bd-y': `${at.y}%`, '--bd-size': at.size } as CSSProperties
  return (
    <svg className="backdrop" viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={place} dangerouslySetInnerHTML={{ __html: art }} />
  )
}

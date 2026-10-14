// What the app knows about sports: the catalog from the server (names, colours, alerts) and how each sport
// is drawn here (its view: live board, mark, line wording). Adding a sport's look is its view module plus
// one line in VIEWS; a sport without one still shows, with the plain score and mark.

import type { SVGProps } from 'react'
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

/** Faint line art behind a hero card: the sport's own, or a plain ring. */
export function SportMark({ sport, className, ...rest }: { sport: string } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 100 100" fill="none" stroke="currentColor" strokeWidth={3.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} {...rest}>
      {viewOf(sport)?.mark ?? <circle cx="50" cy="50" r="46" />}
    </svg>
  )
}

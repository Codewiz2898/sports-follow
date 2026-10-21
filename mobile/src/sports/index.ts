// How the Android app draws each sport: its facts (web/src/sports/facts/, shared with the web app: kinds,
// line art, how it words a line), its live board here, and what its match page shows under the board. A
// sport's look is its facts module there, its module here and the web's, and one line in each list.

import { drawable } from '../../../web/src/sports/facts'
import type { LiveStatus } from '../api'
import { basketball } from './basketball'
import type { Board, MatchPart } from './board'
import { chess } from './chess'
import { cricket } from './cricket'
import { esports } from './esports'
import { football } from './football'
import { tennis } from './tennis'

/** How one sport is drawn here (the web's SportView): its live board, and its part of a match page, if any. */
interface SportView { key: string; Board: Board; Match?: MatchPart }

const VIEWS: SportView[] = [football, cricket, basketball, tennis, chess, esports]

/** The board that draws this live state, or none (then the plain score shows). */
export function boardFor(live: LiveStatus): Board | undefined {
  const facts = drawable(live)
  return facts ? VIEWS.find((v) => v.key === facts.key)?.Board : undefined
}

/** What a match page shows under its board for a sport, by the key it's drawn as (an esports game is drawn as
 * its group: Sports.drawn): a timeline, a scorecard, maps or moves; none for a sport without one, which lists
 * the game's key events instead, if its source names any. */
export function matchPartFor(key: string): MatchPart | undefined {
  return VIEWS.find((v) => v.key === key)?.Match
}

export { INK, MomentKind, onBoard, onPaper, onSport, type MatchProps, type Palette } from './board'

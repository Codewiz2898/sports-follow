// How the Android app draws each sport: its facts (web/src/sports/facts/, shared with the web app: kinds,
// line art, how it words a line) and its live board here. A sport's look is its facts module there, its
// board module here and the web's, and one line in each list.

import { drawable } from '../../../web/src/sports/facts'
import type { LiveStatus } from '../api'
import { basketball } from './basketball'
import type { Board } from './board'
import { chess } from './chess'
import { cricket } from './cricket'
import { esports } from './esports'
import { football } from './football'
import { tennis } from './tennis'

const BOARDS: { key: string; Board: Board }[] = [football, cricket, basketball, tennis, chess, esports]

/** The board that draws this live state, or none (then the plain score shows). */
export function boardFor(live: LiveStatus): Board | undefined {
  const facts = drawable(live)
  return facts ? BOARDS.find((b) => b.key === facts.key)?.Board : undefined
}

export { onInk, onPaper, type Palette } from './board'

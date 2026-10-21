import type { ReactNode } from 'react'
import type { LiveStatus, Stat } from '../api'
import type { SportFacts } from './facts'

/** What a sport's live board is drawn from: the source's state (its kind says whose), and the live block. */
export interface BoardProps<S = unknown> {
  state: S
  clock?: string | null
  compact?: boolean
  teams?: string[] // the followed player's teams: their row in a lobby
  live: LiveStatus
  // Each of the fan's players' numbers in the game, on a card with several (Today's, of a game two of theirs play
  // each other in); without it, the live block's own. A board drawn from their side (chess) has none to take when
  // they're on both.
  lines?: Stat[][]
}

/** What a sport's part of a match page is drawn from: its sections (the server's Sport.match_page, by name:
 * a timeline, a scorecard, maps), the game's live block, if it has one yet, and the names of the fan's
 * players in it, to mark them. */
export interface MatchProps<P = unknown> {
  page: P
  live: LiveStatus | null
  yours?: string[]
}

/**
 * How the web app draws one sport: its facts (the kinds it draws, its line art, how it words a player's
 * line: facts/, shared with the Android app), its live board, and what its match page shows under the board
 * (Match; a sport without one lists the game's key events, if the source names any). Everything else about
 * a sport (its name, colour, alerts) comes from the server (catalog.ts). A sport without a view gets the
 * plain score and no line art.
 */
export interface SportView extends SportFacts {
  Board: (props: BoardProps<never>) => ReactNode
  Match?: (props: MatchProps<never>) => ReactNode
}

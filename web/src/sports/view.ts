import type { ReactNode } from 'react'
import type { LiveStatus } from '../api'
import type { SportFacts } from './facts'

/** What a sport's live board is drawn from: the source's state (its kind says whose), and the live block. */
export interface BoardProps<S = unknown> {
  state: S
  clock?: string | null
  compact?: boolean
  teams?: string[] // the followed player's teams: their row in a lobby
  live: LiveStatus
}

/**
 * How the web app draws one sport: its facts (the kinds it draws, its line art, how it words a player's
 * line: facts/, shared with the Android app) and its live board. Everything else about a sport (its name,
 * colour, alerts) comes from the server (catalog.ts). A sport without a view gets the plain score and no
 * line art.
 */
export interface SportView extends SportFacts {
  Board: (props: BoardProps<never>) => ReactNode
}

import type { ReactNode } from 'react'
import type { LiveStatus } from '../api'

/** What a sport's live board is drawn from: the source's state (its kind says whose), and the live block. */
export interface BoardProps<S = unknown> {
  state: S
  clock?: string | null
  compact?: boolean
  teams?: string[] // the followed player's teams: their row in a lobby
  live: LiveStatus
}

/**
 * How the app draws one sport: its live board, the line art behind its cards on Today, and how it words a
 * player's line. Everything else about a sport (its name, colour, alerts) comes from the server
 * (catalog.ts). A sport without a view gets the plain score and no line art.
 */
export interface SportView {
  key: string
  kinds: string[] // the scoreboard kinds it draws (the server's Snapshot.state["kind"])
  Board: (props: BoardProps<never>) => ReactNode
  draws?: (state: never) => boolean // whether Board can draw this state; if not, the plain score shows
  backdrop?: ReactNode // its ground as line art (a pitch, a board), inside a 100×100 SVG, stroked in currentColor
  highlight?: (line?: string | null) => string | null // a line worth a word: "Triple-double", "Hundred"
  noLine?: string | null // a player with no line while live ("isn't in the matchday squad"); null: say nothing
  inHeadline?: string[] // stats its headline already says ("67* (71)" says Runs and Balls)
}

import type { LiveStatus } from '../../types'
import type { SportFacts } from '.'

export interface ChessSide { id: string; name: string; title?: string | null; rating?: number | null; fed?: string | null; clock?: string | null }
export interface ChessState {
  kind: 'chess'
  round?: string | null
  tournament?: string | null
  white?: ChessSide
  black?: ChessSide
  fen?: string
  last_move?: string | null
  turn?: 'white' | 'black'
  move?: number | null
  result?: string | null
  match?: { games: number; score: string } | null  // knockout rounds: several games between the same two players
}
// A finished game's match page (the server's Chess.match_page): its moves in pairs ("1. d4 Nf6"), its opening, how it
// ended where that says more than the result ("resigned"), and in a mini-match the number of the game they're of.
export interface ChessPage { moves?: string[]; opening?: string | null; ending?: string | null; game?: number | null }

/** The line over a finished game's moves: its opening, and how it ended ("Resigned"). */
export function movesAbout(page: ChessPage): string {
  return [page.opening, page.ending && page.ending[0].toUpperCase() + page.ending.slice(1)].filter(Boolean).join(' · ')
}

export const PIECES: Record<string, string> = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' }

/** The position from the followed player's side: its eight ranks of squares (a piece's FEN letter, or
 * null), whether the board is turned (they're Black), and the squares of the last move. */
export function position(state: ChessState, live: LiveStatus): { ranks: { name: string; piece: string | null; dark: boolean }[][]; flip: boolean; moved: Set<string> } {
  const flip = (live.player_stats ?? []).some((s) => s.label === 'Colour' && s.value === 'Black')
  const rows = (state.fen ?? '').split(' ')[0].split('/')
  const squares: (string | null)[][] = rows.map((row) => row.split('').flatMap((c) => (/\d/.test(c) ? Array(Number(c)).fill(null) : [c])))
  const moved = new Set(state.last_move ? [state.last_move.slice(0, 2), state.last_move.slice(2, 4)] : [])
  if (rows.length !== 8) return { ranks: [], flip, moved }
  const order = flip ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7]
  const ranks = order.map((r) => (flip ? [...squares[r]].reverse() : squares[r]).map((piece, i) => {
    const file = flip ? 7 - i : i
    return { name: `${'abcdefgh'[file]}${8 - r}`, piece, dark: (r + file) % 2 === 1 }
  }))
  return { ranks, flip, moved }
}

/** The big line between the players: the match score in a knockout, the result once it's over, else the clock. */
export function chessMiddle(state: ChessState, clock?: string | null): string | null | undefined {
  return state.match ? `Match ${state.match.score}` : state.result ? state.result.replace('-', '–') : clock
}

export const chess: SportFacts = {
  key: 'chess',
  kinds: ['chess'],
  draws: ((state: ChessState) => Boolean(state.white && state.black)) as SportFacts['draws'],
  // An empty chessboard: its edge and the 32 dark squares, filled.
  art: `<rect x="2" y="2" width="96" height="96" />
    <path stroke="none" fill="currentColor" fill-opacity="0.45" d="${Array.from({ length: 32 }, (_, i) => {
      const rank = Math.floor(i / 4)
      return `M${2 + 12 * (2 * (i % 4) + ((rank + 1) % 2))} ${2 + 12 * rank}h12v12h-12z`
    }).join('')}" />`,
  inHeadline: ['Colour', 'Opponent'],
}

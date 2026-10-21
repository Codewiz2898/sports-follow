import type { LiveStatus, Stat } from '../../types'
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
  eval?: ChessEval | null  // only while a follower wants to hear about swings: the engine looks at no other game
}
export type ChessColour = 'white' | 'black'
// The engine's latest look at the position (sports_follow/engine.py, settled by the server's Chess.settle), from
// White's side: in centipawns, or the moves to a forced mate (plus, White mates; minus, Black does), how deep it
// looked, and the position it looked at; with the band the swing alerts go by (band, raw, told) and when.
export interface ChessEval { cp?: number | null; mate?: number | null; depth?: number | null; fen?: string; at?: number; move?: number | null; band?: number; raw?: number; told?: number }
// A finished game's match page (the server's Chess.match_page): its moves in pairs ("1. d4 Nf6"), its opening, how it
// ended where that says more than the result ("resigned"), and in a mini-match the number of the game they're of.
export interface ChessPage { moves?: string[]; opening?: string | null; ending?: string | null; game?: number | null }

/** The line over a finished game's moves: its opening, and how it ended ("Resigned"). */
export function movesAbout(page: ChessPage): string {
  return [page.opening, page.ending && page.ending[0].toUpperCase() + page.ending.slice(1)].filter(Boolean).join(' · ')
}

export const PIECES: Record<string, string> = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' }

/** The side a board is first seen from: the fan's player's (their Colour in `lines`, each of the fan's players'
 * numbers on the card; by default the live block's own), or White where the card has no one side of theirs:
 * both players followed, or neither (the match page of a game followed by itself). */
export function followedSide(live: LiveStatus, lines: Stat[][] = [live.player_stats ?? []]): ChessColour {
  const colours = new Set(lines.flatMap((stats) => stats.filter((s) => s.label === 'Colour').map((s) => s.value)))
  return colours.size === 1 && colours.has('Black') ? 'black' : 'white'
}

/** The position from a side, the followed player's unless the fan turned the board (`flip`: from Black's): its
 * eight ranks of squares (a piece's FEN letter, or null), whether the board is turned, and the squares of the
 * last move. */
export function position(state: ChessState, live: LiveStatus, flip = followedSide(live) === 'black'): { ranks: { name: string; piece: string | null; dark: boolean }[][]; flip: boolean; moved: Set<string> } {
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

/** White's share of an evaluation bar, 0 to 100: their winning chances in Lichess's model (the one the server's
 * bands are drawn on, sports/chess.py), from 2 / (1 + e^(-0.00368208 * cp)) - 1, with centipawns past ±1000
 * counted as 1000 as Lichess does. A forced mate is the whole bar for the side that mates; mate on the board
 * (mate 0), for the side not to move. */
export function whiteShare(ev: ChessEval): number {
  if (ev.mate != null) return (ev.mate === 0 ? ev.fen?.split(' ')[1] === 'b' : ev.mate > 0) ? 100 : 0
  const cp = Math.max(-1000, Math.min(1000, ev.cp ?? 0))
  return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * cp)) - 1)
}

/** An evaluation's score from White's side, in pawns as Lichess gives it: "+1.4", "−0.6", "0.0"; past ten, in
 * whole pawns ("+12": long decided, and it fits a narrow bar); a forced mate, "M4", or "−M4" when Black mates,
 * and "#" for mate on the board (an analysis board's checkmate).
 * A tenth's half rounds away from zero, in whole centipawns, as a swing alert's score does (sports/chess.py's
 * _score), so the bar and the alert agree. */
export function evalScore(ev: ChessEval): string {
  if (ev.mate === 0) return '#'
  if (ev.mate != null) return `${whiteShare(ev) < 50 ? '−' : ''}M${Math.abs(ev.mate)}`
  const cp = Math.round(ev.cp ?? 0)
  const tenths = Math.floor((Math.abs(cp) + 5) / 10)
  if (!tenths) return '0.0'
  return `${cp > 0 ? '+' : '−'}${tenths >= 100 ? Math.floor((tenths + 5) / 10) : `${Math.floor(tenths / 10)}.${tenths % 10}`}`
}

/** How deep the engine looked ("depth 28"), under a big board's bar and in every bar's words; none for a depth of
 * 0 or none. */
export function evalDepth(ev: ChessEval): string | null {
  return ev.depth ? `depth ${ev.depth}` : null
}

/** An evaluation bar's words, for a screen reader and a small board's title: "Engine evaluation +1.4, from White's
 * side, depth 28". */
export function evalWords(ev: ChessEval): string {
  const depth = evalDepth(ev)
  return `Engine evaluation ${evalScore(ev)}, from White's side${depth ? `, ${depth}` : ''}`
}

// Each castling right a standard FEN gives, and the king and rook it needs where they start.
const CASTLING: Record<string, string[]> = { K: ['e1K', 'h1R'], Q: ['e1K', 'a1R'], k: ['e8k', 'h8r'], q: ['e8k', 'a8r'] }

/** Whether a position plays by chess's standard rules, as a move generator knows them (chess.js, and the engine
 * without its 960 option): every castling right its FEN gives has the king and the rook on their starting
 * squares. A Freestyle (Chess960) game's start puts them elsewhere, or names the rook's file ("HAha"), and
 * castles another way, which a standard generator gets wrong; once neither side can castle, it plays as
 * chess. The live state doesn't say which a game is, so the position does. */
export function standardCastling(fen?: string | null): boolean {
  const [placement = '', , rights = '-'] = (fen ?? '').split(' ')
  if (rights === '-') return true
  if (!/^[KQkq]{1,4}$/.test(rights)) return false
  const rows = placement.split('/').map((row) => row.split('').flatMap((c) => (/\d/.test(c) ? Array<string>(Number(c)).fill('') : [c])))
  const at = (square: string) => rows[8 - Number(square[1])]?.['abcdefgh'.indexOf(square[0])]
  return [...rights].every((right) => CASTLING[right].every((need) => at(need.slice(0, 2)) === need[2]))
}

/** A finished game's moves (ChessPage.moves, in pairs: "1. d4 Nf6") one by one in SAN, to replay from the
 * start: the position after the nth is the nth ply's. Null for moves that don't run from the first on (a list
 * that starts with Black's, "1... e5", or skips a number), which can't be replayed from the start. */
export function gameMoves(pairs: string[]): string[] | null {
  const sans: string[] = []
  for (const [i, pair] of pairs.entries()) {
    const m = /^(\d+)\.\s+(\S+)(?:\s+(\S+))?$/.exec(pair.trim())
    if (!m || Number(m[1]) !== i + 1 || (!m[3] && i < pairs.length - 1)) return null
    sans.push(m[2], ...(m[3] ? [m[3]] : []))
  }
  return sans.length ? sans : null
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

// A fan's analysis board (a match page's Play from here): the line they play from a position and where along it
// the board is, with chess.js for what's legal, and a UCI engine's words read, its score turned to White's side
// for the bar. Pure, nothing of a browser's, so the Android app's board can play the same lines with an engine of
// its own; the web app loads it only when a fan opens one (chessAnalysis.tsx, its engine chessEngine.ts). The
// fan's lines stay on their device.

import { Chess, type Move, type Square } from 'chess.js'
import { standardCastling, type ChessEval, type ChessState } from './chess'

export const PIECE_NAMES: Record<string, string> = { k: 'king', q: 'queen', r: 'rook', b: 'bishop', n: 'knight', p: 'pawn' }

/** One move of a line, in SAN with its squares, and the position after it. */
export interface Ply { san: string; from: string; to: string; fen: string }

/** A line of moves from a position (`start`), and how far along it the board is (`at`: the plies played; 0, the
 * start). Stepping back keeps the moves after it, until the fan plays another there. */
export interface ChessLine { start: string; plies: Ply[]; at: number }

/** A move tried on a game, or null where it isn't legal (chess.js throws). */
function tried(game: Chess, move: string | { from: string; to: string; promotion?: string }): Move | null {
  try {
    return game.move(move)
  } catch {
    return null
  }
}

const ply = (m: Move): Ply => ({ san: m.san, from: m.from, to: m.to, fen: m.after })

/** Whether a FEN is a position the fan can play from. */
export function playable(fen?: string | null): boolean {
  if (!fen) return false
  try {
    new Chess(fen)
    return true
  } catch {
    return false
  }
}

/** Where an analysis starts: the game's position when the fan opened it (`base`, with the move that led there),
 * or a finished game's moves (SAN, from its start) at a ply of theirs. `asked` changes with each new start, the
 * board open or not; `focus` puts the keyboard on the board (the web's, opened by its button, not from the moves). */
export interface Opening { base: string; last?: string | null; moves?: string[] | null; ply?: number; asked: number; focus?: boolean }

/** The line an analysis starts with, or null for one that can't be played here: moves that don't replay (the
 * Moves list offers only those that do), or a position that isn't one, or not by the standard rules. */
export function openingLine(opening: Opening): ChessLine | null {
  if (opening.moves) return lineOfGame(opening.moves, opening.ply ?? 0)
  return playable(opening.base) && standardCastling(opening.base) ? lineFrom(opening.base) : null
}

/** A line from a position, with no moves yet. */
export function lineFrom(fen: string): ChessLine {
  return { start: fen, plies: [], at: 0 }
}

/** A finished game's moves (gameMoves: SAN, from the start) as a line, at the position after the `at`th; null
 * where one of them isn't legal there. */
export function lineOfGame(sans: string[], at: number): ChessLine | null {
  const game = new Chess()
  const start = game.fen()
  const plies: Ply[] = []
  for (const san of sans) {
    const m = tried(game, san)
    if (!m) return null
    plies.push(ply(m))
  }
  return { start, plies, at: Math.max(0, Math.min(at, plies.length)) }
}

/** The position the board is at. */
export function fenAt(line: ChessLine): string {
  return line.at ? line.plies[line.at - 1].fen : line.start
}

/** The line a number of moves back (minus) or on, within its moves. */
export function stepped(line: ChessLine, by: number): ChessLine {
  return { ...line, at: Math.max(0, Math.min(line.plies.length, line.at + by)) }
}

/** Where the piece on a square can go, if it's the side to move's: each square once (a promotion's four
 * pieces are one), and whether going there promotes. */
export function targets(fen: string, square: string): { to: string; promotes: boolean }[] {
  const seen = new Map<string, boolean>()
  for (const m of new Chess(fen).moves({ square: square as Square, verbose: true })) seen.set(m.to, Boolean(m.promotion))
  return [...seen].map(([to, promotes]) => ({ to, promotes }))
}

/** The fan's move where the board is, or null if it isn't legal: the line's own next move if it's that one (a
 * step on), else the line cut there with this move after it. `promotion`: the piece a pawn becomes (q, r, b, n). */
export function played(line: ChessLine, from: string, to: string, promotion?: string): ChessLine | null {
  const m = tried(new Chess(fenAt(line)), { from, to, promotion })
  if (!m) return null
  const next = line.plies[line.at]
  if (next && next.from === m.from && next.to === m.to && samePosition(next.fen, m.after)) return { ...line, at: line.at + 1 }
  return { ...line, plies: [...line.plies.slice(0, line.at), ply(m)], at: line.at + 1 }
}

/** An engine's line (UCI moves, "e7e8q") in SAN from a position, as far as its moves are legal there, at most
 * `most` of them. */
export function sanLine(fen: string, uci: string[], most = 6): string[] {
  const game = new Chess(fen)
  const sans: string[] = []
  for (const u of uci.slice(0, most)) {
    const m = tried(game, { from: u.slice(0, 2), to: u.slice(2, 4), promotion: u[4] })
    if (!m) break
    sans.push(m.san)
  }
  return sans
}

/** How a position has ended, if it has: "Checkmate", "Stalemate", or a draw by the material left or the
 * fifty-move rule. A repetition needs the game before it, which a position alone hasn't. */
export function ended(fen: string): string | null {
  const game = new Chess(fen)
  if (game.isCheckmate()) return 'Checkmate'
  if (game.isStalemate()) return 'Stalemate'
  if (game.isInsufficientMaterial()) return 'Draw: neither side can mate'
  if (game.isDraw()) return 'Draw by the fifty-move rule'
  return null
}

/** The square of the side to move's king when it's in check, else null. */
export function checked(fen: string): string | null {
  const game = new Chess(fen)
  if (!game.inCheck()) return null
  const turn = game.turn()
  for (const row of game.board()) for (const sq of row) if (sq?.type === 'k' && sq.color === turn) return sq.square
  return null
}

/** Whether two FENs are one position: the same pieces, the same side to move (the move counters aside). */
export function samePosition(a?: string | null, b?: string | null): boolean {
  const of = (fen?: string | null) => fen?.split(' ').slice(0, 2).join(' ')
  return Boolean(a && b) && of(a) === of(b)
}

/** Where along its line the board is: "After 12. Nf3", or at its start "The game's position" (the game's own,
 * `base`) or "The starting position" (a finished game's moves, from the first). */
export function lineWhere(line: ChessLine, base: string): string {
  const last = line.at ? line.plies[line.at - 1] : null
  const before = line.at > 1 ? line.plies[line.at - 2].fen : line.start
  return last ? `After ${numbered([last.san], before)}` : samePosition(fenAt(line), base) ? "The game's position" : 'The starting position'
}

/** What the game has done since the fan began playing from `base`: "The game is over: 1–0", or "The game has
 * moved on · move 34"; null while it's where they began. */
export function movedOn(state: ChessState, base: string): string | null {
  if (samePosition(state.fen, base)) return null
  return state.result ? `The game is over: ${state.result.replace('-', '–')}` : `The game has moved on${state.move ? ` · move ${state.move}` : ''}`
}

/** A square of the analysis board as a screen reader says it: "e4, white pawn", and ", move here" where the
 * picked piece can go. `piece` is its FEN letter (white's in capitals), or null. */
export function squareWords(name: string, piece: string | null, aim: boolean): string {
  const colour = piece && (piece === piece.toUpperCase() ? 'white' : 'black')
  return [name, piece && `${colour} ${PIECE_NAMES[piece.toLowerCase()]}`, aim && 'move here'].filter(Boolean).join(', ')
}

/** A UCI engine's look at a position, from one of its info lines: how deep it looked, its score from the side
 * to move's view (centipawns, or moves to a forced mate: minus, the side to move is mated), and the line it
 * expects (moves in UCI, "e2e4"). */
export interface EngineLook { depth: number; cp?: number; mate?: number; pv: string[] }

/** An engine's info line read ("info depth 18 seldepth 24 multipv 1 score cp 34 nodes … pv e2e4 e7e5"), or null
 * for one with no score (the move it's on, a string), a bound mid-search (lowerbound, upperbound: not yet its
 * score), or a line other than its first (multipv). */
export function engineLook(line: string): EngineLook | null {
  const words = line.trim().split(/\s+/)
  if (words[0] !== 'info' || words[1] === 'string' || words.includes('lowerbound') || words.includes('upperbound')) return null
  const after = (word: string) => { const i = words.indexOf(word); return i < 0 ? undefined : words[i + 1] }
  const score = words.indexOf('score')
  const kind = words[score + 1]
  const value = Number(words[score + 2])
  if (score < 0 || (kind !== 'cp' && kind !== 'mate') || !Number.isInteger(value) || (after('multipv') ?? '1') !== '1') return null
  const pv = words.indexOf('pv')
  return { depth: Number(after('depth')) || 0, [kind]: value, pv: pv < 0 ? [] : words.slice(pv + 1) }
}

/** An engine's look as the bar draws it (ChessEval: from White's side), at the position it looked at: the
 * engine's score is the side to move's, so on Black's turn it turns round. */
export function lookFromWhite(look: EngineLook, fen: string): ChessEval {
  const sign = fen.split(' ')[1] === 'b' ? -1 : 1
  return look.mate != null ? { mate: sign * look.mate || 0, depth: look.depth, fen } : { cp: sign * (look.cp ?? 0) || 0, depth: look.depth, fen }
}

/** Moves in SAN from a position, numbered as a game's are: "12. Nf3 Nc6 13. Bb5", or "12... Nc6 13. Bb5" from
 * Black's turn. */
export function numbered(sans: string[], fen: string): string {
  const [, turn, , , , full] = fen.split(' ')
  let n = Number(full) || 1
  let black = turn === 'b'
  return sans.map((san, i) => {
    const said = black ? (i === 0 ? `${n}... ${san}` : san) : `${n}. ${san}`
    if (black) n++
    black = !black
    return said
  }).join(' ')
}

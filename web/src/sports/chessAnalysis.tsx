// A fan's analysis board on a chess game's match page (Play from here): the board made playable where it is, from
// the game's position (or a finished game's move), with an engine of their own in the browser (chessEngine.ts)
// filling the bar. Loaded only when a fan opens one: it brings chess.js (facts/chessLine.ts). Nothing the fan
// plays leaves the device, so it never reaches an alert.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import type { LiveStatus } from '../api'
import { BackIcon, ChevronIcon, CloseIcon } from '../components/Icons'
import { EvalBar } from './chess'
import { startEngine, type Engine } from './chessEngine'
import { PIECES, position, type ChessEval, type ChessState } from './facts/chess'
import { checked, ended, fenAt, lineFrom, lineOfGame, lookFromWhite, numbered, PIECE_NAMES, playable, played, samePosition, sanLine, stepped, targets, type ChessLine } from './facts/chessLine'

/** Where an analysis starts: the game's position when the fan opened it (`base`, with the move that led there),
 * or a finished game's moves (SAN, from its start) at a ply of theirs. `asked` changes with each new start, the
 * board open or not; `focus` puts the keyboard on the board (opened by its button, not from the moves). */
export interface Opening { base: string; last?: string | null; moves?: string[] | null; ply?: number; asked: number; focus?: boolean }

const PROMOTIONS = ['q', 'r', 'b', 'n']
const KEYS = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -8, ArrowDown: 8 }  // along the board as it's drawn

function begin(opening: Opening): ChessLine | null {
  if (opening.moves) {
    const game = lineOfGame(opening.moves, opening.ply ?? 0)
    if (game) return game
  }
  return playable(opening.base) ? lineFrom(opening.base) : null
}

/** The analysis board: the fan's line (ChessLine) on the board, played by tap then tap (or a drag with a mouse),
 * the legal squares of the picked piece marked; a promotion asks what the pawn becomes. Back and forward step
 * along the line, and Back to live leaves (`leave`), the engine with it. `state` is the game's, live: when it
 * moves on, their line stays and a line says so. `flip`: the board's turn, the fan's Switch sides. */
export default function ChessAnalysis({ state, live, flip, opening, leave }: { state: ChessState; live: LiveStatus; flip: boolean; opening: Opening; leave: () => void }) {
  const [line, setLine] = useState(() => begin(opening))
  const [asked, setAsked] = useState(opening.asked)
  const [picked, setPicked] = useState<string | null>(null)
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)  // the square the keyboard is on
  const [ghost, setGhost] = useState<{ piece: string; x: number; y: number } | null>(null)  // a piece being dragged
  const [engine, setEngine] = useState<{ loaded: number; ready: boolean; failed: boolean }>({ loaded: 0, ready: false, failed: false })
  const [look, setLook] = useState<{ fen: string; ev: ChessEval; pv: string[] } | null>(null)
  const worker = useRef<Engine | null>(null)
  const board = useRef<HTMLDivElement>(null)
  const dragged = useRef(false)  // a drag just played (or dropped): the click it ends with isn't a tap

  // A new start (another of the game's moves tapped) while the board is open: its line, the engine kept.
  if (asked !== opening.asked) {
    setAsked(opening.asked)
    setLine(begin(opening))
    setPicked(null)
    setPromoting(null)
  }
  const fen = line ? fenAt(line) : null
  // Of the position, not of each of the engine's looks at it.
  const over = useMemo(() => (fen ? ended(fen) : null), [fen])
  const check = useMemo(() => (fen ? checked(fen) : null), [fen])

  // The engine: one for the board while it's open, ended when the fan leaves it (or the page).
  useEffect(() => {
    const started = startEngine({
      loading: (share) => setEngine((e) => ({ ...e, loaded: share })),
      ready: () => setEngine((e) => ({ ...e, ready: true })),
      failed: () => setEngine((e) => ({ ...e, failed: true })),
    })
    worker.current = started
    return () => {
      started.quit()
      worker.current = null
    }
  }, [])
  // Each position the board comes to, looked at afresh (the last search stops); an ended one needs no look.
  useEffect(() => {
    if (fen && !over) worker.current?.look(fen, (seen) => setLook({ fen, ev: lookFromWhite(seen, fen), pv: seen.pv }))
  }, [fen, over])
  useEffect(() => {
    if (opening.focus) board.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus()
  }, [opening.focus])
  useEffect(() => {
    if (promoting) board.current?.querySelector<HTMLElement>('.chess-promote button')?.focus()
  }, [promoting])

  const best = useMemo(() => (look && look.fen === fen && look.pv.length ? numbered(sanLine(look.fen, look.pv, 5), look.fen) : null), [look, fen])
  if (!line || !fen) {
    return (
      <div className="chess-play">
        <div className="chess-under">
          <span className="small">This position can't be played from here.</span>
          <button type="button" className="btn small" onClick={leave}>Back to the game</button>
        </div>
      </div>
    )
  }

  const turn = fen.split(' ')[1] === 'b' ? 'b' : 'w'
  const mine = (piece: string | null): piece is string => piece != null && (piece === piece.toUpperCase()) === (turn === 'w')
  const last = line.at ? line.plies[line.at - 1] : null
  const { ranks, moved } = position({ ...state, fen, last_move: last ? last.from + last.to : samePosition(fen, opening.base) ? opening.last : null }, live, flip)
  const squares = ranks.flat()
  const aims = new Map<string, boolean>(picked ? targets(fen, picked).map((t) => [t.to, t.promotes] as const) : [])
  const stop = cursor ?? picked ?? squares[0].name  // the one square the Tab key stops on
  // The bar: the engine's look at this position, the last one's until it has one, and an ended game's result.
  const ev: ChessEval | null = over ? (over === 'Checkmate' ? { mate: 0, fen } : { cp: 0, fen }) : look?.fen === fen ? look.ev : look?.ev ?? null

  const go = (from: string, to: string, promotes: boolean) => {
    if (promotes) return setPromoting({ from, to })
    const next = played(line, from, to)
    if (next) setLine(next)
    setPicked(null)
  }
  const tap = (name: string, piece: string | null) => {
    if (dragged.current || promoting) return
    const aim = aims.get(name)
    if (picked && aim != null) return go(picked, name, aim)
    setPicked(mine(piece) && picked !== name ? name : null)
  }
  const promote = (piece: string) => {
    const next = promoting && played(line, promoting.from, promoting.to, piece)
    if (next) setLine(next)
    setPromoting(null)
    setPicked(null)
  }
  const step = (by: number) => {
    setLine(stepped(line, by))
    setPicked(null)
    setPromoting(null)
  }
  // A drag, with a mouse or a pen (a finger taps: a drag there would take the page's scrolling): the piece follows
  // the pointer and drops on a legal square; let go where it started, it's a tap.
  const drag = (e: PointerEvent, name: string, piece: string | null) => {
    if (e.pointerType === 'touch' || e.button !== 0 || !mine(piece) || promoting) return
    const x0 = e.clientX, y0 = e.clientY
    let on = false
    const move = (m: globalThis.PointerEvent) => {
      if (!on && Math.hypot(m.clientX - x0, m.clientY - y0) < 6) return
      if (!on) setPicked(name)
      on = true
      setGhost({ piece, x: m.clientX, y: m.clientY })
    }
    const up = (u: globalThis.PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
      setGhost(null)
      if (!on) return
      dragged.current = true
      window.setTimeout(() => { dragged.current = false })
      const to = document.elementFromPoint(u.clientX, u.clientY)?.closest<HTMLElement>('[data-sq]')?.dataset.sq
      const aim = to ? targets(fen, name).find((t) => t.to === to) : undefined
      if (aim) go(name, aim.to, aim.promotes)
      else setPicked(null)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }
  // The arrow keys move the keyboard along the board as it's drawn; Escape lets go of the piece.
  const keys = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      setPicked(null)
      setPromoting(null)
      return
    }
    if (promoting) return  // the keys stay on the pieces to promote to
    const by = (KEYS as Record<string, number | undefined>)[e.key]
    const at = squares.findIndex((s) => s.name === stop)
    if (by == null || at < 0 || (Math.abs(by) === 1 && Math.floor((at + by) / 8) !== Math.floor(at / 8)) || at + by < 0 || at + by > 63) return
    e.preventDefault()
    const name = squares[at + by].name
    setCursor(name)
    board.current?.querySelector<HTMLElement>(`[data-sq="${name}"]`)?.focus()
  }

  const colour = (piece: string) => (piece === piece.toUpperCase() ? 'white' : 'black')
  const words = (name: string, piece: string | null) => [name, piece && `${colour(piece)} ${PIECE_NAMES[piece.toLowerCase()]}`, aims.has(name) && 'move here'].filter(Boolean).join(', ')
  const side = turn === 'w' ? 'White' : 'Black'
  const before = line.at > 1 ? line.plies[line.at - 2].fen : line.start
  const where = last ? `After ${numbered([last.san], before)}` : samePosition(fen, opening.base) ? "The game's position" : 'The starting position'
  const movedOn = !samePosition(state.fen, opening.base)
  const engineWords = over ?? (engine.failed ? "The engine couldn't start in this browser; the moves still play."
    : best ? `Best: ${best}${look?.ev.depth ? ` · depth ${look.ev.depth}` : ''}`
      : !engine.ready ? `Loading the engine…${engine.loaded > 0 && engine.loaded < 1 ? ` ${Math.round(engine.loaded * 100)}%` : ''}` : 'The engine is looking…')

  return (
    <div className="chess-play eval analysis">
      <EvalBar ev={ev} flip={flip} quiet />
      <div ref={board} className="chessboard playable" role="group" aria-label={`Analysis board, ${side} to move`} onKeyDown={keys}>
        {squares.map((sq) => (
          <button
            key={sq.name}
            type="button"
            data-sq={sq.name}
            tabIndex={sq.name === stop ? 0 : -1}
            className={`sq ${sq.dark ? 'dark' : 'light'}${moved.has(sq.name) ? ' moved' : ''}${picked === sq.name ? ' picked' : ''}${aims.has(sq.name) ? (sq.piece ? ' aim take' : ' aim') : ''}${check === sq.name ? ' check' : ''}`}
            aria-label={words(sq.name, sq.piece)}
            aria-pressed={picked === sq.name}
            onClick={() => tap(sq.name, sq.piece)}
            onPointerDown={(e) => drag(e, sq.name, sq.piece)}
            onFocus={() => setCursor(sq.name)}
          >
            {sq.piece && <span className={`piece ${sq.piece === sq.piece.toUpperCase() ? 'w' : 'b'}${ghost && picked === sq.name ? ' lifted' : ''}`}>{`${PIECES[sq.piece.toLowerCase()]}︎`}</span>}
          </button>
        ))}
        {promoting && (
          <div className="chess-promote" role="group" aria-label="Promote the pawn to">
            {PROMOTIONS.map((p) => (
              <button key={p} type="button" aria-label={PIECE_NAMES[p]} title={PIECE_NAMES[p]} onClick={() => promote(p)}>
                <span className={`piece ${turn}`}>{`${PIECES[p]}︎`}</span>
              </button>
            ))}
            <button type="button" aria-label="Cancel" title="Cancel" onClick={() => { setPromoting(null); setPicked(null) }}><CloseIcon width={18} height={18} /></button>
          </div>
        )}
      </div>
      <div className="chess-under stack">
        <span className="row chess-steps">
          <button type="button" className="btn icon chess-step" aria-label="Back a move" title="Back a move" aria-disabled={!line.at} onClick={() => line.at && step(-1)}>
            <BackIcon width={18} height={18} />
          </button>
          <button type="button" className="btn icon chess-step" aria-label="Forward a move" title="Forward a move" aria-disabled={line.at === line.plies.length} onClick={() => line.at < line.plies.length && step(1)}>
            <ChevronIcon width={18} height={18} />
          </button>
          <button type="button" className={`btn small${movedOn ? ' primary' : ''}`} onClick={leave}>{state.result ? 'Back to the game' : 'Back to live'}</button>
        </span>
        <span className="small" role="status">{where} · {side} to move</span>
        {movedOn && <span className="small chess-moved" role="status">{state.result ? `The game is over: ${state.result.replace('-', '–')}` : `The game has moved on${state.move ? ` · move ${state.move}` : ''}`}</span>}
        <span className="tiny">{engineWords}</span>
        <span className="tiny muted">
          Engine: Stockfish 18 (<a href="/engine/Copying.txt" target="_blank" rel="noreferrer" className="link">GPL-3.0</a>) · <a href="https://github.com/nmrugg/stockfish.js" target="_blank" rel="noreferrer" className="link">source</a>
        </span>
      </div>
      {ghost && createPortal(<span className={`chess-ghost piece ${ghost.piece === ghost.piece.toUpperCase() ? 'w' : 'b'}`} style={{ left: ghost.x, top: ghost.y }} aria-hidden="true">{`${PIECES[ghost.piece.toLowerCase()]}︎`}</span>, document.body)}
    </div>
  )
}

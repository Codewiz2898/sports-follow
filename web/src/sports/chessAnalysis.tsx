// A fan's analysis board on a chess game's match page (Play from here): the board made playable where it is, from
// the game's position (or a finished game's move), with an engine of their own in the browser (chessEngine.ts)
// filling the bar. Loaded only when a fan opens one: it brings chess.js (facts/chessLine.ts). Nothing the fan
// plays leaves the device, so it never reaches an alert.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { LiveStatus } from '../api'
import { BackIcon, ChevronIcon, CloseIcon } from '../components/Icons'
import { EvalBar } from './chess'
import { ENGINE_LICENCE, ENGINE_SOURCE, startEngine, type Engine } from './chessEngine'
import { PIECES, position, type ChessEval, type ChessState } from './facts/chess'
import { checked, ended, fenAt, lineWhere, lookFromWhite, movedOn, numbered, openingLine, PIECE_NAMES, played, samePosition, sanLine, squareWords, stepped, targets, type Opening } from './facts/chessLine'

const PROMOTIONS = ['q', 'r', 'b', 'n']
const KEYS = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -8, ArrowDown: 8 }  // along the board as it's drawn

/** The analysis board: the fan's line (ChessLine) on the board, played by tap then tap, or a drag, the legal
 * squares of the picked piece marked; a promotion asks what the pawn becomes. Back and forward step
 * along the line, and Back to live leaves (`leave`), the engine with it. `state` is the game's, live: when it
 * moves on, their line stays and a line says so. `flip`: the board's turn, the fan's Switch sides. `still`: the
 * game's own board with something under it, for a position that can't be played. */
export default function ChessAnalysis({ state, live, flip, opening, leave, still }: { state: ChessState; live: LiveStatus; flip: boolean; opening: Opening; leave: () => void; still: (under: ReactNode) => ReactNode }) {
  const [line, setLine] = useState(() => openingLine(opening))
  const [asked, setAsked] = useState(opening.asked)
  const [picked, setPicked] = useState<string | null>(null)
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)  // the square the keyboard is on
  const [ghost, setGhost] = useState<{ piece: string; x: number; y: number; touch: boolean } | null>(null)  // a piece being dragged
  const [engine, setEngine] = useState<{ loaded: number; ready: boolean; failed: boolean }>({ loaded: 0, ready: false, failed: false })
  const [look, setLook] = useState<{ fen: string; ev: ChessEval; pv: string[] } | null>(null)
  const worker = useRef<Engine | null>(null)
  const board = useRef<HTMLDivElement>(null)
  const dragged = useRef(false)  // a drag just played (or dropped): the click it ends with isn't a tap
  const refocus = useRef(false)  // the keyboard was on what's gone (a promotion's pieces): back to the board

  // A new start (another of the game's moves tapped) while the board is open: its line, the engine kept.
  if (asked !== opening.asked) {
    setAsked(opening.asked)
    setLine(openingLine(opening))
    setPicked(null)
    setPromoting(null)
  }
  const fen = line ? fenAt(line) : null
  // Of the position, not of each of the engine's looks at it.
  const over = useMemo(() => (fen ? ended(fen) : null), [fen])
  const check = useMemo(() => (fen ? checked(fen) : null), [fen])

  // The engine: one for the board while it's open, ended when the fan leaves it (or the page); none, and nothing
  // downloaded, for a board that can't be played.
  const plays = line != null
  useEffect(() => {
    if (!plays) return
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
  }, [plays])
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
  useEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    board.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus()
  })

  const best = useMemo(() => (look && look.fen === fen && look.pv.length ? numbered(sanLine(look.fen, look.pv, 5), look.fen) : null), [look, fen])
  if (!line || !fen) {
    return still(
      <div className="chess-under row" role="status">
        <span className="small">This position can't be played from here</span>
        <button type="button" className="btn small" onClick={leave}>Back to the game</button>
      </div>,
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
  // A promotion chosen, or let go (null): the keyboard goes back to the board, on the pawn's new square or its old.
  const promote = (piece: string | null) => {
    const next = promoting && piece ? played(line, promoting.from, promoting.to, piece) : null
    if (next) setLine(next)
    if (promoting && board.current?.contains(document.activeElement)) {
      setCursor(next ? promoting.to : promoting.from)
      refocus.current = true
    }
    setPromoting(null)
    setPicked(null)
  }
  const step = (by: number) => {
    setLine(stepped(line, by))
    setPicked(null)
    setPromoting(null)
  }
  // A drag: the piece follows the pointer and drops on a legal square; let go where it started, it's a tap. A
  // finger drags only from a piece of the side to move, the squares that keep it (touch-action: none, .mine): a
  // swipe from anywhere else on the board still scrolls the page. A finger's piece floats over it, to be seen.
  const drag = (e: PointerEvent, name: string, piece: string | null) => {
    if (e.button !== 0 || !mine(piece) || promoting) return
    const touch = e.pointerType === 'touch'
    const x0 = e.clientX, y0 = e.clientY
    let on = false
    const move = (m: globalThis.PointerEvent) => {
      if (!on && Math.hypot(m.clientX - x0, m.clientY - y0) < 6) return
      if (!on) setPicked(name)
      on = true
      setGhost({ piece, x: m.clientX, y: m.clientY, touch })
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
      if (promoting) return promote(null)
      setPicked(null)
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

  const side = turn === 'w' ? 'White' : 'Black'
  const where = lineWhere(line, opening.base)
  const onward = movedOn(state, opening.base)
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
            className={`sq ${sq.dark ? 'dark' : 'light'}${moved.has(sq.name) ? ' moved' : ''}${picked === sq.name ? ' picked' : ''}${aims.has(sq.name) ? (sq.piece ? ' aim take' : ' aim') : ''}${check === sq.name ? ' check' : ''}${mine(sq.piece) ? ' mine' : ''}`}
            aria-label={squareWords(sq.name, sq.piece, aims.has(sq.name))}
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
            <button type="button" aria-label="Cancel" title="Cancel" onClick={() => promote(null)}><CloseIcon width={18} height={18} /></button>
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
          <button type="button" className={`btn small${onward ? ' primary' : ''}`} onClick={leave}>{state.result ? 'Back to the game' : 'Back to live'}</button>
        </span>
        <span className="small" role="status">{where} · {side} to move</span>
        {onward && <span className="small chess-moved" role="status">{onward}</span>}
        <span className="tiny">{engineWords}</span>
        <span className="tiny muted">
          Engine: Stockfish 18 (<a href={ENGINE_LICENCE} target="_blank" rel="noreferrer" className="link">GPL-3.0</a>) · <a href={ENGINE_SOURCE} target="_blank" rel="noreferrer" className="link">source</a>
        </span>
      </div>
      {ghost && createPortal(<span className={`chess-ghost piece ${ghost.piece === ghost.piece.toUpperCase() ? 'w' : 'b'}${ghost.touch ? ' touch' : ''}`} style={{ left: ghost.x, top: ghost.y }} aria-hidden="true">{`${PIECES[ghost.piece.toLowerCase()]}︎`}</span>, document.body)}
    </div>
  )
}

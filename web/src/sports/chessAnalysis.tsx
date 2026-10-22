// A fan's analysis board on a chess game's match page (Play from here): a board of its own over the page, as big
// as the screen lets it be, from the game's position (or a finished game's move), with an engine of their own in
// the browser (chessEngine.ts) filling the bar and showing its best lines. Loaded only when a fan opens one: it
// brings chess.js (facts/chessLine.ts). Nothing the fan plays leaves the device, so it never reaches an alert.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { createPortal } from 'react-dom'
import type { LiveStatus } from '../api'
import { BackIcon, ChevronIcon, CloseIcon, FirstIcon, LastIcon, RefreshIcon, SwapIcon } from '../components/Icons'
import { EvalBar } from './chess'
import { ENGINE_LICENCE, ENGINE_SOURCE, startEngine, type Engine } from './chessEngine'
import { chess as facts, evalScore, PIECES, position, standardCastling, whiteShare, type ChessEval, type ChessState } from './facts/chess'
import { checked, ended, fenAt, lineFrom, lineWhere, lookFromWhite, movedOn, numberedMoves, openingLine, PIECE_NAMES, played, playable, playedOn, samePosition, sanLine, squareWords, stepped, targets, type EngineLook, type Opening } from './facts/chessLine'

const PROMOTIONS = ['q', 'r', 'b', 'n']
const KEYS = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -8, ArrowDown: 8 }  // along the board as it's drawn
// The engine's best lines it shows, each a tap from being played; more would take it longer to reach its depth.
const LINES = 3
// The moves of each line it shows.
const LINE_MOVES = 8

/** The arrow of the engine's best move (UCI, "e2e4") over the board as it's drawn: from the middle of the
 * square it leaves to the middle of the one it goes to, on a board of eight by eight. */
function BestArrow({ move, flip }: { move: string; flip: boolean }) {
  const at = (square: string) => {
    const file = 'abcdefgh'.indexOf(square[0])
    const rank = Number(square[1])
    return { x: (flip ? 7 - file : file) + 0.5, y: (flip ? rank - 1 : 8 - rank) + 0.5 }
  }
  const from = at(move.slice(0, 2))
  const to = at(move.slice(2, 4))
  const length = Math.hypot(to.x - from.x, to.y - from.y)
  if (!length) return null
  // The shaft stops short of the square's middle, where the head's point is.
  const end = { x: to.x - ((to.x - from.x) / length) * 0.32, y: to.y - ((to.y - from.y) / length) * 0.32 }
  return (
    <svg className="chess-arrow" viewBox="0 0 8 8" aria-hidden="true">
      <defs>
        <marker id="chess-arrow-head" viewBox="0 0 4 4" refX="1.2" refY="2" markerWidth="2.6" markerHeight="2.6" orient="auto">
          <path d="M0 0 4 2 0 4z" />
        </marker>
      </defs>
      <line x1={from.x} y1={from.y} x2={end.x} y2={end.y} markerEnd="url(#chess-arrow-head)" />
    </svg>
  )
}

/** The analysis board over the page (a modal dialog): the fan's line (ChessLine) on the board, played by tap
 * then tap, or a drag, the legal squares of the picked piece marked; a promotion asks what the pawn becomes.
 * Beside it (under it on a phone): where the line is, the engine's best lines (each move a tap from being played
 * to), the fan's own moves (each a tap from being gone back to), and its controls: the steps along the line,
 * Flip, Start over, the engine and its arrow on or off. Escape, a tap outside it or Back to live closes it
 * (`leave`), the engine with it. `state` is the game's, live: when it moves on, the fan's line stays, a line
 * says so and offers its new position. `flip`: the game's board's side, the one the analysis starts from. */
export default function ChessAnalysis({ state, live, flip, opening, leave }: { state: ChessState; live: LiveStatus; flip: boolean; opening: Opening; leave: () => void }) {
  const [line, setLine] = useState(() => openingLine(opening))
  const [base, setBase] = useState({ fen: opening.base, last: opening.last ?? null })
  const [turned, setTurned] = useState(flip)
  const [engineOn, setEngineOn] = useState(true)
  const [arrowOn, setArrowOn] = useState(true)
  const [picked, setPicked] = useState<string | null>(null)
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)  // the square the keyboard is on
  const [ghost, setGhost] = useState<{ piece: string; x: number; y: number; size: number; touch: boolean } | null>(null)  // a piece being dragged
  const [engine, setEngine] = useState<{ loaded: number; ready: boolean; failed: boolean }>({ loaded: 0, ready: false, failed: false })
  // The engine's lines at a position, by their number (1, its best), each its latest look.
  const [looks, setLooks] = useState<{ fen: string; lines: EngineLook[] } | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const worker = useRef<Engine | null>(null)
  const board = useRef<HTMLDivElement>(null)
  const ours = useRef<HTMLOListElement>(null)
  const dragged = useRef(false)  // a drag just played (or dropped): the click it ends with isn't a tap
  const refocus = useRef(false)  // the keyboard was on what's gone (a promotion's pieces): back to the board
  const outside = useRef(false)  // a press that began on the backdrop (outside the dialog): its click closes
  // The fan moves along the board's squares by keyboard: they came onto it with Tab, or picked a piece there
  // with Enter or Space. Until then (a mouse's or a finger's fan, or the board just opened) the arrow keys
  // step along the line there too; a press of the pointer ends it.
  const keyed = useRef(false)
  // Its keys (shortcuts, below), heard wherever the keyboard is while it's open: a tapped button that goes leaves
  // it on the page's body, under the dialog.
  const heard = useRef<(e: globalThis.KeyboardEvent) => void>(() => {})

  const fen = line ? fenAt(line) : null
  // Of the position, not of each of the engine's looks at it.
  const over = useMemo(() => (fen ? ended(fen) : null), [fen])
  const check = useMemo(() => (fen ? checked(fen) : null), [fen])

  // Over the page, the rest of it out of reach until it closes; the keyboard on the board when its button opened it.
  useEffect(() => {
    const d = dialog.current
    if (d && !d.open) d.showModal()
    if (opening.focus) board.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus()
  }, [opening.focus])
  // The engine: one for the board while it's open, ended when the fan leaves it (or the page); none, and nothing
  // downloaded, for a board that can't be played.
  const plays = line != null
  useEffect(() => {
    if (!plays) return
    const started = startEngine({
      loading: (share) => setEngine((e) => ({ ...e, loaded: share })),
      ready: () => setEngine((e) => ({ ...e, ready: true })),
      failed: () => setEngine((e) => ({ ...e, failed: true })),
    }, LINES)
    worker.current = started
    return () => {
      started.quit()
      worker.current = null
    }
  }, [plays])
  // Each position the board comes to, looked at afresh (the last search stops); an ended one needs no look, nor
  // does any with the engine off.
  useEffect(() => {
    if (!fen || over || !engineOn) {
      worker.current?.stop()
      return
    }
    worker.current?.look(fen, (seen) => setLooks((was) => {
      const lines = was?.fen === fen ? [...was.lines] : []
      lines[seen.line - 1] = seen
      return { fen, lines }
    }))
  }, [fen, over, engineOn])
  useEffect(() => {
    if (promoting) board.current?.querySelector<HTMLElement>('.chess-promote button')?.focus()
  }, [promoting])
  useEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    board.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus()
  })
  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => heard.current(e)
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [])
  // The move the board is at, kept in sight in the list: the list scrolls, not the dialog, so the board stays put.
  useEffect(() => {
    const list = ours.current
    const item = list?.querySelector<HTMLElement>('[aria-current="true"]')
    if (!list || !item) return
    const top = item.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop
    if (top < list.scrollTop || top + item.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top - (list.clientHeight - item.offsetHeight) / 2
  }, [line?.at, line?.plies.length])

  // A press on the backdrop: the dialog's own event, outside its box (not its scrollbar, nor a full screen's
  // space under its content).
  const onBackdrop = (e: { target: EventTarget; clientX: number; clientY: number }) => {
    const d = dialog.current
    if (!d || e.target !== d) return false
    const r = d.getBoundingClientRect()
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom
  }

  // The engine's lines at this position, best first, in SAN, each with its score from White's side.
  const shown = useMemo(() => {
    if (!fen || !looks || looks.fen !== fen) return []
    return looks.lines.filter(Boolean).map((look) => ({ look, ev: lookFromWhite(look, fen), moves: numberedMoves(sanLine(fen, look.pv, LINE_MOVES), fen) }))
  }, [looks, fen])


  if (!line || !fen) {
    return createPortal(
      <dialog ref={dialog} className="chess-study narrow" data-sport={facts.key} aria-label="Analysis board" onCancel={(e) => { e.preventDefault(); leave() }} onClose={leave}>
        <div className="chess-study-box stack" role="status">
          <span className="title">This position can't be played from here</span>
          <button type="button" className="btn small primary" style={{ alignSelf: 'flex-start' }} onClick={leave}>Back to the game</button>
        </div>
      </dialog>,
      document.body,
    )
  }

  const turn = fen.split(' ')[1] === 'b' ? 'b' : 'w'
  const mine = (piece: string | null): piece is string => piece != null && (piece === piece.toUpperCase()) === (turn === 'w')
  const last = line.at ? line.plies[line.at - 1] : null
  const { ranks, moved } = position({ ...state, fen, last_move: last ? last.from + last.to : samePosition(fen, base.fen) ? base.last : null }, live, turned)
  const squares = ranks.flat()
  const aims = new Map<string, boolean>(picked ? targets(fen, picked).map((t) => [t.to, t.promotes] as const) : [])
  const stop = cursor ?? picked ?? squares[0].name  // the one square the Tab key stops on
  // The bar: the engine's look at this position, the last one's until it has one, and an ended game's result;
  // none with the engine off.
  const best = shown[0]
  const lastLook = looks?.lines[0]
  const ev: ChessEval | null = over ? (over === 'Checkmate' ? { mate: 0, fen } : { cp: 0, fen }) : best ? best.ev : looks && lastLook ? lookFromWhite(lastLook, looks.fen) : null

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
  function promote(piece: string | null) {
    const next = line && promoting && piece ? played(line, promoting.from, promoting.to, piece) : null
    if (next) setLine(next)
    if (promoting && board.current?.contains(document.activeElement)) {
      setCursor(next ? promoting.to : promoting.from)
      refocus.current = true
    }
    setPromoting(null)
    setPicked(null)
  }
  // The board somewhere else along the line (or a new line): what was picked is let go.
  const goTo = (next: NonNullable<typeof line>) => {
    setLine(next)
    setPicked(null)
    setPromoting(null)
  }
  const step = (by: number) => goTo(stepped(line, by))
  const flipBoard = () => setTurned((t) => !t)
  // A drag: the piece follows the pointer and drops on a legal square; let go where it started, it's a tap. A
  // finger drags only from a piece of the side to move, the squares that keep it (touch-action: none, .mine): a
  // swipe from anywhere else on the board still scrolls. A finger's piece floats over it, bigger, to be seen.
  const drag = (e: PointerEvent, name: string, piece: string | null) => {
    if (e.button !== 0 || !mine(piece) || promoting) return
    const touch = e.pointerType === 'touch'
    const size = (board.current?.getBoundingClientRect().width ?? 320) / 8
    const x0 = e.clientX, y0 = e.clientY
    let on = false
    const move = (m: globalThis.PointerEvent) => {
      if (!on && Math.hypot(m.clientX - x0, m.clientY - y0) < 6) return
      if (!on) setPicked(name)
      on = true
      setGhost({ piece, x: m.clientX, y: m.clientY, size, touch })
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
  // On the board, the arrow keys move the keyboard along it as it's drawn, once the fan moves on it by keyboard.
  const keys = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') keyed.current = true
    if (promoting || e.key === 'Escape' || !keyed.current) return  // the keys stay on the pieces to promote to; Escape is the dialog's
    const by = (KEYS as Record<string, number | undefined>)[e.key]
    const at = squares.findIndex((s) => s.name === stop)
    if (by == null || at < 0 || (Math.abs(by) === 1 && Math.floor((at + by) / 8) !== Math.floor(at / 8)) || at + by < 0 || at + by > 63) return
    e.preventDefault()
    const name = squares[at + by].name
    setCursor(name)
    board.current?.querySelector<HTMLElement>(`[data-sq="${name}"]`)?.focus()
  }
  // Anywhere but a board the fan moves on by keyboard: left and right step along the line, Home and End go to its
  // ends, F flips the board.
  heard.current = (e) => {
    if (e.key === 'Tab') keyed.current = true
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || promoting || (keyed.current && board.current?.contains(e.target as Node))) return
    const to = e.key === 'ArrowLeft' ? line.at - 1 : e.key === 'ArrowRight' ? line.at + 1 : e.key === 'Home' ? 0 : e.key === 'End' ? line.plies.length : null
    if (to != null) {
      e.preventDefault()
      goTo(stepped(line, to - line.at))
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault()
      flipBoard()
    }
  }

  const side = turn === 'w' ? 'White' : 'Black'
  const where = lineWhere(line, base.fen)
  const onward = movedOn(state, base.fen)
  // The game's position now, for a fan whose line began before it: one that can be played here.
  const now = onward && state.fen && playable(state.fen) && standardCastling(state.fen) ? state.fen : null
  const fromNow = () => {
    if (!now) return
    setBase({ fen: now, last: state.last_move ?? null })
    goTo(lineFrom(now))
  }
  const engineWords = over ?? (!engineOn ? 'The engine is off.'
    : engine.failed ? "The engine couldn't start in this browser; the moves still play."
      : !engine.ready ? `Loading the engine…${engine.loaded > 0 && engine.loaded < 1 ? ` ${Math.round(engine.loaded * 100)}%` : ''}`
        : !best ? 'The engine is looking…' : null)
  const fans = numberedMoves(line.plies.map((p) => p.san), line.start)
  const names = [state.white?.name, state.black?.name].filter(Boolean).join(' v ')
  const arrow = engineOn && arrowOn && !over && best?.look.pv[0]

  // Escape lets go of a promotion or a picked piece first, then closes; so does a tap outside the box.
  return createPortal(
    <dialog
      ref={dialog}
      className="chess-study"
      data-sport={facts.key}
      aria-label="Analysis board"
      onCancel={(e) => {
        e.preventDefault()
        if (promoting) promote(null)
        else if (picked) setPicked(null)
        else leave()
      }}
      onClose={leave}
      onPointerDown={(e) => {
        outside.current = onBackdrop(e)
        keyed.current = false
      }}
      onClick={(e) => { if (outside.current && onBackdrop(e)) leave() }}
    >
      <div className="chess-study-box">
        <div className="row chess-study-head">
          <span className="stack" style={{ minWidth: 0 }}>
            <span className="eyebrow">Play from here</span>
            <span className="title">{names}</span>
          </span>
          <button type="button" className={`btn small${onward ? ' primary' : ''}`} onClick={leave}>
            <CloseIcon width={16} height={16} />{state.result ? 'Back to the game' : 'Back to live'}
          </button>
        </div>
        <div className="chess-study-main">
          <div className="chess-study-left">
            <div className={`chess-play analysis${engineOn ? ' eval' : ''}`}>
              {engineOn && <EvalBar ev={ev} flip={turned} quiet />}
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
                {arrow && <BestArrow move={arrow} flip={turned} />}
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
            </div>
            <div className="row chess-steps">
              <span className="row" style={{ gap: 6 }}>
                <button type="button" className="btn icon chess-step" aria-label="To the start" title="To the start (Home)" aria-disabled={!line.at} onClick={() => line.at && goTo(stepped(line, -line.at))}>
                  <FirstIcon width={18} height={18} />
                </button>
                <button type="button" className="btn icon chess-step" aria-label="Back a move" title="Back a move (←)" aria-disabled={!line.at} onClick={() => line.at && step(-1)}>
                  <BackIcon width={18} height={18} />
                </button>
                <button type="button" className="btn icon chess-step" aria-label="Forward a move" title="Forward a move (→)" aria-disabled={line.at === line.plies.length} onClick={() => line.at < line.plies.length && step(1)}>
                  <ChevronIcon width={18} height={18} />
                </button>
                <button type="button" className="btn icon chess-step" aria-label="To the end" title="To the end (End)" aria-disabled={line.at === line.plies.length} onClick={() => line.at < line.plies.length && goTo(stepped(line, line.plies.length - line.at))}>
                  <LastIcon width={18} height={18} />
                </button>
              </span>
              <span className="row" style={{ gap: 6 }}>
                <button type="button" className="btn icon chess-step" aria-label="Flip the board" title="Flip the board (F)" onClick={flipBoard}>
                  <SwapIcon width={18} height={18} />
                </button>
                <button type="button" className="btn icon chess-step" aria-label="Start over" title="Start over" onClick={() => { const fresh = openingLine({ ...opening, base: base.fen, moves: samePosition(base.fen, opening.base) ? opening.moves : null }); if (fresh) goTo(fresh) }}>
                  <RefreshIcon width={18} height={18} />
                </button>
              </span>
            </div>
          </div>
          <div className="chess-study-side">
            <div className="stack" style={{ gap: 6 }}>
              <span className="small" role="status"><strong>{where}</strong> · {side} to move</span>
              {onward && (
                <span className="row chess-moved-on">
                  <span className="small chess-moved" role="status">{onward}</span>
                  {now && <button type="button" className="btn small" onClick={fromNow}>{state.result ? 'Play from the end' : 'Play from there'}</button>}
                </span>
              )}
            </div>
            <section className="chess-study-part" aria-label="Engine">
              <span className="row">
                <span className="row" style={{ gap: 10, justifyContent: 'flex-start' }}>
                  <span className="eyebrow">Engine</span>
                  {engineOn && best && <span className="tiny muted">depth {best.look.depth}</span>}
                </span>
                <span className="row" style={{ gap: 6 }}>
                  {engineOn && (
                    <button type="button" className="btn small toggle" aria-pressed={arrowOn} onClick={() => setArrowOn((a) => !a)}>Arrow</button>
                  )}
                  <button type="button" className="btn small toggle" aria-label="Engine" aria-pressed={engineOn} onClick={() => setEngineOn((o) => !o)}>{engineOn ? 'On' : 'Off'}</button>
                </span>
              </span>
              {engineWords && <span className="small muted">{engineWords}</span>}
              {engineOn && shown.length > 0 && (
                <ol className="chess-pvs">
                  {shown.map(({ look, ev: score, moves }) => (
                    <li key={look.line} className="chess-pv">
                      <span className={`num chess-pv-score ${whiteShare(score) >= 50 ? 'white' : 'black'}`}>{evalScore(score)}</span>
                      <span className="chess-pv-moves">
                        {moves.map((m, i) => (
                          <button key={i} type="button" className="chess-move" title={`Play the line to ${m.said}`} aria-label={`Play the line to ${m.said}`} onClick={() => goTo(playedOn(line, look.pv.slice(0, i + 1)))}>
                            {m.number && <span className="muted">{m.number} </span>}{m.san}
                          </button>
                        ))}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
            <section className="chess-study-part" aria-label="Moves">
              <span className="eyebrow">Moves</span>
              {fans.length ? (
                <ol ref={ours} className="chess-line">
                  {fans.map((m, i) => (
                    <li key={i}>
                      {m.number && <span className="muted">{m.number}</span>}
                      <button type="button" className={`chess-move${i >= line.at ? ' ahead' : ''}`} aria-current={i + 1 === line.at} onClick={() => goTo({ ...line, at: i + 1 })}>{m.san}</button>
                    </li>
                  ))}
                </ol>
              ) : (
                <span className="small muted">Move a piece to start your line. The engine's moves above play with a tap.</span>
              )}
            </section>
            <span className="tiny muted">
              Engine: Stockfish 18 (<a href={ENGINE_LICENCE} target="_blank" rel="noreferrer" className="link">GPL-3.0</a>) · <a href={ENGINE_SOURCE} target="_blank" rel="noreferrer" className="link">source</a> · on your device
            </span>
          </div>
        </div>
      </div>
      {ghost && <span className={`chess-ghost piece ${ghost.piece === ghost.piece.toUpperCase() ? 'w' : 'b'}${ghost.touch ? ' touch' : ''}`} style={{ left: ghost.x, top: ghost.y, fontSize: ghost.size * (ghost.touch ? 1.15 : 0.86) }} aria-hidden="true">{`${PIECES[ghost.piece.toLowerCase()]}︎`}</span>}
    </dialog>,
    document.body,
  )
}

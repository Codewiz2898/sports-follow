import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { SwapIcon } from '../components/Icons'
import { usePwa } from '../pwa'
import './chess.css'
import type { Opening, replayedMoves } from './facts/chessLine'
import { chess as facts, chessMiddle, evalDepth, evalScore, evalWords, followedSide, movesAbout, PIECES, position, standardCastling, whiteShare, type ChessColour, type ChessEval, type ChessPage, type ChessSide, type ChessState } from './facts/chess'
import type { BoardProps, SportView } from './view'

// The analysis board (Play from here), with chess.js and the engine's worker: loaded when a fan opens one.
const ChessAnalysis = lazy(() => import('./chessAnalysis'))
// Where the keyboard is on something with arrow keys of its own (a field, a slider, a menu, a video's controls),
// the match page's board doesn't take them.
const OWN_ARROWS = 'input, textarea, select, audio, video, dialog, [role="slider"], [role="tablist"], [role="listbox"], [role="menu"], [role="menubar"], [role="radiogroup"], [role="grid"], [role="tree"]'

/** An evaluation bar: White's share (whiteShare) filling it from White's end, the bottom unless the board is
 * turned (flip), and the score (evalScore) at the end of the side ahead. Live, it's the server's evaluation, the
 * one the swing alerts go by; on an analysis board, the fan's own engine's, and none yet (null) while it loads.
 * A big board says under it how deep the engine looked (unless `quiet`: the board says it itself), a small one
 * in its title. Empty, it says why (`none`). */
export function EvalBar({ ev, flip, compact, quiet, none = 'not yet' }: { ev: ChessEval | null; flip: boolean; compact?: boolean; quiet?: boolean; none?: string }) {
  if (!ev) return <div className="chess-eval waiting" role="img" aria-label={`Engine evaluation: ${none}`} />
  const share = whiteShare(ev)
  const score = evalScore(ev)
  const white = share >= 50  // White ahead, or level
  const depth = evalDepth(ev)
  const words = evalWords(ev)
  return (
    <>
      <div className={`chess-eval${flip ? ' flip' : ''}`} role="img" aria-label={words} title={words}>
        <span className="fill" style={{ height: `${share}%` }} />
        <span className={`num score ${white ? 'white' : 'black'}${white === flip ? ' top' : ''}`}>{score}</span>
      </div>
      {!compact && !quiet && depth && <span className="tiny muted chess-depth">Engine · {depth}</span>}
    </>
  )
}

/** Beside Play from here when the analysis board's code couldn't come (a tab opened before a deploy asks for a
 * chunk the new build doesn't have, or the fan is offline): Reload, the app's own (a new version waiting takes
 * over first). */
function BoardFailed() {
  const { applyUpdate } = usePwa()
  return (
    <span className="row" role="alert" style={{ gap: 8 }}>
      <span className="small">Couldn't open the board</span>
      <button type="button" className="btn small" onClick={applyUpdate}>Reload</button>
    </span>
  )
}

/** A line in phrases ("Move 25 · Black to play"), each kept whole: a narrow column breaks it at a dot. */
function phrases(text?: string | null) {
  return text?.split(' · ').map((part, i) => <span key={i}>{i > 0 && ' · '}<span className="nowrap">{part}</span></span>)
}

// A game's moves, replayed (facts/chessLine.ts replayedMoves): each in SAN, numbered, with the position after it.
type Replayed = NonNullable<ReturnType<typeof replayedMoves>>

/** A game's moves replayed (useReplay), the position they start from, and whether they end where its board is
 * (`now`: a page's look can lag the board by more than a move). */
interface Replay { moves: Replayed; start: string; now: boolean }

/** A game's moves on its match page, replayed as chess (replayedMoves, the Android app's too): that takes chess.js,
 * which comes in the analysis board's own chunk of the rules (facts/chessLine.ts). Null until it has, and where
 * they don't replay: then the pairs show as words. */
function useReplay(page: ChessPage | undefined, state: ChessState): Replay | null {
  const [replay, setReplay] = useState<Replay | null>(null)
  const { fen, last_move: last, result } = state
  // By their words: the page's next look, with the same moves, doesn't replay them again.
  const pairs = page?.moves?.join('\n') ?? ''
  const more = page?.more?.join(' ') ?? ''
  useEffect(() => {
    // With nothing to replay, or a position chess.js won't play, the pairs are words at once.
    if ((!pairs && !more) || !fen || !standardCastling(fen)) {
      setReplay(null)
      return
    }
    let current = true
    // The last list stays while the next replays (no flicker each look), and goes where it doesn't.
    import('./facts/chessLine')
      .then(({ lineOfGame, replayedMoves, samePosition }) => {
        const moves = { moves: pairs ? pairs.split('\n') : [], more: more ? more.split(' ') : [] }
        const replayed = replayedMoves(moves, { fen, last_move: last, result })
        if (current) setReplay(replayed && { moves: replayed, start: lineOfGame([], 0)?.start ?? '', now: samePosition(replayed[replayed.length - 1].fen, fen) })
      })
      .catch(() => { if (current) setReplay(null) })
    return () => { current = false }
  }, [pairs, more, fen, last, result])
  return replay
}

/** The game's moves beside its match page's board, in the board's colours: each, once they replay, opening the
 * analysis board after it (`open`), and the one the board is after marked (the last, or where the fan has
 * stepped back to: `at`, 0 the start); while they don't (or chess.js is on its way), the pairs as words, and
 * those added since as the board gave them (a Freestyle game's: "d6d7"). Its opening and how it ended, or of a
 * mini-match the game's number, over them. Kept scrolled to the newest as moves come, unless the fan has
 * scrolled back or stepped back (then to the move the board is after). */
function MovesPanel({ page, sans, at, open }: { page: ChessPage; sans: Replayed | null; at: number | null; open: (moves: string[], ply: number) => void }) {
  const list = useRef<HTMLOListElement>(null)
  const pinned = useRef(true)  // at the newest: false once the fan scrolls back, true again at the end
  const stepped = useRef(at)  // where the fan has stepped back to, for its room's changes
  const count = sans?.length ?? (page.moves?.length ?? 0) + (page.more?.length ?? 0)
  useEffect(() => {
    const ol = list.current
    if (!ol) return
    if (at == null) {
      if (stepped.current != null) pinned.current = true  // back to the board as it is: to the newest, as moves come
      if (pinned.current) ol.scrollTop = ol.scrollHeight
    } else {
      // The move the board is after, kept in sight: the list scrolls, not the page.
      const item = ol.querySelector<HTMLElement>('[aria-current="true"]')
      const top = item ? item.getBoundingClientRect().top - ol.getBoundingClientRect().top + ol.scrollTop : 0
      if (!item) ol.scrollTop = 0
      else if (top < ol.scrollTop || top + item.offsetHeight > ol.scrollTop + ol.clientHeight) ol.scrollTop = top - (ol.clientHeight - item.offsetHeight) / 2
    }
    stepped.current = at
  }, [count, sans, at])
  // And when its room changes (a phone turned: the list beside the board, or under it).
  useEffect(() => {
    const ol = list.current
    if (!ol || typeof ResizeObserver === 'undefined') return
    const watch = new ResizeObserver(() => { if (pinned.current && stepped.current == null) ol.scrollTop = ol.scrollHeight })
    watch.observe(ol)
    return () => watch.disconnect()
  }, [])
  const about = [page.game ? `Game ${page.game}` : null, movesAbout(page)].filter(Boolean).join(' · ')
  return (
    <div className="chess-moves-panel">
      <span className="row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
        <span className="eyebrow">Moves</span>
        {about && <span className="tiny muted">{about}</span>}
      </span>
      <ol ref={list} className="chess-line" aria-label="Moves" onScroll={(e) => { const ol = e.currentTarget; pinned.current = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 24 }}>
        {sans ? sans.map((m, i) => (
          <li key={i}>
            {m.number && <span className="muted">{m.number}</span>}
            <button type="button" className="chess-move" aria-current={i + 1 === (at ?? sans.length)} aria-label={`Play on from ${m.said}`} title={`Play on from ${m.said}`} onClick={() => open(sans.map((x) => x.san), i + 1)}>{m.san}</button>
          </li>
        )) : [...(page.moves ?? []), ...(page.more ?? [])].map((m, i) => <li key={i}>{m}</li>)}
      </ol>
    </div>
  )
}

/** The board from the position's FEN, with the last move marked and, while the game's on, the engine's evaluation
 * beside it when there is one: from the followed player's side (from White's with no one side of the fan's,
 * followedSide), until the fan turns it, which holds while the page is open. With `explore` (the match page's),
 * the board is big, its players level with its edges (beside it on a wide screen, above and under it on a
 * phone: chess.css), the game's moves beside it (its `page`'s: MovesPanel), and Play from here opens the
 * analysis board over the page (chessAnalysis.tsx), as does a move tapped in the list; closing it is back to
 * the game. Once its moves replay, the left and right arrow keys step the board back and on along them (the
 * live bar empty while it's back: it's the position now's), and Play from here opens where it is; new moves
 * leave it there, and Back to live (or stepping on past the last) is the game as it is again. */
function ChessBoard({ state, clock, compact, live, lines, explore, page }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  const [opening, setOpening] = useState<Opening | null>(null)
  // Where the fan has stepped back to along the game's moves (the plies after its start: 0, the start), of the
  // game it was (another match page's, or a mini-match's next game, starts as it is).
  const [stepped, setStepped] = useState<{ game: string; at: number } | null>(null)
  const play = useRef<HTMLButtonElement>(null)
  const back = useRef<HTMLElement | null>(null)  // where the keyboard was when the analysis board opened
  const heard = useRef<(e: globalThis.KeyboardEvent) => void>(() => {})
  // Play from here: the match page's board, of a position that plays by the standard rules (not a Freestyle
  // game's before its castling).
  const playable = Boolean(explore && state.fen && standardCastling(state.fen))
  const moves = explore ? (page as ChessPage | undefined) : undefined
  const replay = useReplay(moves, state)
  const sans = playable ? replay?.moves ?? null : null
  const game = `${live.match ?? live.event_id ?? ''}:${moves?.game ?? ''}`
  // The steps along the moves: the start, after each, and the board as it is where the moves end short of it.
  const last = sans && replay ? sans.length + (replay.now ? 0 : 1) : 0
  const at = stepped && stepped.game === game && stepped.at < last ? stepped.at : null  // null: the board as it is
  const stepTo = (to: number) => setStepped(to >= last ? null : { game, at: Math.max(0, to) })
  // The arrow keys, wherever the keyboard is on the page but on what has arrows of its own, and not while a
  // dialog is open over it (the analysis board has its own; one that couldn't come leaves them to the page).
  heard.current = (e) => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !last || document.querySelector('dialog[open]')) return
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const target = e.target instanceof HTMLElement ? e.target : null
    if (target && (target.isContentEditable || target.closest(OWN_ARROWS))) return
    e.preventDefault()
    stepTo((at ?? last) + (e.key === 'ArrowLeft' ? -1 : 1))
  }
  useEffect(() => {
    if (!explore) return
    const on = (e: globalThis.KeyboardEvent) => heard.current(e)
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [explore])
  // A move tapped in the list: the analysis board after it.
  const openAt = (line: string[], ply: number) => {
    // The keyboard goes back to it after (Safari doesn't focus a clicked button: then Play from here takes it).
    const at = document.activeElement
    back.current = at instanceof HTMLElement && at !== document.body ? at : null
    setOpening((o) => ({ base: state.fen ?? '', last: state.last_move, moves: line, ply, asked: (o?.asked ?? 0) + 1 }))
  }
  if (!state.white || !state.black) return null
  // The board where the fan has stepped back to, or as it is.
  const ply = at && sans ? sans[at - 1] : null
  const shown = at == null || !replay ? state : { ...state, fen: ply ? ply.fen : replay.start, last_move: ply ? ply.from + ply.to : null }
  const { ranks, flip, moved } = position(shown, live, (side ?? followedSide(live, lines)) === 'black')
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0). Its bar stays, empty, while the fan has stepped back.
  const bar = state.result ? null : state.eval
  const ev = at == null ? bar : null
  const where = at == null ? null : ply ? `After ${ply.said}` : 'The start'
  // Closed: the keyboard back where it was, the move tapped in the list or Play from here.
  const leave = () => {
    setOpening(null)
    window.requestAnimationFrame(() => (back.current?.isConnected ? back.current : play.current)?.focus())
  }
  const player = (s: ChessSide, colour: 'white' | 'black', area?: string) => (
    <div className={`row${area ? ` ${area}` : ''}`} style={{ gap: 12 }}>
      <span className="stack" style={{ minWidth: 0 }}>
        <span className="title">{s.title ? <span className="muted">{s.title} </span> : null}{s.name}</span>
        <span className="tiny muted">{[colour === 'white' ? 'White' : 'Black', s.rating, s.fed].filter(Boolean).join(' · ')}</span>
      </span>
      {s.clock && <span className={`num chess-clock${state.turn === colour && !state.result ? ' running' : ''}`}>{s.clock}</span>}
    </div>
  )
  // The game's board as it is (or where the fan has stepped back to), with its bar.
  const board = ranks.length === 8 && (
    <div className={`chess-play${compact ? ' sm' : ''}${bar ? ' eval' : ''}`}>
      {bar && <EvalBar ev={ev} flip={flip} compact={compact} quiet={explore} none="for the live position only" />}
      <div className="chessboard" role="img" aria-label={at == null ? `Position after move ${state.move ?? ''}` : ply ? `Position after ${ply.said}` : 'The starting position'}>
        {ranks.flat().map((sq) => (
          <span key={sq.name} className={`sq ${sq.dark ? 'dark' : 'light'}${moved.has(sq.name) ? ' moved' : ''}`}>
            {sq.piece && <span className={`piece ${sq.piece === sq.piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[sq.piece.toLowerCase()]}︎`}</span>}
          </span>
        ))}
      </div>
    </div>
  )
  const top = flip ? state.white : state.black
  const bottom = flip ? state.black : state.white
  const topColour = flip ? 'white' : 'black'
  const bottomColour = flip ? 'black' : 'white'
  // Between the players: the clock line (or the result), and the button that changes their places (not on Also
  // live's small card: chess.css).
  const middle = (
    <span className="row">
      <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>{phrases(chessMiddle(state, clock))}</span>
      {ranks.length === 8 && (
        <button type="button" className="btn icon chess-turn over-link" aria-label="Switch sides" title="Switch sides" onClick={() => setSide(flip ? 'white' : 'black')}>
          <SwapIcon width={18} height={18} />
        </button>
      )}
    </span>
  )
  const note = (state.result || state.match) && clock ? <span className="small muted">{clock}</span> : null

  if (explore) {
    // The match page's: the analysis board opens over the page, its code a chunk of its own; while it comes, a
    // word that it is, and if it can't, the game's board stays, saying so.
    const depth = ev?.depth ? <span className="tiny muted">Engine · depth {ev.depth}</span> : null
    return (
      <div className="chess-frame">
        <div className={`chess big${moves?.moves?.length || moves?.more?.length ? ' has-moves' : ''}`}>
          <span className="stack chess-mid">{middle}{note}</span>
          {player(top, topColour, 'chess-top')}
          {board}
          {player(bottom, bottomColour, 'chess-bottom')}
          {moves?.moves?.length || moves?.more?.length ? <MovesPanel page={moves} sans={sans} at={at} open={openAt} /> : null}
          {(playable || depth) && (
            <div className="chess-under row">
              {playable && <button ref={play} type="button" className="btn small" onClick={(e) => { back.current = play.current; setOpening({ base: state.fen ?? '', last: state.last_move, moves: at == null ? null : sans?.map((m) => m.san), ply: at ?? undefined, asked: 1, focus: true, keyed: e.detail === 0 }) }}>Play from here</button>}
              {/* Where the fan has stepped back to, said as they step (a live region there before they do, so
                  their first step is read out too), and the way back: together, on a line of their own where
                  the row is narrow. */}
              {playable && (
                <span className="row" style={{ gap: 8 }}>
                  <span className="small" role="status">{where && <strong>{where}</strong>}</span>
                  {/* It goes once pressed: the keyboard goes to Play from here, not to the page's start. */}
                  {where && <button type="button" className="btn small" onClick={() => { setStepped(null); play.current?.focus() }}>{state.result ? 'To the end' : 'Back to live'}</button>}
                </span>
              )}
              {depth}
              {opening && (
                <ErrorBoundary fallback={<BoardFailed />} resetKey={opening}>
                  <Suspense fallback={<span className="row" style={{ gap: 8 }}><span className="spinner" /><span className="small">Opening the board…</span></span>}>
                    <ChessAnalysis state={state} live={live} flip={flip} opening={opening} leave={leave} />
                  </Suspense>
                </ErrorBoundary>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div className="chess" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {board}
      {/* Compact (Today), the players can sit narrower beside a small board: Also live's card fits both in a row. */}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: compact ? 140 : 180 }}>
        {player(top, topColour)}
        {middle}
        {player(bottom, bottomColour)}
        {note}
      </div>
    </div>
  )
}

// The moves, a chess game's only section, are beside its board (MovesPanel): it has no Match of its own.
export const chess: SportView = { ...facts, Board: ChessBoard as SportView['Board'] }

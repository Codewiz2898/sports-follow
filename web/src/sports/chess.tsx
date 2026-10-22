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

/** An evaluation bar: White's share (whiteShare) filling it from White's end, the bottom unless the board is
 * turned (flip), and the score (evalScore) at the end of the side ahead. Live, it's the server's evaluation, the
 * one the swing alerts go by; on an analysis board, the fan's own engine's, and none yet (null) while it loads.
 * A big board says under it how deep the engine looked (unless `quiet`: the board says it itself), a small one
 * in its title. */
export function EvalBar({ ev, flip, compact, quiet }: { ev: ChessEval | null; flip: boolean; compact?: boolean; quiet?: boolean }) {
  if (!ev) return <div className="chess-eval waiting" role="img" aria-label="Engine evaluation: not yet" />
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

// A game's moves, replayed (facts/chessLine.ts replayedMoves): each in SAN, numbered.
type Replayed = NonNullable<ReturnType<typeof replayedMoves>>

/** A game's moves on its match page, replayed as chess (replayedMoves, the Android app's too): that takes chess.js,
 * which comes in the analysis board's own chunk of the rules (facts/chessLine.ts). Null until it has, and where
 * they don't replay: then the pairs show as words. */
function useReplay(page: ChessPage | undefined, state: ChessState): Replayed | null {
  const [sans, setSans] = useState<Replayed | null>(null)
  const { fen, last_move: last, result } = state
  // By their words: the page's next look, with the same moves, doesn't replay them again.
  const pairs = page?.moves?.join('\n') ?? ''
  const more = page?.more?.join(' ') ?? ''
  useEffect(() => {
    // With nothing to replay, or a position chess.js won't play, the pairs are words at once.
    if ((!pairs && !more) || !fen || !standardCastling(fen)) {
      setSans(null)
      return
    }
    let current = true
    // The last list stays while the next replays (no flicker each look), and goes where it doesn't.
    import('./facts/chessLine')
      .then(({ replayedMoves }) => {
        const moves = { moves: pairs ? pairs.split('\n') : [], more: more ? more.split(' ') : [] }
        if (current) setSans(replayedMoves(moves, { fen, last_move: last, result }))
      })
      .catch(() => { if (current) setSans(null) })
    return () => { current = false }
  }, [pairs, more, fen, last, result])
  return sans
}

/** The game's moves beside its match page's board, in the board's colours: each, once they replay, opening the
 * analysis board after it (`open`), and the last (where the board is) marked; while they don't (or chess.js is
 * on its way), the pairs as words, and those added since as the board gave them (a Freestyle game's: "d6d7"). Its opening and how it ended, or of a mini-match the game's number, over them. Kept scrolled to the
 * newest as moves come, unless the fan has scrolled back. */
function MovesPanel({ page, sans, open }: { page: ChessPage; sans: Replayed | null; open: (moves: string[], ply: number) => void }) {
  const list = useRef<HTMLOListElement>(null)
  const pinned = useRef(true)  // at the newest: false once the fan scrolls back, true again at the end
  const count = sans?.length ?? (page.moves?.length ?? 0) + (page.more?.length ?? 0)
  useEffect(() => {
    if (pinned.current && list.current) list.current.scrollTop = list.current.scrollHeight
  }, [count, sans])
  // And when its room changes (a phone turned: the list beside the board, or under it).
  useEffect(() => {
    const ol = list.current
    if (!ol || typeof ResizeObserver === 'undefined') return
    const watch = new ResizeObserver(() => { if (pinned.current) ol.scrollTop = ol.scrollHeight })
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
            <button type="button" className="chess-move" aria-current={i + 1 === sans.length} aria-label={`Play on from ${m.said}`} title={`Play on from ${m.said}`} onClick={() => open(sans.map((x) => x.san), i + 1)}>{m.san}</button>
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
 * the game. */
function ChessBoard({ state, clock, compact, live, lines, explore, page }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  const [opening, setOpening] = useState<Opening | null>(null)
  const play = useRef<HTMLButtonElement>(null)
  const back = useRef<HTMLElement | null>(null)  // where the keyboard was when the analysis board opened
  // Play from here: the match page's board, of a position that plays by the standard rules (not a Freestyle
  // game's before its castling).
  const playable = Boolean(explore && state.fen && standardCastling(state.fen))
  const moves = explore ? (page as ChessPage | undefined) : undefined
  const sans = useReplay(moves, state)
  // A move tapped in the list: the analysis board after it.
  const openAt = (line: string[], ply: number) => {
    // The keyboard goes back to it after (Safari doesn't focus a clicked button: then Play from here takes it).
    const at = document.activeElement
    back.current = at instanceof HTMLElement && at !== document.body ? at : null
    setOpening((o) => ({ base: state.fen ?? '', last: state.last_move, moves: line, ply, asked: (o?.asked ?? 0) + 1 }))
  }
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0).
  const ev = state.result ? null : state.eval
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
  // The game's board as it is, with its bar.
  const board = ranks.length === 8 && (
    <div className={`chess-play${compact ? ' sm' : ''}${ev ? ' eval' : ''}`}>
      {ev && <EvalBar ev={ev} flip={flip} compact={compact} quiet={explore} />}
      <div className="chessboard" role="img" aria-label={`Position after move ${state.move ?? ''}`}>
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
          {moves?.moves?.length || moves?.more?.length ? <MovesPanel page={moves} sans={playable ? sans : null} open={openAt} /> : null}
          {(playable || depth) && (
            <div className="chess-under row">
              {playable && <button ref={play} type="button" className="btn small" onClick={() => { back.current = play.current; setOpening({ base: state.fen ?? '', last: state.last_move, asked: 1, focus: true }) }}>Play from here</button>}
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

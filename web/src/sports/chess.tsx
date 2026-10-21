import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { SwapIcon } from '../components/Icons'
import { usePwa } from '../pwa'
import './chess.css'
import type { Opening } from './facts/chessLine'
import { chess as facts, chessMiddle, evalDepth, evalScore, evalWords, followedSide, gameMoves, movesAbout, PIECES, position, standardCastling, whiteShare, type ChessColour, type ChessEval, type ChessPage, type ChessSide, type ChessState } from './facts/chess'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

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

// A finished game's move tapped in its Moves list (ChessMatch), for the match page's board to play from: the
// board that offers Play from here listens.
type Ask = { moves: string[]; ply: number }
const listening = new Set<(ask: Ask) => void>()

/** The board from the position's FEN, with the last move marked and, while the game's on, the engine's evaluation
 * beside it when there is one: from the followed player's side (from White's with no one side of the fan's,
 * followedSide), until the fan turns it, which holds while the page is open. With `explore` (the match page's),
 * the board is big, its players level with its edges (beside it on a wide screen, above and under it on a
 * phone: chess.css), and Play from here opens the analysis board over the page (chessAnalysis.tsx), as does a
 * finished game's move tapped in the Moves list; closing it is back to the game. */
function ChessBoard({ state, clock, compact, live, lines, explore }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  const [opening, setOpening] = useState<Opening | null>(null)
  const play = useRef<HTMLButtonElement>(null)
  // Play from here: the match page's board, of a position that plays by the standard rules (not a Freestyle
  // game's before its castling).
  const playable = Boolean(explore && state.fen && standardCastling(state.fen))
  useEffect(() => {
    if (!playable || !state.fen) return
    const base = state.fen
    const asked = (ask: Ask) => setOpening((o) => ({ base, last: state.last_move, moves: ask.moves, ply: ask.ply, asked: (o?.asked ?? 0) + 1 }))
    listening.add(asked)
    return () => { listening.delete(asked) }
  }, [playable, state.fen, state.last_move])
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0).
  const ev = state.result ? null : state.eval
  const leave = () => {
    setOpening(null)
    window.requestAnimationFrame(() => play.current?.focus())
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
        <div className="chess big">
          <span className="stack chess-mid">{middle}{note}</span>
          {player(top, topColour, 'chess-top')}
          {board}
          {player(bottom, bottomColour, 'chess-bottom')}
          {(playable || depth) && (
            <div className="chess-under row">
              {playable && <button ref={play} type="button" className="btn small" onClick={() => setOpening({ base: state.fen ?? '', last: state.last_move, asked: 1, focus: true })}>Play from here</button>}
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

/** A finished game's match page under its board: the moves that led there, in pairs, with its opening and how
 * it ended; of a mini-match, the last game's (the one on the board). Each move opens the board's analysis at the
 * position after it where they replay as chess from the start to the position on the board: a Freestyle game's,
 * or one set up from a position, don't. Knowing that takes chess.js, which comes for it in the analysis board's
 * own chunk of the rules (facts/chessLine.ts); until it has, and where they don't, the moves are words. */
function ChessMatch({ page, live }: MatchProps<ChessPage>) {
  const [replays, setReplays] = useState<string[] | null>(null)
  const fen = (live?.state as ChessState | undefined)?.fen
  const moves = page.moves?.join(' ') ?? ''
  useEffect(() => {
    setReplays(null)
    const sans = page.moves ? gameMoves(page.moves) : null
    if (!sans || !fen || !standardCastling(fen)) return
    let current = true
    import('./facts/chessLine')
      .then(({ fenAt, lineOfGame, samePosition }) => {
        const game = lineOfGame(sans, sans.length)
        if (current && game && samePosition(fenAt(game), fen)) setReplays(sans)
      })
      .catch(() => {})  // the moves stay words
    return () => { current = false }
    // By the moves' words: the page's next look, with the same moves, doesn't replay them again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moves, fen])
  if (!page.moves?.length) return null
  const about = movesAbout(page)
  const move = (ply: number) => {
    const san = replays?.[ply - 1]
    if (!replays || !san) return null
    const said = `${Math.ceil(ply / 2)}${ply % 2 ? '.' : '...'} ${san}`
    return (
      <button type="button" className="chess-move" aria-label={`Play on from ${said}`} title={`Play on from ${said}`} onClick={() => listening.forEach((asked) => asked({ moves: replays, ply }))}>
        {san}
      </button>
    )
  }
  return (
    <MatchSection title="Moves" note={page.game ? `Game ${page.game}` : null}>
      <div className="card card-pad stack" style={{ gap: 10 }}>
        {about && <span className="small muted">{about}</span>}
        {replays && <span className="tiny muted">Tap a move to play on from there.</span>}
        <ol className="chess-moves">
          {page.moves.map((m, i) => (
            <li key={m} className="small">{replays ? <>{i + 1}. {move(2 * i + 1)} {move(2 * i + 2)}</> : m}</li>
          ))}
        </ol>
      </div>
    </MatchSection>
  )
}

export const chess: SportView = { ...facts, Board: ChessBoard as SportView['Board'], Match: ChessMatch as SportView['Match'] }

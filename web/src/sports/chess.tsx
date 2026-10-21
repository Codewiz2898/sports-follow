import { useState } from 'react'
import { SwapIcon } from '../components/Icons'
import './chess.css'
import { chess as facts, chessMiddle, evalScore, followedSide, movesAbout, PIECES, position, whiteShare, type ChessColour, type ChessEval, type ChessPage, type ChessSide, type ChessState } from './facts/chess'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

/** An evaluation bar: White's share (whiteShare) filling it from White's end, the bottom unless the board is
 * turned (flip), and the score (evalScore) at the end of the side ahead. Live, it's the server's evaluation, the
 * one the swing alerts go by; it draws any other the same. A big board says under it how deep the engine looked,
 * a small one in its title. */
function EvalBar({ ev, flip, compact }: { ev: ChessEval; flip: boolean; compact?: boolean }) {
  const share = whiteShare(ev)
  const score = evalScore(ev)
  const white = share >= 50  // White ahead, or level
  const depth = ev.depth ? `depth ${ev.depth}` : null
  const words = `Engine evaluation ${score}, from White's side${depth ? `, ${depth}` : ''}`
  return (
    <>
      <div className={`chess-eval${flip ? ' flip' : ''}`} role="img" aria-label={words} title={words}>
        <span className="fill" style={{ height: `${share}%` }} />
        <span className={`num score ${white ? 'white' : 'black'}${white === flip ? ' top' : ''}`}>{score}</span>
      </div>
      {!compact && depth && <span className="tiny muted chess-depth">Engine · {depth}</span>}
    </>
  )
}

/** The board from the position's FEN, with the last move marked and, while the game's on, the engine's evaluation
 * beside it when there is one: from the followed player's side (from White's with no one side of the fan's,
 * followedSide), until the fan turns it, which holds while the page is open. */
function ChessBoard({ state, clock, compact, live, lines }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0).
  const ev = state.result ? null : state.eval
  const player = (s: ChessSide, colour: 'white' | 'black') => (
    <div className="row" style={{ gap: 12 }}>
      <span className="stack" style={{ minWidth: 0 }}>
        <span className="title">{s.title ? <span className="muted">{s.title} </span> : null}{s.name}</span>
        <span className="tiny muted">{[colour === 'white' ? 'White' : 'Black', s.rating, s.fed].filter(Boolean).join(' · ')}</span>
      </span>
      {s.clock && <span className={`num chess-clock${state.turn === colour && !state.result ? ' running' : ''}`}>{s.clock}</span>}
    </div>
  )
  const top = flip ? state.white : state.black
  const bottom = flip ? state.black : state.white
  return (
    <div className="chess" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {ranks.length === 8 && (
        <div className={`chess-play${compact ? ' sm' : ''}${ev ? ' eval' : ''}`}>
          {ev && <EvalBar ev={ev} flip={flip} compact={compact} />}
          <div className="chessboard" role="img" aria-label={`Position after move ${state.move ?? ''}`}>
            {ranks.flat().map((sq) => (
              <span key={sq.name} className={`sq ${sq.dark ? 'dark' : 'light'}${moved.has(sq.name) ? ' moved' : ''}`}>
                {sq.piece && <span className={`piece ${sq.piece === sq.piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[sq.piece.toLowerCase()]}︎`}</span>}
              </span>
            ))}
          </div>
        </div>
      )}
      {/* Compact (Today), the players can sit narrower beside a small board: Also live's card fits both in a row. */}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black')}
        {/* Between the players, the button that changes their places: not on Also live's small card (chess.css). */}
        <span className="row">
          <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>{chessMiddle(state, clock)}</span>
          {ranks.length === 8 && (
            <button type="button" className="btn icon chess-turn over-link" aria-label="Switch sides" title="Switch sides" onClick={() => setSide(flip ? 'white' : 'black')}>
              <SwapIcon width={18} height={18} />
            </button>
          )}
        </span>
        {player(bottom, flip ? 'black' : 'white')}
        {(state.result || state.match) && clock && <span className="small muted">{clock}</span>}
      </div>
    </div>
  )
}

/** A finished game's match page under its board: the moves that led there, in pairs, with its opening and how
 * it ended; of a mini-match, the last game's (the one on the board). */
function ChessMatch({ page }: MatchProps<ChessPage>) {
  if (!page.moves?.length) return null
  const about = movesAbout(page)
  return (
    <MatchSection title="Moves" note={page.game ? `Game ${page.game}` : null}>
      <div className="card card-pad stack" style={{ gap: 10 }}>
        {about && <span className="small muted">{about}</span>}
        <ol className="chess-moves">
          {page.moves.map((m) => <li key={m} className="small">{m}</li>)}
        </ol>
      </div>
    </MatchSection>
  )
}

export const chess: SportView = { ...facts, Board: ChessBoard as SportView['Board'], Match: ChessMatch as SportView['Match'] }

import { useState, type MouseEvent } from 'react'
import { SwapIcon } from '../components/Icons'
import './chess.css'
import { chess as facts, chessMiddle, followedSide, movesAbout, PIECES, position, type ChessColour, type ChessPage, type ChessSide, type ChessState } from './facts/chess'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

/** The board from the position's FEN, with the last move marked: from the followed player's side (from White's
 * with no one side of the fan's, followedSide), until the fan turns it, which holds while the page is open. */
function ChessBoard({ state, clock, compact, live, lines }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  const turn = (e: MouseEvent) => {
    e.preventDefault()  // on Today the board is inside its card's link: turning it doesn't open the match page
    setSide(flip ? 'white' : 'black')
  }
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
        <div className={`chessboard${compact ? ' sm' : ''}`} role="img" aria-label={`Position after move ${state.move ?? ''}`}>
          {ranks.flat().map((sq) => (
            <span key={sq.name} className={`sq ${sq.dark ? 'dark' : 'light'}${moved.has(sq.name) ? ' moved' : ''}`}>
              {sq.piece && <span className={`piece ${sq.piece === sq.piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[sq.piece.toLowerCase()]}︎`}</span>}
            </span>
          ))}
        </div>
      )}
      {/* Compact (Today), the players can sit narrower beside a small board: Also live's card fits both in a row. */}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black')}
        {/* Between the players, the button that changes their places: not on Also live's small card (chess.css). */}
        <span className="row">
          <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>{chessMiddle(state, clock)}</span>
          {ranks.length === 8 && (
            <button type="button" className="btn icon chess-turn" aria-label="Switch sides" title="Switch sides" onClick={turn}>
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

import './chess.css'
import { chess as facts, chessMiddle, movesAbout, PIECES, position, type ChessPage, type ChessSide, type ChessState } from './facts/chess'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

/** The board from the position's FEN, from the followed player's side, with the last move marked. */
function ChessBoard({ state, clock, compact, live }: BoardProps<ChessState>) {
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live)
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
        <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>{chessMiddle(state, clock)}</span>
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

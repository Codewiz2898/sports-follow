import './chess.css'
import type { BoardProps, SportView } from './view'

export interface ChessSide { id: string; name: string; title?: string | null; rating?: number | null; fed?: string | null; clock?: string | null }
export interface ChessState {
  kind: 'chess'
  round?: string | null
  tournament?: string | null
  white?: ChessSide
  black?: ChessSide
  fen?: string
  last_move?: string | null
  turn?: 'white' | 'black'
  move?: number | null
  result?: string | null
  match?: { games: number; score: string } | null  // knockout rounds: several games between the same two players
}

// An empty chessboard: its edge and the 32 dark squares, filled.
const BOARD = (
  <>
    <rect x="2" y="2" width="96" height="96" />
    <path stroke="none" fill="currentColor" fillOpacity={0.45} d={Array.from({ length: 32 }, (_, i) => {
      const rank = Math.floor(i / 4)
      return `M${2 + 12 * (2 * (i % 4) + ((rank + 1) % 2))} ${2 + 12 * rank}h12v12h-12z`
    }).join('')} />
  </>
)

const PIECES: Record<string, string> = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' }

/** The board from the position's FEN, from the followed player's side, with the last move marked. */
function ChessBoard({ state, clock, compact, live }: BoardProps<ChessState>) {
  if (!state.white || !state.black) return null
  const flip = (live.player_stats ?? []).some((s) => s.label === 'Colour' && s.value === 'Black')
  const rows = (state.fen ?? '').split(' ')[0].split('/')
  const squares: (string | null)[][] = rows.map((row) => row.split('').flatMap((c) => (/\d/.test(c) ? Array(Number(c)).fill(null) : [c])))
  const moved = new Set(state.last_move ? [state.last_move.slice(0, 2), state.last_move.slice(2, 4)] : [])
  const ranks = flip ? [0, 1, 2, 3, 4, 5, 6, 7].reverse() : [0, 1, 2, 3, 4, 5, 6, 7]
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
      {rows.length === 8 && (
        <div className={`chessboard${compact ? ' sm' : ''}`} role="img" aria-label={`Position after move ${state.move ?? ''}`}>
          {ranks.map((r) => (flip ? [...squares[r]].reverse() : squares[r]).map((piece, i) => {
            const file = flip ? 7 - i : i
            const name = `${'abcdefgh'[file]}${8 - r}`
            return (
              <span key={name} className={`sq ${(r + file) % 2 ? 'dark' : 'light'}${moved.has(name) ? ' moved' : ''}`}>
                {piece && <span className={`piece ${piece === piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[piece.toLowerCase()]}︎`}</span>}
              </span>
            )
          }))}
        </div>
      )}
      {/* Compact (Today), the players can sit narrower beside a small board: Also live's card fits both in a row. */}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black')}
        <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>
          {state.match ? `Match ${state.match.score}` : state.result ? state.result.replace('-', '–') : clock}
        </span>
        {player(bottom, flip ? 'black' : 'white')}
        {(state.result || state.match) && clock && <span className="small muted">{clock}</span>}
      </div>
    </div>
  )
}

export const chess: SportView = {
  key: 'chess',
  kinds: ['chess'],
  Board: ChessBoard as SportView['Board'],
  draws: ((state: ChessState) => Boolean(state.white && state.black)) as SportView['draws'],
  backdrop: BOARD,
  inHeadline: ['Colour', 'Opponent'],
}

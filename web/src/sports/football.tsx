import { football as facts, type FootballSide, type FootballState } from './facts/football'
import type { BoardProps, SportView } from './view'

function FootballBoard({ state, clock, compact }: BoardProps<FootballState>) {
  const side = (s: FootballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title">{s.name}</span>
      {!compact && s.scorers.map((g) => <span key={g} className="tiny muted">{g}</span>)}
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
      {side(state.home, 'left')}
      <div className="stack" style={{ alignItems: 'center' }}>
        <span className="num score">{state.home.score}–{state.away.score}</span>
        {clock && <span className="num clock">{clock}</span>}
      </div>
      {side(state.away, 'right')}
    </div>
  )
}

export const football: SportView = { ...facts, Board: FootballBoard as SportView['Board'] }

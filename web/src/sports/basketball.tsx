import { basketball as facts, periodName, type BasketballSide, type BasketballState } from './facts/basketball'
import { TeamCrest } from '../components/Crest'
import { teamKey } from '../look'
import type { BoardProps, SportView } from './view'

/** Away at home, as US leagues write it, with the quarter-by-quarter line under it. */
function BasketballBoard({ state, clock, compact, live, explore }: BoardProps<BasketballState>) {
  // Each side's crest, but on the match page, where its band shows them big.
  const side = (s: BasketballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      {!explore && <TeamCrest teamKey={teamKey(live.match, s.id)} name={s.name} size={compact ? 28 : 36} />}
      <span className="title">{s.name}</span>
      <span className="tiny muted">{align === 'left' ? 'Away' : 'Home'}</span>
    </div>
  )
  const periods = Math.max(state.away.periods.length, state.home.periods.length)
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
        {side(state.away, 'left')}
        <div className="stack" style={{ alignItems: 'center' }}>
          <span className="num score">{state.away.score}–{state.home.score}</span>
          {clock && <span className="num clock">{clock}</span>}
        </div>
        {side(state.home, 'right')}
      </div>
      {!compact && periods > 0 && (
        <table className="linescore">
          <thead><tr><th />{Array.from({ length: periods }, (_, i) => <th key={i}>{periodName(i)}</th>)}<th>T</th></tr></thead>
          <tbody>
            {[state.away, state.home].map((s) => (
              <tr key={s.id}><td>{s.abbr || s.name}</td>{Array.from({ length: periods }, (_, i) => <td key={i}>{s.periods[i] ?? ''}</td>)}<td className="total">{s.score}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export const basketball: SportView = { ...facts, Board: BasketballBoard as SportView['Board'], awayFirst: true }

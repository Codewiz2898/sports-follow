import { lineStats } from '../format'
import type { BoardProps, SportView } from './view'

export interface BasketballSide { id: string; name: string; abbr?: string | null; score: string; periods: string[]; winner?: boolean | null }
export interface BasketballState { kind: 'basketball'; home: BasketballSide; away: BasketballSide; period?: number | null; clock?: string | null; detail?: string | null; note?: string | null }

/** Away at home, as US leagues write it, with the quarter-by-quarter line under it. */
function BasketballBoard({ state, clock, compact }: BoardProps<BasketballState>) {
  const side = (s: BasketballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
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
          <thead><tr><th />{Array.from({ length: periods }, (_, i) => <th key={i}>{i < 4 ? `Q${i + 1}` : `OT${i > 4 ? i - 3 : ''}`}</th>)}<th>T</th></tr></thead>
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

export const basketball: SportView = {
  key: 'basketball',
  kinds: ['basketball'],
  Board: BasketballBoard as SportView['Board'],
  highlight: (line) => {
    const tens = (lineStats(line) ?? []).filter((s) => /^(PTS|REB|AST|STL|BLK)$/i.test(s.label) && Number(s.value) >= 10).length
    return tens >= 3 ? 'Triple-double' : tens === 2 ? 'Double-double' : null
  },
  noLine: "isn't on the game's roster",
}

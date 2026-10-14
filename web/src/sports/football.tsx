import type { BoardProps, SportView } from './view'

export interface FootballSide { id: string; name: string; abbr?: string | null; score: string; scorers: string[] }
export interface FootballState { kind: 'football'; home: FootballSide; away: FootballSide; detail?: string | null }

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

export const football: SportView = {
  key: 'football',
  kinds: ['football'],
  Board: FootballBoard as SportView['Board'],
  mark: <><circle cx="50" cy="50" r="46" /><path d="M50 30l18 13-7 21H39l-7-21zM50 4v26M68 43l26-8M61 64l15 22M39 64 24 86M32 43 6 35" /></>,
  noLine: "isn't in the matchday squad",
  inHeadline: ['Goals', 'Assists'],
}

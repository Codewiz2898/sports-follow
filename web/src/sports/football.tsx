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
  // A pitch's markings, 105 by 68 metres: halfway line, centre circle, both boxes with their arcs, corners.
  backdrop: (
    <svg x="0" y="17.6" width="100" height="64.8" viewBox="0 0 105 68">
      <rect x="1" y="1" width="103" height="66" />
      <path d="M52.5 1v66M17.5 26.69a9.15 9.15 0 0 1 0 14.62M87.5 26.69a9.15 9.15 0 0 0 0 14.62M1 2a1 1 0 0 0 1-1M103 1a1 1 0 0 0 1 1M104 66a1 1 0 0 0-1 1M2 67a1 1 0 0 0-1-1" />
      <circle cx="52.5" cy="34" r="9.15" />
      <rect x="1" y="13.85" width="16.5" height="40.3" />
      <rect x="1" y="24.85" width="5.5" height="18.3" />
      <rect x="87.5" y="13.85" width="16.5" height="40.3" />
      <rect x="98.5" y="24.85" width="5.5" height="18.3" />
      <path d="M12 34h.01M52.5 34h.01M93 34h.01" strokeWidth={3} />
    </svg>
  ),
  noLine: "isn't in the matchday squad",
  inHeadline: ['Goals', 'Assists'],
}

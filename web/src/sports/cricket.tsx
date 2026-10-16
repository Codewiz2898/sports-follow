import { cricket as facts, inningsScore, type CricketState } from './facts/cricket'
import type { BoardProps, SportView } from './view'

function CricketBoard({ state, clock, compact }: BoardProps<CricketState>) {
  if (!state.innings.length) {
    return (
      <div className="stack">
        <span className="num" style={{ fontSize: compact ? 28 : 36 }}>{state.teams.map((t) => t.name).join(' v ')}</span>
        {clock && <span className="live-note">{clock}</span>}
      </div>
    )
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      {state.innings.map((i) => (
        <div key={`${i.team}-${i.period}`} className="row" style={{ alignItems: 'baseline' }}>
          <span className="title" style={{ color: i.batting ? 'var(--text)' : 'var(--muted)' }}>
            {i.abbr || i.team}
            {i.batting && <span className="tiny live-note" style={{ marginLeft: 8 }}>batting</span>}
          </span>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span className={i.batting ? 'num score' : 'num'} style={i.batting ? undefined : { fontSize: 26, color: 'var(--muted)' }}>
              {inningsScore(i)}
            </span>
            {i.overs != null && <span className="small muted">{i.overs} ov</span>}
          </span>
        </div>
      ))}
      {(state.summary || clock) && <span className="live-note">{[clock, state.summary !== clock && state.summary].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

export const cricket: SportView = { ...facts, Board: CricketBoard as SportView['Board'] }

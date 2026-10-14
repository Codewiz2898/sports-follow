import type { BoardProps, SportView } from './view'

export interface CricketInnings { team: string; abbr?: string | null; period?: number | null; runs: number; wickets: number; overs?: number | string | null; batting: boolean; target?: number | null; declared?: boolean }
export interface CricketState {
  kind: 'cricket'
  teams: { id: string; name: string; abbr?: string | null; score: string; winner?: boolean | null }[]
  innings: CricketInnings[]
  summary?: string
  format?: string | null
  player_of_match?: string | null
}

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
              {i.runs}{i.wickets < 10 ? `/${i.wickets}` : ''}{i.declared ? 'd' : ''}
            </span>
            {i.overs != null && <span className="small muted">{i.overs} ov</span>}
          </span>
        </div>
      ))}
      {(state.summary || clock) && <span className="live-note">{[clock, state.summary !== clock && state.summary].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

export const cricket: SportView = {
  key: 'cricket',
  kinds: ['cricket'],
  Board: CricketBoard as SportView['Board'],
  mark: <><circle cx="50" cy="50" r="46" /><path d="M30 10c10 26 10 54 0 80M38 12l-6 3M40 26l-7 2M41 40h-7M41 54l-7-1M40 68l-7-2M38 82l-6-3" /></>,
  highlight: (line) => {
    const runs = Math.max(0, ...[...(line ?? '').matchAll(/(?:^|&\s*)(\d+)\*?\s*\(/g)].map((m) => Number(m[1])))
    return runs >= 100 ? 'Hundred' : runs >= 50 ? 'Fifty' : null
  },
  noLine: "hasn't batted or bowled yet",
  inHeadline: ['Runs', 'Balls', 'Wickets', 'Overs', 'Runs conceded'],
}

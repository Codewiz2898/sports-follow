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
  // The ground from above: the boundary, the 30-yard circle, and the pitch with its creases and stumps.
  backdrop: (
    <>
      <ellipse cx="50" cy="50" rx="48" ry="44" />
      <ellipse cx="50" cy="50" rx="30" ry="27" strokeDasharray="2 2" />
      <rect x="46.5" y="38" width="7" height="24" />
      <path d="M44 41.5h12M44 58.5h12M47 40v-4M47 64v-4M53 40v-4M53 64v-4M48 39.5h4M48 60.5h4M49 39.5h.01M50 39.5h.01M51 39.5h.01M49 60.5h.01M50 60.5h.01M51 60.5h.01" />
    </>
  ),
  highlight: (line) => {
    const runs = Math.max(0, ...[...(line ?? '').matchAll(/(?:^|&\s*)(\d+)\*?\s*\(/g)].map((m) => Number(m[1])))
    return runs >= 100 ? 'Hundred' : runs >= 50 ? 'Fifty' : null
  },
  noLine: "hasn't batted or bowled yet",
  inHeadline: ['Runs', 'Balls', 'Wickets', 'Overs', 'Runs conceded'],
}

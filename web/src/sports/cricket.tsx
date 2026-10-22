import { cricket as facts, inningsScore, type CricketPage, type CricketState } from './facts/cricket'
import { TeamCrest } from '../components/Crest'
import { teamKey } from '../look'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

function CricketBoard({ state, clock, compact, live, explore }: BoardProps<CricketState>) {
  // Each innings' side's crest (a national side's flag), but on the match page, where its band shows them big.
  const crest = (team: string) => {
    const t = state.teams.find((x) => x.name === team)
    return !explore && t ? <TeamCrest teamKey={teamKey(live.match, t.id)} name={t.name} size={26} /> : null
  }
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
        <div key={`${i.team}-${i.period}`} className="row" style={{ alignItems: 'center' }}>
          <span className="title" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: i.batting ? 'var(--text)' : 'var(--muted)' }}>
            {crest(i.team)}
            {i.abbr || i.team}
            {i.batting && <span className="tiny live-note">batting</span>}
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

/** A match page's scorecard: each side in the order they batted, who batted for it and how they were out, and
 * who bowled at it. */
function CricketMatch({ page }: MatchProps<CricketPage>) {
  if (!page.scorecard?.length) return null
  return (
    <>
      {page.scorecard.map((side) => (
        <MatchSection key={side.team} title={side.team} note={side.score}>
          <div className="card card-pad stack" style={{ gap: 14 }}>
            {side.batting.length > 0 && (
              <table className="linescore scorecard">
                <thead><tr><th>Batting</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead>
                <tbody>
                  {side.batting.map((b) => (
                    <tr key={b.name}>
                      <td className="who"><span className="title">{b.name}</span><span className="tiny muted">{b.out || 'not out'}</span></td>
                      <td className="total">{b.runs}</td><td>{b.balls}</td><td>{b.fours}</td><td>{b.sixes}</td><td>{b.strike_rate}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {side.bowling.length > 0 && (
              <table className="linescore scorecard">
                <thead><tr><th>Bowling</th><th>O</th><th>R</th><th>W</th><th>Econ</th></tr></thead>
                <tbody>
                  {side.bowling.map((w) => (
                    <tr key={w.name}><td className="who"><span className="title">{w.name}</span></td><td>{w.overs}</td><td>{w.runs}</td><td className="total">{w.wickets}</td><td>{w.economy}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </MatchSection>
      ))}
    </>
  )
}

export const cricket: SportView = { ...facts, Board: CricketBoard as SportView['Board'], Match: CricketMatch as SportView['Match'] }

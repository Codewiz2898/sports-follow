import { football as facts, type FootballPage, type FootballSide, type FootballState } from './facts/football'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

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

const KINDS: Record<string, string> = { GOAL: 'Goal', DISALLOWED: 'No goal', YELLOW: 'Yellow', RED: 'Red' }

/** A match page's timeline: each goal and card at its minute, newest first, the home side's on the left and
 * the away side's on the right. */
function FootballMatch({ page }: MatchProps<FootballPage>) {
  if (!page.timeline?.length) return null
  return (
    <MatchSection title="Timeline">
      <ol className="card list timeline">
        {page.timeline.map((e, i) => (
          <li key={`${e.clock}-${e.kind}-${i}`} className={e.side === 1 ? 'away' : undefined}>
            <span className="num">{e.clock}</span>
            <span className={`moment-kind ${e.kind.toLowerCase()}`}>{KINDS[e.kind] ?? e.kind.toLowerCase()}</span>
            <span className="small">{e.text}</span>
          </li>
        ))}
      </ol>
    </MatchSection>
  )
}

export const football: SportView = { ...facts, Board: FootballBoard as SportView['Board'], Match: FootballMatch as SportView['Match'] }

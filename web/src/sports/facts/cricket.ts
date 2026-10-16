import type { SportFacts } from '.'

export interface CricketInnings { team: string; abbr?: string | null; period?: number | null; runs: number; wickets: number; overs?: number | string | null; batting: boolean; target?: number | null; declared?: boolean }
export interface CricketState {
  kind: 'cricket'
  teams: { id: string; name: string; abbr?: string | null; score: string; winner?: boolean | null }[]
  innings: CricketInnings[]
  summary?: string
  format?: string | null
  player_of_match?: string | null
}

// A match page's scorecard (the server's Cricket.match_page): each side in the order they batted, who batted for
// it ("out" is how they were out; empty, not out) and who bowled at it.
export interface ScorecardBatter { name: string; runs: string; balls: string; fours: string; sixes: string; strike_rate: string; out: string }
export interface ScorecardBowler { name: string; overs: string; runs: string; wickets: string; economy: string }
export interface ScorecardSide { team: string; score: string; batting: ScorecardBatter[]; bowling: ScorecardBowler[] }
export interface CricketPage { scorecard?: ScorecardSide[] }

/** An innings' score as the board writes it: "189/5", "412/9d", "245" all out. */
export function inningsScore(i: CricketInnings): string {
  return `${i.runs}${i.wickets < 10 ? `/${i.wickets}` : ''}${i.declared ? 'd' : ''}`
}

export const cricket: SportFacts = {
  key: 'cricket',
  kinds: ['cricket'],
  // The ground from above: the boundary, the 30-yard circle, and the pitch with its creases and stumps.
  art: `<ellipse cx="50" cy="50" rx="48" ry="44" />
    <ellipse cx="50" cy="50" rx="30" ry="27" stroke-dasharray="2 2" />
    <rect x="46.5" y="38" width="7" height="24" />
    <path d="M44 41.5h12M44 58.5h12M47 40v-4M47 64v-4M53 40v-4M53 64v-4M48 39.5h4M48 60.5h4M49 39.5h.01M50 39.5h.01M51 39.5h.01M49 60.5h.01M50 60.5h.01M51 60.5h.01" />`,
  highlight: (line) => {
    const runs = Math.max(0, ...[...(line ?? '').matchAll(/(?:^|&\s*)(\d+)\*?\s*\(/g)].map((m) => Number(m[1])))
    return runs >= 100 ? 'Hundred' : runs >= 50 ? 'Fifty' : null
  },
  noLine: "hasn't batted or bowled yet",
  inHeadline: ['Runs', 'Balls', 'Wickets', 'Overs', 'Runs conceded'],
  // The player's own line ("61* (44)"), else the batting side's score as the board has it ("IND 189/5").
  short: ((state: CricketState, live) => {
    const i = state.innings.find((x) => x.batting)
    return live.headline || (i ? `${i.abbr || i.team} ${i.runs}${i.wickets < 10 ? `/${i.wickets}` : ''}` : null)
  }) as SportFacts['short'],
}

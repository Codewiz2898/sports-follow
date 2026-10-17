import type { SportFacts } from '.'

export interface FootballSide { id: string; name: string; abbr?: string | null; score: string; scorers: string[] }
export interface FootballState { kind: 'football'; home: FootballSide; away: FootballSide; detail?: string | null }
// A match page's timeline (the server's Football.match_page): each goal and card, newest first, on the side it
// counts for (0 home, 1 away). kind: GOAL, DISALLOWED (a goal VAR ruled out), YELLOW, RED.
export interface TimelineEntry { clock: string; kind: string; text: string; side: 0 | 1 | null }
export interface FootballPage { timeline?: TimelineEntry[] }

const KINDS: Record<string, string> = { GOAL: 'Goal', DISALLOWED: 'No goal', YELLOW: 'Yellow', RED: 'Red' }

/** What a timeline entry's chip says: "Goal", "No goal" (ruled out), "Yellow", "Red"; another kind in lower case. */
export function timelineKind(kind: string): string {
  return KINDS[kind] ?? kind.toLowerCase()
}

export const football: SportFacts = {
  key: 'football',
  kinds: ['football'],
  // A pitch's markings, 105 by 68 metres, fitted across the box: halfway line, centre circle, both boxes
  // with their arcs, corners.
  art: `<g transform="translate(0 17.62) scale(0.95238)">
    <rect x="1" y="1" width="103" height="66" />
    <path d="M52.5 1v66M17.5 26.69a9.15 9.15 0 0 1 0 14.62M87.5 26.69a9.15 9.15 0 0 0 0 14.62M1 2a1 1 0 0 0 1-1M103 1a1 1 0 0 0 1 1M104 66a1 1 0 0 0-1 1M2 67a1 1 0 0 0-1-1" />
    <circle cx="52.5" cy="34" r="9.15" />
    <rect x="1" y="13.85" width="16.5" height="40.3" />
    <rect x="1" y="24.85" width="5.5" height="18.3" />
    <rect x="87.5" y="13.85" width="16.5" height="40.3" />
    <rect x="98.5" y="24.85" width="5.5" height="18.3" />
    <path d="M12 34h.01M52.5 34h.01M93 34h.01" stroke-width="3" />
  </g>`,
  noLine: "isn't in the matchday squad",
  inHeadline: ['Goals', 'Assists'],
  // The score as the board has it, home first, and the minute: "1–0 · 67'".
  short: ((state: FootballState, live) => [`${state.home.score}–${state.away.score}`, live.clock].filter(Boolean).join(' · ')) as SportFacts['short'],
}

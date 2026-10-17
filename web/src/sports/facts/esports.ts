import type { SportFacts } from '.'

export interface EsportsTeam { id: string; name: string; abbr?: string | null; score: number; winner?: boolean | null }
export interface EsportsMap { n: number; status: string; winner?: string | null }  // status: finished | running | not_started
// Dota 2 inside the map (OpenDota): the map being played, kills a side, clock and gold lead.
export interface DotaLiveMap { n: number; clock: string; radiant: { id: string; name: string; kills: number }; dire: { id: string; name: string; kills: number }; gold_lead: { id: string; amount: number } }
export interface EsportsState { kind: 'esports'; game?: string | null; teams: EsportsTeam[]; maps: EsportsMap[]; format?: string | null; stage?: string | null; forfeit?: boolean; dota?: { live?: DotaLiveMap | null } }

// A battle royale lobby: every team's standing, and who won each game ("chicken dinner", "Booyah").
export interface LobbyGame { n: number; place?: number | null; kills: number; points: number }
export interface LobbyTeam { id: string; name: string; place: number; points: number; kills: number; wins: number; games: LobbyGame[] }
export interface BattleRoyaleState {
  kind: 'battle_royale'
  game?: string | null
  win_word?: string
  stage?: string | null
  standings: LobbyTeam[]
  games: { n: number; map?: string | null; status: string; winner?: string | null }[]
  total?: number
}

// A match page's part (the server's Esports.match_page): a series map by map (who took each, and in a Dota 2
// map its best player's numbers), or a lobby's whole standings and who won each of its matches.
export interface SeriesMap { n: number; status: string; winner?: string | null; best?: string | null }
export interface StandingRow { place: number; name: string; points: number; kills: number; wins: number }
export interface EsportsPage { maps?: SeriesMap[]; standings?: StandingRow[]; games?: { n: number; map?: string | null; winner?: string | null }[] }

/** Who won each of a lobby's matches so far, in a line: "M1 (Erangel): GodLike · M2: Soul"; "" before any. */
export function lobbyWinners(page: EsportsPage): string {
  return (page.games ?? []).filter((g) => g.winner).map((g) => `M${g.n}${g.map ? ` (${g.map})` : ''}: ${g.winner}`).join(' · ')
}

/** A lobby's standings, not a two-team series. */
export function isLobby(state: EsportsState | BattleRoyaleState): state is BattleRoyaleState {
  return state.kind === 'battle_royale'
}

export const place = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`
export const points = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1))
const FILLER = /^(team|esports?|gaming|official|club|the)$/i

/** A team's name short enough for a chip: "GodLike Esports" → "GodLike", "Team SouL" → "SouL". */
export function shortTeam(name: string): string {
  const words = name.split(/\s+/).filter((w) => w && !FILLER.test(w))
  return (words[0] ?? name).slice(0, 10)
}

/** The rows a lobby board shows: the top of the standings (three compact, five in full), and the followed
 * player's team wherever it is, marked. */
export function lobbyRows(state: BattleRoyaleState, teams: string[] | undefined, compact?: boolean): { team: LobbyTeam; ours: boolean }[] {
  const ours = (name: string) => (teams ?? []).some((t) => t.toLowerCase() === name.toLowerCase())
  const top = state.standings.slice(0, compact ? 3 : 5)
  const mine = state.standings.find((t) => ours(t.name))
  return (mine && !top.includes(mine) ? [...top, mine] : top).map((team) => ({ team, ours: ours(team.name) }))
}

/** Inside the Dota 2 map being played: kills in the board's team order, and who leads on gold once it's a
 * real lead (a thousand or more). */
export function dotaLine(live: DotaLiveMap, order: string[]): { kills: string; gold: string | null } {
  const [a, b] = order[0] === live.dire.id ? [live.dire, live.radiant] : [live.radiant, live.dire]
  const leader = live.gold_lead.id === live.radiant.id ? live.radiant : live.dire
  return { kills: `${a.kills}–${b.kills}`, gold: live.gold_lead.amount >= 1000 ? `${leader.name} +${(live.gold_lead.amount / 1000).toFixed(1)}k gold` : null }
}

export const esports: SportFacts = {
  key: 'esports',
  kinds: ['esports', 'battle_royale'],
  draws: ((state: EsportsState | BattleRoyaleState) => isLobby(state) || state.teams.length === 2) as SportFacts['draws'],
  // A game map from above: three lanes between two bases, the river across the middle, and the jungle's paths.
  art: `<rect x="5" y="5" width="90" height="90" rx="8" />
    <path d="M14 82V18a4 4 0 0 1 4-4h64M18 86h64a4 4 0 0 0 4-4V18M17 83 83 17" />
    <path d="M5 66c22-3 36-14 38-28s14-28 52-33" stroke-dasharray="3 3" />
    <path d="M28 70c6-10 14-12 22-10M50 40c8 2 16 0 22-10M30 42c4-8 10-12 16-12M54 70c6 0 12-4 16-12" />
    <circle cx="14" cy="86" r="6" />
    <circle cx="86" cy="14" r="6" />`,
  noLine: null, // no player lines: the team's result says it
  inHeadline: ['Place', 'Points'],
}

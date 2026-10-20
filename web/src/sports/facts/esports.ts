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
export interface EsportsPage {
  maps?: SeriesMap[]
  standings?: StandingRow[]
  games?: { n: number; map?: string | null; winner?: string | null }[]
  teams?: PageTeam[] // the series' two teams, by the ids the detail names them by
  detail?: { maps: MapDetail[] } | null
}

// What a source told the page about each finished map (the server's dota.map_detail, for Dota 2): how it went
// in a line a moment, both sides' totals, one side's lead by minute with marks on it (a Roshan), every
// player with their items, and the draft in order. Its words are the server's; this only lays them out.
export interface PageTeam { id: string; name: string; short: string }
export interface MapMoment { t: number; clock: string; title: string; note?: string | null; team?: string | null; mark?: string | null }
export interface MapPlayer { id?: string | null; name: string; team: string; as?: string | null; kda?: number[] | null; gpm?: number | null; numbers: string[]; items: string[]; note?: string | null }
export interface DraftTurn { hero: string; team: string; pick: boolean; player?: string | null }
export interface MapMark { t: number; team: string; label: string; name: string }
export interface MapDetail {
  n: number
  length?: string | null
  winner?: string | null
  sides?: Record<string, string> // team id -> "Radiant" | "Dire"
  lead?: { team: string; what: string; by_minute: number[] } | null
  marks?: MapMark[]
  totals?: { label: string; values: Record<string, string> }[]
  events?: MapMoment[]
  columns?: string[]
  players?: MapPlayer[]
  draft?: DraftTurn[]
}

/** A map's lead by minute from one team's side: up is theirs. */
export function leadOf(map: MapDetail, team: string): number[] {
  const lead = map.lead?.by_minute ?? []
  return map.lead?.team === team ? lead : lead.map((v) => -v)
}

/** "4.4k", "850". */
export function amount(n: number): string {
  return Math.abs(n) >= 1000 ? `${(Math.abs(n) / 1000).toFixed(1)}k` : String(Math.abs(n))
}

/** Points along a lead line in a w×h box (x from pad to w-pad, zero across the middle), scaled to its
 * biggest lead (at least `floor`), with where each minute and amount falls. */
export function leadLine(values: number[], w: number, h: number, pad = 10, floor = 2000) {
  const top = Math.max(floor, ...values.map((v) => Math.abs(v)))
  const mid = h / 2
  const half = mid - pad
  const x = (minute: number) => pad + (values.length > 1 ? (minute * (w - 2 * pad)) / (values.length - 1) : 0)
  const y = (v: number) => mid - (v / top) * half
  return { x, y, mid, top, points: values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ') }
}

/** The series' best player so far, over the maps whose players are in: the most kills, then the fewest
 * deaths, as each map's best is picked. */
export function seriesBest(maps: MapDetail[]): { name: string; kda: string; maps: number; gpm: number } | null {
  const by = new Map<string, { k: number; d: number; a: number; gpm: number; maps: number }>()
  for (const m of maps) {
    for (const p of m.players ?? []) {
      if (!p.name || !p.kda) continue
      const t = by.get(p.name) ?? { k: 0, d: 0, a: 0, gpm: 0, maps: 0 }
      by.set(p.name, { k: t.k + p.kda[0], d: t.d + p.kda[1], a: t.a + p.kda[2], gpm: t.gpm + (p.gpm ?? 0), maps: t.maps + 1 })
    }
  }
  const best = [...by.entries()].sort(([, x], [, y]) => y.k - x.k || x.d - y.d)[0]
  return best ? { name: best[0], kda: `${best[1].k}/${best[1].d}/${best[1].a}`, maps: best[1].maps, gpm: Math.round(best[1].gpm / best[1].maps) } : null
}

/** A map's best player, as the series' is picked: "watson 19/5/14, Anti-Mage". */
export function mapBest(map: MapDetail): string | null {
  const best = [...(map.players ?? [])].filter((p) => p.name && p.kda).sort((x, y) => (y.kda?.[0] ?? 0) - (x.kda?.[0] ?? 0) || (x.kda?.[1] ?? 0) - (y.kda?.[1] ?? 0))[0]
  return best ? `${best.name} ${best.kda?.join('/')}, ${best.as}` : null
}

/** A ban of a hero someone on the other side played earlier in the series, with how that went: "watson's
 * 14/0/14 in map 3" (the latest such map). */
export function banNote(maps: MapDetail[], map: MapDetail, turn: DraftTurn): string | null {
  if (turn.pick) return null
  const earlier = maps.filter((m) => m.n < map.n).sort((a, b) => b.n - a.n)
  for (const m of earlier) {
    const p = (m.players ?? []).find((q) => q.as === turn.hero && q.team !== turn.team)
    if (p && p.name) return `${p.name}'s ${p.kda ? p.kda.join('/') : p.as} in map ${m.n}`
  }
  return null
}

/** Heroes banned in every map with a draft, once there are two. */
export function everyMapBans(maps: MapDetail[]): string[] {
  const drafted = maps.filter((m) => (m.draft ?? []).length)
  if (drafted.length < 2) return []
  const [first, ...rest] = drafted.map((m) => new Set((m.draft ?? []).filter((d) => !d.pick).map((d) => d.hero)))
  return [...first].filter((hero) => rest.every((s) => s.has(hero))).sort()
}

/** A draft's turns in runs of bans and picks, as captains make them ("Bans · 7", "Picks · 2"), each turn
 * with its place in the order. */
export function draftRounds(draft: DraftTurn[]): { pick: boolean; turns: { turn: DraftTurn; order: number }[] }[] {
  const rounds: { pick: boolean; turns: { turn: DraftTurn; order: number }[] }[] = []
  draft.forEach((turn, i) => {
    const last = rounds[rounds.length - 1]
    if (last && last.pick === turn.pick) last.turns.push({ turn, order: i + 1 })
    else rounds.push({ pick: turn.pick, turns: [{ turn, order: i + 1 }] })
  })
  return rounds
}

/** Each of a team's players with their hero in every map (null where they didn't play it), in the order
 * they first appear. */
export function whoPlayed(maps: MapDetail[], team: string): { name: string; heroes: (string | null)[] }[] {
  const names: string[] = []
  for (const m of maps) for (const p of m.players ?? []) if (p.team === team && p.name && !names.includes(p.name)) names.push(p.name)
  return names.map((name) => ({ name, heroes: maps.map((m) => (m.players ?? []).find((p) => p.name === name)?.as ?? null) }))
}

/** A player across the series' maps: each map's hero and line, whether their side took it, and their totals. */
export function acrossMaps(maps: MapDetail[], name: string): { maps: { n: number; as: string; line: string; won: boolean }[]; kda: string | null; note: string | null } {
  const played = maps.flatMap((m) => {
    const p = (m.players ?? []).find((q) => q.name === name)
    return p ? [{ m, p }] : []
  })
  const sum = played.reduce((t, { p }) => (p.kda ? [t[0] + p.kda[0], t[1] + p.kda[1], t[2] + p.kda[2]] : t), [0, 0, 0])
  const last = played[played.length - 1]
  return {
    maps: played.map(({ m, p }) => ({ n: m.n, as: p.as ?? '', line: p.numbers[0] ?? '', won: m.winner === p.team })),
    kda: played.some(({ p }) => p.kda) ? sum.join('/') : null,
    note: last?.p.note ? `Map ${last.m.n}: ${last.p.note}` : null,
  }
}

/** Whether a name is one of the fan's players in the game (its match page's "yours"). */
export function isYours(name: string, yours: string[] | undefined): boolean {
  const n = name.trim().toLowerCase()
  return !!n && (yours ?? []).some((y) => y.trim().toLowerCase() === n)
}

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

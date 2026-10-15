import './esports.css'
import type { BoardProps, SportView } from './view'

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

const place = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`
const points = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1))
const FILLER = /^(team|esports?|gaming|official|club|the)$/i

/** A team's name short enough for a chip: "GodLike Esports" → "GodLike", "Team SouL" → "SouL". */
function shortTeam(name: string): string {
  const words = name.split(/\s+/).filter((w) => w && !FILLER.test(w))
  return (words[0] ?? name).slice(0, 10)
}

/**
 * A battle royale lobby: the top of the standings (and the followed player's team, wherever it is),
 * and one chip a game with who won it.
 */
function LobbyBoard({ state, clock, teams, compact }: BoardProps<BattleRoyaleState>) {
  const ours = (name: string) => (teams ?? []).some((t) => t.toLowerCase() === name.toLowerCase())
  const top = state.standings.slice(0, compact ? 3 : 5)
  const mine = state.standings.find((t) => ours(t.name))
  const rows = mine && !top.includes(mine) ? [...top, mine] : top
  const tag = (id?: string | null) => shortTeam(state.standings.find((s) => s.id === id)?.name ?? '')
  return (
    <div className="stack" style={{ gap: 12 }}>
      {clock && <span className="num clock">{clock}</span>}
      <table className="linescore lobby">
        <thead><tr><th>#</th><th>Team</th><th title={`Games won (${state.win_word ?? 'wins'})`}>Won</th><th title="Finishes">Fin.</th><th>Pts</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className={ours(t.name) ? 'ours' : undefined}>
              <td>{place(t.place)}</td><td className="who">{t.name}</td><td>{t.wins}</td><td>{t.kills}</td><td className="total">{points(t.points)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!compact && state.games.length > 0 && (
        <div className="maps" aria-label="Matches">
          {state.games.map((g) => (
            <span key={g.n} className="map" title={g.map ?? undefined}>
              M{g.n}{g.winner ? <span className="num">{tag(g.winner)}</span> : ''}
            </span>
          ))}
        </div>
      )}
      {state.game && <span className="live-note">{state.game}</span>}
    </div>
  )
}

/** Inside the Dota 2 map being played (its number and clock are under the score): kills, in the board's
 * team order, and who leads on gold once it's a real lead. */
function DotaMapLine({ live, order }: { live: DotaLiveMap; order: string[] }) {
  const [a, b] = order[0] === live.dire.id ? [live.dire, live.radiant] : [live.radiant, live.dire]
  const leader = live.gold_lead.id === live.radiant.id ? live.radiant : live.dire
  return (
    <div className="row small" style={{ justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }} aria-label={`Map ${live.n}: kills and gold`}>
      <span>Kills <b className="num">{a.kills}–{b.kills}</b></span>
      {live.gold_lead.amount >= 1000 && <span className="muted">{leader.name} +{(live.gold_lead.amount / 1000).toFixed(1)}k gold</span>}
    </div>
  )
}

/** A series: the two teams and maps won, the map on now, and one chip a map with who took it. */
function SeriesBoard({ state, clock, compact }: BoardProps<EsportsState>) {
  const [a, b] = state.teams
  const side = (t: EsportsTeam, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title">{t.name}</span>
      {t.abbr && t.abbr !== t.name && <span className="tiny muted">{t.abbr}</span>}
    </div>
  )
  const tag = (id?: string | null) => state.teams.find((t) => t.id === id)?.abbr || state.teams.find((t) => t.id === id)?.name
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
        {side(a, 'left')}
        <div className="stack" style={{ alignItems: 'center' }}>
          <span className="num score">{a.score}–{b.score}</span>
          {clock && <span className="num clock">{clock}</span>}
        </div>
        {side(b, 'right')}
      </div>
      {!compact && state.maps.length > 0 && (
        <div className="maps" aria-label="Maps">
          {state.maps.map((m) => (
            <span key={m.n} className={`map${m.status === 'running' ? ' running' : ''}`}>
              Map {m.n}{m.winner ? <span className="num">{tag(m.winner)}</span> : m.status === 'running' ? ' · now' : ''}
            </span>
          ))}
        </div>
      )}
      {!compact && state.dota?.live && state.dota.live.clock !== 'draft' && <DotaMapLine live={state.dota.live} order={state.teams.map((t) => t.id)} />}
      {state.game && <span className="live-note">{state.game}</span>}
    </div>
  )
}

function EsportsBoard(props: BoardProps<EsportsState | BattleRoyaleState>) {
  return props.state.kind === 'battle_royale' ? <LobbyBoard {...(props as BoardProps<BattleRoyaleState>)} /> : <SeriesBoard {...(props as BoardProps<EsportsState>)} />
}

export const esports: SportView = {
  key: 'esports',
  kinds: ['esports', 'battle_royale'],
  Board: EsportsBoard as SportView['Board'],
  draws: ((state: EsportsState | BattleRoyaleState) => state.kind === 'battle_royale' || state.teams.length === 2) as SportView['draws'],
  // A game map from above: three lanes between two bases, the river across the middle, and the jungle's paths.
  backdrop: (
    <>
      <rect x="5" y="5" width="90" height="90" rx="8" />
      <path d="M14 82V18a4 4 0 0 1 4-4h64M18 86h64a4 4 0 0 0 4-4V18M17 83 83 17" />
      <path d="M5 66c22-3 36-14 38-28s14-28 52-33" strokeDasharray="3 3" />
      <path d="M28 70c6-10 14-12 22-10M50 40c8 2 16 0 22-10M30 42c4-8 10-12 16-12M54 70c6 0 12-4 16-12" />
      <circle cx="14" cy="86" r="6" />
      <circle cx="86" cy="14" r="6" />
    </>
  ),
  noLine: null, // no player lines: the team's result says it
  inHeadline: ['Place', 'Points'],
}

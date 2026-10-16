import './esports.css'
import { dotaLine, esports as facts, isLobby, lobbyRows, place, points, shortTeam, type BattleRoyaleState, type DotaLiveMap, type EsportsPage, type EsportsState, type EsportsTeam } from './facts/esports'
import { MatchSection } from './section'
import type { BoardProps, MatchProps, SportView } from './view'

/**
 * A battle royale lobby: the top of the standings (and the followed player's team, wherever it is),
 * and one chip a game with who won it.
 */
function LobbyBoard({ state, clock, teams, compact }: BoardProps<BattleRoyaleState>) {
  const tag = (id?: string | null) => shortTeam(state.standings.find((s) => s.id === id)?.name ?? '')
  return (
    <div className="stack" style={{ gap: 12 }}>
      {clock && <span className="num clock">{clock}</span>}
      <table className="linescore lobby">
        <thead><tr><th>#</th><th>Team</th><th title={`Games won (${state.win_word ?? 'wins'})`}>Won</th><th title="Finishes">Fin.</th><th>Pts</th></tr></thead>
        <tbody>
          {lobbyRows(state, teams, compact).map(({ team: t, ours }) => (
            <tr key={t.id} className={ours ? 'ours' : undefined}>
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
  const line = dotaLine(live, order)
  return (
    <div className="row small" style={{ justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }} aria-label={`Map ${live.n}: kills and gold`}>
      <span>Kills <b className="num">{line.kills}</b></span>
      {line.gold && <span className="muted">{line.gold}</span>}
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
  return isLobby(props.state) ? <LobbyBoard {...(props as BoardProps<BattleRoyaleState>)} /> : <SeriesBoard {...(props as BoardProps<EsportsState>)} />
}

/** A match page's part: a series map by map (who took each, the one being played, a Dota 2 map's best
 * numbers), or a lobby's whole standings and who won each of its matches. */
function EsportsMatch({ page }: MatchProps<EsportsPage>) {
  if (page.standings?.length) {
    const won = (page.games ?? []).filter((g) => g.winner)
    return (
      <MatchSection title="Standings" note={`${page.standings.length} teams`}>
        <div className="card card-pad stack" style={{ gap: 12 }}>
          <table className="linescore lobby">
            <thead><tr><th>#</th><th>Team</th><th title="Matches won">Won</th><th title="Finishes">Fin.</th><th>Pts</th></tr></thead>
            <tbody>
              {page.standings.map((t) => (
                <tr key={`${t.place}-${t.name}`}><td>{place(t.place)}</td><td className="who">{t.name}</td><td>{t.wins}</td><td>{t.kills}</td><td className="total">{points(t.points)}</td></tr>
              ))}
            </tbody>
          </table>
          {won.length > 0 && <span className="small muted">{won.map((g) => `M${g.n}${g.map ? ` (${g.map})` : ''}: ${g.winner}`).join(' · ')}</span>}
        </div>
      </MatchSection>
    )
  }
  if (!page.maps?.length) return null
  return (
    <MatchSection title="Maps">
      <ol className="card list">
        {page.maps.map((m) => (
          <li key={m.n} className="row">
            <span className="stack">
              <span className="title">Map {m.n}</span>
              {m.best && <span className="small muted">{m.best}</span>}
            </span>
            {m.winner ? <span className="title" style={{ textAlign: 'right' }}>{m.winner}</span> : m.status === 'running' ? <span className="badge-live">Now</span> : <span className="small muted">To play</span>}
          </li>
        ))}
      </ol>
    </MatchSection>
  )
}

export const esports: SportView = { ...facts, Board: EsportsBoard as SportView['Board'], Match: EsportsMatch as SportView['Match'] }

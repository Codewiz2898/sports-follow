import { useState, type KeyboardEvent, type PointerEvent } from 'react'
import './esports.css'
import {
  acrossMaps, amount, banNote, dotaLine, mapBest, draftRounds, esports as facts, everyMapBans, isLobby, isYours, LANES, laneWords, leadLine, leadOf, liveLeadOf,
  lobbyRows, lobbyWinners, place, points, seriesBest, shortTeam, towersLeft, whoPlayed, type BattleRoyaleState, type DotaLiveMap, type EsportsPage, type EsportsState,
  type EsportsTeam, type LaneBuildings, type LiveDetail, type MapDetail, type MapMark, type PageTeam, type Reading,
} from './facts/esports'
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

/** A map's lead line, small, on the series' one scale so the maps compare: up is the first team's. */
function Spark({ readings, top, label }: { readings: Reading[]; top: number; label: string }) {
  const line = leadLine(readings, 92, 32, 3, top)
  return (
    <svg className="spark" width="92" height="32" viewBox="0 0 92 32" role="img" aria-label={label}>
      <line x1="0" y1={line.mid} x2="92" y2={line.mid} className="zero" />
      <polyline points={line.points} className="line" />
    </svg>
  )
}

/** Who leads, in a few words: "Yandex +4.4k", "Level". */
function leader(v: number, [a, b]: PageTeam[]): string {
  return Math.round(v) === 0 ? 'Level' : `${v > 0 ? a.short : b.short} +${amount(v)}`
}

/** A map's lead over time (gold, in Dota 2), up for the first team: a finished map's minute by minute, each
 * mark (a Roshan) on it in its taker's colour, or the map being played's so far. A pointer or the arrow
 * keys read any reading. */
function LeadGraph({ id, title, what, readings, marks = [], teams }: { id: string; title: string; what: string; readings: Reading[]; marks?: MapMark[]; teams: PageTeam[] }) {
  const [a, b] = teams
  const [at, setAt] = useState<number | null>(null)
  const W = 354
  const H = 128
  const line = leadLine(readings, W, H, 8)
  const last = readings.length - 1
  const minute = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const t = ((((e.clientX - box.left) / box.width) * W - 8) / (W - 16)) * line.end
    setAt(readings.reduce((best, [rt], i) => (Math.abs(rt - t) < Math.abs(readings[best][0] - t) ? i : best), 0))
  }
  const key = (e: KeyboardEvent<SVGSVGElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    setAt((i) => Math.max(0, Math.min(last, (i ?? last) + (e.key === 'ArrowLeft' ? -1 : 1))))
  }
  const most = (sign: number) => readings.reduce((best, [, v], i) => (sign * v > sign * readings[best][1] ? i : best), 0)
  const [up, down] = [readings[most(1)], readings[most(-1)]]
  const summary = [
    up[1] > 0 ? `${a.short} up most, ${amount(up[1])}, at minute ${Math.round(up[0] / 60)}` : `${a.short} never ahead`,
    down[1] < 0 ? `${b.short} up most, ${amount(down[1])}, at minute ${Math.round(down[0] / 60)}` : `${b.short} never ahead`,
    `at the last reading ${leader(readings[last][1], teams)}`,
  ].join('; ')
  const ticks = Array.from({ length: Math.floor(line.end / 600) + 1 }, (_, i) => i * 600)
  const [t, v] = readings[at ?? last]
  const area = `${line.points} ${line.x(readings[last][0]).toFixed(1)},${line.mid} ${line.x(readings[0][0]).toFixed(1)},${line.mid}`
  return (
    <div className="card card-pad stack lead-graph" style={{ gap: 10 }}>
      <span className="row">
        <span className="eyebrow">{title}</span>
        <span className="small" style={{ fontWeight: 800 }} aria-live="polite">{Math.round(t / 60)}′ · {leader(v, teams)}</span>
      </span>
      <svg viewBox={`0 0 ${W} ${H + 46}`} role="img" aria-label={`${a.short}'s ${what} lead, ${title.toLowerCase()}: ${summary}.`} tabIndex={0}
        onPointerMove={minute} onPointerDown={minute} onPointerLeave={() => setAt(null)} onKeyDown={key} onBlur={() => setAt(null)}>
        <defs>
          <clipPath id={`up-${id}`}><rect x="0" y="0" width={W} height={line.mid} /></clipPath>
          <clipPath id={`down-${id}`}><rect x="0" y={line.mid} width={W} height={H} /></clipPath>
        </defs>
        <text x="8" y="11" className="axis">{a.short} ahead</text>
        <g transform="translate(0 16)">
          <polygon points={area} clipPath={`url(#up-${id})`} className="fill up" />
          <polygon points={area} clipPath={`url(#down-${id})`} className="fill down" />
          <line x1="4" y1={line.mid} x2={W - 4} y2={line.mid} className="zero" />
          <polyline points={line.points} className="line" />
          {at !== null && <line x1={line.x(t)} y1="0" x2={line.x(t)} y2={H} className="cross" />}
          {marks.map((m) => (
            <g key={`${m.t}-${m.team}`} className={m.team === a.id ? 'mark first' : 'mark'}>
              <circle cx={line.x(m.t)} cy={line.y(line.at(m.t))} r="8" />
              <text x={line.x(m.t)} y={line.y(line.at(m.t)) + 3.5} textAnchor="middle">{m.label}</text>
            </g>
          ))}
        </g>
        <text x="8" y={H + 28} className="axis">{b.short} ahead</text>
        {ticks.map((s) => <text key={s} x={line.x(s)} y={H + 44} textAnchor={s === 0 ? 'start' : 'middle'} className="axis">{s / 60}′</text>)}
      </svg>
      {marks.length > 0 && (
        <span className="row small muted" style={{ justifyContent: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
          {teams.map((team, i) => marks.some((m) => m.team === team.id) && (
            <span key={team.id} className="row" style={{ gap: 6 }}><span className={i === 0 ? 'mark-key first' : 'mark-key'} aria-hidden="true">{marks[0].label}</span>{marks[0].name}, {team.short}</span>
          ))}
        </span>
      )}
    </div>
  )
}

/** One side's buildings in a lane, from its base out to the river: its barracks, then T3, T2, T1 (or the
 * other way round, for the side on the right). */
function LaneSide({ lane, right }: { lane?: LaneBuildings; right?: boolean }) {
  if (!lane) return <span className="small muted" style={{ textAlign: right ? 'left' : 'right' }}>–</span>
  const rax = <span className="rax">{lane.barracks.map((up, i) => <span key={i} className={up ? 'pip box' : 'pip box down'} />)}</span>
  const towers = [...lane.towers].reverse().map((up, i) => <span key={i} className={up ? 'pip' : 'pip down'} />)
  return <span className={right ? 'lane-side right' : 'lane-side'}>{right ? <>{[...towers].reverse()}{rax}</> : <>{rax}{towers}</>}</span>
}

/** The map being played: its clock, kills and gold lead (the board's, as fresh as the polls), its gold line
 * so far, which towers and barracks stand in each lane, and all ten heroes, the fan's players marked. */
function LiveMap({ live, board, teams, yours }: { live: LiveDetail; board?: DotaLiveMap | null; teams: PageTeam[]; yours?: string[] }) {
  const [a, b] = teams
  const now = board && board.n === live.n ? board : null
  const side = (id: string) => (now ? (now.radiant.id === id ? now.radiant : now.dire) : null)
  const gold = now ? (now.gold_lead.amount >= 1000 ? `${teams.find((t) => t.id === now.gold_lead.id)?.short ?? ''} up ${amount(now.gold_lead.amount)} gold` : 'Level on gold') : null
  const readings = liveLeadOf(live, a.id)
  const built = live.buildings
  return (
    <>
      <MatchSection title={`Map ${live.n} · now`} note={now?.clock ?? null}>
        <div className="card card-pad stack" style={{ gap: 12 }}>
          {now && (
            <div className="totals">
              <span className="dsp muted">{a.short}</span><span /><span className="dsp muted" style={{ textAlign: 'right' }}>{b.short}</span>
              <span className="num">{side(a.id)?.kills ?? '–'}</span><span className="eyebrow">Kills</span><span className="num" style={{ textAlign: 'right' }}>{side(b.id)?.kills ?? '–'}</span>
            </div>
          )}
          {gold && <span className="dsp" style={{ fontSize: 26 }}>{gold}</span>}
          {built && (
            <div className="stack lanes" style={{ gap: 10 }}>
              <span className="row"><span className="eyebrow">Towers and barracks</span><span className="tiny muted">Towers left: {a.short} {towersLeft(built[a.id])} · {b.short} {towersLeft(built[b.id])}</span></span>
              {LANES.map((lane) => (
                <div key={lane} className="lane" role="img" aria-label={laneWords(lane, [{ short: a.short, lanes: built[a.id] }, { short: b.short, lanes: built[b.id] }])}>
                  <LaneSide lane={built[a.id]?.[lane]} />
                  <span className="eyebrow" aria-hidden="true">{lane}</span>
                  <LaneSide lane={built[b.id]?.[lane]} right />
                </div>
              ))}
              <span className="tiny muted">Barracks at each end, then towers out to the river. Hollow: fallen.</span>
            </div>
          )}
        </div>
      </MatchSection>
      {readings.length > 1 && <LeadGraph id="live" title={`Map ${live.n}, gold so far`} what={live.lead?.what ?? 'lead'} readings={readings} teams={teams} />}
      {(live.heroes ?? []).length > 0 && (
        <MatchSection title={`Heroes on map ${live.n}`}>
          <div className="card heroes">
            {teams.map((t) => (
              <div key={t.id} className="stack" style={{ gap: 2 }}>
                <span className="dsp muted team">{t.short}</span>
                {(live.heroes ?? []).filter((h) => h.team === t.id).map((h) => (
                  <span key={`${h.name}-${h.as}`} className={isYours(h.name, yours) ? 'hero-row yours' : 'hero-row'}>
                    <span className="dsp">{h.name || h.as}</span>
                    <span className="tiny muted">{h.as}{isYours(h.name, yours) ? ' · you follow' : ''}</span>
                  </span>
                ))}
              </div>
            ))}
          </div>
        </MatchSection>
      )}
    </>
  )
}

const escaped = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The fan's players' names as whole words to find in a line ("Aegis to Yatoro"). */
function names(yours?: string[]): RegExp[] {
  return (yours ?? []).map((y) => y.trim()).filter(Boolean).map((y) => new RegExp(`(^|[^\\p{L}\\p{N}_])${escaped(y)}($|[^\\p{L}\\p{N}_])`, 'iu'))
}

/** One finished map: who took it and both sides' totals, its lead by minute, how it went, every player with
 * their items, and its draft. */
function MapView({ maps, map, teams, yours }: { maps: MapDetail[]; map: MapDetail; teams: PageTeam[]; yours?: string[] }) {
  const [a, b] = teams
  const byId = (id?: string | null) => teams.find((t) => t.id === id)
  const worth = (map.totals ?? []).find((t) => t.label === 'Net worth')?.values ?? {}
  const rounds = draftRounds(map.draft ?? [])
  const first = map.draft?.find((d) => d.pick)
  return (
    <>
      <div className="card card-pad stack" style={{ gap: 12 }}>
        <span className="row">
          <span className="dsp" style={{ fontSize: 26 }}>{byId(map.winner) ? `${byId(map.winner)?.short} take map ${map.n}` : `Map ${map.n}`}</span>
          {map.length && <span className="num" style={{ fontSize: 20 }}>{map.length}</span>}
        </span>
        {(map.totals ?? []).length > 0 && (
          <div className="totals">
            <span className="dsp muted">{a.short}</span><span /><span className="dsp muted" style={{ textAlign: 'right' }}>{b.short}</span>
            {(map.totals ?? []).map((t) => (
              <span key={t.label} style={{ display: 'contents' }}>
                <span className="num">{t.values[a.id] ?? '–'}</span><span className="eyebrow">{t.label}</span><span className="num" style={{ textAlign: 'right' }}>{t.values[b.id] ?? '–'}</span>
              </span>
            ))}
          </div>
        )}
      </div>
      {(map.lead?.by_minute ?? []).length > 1 && <LeadGraph id={`map-${map.n}`} title={`Map ${map.n}, ${map.lead?.what ?? 'lead'} by minute`} what={map.lead?.what ?? 'lead'} readings={leadOf(map, a.id)} marks={map.marks} teams={teams} />}
      {(map.events ?? []).length > 0 && (
        <MatchSection title={`How map ${map.n} went`}>
          <ol className="card list map-moments">
            {(map.events ?? []).map((e, i) => {
              const ours = names(yours).some((y) => y.test(`${e.title} ${e.note ?? ''}`))
              return (
                <li key={`${e.t}-${i}`} className={ours ? 'yours' : undefined}>
                  <span className="num">{e.clock}</span>
                  <span className="stack">
                    <span className="title row" style={{ justifyContent: 'flex-start', gap: 8 }}>
                      {e.mark && <span className={e.team === a.id ? 'mark-key first' : 'mark-key'} aria-hidden="true">{e.mark}</span>}
                      {e.title}
                    </span>
                    {e.note && <span className="small muted">{e.note}{ours ? ' · you follow' : ''}</span>}
                  </span>
                </li>
              )
            })}
          </ol>
        </MatchSection>
      )}
      {(map.players ?? []).length > 0 && (
        <MatchSection title={`Players in map ${map.n}`}>
          <div className="card map-players">
            {(map.columns ?? []).length > 0 && (
              <div className="player head" aria-hidden="true"><span />{(map.columns ?? []).map((c) => <span key={c} className="eyebrow">{c}</span>)}</div>
            )}
            {teams.map((t) => (
              <div key={t.id} className="stack" style={{ gap: 0 }}>
                <span className="team-rule"><span className="dsp">{t.short}{worth[t.id] ? ` ${worth[t.id]}` : ''}</span></span>
                {(map.players ?? []).filter((p) => p.team === t.id).map((p) => (
                  <div key={`${p.name}-${p.as}`} className={isYours(p.name, yours) ? 'player yours' : 'player'}>
                    <span className="stack" style={{ minWidth: 0 }}>
                      <span className="dsp name">{p.name || p.as}</span>
                      <span className="tiny muted">{p.as}{isYours(p.name, yours) ? ' · you follow' : ''}</span>
                    </span>
                    {p.numbers.map((n, i) => <span key={i} className="num">{n}</span>)}
                    {p.items.length > 0 && <span className="tiny muted items">{p.items.join(' · ')}</span>}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </MatchSection>
      )}
      {rounds.length > 0 && (
        <MatchSection title={`Draft · map ${map.n}`} note={first ? `${byId(first.team)?.short ?? ''} pick first` : null}>
          <div className="card draft">
            <span className="dsp">{a.short}</span><span /><span className="dsp" style={{ textAlign: 'right' }}>{b.short}</span>
            {rounds.map((r, ri) => (
              <span key={ri} style={{ display: 'contents' }}>
                <span className="eyebrow round">{r.pick ? 'Picks' : 'Bans'} · {r.turns.length}</span>
                {r.turns.map(({ turn, order }) => {
                  const note = banNote(maps, map, turn)
                  const cell = turn.pick ? (
                    <span className={isYours(turn.player ?? '', yours) ? 'pick yours' : 'pick'}>
                      <span className="dsp">{turn.hero}</span>
                      <span className="tiny">{turn.player}{isYours(turn.player ?? '', yours) ? ' · you follow' : ''}</span>
                    </span>
                  ) : (
                    <span className="ban"><s>{turn.hero}</s>{note && <span className="tiny">{note}</span>}</span>
                  )
                  return (
                    <span key={order} style={{ display: 'contents' }}>
                      <span className="side">{turn.team === a.id ? cell : null}</span>
                      <span className="num order">{order}</span>
                      <span className="side right">{turn.team === b.id ? cell : null}</span>
                    </span>
                  )
                })}
              </span>
            ))}
          </div>
        </MatchSection>
      )}
    </>
  )
}

/** The series' finished maps in depth (what a source told the page: Dota 2's): its best player, a tab a map,
 * the fan's players across the maps, and who played what. */
function SeriesDetail({ maps, teams, yours, live }: { maps: MapDetail[]; teams: PageTeam[]; yours?: string[]; live: boolean }) {
  const [picked, setPicked] = useState<number | null>(null)
  const map = maps.find((m) => m.n === picked) ?? maps[maps.length - 1]
  const best = seriesBest(maps)
  const bans = everyMapBans(maps)
  const followed = [...new Set(maps.flatMap((m) => (m.players ?? []).map((p) => p.name)))].filter((n) => isYours(n, yours))
  return (
    <>
      {best && (
        <div className="best-card">
          <span className="stack"><span className="eyebrow">{live ? 'Best so far' : 'Best of the series'}</span><span className="dsp" style={{ fontSize: 26 }}>{best.name}</span></span>
          <span className="stack" style={{ alignItems: 'flex-end' }}><span className="num" style={{ fontSize: 24 }}>{best.kda}</span><span className="tiny">over {best.maps} map{best.maps === 1 ? '' : 's'}{best.gpm ? ` · ${best.gpm} GPM` : ''}</span></span>
        </div>
      )}
      {maps.length > 1 && (
        <div className="tabs" role="tablist" aria-label="Maps">
          {maps.map((m) => <button key={m.n} type="button" role="tab" aria-selected={m.n === map.n} onClick={() => setPicked(m.n)}>Map {m.n}</button>)}
        </div>
      )}
      <MapView maps={maps} map={map} teams={teams} yours={yours} />
      {followed.map((name) => {
        const across = acrossMaps(maps, name)
        return (
          <MatchSection key={name} title={`${name}, who you follow`}>
            <div className="card card-pad stack" style={{ gap: 12 }}>
              <span className="row"><span className="dsp" style={{ fontSize: 28 }}>{name}</span>{across.kda && <span className="stack" style={{ alignItems: 'flex-end' }}><span className="num" style={{ fontSize: 22 }}>{across.kda}</span><span className="tiny muted">over {across.maps.length} map{across.maps.length === 1 ? '' : 's'}</span></span>}</span>
              <div className="across">
                {across.maps.map((m) => (
                  <span key={m.n} className={m.won ? 'won' : undefined}><span className="tiny">Map {m.n}{m.won ? ' · won' : ''}</span><span className="small" style={{ fontWeight: 800 }}>{m.as}</span><span className="num">{m.line}</span></span>
                ))}
              </div>
              {across.note && <span className="small muted">{across.note}</span>}
            </div>
          </MatchSection>
        )
      })}
      {bans.length > 0 && (
        <div className="card card-pad stack" style={{ gap: 4 }}>
          <span className="eyebrow">Banned in every map</span>
          <span style={{ fontWeight: 800 }}>{bans.join(' · ')}</span>
        </div>
      )}
      {maps.some((m) => (m.players ?? []).length) && (
        <MatchSection title="Who played what">
          <div className="card who-played" style={{ gridTemplateColumns: `repeat(${maps.length}, minmax(0, 1fr))` }}>
            {teams.map((t, ti) => (
              <span key={t.id} style={{ display: 'contents' }}>
                <span className={ti ? 'dsp team next' : 'dsp team'}>{t.short}</span>
                {maps.map((m) => <span key={m.n} className="eyebrow head">Map {m.n}{m.winner === t.id ? ' · W' : ''}</span>)}
                {whoPlayed(maps, t.id).map((row) => {
                  const mine = isYours(row.name, yours)
                  return (
                    <span key={row.name} style={{ display: 'contents' }}>
                      <span className={mine ? 'who yours' : 'who'}><span className="dsp">{row.name}</span>{mine && <span className="tiny muted"> · you follow</span>}</span>
                      {row.heroes.map((h, i) => <span key={i} className={mine ? 'played yours' : 'played'}>{h ?? '–'}</span>)}
                    </span>
                  )
                })}
              </span>
            ))}
          </div>
        </MatchSection>
      )}
    </>
  )
}

/** A match page's part: a series map by map (who took each, the one being played, a Dota 2 map's best
 * numbers, and where its source told the page more, each finished map's lead line and the maps in depth), or a
 * lobby's whole standings and who won each of its matches. */
function EsportsMatch({ page, live, yours }: MatchProps<EsportsPage>) {
  if (page.standings?.length) {
    const won = lobbyWinners(page)
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
          {won ? <span className="small muted">{won}</span> : null}
        </div>
      </MatchSection>
    )
  }
  if (!page.maps?.length) return null
  const teams = page.teams?.length === 2 ? page.teams : null
  const detail = teams ? (page.detail?.maps ?? []) : []
  const board = (live?.state as unknown as EsportsState | undefined)?.dota?.live
  // The map being played, while the board says it still is (a read can be a minute behind a map's end).
  const now = teams && live?.is_live && board && board.n === page.detail?.live?.n ? page.detail.live : null
  const told = new Map(detail.map((m) => [m.n, m]))
  const top = Math.max(2000, ...detail.flatMap((m) => (m.lead?.by_minute ?? []).map((v) => Math.abs(v))))
  return (
    <>
      {now && teams && <LiveMap live={now} board={board} teams={teams} yours={yours} />}
      <MatchSection title="Maps" note={teams && detail.some((m) => m.lead) ? `${teams[0].short} up, ${teams[1].short} down` : null}>
        <ol className="card list">
          {page.maps.map((m) => {
            const d = told.get(m.n)
            const values = d && teams ? leadOf(d, teams[0].id) : []
            const end = values[values.length - 1]?.[1] ?? 0
            return (
              <li key={m.n} className={values.length > 1 ? 'map-row' : 'row'}>
                <span className="stack">
                  <span className="title">Map {m.n}</span>
                  {values.length <= 1 && m.best && <span className="small muted">{m.best}</span>}
                  {values.length > 1 && m.winner && <span className="dsp" style={{ fontSize: 18 }}>{shortTeam(m.winner)}</span>}
                </span>
                {values.length > 1 && teams && <Spark readings={values} top={top} label={`Map ${m.n}: ${leader(end, teams)} at the end`} />}
                {values.length > 1 ? (
                  <span className="stack" style={{ alignItems: 'flex-end', textAlign: 'right', minWidth: 0 }}>
                    {d?.length && <span className="num" style={{ fontSize: 15 }}>{d.length}</span>}
                    {(m.best || (d && mapBest(d))) && <span className="tiny muted">{m.best || (d && mapBest(d))}</span>}
                  </span>
                ) : m.winner ? <span className="title" style={{ textAlign: 'right' }}>{m.winner}</span> : m.status === 'running' ? <span className="badge-live">Now</span> : <span className="small muted">To play</span>}
              </li>
            )
          })}
        </ol>
      </MatchSection>
      {teams && detail.length > 0 && <SeriesDetail maps={detail} teams={teams} yours={yours} live={!!live?.is_live} />}
    </>
  )
}

export const esports: SportView = { ...facts, Board: EsportsBoard as SportView['Board'], Match: EsportsMatch as SportView['Match'] }

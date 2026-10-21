import { useState } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View, type GestureResponderEvent } from 'react-native'
import Svg, { ClipPath, Defs, Line, Polygon, Polyline, Rect } from 'react-native-svg'
import {
  acrossMaps, banNote, boardSide, dotaLine, draftRounds, esports as facts, everyMapBans, followedIn, isLobby, isYours, LANES, laneWords, leader, leadLine, leadOf,
  leadSummary, liveGold, liveLeadOf, liveNow, lobbyRows, lobbyWinners, mapBest, namesYours, nearest, place, points, seriesBest, shortTeam, sparkTop, towersLeft,
  whoPlayed, type BattleRoyaleState, type DotaLiveMap, type DraftTurn, type EsportsPage, type EsportsState, type EsportsTeam, type LaneBuildings, type LiveDetail,
  type MapDetail, type MapMark, type PageTeam, type Reading,
} from '../../../web/src/sports/facts/esports'
import type { Tone } from '../catalog'
import { colour, dsp, dspInk, font, num, t } from '../theme'
import { alpha, Card, Dsp, Eyebrow, ListCard, LiveBadge, s as ui } from '../ui'
import { Clock, INK, LineRow, LOBBY_COLUMNS, Muted, Note, Score, Sides, Title, type Board, type BoardProps, type MatchPart, type MatchProps, type Palette } from './board'
import { MatchSection } from './section'

/** One chip a map or match, the winner's tag in it; the one being played is happening now (esports.css). */
function MapChip({ label, winner, running, pal }: { label: string; winner?: string | null; running?: boolean; pal: Palette }) {
  return (
    <View style={[{ flexDirection: 'row', alignItems: 'baseline', gap: 6, paddingVertical: 5, paddingHorizontal: 9, borderRadius: 8, backgroundColor: pal.raised }, running && { borderWidth: 2, borderColor: colour.live }]}>
      <Text style={{ fontFamily: font[800], fontSize: 12, color: running ? pal.text : pal.muted }}>{label}</Text>
      {winner ? <Text style={[num(15), { color: pal.text }]}>{winner}</Text> : null}
    </View>
  )
}

/** A battle royale lobby: the top of the standings (and the followed player's team, wherever it is), and
 * one chip a game with who won it. */
function LobbyBoard({ state, clock, teams, compact, pal }: BoardProps<BattleRoyaleState>) {
  const tag = (id?: string | null) => shortTeam(state.standings.find((s) => s.id === id)?.name ?? '')
  return (
    <View style={{ gap: 12 }}>
      {clock ? <Clock pal={pal}>{clock}</Clock> : null}
      <View>
        <LineRow pal={pal} head wide={1} right widths={LOBBY_COLUMNS} cells={['#', 'Team', 'Won', 'Fin.', 'Pts']} />
        {lobbyRows(state, teams, compact).map(({ team: x, ours }) => (
          <View key={x.id} style={ours && pal.wash ? { backgroundColor: pal.wash, borderRadius: 6 } : undefined}>
            <LineRow pal={ours ? { ...pal, muted: pal.ours, text: pal.ours } : pal} total wide={1} right widths={LOBBY_COLUMNS} bold={[false, true]} cells={[place(x.place), x.name, String(x.wins), String(x.kills), points(x.points)]} />
          </View>
        ))}
      </View>
      {!compact && state.games.length > 0 ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {state.games.map((g) => <MapChip key={g.n} label={`M${g.n}`} winner={g.winner ? tag(g.winner) : null} pal={pal} />)}
        </View>
      ) : null}
      {state.game ? <Note pal={pal}>{state.game}</Note> : null}
    </View>
  )
}

/** A series: the two teams and maps won, the map on now, and one chip a map with who took it. */
function SeriesBoard({ state, clock, compact, pal }: BoardProps<EsportsState>) {
  const [a, b] = state.teams
  const side = (x: EsportsTeam, align: 'left' | 'right') => (
    <>
      <Title pal={pal} style={{ textAlign: align }}>{x.name}</Title>
      {x.abbr && x.abbr !== x.name ? <Muted pal={pal}>{x.abbr}</Muted> : null}
    </>
  )
  const tag = (id?: string | null) => state.teams.find((x) => x.id === id)?.abbr || state.teams.find((x) => x.id === id)?.name
  const dota = !compact && state.dota?.live && state.dota.live.clock !== 'draft' ? dotaLine(state.dota.live, state.teams.map((x) => x.id)) : null
  return (
    <View style={{ gap: 12 }}>
      <Sides
        left={side(a, 'left')}
        middle={<><Score pal={pal}>{a.score}–{b.score}</Score>{clock ? <Clock pal={pal}>{clock}</Clock> : null}</>}
        right={side(b, 'right')}
      />
      {!compact && state.maps.length > 0 ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {state.maps.map((m) => <MapChip key={m.n} label={`Map ${m.n}${!m.winner && m.status === 'running' ? ' · now' : ''}`} winner={m.winner ? tag(m.winner) : null} running={m.status === 'running'} pal={pal} />)}
        </View>
      ) : null}
      {dota ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
          <Text style={[t.small, { color: pal.text }]}>Kills <Text style={num(15)}>{dota.kills}</Text></Text>
          {dota.gold ? <Text style={[t.small, { color: pal.muted }]}>{dota.gold}</Text> : null}
        </View>
      ) : null}
      {state.game ? <Note pal={pal}>{state.game}</Note> : null}
    </View>
  )
}

function EsportsBoard(props: BoardProps<EsportsState | BattleRoyaleState>) {
  return isLobby(props.state) ? <LobbyBoard {...(props as BoardProps<BattleRoyaleState>)} /> : <SeriesBoard {...(props as BoardProps<EsportsState>)} />
}

// ---------------------------------------------------------------- a series map by map, in depth (esports.css)
// The first team's side is up and wears the sport's colour; the second's is down and ink. The fan's players are
// tinted with the sport's colour.

/** The sport's colour, faint, behind the fan's players (the web's color-mix of 13% on the card). */
const tint = (tone: Tone) => alpha(tone.sport, 0.13)

/** A map's lead line, small, on the series' one scale so the maps compare: up is the first team's (.spark). */
function Spark({ readings, top, label }: { readings: Reading[]; top: number; label: string }) {
  const line = leadLine(readings, 92, 32, 3, top)
  return (
    <View accessible accessibilityRole="image" accessibilityLabel={label}>
      <Svg width={92} height={32}>
        <Line x1={0} y1={line.mid} x2={92} y2={line.mid} stroke={colour.line} strokeWidth={1.5} strokeDasharray="4 4" />
        <Polyline points={line.points} fill="none" stroke={colour.text} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
      </Svg>
    </View>
  )
}

/** A mark's letter in its taker's colour (.mark-key): the first team's in the sport's, the second's in ink. */
function MarkKey({ label, first, tone }: { label: string; first: boolean; tone: Tone }) {
  return (
    <View style={[s.markKey, { backgroundColor: first ? tone.sport : colour.text }]} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
      <Text style={[s.markText, { color: first ? tone.on : colour.surface }]}>{label}</Text>
    </View>
  )
}

/** Both sides' numbers a line each, the label between them (.totals). */
function Totals({ teams, rows }: { teams: PageTeam[]; rows: { label: string; values: Record<string, string> }[] }) {
  const [a, b] = teams
  return (
    <View>
      <View style={s.totalsRow}>
        <Dsp size={16} color={colour.muted} style={s.totalsHead}>{a.short}</Dsp>
        <Dsp size={16} color={colour.muted} style={[s.totalsHead, { textAlign: 'right' }]}>{b.short}</Dsp>
      </View>
      {rows.map((r) => (
        <View key={r.label} style={[s.totalsRow, s.rule]}>
          <Text style={[num(22), s.totalsNum]}>{r.values[a.id] ?? '–'}</Text>
          <Eyebrow style={{ textAlign: 'center' }}>{r.label}</Eyebrow>
          <Text style={[num(22), s.totalsNum, { textAlign: 'right' }]}>{r.values[b.id] ?? '–'}</Text>
        </View>
      ))}
    </View>
  )
}

const GRAPH = 128 // the lead graph's height, as the web's
const PAD = 8 // its line's room at either end
// A minute's mark: the web's prime (′) isn't in Manrope, and Android measures a line with a fallback glyph in it
// short, cutting off its end ("52′ · Yandex" for "52′ · Yandex +17.5k"); an apostrophe, as football's minutes.
const MINUTE = "'"

/** Where a minute's tick sits under the graph: the first from the left edge, the others centred on their time,
 * kept inside the graph. */
function tickAt(x: number, width: number, first: boolean) {
  return first ? { left: x } : { left: Math.max(0, Math.min(width - 40, x - 20)), width: 40, textAlign: 'center' as const }
}

/** A map's lead over time (gold, in Dota 2), up for the first team: a finished map's minute by minute, each mark
 * (a Roshan) on it in its taker's colour, or the map being played's so far. A finger on it reads any reading, back
 * to the last once it lifts or the page scrolls (as the web's pointer); a screen reader's swipe up or down steps
 * through them (the web's arrow keys). */
function LeadGraph({ title, what, readings, marks = [], teams, tone }: { title: string; what: string; readings: Reading[]; marks?: MapMark[]; teams: PageTeam[]; tone: Tone }) {
  const [a, b] = teams
  const [width, setWidth] = useState(0)
  const [at, setAt] = useState<number | null>(null)
  const line = leadLine(readings, Math.max(1, width), GRAPH, PAD)
  const last = readings.length - 1
  const touch = (e: GestureResponderEvent) => {
    if (width > 2 * PAD) setAt(nearest(readings, ((e.nativeEvent.locationX - PAD) / (width - 2 * PAD)) * line.end))
  }
  const step = (by: number) => setAt((i) => Math.max(0, Math.min(last, (i ?? last) + by)))
  const [time, value] = readings[Math.min(at ?? last, last)] // a reading stepped to on a longer line: its last
  const reading = `${Math.round(time / 60)}${MINUTE} · ${leader(value, teams)}`
  const ticks = Array.from({ length: Math.floor(line.end / 600) + 1 }, (_, i) => i * 600)
  const area = `${line.points} ${line.x(readings[last][0]).toFixed(1)},${line.mid} ${line.x(readings[0][0]).toFixed(1)},${line.mid}`
  const id = title.replace(/\W+/g, '-')
  return (
    <Card pad style={{ gap: 10 }}>
      <View style={s.row}>
        <Eyebrow style={{ flex: 1 }}>{title}</Eyebrow>
        <Text style={[t.small, { fontFamily: font[800], flexShrink: 0 }]} accessibilityLiveRegion="polite">{reading}</Text>
      </View>
      <View accessible accessibilityRole="adjustable" accessibilityLabel={`${a.short}'s ${what} lead, ${title.toLowerCase()}: ${leadSummary(readings, teams)}.`}
        accessibilityValue={{ text: reading }} accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
        onAccessibilityAction={(e) => step(e.nativeEvent.actionName === 'increment' ? 1 : -1)}>
        <Text style={s.axis}>{a.short} ahead</Text>
        {/* A sideways drag reads along the line; an upward one is the page's scroll, which takes it over. */}
        <View style={{ height: GRAPH, marginTop: 4 }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
          onStartShouldSetResponder={() => true} onResponderGrant={touch} onResponderMove={touch} onResponderTerminationRequest={() => true}
          onResponderRelease={() => setAt(null)} onResponderTerminate={() => setAt(null)}>
          {width > 0 ? (
            <View pointerEvents="none" style={StyleSheet.absoluteFill}>
              <Svg width={width} height={GRAPH}>
                <Defs>
                  <ClipPath id={`up-${id}`}><Rect x={0} y={0} width={width} height={line.mid} /></ClipPath>
                  <ClipPath id={`down-${id}`}><Rect x={0} y={line.mid} width={width} height={GRAPH} /></ClipPath>
                </Defs>
                <Polygon points={area} clipPath={`url(#up-${id})`} fill={alpha(tone.sport, 0.24)} />
                <Polygon points={area} clipPath={`url(#down-${id})`} fill="rgba(13, 13, 13, 0.1)" />
                <Line x1={4} y1={line.mid} x2={width - 4} y2={line.mid} stroke={colour.line} strokeWidth={1.5} strokeDasharray="4 4" />
                <Polyline points={line.points} fill="none" stroke={colour.text} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
                {at !== null ? <Line x1={line.x(time)} y1={0} x2={line.x(time)} y2={GRAPH} stroke={colour.muted} strokeWidth={1.5} /> : null}
              </Svg>
              {marks.map((m) => (
                <View key={`${m.t}-${m.team}`} style={[s.mark, { left: line.x(m.t) - 9, top: line.y(line.at(m.t)) - 9, backgroundColor: m.team === a.id ? tone.sport : colour.text }]}>
                  <Text style={[s.markText, { color: m.team === a.id ? tone.on : colour.surface }]}>{m.label}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </View>
        <Text style={[s.axis, { marginTop: 4 }]}>{b.short} ahead</Text>
        <View style={{ height: 16 }}>
          {width > 0 ? ticks.map((sec) => <Text key={sec} style={[s.axis, s.tick, tickAt(line.x(sec), width, sec === 0)]}>{sec / 60}{MINUTE}</Text>) : null}
        </View>
      </View>
      {marks.length > 0 ? (
        <View style={[s.row, { justifyContent: 'flex-start', gap: 14, flexWrap: 'wrap' }]}>
          {teams.map((team, i) => marks.some((m) => m.team === team.id) ? (
            <View key={team.id} style={[s.row, { gap: 6 }]}>
              <MarkKey label={marks[0].label} first={i === 0} tone={tone} />
              <Text style={[t.small, t.muted]}>{marks[0].name}, {team.short}</Text>
            </View>
          ) : null)}
        </View>
      ) : null}
    </Card>
  )
}

/** One side's buildings in a lane, from its base out to the river: its barracks, then T3, T2, T1 (or the other way
 * round, for the side on the right). Filled: standing; hollow: fallen. */
function LaneSide({ lane, right }: { lane?: LaneBuildings; right?: boolean }) {
  if (!lane) return <Text style={[t.small, t.muted, { flex: 1, textAlign: right ? 'left' : 'right' }]}>–</Text>
  const rax = <View key="rax" style={s.rax}>{lane.barracks.map((up, i) => <View key={i} style={[s.pip, s.box, !up && s.down]} />)}</View>
  const towers = lane.towers.map((up, i) => <View key={i} style={[s.pip, !up && s.down]} />) // T1, T2, T3
  return <View style={[s.laneSide, right && { justifyContent: 'flex-start' }]}>{right ? [...towers, rax] : [rax, ...[...towers].reverse()]}</View>
}

/** The map being played: its clock, kills and gold lead (the board's, as fresh as the polls), its gold line so
 * far, which towers and barracks stand in each lane, and all ten heroes, the fan's players marked. */
function LiveMap({ live, board, teams, yours, tone }: { live: LiveDetail; board?: DotaLiveMap | null; teams: PageTeam[]; yours?: string[]; tone: Tone }) {
  const [a, b] = teams
  const now = board && board.n === live.n ? board : null
  const readings = liveLeadOf(live, a.id)
  const built = live.buildings
  const heroes = live.heroes ?? []
  return (
    <>
      <MatchSection title={`Map ${live.n} · now`} note={now?.clock ?? null}>
        <Card pad style={{ gap: 12 }}>
          {now ? <Totals teams={teams} rows={[{ label: 'Kills', values: { [a.id]: String(boardSide(now, a.id).kills), [b.id]: String(boardSide(now, b.id).kills) } }]} /> : null}
          {now ? <Dsp size={26}>{liveGold(now, teams)}</Dsp> : null}
          {built ? (
            <View style={{ gap: 10 }}>
              <View style={s.row}>
                <Eyebrow>Towers and barracks</Eyebrow>
                <Text style={[t.tiny, t.muted, { flexShrink: 1, textAlign: 'right' }]}>Towers left: {a.short} {towersLeft(built[a.id])} · {b.short} {towersLeft(built[b.id])}</Text>
              </View>
              {LANES.map((lane) => (
                <View key={lane} style={s.lane} accessible accessibilityRole="image" accessibilityLabel={laneWords(lane, [{ short: a.short, lanes: built[a.id] }, { short: b.short, lanes: built[b.id] }])}>
                  <LaneSide lane={built[a.id]?.[lane]} />
                  <Eyebrow style={{ width: 44, textAlign: 'center' }}>{lane}</Eyebrow>
                  <LaneSide lane={built[b.id]?.[lane]} right />
                </View>
              ))}
              <Text style={[t.tiny, t.muted]}>Barracks at each end, then towers out to the river. Hollow: fallen.</Text>
            </View>
          ) : null}
        </Card>
      </MatchSection>
      {readings.length > 1 ? <LeadGraph key={live.n} title={`Map ${live.n}, gold so far`} what={live.lead?.what ?? 'lead'} readings={readings} teams={teams} tone={tone} /> : null}
      {heroes.length > 0 ? (
        <MatchSection title={`Heroes on map ${live.n}`}>
          <Card style={s.heroes}>
            {teams.map((x) => (
              <View key={x.id} style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Dsp size={16} color={colour.muted} style={{ paddingHorizontal: 8, paddingBottom: 4 }}>{x.short}</Dsp>
                {heroes.filter((h) => h.team === x.id).map((h) => {
                  const mine = isYours(h.name, yours)
                  return (
                    <View key={`${h.name}-${h.as}`} style={[s.heroRow, mine && { backgroundColor: tint(tone) }]}>
                      <Dsp size={18}>{h.name || h.as}</Dsp>
                      <Text style={[t.tiny, t.muted]}>{h.as}{mine ? ' · you follow' : ''}</Text>
                    </View>
                  )
                })}
              </View>
            ))}
          </Card>
        </MatchSection>
      ) : null}
    </>
  )
}

/** A turn of the draft on its side: a pick in ink (the fan's player's in the sport's colour) with who played it,
 * or a ban struck through, with how that hero went for the other side earlier in the series. */
function Turn({ turn, note, right, yours, tone }: { turn: DraftTurn; note: string | null; right?: boolean; yours?: string[]; tone: Tone }) {
  if (!turn.pick) {
    return (
      <View style={right ? { alignItems: 'flex-end' } : null}>
        <Text style={[s.ban, right && { textAlign: 'right' }]}>{turn.hero}</Text>
        {note ? <Text style={[t.tiny, { fontFamily: font[700] }, right && { textAlign: 'right' }]}>{note}</Text> : null}
      </View>
    )
  }
  const mine = isYours(turn.player ?? '', yours)
  const ink = mine ? tone.on : colour.surface
  return (
    <View style={[s.pick, { backgroundColor: mine ? tone.sport : colour.text }, right && { alignItems: 'flex-end' }]}>
      <Dsp size={18} color={ink} style={right ? { textAlign: 'right' } : null}>{turn.hero}</Dsp>
      <Text style={[t.tiny, { color: ink }, right && { textAlign: 'right' }]}>{turn.player}{mine ? ' · you follow' : ''}</Text>
    </View>
  )
}

const COLUMNS = [62, 38, 48] // a map's players' numbers (K/D/A, GPM, net worth), as .map-players' grid

/** One finished map: who took it and both sides' totals, its lead by minute, how it went, every player with their
 * items, and its draft. */
function MapView({ maps, map, teams, yours, tone }: { maps: MapDetail[]; map: MapDetail; teams: PageTeam[]; yours?: string[]; tone: Tone }) {
  const [a, b] = teams
  const byId = (id?: string | null) => teams.find((x) => x.id === id)
  const worth = (map.totals ?? []).find((x) => x.label === 'Net worth')?.values ?? {}
  const rounds = draftRounds(map.draft ?? [])
  const first = map.draft?.find((d) => d.pick)
  const events = map.events ?? []
  const players = map.players ?? []
  const columns = map.columns ?? []
  return (
    <>
      <Card pad style={{ gap: 12 }}>
        <View style={s.row}>
          <Dsp size={26} style={{ flexShrink: 1 }}>{byId(map.winner) ? `${byId(map.winner)?.short} take map ${map.n}` : `Map ${map.n}`}</Dsp>
          {map.length ? <Text style={num(20)}>{map.length}</Text> : null}
        </View>
        {(map.totals ?? []).length > 0 ? <Totals teams={teams} rows={map.totals ?? []} /> : null}
      </Card>
      {(map.lead?.by_minute ?? []).length > 1 ? (
        <LeadGraph key={map.n} title={`Map ${map.n}, ${map.lead?.what ?? 'lead'} by minute`} what={map.lead?.what ?? 'lead'} readings={leadOf(map, a.id)} marks={map.marks} teams={teams} tone={tone} />
      ) : null}
      {events.length > 0 ? (
        <MatchSection title={`How map ${map.n} went`}>
          <View style={[ui.card, ui.list]}>
            {events.map((e, i) => {
              const ours = namesYours(`${e.title} ${e.note ?? ''}`, yours)
              const end = i === events.length - 1
              return (
                <View key={`${e.t}-${i}`} style={[ui.listRow, !end && ui.listRowLine, s.moment, ours && [s.yours, { backgroundColor: tint(tone) }]]}>
                  <Text style={[num(16), { width: 52, paddingTop: 2, color: end ? colour.liveText : colour.muted }]}>{e.clock}</Text>
                  <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                    <View style={[s.row, { justifyContent: 'flex-start', gap: 8 }]}>
                      {e.mark ? <MarkKey label={e.mark} first={e.team === a.id} tone={tone} /> : null}
                      <Text style={[t.title, { flexShrink: 1 }]}>{e.title}</Text>
                    </View>
                    {e.note ? <Text style={[t.small, t.muted]}>{e.note}{ours ? ' · you follow' : ''}</Text> : null}
                  </View>
                </View>
              )
            })}
          </View>
        </MatchSection>
      ) : null}
      {players.length > 0 ? (
        <MatchSection title={`Players in map ${map.n}`}>
          <Card style={s.players}>
            {columns.length > 0 ? (
              <View style={[s.player, { paddingTop: 10 }]} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
                <View style={{ flex: 1 }} />
                {columns.map((c, i) => <Eyebrow key={c} style={{ width: COLUMNS[i] ?? 48, letterSpacing: 0.44, textAlign: i === 0 ? 'left' : 'right' }}>{c}</Eyebrow>)}
              </View>
            ) : null}
            {teams.map((x) => (
              <View key={x.id}>
                <View style={s.teamRule}>
                  <Dsp size={16}>{x.short}{worth[x.id] ? ` ${worth[x.id]}` : ''}</Dsp>
                  <View style={{ flex: 1, height: 2, backgroundColor: colour.text }} />
                </View>
                {players.filter((p) => p.team === x.id).map((p, i) => {
                  const mine = isYours(p.name, yours)
                  return (
                    <View key={`${p.name}-${p.as}`} style={[s.playerRow, i > 0 && s.rule, mine && [s.yours, { backgroundColor: tint(tone), borderTopColor: 'transparent' }]]}>
                      <View style={s.player}>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <Dsp size={18}>{p.name || p.as}</Dsp>
                          <Text style={[t.tiny, t.muted]}>{p.as}{mine ? ' · you follow' : ''}</Text>
                        </View>
                        {p.numbers.map((n, j) => <Text key={j} style={[num(16), { width: COLUMNS[j] ?? 48, textAlign: j === 0 ? 'left' : 'right' }]} numberOfLines={1} adjustsFontSizeToFit>{n}</Text>)}
                      </View>
                      {p.items.length > 0 ? <Text style={[t.tiny, t.muted]}>{p.items.join(' · ')}</Text> : null}
                    </View>
                  )
                })}
              </View>
            ))}
          </Card>
        </MatchSection>
      ) : null}
      {rounds.length > 0 ? (
        <MatchSection title={`Draft · map ${map.n}`} note={first ? `${byId(first.team)?.short ?? ''} pick first` : null}>
          <Card style={s.draft}>
            <View style={s.draftRow}>
              <Dsp size={18} style={{ flex: 1 }}>{a.short}</Dsp>
              <View style={{ width: 30 }} />
              <Dsp size={18} style={{ flex: 1, textAlign: 'right' }}>{b.short}</Dsp>
            </View>
            {rounds.map((r, ri) => (
              <View key={ri} style={{ gap: 6 }}>
                <Eyebrow style={s.round}>{r.pick ? 'Picks' : 'Bans'} · {r.turns.length}</Eyebrow>
                {r.turns.map(({ turn, order }) => {
                  const note = banNote(maps, map, turn)
                  return (
                    <View key={order} style={s.draftRow}>
                      <View style={s.side}>{turn.team === a.id ? <Turn turn={turn} note={note} yours={yours} tone={tone} /> : null}</View>
                      <Text style={[num(13), s.order]}>{order}</Text>
                      <View style={[s.side, { alignItems: 'flex-end' }]}>{turn.team === b.id ? <Turn turn={turn} note={note} right yours={yours} tone={tone} /> : null}</View>
                    </View>
                  )
                })}
              </View>
            ))}
          </Card>
        </MatchSection>
      ) : null}
    </>
  )
}

/** A player's hero and line in each map, a tile a map, as many to a row as fit at 68 or more wide, fewer tiles
 * than that sharing the row (.across, the web's auto-fit grid); the maps their side took in ink. */
function Across({ maps }: { maps: ReturnType<typeof acrossMaps>['maps'] }) {
  const [width, setWidth] = useState(0)
  const per = Math.max(1, Math.min(maps.length, Math.floor((width + 6) / (68 + 6))))
  const tile = width ? (width - 6 * (per - 1)) / per - 0.5 : 68 // a hair under, so rounding to pixels can't wrap the last
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
      {maps.map((m) => {
        const ink = m.won ? colour.surface : colour.text
        return (
          <View key={m.n} style={[s.acrossTile, { width: tile, backgroundColor: m.won ? colour.text : colour.bg }]}>
            <Text style={[t.tiny, { color: ink, textAlign: 'center' }]}>Map {m.n}{m.won ? ' · won' : ''}</Text>
            <Text style={[t.small, { fontFamily: font[800], color: ink, textAlign: 'center' }]}>{m.as}</Text>
            <Text style={[num(14), { color: ink }]}>{m.line}</Text>
          </View>
        )
      })}
    </View>
  )
}

/** The series' finished maps in depth (what a source told the page: Dota 2's): its best player, a tab a map, the
 * fan's players across the maps, and who played what. */
function SeriesDetail({ maps, teams, yours, live, tone }: { maps: MapDetail[]; teams: PageTeam[]; yours?: string[]; live: boolean; tone: Tone }) {
  const [picked, setPicked] = useState<number | null>(null)
  const map = maps.find((m) => m.n === picked) ?? maps[maps.length - 1]
  const best = seriesBest(maps)
  const bans = everyMapBans(maps)
  const followed = followedIn(maps, yours)
  return (
    <>
      {best ? (
        <View style={s.best}>
          <View style={{ flexShrink: 1 }}>
            <Eyebrow color={tone.ink}>{live ? 'Best so far' : 'Best of the series'}</Eyebrow>
            <Dsp size={26} color={colour.surface}>{best.name}</Dsp>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={[num(24), { color: colour.surface }]}>{best.kda}</Text>
            <Text style={[t.tiny, { color: colour.surface }]}>over {best.maps} map{best.maps === 1 ? '' : 's'}{best.gpm ? ` · ${best.gpm} GPM` : ''}</Text>
          </View>
        </View>
      ) : null}
      {maps.length > 1 ? (
        <View accessibilityRole="tablist" accessibilityLabel="Maps">
          <View style={s.tabsLine} />
          {/* Sideways, as the web's .tabs, when five maps don't fit at a large text size. */}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 18 }}>
            {maps.map((m) => (
              <Pressable key={m.n} accessibilityRole="tab" accessibilityState={{ selected: m.n === map.n }} onPress={() => setPicked(m.n)} style={[s.tab, m.n === map.n && s.tabOn]}>
                <Text style={[s.tabText, m.n === map.n && { color: colour.text }]}>Map {m.n}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      ) : null}
      <MapView maps={maps} map={map} teams={teams} yours={yours} tone={tone} />
      {followed.map((name) => {
        const across = acrossMaps(maps, name)
        return (
          <MatchSection key={name} title={`${name}, who you follow`}>
            <Card pad style={{ gap: 12 }}>
              <View style={s.row}>
                <Dsp size={28} style={{ flexShrink: 1 }}>{name}</Dsp>
                {across.kda ? (
                  <View style={{ alignItems: 'flex-end' }}>
                    <Text style={num(22)}>{across.kda}</Text>
                    <Text style={[t.tiny, t.muted]}>over {across.maps.length} map{across.maps.length === 1 ? '' : 's'}</Text>
                  </View>
                ) : null}
              </View>
              <Across maps={across.maps} />
              {across.note ? <Text style={[t.small, t.muted]}>{across.note}</Text> : null}
            </Card>
          </MatchSection>
        )
      })}
      {bans.length > 0 ? (
        <Card pad style={{ gap: 4 }}>
          <Eyebrow>Banned in every map</Eyebrow>
          <Text style={[t.body, { fontFamily: font[800] }]}>{bans.join(' · ')}</Text>
        </Card>
      ) : null}
      {maps.some((m) => (m.players ?? []).length) ? (
        <MatchSection title="Who played what">
          <Card style={s.whoPlayed}>
            {teams.map((x, ti) => (
              <View key={x.id} style={ti ? s.nextTeam : null}>
                <Dsp size={18} style={{ paddingBottom: 6 }}>{x.short}</Dsp>
                <View style={s.cells}>
                  {maps.map((m) => <Eyebrow key={m.n} style={[s.cell, { paddingBottom: 6 }]}>Map {m.n}{m.winner === x.id ? ' · W' : ''}</Eyebrow>)}
                </View>
                {whoPlayed(maps, x.id).map((row) => {
                  const mine = isYours(row.name, yours)
                  return (
                    <View key={row.name} style={[s.rule, mine && [s.whoYours, { backgroundColor: tint(tone) }]]}>
                      <Text style={[{ paddingTop: 8 }, dspInk(16, { paddingBottom: 3 })]}>
                        <Text style={dsp(16)}>{row.name}</Text>
                        {mine ? <Text style={[t.tiny, t.muted]}> · you follow</Text> : null}
                      </Text>
                      <View style={s.cells}>
                        {row.heroes.map((h, i) => <Text key={i} style={[s.played, s.cell]}>{h ?? '–'}</Text>)}
                      </View>
                    </View>
                  )
                })}
              </View>
            ))}
          </Card>
        </MatchSection>
      ) : null}
    </>
  )
}

/** A match page's part: a series map by map (who took each, the one being played, a Dota 2 map's best numbers, and
 * where its source told the page more, each finished map's lead line and the maps in depth), or a lobby's whole
 * standings and who won each of its matches. */
function EsportsMatch({ page, live, yours, tone }: MatchProps<EsportsPage>) {
  if (page.standings?.length) {
    const won = lobbyWinners(page)
    return (
      <MatchSection title="Standings" note={`${page.standings.length} teams`}>
        <Card pad style={{ gap: 12 }}>
          <View>
            <LineRow pal={INK} head wide={1} right widths={LOBBY_COLUMNS} cells={['#', 'Team', 'Won', 'Fin.', 'Pts']} />
            {page.standings.map((x) => (
              <LineRow key={`${x.place}-${x.name}`} pal={INK} total wide={1} right widths={LOBBY_COLUMNS} bold={[false, true]} cells={[place(x.place), x.name, String(x.wins), String(x.kills), points(x.points)]} />
            ))}
          </View>
          {won ? <Text style={[t.small, t.muted]}>{won}</Text> : null}
        </Card>
      </MatchSection>
    )
  }
  if (!page.maps?.length) return null
  const teams = page.teams?.length === 2 ? page.teams : null
  const detail = teams ? (page.detail?.maps ?? []) : []
  const board = (live?.state as unknown as EsportsState | undefined)?.dota?.live
  const now = liveNow(page, board, !!live?.is_live)
  const told = new Map(detail.map((m) => [m.n, m]))
  const top = sparkTop(detail)
  return (
    <>
      {now && teams ? <LiveMap live={now} board={board} teams={teams} yours={yours} tone={tone} /> : null}
      <MatchSection title="Maps" note={teams && detail.some((m) => m.lead) ? `${teams[0].short} up, ${teams[1].short} down` : null}>
        <ListCard>
          {page.maps.map((m) => {
            const d = told.get(m.n)
            const values = d && teams ? leadOf(d, teams[0].id) : []
            if (d && teams && values.length > 1) {
              const best = m.best || mapBest(d)
              return (
                <View key={m.n} style={s.mapRow}>
                  <View style={{ width: 64, gap: 2 }}>
                    <Text style={t.title}>Map {m.n}</Text>
                    {m.winner ? <Dsp size={18}>{shortTeam(m.winner)}</Dsp> : null}
                  </View>
                  <Spark readings={values} top={top} label={`Map ${m.n}: ${leader(values[values.length - 1][1], teams)} at the end`} />
                  <View style={{ flex: 1, minWidth: 0, alignItems: 'flex-end', gap: 2 }}>
                    {d.length ? <Text style={num(15)}>{d.length}</Text> : null}
                    {best ? <Text style={[t.tiny, t.muted, { textAlign: 'right' }]}>{best}</Text> : null}
                  </View>
                </View>
              )
            }
            return (
              <View key={m.n} style={[s.row, { gap: 12 }]}>
                <View style={{ flexShrink: 1, gap: 2 }}>
                  <Text style={t.title}>Map {m.n}</Text>
                  {m.best ? <Text style={[t.small, t.muted]}>{m.best}</Text> : null}
                </View>
                {m.winner ? <Text style={[t.title, { textAlign: 'right', flexShrink: 1 }]}>{m.winner}</Text> : m.status === 'running' ? <LiveBadge label="Now" /> : <Text style={[t.small, t.muted]}>To play</Text>}
              </View>
            )
          })}
        </ListCard>
      </MatchSection>
      {teams && detail.length > 0 ? <SeriesDetail maps={detail} teams={teams} yours={yours} live={!!live?.is_live} tone={tone} /> : null}
    </>
  )
}

export const esports = { ...facts, Board: EsportsBoard as Board, Match: EsportsMatch as MatchPart }

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  rule: { borderTopWidth: 1, borderTopColor: colour.line },
  yours: { marginHorizontal: -10, paddingHorizontal: 10, borderRadius: 12 },
  axis: { fontFamily: font[700], fontSize: 11, lineHeight: 14, color: colour.muted },
  tick: { position: 'absolute', top: 2 },
  mark: { position: 'absolute', width: 18, height: 18, borderRadius: 9, borderWidth: 2, borderColor: colour.surface, alignItems: 'center', justifyContent: 'center' },
  markKey: { width: 18, height: 18, borderRadius: 9, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  markText: { fontFamily: font[800], fontSize: 10, lineHeight: 12, includeFontPadding: false },
  totalsRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  totalsHead: { flex: 1, paddingBottom: 6 },
  totalsNum: { flex: 1, paddingVertical: 8 },
  mapRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  moment: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  lane: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  laneSide: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 8 },
  rax: { flexDirection: 'row', gap: 3 },
  pip: { width: 16, height: 16, borderRadius: 8, backgroundColor: colour.text, flexShrink: 0 },
  box: { width: 10, borderRadius: 3 },
  down: { backgroundColor: 'transparent', borderWidth: 2, borderColor: colour.muted },
  heroes: { flexDirection: 'row', gap: 10, padding: 14 },
  heroRow: { paddingVertical: 7, paddingHorizontal: 8, borderRadius: 12, minWidth: 0 },
  players: { paddingTop: 4, paddingHorizontal: 16, paddingBottom: 10 },
  teamRule: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 12, paddingBottom: 4 },
  playerRow: { paddingVertical: 9, gap: 4 },
  player: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  draft: { padding: 14, gap: 6 },
  draftRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  round: { textAlign: 'center', paddingTop: 10, paddingBottom: 2, borderTopWidth: 1, borderTopColor: colour.line },
  order: { width: 30, textAlign: 'center', color: colour.muted },
  side: { flex: 1, minWidth: 0 },
  ban: { fontFamily: font[500], fontSize: 14, lineHeight: 19, color: colour.muted, textDecorationLine: 'line-through' },
  pick: { alignSelf: 'stretch', paddingVertical: 8, paddingHorizontal: 10, borderRadius: 12 },
  best: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, paddingVertical: 12, paddingHorizontal: 14, borderRadius: 18, backgroundColor: colour.text },
  tabsLine: { position: 'absolute', left: 0, right: 0, bottom: 0, height: 2, backgroundColor: colour.line }, // under the picked tab's own
  tab: { height: 44, justifyContent: 'center', borderBottomWidth: 3, borderBottomColor: 'transparent' },
  tabOn: { borderBottomColor: colour.text },
  tabText: { fontFamily: font[800], fontSize: 13, letterSpacing: 1, textTransform: 'uppercase', color: colour.muted },
  acrossTile: { alignItems: 'center', gap: 2, paddingVertical: 8, paddingHorizontal: 4, borderRadius: 12 },
  whoPlayed: { paddingVertical: 12, paddingHorizontal: 16 },
  nextTeam: { marginTop: 12, paddingTop: 14, borderTopWidth: 2, borderTopColor: colour.text },
  cells: { flexDirection: 'row' },
  cell: { flex: 1, minWidth: 0, paddingRight: 6 },
  played: { fontFamily: font[700], fontSize: 12, lineHeight: 15, color: colour.text, paddingBottom: 9 },
  whoYours: { marginHorizontal: -8, paddingHorizontal: 8, borderRadius: 10, borderTopColor: 'transparent' },
})

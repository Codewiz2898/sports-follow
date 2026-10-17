import { Text, View } from 'react-native'
import { dotaLine, esports as facts, isLobby, lobbyRows, lobbyWinners, place, points, shortTeam, type BattleRoyaleState, type EsportsPage, type EsportsState, type EsportsTeam } from '../../../web/src/sports/facts/esports'
import { colour, font, num, t } from '../theme'
import { Card, ListCard, LiveBadge } from '../ui'
import { Clock, INK, LineRow, Muted, Note, Score, Sides, Title, type Board, type BoardProps, type MatchPart, type MatchProps, type Palette } from './board'
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
        <LineRow pal={pal} head wide={1} right cells={['#', 'Team', 'Won', 'Fin.', 'Pts']} />
        {lobbyRows(state, teams, compact).map(({ team: x, ours }) => (
          <LineRow key={x.id} pal={ours ? { ...pal, muted: pal.ours, text: pal.ours } : pal} total wide={1} right bold={[false, true]} cells={[place(x.place), x.name, String(x.wins), String(x.kills), points(x.points)]} />
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

/** A match page's part: a series map by map (who took each, the one being played, a Dota 2 map's best
 * numbers), or a lobby's whole standings and who won each of its matches. */
function EsportsMatch({ page }: MatchProps<EsportsPage>) {
  if (page.standings?.length) {
    const won = lobbyWinners(page)
    return (
      <MatchSection title="Standings" note={`${page.standings.length} teams`}>
        <Card pad style={{ gap: 12 }}>
          <View>
            <LineRow pal={INK} head wide={1} right cells={['#', 'Team', 'Won', 'Fin.', 'Pts']} />
            {page.standings.map((x) => (
              <LineRow key={`${x.place}-${x.name}`} pal={INK} total wide={1} right bold={[false, true]} cells={[place(x.place), x.name, String(x.wins), String(x.kills), points(x.points)]} />
            ))}
          </View>
          {won ? <Text style={[t.small, t.muted]}>{won}</Text> : null}
        </Card>
      </MatchSection>
    )
  }
  if (!page.maps?.length) return null
  return (
    <MatchSection title="Maps">
      <ListCard>
        {page.maps.map((m) => (
          <View key={m.n} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <View style={{ flexShrink: 1, gap: 2 }}>
              <Text style={t.title}>Map {m.n}</Text>
              {m.best ? <Text style={[t.small, t.muted]}>{m.best}</Text> : null}
            </View>
            {m.winner ? <Text style={[t.title, { textAlign: 'right', flexShrink: 1 }]}>{m.winner}</Text> : m.status === 'running' ? <LiveBadge label="Now" /> : <Text style={[t.small, t.muted]}>To play</Text>}
          </View>
        ))}
      </ListCard>
    </MatchSection>
  )
}

export const esports = { ...facts, Board: EsportsBoard as Board, Match: EsportsMatch as MatchPart }

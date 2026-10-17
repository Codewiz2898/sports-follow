import { Text, View } from 'react-native'
import { cricket as facts, inningsScore, type CricketPage, type CricketState } from '../../../web/src/sports/facts/cricket'
import { num, t } from '../theme'
import { Card } from '../ui'
import { INK, LineRow, Note, Score, Title, type Board, type BoardProps, type MatchPart, type MatchProps } from './board'
import { MatchSection } from './section'

function CricketBoard({ state, clock, compact, pal }: BoardProps<CricketState>) {
  if (!state.innings.length) {
    return (
      <View style={{ gap: 4 }}>
        <Score pal={pal} size={compact ? 28 : 36}>{state.teams.map((x) => x.name).join(' v ')}</Score>
        {clock ? <Note pal={pal}>{clock}</Note> : null}
      </View>
    )
  }
  return (
    <View style={{ gap: 8 }}>
      {state.innings.map((i) => (
        <View key={`${i.team}-${i.period}`} style={{ flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8, flexShrink: 1 }}>
            <Title pal={pal} style={{ color: i.batting ? pal.text : pal.muted }}>{i.abbr || i.team}</Title>
            {i.batting ? <Text style={[t.tiny, { color: pal.note, fontFamily: 'Manrope_700Bold' }]}>batting</Text> : null}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 8 }}>
            <Text style={[num(i.batting ? pal.score : 26), { color: i.batting ? pal.text : pal.muted }]}>{inningsScore(i)}</Text>
            {i.overs != null ? <Text style={[t.small, { color: pal.muted }]}>{i.overs} ov</Text> : null}
          </View>
        </View>
      ))}
      {state.summary || clock ? <Note pal={pal}>{[clock, state.summary !== clock && state.summary].filter(Boolean).join(' · ')}</Note> : null}
    </View>
  )
}

/** A match page's scorecard: each side in the order they batted, who batted for it and how they were out, and
 * who bowled at it. */
function CricketMatch({ page }: MatchProps<CricketPage>) {
  if (!page.scorecard?.length) return null
  return (
    <>
      {page.scorecard.map((side) => (
        <MatchSection key={side.team} title={side.team} note={side.score}>
          <Card pad style={{ gap: 14 }}>
            {side.batting.length > 0 ? (
              <View>
                <LineRow pal={INK} head right cells={['Batting', 'R', 'B', '4s', '6s', 'SR']} />
                {side.batting.map((x) => (
                  <LineRow key={x.name} pal={INK} right total={1} cells={[
                    <View key="who"><Text style={t.title}>{x.name}</Text><Text style={[t.tiny, t.muted]}>{x.out || 'not out'}</Text></View>,
                    x.runs, x.balls, x.fours, x.sixes, x.strike_rate,
                  ]} />
                ))}
              </View>
            ) : null}
            {side.bowling.length > 0 ? (
              <View>
                <LineRow pal={INK} head right cells={['Bowling', 'O', 'R', 'W', 'Econ']} />
                {side.bowling.map((x) => (
                  <LineRow key={x.name} pal={INK} right total={3} cells={[<Text key="who" style={t.title}>{x.name}</Text>, x.overs, x.runs, x.wickets, x.economy]} />
                ))}
              </View>
            ) : null}
          </Card>
        </MatchSection>
      ))}
    </>
  )
}

export const cricket = { ...facts, Board: CricketBoard as Board, Match: CricketMatch as MatchPart }

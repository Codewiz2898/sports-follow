import { Text, View } from 'react-native'
import { cricket as facts, inningsScore, type CricketState } from '../../../web/src/sports/facts/cricket'
import { num, t } from '../theme'
import { Note, Score, Title, type Board, type BoardProps } from './board'

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

export const cricket = { ...facts, Board: CricketBoard as Board }

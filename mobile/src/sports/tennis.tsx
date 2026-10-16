import { Text, View } from 'react-native'
import { tennis as facts, type TennisState } from '../../../web/src/sports/facts/tennis'
import { colour, num, t } from '../theme'
import { Note, Title, type Board, type BoardProps } from './board'

/** One row per player, one column per set; the serving player is marked. */
function TennisBoard({ state, clock, pal }: BoardProps<TennisState>) {
  const sets = Math.max(0, ...state.players.map((p) => p.sets.length))
  return (
    <View style={{ gap: 8 }}>
      {state.players.map((p) => (
        <View key={p.id} style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 4 }}>
          <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', paddingHorizontal: 6 }}>
            <Title pal={pal} style={{ color: p.winner ? pal.text : pal.muted }}>{p.name}</Title>
            {p.seed ? <Text style={[t.tiny, { color: pal.muted }]}> ({p.seed})</Text> : null}
            {p.serving ? <View accessibilityLabel="serving" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colour.live, marginLeft: 8 }} /> : null}
          </View>
          {Array.from({ length: sets }, (_, i) => {
            const set = p.sets[i]
            return (
              <View key={i} style={{ minWidth: 30, paddingHorizontal: 6, flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'center' }}>
                <Text style={[num(26), { color: set?.won ? pal.text : pal.muted }]}>{set?.games ?? ''}</Text>
                {set?.tiebreak != null ? <Text style={[t.tiny, { color: set?.won ? pal.text : pal.muted, marginLeft: 1 }]}>{set.tiebreak}</Text> : null}
              </View>
            )
          })}
        </View>
      ))}
      {clock ? <Note pal={pal}>{[state.round, clock].filter(Boolean).join(' · ')}</Note> : null}
    </View>
  )
}

export const tennis = { ...facts, Board: TennisBoard as Board }

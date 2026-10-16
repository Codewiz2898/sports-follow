import { View } from 'react-native'
import { basketball as facts, periodName, type BasketballSide, type BasketballState } from '../../../web/src/sports/facts/basketball'
import { Clock, LineRow, Muted, Score, Sides, Title, type Board, type BoardProps, type Palette } from './board'

function side(s: BasketballSide, pal: Palette, align: 'left' | 'right') {
  return (
    <>
      <Title pal={pal} style={{ textAlign: align }}>{s.name}</Title>
      <Muted pal={pal}>{align === 'left' ? 'Away' : 'Home'}</Muted>
    </>
  )
}

/** Away at home, as US leagues write it, with the quarter-by-quarter line under it. */
function BasketballBoard({ state, clock, compact, pal }: BoardProps<BasketballState>) {
  const periods = Math.max(state.away.periods.length, state.home.periods.length)
  const cols = Array.from({ length: periods }, (_, i) => i)
  return (
    <View style={{ gap: 12 }}>
      <Sides
        left={side(state.away, pal, 'left')}
        middle={<><Score pal={pal}>{state.away.score}–{state.home.score}</Score>{clock ? <Clock pal={pal}>{clock}</Clock> : null}</>}
        right={side(state.home, pal, 'right')}
      />
      {!compact && periods > 0 ? (
        <View>
          <LineRow pal={pal} head cells={['', ...cols.map(periodName), 'T']} />
          {[state.away, state.home].map((s) => (
            <LineRow key={s.id} pal={pal} total cells={[s.abbr || s.name, ...cols.map((i) => s.periods[i] ?? ''), s.score]} />
          ))}
        </View>
      ) : null}
    </View>
  )
}

export const basketball = { ...facts, Board: BasketballBoard as Board }

import { Text, View } from 'react-native'
import { football as facts, timelineKind, type FootballPage, type FootballSide, type FootballState } from '../../../web/src/sports/facts/football'
import { num, t } from '../theme'
import { ListCard } from '../ui'
import { Clock, MomentKind, Muted, Score, Sides, Title, type Board, type BoardProps, type MatchPart, type MatchProps, type Palette } from './board'
import { MatchSection } from './section'

function side(s: FootballSide, pal: Palette, compact: boolean | undefined, align: 'left' | 'right') {
  return (
    <>
      <Title pal={pal} style={{ textAlign: align }}>{s.name}</Title>
      {!compact && s.scorers.map((g) => <Muted key={g} pal={pal} style={{ textAlign: align }}>{g}</Muted>)}
    </>
  )
}

function FootballBoard({ state, clock, compact, pal }: BoardProps<FootballState>) {
  return (
    <Sides
      left={side(state.home, pal, compact, 'left')}
      middle={<><Score pal={pal}>{state.home.score}–{state.away.score}</Score>{clock ? <Clock pal={pal}>{clock}</Clock> : null}</>}
      right={side(state.away, pal, compact, 'right')}
    />
  )
}

/** A match page's timeline: each goal and card at its minute, newest first, the home side's on the left and
 * the away side's on the right. */
function FootballMatch({ page }: MatchProps<FootballPage>) {
  if (!page.timeline?.length) return null
  return (
    <MatchSection title="Timeline">
      <ListCard>
        {page.timeline.map((e, i) => {
          const away = e.side === 1
          return (
            <View key={`${e.clock}-${e.kind}-${i}`} style={{ flexDirection: away ? 'row-reverse' : 'row', alignItems: 'baseline', gap: 10 }}>
              <Text style={[num(17), { minWidth: 44, textAlign: away ? 'right' : 'left' }]}>{e.clock}</Text>
              <MomentKind kind={e.kind} label={timelineKind(e.kind)} />
              <Text style={[t.small, { flex: 1, textAlign: away ? 'right' : 'left' }]}>{e.text}</Text>
            </View>
          )
        })}
      </ListCard>
    </MatchSection>
  )
}

export const football = { ...facts, Board: FootballBoard as Board, Match: FootballMatch as MatchPart }

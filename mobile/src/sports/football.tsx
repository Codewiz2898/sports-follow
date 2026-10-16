import { football as facts, type FootballSide, type FootballState } from '../../../web/src/sports/facts/football'
import { Clock, Muted, Score, Sides, Title, type Board, type BoardProps, type Palette } from './board'

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

export const football = { ...facts, Board: FootballBoard as Board }

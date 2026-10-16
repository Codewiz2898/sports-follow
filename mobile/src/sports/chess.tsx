import { Text, View } from 'react-native'
import { chess as facts, chessMiddle, PIECES, position, type ChessSide, type ChessState } from '../../../web/src/sports/facts/chess'
import { colour, num, t } from '../theme'
import { Muted, Title, type Board, type BoardProps, type Palette } from './board'

// The web's chess.css: light and dark squares, the last move's in yellow, white pieces outlined.
const SQUARE = { light: '#ECE6D6', dark: '#8B6FD6', movedLight: '#F3E38A', movedDark: '#C9B34A' }

function player(s: ChessSide, colourName: 'white' | 'black', state: ChessState, pal: Palette, onPaper: boolean) {
  const running = state.turn === colourName && !state.result
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <View style={{ flexShrink: 1, minWidth: 0 }}>
        <Title pal={pal} lines={1}>{s.title ? <Text style={{ color: pal.muted }}>{s.title} </Text> : null}{s.name}</Title>
        {!onPaper ? <Muted pal={pal}>{[colourName === 'white' ? 'White' : 'Black', s.rating, s.fed].filter(Boolean).join(' · ')}</Muted> : null}
      </View>
      {s.clock ? (
        <Text style={[num(onPaper ? 18 : 22), { paddingHorizontal: onPaper ? 6 : 8, paddingVertical: onPaper ? 2 : 3, borderRadius: 8, backgroundColor: pal.raised, color: running ? pal.text : pal.muted, overflow: 'hidden' }, running && { borderWidth: 2, borderColor: colour.live }]}>{s.clock}</Text>
      ) : null}
    </View>
  )
}

/** The board from the position's FEN, from the followed player's side, with the last move marked. */
function ChessBoard({ state, clock, compact, live, pal }: BoardProps<ChessState>) {
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live)
  // On a paper card (a game none of the fan's players are in), a small board with the players beside it.
  const onPaper = pal.paper
  const size = onPaper ? 100 : compact ? 176 : 280
  const glyph = onPaper ? 11 : compact ? 19 : 30
  const top = flip ? state.white : state.black
  const bottom = flip ? state.black : state.white
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
      {ranks.length === 8 ? (
        <View accessible accessibilityLabel={`Position after move ${state.move ?? ''}`} style={{ width: size, height: size, borderRadius: 8, overflow: 'hidden' }}>
          {ranks.map((rank, r) => (
            <View key={r} style={{ flex: 1, flexDirection: 'row' }}>
              {rank.map((sq) => (
                <View key={sq.name} style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: moved.has(sq.name) ? (sq.dark ? SQUARE.movedDark : SQUARE.movedLight) : sq.dark ? SQUARE.dark : SQUARE.light }}>
                  {sq.piece ? (
                    <Text style={{ fontSize: glyph, lineHeight: glyph * 1.1, color: sq.piece === sq.piece.toUpperCase() ? '#FBFAF6' : '#141414', textShadowColor: sq.piece === sq.piece.toUpperCase() ? '#000' : 'transparent', textShadowRadius: 1.5 }}>{`${PIECES[sq.piece.toLowerCase()]}︎`}</Text>
                  ) : null}
                </View>
              ))}
            </View>
          ))}
        </View>
      ) : null}
      {/* The players beside or under the board, the match score, result or clock between them. */}
      <View style={{ gap: 10, flex: 1, minWidth: onPaper || compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black', state, pal, onPaper)}
        <Text style={[num(compact ? 24 : 30), { color: pal.clock }]}>{chessMiddle(state, clock)}</Text>
        {player(bottom, flip ? 'black' : 'white', state, pal, onPaper)}
        {(state.result || state.match) && clock ? <Text style={[t.small, { color: pal.muted }]}>{clock}</Text> : null}
      </View>
    </View>
  )
}

export const chess = { ...facts, Board: ChessBoard as Board }

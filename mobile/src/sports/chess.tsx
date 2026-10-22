import { useMemo, useRef, useState } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { chess as facts, chessMiddle, evalDepth, followedSide, gameMoves, movesAbout, position, standardCastling, type ChessColour, type ChessPage, type ChessSide, type ChessState } from '../../../web/src/sports/facts/chess'
import { fenAt, lineOfGame, playedOn, samePosition, type Opening } from '../../../web/src/sports/facts/chessLine'
import { SwapIcon } from '../icons'
import { colour, num, t } from '../theme'
import { Btn } from '../ui'
import { Muted, Title, type Board, type BoardProps, type Palette } from './board'
import ChessAnalysis from './chessAnalysis'
import { BAR, EvalBar, Piece, SQUARE } from './chessParts'

function player(s: ChessSide, colourName: ChessColour, state: ChessState, pal: Palette, onPaper: boolean) {
  const running = state.turn === colourName && !state.result
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <View style={{ flexShrink: 1, minWidth: 0 }}>
        <Title pal={pal} lines={1}>{s.title ? <Text style={{ color: pal.muted }}>{s.title} </Text> : null}{s.name}</Title>
        {!onPaper ? <Muted pal={pal}>{[colourName === 'white' ? 'White' : 'Black', s.rating, s.fed].filter(Boolean).join(' · ')}</Muted> : null}
      </View>
      {s.clock ? (
        <Text style={[num(onPaper ? 18 : 22), c.clock, onPaper && c.clockSmall, { backgroundColor: pal.raised, color: running ? pal.text : pal.muted, borderColor: running ? colour.live : 'transparent' }]}>{s.clock}</Text>
      ) : null}
    </View>
  )
}

// The match page's board: as wide as the band, up to this (the web's .chess.big, its bar's room in it).
const BIG = 560
// The game's moves under it: a few lines, scrolled to the newest (the web's .chess.big .chess-line).
const MOVES_HEIGHT = 112

/** A game's moves on its match page (ChessPage: in pairs, and while it's on those its source added since, as the
 * board gave each, "d5e3"), replayed as chess from the start, each in SAN (the web's useReplay), with the board's
 * own last move where they're one short of it: a page's look lags the board a little. Null where they don't
 * replay (a Freestyle game's, or one set up from a position): then the pairs show as words. A finished game's must
 * end where its board does; a live game's may lag it. */
function replay(page: ChessPage | undefined, state: ChessState): string[] | null {
  const pairs = page?.moves ?? []
  const added = page?.more ?? []
  const from = pairs.length ? gameMoves(pairs) : []
  if (!from || (!from.length && !added.length) || !state.fen || !standardCastling(state.fen)) return null
  const start = lineOfGame(from, from.length)
  let game = start && playedOn(start, added)
  if (!game || game.plies.length < from.length + added.length) return null // one of them doesn't play
  if (!samePosition(fenAt(game), state.fen) && state.last_move) {
    const on = playedOn(game, [state.last_move])
    if (samePosition(fenAt(on), state.fen)) game = on
  }
  if (state.result && !samePosition(fenAt(game), state.fen)) return null
  return game.plies.map((m) => m.san)
}

/** The game's moves under its match page's board (the web's MovesPanel), in the band's colours: each, once they
 * replay, opening the analysis after it (`open`), the last (where the board is) marked; where they don't, the
 * pairs as words, and those added since as the board gave them. Its opening and how it ended, or of a
 * mini-match the game's number, over them. Kept scrolled to the newest as moves come, unless the fan has scrolled
 * back. */
function MovesPanel({ page, sans, open, pal }: { page: ChessPage; sans: string[] | null; open: (moves: string[], ply: number) => void; pal: Palette }) {
  const list = useRef<ScrollView>(null)
  const pinned = useRef(true) // at the newest: false once the fan scrolls back, true again at the end
  const about = [page.game ? `Game ${page.game}` : null, movesAbout(page)].filter(Boolean).join(' · ')
  return (
    <View style={{ gap: 6 }}>
      <View style={c.panelHead}>
        <Text style={[t.eyebrow, { color: pal.muted }]}>Moves</Text>
        {about ? <Text style={[t.tiny, { color: pal.muted, flexShrink: 1 }]}>{about}</Text> : null}
      </View>
      <ScrollView ref={list} style={{ maxHeight: MOVES_HEIGHT }} nestedScrollEnabled scrollEventThrottle={64}
        onContentSizeChange={() => { if (pinned.current) list.current?.scrollToEnd({ animated: false }) }}
        onScroll={(e) => { const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent; pinned.current = contentSize.height - contentOffset.y - layoutMeasurement.height < 24 }}>
        <View style={c.moves}>
          {sans ? sans.map((san, i) => {
            const said = `${Math.floor(i / 2) + 1}${i % 2 ? '...' : '.'} ${san}`
            const now = i + 1 === sans.length
            return (
              <View key={i} style={c.pair}>
                {i % 2 === 0 ? <Text style={[t.small, c.nums, { color: pal.muted }]}>{i / 2 + 1}.</Text> : null}
                <Pressable onPress={() => open(sans, i + 1)} accessibilityRole="button" accessibilityLabel={`Play on from ${said}`} accessibilityState={{ selected: now }} hitSlop={4}
                  style={({ pressed }) => [c.move, now ? { backgroundColor: pal.text } : pressed && { backgroundColor: pal.raised }]}>
                  <Text style={[t.small, c.nums, { color: now ? pal.ground : pal.text }]}>{san}</Text>
                </Pressable>
              </View>
            )
          }) : [...(page.moves ?? []), ...(page.more ?? [])].map((m, i) => <Text key={i} style={[t.small, c.nums, { color: pal.text }]}>{m}</Text>)}
        </View>
      </ScrollView>
    </View>
  )
}

/** The board from the position's FEN, with the last move marked and, while the game's on, the engine's evaluation
 * beside it when there is one: from the followed player's side (from White's with no one side of the fan's,
 * followedSide), until the fan turns it, which holds while the screen is open. On a paper card (a game none of
 * the fan's players are in), a small board with the players beside it, and neither. With `explore` (the match
 * page's, the web's .chess.big), the board as wide as the band, up to BIG: the move line over it, the players
 * above and under it as in a chess app, the game's moves under them (its `page`'s: MovesPanel), and Play from
 * here opening the analysis board over the screen (chessAnalysis.tsx), as does a move tapped in the list. */
function ChessBoard({ state, clock, compact, live, lines, explore, page, pal }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  const [opening, setOpening] = useState<Opening | null>(null)
  const [room, setRoom] = useState(0) // the band's width, once laid out
  // Play from here: the match page's board, of a position that plays by the standard rules (not a Freestyle
  // game's before its castling).
  const playable = Boolean(explore && state.fen && standardCastling(state.fen))
  const moves = explore ? (page as ChessPage | undefined) : undefined
  const pairs = moves?.moves?.join('\n') ?? ''
  const more = moves?.more?.join(' ') ?? ''
  const sans = useMemo(() => (playable ? replay(moves, state) : null),
    // By their words: the page's next look, with the same moves, doesn't replay them again.
    [playable, pairs, more, state.fen, state.last_move, state.result])
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  const onPaper = pal.paper
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0).
  const ev = onPaper || state.result ? null : state.eval
  const bar = ev ? (compact ? BAR.small : BAR.width) + BAR.gap : 0
  const size = onPaper ? 100 : compact ? 176 : explore ? Math.max(0, Math.min(room, BIG) - bar) : 280
  const glyph = onPaper ? 11 : compact ? 19 : explore ? Math.round((size / 8) * 0.86) : 30
  // The game's board as it is, with its bar beside it, which keeps the board's size; how deep the engine looked
  // under a big one that isn't the match page's (that one says it beside Play from here).
  const still = () => {
    const depth = ev && !compact && !explore ? evalDepth(ev) : null
    return (
      <View style={{ width: size + bar, maxWidth: '100%', gap: 4 }}>
        <View style={{ flexDirection: 'row', gap: BAR.gap }}>
          {ev ? <EvalBar ev={ev} flip={flip} compact={compact} pal={pal} /> : null}
          <View accessible accessibilityLabel={`Position after move ${state.move ?? ''}`} style={{ flex: 1, aspectRatio: 1, borderRadius: 8, overflow: 'hidden' }}>
            {ranks.map((rank, r) => (
              <View key={r} style={{ flex: 1, flexDirection: 'row' }}>
                {rank.map((sq) => (
                  <View key={sq.name} style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: moved.has(sq.name) ? (sq.dark ? SQUARE.movedDark : SQUARE.movedLight) : sq.dark ? SQUARE.dark : SQUARE.light }}>
                    {sq.piece ? <Piece piece={sq.piece} size={glyph} /> : null}
                  </View>
                ))}
              </View>
            ))}
          </View>
        </View>
        {depth ? <Muted pal={pal}>Engine · {depth}</Muted> : null}
      </View>
    )
  }
  const top = flip ? state.white : state.black
  const bottom = flip ? state.black : state.white
  // The match score, result or clock between the players, and the button that changes their places: not on a
  // paper card's small board. Inside a card that opens its match page (Today's), it takes the tap itself: the
  // innermost Pressable answers a touch.
  const middle = (
    <View style={c.middle}>
      <Text style={[num(compact ? 24 : 30), { color: pal.clock, flexShrink: 1 }]}>{chessMiddle(state, clock)}</Text>
      {ranks.length === 8 && !onPaper ? (
        <Pressable onPress={() => setSide(flip ? 'white' : 'black')} accessibilityRole="button" accessibilityLabel="Switch sides" hitSlop={4}
          style={({ pressed }) => [c.turn, { backgroundColor: pal.raised }, pressed && { opacity: 0.8 }]}>
          <SwapIcon size={18} color={pal.text} />
        </Pressable>
      ) : null}
    </View>
  )
  const note = (state.result || state.match) && clock ? <Text style={[t.small, { color: pal.muted }]}>{clock}</Text> : null

  if (explore) {
    // A move tapped in the list: the analysis board after it.
    const openAt = (line: string[], ply: number) => setOpening((o) => ({ base: state.fen ?? '', last: state.last_move, moves: line, ply, asked: (o?.asked ?? 0) + 1 }))
    return (
      <View onLayout={(e) => setRoom(e.nativeEvent.layout.width)} style={{ gap: 10 }}>
        <View style={{ gap: 4 }}>{middle}{note}</View>
        {player(top, flip ? 'white' : 'black', state, pal, false)}
        {ranks.length === 8 && room > 0 ? still() : null}
        {player(bottom, flip ? 'black' : 'white', state, pal, false)}
        {moves?.moves?.length || moves?.more?.length ? <MovesPanel page={moves} sans={sans} open={openAt} pal={pal} /> : null}
        {playable || ev?.depth ? (
          <View style={c.under}>
            {playable ? <Btn small label="Play from here" colours={{ fg: pal.text, border: pal.text }} onPress={() => setOpening({ base: state.fen ?? '', last: state.last_move, asked: 1 })} /> : null}
            {ev?.depth ? <Muted pal={pal}>Engine · depth {ev.depth}</Muted> : null}
          </View>
        ) : null}
        {opening && ranks.length === 8 ? <ChessAnalysis state={state} live={live} flip={flip} opening={opening} leave={() => setOpening(null)} /> : null}
      </View>
    )
  }
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
      {ranks.length === 8 ? still() : null}
      {/* The players beside or under the board, the match score, result or clock between them. */}
      <View style={{ gap: 10, flex: 1, minWidth: onPaper || compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black', state, pal, onPaper)}
        {middle}
        {player(bottom, flip ? 'black' : 'white', state, pal, onPaper)}
        {note}
      </View>
    </View>
  )
}

// The moves, a chess game's only section, are with its board (MovesPanel): it has no Match of its own.
export const chess = { ...facts, Board: ChessBoard as Board }

const c = StyleSheet.create({
  middle: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  turn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, // .btn.icon.chess-turn
  // A clock (.chess-clock): the running one ringed in red inside its box, as the web's inset shadow, so a clock
  // keeps its size when the turn passes; the ring is always there, clear when it's not running (Android draws a
  // Text whose border is taken away blank). Its padding is the web's less the ring.
  clock: { paddingHorizontal: 6, paddingVertical: 1, borderWidth: 2, borderRadius: 8, overflow: 'hidden' },
  clockSmall: { paddingHorizontal: 4, paddingVertical: 0 },
  under: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 4 }, // .chess-under
  // The game's moves (.chess-line): numbered pairs that wrap, each move its own touch, raised while pressed.
  panelHead: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  moves: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 8, rowGap: 4, paddingVertical: 2 },
  pair: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  move: { marginVertical: -3, marginHorizontal: -2, paddingVertical: 3, paddingHorizontal: 5, borderRadius: 6 },
  nums: { fontVariant: ['tabular-nums'] },
})

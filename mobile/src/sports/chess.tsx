import { use, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Pressable, StyleSheet, Text, View, type StyleProp, type TextStyle } from 'react-native'
import Animated, { Easing, ReduceMotion, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated'
import { chess as facts, chessMiddle, evalDepth, evalScore, evalWords, followedSide, gameMoves, movesAbout, PIECES, position, standardCastling, whiteShare, type ChessColour, type ChessEval, type ChessPage, type ChessSide, type ChessState } from '../../../web/src/sports/facts/chess'
import { fenAt, lineOfGame, samePosition, type Opening } from '../../../web/src/sports/facts/chessLine'
import { SwapIcon } from '../icons'
import { PageScroll } from '../page'
import { colour, num, t } from '../theme'
import { Btn, Card } from '../ui'
import { Muted, Title, type Board, type BoardProps, type MatchPart, type MatchProps, type Palette } from './board'
import ChessAnalysis from './chessAnalysis'
import { MatchSection } from './section'

const MOVE = 118 // a column of moves, at least (the web's .chess-moves grid)
const MOVE_GAP = 14

// The web's chess.css: light and dark squares, the last move's in yellow, white pieces outlined.
export const SQUARE = { light: '#ECE6D6', dark: '#8B6FD6', movedLight: '#F3E38A', movedDark: '#C9B34A' }
// Its evaluation bar (.chess-eval): White's share in White's paper, the rest in Black's ink, a grey mark at level;
// 22 wide beside a big board, 20 beside a small one, 6 from it. The share eases to each new one as the site's
// does (0.9 s, CSS's ease-in-out), and moves at once with the phone's Remove animations on.
export const BAR = { white: '#F4F1E8', black: '#141414', level: 'rgba(128, 128, 128, 0.6)', width: 22, small: 20, gap: 6 }
const EASE = { duration: 900, easing: Easing.bezier(0.42, 0, 0.58, 1), reduceMotion: ReduceMotion.System }

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

/** A piece of a board, by its FEN letter (white's in capitals): white outlined, as the web's .piece.w. */
export function Piece({ piece, size, style }: { piece: string; size: number; style?: StyleProp<TextStyle> }) {
  const white = piece === piece.toUpperCase()
  return <Text style={[{ fontSize: size, lineHeight: size * 1.1, color: white ? '#FBFAF6' : '#141414', textShadowColor: white ? '#000' : 'transparent', textShadowRadius: 1.5 }, style]}>{`${PIECES[piece.toLowerCase()]}︎`}</Text>
}

/** An evaluation bar (the web's EvalBar): White's share (whiteShare) filling it from White's end, the bottom
 * unless the board is turned (flip), and the score (evalScore) at the end of the side ahead. Live, it's the
 * server's evaluation, the one the swing alerts go by; on an analysis board, the fan's own engine's, and none yet
 * (null) while it starts, in the board's raised colour. To a screen reader it says the score and how deep the
 * engine looked (evalWords). */
export function EvalBar({ ev, flip, compact, pal }: { ev: ChessEval | null; flip: boolean; compact?: boolean; pal: Palette }) {
  if (!ev) return <View accessible accessibilityRole="image" accessibilityLabel="Engine evaluation: not yet" style={[c.bar, { width: compact ? BAR.small : BAR.width, outlineColor: pal.line, backgroundColor: pal.raised }]} />
  return <Bar ev={ev} flip={flip} compact={compact} pal={pal} />
}

function Bar({ ev, flip, compact, pal }: { ev: ChessEval; flip: boolean; compact?: boolean; pal: Palette }) {
  const share = whiteShare(ev)
  const white = share >= 50 // White ahead, or level
  const fill = useSharedValue(share)
  useEffect(() => {
    fill.value = withTiming(share, EASE)
  }, [fill, share])
  const filled = useAnimatedStyle(() => ({ height: `${fill.value}%` as const }))
  return (
    <View accessible accessibilityRole="image" accessibilityLabel={evalWords(ev)} style={[c.bar, { width: compact ? BAR.small : BAR.width, outlineColor: pal.line }]}>
      {/* Laid over the bar, not in it: a share of a height that comes from the board's beside it. */}
      <Animated.View style={[c.fill, flip ? { top: 0 } : { bottom: 0 }, filled]} />
      <View style={c.level} />
      <Text style={[num(compact ? 10 : 11), c.score, white === flip ? { top: 5 } : { bottom: 5 }, { color: white ? BAR.black : BAR.white }]}>{evalScore(ev)}</Text>
    </View>
  )
}

// A finished game's move tapped in its Moves list (ChessMatch), for the match page's board to play from: the
// board that offers Play from here listens while it's open.
type Ask = { moves: string[]; ply: number }
const listening = new Set<(ask: Ask) => void>()

/** The board from the position's FEN, with the last move marked and, while the game's on, the engine's evaluation
 * beside it when there is one: from the followed player's side (from White's with no one side of the fan's,
 * followedSide), until the fan turns it, which holds while the screen is open. On a paper card (a game none of
 * the fan's players are in), a small board with the players beside it, and neither. With `explore` (the match
 * page's), Play from here makes it an analysis board where it is (chessAnalysis.tsx), as does a finished game's
 * move tapped in the Moves list; Back to live makes it the game's again. */
function ChessBoard({ state, clock, compact, live, lines, explore, pal }: BoardProps<ChessState>) {
  const [side, setSide] = useState<ChessColour | null>(null)
  const [opening, setOpening] = useState<Opening | null>(null)
  const box = useRef<View>(null)
  const show = use(PageScroll)
  // Play from here: the match page's board, of a position that plays by the standard rules (not a Freestyle
  // game's before its castling).
  const playable = Boolean(explore && state.fen && standardCastling(state.fen))
  useEffect(() => {
    if (!playable || !state.fen) return
    const base = state.fen
    const asked = (ask: Ask) => {
      setOpening((o) => ({ base, last: state.last_move, moves: ask.moves, ply: ask.ply, asked: (o?.asked ?? 0) + 1 }))
      show?.(box.current)
    }
    listening.add(asked)
    return () => { listening.delete(asked) }
  }, [playable, state.fen, state.last_move, show])
  if (!state.white || !state.black) return null
  const { ranks, flip, moved } = position(state, live, (side ?? followedSide(live, lines)) === 'black')
  const onPaper = pal.paper
  // The live evaluation while the game's on: once it's over, the last one isn't what the result says (a draw
  // agreed at +3.0).
  const ev = onPaper || state.result ? null : state.eval
  const size = onPaper ? 100 : compact ? 176 : 280
  const glyph = onPaper ? 11 : compact ? 19 : 30
  // The game's board as it is, with `under` it what goes there: the bar's room beside the board, which keeps its
  // size; how deep the engine looked under a big board, or beside Play from here on one that can be explored.
  const still = (under?: ReactNode) => {
    const bar = ev ? (compact ? BAR.small : BAR.width) + BAR.gap : 0
    const depth = ev && !compact && !playable ? evalDepth(ev) : null
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
        {under}
      </View>
    )
  }
  const play = playable ? (
    <View style={c.under}>
      <Btn small label="Play from here" colours={{ fg: pal.text, border: pal.text }} onPress={() => setOpening({ base: state.fen ?? '', last: state.last_move, asked: 1 })} />
      {ev?.depth ? <Muted pal={pal}>Engine · depth {ev.depth}</Muted> : null}
    </View>
  ) : undefined
  const top = flip ? state.white : state.black
  const bottom = flip ? state.black : state.white
  return (
    <View ref={box} style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
      {ranks.length === 8 ? (
        opening ? <ChessAnalysis state={state} live={live} flip={flip} opening={opening} leave={() => setOpening(null)} still={still} size={size} pal={pal} /> : still(play)
      ) : null}
      {/* The players beside or under the board, the match score, result or clock between them. */}
      <View style={{ gap: 10, flex: 1, minWidth: onPaper || compact ? 140 : 180 }}>
        {player(top, flip ? 'white' : 'black', state, pal, onPaper)}
        <View style={c.middle}>
          <Text style={[num(compact ? 24 : 30), { color: pal.clock, flexShrink: 1 }]}>{chessMiddle(state, clock)}</Text>
          {/* The button that changes their places, not on a paper card's small board. Inside a card that opens its
              match page (Today's), it takes the tap itself: the innermost Pressable answers a touch. */}
          {ranks.length === 8 && !onPaper ? (
            <Pressable onPress={() => setSide(flip ? 'white' : 'black')} accessibilityRole="button" accessibilityLabel="Switch sides" hitSlop={4}
              style={({ pressed }) => [c.turn, { backgroundColor: pal.raised }, pressed && { opacity: 0.8 }]}>
              <SwapIcon size={18} color={pal.text} />
            </Pressable>
          ) : null}
        </View>
        {player(bottom, flip ? 'black' : 'white', state, pal, onPaper)}
        {(state.result || state.match) && clock ? <Text style={[t.small, { color: pal.muted }]}>{clock}</Text> : null}
      </View>
    </View>
  )
}

/** A finished game's match page under its board: the moves that led there, in pairs, as many columns as fit,
 * with its opening and how it ended; of a mini-match, the last game's (the one on the board). Each move opens the
 * board's analysis at the position after it where they replay as chess from the start to the position on the
 * board: a Freestyle game's, or one set up from a position, don't, and stay words. */
function ChessMatch({ page, live }: MatchProps<ChessPage>) {
  const [width, setWidth] = useState(0)
  const fen = (live?.state as ChessState | undefined)?.fen
  const moves = page.moves?.join(' ') ?? ''
  const replays = useMemo(() => {
    const sans = page.moves ? gameMoves(page.moves) : null
    if (!sans || !fen || !standardCastling(fen)) return null
    const game = lineOfGame(sans, sans.length)
    return game && samePosition(fenAt(game), fen) ? sans : null
    // By the moves' words: the page's next look, with the same moves, doesn't replay them again.
  }, [moves, fen])
  if (!page.moves?.length) return null
  const about = movesAbout(page)
  const columns = Math.max(1, Math.floor((width + MOVE_GAP) / (MOVE + MOVE_GAP)))
  const cell = width ? Math.floor((width - MOVE_GAP * (columns - 1)) / columns) : 0 // whole pixels: a row's sum never runs over
  const move = (ply: number) => {
    const san = replays?.[ply - 1]
    if (!replays || !san) return null
    const said = `${Math.ceil(ply / 2)}${ply % 2 ? '.' : '...'} ${san}`
    return (
      <Pressable onPress={() => listening.forEach((asked) => asked({ moves: replays, ply }))} accessibilityRole="button" accessibilityLabel={`Play on from ${said}`} hitSlop={6}
        style={({ pressed }) => [c.move, pressed && { backgroundColor: colour.raised }]}>
        <Text style={[t.small, c.nums]}>{san}</Text>
      </Pressable>
    )
  }
  return (
    <MatchSection title="Moves" note={page.game ? `Game ${page.game}` : null}>
      <Card pad style={{ gap: 10 }}>
        {about ? <Text style={[t.small, t.muted]}>{about}</Text> : null}
        {replays ? <Text style={[t.tiny, t.muted]}>Tap a move to play on from there.</Text> : null}
        <View onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: MOVE_GAP, rowGap: 4 }}>
          {cell > 0 ? page.moves.map((m, i) => replays ? (
            <View key={m} style={[c.pair, { width: cell }]}>
              <Text style={[t.small, c.nums]}>{i + 1}.</Text>
              {move(2 * i + 1)}
              {move(2 * i + 2)}
            </View>
          ) : <Text key={m} style={[t.small, c.nums, { width: cell }]}>{m}</Text>) : null}
        </View>
      </Card>
    </MatchSection>
  )
}

export const chess = { ...facts, Board: ChessBoard as Board, Match: ChessMatch as MatchPart }

const c = StyleSheet.create({
  // The ring is the web's 1px box-shadow in the board's line colour, outside the bar.
  bar: { borderRadius: 6, overflow: 'hidden', backgroundColor: BAR.black, outlineWidth: 1, outlineStyle: 'solid' },
  fill: { position: 'absolute', left: 0, right: 0, backgroundColor: BAR.white },
  level: { position: 'absolute', left: 0, right: 0, top: '50%', marginTop: -0.5, height: 1, backgroundColor: BAR.level },
  score: { position: 'absolute', left: 0, right: 0, textAlign: 'center', letterSpacing: 0 },
  middle: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  turn: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, // .btn.icon.chess-turn
  // A clock (.chess-clock): the running one ringed in red inside its box, as the web's inset shadow, so a clock
  // keeps its size when the turn passes; the ring is always there, clear when it's not running (Android draws a
  // Text whose border is taken away blank). Its padding is the web's less the ring.
  clock: { paddingHorizontal: 6, paddingVertical: 1, borderWidth: 2, borderRadius: 8, overflow: 'hidden' },
  clockSmall: { paddingHorizontal: 4, paddingVertical: 0 },
  under: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 6 }, // .chess-under
  // A finished game's move that opens the analysis board (.chess-move): its own touch, raised while pressed.
  pair: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  move: { marginVertical: -3, marginHorizontal: -2, paddingVertical: 3, paddingHorizontal: 5, borderRadius: 6 },
  nums: { fontVariant: ['tabular-nums'] },
})

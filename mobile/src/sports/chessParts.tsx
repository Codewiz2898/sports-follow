// What a chess board is drawn with, the game's and the analysis board's alike (chess.tsx, chessAnalysis.tsx): its
// squares' and its evaluation bar's colours, a piece, and the bar.

import { useEffect } from 'react'
import { StyleSheet, Text, View, type StyleProp, type TextStyle } from 'react-native'
import Animated, { Easing, ReduceMotion, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated'
import { evalScore, evalWords, PIECES, whiteShare, type ChessEval } from '../../../web/src/sports/facts/chess'
import { num } from '../theme'
import type { Palette } from './board'

// The web's chess.css: light and dark squares, the last move's in yellow, white pieces outlined.
export const SQUARE = { light: '#ECE6D6', dark: '#8B6FD6', movedLight: '#F3E38A', movedDark: '#C9B34A' }
// Its evaluation bar (.chess-eval): White's share in White's paper, the rest in Black's ink, a grey mark at level;
// 22 wide beside a big board, 20 beside a small one, 6 from it. The share eases to each new one as the site's
// does (0.9 s, CSS's ease-in-out), and moves at once with the phone's Remove animations on.
export const BAR = { white: '#F4F1E8', black: '#141414', level: 'rgba(128, 128, 128, 0.6)', width: 22, small: 20, gap: 6 }
const EASE = { duration: 900, easing: Easing.bezier(0.42, 0, 0.58, 1), reduceMotion: ReduceMotion.System }

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
  if (!ev) return <View accessible accessibilityRole="image" accessibilityLabel="Engine evaluation: not yet" style={[p.bar, { width: compact ? BAR.small : BAR.width, outlineColor: pal.line, backgroundColor: pal.raised }]} />
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
    <View accessible accessibilityRole="image" accessibilityLabel={evalWords(ev)} style={[p.bar, { width: compact ? BAR.small : BAR.width, outlineColor: pal.line }]}>
      {/* Laid over the bar, not in it: a share of a height that comes from the board's beside it. */}
      <Animated.View style={[p.fill, flip ? { top: 0 } : { bottom: 0 }, filled]} />
      <View style={p.level} />
      <Text style={[num(compact ? 10 : 11), p.score, white === flip ? { top: 5 } : { bottom: 5 }, { color: white ? BAR.black : BAR.white }]}>{evalScore(ev)}</Text>
    </View>
  )
}

const p = StyleSheet.create({
  // The ring is the web's 1px box-shadow in the board's line colour, outside the bar.
  bar: { borderRadius: 6, overflow: 'hidden', backgroundColor: BAR.black, outlineWidth: 1, outlineStyle: 'solid' },
  fill: { position: 'absolute', left: 0, right: 0, backgroundColor: BAR.white },
  level: { position: 'absolute', left: 0, right: 0, top: '50%', marginTop: -0.5, height: 1, backgroundColor: BAR.level },
  score: { position: 'absolute', left: 0, right: 0, textAlign: 'center', letterSpacing: 0 },
})

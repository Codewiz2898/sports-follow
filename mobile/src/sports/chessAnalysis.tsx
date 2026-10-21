// A fan's analysis board on a chess game's match page (Play from here), the web's chessAnalysis.tsx: the board
// made playable where it is, from the game's position (or a finished game's move), with an engine of their own on
// the phone (chessEngine.tsx) filling the bar. Its line, its rules and its words are the web's
// (facts/chessLine.ts). Nothing the fan plays leaves the phone, so it never reaches an alert.

import { useIsFocused } from 'expo-router'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Linking, PanResponder, Pressable, StyleSheet, Text, View, type GestureResponderEvent } from 'react-native'
import Svg, { Circle, Defs, Path, RadialGradient, Rect, Stop } from 'react-native-svg'
import { safeHref } from '../../../web/src/format'
import { PIECES, position, type ChessEval, type ChessState } from '../../../web/src/sports/facts/chess'
import { checked, ended, fenAt, lineWhere, lookFromWhite, movedOn, numbered, openingLine, PIECE_NAMES, played, samePosition, sanLine, squareWords, stepped, targets, type Opening } from '../../../web/src/sports/facts/chessLine'
import type { LiveStatus } from '../api'
import { BackIcon, ChevronIcon, CloseIcon } from '../icons'
import { font, t } from '../theme'
import { Btn } from '../ui'
import { useInFront } from '../watch'
import type { Palette } from './board'
import { BAR, EvalBar, Piece, SQUARE } from './chess'
import { ENGINE_LICENCE, ENGINE_SOURCE, EngineView, startEngine, type Engine } from './chessEngine'

const PROMOTIONS = ['q', 'r', 'b', 'n']
const INK = '#141414'
// A finger's travel before a touch on a piece is a drag, not a tap (the web's 6px).
const SLOP = 6
// A dragged piece floats bigger, above the finger, to be seen (the web's .chess-ghost.touch).
const GHOST = 48

const open = (url: string) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

/** The marks on a square, as the web's backgrounds (chess.css): a dot where the picked piece can go, a ring round
 * a piece it can take, a king in check glowing amber (red means live here). Drawn on a 100 × 100 square. */
function Marks({ aim, take, check }: { aim: boolean; take: boolean; check: boolean }) {
  if (!aim && !check) return null
  return (
    <Svg style={StyleSheet.absoluteFill} viewBox="0 0 100 100" pointerEvents="none">
      {check ? (
        <>
          <Defs>
            <RadialGradient id="check" cx="50" cy="50" r="51" gradientUnits="userSpaceOnUse">
              <Stop offset="0" stopColor="#F59E0B" />
              <Stop offset="0.625" stopColor="#F59E0B" stopOpacity="0.55" />
              <Stop offset="1" stopColor="#F59E0B" stopOpacity="0" />
            </RadialGradient>
          </Defs>
          <Rect width="100" height="100" fill="url(#check)" />
        </>
      ) : null}
      {aim && take ? <Path d="M0 0H100V100H0Z M7 50a43 43 0 1 0 86 0a43 43 0 1 0 -86 0Z" fillRule="evenodd" fill="rgba(20, 20, 20, 0.4)" /> : null}
      {aim && !take ? <Circle cx="50" cy="50" r="13" fill="rgba(20, 20, 20, 0.4)" /> : null}
    </Svg>
  )
}

/** A step along the line (.chess-step): a round button on the board's raised colour. */
function Step({ label, icon, off, onPress, pal }: { label: string; icon: ReactNode; off: boolean; onPress: () => void; pal: Palette }) {
  return (
    <Pressable onPress={() => { if (!off) onPress() }} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: off }} hitSlop={4}
      style={({ pressed }) => [s.step, { backgroundColor: pal.raised }, off ? { opacity: 0.45 } : pressed && { opacity: 0.8 }]}>
      {icon}
    </Pressable>
  )
}

/** The analysis board: the fan's line (ChessLine) on the board, played by tap then tap, or a drag with a finger,
 * the legal squares of the picked piece marked; a promotion asks what the pawn becomes. Back and forward step
 * along the line, and Back to live leaves (`leave`), the engine with it. `state` is the game's, live: when it moves
 * on, their line stays and a line says so. `flip`: the board's turn, the fan's Switch sides. `still`: the game's own
 * board with something under it, for a position that can't be played. `size`: the board's width. */
export default function ChessAnalysis({ state, live, flip, opening, leave, still, size, pal }: { state: ChessState; live: LiveStatus; flip: boolean; opening: Opening; leave: () => void; still: (under: ReactNode) => ReactNode; size: number; pal: Palette }) {
  const [line, setLine] = useState(() => openingLine(opening))
  const [asked, setAsked] = useState(opening.asked)
  const [picked, setPicked] = useState<string | null>(null)
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null)
  const [ghost, setGhost] = useState<{ piece: string; x: number; y: number } | null>(null) // a piece being dragged
  const [engine, setEngine] = useState<{ ready: boolean; failed: boolean }>({ ready: false, failed: false })
  const [worker, setWorker] = useState<Engine | null>(null)
  const [look, setLook] = useState<{ fen: string; ev: ChessEval; pv: string[] } | null>(null)
  const [width, setWidth] = useState(0) // the board's, once laid out
  const front = useInFront()
  const focused = useIsFocused()

  // A new start (another of the game's moves tapped) while the board is open: its line, the engine kept.
  if (asked !== opening.asked) {
    setAsked(opening.asked)
    setLine(openingLine(opening))
    setPicked(null)
    setPromoting(null)
  }
  const fen = line ? fenAt(line) : null
  // Of the position, not of each of the engine's looks at it.
  const over = useMemo(() => (fen ? ended(fen) : null), [fen])
  const check = useMemo(() => (fen ? checked(fen) : null), [fen])

  // The engine: one for the board while it's open, ended when the fan leaves it, or the page (another page over
  // it in the stack too: started again when they're back); none for a board that can't be played.
  const plays = line != null && focused
  useEffect(() => {
    if (!plays) return
    setEngine({ ready: false, failed: false })
    const started = startEngine({
      ready: () => setEngine((e) => ({ ...e, ready: true })),
      failed: () => setEngine((e) => ({ ...e, failed: true })),
    })
    setWorker(started)
    return () => {
      started.quit()
      setWorker(null)
    }
  }, [plays])
  // Each position the board comes to, looked at afresh (the last search stops); an ended one needs no look, and
  // nor does a board behind another app: it stops there, and looks again on the fan's return.
  useEffect(() => {
    if (!worker) return
    if (fen && !over && front) worker.look(fen, (seen) => setLook({ fen, ev: lookFromWhite(seen, fen), pv: seen.pv }))
    else worker.stop()
  }, [worker, fen, over, front])

  const best = useMemo(() => (look && look.fen === fen && look.pv.length ? numbered(sanLine(look.fen, look.pv, 5), look.fen) : null), [look, fen])

  // What a touch does, from the latest render: the board takes every touch on it (its responder is made once).
  const latest = useRef<{ at: (x: number, y: number) => { name: string; piece: string | null } | null; mine: (piece: string | null) => boolean; tap: (name: string, piece: string | null) => void; drop: (from: string, to: string | null) => void; busy: boolean } | null>(null)
  const touch = useRef<{ from: { name: string; piece: string | null }; x: number; y: number; grab: boolean; dragging: boolean } | null>(null)
  const pan = useMemo(() => {
    const where = (e: GestureResponderEvent) => ({ x: e.nativeEvent.locationX, y: e.nativeEvent.locationY })
    const end = () => {
      touch.current = null
      setGhost(null)
    }
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      // A finger drags only from a piece of the side to move, which keeps the touch: a swipe from anywhere else on
      // the board still scrolls the page (the web's touch-action: none on .mine squares alone).
      onPanResponderGrant: (e) => {
        const now = latest.current
        const { x, y } = where(e)
        const from = now?.at(x, y)
        touch.current = from ? { from, x, y, grab: !now?.busy && now?.mine(from.piece) === true, dragging: false } : null
      },
      onShouldBlockNativeResponder: () => touch.current?.grab ?? false,
      onPanResponderTerminationRequest: () => !touch.current?.grab,
      onPanResponderMove: (_e, g) => {
        const held = touch.current
        if (!held?.grab || !held.from.piece) return
        if (!held.dragging && Math.hypot(g.dx, g.dy) < SLOP) return
        if (!held.dragging) setPicked(held.from.name)
        held.dragging = true
        setGhost({ piece: held.from.piece, x: held.x + g.dx, y: held.y + g.dy })
      },
      onPanResponderRelease: (_e, g) => {
        const held = touch.current
        end()
        if (!held) return
        if (held.dragging) latest.current?.drop(held.from.name, latest.current.at(held.x + g.dx, held.y + g.dy)?.name ?? null)
        else latest.current?.tap(held.from.name, held.from.piece)
      },
      onPanResponderTerminate: end,
    })
  }, [])

  if (!line || !fen) {
    return still(
      <View style={s.row}>
        <Text style={[t.small, { color: pal.text }]}>This position can't be played from here</Text>
        <Btn small label="Back to the game" colours={{ fg: pal.text, border: pal.text }} onPress={leave} />
      </View>,
    )
  }

  const turn = fen.split(' ')[1] === 'b' ? 'b' : 'w'
  const mine = (piece: string | null): piece is string => piece != null && (piece === piece.toUpperCase()) === (turn === 'w')
  const last = line.at ? line.plies[line.at - 1] : null
  const { ranks, moved } = position({ ...state, fen, last_move: last ? last.from + last.to : samePosition(fen, opening.base) ? opening.last : null }, live, flip)
  const aims = new Map<string, boolean>(picked ? targets(fen, picked).map((m) => [m.to, m.promotes] as const) : [])
  // The bar: the engine's look at this position, the last one's until it has one, and an ended game's result.
  const ev: ChessEval | null = over ? (over === 'Checkmate' ? { mate: 0, fen } : { cp: 0, fen }) : look?.fen === fen ? look.ev : look?.ev ?? null

  const go = (from: string, to: string, promotes: boolean) => {
    if (promotes) return setPromoting({ from, to })
    const next = played(line, from, to)
    if (next) setLine(next)
    setPicked(null)
  }
  const tap = (name: string, piece: string | null) => {
    if (promoting) return
    const aim = aims.get(name)
    if (picked && aim != null) return go(picked, name, aim)
    setPicked(mine(piece) && picked !== name ? name : null)
  }
  const drop = (from: string, to: string | null) => {
    const aim = to ? targets(fen, from).find((m) => m.to === to) : undefined
    if (aim) go(from, aim.to, aim.promotes)
    else setPicked(null)
  }
  // A promotion chosen, or let go (null).
  const promote = (piece: string | null) => {
    const next = promoting && piece ? played(line, promoting.from, promoting.to, piece) : null
    if (next) setLine(next)
    setPromoting(null)
    setPicked(null)
  }
  const step = (by: number) => {
    setLine(stepped(line, by))
    setPicked(null)
    setPromoting(null)
  }
  const square = width / 8
  latest.current = {
    at: (x, y) => (square ? ranks[Math.floor(y / square)]?.[Math.floor(x / square)] ?? null : null),
    mine,
    tap,
    drop,
    busy: promoting != null,
  }

  const side = turn === 'w' ? 'White' : 'Black'
  const where = lineWhere(line, opening.base)
  const onward = movedOn(state, opening.base)
  const engineWords = over ?? (engine.failed ? 'Engine unavailable: the moves still play.'
    : best ? `Best: ${best}${look?.ev.depth ? ` · depth ${look.ev.depth}` : ''}`
      : !engine.ready ? 'Loading the engine…' : 'The engine is looking…')
  const glyph = size >= 280 ? 30 : 19

  return (
    <View style={{ width: size + BAR.width + BAR.gap, maxWidth: '100%', gap: 4 }}>
      <View style={{ flexDirection: 'row', gap: BAR.gap }}>
        <EvalBar ev={ev} flip={flip} pal={pal} />
        <View style={{ flex: 1 }}>
          <View {...pan.panHandlers} pointerEvents="box-only" onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={s.board}>
            {ranks.map((rank, r) => (
              <View key={r} style={{ flex: 1, flexDirection: 'row' }}>
                {rank.map((sq) => (
                  <View
                    key={sq.name}
                    accessible
                    accessibilityRole="button"
                    accessibilityLabel={squareWords(sq.name, sq.piece, aims.has(sq.name))}
                    accessibilityState={{ selected: picked === sq.name }}
                    accessibilityActions={[{ name: 'activate' }]}
                    onAccessibilityAction={() => tap(sq.name, sq.piece)}
                    style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: moved.has(sq.name) ? (sq.dark ? SQUARE.movedDark : SQUARE.movedLight) : sq.dark ? SQUARE.dark : SQUARE.light }}
                  >
                    <Marks aim={aims.has(sq.name)} take={Boolean(sq.piece)} check={check === sq.name} />
                    {picked === sq.name ? <View pointerEvents="none" style={s.picked} /> : null}
                    {sq.piece ? <Piece piece={sq.piece} size={glyph} style={ghost && picked === sq.name ? { opacity: 0.35 } : undefined} /> : null}
                  </View>
                ))}
              </View>
            ))}
          </View>
          {/* What a pawn on the last rank becomes, over the board. */}
          {promoting ? (
            <View style={s.promoteWrap} pointerEvents="box-none">
              <View style={s.promote} accessibilityLabel="Promote the pawn to">
                {PROMOTIONS.map((p) => (
                  <Pressable key={p} onPress={() => promote(p)} accessibilityRole="button" accessibilityLabel={PIECE_NAMES[p]} style={({ pressed }) => [s.promoteTo, pressed && { opacity: 0.8 }]}>
                    <Piece piece={turn === 'w' ? p.toUpperCase() : p} size={30} />
                  </Pressable>
                ))}
                <Pressable onPress={() => promote(null)} accessibilityRole="button" accessibilityLabel="Cancel" style={s.cancel}>
                  <CloseIcon size={18} color="#F4F1E8" />
                </Pressable>
              </View>
            </View>
          ) : null}
          {ghost ? (
            <View pointerEvents="none" style={[s.ghost, { left: ghost.x - GHOST / 2, top: ghost.y - GHOST * 1.15 }]}>
              <Piece piece={ghost.piece} size={GHOST} />
            </View>
          ) : null}
        </View>
      </View>
      <View style={s.under}>
        <View style={s.row}>
          <Step label="Back a move" icon={<BackIcon size={18} color={pal.text} />} off={!line.at} onPress={() => step(-1)} pal={pal} />
          <Step label="Forward a move" icon={<ChevronIcon size={18} color={pal.text} />} off={line.at === line.plies.length} onPress={() => step(1)} pal={pal} />
          <Btn small kind={onward ? 'primary' : 'outline'} label={state.result ? 'Back to the game' : 'Back to live'} onPress={leave}
            colours={onward ? { fg: pal.ground, bg: pal.text, border: pal.text } : { fg: pal.text, border: pal.text }} />
        </View>
        <Text style={[t.small, { color: pal.text }]} accessibilityLiveRegion="polite">{where} · {side} to move</Text>
        {onward ? <Text style={[t.small, { color: pal.text, fontFamily: font[800] }]} accessibilityLiveRegion="polite">{onward}</Text> : null}
        <Text style={[t.tiny, { color: pal.text }]}>{engineWords}</Text>
        <Text style={[t.tiny, { color: pal.muted }]}>
          Engine: Stockfish 18 (<Text onPress={open(ENGINE_LICENCE)} accessibilityRole="link" style={t.link}>GPL-3.0</Text>) · <Text onPress={open(ENGINE_SOURCE)} accessibilityRole="link" style={t.link}>source</Text>
        </Text>
      </View>
      {worker && !engine.failed ? <EngineView engine={worker} /> : null}
    </View>
  )
}

const s = StyleSheet.create({
  board: { aspectRatio: 1, borderRadius: 8, overflow: 'hidden' },
  // The picked piece's square, ringed in ink inside it.
  picked: { ...StyleSheet.absoluteFill, borderWidth: 3, borderColor: INK },
  promoteWrap: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', zIndex: 2 },
  promote: { flexDirection: 'row', gap: 4, padding: 6, borderRadius: 12, backgroundColor: 'rgba(20, 20, 20, 0.9)' },
  promoteTo: { width: 44, height: 44, borderRadius: 8, alignItems: 'center', justifyContent: 'center', backgroundColor: '#ECE6D6' }, // a light square's
  cancel: { width: 32, height: 44, alignItems: 'center', justifyContent: 'center' },
  ghost: { position: 'absolute', width: GHOST, height: GHOST * 1.1, alignItems: 'center', justifyContent: 'center', zIndex: 3 },
  under: { marginTop: 6, gap: 6 },
  row: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  step: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, // .btn.icon.chess-step
})

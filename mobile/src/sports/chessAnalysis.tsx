// A fan's analysis board on a chess game's match page (Play from here), the web's chessAnalysis.tsx: a board of its
// own over the screen, as big as the phone lets it be, from the game's position (or a move in its list), with an
// engine of their own on the phone (chessEngine.tsx) filling the bar and showing its best lines. Its line, its rules
// and its words are the web's (facts/chessLine.ts). Nothing the fan plays leaves the phone, so it never reaches an
// alert.

import { useIsFocused } from 'expo-router'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Linking, Modal, PanResponder, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View, type GestureResponderEvent } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Svg, { Circle, Defs, Line, Path, Polygon, RadialGradient, Rect, Stop } from 'react-native-svg'
import { safeHref } from '../../../web/src/format'
import { evalScore, position, standardCastling, whiteShare, type ChessEval, type ChessState } from '../../../web/src/sports/facts/chess'
import { checked, ended, fenAt, lineFrom, lineWhere, lookFromWhite, movedOn, numberedMoves, openingLine, PIECE_NAMES, playable, played, playedOn, samePosition, sanLine, squareWords, stepped, targets, type ChessLine, type EngineLook, type Opening } from '../../../web/src/sports/facts/chessLine'
import type { LiveStatus } from '../api'
import { BackIcon, ChevronIcon, CloseIcon, FirstIcon, LastIcon, RefreshIcon, SwapIcon } from '../icons'
import { colour, font, num, t } from '../theme'
import { Btn } from '../ui'
import { useInFront } from '../watch'
import { BAR, EvalBar, Piece, SQUARE } from './chessParts'
import { ENGINE_LICENCE, ENGINE_SOURCE, EngineView, startEngine, type Engine } from './chessEngine'

const PROMOTIONS = ['q', 'r', 'b', 'n']
const INK = '#141414'
// A finger's travel before a touch on a piece is a drag, not a tap (the web's 6px).
const SLOP = 6
// The engine's best lines it shows, each a tap from being played; more would take it longer to reach its depth.
const LINES = 3
// The moves of each line it shows.
const LINE_MOVES = 8
// The board: up to this wide (the web's 720px), and short enough to leave the header and the steps in sight.
const MOST = 720
const ROOM = 230 // the screen's height the header, the steps and the padding take
const PAD = 16

// The box's own colours: ink on white, as the web's dialog (.chess-study).
const pal = { ground: colour.surface, text: colour.text, muted: colour.muted, raised: colour.raised, line: colour.line, clock: colour.liveText, note: colour.liveText, ours: colour.text, score: 52, paper: false }

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

/** The arrow of the engine's best move (UCI, "e2e4") over the board as it's drawn (the web's BestArrow): from the
 * middle of the square it leaves to the middle of the one it goes to, on a board of eight by eight. */
function BestArrow({ move, flip }: { move: string; flip: boolean }) {
  const at = (square: string) => {
    const file = 'abcdefgh'.indexOf(square[0])
    const rank = Number(square[1])
    return { x: (flip ? 7 - file : file) + 0.5, y: (flip ? rank - 1 : 8 - rank) + 0.5 }
  }
  const from = at(move.slice(0, 2))
  const to = at(move.slice(2, 4))
  const length = Math.hypot(to.x - from.x, to.y - from.y)
  if (!length) return null
  const u = { x: (to.x - from.x) / length, y: (to.y - from.y) / length }
  // The head: its point short of the square's middle, its base across the shaft's end.
  const tip = { x: to.x - u.x * 0.12, y: to.y - u.y * 0.12 }
  const base = { x: tip.x - u.x * 0.42, y: tip.y - u.y * 0.42 }
  const side = { x: -u.y * 0.24, y: u.x * 0.24 }
  const head = [tip, { x: base.x + side.x, y: base.y + side.y }, { x: base.x - side.x, y: base.y - side.y }].map((p) => `${p.x},${p.y}`).join(' ')
  return (
    <Svg style={StyleSheet.absoluteFill} viewBox="0 0 8 8" pointerEvents="none">
      <Line x1={from.x} y1={from.y} x2={base.x} y2={base.y} stroke="rgba(20, 20, 20, 0.62)" strokeWidth={0.16} strokeLinecap="round" />
      <Polygon points={head} fill="rgba(20, 20, 20, 0.62)" />
    </Svg>
  )
}

/** A round button on the box's raised colour (.chess-step): a step along the line, Flip, Start over. */
function Step({ label, icon, off, onPress }: { label: string; icon: ReactNode; off?: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={() => { if (!off) onPress() }} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: Boolean(off) }} hitSlop={4}
      style={({ pressed }) => [s.step, { backgroundColor: pal.raised }, off ? { opacity: 0.45 } : pressed && { opacity: 0.8 }]}>
      {icon}
    </Pressable>
  )
}

/** An on/off switch as a small pill (.btn.toggle): filled in ink when on. Named by `label`, its state its own. */
function Toggle({ label, on, words, onPress }: { label: string; on: boolean; words: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="switch" accessibilityLabel={label} accessibilityState={{ checked: on }} hitSlop={4}
      style={({ pressed }) => [s.toggle, on && { backgroundColor: INK }, pressed && { opacity: 0.8 }]}>
      <Text style={[s.toggleWords, { color: on ? colour.surface : INK }]}>{words}</Text>
    </Pressable>
  )
}

/** The analysis board over the screen (a Modal, the web's dialog): the fan's line (ChessLine) on the board, played
 * by tap then tap, or a drag with a finger, the legal squares of the picked piece marked; a promotion asks what the
 * pawn becomes. Under it: its steps, Flip and Start over; where the line is; the engine's best lines (each move a
 * tap from being played to) with its best move as an arrow, the engine and the arrow each switched off with a tap;
 * the fan's moves (each a tap from being gone back to). The back button, or Back to live, closes it (`leave`), the
 * engine with it. `state` is the game's, live: when it moves on, the fan's line stays, a line says so and offers its
 * new position. `flip`: the game's board's side, the one the analysis starts from. */
export default function ChessAnalysis({ state, live, flip, opening, leave }: { state: ChessState; live: LiveStatus; flip: boolean; opening: Opening; leave: () => void }) {
  const [line, setLine] = useState(() => openingLine(opening))
  const [base, setBase] = useState({ fen: opening.base, last: opening.last ?? null })
  const [turned, setTurned] = useState(flip)
  const [engineOn, setEngineOn] = useState(true)
  const [arrowOn, setArrowOn] = useState(true)
  const [picked, setPicked] = useState<string | null>(null)
  const [promoting, setPromoting] = useState<{ from: string; to: string } | null>(null)
  const [ghost, setGhost] = useState<{ piece: string; x: number; y: number } | null>(null) // a piece being dragged
  const [engine, setEngine] = useState<{ ready: boolean; failed: boolean; gone: boolean }>({ ready: false, failed: false, gone: false })
  const [round, setRound] = useState(0) // each engine the board has started: another after one Android ended
  const [worker, setWorker] = useState<Engine | null>(null)
  // The engine's lines at a position, by their number (1, its best), each its latest look.
  const [looks, setLooks] = useState<{ fen: string; lines: EngineLook[] } | null>(null)
  const [width, setWidth] = useState(0) // the board's, once laid out
  const front = useInFront()
  const focused = useIsFocused()
  const insets = useSafeAreaInsets()
  const screen = useWindowDimensions()
  const away = useRef(false) // the app has been behind another since this engine started

  const fen = line ? fenAt(line) : null
  // Of the position, not of each of the engine's looks at it.
  const over = useMemo(() => (fen ? ended(fen) : null), [fen])
  const check = useMemo(() => (fen ? checked(fen) : null), [fen])

  // The engine: one for the board while it's open, ended when the fan leaves it, or the page (another page over
  // it in the stack too: started again when they're back); none for a board that can't be played.
  const plays = line != null && focused
  useEffect(() => {
    if (!plays) return
    away.current = false
    setEngine({ ready: false, failed: false, gone: false })
    const started = startEngine({
      ready: () => setEngine((e) => ({ ...e, ready: true })),
      failed: (gone) => setEngine((e) => ({ ...e, failed: true, gone })),
    }, LINES)
    setWorker(started)
    return () => {
      started.quit()
      setWorker(null)
    }
  }, [plays, round])
  // Behind another app the board keeps its engine, only stopping its search (below), so coming back doesn't compile
  // the 7 MB engine again. If Android ended its page once the app had been behind another (the phone short of
  // memory), a fresh one starts with the fan back on the board; one that goes while they've been on it all along
  // says so, and stays gone until they open the board again.
  useEffect(() => {
    if (!front) away.current = true
    else if (away.current && engine.gone) setRound((r) => r + 1)
  }, [front, engine.gone])
  // Each position the board comes to, looked at afresh (the last search stops); an ended one needs no look, nor
  // does one with the engine off, nor a board behind another app: it stops there, and looks again on the fan's
  // return.
  useEffect(() => {
    if (!worker) return
    if (fen && !over && front && engineOn) {
      worker.look(fen, (seen) => setLooks((was) => {
        const lines = was?.fen === fen ? [...was.lines] : []
        lines[seen.line - 1] = seen
        return { fen, lines }
      }))
    } else worker.stop()
  }, [worker, fen, over, front, engineOn])

  // The engine's lines at this position, best first, in SAN, each with its score from White's side.
  const shown = useMemo(() => {
    if (!fen || !looks || looks.fen !== fen) return []
    return looks.lines.filter(Boolean).map((look) => ({ look, ev: lookFromWhite(look, fen), moves: numberedMoves(sanLine(fen, look.pv, LINE_MOVES), fen) }))
  }, [looks, fen])

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
      // the board still scrolls (the web's touch-action: none on .mine squares alone).
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

  // Android's back: lets go of a promotion or a picked piece first, then closes.
  const back = () => {
    if (promoting || picked) {
      setPromoting(null)
      setPicked(null)
    } else leave()
  }
  const head = (words: string) => (
    <View style={s.head}>
      <View style={{ flexShrink: 1, gap: 2 }}>
        <Text style={t.eyebrow}>Play from here</Text>
        <Text style={[t.title, { fontSize: 17, lineHeight: 22 }]}>{[state.white?.name, state.black?.name].filter(Boolean).join(' v ')}</Text>
      </View>
      <Btn small kind={movedOn(state, base.fen) ? 'primary' : 'outline'} label={words} icon={<CloseIcon size={16} color={movedOn(state, base.fen) ? colour.bg : colour.text} />} onPress={leave} />
    </View>
  )
  const frame = (children: ReactNode) => (
    // Its own window, over the screen while the screen is in front: a screen pushed over it (a notification's, a
    // link's) shows, and the board comes back with the screen.
    <Modal visible={focused} animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={back}>
      <ScrollView style={{ backgroundColor: colour.surface }} contentContainerStyle={{ paddingTop: insets.top + 14, paddingBottom: insets.bottom + 24, paddingHorizontal: PAD, gap: 14 }}>
        {children}
      </ScrollView>
    </Modal>
  )

  if (!line || !fen) {
    return frame(
      <>
        {head('Back to the game')}
        <Text style={t.small} accessibilityLiveRegion="polite">This position can't be played from here</Text>
      </>,
    )
  }

  const turn = fen.split(' ')[1] === 'b' ? 'b' : 'w'
  const mine = (piece: string | null): piece is string => piece != null && (piece === piece.toUpperCase()) === (turn === 'w')
  const last = line.at ? line.plies[line.at - 1] : null
  const { ranks, moved } = position({ ...state, fen, last_move: last ? last.from + last.to : samePosition(fen, base.fen) ? base.last : null }, live, turned)
  const aims = new Map<string, boolean>(picked ? targets(fen, picked).map((m) => [m.to, m.promotes] as const) : [])
  // The bar: the engine's look at this position, the last one's until it has one, and an ended game's result.
  const best = shown[0]
  const lastLook = looks?.lines[0]
  const ev: ChessEval | null = over ? (over === 'Checkmate' ? { mate: 0, fen } : { cp: 0, fen }) : best ? best.ev : looks && lastLook ? lookFromWhite(lastLook, looks.fen) : null

  // The board somewhere else along the line (or a new line): what was picked is let go.
  const goTo = (next: ChessLine) => {
    setLine(next)
    setPicked(null)
    setPromoting(null)
  }
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
  const square = width / 8
  latest.current = {
    at: (x, y) => (square ? ranks[Math.floor(y / square)]?.[Math.floor(x / square)] ?? null : null),
    mine,
    tap,
    drop,
    busy: promoting != null,
  }

  const side = turn === 'w' ? 'White' : 'Black'
  const where = lineWhere(line, base.fen)
  const onward = movedOn(state, base.fen)
  // The game's position now, for a fan whose line began before it: one that can be played here.
  const now = onward && state.fen && playable(state.fen) && standardCastling(state.fen) ? state.fen : null
  const fromNow = () => {
    if (!now) return
    setBase({ fen: now, last: state.last_move ?? null })
    goTo(lineFrom(now))
  }
  const startOver = () => {
    const fresh = openingLine({ ...opening, base: base.fen, moves: samePosition(base.fen, opening.base) ? opening.moves : null })
    if (fresh) goTo(fresh)
  }
  const engineWords = over ?? (!engineOn ? 'The engine is off.'
    : engine.failed ? 'Engine unavailable: the moves still play.'
      : !engine.ready ? 'Loading the engine…'
        : !best ? 'The engine is looking…' : null)
  const fans = numberedMoves(line.plies.map((m) => m.san), line.start)
  const arrow = engineOn && arrowOn && !over ? best?.look.pv[0] : undefined
  // As wide as the screen, short enough to leave the header and the steps in sight, up to MOST.
  const bar = engineOn ? BAR.width + BAR.gap : 0
  const size = Math.max(200, Math.min(screen.width - PAD * 2 - bar, screen.height - insets.top - insets.bottom - ROOM, MOST))
  const glyph = Math.round((size / 8) * 0.86)
  const ghostSize = Math.round(glyph * 1.4)

  return frame(
    <>
      {head(state.result ? 'Back to the game' : 'Back to live')}
      <View style={{ width: size + bar, alignSelf: 'center', gap: 12 }}>
        <View style={{ flexDirection: 'row', gap: BAR.gap }}>
          {engineOn ? <EvalBar ev={ev} flip={turned} pal={pal} /> : null}
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
              {arrow ? <BestArrow move={arrow} flip={turned} /> : null}
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
            {/* A dragged piece floats bigger, above the finger, to be seen (the web's .chess-ghost.touch). */}
            {ghost ? (
              <View pointerEvents="none" style={[s.ghost, { width: ghostSize, height: ghostSize * 1.1, left: ghost.x - ghostSize / 2, top: ghost.y - ghostSize * 1.15 }]}>
                <Piece piece={ghost.piece} size={ghostSize} />
              </View>
            ) : null}
          </View>
        </View>
        <View style={s.steps}>
          <View style={s.row}>
            <Step label="To the start" icon={<FirstIcon size={18} color={pal.text} />} off={!line.at} onPress={() => goTo(stepped(line, -line.at))} />
            <Step label="Back a move" icon={<BackIcon size={18} color={pal.text} />} off={!line.at} onPress={() => goTo(stepped(line, -1))} />
            <Step label="Forward a move" icon={<ChevronIcon size={18} color={pal.text} />} off={line.at === line.plies.length} onPress={() => goTo(stepped(line, 1))} />
            <Step label="To the end" icon={<LastIcon size={18} color={pal.text} />} off={line.at === line.plies.length} onPress={() => goTo(stepped(line, line.plies.length - line.at))} />
          </View>
          <View style={s.row}>
            <Step label="Flip the board" icon={<SwapIcon size={18} color={pal.text} />} onPress={() => setTurned((x) => !x)} />
            <Step label="Start over" icon={<RefreshIcon size={18} color={pal.text} />} onPress={startOver} />
          </View>
        </View>
      </View>
      <View style={{ gap: 6 }}>
        <Text style={t.small} accessibilityLiveRegion="polite"><Text style={{ fontFamily: font[800] }}>{where}</Text> · {side} to move</Text>
        {onward ? (
          <View style={s.row}>
            <Text style={[t.small, { fontFamily: font[800] }]} accessibilityLiveRegion="polite">{onward}</Text>
            {now ? <Btn small label={state.result ? 'Play from the end' : 'Play from there'} onPress={fromNow} /> : null}
          </View>
        ) : null}
      </View>
      <View style={s.part}>
        <View style={s.partHead}>
          <View style={[s.row, { gap: 10 }]}>
            <Text style={t.eyebrow}>Engine</Text>
            {engineOn && best ? <Text style={[t.tiny, t.muted]}>depth {best.look.depth}</Text> : null}
          </View>
          <View style={s.row}>
            {engineOn ? <Toggle label="Best move arrow" on={arrowOn} words="Arrow" onPress={() => setArrowOn((a) => !a)} /> : null}
            <Toggle label="Engine" on={engineOn} words={engineOn ? 'On' : 'Off'} onPress={() => setEngineOn((o) => !o)} />
          </View>
        </View>
        {engineWords ? <Text style={[t.small, t.muted]}>{engineWords}</Text> : null}
        {engineOn ? shown.map(({ look, ev: score, moves }) => (
          <View key={look.line} style={s.pv}>
            <Text style={[num(14), s.pvScore, whiteShare(score) >= 50 ? s.pvWhite : s.pvBlack]}>{evalScore(score)}</Text>
            <View style={s.pvMoves}>
              {moves.map((m, i) => (
                <Pressable key={i} onPress={() => goTo(playedOn(line, look.pv.slice(0, i + 1)))} accessibilityRole="button" accessibilityLabel={`Play the line to ${m.said}`} hitSlop={3}
                  style={({ pressed }) => [s.move, pressed && { backgroundColor: pal.raised }]}>
                  <Text style={[t.small, s.nums]}>{m.number ? <Text style={t.muted}>{m.number} </Text> : null}{m.san}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        )) : null}
      </View>
      <View style={s.part}>
        <Text style={t.eyebrow}>Moves</Text>
        {fans.length ? (
          <View style={s.line}>
            {fans.map((m, i) => {
              const at = i + 1 === line.at
              return (
                <View key={i} style={s.pair}>
                  {m.number ? <Text style={[t.small, s.nums, t.muted]}>{m.number}</Text> : null}
                  <Pressable onPress={() => goTo({ ...line, at: i + 1 })} accessibilityRole="button" accessibilityLabel={`To ${m.said}`} accessibilityState={{ selected: at }} hitSlop={3}
                    style={({ pressed }) => [s.move, at ? { backgroundColor: INK } : pressed && { backgroundColor: pal.raised }]}>
                    <Text style={[t.small, s.nums, { color: at ? colour.surface : i >= line.at ? colour.muted : colour.text }]}>{m.san}</Text>
                  </Pressable>
                </View>
              )
            })}
          </View>
        ) : <Text style={[t.small, t.muted]}>Move a piece to start your line. The engine's moves above play with a tap.</Text>}
      </View>
      <Text style={[t.tiny, t.muted]}>
        Engine: Stockfish 18 (<Text onPress={open(ENGINE_LICENCE)} accessibilityRole="link" style={t.link}>GPL-3.0</Text>) · <Text onPress={open(ENGINE_SOURCE)} accessibilityRole="link" style={t.link}>source</Text> · on your phone
      </Text>
      {worker && !engine.failed ? <EngineView engine={worker} /> : null}
    </>,
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 },
  board: { aspectRatio: 1, borderRadius: 8, overflow: 'hidden' },
  // The picked piece's square, ringed in ink inside it.
  picked: { ...StyleSheet.absoluteFill, borderWidth: 3, borderColor: INK },
  promoteWrap: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', zIndex: 2 },
  promote: { flexDirection: 'row', gap: 4, padding: 6, borderRadius: 12, backgroundColor: 'rgba(20, 20, 20, 0.9)' },
  promoteTo: { width: 44, height: 44, borderRadius: 8, alignItems: 'center', justifyContent: 'center', backgroundColor: '#ECE6D6' }, // a light square's
  cancel: { width: 32, height: 44, alignItems: 'center', justifyContent: 'center' },
  ghost: { position: 'absolute', alignItems: 'center', justifyContent: 'center', zIndex: 3 },
  steps: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 6 },
  row: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 },
  step: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' }, // .btn.icon.chess-step
  toggle: { minWidth: 58, height: 36, paddingHorizontal: 14, borderRadius: 999, borderWidth: 2, borderColor: INK, alignItems: 'center', justifyContent: 'center' },
  toggleWords: { fontFamily: font[800], fontSize: 13 },
  // A part under the board (.chess-study-part): a line over it.
  part: { gap: 10, paddingTop: 14, borderTopWidth: 1, borderTopColor: colour.line },
  partHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  // The engine's lines (.chess-pv): the score, white or black for the side it favours, then the moves.
  pv: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  pvScore: { minWidth: 54, paddingVertical: 2, paddingHorizontal: 6, borderRadius: 6, overflow: 'hidden', textAlign: 'center' },
  pvWhite: { backgroundColor: BAR.white, color: BAR.black, borderWidth: 1, borderColor: colour.line },
  pvBlack: { backgroundColor: BAR.black, color: BAR.white },
  pvMoves: { flex: 1, flexDirection: 'row', flexWrap: 'wrap', columnGap: 6, rowGap: 2 },
  // The fan's moves (.chess-line), the one the board is at in ink, those after it (kept after a step back) soft.
  line: { flexDirection: 'row', flexWrap: 'wrap', columnGap: 8, rowGap: 4 },
  pair: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  move: { marginVertical: -3, marginHorizontal: -2, paddingVertical: 3, paddingHorizontal: 5, borderRadius: 6 },
  nums: { fontVariant: ['tabular-nums'] },
})

// What a sport's live board is drawn from and in (the web's BoardProps, web/src/sports/view.ts), and the bits
// every board shares. A board is drawn on white (the scoreboard on Today and a player's page, .board), on paper
// (a game none of the fan's players are in, .live-other) or on its sport's colour (a match page's band,
// .match-board): the palette says which.

import { useState, type ReactNode } from 'react'
import { StyleSheet, Text, useWindowDimensions, View, type StyleProp, type TextStyle } from 'react-native'
import type { LiveStatus, Stat } from '../api'
import type { Tone } from '../catalog'
import { colour, font, num, t } from '../theme'
import { alpha } from '../ui'

export interface Palette {
  ground: string // what the board sits on: the scoreboard's white, a card's paper, the sport's colour
  text: string
  muted: string
  raised: string
  line: string
  clock: string // the clock, in red
  note: string // a live note ("batting", the game's state), in red
  ours: string // the followed player's team in a lobby: its words
  wash?: string // and its row's ground, where the sport's colour can't be the words (on white: a faint wash of it)
  score: number // the big score's size
  paper: boolean // on a paper card (a game none of the fan's players are in): boards draw smaller there
  title?: number // the sides' names' size, where it isn't a title's 15px
  clockSize?: number // the clock's, where it isn't 22px
}

/** On the white scoreboard (.board): ink type, the clock in the page's red, the followed team's row washed in its
 * sport's colour; 52px score, 40px compact. */
export function onBoard(tone: Tone, compact?: boolean): Palette {
  return { ground: colour.surface, text: colour.text, muted: colour.muted, raised: colour.raised, line: colour.line, clock: colour.liveText, note: colour.liveText, ours: colour.text, wash: alpha(tone.sport, 0.18), score: compact ? 40 : 52, paper: false }
}

/** On a paper card (.live-other): ink type, the clock in the page's red, the followed team's row washed in its sport's
 * colour (as on the white scoreboard); a 40px score. */
export function onPaper(tone: Tone): Palette {
  return { ground: colour.surface, text: colour.text, muted: colour.muted, raised: colour.raised, line: colour.line, clock: colour.liveText, note: colour.liveText, ours: colour.text, wash: alpha(tone.sport, 0.18), score: 40, paper: true }
}

/** On a match page's band (.match-band .match-board): everything in the ink that reads on the sport's colour,
 * the clock too; a 64px score, 18px names and a 24px clock. */
export function onSport(tone: Tone): Palette {
  return { ground: tone.sport, text: tone.on, muted: tone.onSoft, raised: alpha(tone.on, 0.14), line: alpha(tone.on, 0.24), clock: tone.on, note: tone.on, ours: tone.on, score: 64, paper: false, title: 18, clockSize: 24 }
}

export interface BoardProps<S = unknown> {
  state: S
  clock?: string | null
  compact?: boolean
  teams?: string[] // the followed player's teams: their row in a lobby
  live: LiveStatus
  // Each of the fan's players' numbers in the game, on a card with several (Today's, of a game two of theirs play
  // each other in) or a match page; without it, the live block's own. A board drawn from their side (chess) has
  // none to take when they're on both.
  lines?: Stat[][]
  // The match page's board: one the fan may explore, where its sport has a way to (a chess board's Play from
  // here, an analysis board on the phone). Elsewhere a board is the game's alone.
  explore?: boolean
  pal: Palette
}

export type Board = (props: BoardProps<never>) => ReactNode

/** What a sport's part of a match page is drawn from (the web's MatchProps): its sections (the server's
 * Sport.match_page, by name: a timeline, a scorecard, maps, moves), the game's live block, if it has one yet, the
 * names of the fan's players in it, to mark them, and its sport's colours (the web's, from the page's data-sport). */
export interface MatchProps<P = unknown> {
  page: P
  live: LiveStatus | null
  yours?: string[]
  tone: Tone
}

export type MatchPart = (props: MatchProps<never>) => ReactNode

/** Ink on a paper card, for a line score drawn under a match page's band (a scorecard, a lobby's standings). */
export const INK = { text: colour.text, muted: colour.muted }

/** A lobby's columns (#, team, matches won, finishes, points), on its board and its match page: room for a
 * "23rd" and a "99.5" at the phone's usual text size. */
export const LOBBY_COLUMNS = [46, null, 44, 38, 42]

const CELL_PAD = 6 // a cell's padding a side, as .linescore's
const MAX_GROWTH = 1.15 // a table's fixed columns grow with the phone's text size up to this; past it the numbers shrink to fit
const ROOM = 100 // the wide column's least room (a batter and how they were out): the others give way to keep it,
const MIN_SHRINK = 0.85 // down to this share of their own widths (a narrow phone at a large text size)

// ---------------------------------------------------------------- shared pieces

export function Title({ children, pal, style, lines }: { children: ReactNode; pal: Palette; style?: StyleProp<TextStyle>; lines?: number }) {
  return <Text style={[t.title, { color: pal.text }, pal.title ? { fontSize: pal.title, lineHeight: pal.title * 1.3 } : null, style]} numberOfLines={lines}>{children}</Text>
}

export function Muted({ children, pal, size = 12, style }: { children: ReactNode; pal: Palette; size?: number; style?: StyleProp<TextStyle> }) {
  return <Text style={[t.tiny, { color: pal.muted, fontSize: size }, style]}>{children}</Text>
}

export function Score({ children, pal, size }: { children: ReactNode; pal: Palette; size?: number }) {
  return <Text style={[num(size ?? pal.score), { color: pal.text }]} numberOfLines={1} adjustsFontSizeToFit>{children}</Text>
}

export function Clock({ children, pal, size }: { children: ReactNode; pal: Palette; size?: number }) {
  return <Text style={[num(size ?? pal.clockSize ?? 22), { color: pal.clock }]}>{children}</Text>
}

export function Note({ children, pal }: { children: ReactNode; pal: Palette }) {
  return <Text style={{ fontFamily: font[700], fontSize: 13, lineHeight: 18, color: pal.note }}>{children}</Text>
}

/** Two sides and the score between them, as the web's 1fr auto 1fr grid. */
export function Sides({ left, right, middle }: { left: ReactNode; right: ReactNode; middle: ReactNode }) {
  return (
    <View style={s.sides}>
      <View style={[s.side, { alignItems: 'flex-start' }]}>{left}</View>
      <View style={{ alignItems: 'center' }}>{middle}</View>
      <View style={[s.side, { alignItems: 'flex-end' }]}>{right}</View>
    </View>
  )
}

/** A row of a line score (.linescore): its cells, the `wide` one (the first) taking the room, it and those before
 * it to the left, the rest centred, or to the right (`right`: a scorecard's, a lobby's). `total` is the column in
 * bold: the last, or the one at that index. `widths`, each column's own with its padding (none for the wide
 * one), the same for every row of a table, so its columns line up as a table's do whatever's in them. They grow
 * a little with the phone's text size, and give way, a little, to keep the wide column's ROOM in a narrow row;
 * a number that doesn't fit is drawn smaller rather than cut. */
export function LineRow({ cells, pal, head, total, first = 1, bold, wide = 0, right, widths }: { cells: ReactNode[]; pal: Pick<Palette, 'text' | 'muted'>; head?: boolean; total?: boolean | number; first?: number; bold?: boolean[]; wide?: number; right?: boolean; widths?: (number | null)[] }) {
  const { fontScale } = useWindowDimensions()
  const [row, setRow] = useState(0) // the row's width, once laid out
  const strong = total === true ? cells.length - 1 : typeof total === 'number' ? total : -1
  // Each fixed column is its padding and its text's room, k times its own: as the text size has it, but no more
  // than leaves the wide column ROOM in the row (once it's laid out), nor less than MIN_SHRINK.
  const own = (widths ?? []).filter((w, i): w is number => Boolean(w) && i !== wide)
  const pads = own.length * 2 * CELL_PAD
  const text = own.reduce((sum, w) => sum + w, 0) - pads
  const growth = Math.min(MAX_GROWTH, Math.max(1, fontScale))
  const k = row && text ? Math.max(MIN_SHRINK, Math.min(growth, (row - ROOM - pads) / text)) : growth
  const width = (w: number) => 2 * CELL_PAD + (w - 2 * CELL_PAD) * k
  return (
    <View style={s.lineRow} onLayout={widths ? (e) => setRow(e.nativeEvent.layout.width) : undefined}>
      {cells.map((c, i) => (
        <View key={i} style={i === wide ? { flex: first, minWidth: 0, paddingHorizontal: CELL_PAD } : [widths?.[i] ? { width: width(widths[i]) } : { minWidth: 30 }, { paddingHorizontal: CELL_PAD, alignItems: i < wide ? 'flex-start' : right ? 'flex-end' : 'center' }]}>
          {typeof c === 'string' || typeof c === 'number' ? (
            <Text style={[head ? s.lineHead : s.lineCell, { color: i === strong || bold?.[i] ? pal.text : pal.muted }, i === strong && { fontFamily: font[800] }]} numberOfLines={i === wide ? 2 : 1} adjustsFontSizeToFit={i !== wide}>{c}</Text>
          ) : c}
        </View>
      ))}
    </View>
  )
}

const KIND: Record<string, { bg: string; fg: string }> = {
  goal: { bg: colour.live, fg: '#FFFFFF' },
  yellow: { bg: '#F2C94C', fg: '#1A1400' },
  red: { bg: '#EF5A5A', fg: '#1A0000' },
}

/** A moment's kind on a chip (.moment-kind): a goal in red, a card in its colour, a goal ruled out struck
 * through, anything else quiet. */
export function MomentKind({ kind, label }: { kind: string; label: string }) {
  const k = kind.toLowerCase()
  const look = KIND[k] ?? { bg: colour.raised, fg: colour.muted }
  return (
    <View style={{ paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6, backgroundColor: look.bg, flexShrink: 0 }}>
      <Text style={[s.kind, { color: look.fg }, k === 'disallowed' && { textDecorationLine: 'line-through' }]}>{label}</Text>
    </View>
  )
}

export const s = StyleSheet.create({
  sides: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  side: { flex: 1, minWidth: 0 },
  lineRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 4 },
  lineHead: { fontFamily: font[800], fontSize: 11, letterSpacing: 0.66, textTransform: 'uppercase' },
  lineCell: { fontFamily: font[500], fontSize: 13 },
  kind: { fontFamily: font[800], fontSize: 11, letterSpacing: 0.66, textTransform: 'uppercase' },
})

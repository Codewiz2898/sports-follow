// What a sport's live board is drawn from and in (the web's BoardProps, web/src/sports/view.ts), and the bits
// every board shares. A board is drawn on ink (the scoreboard on Today and a player's page, .board) or on
// paper (a game none of the fan's players are in, .live-other): the palette says which.

import type { ReactNode } from 'react'
import { StyleSheet, Text, View, type StyleProp, type TextStyle } from 'react-native'
import type { LiveStatus } from '../api'
import type { Tone } from '../catalog'
import { colour, font, num, t } from '../theme'

export interface Palette {
  text: string
  muted: string
  raised: string
  line: string
  clock: string // the clock, in red
  note: string // a live note ("batting", the game's state), in red
  ours: string // the followed player's team in a lobby: the sport's colour as text on ink
  score: number // the big score's size
  paper: boolean // on a paper card (a game none of the fan's players are in): boards draw smaller there
}

/** On the ink scoreboard (.board): white type; 52px score, 40px compact. */
export function onInk(tone: Tone, compact?: boolean): Palette {
  return { text: '#FFFFFF', muted: colour.inkMuted, raised: colour.inkRaised, line: colour.inkLine, clock: '#FF6B5F', note: '#FF8A80', ours: tone.ink, score: compact ? 40 : 52, paper: false }
}

/** On a paper card (.live-other): ink type, the clock in the page's red; a 40px score. */
export function onPaper(tone: Tone): Palette {
  return { text: colour.text, muted: colour.muted, raised: colour.raised, line: colour.line, clock: colour.liveText, note: colour.liveText, ours: tone.ink, score: 40, paper: true }
}

export interface BoardProps<S = unknown> {
  state: S
  clock?: string | null
  compact?: boolean
  teams?: string[] // the followed player's teams: their row in a lobby
  live: LiveStatus
  pal: Palette
}

export type Board = (props: BoardProps<never>) => ReactNode

// ---------------------------------------------------------------- shared pieces

export function Title({ children, pal, style, lines }: { children: ReactNode; pal: Palette; style?: StyleProp<TextStyle>; lines?: number }) {
  return <Text style={[t.title, { color: pal.text }, style]} numberOfLines={lines}>{children}</Text>
}

export function Muted({ children, pal, size = 12, style }: { children: ReactNode; pal: Palette; size?: number; style?: StyleProp<TextStyle> }) {
  return <Text style={[t.tiny, { color: pal.muted, fontSize: size }, style]}>{children}</Text>
}

export function Score({ children, pal, size }: { children: ReactNode; pal: Palette; size?: number }) {
  return <Text style={[num(size ?? pal.score), { color: pal.text }]} numberOfLines={1} adjustsFontSizeToFit>{children}</Text>
}

export function Clock({ children, pal, size = 22 }: { children: ReactNode; pal: Palette; size?: number }) {
  return <Text style={[num(size), { color: pal.clock }]}>{children}</Text>
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

/** A row of a line score (.linescore): its cells, the first wide and to the left. */
export function LineRow({ cells, pal, head, total, first = 1, bold }: { cells: ReactNode[]; pal: Palette; head?: boolean; total?: boolean; first?: number; bold?: boolean[] }) {
  return (
    <View style={s.lineRow}>
      {cells.map((c, i) => (
        <View key={i} style={i === 0 ? { flex: first, paddingHorizontal: 6 } : { minWidth: 30, paddingHorizontal: 6, alignItems: 'center' }}>
          {typeof c === 'string' || typeof c === 'number' ? (
            <Text style={[head ? s.lineHead : s.lineCell, { color: (total && i === cells.length - 1) || bold?.[i] ? pal.text : pal.muted }, (total && i === cells.length - 1) && { fontFamily: font[800] }]} numberOfLines={i === 0 ? 2 : 1}>{c}</Text>
          ) : c}
        </View>
      ))}
    </View>
  )
}

export const s = StyleSheet.create({
  sides: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  side: { flex: 1, minWidth: 0 },
  lineRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 4 },
  lineHead: { fontFamily: font[800], fontSize: 11, letterSpacing: 0.66, textTransform: 'uppercase' },
  lineCell: { fontFamily: font[500], fontSize: 13 },
})

// A section whose cards run sideways in one row, out to the page's edge (the web's Rail, components/Rail.tsx):
// each card snaps to the page padding with the next one peeking in, and with `all` (what the end card says)
// an "All N" card at the end lays them all out at once; with `max` too, the row holds that many and the
// "All N" card the rest. A `head` takes the place of the title row (Today's players strip).

import { Children, cloneElement, isValidElement, useState, type ReactElement, type ReactNode } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { colour, font, GUTTER, RADIUS_TILE } from './theme'
import { Dsp, Eyebrow, SectionHead } from './ui'

const ALL_FROM = 3 // a rail this long ends in an "All N" card that lays every card out at once
const GAP = 10

/** A card in a rail takes `fill` to drop its fixed width and fill its column when the rail lays out all. */
export interface RailCard { fill?: boolean }

export function Rail({ title, note, all, max, head, columns = 1, gap = GAP, children }: { title: string; note?: string; all?: string; max?: number; head?: ReactNode; columns?: number; gap?: number; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [starts, setStarts] = useState<number[]>([])
  const [width, setWidth] = useState(0)
  const cards = Children.toArray(children)
  const count = cards.length
  const shown = open || !all || max == null ? cards : cards.slice(0, max)

  const allCard = all && count >= ALL_FROM ? (
    <Pressable key="all" onPress={() => setOpen(!open)} accessibilityRole="button" accessibilityState={{ expanded: open }} style={[s.all, open && { width: '100%', minHeight: 96 }]}>
      <Dsp size={34}>{open ? 'Fewer' : `All ${count}`}</Dsp>
      <Text style={s.allNote}>{open ? 'Back to one row' : all}</Text>
    </Pressable>
  ) : null

  const fill = (card: ReactNode) => (isValidElement(card) ? cloneElement(card as ReactElement<RailCard>, { fill: true }) : card)
  const cell = width ? (width - gap * (columns - 1)) / columns : 0

  return (
    <View style={s.section}>
      {head ?? <SectionHead title={title} right={note ? <Eyebrow>{note}</Eyebrow> : undefined} />}
      {open ? (
        <View style={[s.grid, { gap }]} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
          {cell > 0 && shown.map((card, i) => <View key={i} style={{ width: cell }}>{fill(card)}</View>)}
          {allCard}
        </View>
      ) : (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={s.row}
          contentContainerStyle={[s.rowContent, { gap }]}
          decelerationRate="fast"
          snapToOffsets={starts.length ? starts : undefined}
        >
          {[...shown, allCard].filter(Boolean).map((card, i) => (
            <View key={i} onLayout={(e) => { const x = e.nativeEvent.layout.x - GUTTER; setStarts((p) => (p[i] === x ? p : Object.assign([...p], { [i]: x }))) }}>
              {card}
            </View>
          ))}
        </ScrollView>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  section: { gap: 10 },
  row: { marginHorizontal: -GUTTER },
  rowContent: { paddingHorizontal: GUTTER, alignItems: 'stretch' },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  all: { justifyContent: 'flex-end', gap: 4, width: 150, padding: 16, borderRadius: RADIUS_TILE, borderWidth: 2, borderColor: colour.text, flexGrow: 1 },
  allNote: { fontFamily: font[700], fontSize: 13, lineHeight: 18, color: colour.muted },
})

// Watch live on a match page, the web's components/Watch.tsx: where to watch a game while it's on or about to be
// (sports_follow/watch.py; web/src/match.ts's watchable says when). Its first stream, the organiser's main one where
// there is one, big in the sport's colour, and its others as rows. Every one opens outside the app, in its
// platform's app or the browser, as a feed video does: the app has no player of its own for them.

import { Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { safeHref } from '../../web/src/format'
import { streamNote } from '../../web/src/match'
import type { MatchStream } from './api'
import type { Tone } from './catalog'
import { OutIcon } from './icons'
import { colour, dsp, RADIUS_TILE, t } from './theme'
import { ListCard, SectionHead } from './ui'

const open = (url: string) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

export function Watch({ streams, tone }: { streams: MatchStream[]; tone: Tone }) {
  const [first, ...more] = streams
  if (!first) return null
  const note = streamNote(first)
  return (
    <View style={s.section}>
      <SectionHead title="Watch live" />
      <Pressable onPress={open(first.url)} accessibilityRole="link" accessibilityLabel={`Watch on ${first.platform}${note ? `, ${note}` : ''}`}
        style={({ pressed }) => [s.big, { backgroundColor: tone.sport }, pressed && { opacity: 0.85 }]}>
        <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
          <Text style={[dsp(34), { color: tone.on }]}>Watch on {first.platform}</Text>
          {note ? <Text style={[t.tag, { color: tone.onSoft }]}>{note}</Text> : null}
        </View>
        <OutIcon size={26} color={tone.on} />
      </Pressable>
      {more.length > 0 ? (
        <ListCard>
          {more.map((x) => (
            <Pressable key={x.url} onPress={open(x.url)} accessibilityRole="link" accessibilityLabel={`Watch on ${x.platform}${streamNote(x) ? `, ${streamNote(x)}` : ''}`} style={s.row}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Text style={t.title}>{x.platform}</Text>
                {streamNote(x) ? <Text style={[t.small, t.muted]}>{streamNote(x)}</Text> : null}
              </View>
              <OutIcon size={20} color={colour.muted} />
            </Pressable>
          ))}
        </ListCard>
      ) : null}
    </View>
  )
}

const s = StyleSheet.create({
  section: { gap: 10 },
  big: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 96, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_TILE },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
})

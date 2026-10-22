// A video's source's icon, the web's SourceMark (web/src/components/SourceMark.tsx): YouTube's, for one of YouTube's,
// as the server sends it (feed.ts sourceMark), linked to the video where it plays, in its own clear space (padding:
// at least the size of its triangle all round, YouTube's brand rules, and its tap area), so a fan can tell where
// it's from. Nothing for a post, a story, or a video whose source sends no icon.

import { Linking, Pressable, StyleSheet } from 'react-native'
import Svg, { Path } from 'react-native-svg'
import { sourceMark } from '../../web/src/feed'
import type { FeedItem } from './api'

export function SourceMark({ item, height }: { item: FeedItem; height?: number }) {
  const shown = sourceMark(item, height)
  if (!shown) return null
  const { mark, href, width, label } = shown
  return (
    <Pressable onPress={() => { Linking.openURL(href).catch(() => {}) }} accessibilityRole="link" accessibilityLabel={label}
      style={({ pressed }) => [s.mark, pressed && { opacity: 0.6 }]}>
      <Svg width={width} height={shown.height} viewBox={`0 0 ${mark.width} ${mark.height}`}>
        {mark.paths.map((p, i) => <Path key={i} fill={p.fill} d={p.d} />)}
      </Svg>
    </Pressable>
  )
}

// Above and below, its clear space overlaps the row's own, so the row doesn't grow (.source-mark).
const s = StyleSheet.create({ mark: { padding: 9, marginVertical: -9, flexShrink: 0, alignItems: 'center', justifyContent: 'center' } })

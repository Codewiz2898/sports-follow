// The Share button (the web's components/Share.tsx): the phone's share sheet with a page's link and its title
// (web/src/share.ts says which). The link is on the site the app talks to (API_URL), never the app's own
// scheme, so it previews in WhatsApp and the rest and opens for anyone, with the app or without it.

import { Platform, Pressable, Share, StyleSheet, Text, type StyleProp, type ViewStyle } from 'react-native'
import { shareLink, type Shared } from '../../web/src/share'
import { API_URL } from './api'
import { ShareIcon } from './icons'
import { colour, font } from './theme'
import { Btn, type BtnColours } from './ui'

/** The share sheet: on Android the link as the text and the title as its subject, as Chrome's Web Share sends
 * them; on iOS the link as a URL. Android always reports it shared, and a failure has nothing to show. */
export function share({ path, title }: Shared) {
  const url = shareLink(API_URL, path)
  Share.share(Platform.OS === 'ios' ? { url } : { message: url, title }, { subject: title }).catch(() => {})
}

/**
 * A Share button: round, as Btn's `icon` kind, which `colours` repaint (the player band); with `poster`, a
 * poster's 32px ink circle (.poster-share), placed by `style`; with `text`, the icon and those words in ink,
 * bare (a Feed card's .post-act).
 */
export function ShareButton({ colours, poster, text, style, ...page }: Shared & { colours?: BtnColours; poster?: boolean; text?: string; style?: StyleProp<ViewStyle> }) {
  if (text) {
    return (
      <Pressable accessibilityRole="button" accessibilityLabel={page.label} hitSlop={6} onPress={() => share(page)} style={({ pressed }) => [s.text, pressed && { opacity: 0.7 }, style]}>
        <ShareIcon size={18} color={colour.text} />
        <Text style={s.words}>{text}</Text>
      </Pressable>
    )
  }
  if (poster) {
    return (
      <Pressable accessibilityRole="button" accessibilityLabel={page.label} hitSlop={8} onPress={() => share(page)} style={({ pressed }) => [s.poster, pressed && { opacity: 0.8 }, style]}>
        <ShareIcon size={18} color={colour.bg} />
      </Pressable>
    )
  }
  return <Btn kind="icon" accessibilityLabel={page.label} colours={colours} icon={<ShareIcon size={18} color={colours?.fg ?? colour.bg} />} onPress={() => share(page)} style={style} />
}

const s = StyleSheet.create({
  text: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 36 },
  words: { fontFamily: font[800], fontSize: 13, color: colour.text },
  poster: { alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: 16, backgroundColor: colour.text },
})

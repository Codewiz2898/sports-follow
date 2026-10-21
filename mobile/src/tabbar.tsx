// The phone's tab bar, the web's (.tabbar): a floating ink pill with four places, over every page. The
// Following and Live pages are part of Today (Today opens them): Today stays lit there. The Feed has a dot
// while something came into it since the fan last opened it (feedSeen.ts); a small phone shows the words alone.

import { router, usePathname } from 'expo-router'
import { useEffect, useState } from 'react'
import { Keyboard, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useActivity } from './activity'
import { useFeedDot } from './feedSeen'
import { useFollowing } from './following'
import { BellIcon, FeedIcon, HomeIcon, SearchIcon } from './icons'
import { colour, font } from './theme'

const PLACES = [
  { path: '/', label: 'Today', Icon: HomeIcon, lit: (p: string) => p === '/' || p === '/following' || p === '/live' },
  { path: '/feed', label: 'Feed', Icon: FeedIcon, lit: (p: string) => p === '/feed' },
  { path: '/search', label: 'Search', Icon: SearchIcon, lit: (p: string) => p === '/search' },
  { path: '/notifications', label: 'Alerts', Icon: BellIcon, lit: (p: string) => p === '/notifications' },
] as const

export function TabBar() {
  const insets = useSafeAreaInsets()
  const path = usePathname()
  const { unread } = useActivity()
  const { cards, loaded } = useFollowing()
  const dot = useFeedDot(loaded ? cards.map((c) => c.player_id).sort((a, b) => a - b).join(',') : null)
  const icons = useWindowDimensions().width >= 380
  const [typing, setTyping] = useState(false)
  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', () => setTyping(true))
    const hide = Keyboard.addListener('keyboardDidHide', () => setTyping(false))
    return () => { show.remove(); hide.remove() }
  }, [])
  if (typing || path === '/welcome') return null
  return (
    <View style={[s.bar, { bottom: 16 + insets.bottom }]} accessibilityRole="tablist">
      {PLACES.map(({ path: to, label, Icon, lit }) => {
        const on = lit(path)
        return (
          <Pressable key={to} accessibilityRole="tab" accessibilityState={{ selected: on }} accessibilityLabel={to === '/feed' && dot ? `${label}, something new` : label} onPress={() => router.navigate(to as never)} style={[s.place, on && s.on]}>
            {icons ? <Icon size={20} color={on ? colour.text : colour.bg} /> : null}
            <Text style={[s.label, on && s.labelOn]} numberOfLines={1}>{label}</Text>
            {to === '/feed' && dot ? <View style={[s.dot, on && { backgroundColor: colour.text }]} accessibilityLabel="new in your Feed" /> : null}
            {to === '/notifications' && unread ? (
              <View style={s.count} accessibilityLabel={`${unread} new`}><Text style={s.countText}>{unread > 9 ? '9+' : unread}</Text></View>
            ) : null}
          </Pressable>
        )
      })}
    </View>
  )
}

const s = StyleSheet.create({
  bar: {
    position: 'absolute', left: 16, right: 16, height: 64, paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center',
    borderRadius: 999, backgroundColor: colour.text, maxWidth: 440, alignSelf: 'center',
    shadowColor: colour.text, shadowOpacity: 0.28, shadowRadius: 16, shadowOffset: { width: 0, height: 12 }, elevation: 12,
  },
  place: { flex: 1, height: 48, borderRadius: 999, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 },
  on: { backgroundColor: colour.bg },
  label: { fontFamily: font[700], fontSize: 14, color: colour.bg },
  labelOn: { fontFamily: font[800], color: colour.text },
  // Something new in the Feed: a dot by its name, in the bar's paper colour (red is only for live).
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colour.bg, marginTop: -12, marginLeft: -3 },
  count: { minWidth: 20, height: 20, paddingHorizontal: 6, marginLeft: -2, borderRadius: 999, backgroundColor: colour.live, alignItems: 'center', justifyContent: 'center' },
  countText: { fontFamily: font[800], fontSize: 11, color: '#FFFFFF' },
})

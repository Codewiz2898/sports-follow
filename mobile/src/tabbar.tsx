// The phone's tab bar, the web's (.tabbar): a floating ink pill with four places, over every page. The
// Following and Live pages are part of Today (Today opens them): Today stays lit there. The Feed has a dot
// while something came into it since the fan last opened it (feedSeen.ts); a small phone shows the words alone.
// A place is one screen: tapping it goes back to that screen when it's already open under this one (what's over
// it closes) rather than opening another on top, so the stack and Back don't grow with every tap.

import { router, useNavigationContainerRef, usePathname } from 'expo-router'
import { useCallback, useEffect, useState } from 'react'
import { Keyboard, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useActivity } from './activity'
import { useFeedDot } from './feedSeen'
import { useFollowing } from './following'
import { BellIcon, FeedIcon, HomeIcon, SearchIcon } from './icons'
import { colour, font } from './theme'

const PLACES = [
  { path: '/', screen: 'index', label: 'Today', Icon: HomeIcon, lit: (p: string) => p === '/' || p === '/following' || p === '/live' },
  { path: '/feed', screen: 'feed', label: 'Feed', Icon: FeedIcon, lit: (p: string) => p === '/feed' },
  { path: '/search', screen: 'search', label: 'Search', Icon: SearchIcon, lit: (p: string) => p === '/search' },
  { path: '/notifications', screen: 'notifications', label: 'Alerts', Icon: BellIcon, lit: (p: string) => p === '/notifications' },
] as const

type Place = (typeof PLACES)[number]['path']
type Nav = { type?: string; index?: number; routes: readonly { name: string; state?: Nav }[] }

/** Whether a stack on the way to the screen in front has `screen` under it. */
function under(state: Nav | undefined, screen: string): boolean {
  for (let at = state; at; ) {
    const front = at.index ?? at.routes.length - 1
    if (at.type === 'stack' && at.routes.slice(0, front).some((r) => r.name === screen)) return true
    at = at.routes[front]?.state
  }
  return false
}

/** Goes to one of the bar's places: back to its screen when one is open under this one (what's over it closes),
 * else a new one on top. expo-router's navigate only reuses the screen in front, and its dismissTo puts the
 * place in the front screen's stead when it isn't open, so the stack decides which. */
export function useGoToPlace() {
  const nav = useNavigationContainerRef()
  return useCallback((to: Place) => {
    const { screen } = PLACES.find((p) => p.path === to)!
    if (nav.isReady() && under(nav.getRootState(), screen)) router.dismissTo(to)
    else router.navigate(to)
  }, [nav])
}

export function TabBar() {
  const insets = useSafeAreaInsets()
  const path = usePathname()
  const { unread } = useActivity()
  const { cards, loaded } = useFollowing()
  const dot = useFeedDot(loaded ? cards.map((c) => c.player_id).sort((a, b) => a - b).join(',') : null)
  const icons = useWindowDimensions().width >= 380
  const go = useGoToPlace()
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
          <Pressable key={to} accessibilityRole="tab" accessibilityState={{ selected: on }} accessibilityLabel={to === '/feed' && dot ? `${label}, something new` : label} onPress={() => go(to)} style={[s.place, on && s.on]}>
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

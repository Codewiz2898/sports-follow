// The phone's tab bar, the web's (.tabbar): a floating ink pill with three places, over every page. The
// Following and Live pages are part of Today (Today opens them): Today stays lit there.

import { router, usePathname } from 'expo-router'
import { useEffect, useState } from 'react'
import { Keyboard, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useActivity } from './activity'
import { BellIcon, HomeIcon, SearchIcon } from './icons'
import { colour, font } from './theme'

const PLACES = [
  { path: '/', label: 'Today', Icon: HomeIcon, lit: (p: string) => p === '/' || p === '/following' || p === '/live' },
  { path: '/search', label: 'Search', Icon: SearchIcon, lit: (p: string) => p === '/search' },
  { path: '/notifications', label: 'Alerts', Icon: BellIcon, lit: (p: string) => p === '/notifications' },
] as const

export function TabBar() {
  const insets = useSafeAreaInsets()
  const path = usePathname()
  const { unread } = useActivity()
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
          <Pressable key={to} accessibilityRole="tab" accessibilityState={{ selected: on }} accessibilityLabel={label} onPress={() => router.navigate(to as never)} style={[s.place, on && s.on]}>
            <Icon size={20} color={on ? colour.text : colour.bg} />
            <Text style={[s.label, on && s.labelOn]}>{label}</Text>
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
  place: { flex: 1, height: 48, borderRadius: 999, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  on: { backgroundColor: colour.bg },
  label: { fontFamily: font[700], fontSize: 14, color: colour.bg },
  labelOn: { fontFamily: font[800], color: colour.text },
  count: { minWidth: 20, height: 20, paddingHorizontal: 6, marginLeft: -2, borderRadius: 999, backgroundColor: colour.live, alignItems: 'center', justifyContent: 'center' },
  countText: { fontFamily: font[800], fontSize: 11, color: '#FFFFFF' },
})

// The tabs: Today, Search, Following. Alerts joins them with the rest of the screens (BHA-231).

import { Tabs } from 'expo-router'
import { SymbolView, type SymbolViewProps } from 'expo-symbols'
import type { ColorValue } from 'react-native'
import { colour, font } from '../../theme'

function icon(ios: string, android: string) {
  return function TabIcon({ color }: { color: ColorValue }) {
    return <SymbolView name={{ ios, android } as SymbolViewProps['name']} tintColor={color} size={24} />
  }
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: colour.paper },
        tabBarActiveTintColor: colour.ink,
        tabBarInactiveTintColor: colour.muted,
        tabBarStyle: { backgroundColor: colour.surface, borderTopColor: colour.line },
        tabBarLabelStyle: { fontFamily: font.heavy, fontSize: 11 },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Today', tabBarIcon: icon('sportscourt.fill', 'stadium') }} />
      <Tabs.Screen name="search" options={{ title: 'Search', tabBarIcon: icon('magnifyingglass', 'search') }} />
      <Tabs.Screen name="following" options={{ title: 'Following', tabBarIcon: icon('star.fill', 'star') }} />
    </Tabs>
  )
}

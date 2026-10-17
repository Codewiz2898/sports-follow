// The root: who's signed in, the sports, the fan's players and their activity for every page; one stack of
// pages, as the web app's routes (main.tsx), with the floating tab bar over all of them.

import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { View } from 'react-native'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { AccountProvider } from '../account'
import { ActivityProvider } from '../activity'
import { SportsProvider } from '../catalog'
import { FollowingProvider, useFollowing } from '../following'
import { useNotificationTaps, usePushRegistration } from '../push'
import { TabBar } from '../tabbar'
import { colour } from '../theme'

/** Notifications on this phone (push.ts): registered as this fan once their players are known (it asks the
 * first time there are some), and a tapped one's player opened. After the stack, so a tap that started the
 * app opens over a mounted navigator. */
function Push() {
  const { cards, loaded } = useFollowing()
  usePushRegistration(loaded ? cards.length > 0 : null)
  useNotificationTaps()
  return null
}

/** The fan's own state (which starts over when this phone signs in or out: account.tsx's epoch). */
function Fan() {
  return (
    <FollowingProvider>
      <ActivityProvider>
        <View style={{ flex: 1, backgroundColor: colour.bg }}>
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colour.bg }, animation: 'slide_from_right' }} />
          <TabBar />
          <Push />
        </View>
      </ActivityProvider>
    </FollowingProvider>
  )
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <AccountProvider>
        <SportsProvider>
          <Fan />
        </SportsProvider>
      </AccountProvider>
    </SafeAreaProvider>
  )
}

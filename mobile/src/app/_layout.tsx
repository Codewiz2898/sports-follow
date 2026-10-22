// The root: who's signed in, the sports, the fan's players and their activity for every page; one stack of
// pages, as the web app's routes (main.tsx), with the floating tab bar over all of them. A page that fails to draw
// says so (ui.tsx's PageError), as the web's do, and the tab bar still works: the app doesn't close, again at every
// launch, over one block in a shape it doesn't expect (BUG-156).

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
import { PageError } from '../ui'

/** What's around the pages failing (a provider, the tab bar) shows PageError instead of closing the app. */
export { PageError as ErrorBoundary }

/** Notifications on this phone (push.ts): registered as this fan once their players, teams and games are known
 * (it asks the first time there are some: a fan of a team alone has its moments and live cards too), and a tapped
 * one's page opened. After the stack, so a tap that started the app opens over a mounted navigator. */
function Push() {
  const { cards, loaded, matches, matchesLoaded, teams } = useFollowing()
  usePushRegistration(loaded && matchesLoaded ? cards.length > 0 || matches.length > 0 || teams.length > 0 : null)
  useNotificationTaps()
  return null
}

/** The fan's own state (which starts over when this phone signs in or out: account.tsx's epoch). */
function Fan() {
  return (
    <FollowingProvider>
      <ActivityProvider>
        <View style={{ flex: 1, backgroundColor: colour.bg }}>
          {/* Each page in its own boundary, so one that fails leaves the rest of the stack, and the tab bar, working. */}
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colour.bg }, animation: 'slide_from_right' }} unstable_screenErrorBoundary={PageError} />
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

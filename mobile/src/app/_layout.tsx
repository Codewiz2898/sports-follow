// The root: the sports and the fan's players for every screen, a stack with the tabs at the bottom of it and
// a player's page over them, and notifications (push.ts).

import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { FollowingProvider, useFollowing } from '../following'
import { useNotificationTaps, usePushRegistration } from '../push'
import { SportsProvider } from '../sports'
import { colour, font } from '../theme'

/** Registers for notifications once the fan's players are known (it asks when there are some), and opens a
 * tapped one's player. After the stack, so a tap that started the app opens over a mounted navigator. */
function Push() {
  const { cards, loaded } = useFollowing()
  usePushRegistration(loaded ? cards.length > 0 : null)
  useNotificationTaps()
  return null
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <SportsProvider>
        <FollowingProvider>
          <Stack
            screenOptions={{
              contentStyle: { backgroundColor: colour.paper },
              headerStyle: { backgroundColor: colour.paper },
              headerShadowVisible: false,
              headerTintColor: colour.ink,
              headerTitleStyle: { fontFamily: font.heavy, fontSize: 16 },
            }}
          >
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="player/[id]" options={{ title: '' }} />
          </Stack>
          <Push />
        </FollowingProvider>
      </SportsProvider>
    </SafeAreaProvider>
  )
}

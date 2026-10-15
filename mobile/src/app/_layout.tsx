// The root: the sports and the fan's players for every screen, and a stack with the tabs at the bottom of it
// and a player's page over them.

import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { FollowingProvider } from '../following'
import { SportsProvider } from '../sports'
import { colour, font } from '../theme'

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
        </FollowingProvider>
      </SportsProvider>
    </SafeAreaProvider>
  )
}

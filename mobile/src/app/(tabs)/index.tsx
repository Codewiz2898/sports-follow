// Today: your players' live games first, then what else is on now (GET /api/live), then a tile for each of
// your players in their sport's colour.

import Constants from 'expo-constants'
import { router, useFocusEffect } from 'expo-router'
import { useCallback, useState } from 'react'
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { api, type LiveSport } from '../../api'
import { Brand, Button, Empty, LiveCard, PlayerTile, SectionTitle } from '../../components'
import { useFollowing } from '../../following'
import { colour, GUTTER, type } from '../../theme'

const EVERY_MS = 30_000
const OTHER_GAMES = 6

export default function TodayScreen() {
  const following = useFollowing()
  const [live, setLive] = useState<LiveSport[]>([])
  const [refreshing, setRefreshing] = useState(false)

  const loadLive = useCallback(() => api.live().then((r) => setLive(r.sports)).catch(() => {}), [])

  // What's on now, while Today is in front.
  useFocusEffect(useCallback(() => {
    loadLive()
    const timer = setInterval(loadLive, EVERY_MS)
    return () => clearInterval(timer)
  }, [loadLive]))

  const refresh = useCallback(async () => {
    setRefreshing(true)
    await Promise.all([following.reload(), loadLive()])
    setRefreshing(false)
  }, [following, loadLive])

  const mineLive = following.cards.filter((c) => c.live?.is_live)
  const mineEvents = new Set(mineLive.map((c) => c.live.event_id).filter((id): id is number => id != null))
  const others = live
    .flatMap((s) => s.games.map((g) => ({ ...g, sport: s.key })))
    .filter((g) => !g.event_ids?.some((id) => mineEvents.has(id)))
    .slice(0, OTHER_GAMES)

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
        <Brand name={Constants.expoConfig?.name ?? ''} />

        {following.error && !following.cards.length ? <Text style={[type.small, styles.error]}>{following.error}</Text> : null}

        {mineLive.length ? (
          <>
            <SectionTitle>Your players, live</SectionTitle>
            {mineLive.map((c) => (
              <LiveCard key={c.player_id} title={c.player.name} live={c.live} sport={c.player.sport} onPress={() => router.push(`/player/${c.player_id}`)} />
            ))}
          </>
        ) : null}

        {others.length ? (
          <>
            <SectionTitle>On now</SectionTitle>
            {others.map((g) => {
              const first = g.players.find((p) => p.player_id)
              return <LiveCard key={g.key} title={g.title} live={g.live} sport={g.sport} onPress={first?.player_id ? () => router.push(`/player/${first.player_id}`) : undefined} />
            })}
          </>
        ) : null}

        <SectionTitle>Your players</SectionTitle>
        {following.loaded && !following.cards.length ? (
          <Empty title="Follow your first player">
            <Text style={type.body}>Live scores, their own numbers while they play, next games and results, all on one page.</Text>
            <Button label="Find a player" onPress={() => router.push('/search')} />
          </Empty>
        ) : (
          <View style={styles.grid}>
            {following.cards.map((c) => (
              <View key={c.player_id} style={styles.cell}>
                <PlayerTile card={c} />
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colour.paper },
  content: { padding: GUTTER, paddingBottom: 32 },
  error: { color: colour.liveText, marginTop: 12 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5 },
  cell: { width: '50%', padding: 5 },
})

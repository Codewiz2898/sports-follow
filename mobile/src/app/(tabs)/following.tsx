// Following: every player the fan follows, with a way to stop.

import { router } from 'expo-router'
import { useState } from 'react'
import { Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import type { Card } from '../../api'
import { Avatar, Button, Empty } from '../../components'
import { useFollowing } from '../../following'
import { useSports } from '../../sports'
import { colour, font, GUTTER, type } from '../../theme'

export default function FollowingScreen() {
  const following = useFollowing()
  const [refreshing, setRefreshing] = useState(false)

  const refresh = async () => {
    setRefreshing(true)
    await following.reload()
    setRefreshing(false)
  }

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <FlatList
        data={following.cards}
        keyExtractor={(c) => String(c.player_id)}
        renderItem={({ item }) => <Row card={item} />}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}
        ListHeaderComponent={<Text style={[type.display, styles.title]}>Following</Text>}
        ListEmptyComponent={following.loaded ? (
          <Empty title="No one yet">
            <Button label="Find a player" onPress={() => router.push('/search')} />
          </Empty>
        ) : null}
      />
    </SafeAreaView>
  )
}

function Row({ card }: { card: Card }) {
  const following = useFollowing()
  const sports = useSports()

  const stop = () => Alert.alert(`Unfollow ${card.player.name}?`, 'Their page and alerts leave your app.', [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Unfollow', style: 'destructive', onPress: () => following.unfollow(card.player_id).catch((e: Error) => Alert.alert(e.message)) },
  ])

  return (
    <Pressable onPress={() => router.push(`/player/${card.player_id}`)} onLongPress={stop} android_ripple={{ color: 'rgba(0,0,0,0.06)' }} style={styles.row}>
      <Avatar card={card} size={48} />
      <View style={styles.rowText}>
        <Text style={type.bold} numberOfLines={1}>{card.player.name}</Text>
        <Text style={type.small} numberOfLines={1}>{[sports.name(card.player.sport), card.player.teams?.[0]].filter(Boolean).join(' · ')}</Text>
      </View>
      <Pressable onPress={stop} hitSlop={8} accessibilityRole="button" accessibilityLabel={`Unfollow ${card.player.name}`}>
        <Text style={styles.unfollow}>Unfollow</Text>
      </Pressable>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colour.paper },
  title: { fontSize: 34, lineHeight: 36, color: colour.ink, marginBottom: 12 },
  list: { padding: GUTTER, gap: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: colour.surface, borderRadius: 18, padding: 12, overflow: 'hidden' },
  rowText: { flex: 1, gap: 2 },
  unfollow: { fontFamily: font.heavy, fontSize: 13, color: colour.muted },
})

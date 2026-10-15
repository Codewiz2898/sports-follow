// Search: names as you type (GET /api/search, from the player registry), and Follow beside each. Following
// sends what the web app sends (web/src/follow.ts), so the server binds the exact athlete.

import { router } from 'expo-router'
import { useEffect, useRef, useState } from 'react'
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { followTarget } from '../../../../web/src/follow'
import { api, type SearchResponse, type SearchResult } from '../../api'
import { Button } from '../../components'
import { useFollowing } from '../../following'
import { useSports, useTone } from '../../sports'
import { colour, font, GUTTER, type } from '../../theme'

const WAIT_MS = 250

export default function SearchScreen() {
  const [query, setQuery] = useState('')
  const [answer, setAnswer] = useState<{ asked: string; found: SearchResponse } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef<AbortController | null>(null)
  const q = query.trim()

  // As the web's search (web/src/components/Search.tsx): a new name drops the last answer's error and
  // its "no one" line, and an answer for an older name never lands.
  useEffect(() => {
    latest.current?.abort()
    setError(null)
    if (q.length < 2) {
      setAnswer(null)
      return
    }
    const ask = new AbortController()
    latest.current = ask
    const timer = setTimeout(() => {
      api.search(q, ask.signal)
        .then((found) => setAnswer({ asked: q, found }))
        .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
    }, WAIT_MS)
    return () => clearTimeout(timer)
  }, [q])

  // The last answer's people stay while the next name is asked; what it says about the name only while
  // that is still the name.
  const found = answer?.found
  const current = answer?.asked === q ? found : null
  const results = found ? (found.results.length ? found.results : found.suggestions) : []

  return (
    <SafeAreaView style={styles.screen} edges={['top']}>
      <View style={styles.top}>
        <Text style={[type.display, styles.title]}>Search</Text>
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Any athlete, any sport"
          placeholderTextColor={colour.muted}
          autoCorrect={false}
          autoCapitalize="words"
          returnKeyType="search"
          style={styles.input}
          accessibilityLabel="Search players"
        />
        {error ? <Text style={[type.small, styles.error]}>{error}</Text> : null}
        {current && !current.results.length && current.suggestions.length ? <Text style={type.small}>Nothing for “{current.query}”. Did you mean:</Text> : null}
        {current && !results.length ? <Text style={type.small}>No one called “{current.query}” yet.</Text> : null}
      </View>
      <FlatList
        data={results}
        keyExtractor={(r) => r.key}
        renderItem={({ item }) => <ResultRow result={item} />}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.list}
      />
    </SafeAreaView>
  )
}

function ResultRow({ result: r }: { result: SearchResult }) {
  const following = useFollowing()
  const sports = useSports()
  const tone = useTone(r.sport)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const followed = r.following || following.isFollowing(r.player_id)
  // A sport only the research agent covers is an AI research, not a follow: the web app's Research button
  // comes with the rest of search (BHA-231).
  const followable = r.live_scores || Boolean(r.player_id)

  const follow = async () => {
    setBusy(true)
    setError(null)
    try {
      const card = await following.follow(followTarget(r))
      router.push(`/player/${card.player_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Pressable
      onPress={r.player_id ? () => router.push(`/player/${r.player_id}`) : undefined}
      android_ripple={{ color: 'rgba(0,0,0,0.06)' }}
      style={styles.row}
    >
      <View style={[styles.chip, { backgroundColor: tone.block }]} />
      <View style={styles.rowText}>
        <Text style={type.bold} numberOfLines={1}>{r.name}{r.born ? <Text style={type.small}>  b. {r.born}</Text> : null}</Text>
        <Text style={type.small} numberOfLines={1}>{[sports.name(r.sport), r.detail].filter(Boolean).join(' · ')}</Text>
        {error ? <Text style={[type.small, styles.error]}>{error}</Text> : null}
      </View>
      {followed ? <Text style={styles.followed}>Following</Text> : followable ? <Button label="Follow" onPress={follow} busy={busy} /> : null}
    </Pressable>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colour.paper },
  top: { paddingHorizontal: GUTTER, paddingTop: GUTTER, gap: 10 },
  title: { fontSize: 34, lineHeight: 36, color: colour.ink },
  input: { backgroundColor: colour.surface, borderRadius: 999, borderWidth: 2, borderColor: colour.ink, paddingHorizontal: 18, paddingVertical: 12, fontFamily: font.bold, fontSize: 16, color: colour.ink },
  error: { color: colour.liveText },
  list: { padding: GUTTER, gap: 8 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: colour.surface, borderRadius: 18, padding: 12, overflow: 'hidden' },
  chip: { width: 10, alignSelf: 'stretch', borderRadius: 5 },
  rowText: { flex: 1, gap: 2 },
  followed: { fontFamily: font.heavy, fontSize: 13, color: colour.muted },
})

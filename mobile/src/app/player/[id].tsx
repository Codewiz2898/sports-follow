// A player's page (GET /api/players/{id}/card): who they are in their sport's colour, the live game, what's
// next, recent results, season numbers and news. Results history, form and each sport's scoreboard come with
// the rest of the screens (BHA-231).

import { Stack, useFocusEffect, useLocalSearchParams } from 'expo-router'
import { useCallback, useState } from 'react'
import { Linking, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { dateTime, dayLabel, playedOn, safeHref, shortWhen } from '../../../../web/src/format'
import { api, type Card } from '../../api'
import { Avatar, Button, Empty, LiveCard, SectionTitle } from '../../components'
import { useFollowing } from '../../following'
import { useSports, useTone } from '../../sports'
import { colour, font, GUTTER, type } from '../../theme'

const EVERY_MS = 30_000
const BUILDING_MS = 3_000

export default function PlayerScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const playerId = Number(id)
  const sports = useSports()
  const [card, setCard] = useState<Card | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(() => api.card(playerId).then((c) => { setCard(c); setError(null); return c }).catch((e: Error) => { setError(e.message); return null }), [playerId])

  // Every 30 s while the page is in front; every 3 s while the page is still being built.
  useFocusEffect(useCallback(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let gone = false
    const tick = async () => {
      const c = await load()
      if (!gone) timer = setTimeout(tick, c?.status === 'building' ? BUILDING_MS : EVERY_MS)
    }
    tick()
    return () => { gone = true; clearTimeout(timer) }
  }, [load]))

  const refresh = async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }

  if (!card) {
    return (
      <View style={styles.screen}>
        <Stack.Screen options={{ title: '' }} />
        {error ? <Empty title="Can't open this page"><Text style={type.body}>{error}</Text></Empty> : null}
      </View>
    )
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} />}>
      <Stack.Screen options={{ title: card.player.name }} />
      <Hero card={card} />
      {card.status === 'building' ? <Text style={[type.small, styles.note]}>Building this page: {card.pending?.length ? card.pending.join(', ') : 'profile and news'} still to come.</Text> : null}
      {card.status === 'failed' ? <Text style={[type.small, styles.error]}>{card.error || "This page couldn't be built."}</Text> : null}

      {card.live?.is_live ? (
        <>
          <SectionTitle>Live</SectionTitle>
          <LiveCard title={card.live.event ?? card.player.name} live={card.live} sport={card.player.sport} />
        </>
      ) : null}

      {card.upcoming?.length ? (
        <>
          <SectionTitle>Next</SectionTitle>
          <View style={styles.box}>
            {card.upcoming.slice(0, 3).map((u, i) => (
              <View key={`${u.event_id ?? u.title}-${i}`} style={[styles.line, i > 0 && styles.divided]}>
                <View style={styles.lineMain}>
                  <Text style={type.bold} numberOfLines={2}>{u.title}</Text>
                  <Text style={type.small} numberOfLines={1}>{[u.competition, u.venue].filter(Boolean).join(' · ')}</Text>
                </View>
                <Text style={styles.when}>{i === 0 ? dateTime(u.start_utc) : shortWhen(u.start_utc)}</Text>
              </View>
            ))}
          </View>
        </>
      ) : null}

      {card.recent_results?.length ? (
        <>
          <SectionTitle>Results</SectionTitle>
          <View style={styles.box}>
            {card.recent_results.slice(0, 5).map((r, i) => (
              <View key={`${r.event_id ?? r.title}-${i}`} style={[styles.line, i > 0 && styles.divided]}>
                <View style={styles.lineMain}>
                  <Text style={type.bold} numberOfLines={2}>{r.result}</Text>
                  <Text style={type.small} numberOfLines={1}>{r.title}</Text>
                  {r.player_contribution ? <Text style={type.small} numberOfLines={2}>{r.player_contribution}</Text> : null}
                </View>
                <Text style={styles.when}>{dayLabel(playedOn(r))}</Text>
              </View>
            ))}
          </View>
        </>
      ) : null}

      {card.season_stats?.length ? (
        <>
          <SectionTitle>{card.season_stats_note || sports.info(card.player.sport).stats_title}</SectionTitle>
          <View style={styles.stats}>
            {card.season_stats.slice(0, 8).map((s) => (
              <View key={s.label} style={styles.stat}>
                <Text style={[type.number, styles.statValue]} numberOfLines={1}>{s.value}</Text>
                <Text style={type.eyebrow} numberOfLines={2}>{s.label}</Text>
              </View>
            ))}
          </View>
        </>
      ) : null}

      {card.news?.length ? (
        <>
          <SectionTitle>News</SectionTitle>
          <View style={styles.box}>
            {card.news.slice(0, 5).map((n, i) => {
              const href = safeHref(n.url)
              return (
                <Pressable key={`${n.url}-${i}`} disabled={!href} onPress={() => href && Linking.openURL(href)} style={[styles.news, i > 0 && styles.divided]}>
                  <Text style={type.bold}>{n.headline}</Text>
                  <Text style={type.small}>{n.source}</Text>
                </Pressable>
              )
            })}
          </View>
        </>
      ) : null}
    </ScrollView>
  )
}

function Hero({ card }: { card: Card }) {
  const tone = useTone(card.player.sport)
  const sports = useSports()
  const following = useFollowing()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const followed = following.isFollowing(card.player_id)

  const toggle = async () => {
    setBusy(true)
    setError(null)
    try {
      if (followed) await following.unfollow(card.player_id)
      else await following.follow({ player_id: card.player_id })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <View style={[styles.hero, { backgroundColor: tone.block }]}>
      <View style={styles.heroTop}>
        <Avatar card={card} size={72} />
        <View style={styles.heroText}>
          <Text style={[styles.heroSport, { color: tone.soft }]}>{[sports.name(card.player.sport), card.player.role].filter(Boolean).join(' · ')}</Text>
          <Text style={[type.display, styles.heroName, { color: tone.on }]}>{card.player.name}</Text>
          {card.player.teams?.length ? <Text style={[styles.heroTeams, { color: tone.soft }]} numberOfLines={2}>{card.player.teams.join(' · ')}</Text> : null}
        </View>
      </View>
      {card.player.summary ? <Text style={[styles.summary, { color: tone.on }]} numberOfLines={4}>{card.player.summary}</Text> : null}
      <Button label={followed ? 'Following' : 'Follow'} kind={followed ? 'plain' : 'primary'} onPress={toggle} busy={busy} style={styles.heroButton} />
      {error ? <Text style={[styles.summary, { color: tone.on }]}>{error}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colour.paper },
  content: { padding: GUTTER, paddingBottom: 40 },
  note: { marginTop: 10 },
  error: { marginTop: 10, color: colour.liveText },
  hero: { borderRadius: 22, padding: 16, gap: 12 },
  heroTop: { flexDirection: 'row', gap: 14, alignItems: 'center' },
  heroText: { flex: 1, gap: 2 },
  heroSport: { fontFamily: font.heavy, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase' },
  heroName: { fontSize: 34, lineHeight: 36 },
  heroTeams: { fontFamily: font.bold, fontSize: 13 },
  summary: { fontFamily: font.body, fontSize: 14, lineHeight: 20 },
  heroButton: { alignSelf: 'flex-start' },
  box: { backgroundColor: colour.surface, borderRadius: 22, paddingHorizontal: 14 },
  line: { flexDirection: 'row', gap: 12, paddingVertical: 12, alignItems: 'flex-start' },
  lineMain: { flex: 1, gap: 2 },
  divided: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colour.line },
  when: { fontFamily: font.heavy, fontSize: 12, color: colour.muted, textAlign: 'right', maxWidth: 120 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  stat: { backgroundColor: colour.surface, borderRadius: 18, padding: 12, width: '48%', gap: 4 },
  statValue: { fontSize: 30, lineHeight: 32, color: colour.ink },
  news: { paddingVertical: 12, gap: 2 },
})

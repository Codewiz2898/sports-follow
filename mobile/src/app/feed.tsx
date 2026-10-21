// The Feed tab, the web's pages/FeedPage.tsx (the InstaFeed board): every followed player's videos, posts and
// news as one feed, by day, players taking turns (GET /api/me/feed/items, sports_follow/feed.py `page`). Their
// circles across the top filter it to one of them (/feed?player=<id>), ringed in their sport's colour while they
// have something the fan hasn't seen. The next page loads as the list nears its end. Coming to it takes away the
// tab's dot (feedSeen.ts). It's read again from the top when the dot was showing or the fan's players changed.
// The web's address is /me/feed; here it's /feed, since the app's /me/ would claim the site's /me/leagues too
// (app.config.ts).

import { router, useFocusEffect, useIsFocused, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { byDay, ringed } from '../../../web/src/feed'
import type { FeedCircle, FeedEntry } from '../api'
import { useNow } from '../clock'
import { feedHasNew, markOpened, markSeen, seenAt, useFeedSeen } from '../feedSeen'
import { useFollowing } from '../following'
import { SearchIcon } from '../icons'
import { useMyFeed } from '../myFeed'
import { PageList } from '../page'
import { Circles, FeedPost, ItemSheet } from '../post'
import { colour, dsp, t } from '../theme'
import { Btn, Card, ErrorBox, Eyebrow, Spinner } from '../ui'

/** A row of the list: a day's head, or one of its items. */
type Row = { day: string } | { entry: FeedEntry }

export default function FeedScreen() {
  const now = useNow()
  const { cards, loaded, failed: noFollowList } = useFollowing()
  const params = useLocalSearchParams<{ player?: string }>()
  const wanted = Number(params.player)
  const player = Number.isInteger(wanted) && wanted > 0 ? wanted : null
  const focused = useIsFocused()
  const [again, setAgain] = useState(0)
  // Read again from the top on a pull, when the tab's dot lit, and when the fan's players changed; the last two
  // only once the screen is in front again, since it stays in the app's stack while the fan is elsewhere (where
  // the web's page would be loaded anew). Not waiting on a follow list that can't be read.
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const [read, setRead] = useState<string | null>(null)
  useEffect(() => { if (focused && (loaded || noFollowList)) setRead(`${who}:${again}`) }, [focused, loaded, noFollowList, who, again])
  const { page, error, failed, loading, more, hide } = useMyFeed(player, undefined, read)
  const [opened, setOpened] = useState<FeedEntry | null>(null)
  const [circles, setCircles] = useState<FeedCircle[]>([])
  const [refreshing, setRefreshing] = useState(false)
  useFeedSeen()

  // In front: the tab's dot goes, and what came in since it lit is read from the top (the first time, it's read anyway).
  const first = useRef(true)
  useFocusEffect(useCallback(() => {
    if (!first.current && feedHasNew()) setAgain((n) => n + 1)
    first.current = false
    markOpened()
  }, []))
  // And again once a page says what's newest (it may be newer than the dot knew), while the fan's looking at it.
  useEffect(() => { if (focused && page?.newest) markOpened() }, [focused, page?.newest])
  // The circles stay put while one player's is shown (each page lists them all).
  useEffect(() => { if (page) setCircles(page.players) }, [page])
  useEffect(() => { if (!loading) setRefreshing(false) }, [loading])

  const pick = (id: number | null) => {
    if (id != null) markSeen(id)
    router.setParams({ player: id == null ? undefined : String(id) })
  }
  const picked = circles.find((c) => c.id === player)
  const rows: Row[] = page ? byDay(page.items).flatMap((d) => [{ day: d.day }, ...d.entries.map((entry) => ({ entry }))]) : []

  const head = (
    <>
      <View style={s.head}>
        <Text style={dsp(54)} accessibilityRole="header">Feed</Text>
        <Eyebrow style={{ paddingBottom: 8 }}>From your players</Eyebrow>
      </View>
      {circles.length > 0 ? <Circles players={circles} picked={player} lit={(c) => ringed(c, seenAt(c.id), now)} onPick={pick} /> : null}
      {error && !page ? <ErrorBox>{error}</ErrorBox> : null}
      {!page && !error ? <View style={s.row}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View> : null}
      {page && page.items.length === 0 ? (
        loaded && cards.length === 0 ? (
          <Card pad style={{ gap: 10 }}>
            <Text style={[t.title, { fontSize: 16 }]}>Follow a player to fill your Feed</Text>
            <Text style={[t.small, t.muted]}>What fans, creators and the players themselves share about them shows here, newest first.</Text>
            <Btn kind="primary" label="Find a player" icon={<SearchIcon size={18} color={colour.bg} />} onPress={() => router.push('/search')} style={{ alignSelf: 'flex-start' }} />
          </Card>
        ) : (
          <Card pad style={{ gap: 6 }}>
            <Text style={[t.title, { fontSize: 16 }]}>Nothing here yet</Text>
            <Text style={[t.small, t.muted]}>We only show what fans are actually watching and sharing about {picked ? picked.name : 'your players'}. Nothing sponsored, nothing in passing.</Text>
          </Card>
        )
      ) : null}
    </>
  )

  const foot = (
    <View style={{ gap: 16 }}>
      {page?.cursor ? <View style={[s.row, { justifyContent: 'center', minHeight: 48 }]}>{loading ? <Spinner /> : null}</View> : null}
      {error && page ? (
        <View style={{ gap: 8 }}>
          <ErrorBox>{error}</ErrorBox>
          <Btn kind="link" label="Try again" onPress={failed === 'first' ? () => setAgain((n) => n + 1) : more} style={{ alignSelf: 'flex-start' }} />
        </View>
      ) : null}
      {page && !page.cursor && page.items.length > 0 ? <Eyebrow style={{ textAlign: 'center' }}>You're up to date · newer ones arrive through the day</Eyebrow> : null}
    </View>
  )

  return (
    <>
      <PageList<Row>
        data={rows}
        keyExtractor={(r) => ('day' in r ? `day:${r.day}` : r.entry.item.id)}
        renderItem={({ item: r }) => ('day' in r ? <DayHead day={r.day} /> : (
          <FeedPost who={r.entry.player} item={r.entry.item} moment={r.entry.moment} now={now} onMore={() => setOpened(r.entry)} />
        ))}
        head={head}
        foot={foot}
        // A screen and a half from the end, as the web's 800px margin: the next page is usually in before it's reached.
        onEndReached={error ? undefined : more}
        onEndReachedThreshold={1.5}
        initialNumToRender={4}
        windowSize={7}
        onRefresh={read == null ? undefined : () => { setRefreshing(true); setAgain((n) => n + 1) }}
        refreshing={refreshing}
      />
      <ItemSheet item={opened?.item ?? null} name={opened?.player.name ?? ''} onClose={() => setOpened(null)} onHide={(_, verdict) => (opened ? hide(opened, verdict) : Promise.resolve())} />
    </>
  )
}

/** A day's head (.feed-day-head): its name and a line to the edge. */
function DayHead({ day }: { day: string }) {
  return (
    <View style={s.day} accessibilityRole="header">
      <Eyebrow color={colour.text}>{day}</Eyebrow>
      <View style={s.dayLine} />
    </View>
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  day: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 6, marginHorizontal: 4 },
  dayLine: { flex: 1, height: 2, backgroundColor: colour.line },
})

// The Feed tab, the web's pages/FeedPage.tsx (BHA-349): For you, the fan's players' videos, posts and news with
// videos of players they don't follow mixed in (3 of every 5 theirs), and Following, theirs alone (/feed?tab=following;
// GET /api/me/feed/items, sports_follow/feed.py `page`). The server says how to draw it (`layout`, which the admin
// panel changes live, asked again each time the tab comes to the front): edge to edge, or full screen
// (feedLayouts.tsx). Following's circles across the top filter it to one player (/feed?tab=following&player=<id>),
// ringed in their sport's colour while they have something the fan hasn't seen. The next page loads as the end comes
// near. Coming to it takes away the tab's dot (feedSeen.ts). It's read again from the top when the dot was showing or
// the fan's players changed. The web's address is /me/feed; here it's /feed, since the app's /me/ would claim the
// site's /me/leagues too (app.config.ts).

import { router, useFocusEffect, useIsFocused, useLocalSearchParams } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { useCallback, useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { ringed } from '../../../web/src/feed'
import type { FeedCircle, FeedEntry, FeedTabKey } from '../api'
import { useNow } from '../clock'
import { EdgePost, EdgeSheetExtras, FeedTabs, Reels, useFeedLayout, useFollows, useVideo } from '../feedLayouts'
import { feedHasNew, markOpened, markSeen, seenAt, useFeedSeen } from '../feedSeen'
import { useFollowing } from '../following'
import { SearchIcon } from '../icons'
import { useMyFeed } from '../myFeed'
import { PageList } from '../page'
import { Circles, ItemSheet } from '../post'
import { useTabBarOverInk } from '../tabbar'
import { colour, dsp, GUTTER, t } from '../theme'
import { Btn, Card, ErrorBox, Eyebrow, Spinner } from '../ui'

export default function FeedScreen() {
  const now = useNow()
  const insets = useSafeAreaInsets()
  const { cards, loaded, failed: noFollowList } = useFollowing()
  const params = useLocalSearchParams<{ player?: string; tab?: string }>()
  const wanted = Number(params.player)
  const player = Number.isInteger(wanted) && wanted > 0 ? wanted : null
  const tab: FeedTabKey = player != null || params.tab === 'following' ? 'following' : 'for_you'
  const focused = useIsFocused()
  const [again, setAgain] = useState(0)
  // Read again from the top on a pull, when the tab's dot lit, and when the fan's players changed; the last two
  // only once the screen is in front again, since it stays in the app's stack while the fan is elsewhere (where
  // the web's page would be loaded anew). A follow from a card here changes the list too, but the Feed stays as it
  // is under the fan's thumb, as the web's does. Not waiting on a follow list that can't be read.
  const who = useRef('')
  who.current = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const [read, setRead] = useState<string | null>(null)
  useEffect(() => { if (focused && (loaded || noFollowList)) setRead(`${who.current}:${again}`) }, [focused, loaded, noFollowList, again])
  const { page, error, failed, loading, more, hide } = useMyFeed(player, undefined, read, tab)
  const layout = useFeedLayout(page?.layout, focused)
  const { follows, error: followError } = useFollows()
  const video = useVideo()
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
  // Full screen and in front: the tab bar turns paper over the dark, and the status bar's icons light.
  const reels = focused && layout === 'full'
  useTabBarOverInk(reels)

  const showTab = (to: FeedTabKey) => router.setParams({ tab: to === 'following' ? 'following' : undefined, player: undefined })
  const pick = (id: number | null) => {
    if (id != null) markSeen(id)
    router.setParams({ tab: 'following', player: id == null ? undefined : String(id) })
  }
  const picked = circles.find((c) => c.id === player)
  const items = page?.items ?? []
  const retry = failed === 'first' ? () => setAgain((n) => n + 1) : more
  const sheet = (
    <ItemSheet item={opened?.item ?? null} name={opened?.player.name ?? ''} onClose={() => setOpened(null)} onHide={(_, verdict) => (opened ? hide(opened, verdict) : Promise.resolve())}>
      {opened && layout === 'edge' ? <EdgeSheetExtras entry={opened} onGo={() => setOpened(null)} /> : null}
    </ItemSheet>
  )

  // Nothing (yet): why, and the way on.
  const followsNobody = loaded && cards.length === 0
  const empty = page && items.length === 0 ? (
    tab === 'following' && followsNobody ? (
      <Card pad style={{ gap: 10, alignSelf: 'stretch' }}>
        <Text style={[t.title, { fontSize: 16 }]}>Follow a player to fill Following</Text>
        <Text style={[t.small, t.muted]}>What fans, creators and the players themselves share about them shows here, newest first. For you has the top players' meanwhile.</Text>
        <View style={s.row}>
          <Btn kind="primary" label="See For you" onPress={() => showTab('for_you')} />
          <Btn label="Find a player" icon={<SearchIcon size={18} color={colour.text} />} onPress={() => router.push('/search')} />
        </View>
      </Card>
    ) : (
      <Card pad style={{ gap: 6, alignSelf: 'stretch' }}>
        <Text style={[t.title, { fontSize: 16 }]}>Nothing here yet</Text>
        <Text style={[t.small, t.muted]}>We only show what's on the official channels and the top creators' about {picked ? picked.name : tab === 'following' ? 'your players' : 'the players fans follow'}. Nothing sponsored, nothing in passing.</Text>
      </Card>
    )
  ) : null
  // The end: Following offers For you once its players have run out.
  const done = (onInk: boolean) => (page && !page.cursor && items.length > 0 ? (
    tab === 'following' ? (
      <View style={s.done}>
        <Eyebrow color={onInk ? INK_SOFT : undefined} style={{ textAlign: 'center' }}>That's everything from {picked ? picked.name : 'your players'}</Eyebrow>
        {!picked ? <Btn label="See For you" colours={onInk ? ON_INK : undefined} onPress={() => showTab('for_you')} /> : null}
      </View>
    ) : <Eyebrow color={onInk ? INK_SOFT : undefined} style={[s.done, { textAlign: 'center' }]}>You're up to date · newer ones arrive through the day</Eyebrow>
  ) : null)

  // The phone's own note of the layout is read in a moment: nothing drawn till then, rather than the wrong one.
  if (layout == null) return <View style={{ flex: 1, backgroundColor: colour.bg }} />

  if (layout === 'full') {
    return (
      <>
        <Reels
          entries={items} now={now} tab={tab} read={read} onTab={showTab} follows={follows} onPlay={video.play} onMore={setOpened}
          onNear={error ? undefined : more}
          footer={error ? (
            <View style={s.done}>
              <Eyebrow color={INK_SOFT} style={{ textAlign: 'center' }}>{error}</Eyebrow>
              <Btn label="Try again" colours={ON_INK} onPress={retry} />
            </View>
          ) : !page || page.cursor ? <Spinner color="#FFFFFF" /> : items.length === 0 ? empty : done(true)}
        />
        {followError ? <View style={[s.reelsError, { top: insets.top + 64 }]}><ErrorBox>{followError}</ErrorBox></View> : null}
        {reels ? <StatusBar style="light" /> : null}
        {sheet}
        {video.player}
      </>
    )
  }

  const showCircles = tab === 'following' && circles.length > 0
  const head = (
    <View style={{ gap: 16, marginBottom: items.length > 0 && !showCircles ? -8 : 0 }}>
      <Text style={dsp(54)} accessibilityRole="header">Feed</Text>
      <FeedTabs tab={tab} onTab={showTab} />
      {showCircles ? <Circles players={circles} picked={player} lit={(c) => ringed(c, seenAt(c.id), now)} onPick={pick} /> : null}
      {error && !page ? <ErrorBox>{error}</ErrorBox> : null}
      {!page && !error ? <View style={s.row}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View> : null}
      {followError ? <ErrorBox>{followError}</ErrorBox> : null}
      {empty}
    </View>
  )

  const foot = (
    <View style={{ gap: 16, paddingTop: 8 }}>
      {page?.cursor ? <View style={[s.row, { justifyContent: 'center', minHeight: 48 }]}>{loading ? <Spinner /> : null}</View> : null}
      {error && page ? (
        <View style={{ gap: 8 }}>
          <ErrorBox>{error}</ErrorBox>
          <Btn kind="link" label="Try again" onPress={retry} style={{ alignSelf: 'flex-start' }} />
        </View>
      ) : null}
      {done(false)}
    </View>
  )

  return (
    <>
      <PageList<FeedEntry>
        key={read ?? ''} // read again from the top (a pull, the dot, other players): shown from the top
        data={items}
        keyExtractor={(x) => x.item.id}
        renderItem={({ item: x }) => <EdgePost entry={x} now={now} follows={follows} onPlay={video.play} onMore={() => setOpened(x)} />}
        head={head}
        foot={foot}
        gap={8}
        // A screen and a half from the end, as the web's 800px margin: the next page is usually in before it's reached.
        onEndReached={error ? undefined : more}
        onEndReachedThreshold={1.5}
        initialNumToRender={4}
        windowSize={7}
        onRefresh={read == null ? undefined : () => { setRefreshing(true); setAgain((n) => n + 1) }}
        refreshing={refreshing}
      />
      {sheet}
      {video.player}
    </>
  )
}

const INK_SOFT = 'rgba(255,255,255,0.75)'
const ON_INK = { fg: '#FFFFFF', border: '#FFFFFF' }

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 10 },
  done: { alignItems: 'center', gap: 10, paddingTop: 8, paddingBottom: 16 },
  reelsError: { position: 'absolute', left: GUTTER, right: GUTTER },
})

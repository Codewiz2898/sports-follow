// The fan's Feed in the two layouts the server picks between, the web's components/FeedLayouts.tsx (BHA-349; GET
// /api/me/feed/items' `layout` and /api/config's `feed_layout`, which the admin panel changes live): edge to edge
// (the S2Edge board), full-width posts down the page; and full screen (the S5Reels board), one item a screen, paging
// from one to the next. A video is YouTube's biggest thumbnail until it's tapped, and only then does it play, in the
// app's own player over the whole screen (watch.tsx's, as Watch live's: the site's /watch/youtube/<id>), where the
// web's plays in the picture's place. Items of players the fan doesn't follow (For you's) offer Follow, which works
// where it is. "…" (More) opens "Why this is here". Also Today's "From your players" (the web's components/Feed.tsx),
// in the edge layout's cards.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { Image } from 'expo-image'
import { router } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { AppState, FlatList, Linking, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Svg, { Circle } from 'react-native-svg'
import { watchPage } from '../../web/src/endpoints'
import { duration, sourceLine } from '../../web/src/feed'
import { safeHref, shortName } from '../../web/src/format'
import { shareFeedItem } from '../../web/src/share'
import { api, API_URL, type FeedEntry, type FeedItem, type FeedLayout, type FeedTabKey } from './api'
import { useTone } from './catalog'
import { useFollowing } from './following'
import { CheckIcon, PlayIcon, PlusIcon, ShareIcon } from './icons'
import { useMyFeed } from './myFeed'
import { Face, ItemSheet, Picture } from './post'
import { share, ShareButton } from './share'
import { useGoToPlace } from './tabbar'
import { colour, dsp, font, GUTTER, RADIUS_CARD, t } from './theme'
import { Btn, SectionHead } from './ui'
import { WholeScreen } from './watch'

const open = (url?: string | null) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}
const toPlayer = (id: number, feed = false) => router.push({ pathname: '/player/[id]', params: feed ? { id: String(id), tab: 'feed' } : { id: String(id) } })

function Dots({ color }: { color: string }) {
  return <Svg width={20} height={20} viewBox="0 0 24 24" fill={color}><Circle cx="5" cy="12" r="2" /><Circle cx="12" cy="12" r="2" /><Circle cx="19" cy="12" r="2" /></Svg>
}

// ---------------------------------------------------------------- what both layouts need

const LAYOUT_KEY = 'stann.feed-layout' // the last layout the server said, so the next start draws it before it says again

/** The layout the server says: asked (/api/config) each time the Feed comes to the front, so the admin panel's
 * switch reaches the app without an update, and taken from each first page (`said`) too. Until it answers, the
 * last one it said; edge when it never has, or says nothing. Null only for the moment the phone's own note is read. */
export function useFeedLayout(said: FeedLayout | undefined, focused: boolean): FeedLayout | null {
  const [layout, setLayout] = useState<FeedLayout | null>(null)
  const keep = useCallback((l: unknown) => {
    if (l !== 'edge' && l !== 'full') return
    setLayout(l)
    AsyncStorage.setItem(LAYOUT_KEY, l).catch(() => {})
  }, [])
  useEffect(() => {
    AsyncStorage.getItem(LAYOUT_KEY)
      .then((was) => setLayout((now) => now ?? (was === 'full' ? 'full' : 'edge')))
      .catch(() => setLayout((now) => now ?? 'edge'))
  }, [])
  useEffect(() => {
    if (!focused) return
    let stale = false
    api.config().then((c) => { if (!stale) keep(c.feed_layout) }).catch(() => {})
    return () => { stale = true }
  }, [focused, keep])
  useEffect(() => { keep(said) }, [said, keep])
  return layout
}

/** What a follow button needs: whether the fan follows the entry's player now, and the way to change that. */
export interface Follows { following: (entry: FeedEntry) => boolean; toggle: (entry: FeedEntry) => void; busy: (entry: FeedEntry) => boolean }

/** Following a card's player where it is: whether the fan follows them (their follow list once it's in, the page's
 * word before), and a tap that follows or stops following them. The web's FeedPage useFollows. */
export function useFollows(): { follows: Follows; error: string | null } {
  const { cards, loaded, follow, unfollow } = useFollowing()
  const ids = useMemo(() => new Set(cards.map((c) => c.player_id)), [cards])
  const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const toggle = useCallback((e: FeedEntry) => {
    const id = e.player.id
    const on = loaded ? ids.has(id) : e.following
    setBusy((b) => new Set(b).add(id))
    setError(null)
    ;(on ? unfollow(id) : follow({ player_id: id }).then(() => undefined))
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy((b) => { const left = new Set(b); left.delete(id); return left }))
  }, [ids, loaded, follow, unfollow])
  const follows = useMemo<Follows>(() => ({
    following: (e) => (loaded ? ids.has(e.player.id) : e.following),
    busy: (e) => busy.has(e.player.id),
    toggle,
  }), [ids, loaded, busy, toggle])
  return { follows, error }
}

/** A video played on tap: `play` it, and `player`, the app's player over the whole screen while one plays (put
 * it anywhere on the screen). A video without a YouTube id, or a player that won't load, opens where it was posted. */
export function useVideo(): { play: (item: FeedItem) => void; player: ReactNode } {
  const [playing, setPlaying] = useState<FeedItem | null>(null)
  const play = useCallback((item: FeedItem) => {
    if (item.video?.youtube_id) setPlaying(item)
    else open(item.url)?.()
  }, [])
  const id = playing?.video?.youtube_id
  const player = playing && id ? (
    <WholeScreen page={API_URL + watchPage({ player: 'youtube', id })} platform="YouTube" onOut={open(playing.url)} onClose={() => setPlaying(null)} />
  ) : null
  return { play, player }
}

/** For you and Following: which of the fan's Feed shows. Under the page's title on paper, or over the full screen's
 * top on ink (`onInk`). */
export function FeedTabs({ tab, onTab, onInk }: { tab: FeedTabKey; onTab: (tab: FeedTabKey) => void; onInk?: boolean }) {
  const one = (key: FeedTabKey, label: string) => {
    const on = tab === key
    return (
      <Pressable key={key} onPress={() => onTab(key)} accessibilityRole="tab" accessibilityState={{ selected: on }} hitSlop={onInk ? 10 : 0}
        style={[onInk ? f.inkTab : f.tab, on && { borderBottomColor: onInk ? '#FFFFFF' : colour.text }]}>
        <Text style={onInk ? [f.inkLabel, on && { color: '#FFFFFF' }] : [f.label, on && { color: colour.text }]}>{label}</Text>
      </Pressable>
    )
  }
  return (
    <View style={onInk ? f.inkTabs : f.tabs} accessibilityRole="tablist" accessibilityLabel="Whose">
      {one('for_you', 'For you')}
      {one('following', 'Following')}
    </View>
  )
}

// ---------------------------------------------------------------- edge to edge

/** One post edge to edge (.epost): who it's about (photo ringed in their sport's colour, name, Follow when the fan
 * doesn't, "…"), the full-width 16:9 picture with the video's length, the title, and "channel · views · when".
 * `boxed`: a rounded card inside the page's margins (Today's), else the screen's full width. */
export function EdgePost({ entry, now, follows, boxed, onPlay, onMore }: {
  entry: FeedEntry; now: number; follows?: Follows; boxed?: boolean; onPlay: (item: FeedItem) => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const tone = useTone(who.sport)
  const [noPicture, setNoPicture] = useState(false)
  useEffect(() => setNoPicture(false), [item.picture])
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : undefined
  const go = open(item.url)
  const length = duration(item.video?.duration)
  const mine = follows ? follows.following(entry) : true
  const gone = () => setNoPicture(true)

  let media: ReactNode = null
  if (item.kind === 'video' && id) {
    media = (
      <Pressable onPress={() => onPlay(item)} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={[e.pic, { backgroundColor: colour.text }]}>
        {picture ? <Picture src={picture} onGone={gone} /> : null}
        {length ? <Text style={e.dur}>{length}</Text> : null}
      </Pressable>
    )
  } else if (item.kind === 'news') {
    media = picture ? (
      <Pressable onPress={go} disabled={!go} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={[e.pic, { backgroundColor: tone.sport }]}>
        <Picture src={picture} onGone={gone} />
      </Pressable>
    ) : (
      // No image came with the story: the headline is the picture, on the sport's colour.
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" style={[e.block, { backgroundColor: tone.sport }]}>
        <Text style={[t.eyebrow, { color: tone.onSoft }]} numberOfLines={1}>{item.source}</Text>
        <Text style={[dsp(30), { lineHeight: 29, color: tone.on }]} numberOfLines={4}>{item.title}</Text>
      </Pressable>
    )
  } else if (item.kind === 'post') {
    media = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="link">
        <Text style={e.quote} numberOfLines={3}>“{item.title}”</Text>
      </Pressable>
    )
  }
  if (!media) return null

  const titled = item.kind === 'video' || (item.kind === 'news' && picture)
  const from = item.kind === 'post' ? `${item.source} on ${item.post?.platform ?? ''}` : sourceLine(item, now)
  return (
    <View style={[e.post, boxed ? e.boxed : e.wide]}>
      <View style={e.head}>
        <Pressable onPress={() => toPlayer(who.id)} accessibilityRole="link" style={e.who}>
          <View style={[e.ring, { borderColor: tone.sport }]}><Face name={who.name} sport={who.sport} photo={who.photo} size={34} /></View>
          <Text style={e.name} numberOfLines={1}>{who.name}</Text>
        </Pressable>
        {follows && (!entry.following || !mine) ? (
          <>
            <Text style={e.sep} accessibilityElementsHidden importantForAccessibility="no">·</Text>
            <Pressable onPress={() => follows.toggle(entry)} disabled={follows.busy(entry)} accessibilityRole="button" accessibilityState={{ selected: mine, busy: follows.busy(entry) }}
              accessibilityLabel={mine ? `Following ${who.name}` : `Follow ${who.name}`} hitSlop={6} style={[e.follow, follows.busy(entry) && { opacity: 0.6 }]}>
              <Text style={[e.followText, mine && { color: colour.muted }]}>{mine ? 'Following' : 'Follow'}</Text>
            </Pressable>
          </>
        ) : null}
        <Pressable onPress={onMore} accessibilityRole="button" accessibilityLabel="More about this item" hitSlop={4} style={({ pressed }) => [e.more, pressed && { backgroundColor: colour.bg }]}>
          <Dots color={colour.text} />
        </Pressable>
      </View>
      {media}
      <View style={e.cap}>
        {titled ? <Text style={e.title} numberOfLines={3}>{item.title}</Text> : null}
        <Text style={e.src}>{from}{entry.moment ? ` · after ${entry.moment}` : ''}</Text>
      </View>
    </View>
  )
}

/** What the "…" sheet adds in the edge-to-edge layout, where the card itself has no Share or way on: both. `onGo`
 * closes the sheet before "More from …" opens the player's Feed. */
export function EdgeSheetExtras({ entry, onGo }: { entry: FeedEntry; onGo: () => void }) {
  const { player: who, item } = entry
  return (
    <View style={e.extras}>
      <ShareButton {...shareFeedItem(who.id, item)} text="Share" />
      <Text onPress={() => { onGo(); toPlayer(who.id, true) }} accessibilityRole="link" style={e.moreFrom} numberOfLines={1}>More from {shortName(who.name)}</Text>
    </View>
  )
}

// ---------------------------------------------------------------- full screen

/** The full-screen Feed (.reels): one item a screen on ink, paging (Android's own paging scroll); For you and
 * Following over the top. The next page loads three screens from the end (onNear), and `footer` is a last screen of
 * its own (the spinner, the end, why it's empty). A new tab, or the Feed read again from the top (`read`), starts
 * from its top. */
export function Reels({ entries, now, tab, read, onTab, follows, onPlay, onMore, onNear, footer }: {
  entries: FeedEntry[]; now: number; tab: FeedTabKey; read: string | null; onTab: (tab: FeedTabKey) => void; follows: Follows
  onPlay: (item: FeedItem) => void; onMore: (entry: FeedEntry) => void; onNear?: () => void; footer?: ReactElement | null
}) {
  const insets = useSafeAreaInsets()
  const [height, setHeight] = useState(0)
  const list = useRef<FlatList<FeedEntry>>(null)
  useEffect(() => { list.current?.scrollToOffset({ offset: 0, animated: false }) }, [tab, read])
  return (
    // Sideways is only ever the video player over it (watch.tsx): a screen that height would move the fan to another
    // item, so the reels keep their upright one.
    <View style={r.reels} onLayout={(ev) => { const box = ev.nativeEvent.layout; if (box.height >= box.width) setHeight(Math.round(box.height)) }}>
      {height > 0 ? (
        <FlatList
          ref={list}
          data={entries}
          keyExtractor={(x) => x.item.id}
          renderItem={({ item: x }) => <Reel entry={x} now={now} height={height} follows={follows} onPlay={onPlay} onMore={() => onMore(x)} />}
          // One screen a fling, a flick enough: Android's own snapping scroll (pagingEnabled alone wants half a screen dragged).
          snapToInterval={height}
          snapToAlignment="start"
          decelerationRate="fast"
          disableIntervalMomentum
          showsVerticalScrollIndicator={false}
          overScrollMode="never"
          getItemLayout={(_, index) => ({ length: height, offset: height * index, index })}
          onEndReached={onNear}
          onEndReachedThreshold={3}
          initialNumToRender={2}
          maxToRenderPerBatch={3}
          windowSize={5}
          ListFooterComponent={footer ? <View style={[r.end, { height }]}>{footer}</View> : null}
        />
      ) : null}
      <View style={[r.tabs, { top: insets.top + 18 }]} pointerEvents="box-none">
        <FeedTabs tab={tab} onTab={onTab} onInk />
      </View>
    </View>
  )
}

/** One screen (.reel): the picture on a blurred, darkened copy of itself, a little above the middle; who and what
 * at the foot; Follow (for a player the fan doesn't follow), Share and More down the right. */
function Reel({ entry, now, height, follows, onPlay, onMore }: {
  entry: FeedEntry; now: number; height: number; follows: Follows; onPlay: (item: FeedItem) => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const tone = useTone(who.sport)
  const insets = useSafeAreaInsets()
  const { width } = useWindowDimensions()
  const [noPicture, setNoPicture] = useState(false)
  useEffect(() => setNoPicture(false), [item.picture])
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : undefined
  const go = open(item.url)
  const mine = follows.following(entry)
  const gone = () => setNoPicture(true)
  // The frame's middle a little above the screen's (top: 42%, translateY(-58%)); higher on a short phone, clear of the buttons.
  const short = height < 700
  const top = Math.max(insets.top + 56, height * (short ? 0.34 : 0.42) - (width * 9 / 16) * 0.58)

  let frame: ReactNode
  if (item.kind === 'video' && id) {
    frame = (
      <Pressable onPress={() => onPlay(item)} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={[r.frame, { top }]}>
        {picture ? <Picture src={picture} onGone={gone} /> : null}
        <View style={r.bigplay}><PlayIcon size={24} color={colour.text} /></View>
      </Pressable>
    )
  } else if (picture) {
    frame = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" accessibilityLabel={`Read: ${item.title}`} style={[r.frame, { top }]}>
        <Picture src={picture} onGone={gone} />
      </Pressable>
    )
  } else {
    // No picture (a story without one, a post): the words on the sport's colour.
    frame = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" style={[r.frame, r.words, { top, backgroundColor: tone.sport }]}>
        <Text style={[t.eyebrow, { color: tone.onSoft }]} numberOfLines={1}>{item.source}</Text>
        <Text style={[dsp(28), { lineHeight: 27, color: tone.on }]} numberOfLines={4}>{item.kind === 'post' ? `“${item.title}”` : item.title}</Text>
      </Pressable>
    )
  }

  const act = (label: string, icon: ReactNode, onPress: () => void, extra?: { on?: boolean; busy?: boolean; a11y?: string }) => (
    <Pressable onPress={onPress} disabled={extra?.busy} accessibilityRole="button" accessibilityLabel={extra?.a11y ?? label} accessibilityState={{ selected: extra?.on, busy: extra?.busy }}
      style={({ pressed }) => [r.act, (pressed || extra?.busy) && { opacity: 0.6 }]}>
      <View style={[r.ic, extra?.on && { backgroundColor: colour.bg }]}>{icon}</View>
      <Text style={r.actText}>{label}</Text>
    </Pressable>
  )
  return (
    <View style={[r.reel, { height }]} accessibilityLabel={item.title}>
      {picture ? (
        <>
          <Image source={{ uri: picture }} style={r.blur} contentFit="cover" blurRadius={28} recyclingKey={`blur:${picture}`} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" />
          <View style={r.dim} />
        </>
      ) : null}
      {frame}
      <View style={[r.info, { bottom: 112 + insets.bottom }]}>
        <Pressable onPress={() => toPlayer(who.id)} accessibilityRole="link" style={r.who}>
          <View style={r.faceRing}><Face name={who.name} sport={who.sport} photo={who.photo} size={40} /></View>
          <Text style={r.name} numberOfLines={1}>{who.name}</Text>
        </Pressable>
        <Text style={r.title} numberOfLines={3}>{item.kind === 'post' ? `${shortName(who.name)} on ${item.post?.platform ?? ''}` : item.title}</Text>
        <Text style={r.src}>{sourceLine(item, now, true)}{entry.moment ? ` · after ${entry.moment}` : ''}</Text>
      </View>
      <View style={[r.side, { bottom: 120 + insets.bottom, gap: short ? 12 : 18 }]}>
        {!entry.following || !mine
          ? act(mine ? 'Following' : 'Follow', mine ? <CheckIcon size={20} color={colour.text} /> : <PlusIcon size={20} color="#FFFFFF" />, () => follows.toggle(entry),
            { on: mine, busy: follows.busy(entry), a11y: mine ? `Following ${who.name}` : `Follow ${who.name}` })
          : null}
        {act('Share', <ShareIcon size={20} color="#FFFFFF" />, () => share(shareFeedItem(who.id, item)), { a11y: shareFeedItem(who.id, item).label })}
        {act('More', <Dots color="#FFFFFF" />, onMore, { a11y: 'More about this item' })}
      </View>
    </View>
  )
}

// ---------------------------------------------------------------- Today

/** Today's "From your players" (the web's components/Feed.tsx): after Results, before Coming up; the two best of
 * the fan's Feed (Following) right now in the edge layout's cards, boxed (whichever layout the Feed tab draws: a
 * full-screen one doesn't sit in a page), and the way to the rest. Absent while their Feed is empty. Read once the
 * follow list is in, and again when it comes back changed, when Today is pulled (`again`) and when the app comes
 * back to the front, since Today stays open where the web's page would be loaded anew. */
export function FromYourPlayers({ now, again = 0 }: { now: number; again?: number }) {
  const { cards, loaded, heard } = useFollowing()
  const [back, setBack] = useState(0)
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') setBack((n) => n + 1) })
    return () => sub.remove()
  }, [])
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const { page, hide } = useMyFeed(null, 2, loaded ? `${who}:${heard}:${again}:${back}` : null)
  const [opened, setOpened] = useState<FeedEntry | null>(null)
  const video = useVideo()
  const go = useGoToPlace()
  if (!loaded || !page?.items.length) return null
  const all = () => go('/feed', { tab: 'following' })
  return (
    <View style={{ gap: 10 }} accessibilityLabel="From your players">
      <SectionHead title="From your players" right={<Text onPress={all} accessibilityRole="link" style={[t.eyebrow, { color: colour.text, textDecorationLine: 'underline' }]}>All {page.count}</Text>} />
      <View style={{ gap: 12 }}>
        {page.items.map((x) => <EdgePost key={x.item.id} entry={x} now={now} boxed onPlay={video.play} onMore={() => setOpened(x)} />)}
      </View>
      <Btn label={`All ${page.count} in your Feed`} onPress={all} style={{ height: 52 }} />
      <ItemSheet item={opened?.item ?? null} name={opened?.player.name ?? ''} onClose={() => setOpened(null)} onHide={(_, verdict) => (opened ? hide(opened, verdict) : Promise.resolve())}>
        {opened ? <EdgeSheetExtras entry={opened} onGo={() => setOpened(null)} /> : null}
      </ItemSheet>
      {video.player}
    </View>
  )
}

const f = StyleSheet.create({
  tabs: { flexDirection: 'row', gap: 20, marginHorizontal: -GUTTER, marginTop: -6, paddingHorizontal: GUTTER, borderBottomWidth: 1, borderBottomColor: colour.line },
  tab: { height: 42, justifyContent: 'center', marginBottom: -1, borderBottomWidth: 3, borderBottomColor: 'transparent' },
  label: { fontFamily: font[800], fontSize: 14, color: colour.muted },
  inkTabs: { flexDirection: 'row', justifyContent: 'center', gap: 22 },
  inkTab: { paddingVertical: 4, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  inkLabel: {
    fontFamily: font[800], fontSize: 15, color: 'rgba(255,255,255,0.6)',
    textShadowColor: 'rgba(0,0,0,0.5)', textShadowRadius: 8, textShadowOffset: { width: 0, height: 1 },
  },
})

const e = StyleSheet.create({
  post: { backgroundColor: colour.surface, overflow: 'hidden' },
  wide: { marginHorizontal: -GUTTER },
  boxed: { borderRadius: RADIUS_CARD },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 54, paddingVertical: 8, paddingRight: 6, paddingLeft: 14 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 10, minWidth: 0, flexShrink: 1 },
  // A 2px ring of the sport's colour with 2px of the card between it and the photo (.epost-ring).
  ring: { width: 42, height: 42, borderRadius: 21, borderWidth: 2, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  name: { fontFamily: font[800], fontSize: 14, lineHeight: 18, color: colour.text, flexShrink: 1 },
  sep: { fontFamily: font[700], fontSize: 14, color: colour.muted },
  follow: { height: 36, justifyContent: 'center', paddingHorizontal: 2 },
  followText: { fontFamily: font[800], fontSize: 13, color: colour.follow },
  more: { marginLeft: 'auto', width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  pic: { width: '100%', aspectRatio: 16 / 9, overflow: 'hidden' },
  dur: {
    position: 'absolute', right: 10, bottom: 10, height: 22, paddingHorizontal: 8, borderRadius: 6, overflow: 'hidden',
    backgroundColor: 'rgba(13,13,13,0.8)', color: '#FFFFFF', fontFamily: font[800], fontSize: 11, lineHeight: 22,
  },
  block: { aspectRatio: 16 / 9, justifyContent: 'space-between', gap: 16, padding: 18 },
  quote: { paddingTop: 6, paddingHorizontal: 16, paddingBottom: 4, fontFamily: font[600], fontStyle: 'italic', fontSize: 18, lineHeight: 25, color: '#2A2B2E' },
  cap: { gap: 3, paddingTop: 10, paddingHorizontal: 14, paddingBottom: 14 },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 20, color: colour.text },
  src: { fontFamily: font[700], fontSize: 12, lineHeight: 16, color: colour.muted },
  extras: { flexDirection: 'row', alignItems: 'center', gap: 18 },
  moreFrom: { marginLeft: 'auto', flexShrink: 1, fontFamily: font[800], fontSize: 13, lineHeight: 36, color: colour.text, textDecorationLine: 'underline', textAlign: 'right' },
})

const r = StyleSheet.create({
  reels: { flex: 1, backgroundColor: colour.text },
  reel: { overflow: 'hidden', backgroundColor: colour.text },
  blur: { position: 'absolute', top: -40, left: -40, right: -40, bottom: -40 },
  dim: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(0,0,0,0.55)' }, // the web's brightness(0.45)
  frame: { position: 'absolute', left: 0, right: 0, aspectRatio: 16 / 9, overflow: 'hidden', backgroundColor: '#000000', alignItems: 'center', justifyContent: 'center' },
  bigplay: { width: 64, height: 64, borderRadius: 32, backgroundColor: 'rgba(243,243,239,0.92)', alignItems: 'center', justifyContent: 'center' },
  words: { alignItems: 'stretch', justifyContent: 'space-between', gap: 12, padding: 18 },
  tabs: { position: 'absolute', left: 0, right: 0 },
  info: { position: 'absolute', left: 16, right: 84, gap: 10 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 9, alignSelf: 'flex-start', maxWidth: '100%' },
  faceRing: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#FFFFFF', alignItems: 'center', justifyContent: 'center' }, // .reel-who .face's 2px white ring
  name: { fontFamily: font[800], fontSize: 15, color: '#FFFFFF', flexShrink: 1 },
  title: { fontFamily: font[700], fontSize: 17, lineHeight: 23, color: '#FFFFFF' },
  src: { fontFamily: font[700], fontSize: 12, lineHeight: 16, color: 'rgba(255,255,255,0.75)' },
  side: { position: 'absolute', right: 12, alignItems: 'center' },
  act: { alignItems: 'center', gap: 4 },
  ic: { width: 46, height: 46, borderRadius: 23, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center' },
  actText: { fontFamily: font[800], fontSize: 11, color: '#FFFFFF' },
  end: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: GUTTER },
})

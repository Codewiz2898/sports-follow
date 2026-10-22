// The fan's Feed in the two layouts the server picks between, the web's components/FeedLayouts.tsx (BHA-349; GET
// /api/me/feed/items' `layout` and /api/config's `feed_layout`, which the admin panel changes live): edge to edge
// (the S2Edge board), full-width posts down the page; and full screen (the S5Reels board), one item a screen, paging
// from one to the next. A video is YouTube's biggest thumbnail until it plays, in the picture's place, as the web's
// does: YouTube's player in a WebView on the site's address (InlineVideo), the tab bar where it was. It starts when
// tapped, with its sound, or by itself (BHA-359), once more than half of it is on screen, as the fan's autoplay
// setting allows (autoplay.ts): muted until the fan turns the sound on, then with it until they turn it off (BHA-381,
// sound.ts; the sound button beside a playing video). One at a time, and it stops once it's off the screen, the Feed's
// left or the app's behind another. An upright video (a Short) plays as Shorts do (BHA-384): full screen in the
// full-screen layout, its frame as wide as the phone allows under the tabs and who and what in the strip below it
// (ShortReel), and from edge to edge in a viewer of the Feed's Shorts over everything (ShortsViewer); never anything of
// the app's over its player, as YouTube's rules want. Each card names its channel under whose it is. Items of
// players the fan doesn't follow (For you's) offer Follow, which works where it is. "…" (More) opens "Why this is here". Also Today's "From your players" (the web's components/Feed.tsx),
// in the edge layout's cards.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { Image } from 'expo-image'
import { router, useIsFocused } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { ActivityIndicator, AppState, FlatList, Linking, Modal, Pressable, StyleSheet, Text, useWindowDimensions, View, type ViewabilityConfigCallbackPairs, type ViewToken } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Svg, { Circle } from 'react-native-svg'
import { WebView } from 'react-native-webview'
import { channelOf, duration, isShort, openLabel, refusedHere, shortFrame, sourceMark, statsLine, upright, youtubeEmbed } from '../../web/src/feed'
import { safeHref, shortName } from '../../web/src/format'
import { shareFeedItem } from '../../web/src/share'
import { api, API_URL, type FeedEntry, type FeedItem, type FeedLayout, type FeedTabKey } from './api'
import { useSports, useTone } from './catalog'
import { useAutoplay } from './autoplay'
import { useFollowing } from './following'
import { BackIcon, CheckIcon, MutedIcon, PlayIcon, PlusIcon, ShareIcon, SoundIcon } from './icons'
import { useMyFeed } from './myFeed'
import { Face, ItemSheet, Picture } from './post'
import { feedReads, type FeedReads } from './refetchFeed'
import { setSound, useSound } from './sound'
import { share, ShareButton } from './share'
import { SourceMark } from './sourceMark'
import { useGoToPlace } from './tabbar'
import { colour, dsp, font, GUTTER, RADIUS_CARD, t } from './theme'
import { Btn, SectionHead } from './ui'
import { useInFront } from './watch'

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
  // Said for a few seconds, then gone: over the full screen it sits above the tab bar, and shouldn't stay there.
  useEffect(() => {
    if (!error) return
    const gone = setTimeout(() => setError(null), 5000)
    return () => clearTimeout(gone)
  }, [error])
  return { follows, error }
}

/** The one Feed video playing: which item, whether it started by itself (`auto`), and whether the fan has touched its
 * player since (`kept`). Whether it has its sound is the fan's, for every video (sound.ts). */
export interface Playing { id: string; auto?: boolean; kept?: boolean }

/** Which of a list's videos plays (one at a time). `play` starts one with its sound (a tap), which turns the sound on
 * for the ones after it. While `on` (the screen in front, the app too) and `auto` (a list that autoplays, and the
 * fan's setting allows it now), the topmost video more than half on screen starts by itself, with the sound as the fan
 * last left it, unless one the fan started or touched (`keep`) is still playing; a
 * video goes once none of it is on screen. `pairs` is what the list watches its items with (FlatList's
 * viewabilityConfigCallbackPairs), and `playing` what it draws again on (extraData); a list that isn't watched
 * (Today's cards, `auto` false) keeps what plays till the screen goes. */
export function usePlaying(on: boolean, auto = true): { playing: Playing | null; play: (item: FeedItem) => void; keep: () => void; pairs: ViewabilityConfigCallbackPairs } {
  const [playing, setPlaying] = useState<Playing | null>(null)
  const allowed = useAutoplay(auto)
  const starts = useRef(false)
  starts.current = on && auto && allowed
  const top = useRef<string | null>(null) // the topmost video more than half on screen
  const seen = useRef<ReadonlySet<string>>(new Set()) // every item any of which is on screen
  const watched = useRef(auto)
  watched.current = auto
  const settle = useCallback(() => setPlaying((was) => {
    const now = was && (!watched.current || seen.current.has(was.id)) ? was : null
    // The 60% watch waits 250ms to say, the 1% one doesn't: one just gone off screen isn't started again meanwhile.
    const next = top.current && seen.current.has(top.current) ? top.current : null
    if (starts.current && next && (!now || (now.auto && !now.kept)) && now?.id !== next) return { id: next, auto: true }
    return now
  }), [])
  useEffect(() => { if (on) settle(); else setPlaying(null) }, [on, allowed, settle])
  const play = useCallback((item: FeedItem) => {
    if (item.video?.youtube_id) { setPlaying({ id: item.id }); setSound(true) }
    else open(item.url)?.()
  }, [])
  const keep = useCallback(() => setPlaying((was) => (was && was.auto && !was.kept ? { ...was, kept: true } : was)), [])
  const pairs = useRef<ViewabilityConfigCallbackPairs>([
    { viewabilityConfig: { itemVisiblePercentThreshold: 60, minimumViewTime: 250 }, onViewableItemsChanged: ({ viewableItems }: { viewableItems: ViewToken<FeedEntry>[] }) => {
      top.current = viewableItems.find((v) => v.isViewable && v.item?.item.kind === 'video' && v.item.item.video?.youtube_id)?.item?.item.id ?? null
      settle()
    } },
    { viewabilityConfig: { itemVisiblePercentThreshold: 1 }, onViewableItemsChanged: ({ viewableItems }: { viewableItems: ViewToken<FeedEntry>[] }) => {
      seen.current = new Set(viewableItems.filter((v) => v.isViewable && v.item).map((v) => (v.item as FeedEntry).item.id))
      settle()
    } },
  ]).current
  return { playing, play, keep, pairs }
}

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
// The player page's own script, YouTube's IFrame Player API over its player: its errors come to the app (onMessage)
// as {"error": code}; window.sound(muted) turns its sound off or on where it plays (the app's sound button, BHA-381);
// and what YouTube's own sound button did is told to the app as {"sound": on} once it has played (asked of the
// player: it starts by itself before the API is on it, so there's no event for that). A change the page itself just
// asked for takes YouTube a moment to report, so it isn't told back.
const PLAYER_SCRIPT = `
var want = START, ready = false, played = false, quiet = 0, p
function say(what) { window.ReactNativeWebView.postMessage(JSON.stringify(what)) }
function apply() {
  if (!ready) return
  quiet = Date.now() + 1500
  if (want) p.mute(); else { p.unMute(); if (p.getVolume() === 0) p.setVolume(100) }
}
window.sound = function (muted) { want = muted; apply() }
window.onYouTubeIframeAPIReady = function () {
  p = new YT.Player('v', { events: {
    onError: function (e) { say({ error: e.data }) },
    onReady: function () {
      ready = true; apply()
      setInterval(function () {
        played = played || p.getPlayerState() === YT.PlayerState.PLAYING
        if (!played || Date.now() < quiet) return
        var muted = p.isMuted()
        if (muted !== want) { want = muted; say({ sound: !muted }) }
      }, 400)
    },
  } })
}`
const told = new Set<string>() // the videos this app has told the server won't play here, once each

/** YouTube's no-cookie player playing a Feed video where its picture was: a WebView of a page with the player alone,
 * on the site's address (YouTube's embeds want a site around them, as the web's are), with its sound as the fan's
 * left it (sound.ts): turned on and off where it plays, through YouTube's IFrame Player API, and YouTube's own sound
 * button turns it on or off for the videos after it too. YouTube's own buttons do the rest: pausing, its full screen
 * (upright, as the phone is held). Drawn once a video (`key` it by the video). A link out of it opens in the YouTube
 * app or the browser. One that won't load says so, with the way to the video; one YouTube won't play here (its owner
 * keeps it off other sites: refusedHere) says that, and the server is told, once, so this fan's Feed doesn't serve it
 * again, nor anyone's once enough networks have said so (BHA-380). `bare`: without YouTube's controls, a Short's (BHA-384). */
function InlineVideo({ item, playerId, bare, onTouch }: { item: FeedItem; playerId: number; bare?: boolean; onTouch?: () => void }) {
  const [failed, setFailed] = useState<'load' | 'refused' | null>(null)
  const site = `${API_URL}/`
  const id = item.video?.youtube_id ?? ''
  const muted = !useSound()
  const [startMuted] = useState(muted) // the page starts as the sound is; a change after goes through the player
  const web = useRef<WebView>(null)
  const html = useMemo(() => '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    + '<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}iframe{display:block;width:100%;height:100%;border:0}</style></head><body>'
    + `<iframe id="v" src="${escape(youtubeEmbed(id, startMuted, API_URL, bare))}" title="${escape(item.title)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`
    + `<script>${PLAYER_SCRIPT.replace('START', String(startMuted))}</script><script src="https://www.youtube.com/iframe_api"></script>`
    + '</body></html>', [id, startMuted, item.title, bare])
  const tell = useCallback(() => web.current?.injectJavaScript(`window.sound && window.sound(${muted}); true;`), [muted])
  useEffect(() => { tell() }, [tell])
  const leave = (url: string) => { open(url)?.() }
  const down = useRef<number | null>(null) // where a touch began: one that ends where it began is a tap, not a scroll
  const heard = (data: string) => {
    let said: { error?: unknown; sound?: unknown }
    try { said = JSON.parse(data) as typeof said } catch { return }
    if (typeof said.sound === 'boolean') { setSound(said.sound); return }
    const code = said.error
    if (!refusedHere(code)) return // another of the player's errors: YouTube's own message says it, as on the web
    setFailed('refused')
    if (told.has(item.id)) return
    told.add(item.id)
    api.feedUnplayable(playerId, item.id).catch(() => { told.delete(item.id) }) // said again the next time it's played
  }
  if (failed) {
    return (
      <View style={v.cover}>
        <Text style={[t.small, { color: '#FFFFFF' }]}>{failed === 'refused' ? 'This one plays only on YouTube.' : "Couldn't load the player."}</Text>
        <Btn kind="link" label={openLabel(item)} colours={{ fg: '#FFFFFF' }} onPress={() => leave(item.url)} />
      </View>
    )
  }
  return (
    // The whole of its frame, whatever that centres (the play button it replaces). A tap on it is the fan's: it stays
    // playing as they scroll on (they may have turned its sound on).
    <View style={StyleSheet.absoluteFill} onTouchStart={(ev) => { down.current = ev.nativeEvent.pageY }}
      onTouchEnd={(ev) => { if (down.current != null && Math.abs(ev.nativeEvent.pageY - down.current) < 12) onTouch?.(); down.current = null }}>
    <WebView
      ref={web}
      source={{ html, baseUrl: site }}
      onLoadEnd={tell} // a change made while the page was loading
      style={v.web}
      containerStyle={v.web}
      originWhitelist={['*']}
      onShouldStartLoadWithRequest={(r) => {
        if (!r.isTopFrame || r.url === site || r.url.startsWith('about:') || r.url.startsWith('data:')) return true
        leave(r.url)
        return false
      }}
      onOpenWindow={(e) => leave(e.nativeEvent.targetUrl)}
      mediaPlaybackRequiresUserAction={false} // it plays at once, with its sound when the fan has that on
      allowsInlineMediaPlayback
      allowsFullscreenVideo
      setBuiltInZoomControls={false}
      overScrollMode="never"
      startInLoadingState
      renderLoading={() => <View style={v.cover}><ActivityIndicator color="#FFFFFF" /></View>}
      renderError={() => <View style={v.cover}><Text style={[t.small, { color: '#FFFFFF' }]}>Couldn't load the player.</Text></View>}
      onMessage={(e) => heard(e.nativeEvent.data)}
      onRenderProcessGone={() => setFailed('load')} // its process was ended (memory): said, rather than blank
    />
    </View>
  )
}

/** The sound of every Feed video, near the one playing but never over it (YouTube's rules want nothing over its
 * player): a speaker, crossed out while muted; a tap turns it on or off for this video and the ones after it. Under
 * the player's right-hand corner on paper; `onInk`, the full screen's, in a ring at the top right, by the tabs. */
function SoundButton({ onInk }: { onInk?: boolean }) {
  const on = useSound()
  const Icon = on ? SoundIcon : MutedIcon
  return (
    <Pressable onPress={() => setSound(!on)} accessibilityRole="button" accessibilityLabel={on ? 'Mute the videos' : 'Turn the videos’ sound on'}
      style={({ pressed }) => [onInk ? r.sound : e.sound, pressed && { opacity: 0.6 }]}>
      <Icon size={20} color={onInk ? '#FFFFFF' : colour.text} />
    </Pressable>
  )
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

/** One post edge to edge (.epost): who it's about (photo ringed in their sport's colour, name and under it the
 * channel, Follow when the fan doesn't, "…"), the full-width 16:9 picture with the video's length (an upright video
 * upright in the middle of a 4:5 one), the title, and "views · when". `boxed`: a rounded card inside the page's
 * margins (Today's), else the screen's full width. `playing`: its video plays in the picture's place. */
export function EdgePost({ entry, now, follows, boxed, playing, onPlay, onKeep, onMore }: {
  entry: FeedEntry; now: number; follows?: Follows; boxed?: boolean; playing?: Playing | null; onPlay: (item: FeedItem) => void; onKeep?: () => void; onMore: () => void
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
  const channel = channelOf(item)

  let media: ReactNode = null
  if (item.kind === 'video' && id) {
    // An upright one (a Short): 9:16 in the middle of a 4:5 band, its 16:9 thumbnail's pillarbox cropped off exactly.
    const still = playing ? <InlineVideo key={id} item={item} playerId={who.id} onTouch={onKeep} /> : picture ? <Picture src={picture} onGone={gone} /> : null
    media = playing ? (
      <View style={[e.pic, upright(item) && e.band, { backgroundColor: colour.text }]}>
        {upright(item) ? <View style={e.tall}>{still}</View> : still}
      </View>
    ) : (
      <Pressable onPress={() => onPlay(item)} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`}
        style={[e.pic, upright(item) && e.band, { backgroundColor: colour.text }]}>
        {upright(item) ? <View style={e.tall}>{still}</View> : still}
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
  const from = [item.kind === 'post' ? `${item.source} on ${item.post?.platform ?? ''}` : statsLine(item, now), entry.moment ? `after ${entry.moment}` : '']
    .filter(Boolean).join(' · ')
  return (
    <View style={[e.post, boxed ? e.boxed : e.wide]}>
      <View style={e.head}>
        <Pressable onPress={() => toPlayer(who.id)} accessibilityRole="link" style={e.who}>
          <View style={[e.ring, { borderColor: tone.sport }]}><Face name={who.name} sport={who.sport} photo={who.photo} size={34} /></View>
          <View style={e.names}>
            <Text style={e.name} numberOfLines={1}>{who.name}</Text>
            {channel ? <Text style={e.chan} numberOfLines={1}>{channel}</Text> : null}
          </View>
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
        {sourceMark(item) ? <View style={e.markAt}><SourceMark item={item} /></View> : null}
        <Pressable onPress={onMore} accessibilityRole="button" accessibilityLabel="More about this item" hitSlop={4}
          style={({ pressed }) => [e.more, sourceMark(item) ? { marginLeft: 0 } : null, pressed && { backgroundColor: colour.bg }]}>
          <Dots color={colour.text} />
        </Pressable>
      </View>
      {media}
      <View style={e.foot}>
        <View style={e.cap}>
          {titled ? <Text style={e.title} numberOfLines={3}>{item.title}</Text> : null}
          {from ? <Text style={e.src}>{from}</Text> : null}
        </View>
        {playing ? <SoundButton /> : null}
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
export function Reels({ entries, now, tab, read, onTab, follows, videos, onMore, onNear, onShort, footer }: {
  entries: FeedEntry[]; now: number; tab: FeedTabKey; read: string | null; onTab: (tab: FeedTabKey) => void; follows: Follows
  videos: ReturnType<typeof usePlaying>; onMore: (entry: FeedEntry) => void; onNear?: () => void
  onShort?: (on: boolean) => void; footer?: ReactElement | null
}) {
  const insets = useSafeAreaInsets()
  const [height, setHeight] = useState(0)
  const [shown, setShown] = useState(0) // which screen it has settled on
  const list = useRef<FlatList<FeedEntry>>(null)
  useEffect(() => { list.current?.scrollToOffset({ offset: 0, animated: false }); setShown(0) }, [tab, read])
  // A Short on screen: the tab bar over it turns dark and see-through, as Shorts' own (tabbar.tsx).
  const short = isShort(entries[shown]?.item)
  useEffect(() => { onShort?.(short) }, [short, onShort])
  useEffect(() => () => onShort?.(false), [onShort])
  const settled = (y: number) => { if (height > 0) setShown(Math.round(y / height)) }
  return (
    // Sideways is only ever the video player over it (watch.tsx): a screen that height would move the fan to another
    // item, so the reels keep their upright one.
    <View style={r.reels} onLayout={(ev) => { const box = ev.nativeEvent.layout; if (box.height >= box.width) setHeight(Math.round(box.height)) }}>
      {height > 0 ? (
        <FlatList
          ref={list}
          data={entries}
          keyExtractor={(x) => x.item.id}
          renderItem={({ item: x }) => (
            isShort(x.item) ? (
              <ShortReel entry={x} now={now} height={height} foot={88 + insets.bottom} follows={follows} playing={videos.playing?.id === x.item.id ? videos.playing : null}
                onPlay={videos.play} onKeep={videos.keep} onMore={() => onMore(x)} />
            ) : (
              <Reel entry={x} now={now} height={height} follows={follows} playing={videos.playing?.id === x.item.id ? videos.playing : null}
                onPlay={videos.play} onKeep={videos.keep} onMore={() => onMore(x)} />
            )
          )}
          extraData={videos.playing}
          viewabilityConfigCallbackPairs={videos.pairs}
          // One screen a fling, a flick enough: Android's own snapping scroll (pagingEnabled alone wants half a screen dragged).
          snapToInterval={height}
          snapToAlignment="start"
          decelerationRate="fast"
          disableIntervalMomentum
          showsVerticalScrollIndicator={false}
          overScrollMode="never"
          getItemLayout={(_, index) => ({ length: height, offset: height * index, index })}
          onMomentumScrollEnd={(ev) => settled(ev.nativeEvent.contentOffset.y)}
          onScrollEndDrag={(ev) => settled(ev.nativeEvent.contentOffset.y)}
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
      {videos.playing ? <View style={[r.soundAt, { top: insets.top + 11 }]}><SoundButton onInk /></View> : null}
    </View>
  )
}

/** One screen (.reel): the picture on a blurred, darkened copy of itself, a little above the middle; who and what
 * at the foot; Follow (for a player the fan doesn't follow), Share and More down the right. */
function Reel({ entry, now, height, follows, playing, onPlay, onKeep, onMore }: {
  entry: FeedEntry; now: number; height: number; follows: Follows; playing: Playing | null; onPlay: (item: FeedItem) => void; onKeep: () => void; onMore: () => void
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
  const channel = channelOf(item)
  // The frame's middle a little above the screen's (top: 42%, translateY(-58%)); higher on a short phone, clear of the buttons.
  const short = height < 700
  const top = Math.max(insets.top + 56, height * (short ? 0.34 : 0.42) - (width * 9 / 16) * 0.58)
  // An upright video (a Short): 9:16 under the tabs, its foot above who and what (three lines of title at most: 310
  // above the screen's foot) and 70 clear each side, so nothing of the app's sits over YouTube's player while it
  // plays here (.reel-frame.upright).
  const tall = upright(item)
  const tallHeight = Math.max(0, Math.min(height - 374 - insets.top - insets.bottom, ((width - 140) * 16) / 9))
  const place = tall ? { top: insets.top + 64, left: (width - (tallHeight * 9) / 16) / 2, right: undefined, width: (tallHeight * 9) / 16, height: tallHeight, aspectRatio: undefined } : { top }

  let frame: ReactNode
  if (item.kind === 'video' && id && playing) {
    frame = <View style={[r.frame, place]}><InlineVideo key={id} item={item} playerId={who.id} onTouch={onKeep} /></View>
  } else if (item.kind === 'video' && id) {
    frame = (
      <Pressable onPress={() => onPlay(item)} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={[r.frame, place]}>
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
        {/* The source's icon before the channel, not down the side: the buttons there grow toward the player. */}
        {channel ? <View style={r.chanRow}><View style={r.markAt}><SourceMark item={item} /></View><Text style={[r.chan, { marginTop: 0, flexShrink: 1 }]} numberOfLines={1}>{channel}</Text></View> : null}
        <Text style={r.title} numberOfLines={3}>{item.kind === 'post' ? `${shortName(who.name)} on ${item.post?.platform ?? ''}` : item.title}</Text>
        <Text style={r.src}>{[statsLine(item, now, true), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</Text>
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

// ---------------------------------------------------------------- Shorts

const SHORT_TOP = 58 // below the status bar: the tabs (or Back) and the sound button, level, above the Short
const STRIP = { min: 72, room: 104 } // who and what under it: a row and a title line at least; room for two and the numbers

/** A Short as Shorts play (BHA-384, the web's .reel.short): on black, its 9:16 frame under the tabs as wide as the
 * phone allows once what's under it is clear (shortFrame), its picture there till it plays (so nothing moves as it
 * starts), YouTube's player without its controls once it does; who, the channel, Follow, Share and More in a row
 * under it, then the title, and the numbers when there's room. `foot`: what's below that strip (the tab bar, or
 * the phone's own bar in the viewer), which nothing is drawn under. */
function ShortReel({ entry, now, height, foot, roomy, follows, playing, onPlay, onKeep, onMore, onLeave }: {
  entry: FeedEntry; now: number; height: number; foot: number; roomy?: boolean; follows: Follows; playing: Playing | null
  onPlay: (item: FeedItem) => void; onKeep: () => void; onMore: () => void; onLeave?: () => void // onLeave: before the player's page opens
}) {
  const { player: who, item } = entry
  const insets = useSafeAreaInsets()
  const { width } = useWindowDimensions()
  const [noPicture, setNoPicture] = useState(false)
  useEffect(() => setNoPicture(false), [item.picture])
  const picture = !noPicture && item.picture ? safeHref(item.picture) : undefined
  const mine = follows.following(entry)
  const channel = channelOf(item)
  const box = shortFrame(width, height, insets.top + SHORT_TOP, foot + (roomy ? STRIP.room : STRIP.min))
  const room = height - box.top - box.height - foot - 10 // the strip's, under the frame
  const lines = room >= 36 + 4 + 40 ? 2 : room >= 36 + 4 + 20 ? 1 : 0
  const numbers = room >= 36 + 4 + 40 + 4 + 16
  const icon = (label: string, child: ReactNode, onPress: () => void) => (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} hitSlop={6} style={({ pressed }) => [sh.icon, pressed && { opacity: 0.6 }]}>{child}</Pressable>
  )
  return (
    <View style={[sh.reel, { height }]} accessibilityLabel={item.title}>
      {playing ? (
        <View style={[sh.frame, box]}><InlineVideo key={item.video?.youtube_id} item={item} playerId={who.id} bare onTouch={onKeep} /></View>
      ) : (
        <Pressable onPress={() => onPlay(item)} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={[sh.frame, box]}>
          {/* The 16:9 thumbnail YouTube keeps of a Short, covering the 9:16 frame: its pillarbox cropped off. */}
          {picture ? <Picture src={picture} onGone={() => setNoPicture(true)} /> : null}
          <View style={r.bigplay}><PlayIcon size={24} color={colour.text} /></View>
        </Pressable>
      )}
      <View style={[sh.strip, { top: box.top + box.height + 8 }]}>
        <View style={sh.row}>
          <Pressable onPress={() => { onLeave?.(); toPlayer(who.id) }} accessibilityRole="link" style={sh.who}>
            <View style={sh.faceRing}><Face name={who.name} sport={who.sport} photo={who.photo} size={28} /></View>
            <Text style={sh.name} numberOfLines={1}>{who.name}{channel ? <Text style={sh.chan}>{`  ·  ${channel}`}</Text> : null}</Text>
          </Pressable>
          {!entry.following || !mine ? (
            <Pressable onPress={() => follows.toggle(entry)} disabled={follows.busy(entry)} accessibilityRole="button" accessibilityState={{ selected: mine, busy: follows.busy(entry) }}
              accessibilityLabel={mine ? `Following ${who.name}` : `Follow ${who.name}`} hitSlop={6}
              style={({ pressed }) => [sh.follow, mine && sh.following, (pressed || follows.busy(entry)) && { opacity: 0.6 }]}>
              <Text style={[sh.followText, mine && { color: '#FFFFFF' }]}>{mine ? 'Following' : 'Follow'}</Text>
            </Pressable>
          ) : null}
          <SourceMark item={item} />
          {icon(shareFeedItem(who.id, item).label, <ShareIcon size={18} color="#FFFFFF" />, () => share(shareFeedItem(who.id, item)))}
          {icon('More about this item', <Dots color="#FFFFFF" />, onMore)}
        </View>
        {lines > 0 ? <Text style={sh.title} numberOfLines={lines}>{item.title}</Text> : null}
        {numbers ? <Text style={sh.src} numberOfLines={1}>{[statsLine(item, now, true), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</Text> : null}
      </View>
    </View>
  )
}

/** The Feed's Shorts over everything, from edge to edge (BHA-384): opened at the one tapped, `start`, one a screen,
 * snapping, the one on screen playing as the Feed's would (the fan's autoplay and sound), swiping through the rest of
 * what the Feed has loaded (`entries`; `onNear` loads more as the end comes near). Back, or the arrow, closes it, and the
 * Feed is where it was under it. No tab bar here: the strip under each has the room. Null `start`: closed. */
export function ShortsViewer({ entries, start, now, follows, onMore, onNear, onClose }: {
  entries: FeedEntry[]; start: string | null; now: number; follows: Follows
  onMore: (entry: FeedEntry) => void; onNear?: () => void; onClose: () => void
}) {
  const insets = useSafeAreaInsets()
  const front = useInFront()
  const [height, setHeight] = useState(0)
  const [first] = useState(() => entries.filter((x) => isShort(x.item))) // as it opened, so what's under the thumb stays
  const [list, setList] = useState(first)
  useEffect(() => { // more of the Feed's Shorts as its pages come, after those it opened with; one hidden ("…") goes
    setList((was) => [...was.filter((w) => entries.some((x) => x.item.id === w.item.id)),
      ...entries.filter((x) => isShort(x.item) && !was.some((w) => w.item.id === x.item.id))])
  }, [entries])
  const at = Math.max(0, first.findIndex((x) => x.item.id === start))
  const videos = usePlaying(start != null && front && height > 0)
  // The one tapped plays as it opens (a tap: with its sound), once the list is drawn.
  const { play } = videos
  useEffect(() => { if (height > 0 && first[at]) play(first[at].item) }, [height > 0])
  return (
    <Modal visible={start != null} animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={onClose}>
      <StatusBar style="light" />
      <View style={sh.viewer} onLayout={(ev) => { const box = ev.nativeEvent.layout; if (box.height >= box.width) setHeight(Math.round(box.height)) }}>
        {height > 0 ? (
          <FlatList
            key={height} // the Modal settles its height once it's up: the list starts again at the one tapped, lined up
            data={list}
            keyExtractor={(x) => x.item.id}
            renderItem={({ item: x }) => (
              <ShortReel entry={x} now={now} height={height} foot={insets.bottom + 12} roomy follows={follows} playing={videos.playing?.id === x.item.id ? videos.playing : null}
                onPlay={videos.play} onKeep={videos.keep} onMore={() => onMore(x)} onLeave={onClose} />
            )}
            extraData={videos.playing}
            viewabilityConfigCallbackPairs={videos.pairs}
            initialScrollIndex={at}
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
          />
        ) : null}
        <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Back to the Feed" hitSlop={6}
          style={({ pressed }) => [r.sound, sh.back, { top: insets.top + 11 }, pressed && { opacity: 0.6 }]}>
          <BackIcon size={22} color="#FFFFFF" />
        </Pressable>
        {videos.playing ? <View style={[r.soundAt, { top: insets.top + 11 }]}><SoundButton onInk /></View> : null}
      </View>
    </Modal>
  )
}

// ---------------------------------------------------------------- Today

const PICKED_FROM = 8 // the posts From your players picks its two from: a game picked on Today has some among them

/** Today's "From your players" (the web's components/Feed.tsx): at its foot, after This week and the Wire; the two
 * best of the fan's Feed (Following) right now in the edge layout's cards, boxed (whichever layout the Feed tab
 * draws: a full-screen one doesn't sit in a page), and the way to the rest. With a game picked on Today (`game`, a
 * sport's key), the two best of that game's among the first few; the way to the rest is to the whole Feed, so it
 * says no count. Absent while there are none. Read once the follow list is in, and again when the fan's players
 * change, when Today is pulled (`again`) and when the app comes back to the front, since Today stays open where the
 * web's page would be loaded anew; after news (a notification, a followed game moving on), only minutes later, at a
 * moment of this phone's own (refetchFeed.ts). Enough for a game picked, so picking one asks nothing more. */
export function FromYourPlayers({ now, again = 0, game = null }: { now: number; again?: number; game?: string | null }) {
  const sports = useSports()
  const { cards, loaded, heard } = useFollowing()
  const [back, setBack] = useState(0)
  const [told, setTold] = useState(0) // the reads after news
  const reads = useRef<FeedReads | null>(null)
  useEffect(() => {
    const r = feedReads(() => setTold((n) => n + 1), () => AppState.currentState === 'active')
    reads.current = r
    // Back in front: a read at once, in place of one after news still waiting (before its timer can run it too).
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') { r.now(); setBack((n) => n + 1) } })
    return () => { r.stop(); sub.remove() }
  }, [])
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const key = loaded ? `${who}:${again}:${back}` : null
  // News waits (refetchFeed.ts). Heard first: news that comes with a read at once (the follow list in or changed) is
  // that read's.
  useEffect(() => { if (heard) reads.current?.heard() }, [heard])
  useEffect(() => { if (key != null) reads.current?.now() }, [key])
  const { page, hide } = useMyFeed(null, PICKED_FROM, key == null ? null : `${key}:${told}`)
  const [opened, setOpened] = useState<FeedEntry | null>(null)
  // Its videos play in their cards on a tap, never by themselves (it isn't the Feed), and stop once Today's left.
  const focused = useIsFocused()
  const front = useInFront()
  const videos = usePlaying(focused && front, false)
  const go = useGoToPlace()
  const shown = (page?.items ?? []).filter((x) => !game || sports.info(x.player.sport).key === game).slice(0, 2)
  if (!loaded || !page || !shown.length) return null
  const all = () => go('/feed', { tab: 'following' })
  const count = game ? 'All' : `All ${page.count}`
  return (
    <View style={{ gap: 10 }} accessibilityLabel="From your players">
      <SectionHead title="From your players" right={<Text onPress={all} accessibilityRole="link" style={[t.eyebrow, { color: colour.text, textDecorationLine: 'underline' }]}>{count}</Text>} />
      <View style={{ gap: 12 }}>
        {shown.map((x) => <EdgePost key={x.item.id} entry={x} now={now} boxed playing={videos.playing?.id === x.item.id ? videos.playing : null} onPlay={videos.play} onMore={() => setOpened(x)} />)}
      </View>
      <Btn label={`${count} in your Feed`} onPress={all} style={{ height: 52 }} />
      <ItemSheet item={opened?.item ?? null} name={opened?.player.name ?? ''} onClose={() => setOpened(null)} onHide={(_, verdict) => (opened ? hide(opened, verdict) : Promise.resolve())}>
        {opened ? <EdgeSheetExtras entry={opened} onGo={() => setOpened(null)} /> : null}
      </ItemSheet>
    </View>
  )
}

const v = StyleSheet.create({
  web: { flex: 1, backgroundColor: '#000000' },
  cover: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: '#000000' },
})

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
  names: { flexShrink: 1, minWidth: 0 },
  name: { fontFamily: font[800], fontSize: 14, lineHeight: 18, color: colour.text, flexShrink: 1 },
  chan: { fontFamily: font[700], fontSize: 12, lineHeight: 15, color: colour.muted },  // the channel or outlet it's from (.epost-chan)
  sep: { fontFamily: font[700], fontSize: 14, color: colour.muted },
  follow: { height: 36, justifyContent: 'center', paddingHorizontal: 2 },
  followText: { fontFamily: font[800], fontSize: 13, color: colour.follow },
  more: { marginLeft: 'auto', width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  markAt: { marginLeft: 'auto' }, // the source's icon, by the "…" (.epost-head .source-mark)
  pic: { width: '100%', aspectRatio: 16 / 9, overflow: 'hidden' },
  band: { aspectRatio: 4 / 5, alignItems: 'center' },  // an upright video's (.epost-pic.upright)
  tall: { height: '100%', aspectRatio: 9 / 16, overflow: 'hidden' },
  dur: {
    position: 'absolute', right: 10, bottom: 10, height: 22, paddingHorizontal: 8, borderRadius: 6, overflow: 'hidden',
    backgroundColor: 'rgba(13,13,13,0.8)', color: '#FFFFFF', fontFamily: font[800], fontSize: 11, lineHeight: 22,
  },
  block: { aspectRatio: 16 / 9, justifyContent: 'space-between', gap: 16, padding: 18 },
  quote: { paddingTop: 6, paddingHorizontal: 16, paddingBottom: 4, fontFamily: font[600], fontStyle: 'italic', fontSize: 18, lineHeight: 25, color: '#2A2B2E' },
  foot: { flexDirection: 'row', alignItems: 'flex-start' },
  cap: { flex: 1, gap: 3, paddingTop: 10, paddingHorizontal: 14, paddingBottom: 14 },
  sound: { width: 44, height: 44, marginTop: 2, marginRight: 4, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },  // under the player's right-hand corner
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 20, color: colour.text },
  src: { fontFamily: font[700], fontSize: 12, lineHeight: 16, color: colour.muted },
  extras: { flexDirection: 'row', alignItems: 'center', gap: 18 },
  moreFrom: { marginLeft: 'auto', flexShrink: 1, fontFamily: font[800], fontSize: 13, lineHeight: 36, color: colour.text, textDecorationLine: 'underline', textAlign: 'right' },
})

const sh = StyleSheet.create({
  reel: { backgroundColor: '#000000', overflow: 'hidden' },
  viewer: { flex: 1, backgroundColor: '#000000' },
  frame: { position: 'absolute', overflow: 'hidden', backgroundColor: '#000000', alignItems: 'center', justifyContent: 'center' },
  strip: { position: 'absolute', left: 12, right: 12, gap: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 36 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 },
  faceRing: { width: 32, height: 32, borderRadius: 16, backgroundColor: '#FFFFFF', alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  name: { flexShrink: 1, fontFamily: font[800], fontSize: 14, color: '#FFFFFF' },
  chan: { fontFamily: font[700], fontSize: 13, color: 'rgba(255,255,255,0.7)' },
  follow: { height: 30, paddingHorizontal: 12, borderRadius: 999, backgroundColor: '#FFFFFF', justifyContent: 'center', flexShrink: 0 },
  following: { backgroundColor: 'transparent', borderWidth: 1, borderColor: 'rgba(255,255,255,0.5)' },
  followText: { fontFamily: font[800], fontSize: 12, color: colour.text },
  icon: { width: 34, height: 34, borderRadius: 17, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 20, color: '#FFFFFF' },
  src: { fontFamily: font[700], fontSize: 12, lineHeight: 16, color: 'rgba(255,255,255,0.7)' },
  back: { position: 'absolute', left: 13 },
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
  // The sound button, 44 across, level with the tabs (whose row is shorter, so it's beside it rather than in it: all of
  // it takes taps), at the side buttons' right edge, clear of the highest a player goes (insets.top + 56).
  soundAt: { position: 'absolute', right: 13 },
  sound: { width: 44, height: 44, borderRadius: 22, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center' },
  info: { position: 'absolute', left: 16, right: 84, gap: 10 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 9, alignSelf: 'flex-start', maxWidth: '100%' },
  faceRing: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#FFFFFF', alignItems: 'center', justifyContent: 'center' }, // .reel-who .face's 2px white ring
  name: { fontFamily: font[800], fontSize: 15, color: '#FFFFFF', flexShrink: 1 },
  chan: { marginTop: -4, fontFamily: font[800], fontSize: 14, lineHeight: 18, color: '#FFFFFF' },  // .reel-chan
  chanRow: { flexDirection: 'row', alignItems: 'center', minWidth: 0, marginTop: -4 }, // .reel-chan-row: the source's icon, then the channel
  markAt: { marginLeft: -9 }, // the icon in line with the words, its clear space to its left
  title: { fontFamily: font[700], fontSize: 17, lineHeight: 23, color: '#FFFFFF' },
  src: { fontFamily: font[700], fontSize: 12, lineHeight: 16, color: 'rgba(255,255,255,0.75)' },
  side: { position: 'absolute', right: 12, alignItems: 'center' },
  act: { alignItems: 'center', gap: 4 },
  ic: { width: 46, height: 46, borderRadius: 23, backgroundColor: 'rgba(255,255,255,0.16)', alignItems: 'center', justifyContent: 'center' },
  actText: { fontFamily: font[800], fontSize: 11, color: '#FFFFFF' },
  end: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: GUTTER },
})

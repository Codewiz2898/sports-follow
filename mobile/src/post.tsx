// The Feed's cards, the web's components/FeedPost.tsx (the InstaCards board): one shape for every kind, who it's
// about with their photo, where it's from and when, what it is, a picture, the words, then Share and the way to
// the source; the "…" opens "Why this is here". A video is YouTube's thumbnail behind a play button, and a tap
// opens it where it plays (the YouTube app when the phone has it, else the browser), as the player's Feed does
// (feed.tsx): nothing of YouTube's loads here. A story shows the image its publisher's feed attaches, or without
// one its headline on the sport's colour. A post is words only, opened where it was posted. Every picture has
// its box before it loads (16:9, a headline 4:3), so nothing moves as the pictures come in. Also the players'
// circles across the top of the Feed, and Today's "From your players".

import { Image } from 'expo-image'
import { router } from 'expo-router'
import { useEffect, useRef, useState } from 'react'
import { AppState, Linking, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import Svg, { Circle, Path } from 'react-native-svg'
import { duration, fromLine, openLabel, views } from '../../web/src/feed'
import { initials, safeHref, shortName } from '../../web/src/format'
import { shareFeedItem } from '../../web/src/share'
import { absolute, type FeedCircle, type FeedEntry, type FeedItem, type FeedVerdict, type FeedWho, type Photo } from './api'
import { useTone } from './catalog'
import type { Hide } from './feed'
import { useFollowing } from './following'
import { useMyFeed } from './myFeed'
import { ShareButton } from './share'
import { useGoToPlace } from './tabbar'
import { colour, dsp, font, GUTTER, RADIUS_CARD, t } from './theme'
import { Btn, ErrorBox, Eyebrow, SectionHead } from './ui'

const open = (url?: string | null) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

/** A player's photo in a circle (.face): the square around their face when the photos job found it, else the
 * photo's top, and their initials on their sport's colour without one (or once it fails to load). */
export function Face({ name, sport, photo, size = 38, ring }: { name: string; sport?: string | null; photo?: Photo | null; size?: number; ring?: string }) {
  const tone = useTone(sport)
  const [broken, setBroken] = useState<string | null>(null)
  const shown = photo && photo.url !== broken ? photo : null
  const c = shown?.crop
  const inner = ring ? size - 6 : size // a ring's paper border is inside the circle, as the web's border is
  // The face's square fills the circle: the photo scaled so the square is the circle's width, moved so it's in it.
  const framed = c && c.w > 0 && c.h > 0
    ? { position: 'absolute' as const, width: inner / c.w, height: inner / c.h, left: (-c.x / c.w) * inner, top: (-c.y / c.h) * inner }
    : StyleSheet.absoluteFill
  return (
    <View style={[s.face, { width: size, height: size, borderRadius: size / 2, backgroundColor: tone.sport }, ring ? { borderWidth: 3, borderColor: ring } : null]} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Text style={{ fontFamily: font[800], fontSize: Math.round(size * 0.32), letterSpacing: size * 0.0064, color: tone.on }}>{initials(name)}</Text>
      {/* Over the initials, which show while it loads. */}
      {shown ? (
        <Image source={{ uri: absolute(shown.url) }} style={framed} contentFit={c ? 'fill' : 'cover'} contentPosition={{ top: '15%', left: '50%' }}
          onError={() => setBroken(shown.url)} recyclingKey={shown.url} />
      ) : null}
    </View>
  )
}

function Dots() {
  return <Svg width={20} height={20} viewBox="0 0 24 24" fill={colour.muted}><Circle cx="5" cy="12" r="2" /><Circle cx="12" cy="12" r="2" /><Circle cx="19" cy="12" r="2" /></Svg>
}

function Play() {
  return <Svg width={22} height={22} viewBox="0 0 24 24" fill={colour.text}><Path d="M8 5v14l11-7z" /></Svg>
}

/** A picture as its host serves it, into the box it's given; nothing once it fails, or for YouTube's grey 120×90
 * stand-in for a video it has none for. */
function Picture({ src, onGone }: { src: string; onGone: () => void }) {
  return (
    <Image source={{ uri: src }} style={StyleSheet.absoluteFill} contentFit="cover" transition={120} recyclingKey={src}
      onError={onGone} onLoad={(e) => { if (e.source.width > 0 && e.source.width <= 120) onGone() }} />
  )
}

/** One item of the Feed as a post. `more`: "More from …" under a video, to the player's own Feed (on their own
 * page, the video's source instead). */
export function FeedPost({ who, item, now, moment, more = true, onMore }: { who: FeedWho; item: FeedItem; now: number; moment?: string | null; more?: boolean; onMore?: () => void }) {
  const tone = useTone(who.sport)
  const [noPicture, setNoPicture] = useState(false)
  useEffect(() => setNoPicture(false), [item.picture])
  const short = shortName(who.name)
  const own = item.kind === 'post' && Boolean(item.post?.own)
  const go = open(item.url)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : undefined
  const length = duration(item.video?.duration)
  const gone = () => setNoPicture(true)

  let media = null
  let caption = null
  if (item.kind === 'video' && id) {
    media = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={[s.media, { backgroundColor: colour.text }]}>
        {picture ? <Picture src={picture} onGone={gone} /> : null}
        <View style={s.play}><Play /></View>
        {length ? <Text style={s.chip}>{length}</Text> : null}
      </Pressable>
    )
    caption = (
      <>
        <Text style={s.title} numberOfLines={2}>{item.title}</Text>
        <Text style={s.sub}>{[views(item.video?.views), moment && `after ${moment}`].filter(Boolean).join(' · ')}</Text>
      </>
    )
  } else if (item.kind === 'news' && picture) {
    media = (
      <Pressable onPress={go} disabled={!go} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={[s.media, { backgroundColor: tone.sport }]}>
        <Picture src={picture} onGone={gone} />
      </Pressable>
    )
    caption = (
      <>
        <Text style={s.title}>{item.title}</Text>
        {item.summary ? <Text style={[s.sub, { fontFamily: font[600] }]} numberOfLines={2}>{item.summary}</Text> : null}
      </>
    )
  } else if (item.kind === 'news') {
    // No image came with the story: the headline is the picture, on the sport's colour.
    media = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" style={[s.block, { backgroundColor: tone.sport }]}>
        <Text style={[t.eyebrow, { color: tone.onSoft }]} numberOfLines={1}>{item.source}</Text>
        <Text style={[dsp(34), { lineHeight: 33, color: tone.on }]} numberOfLines={5}>{item.title}</Text>
      </Pressable>
    )
    caption = item.summary ? <Text style={[s.sub, { fontFamily: font[600] }]} numberOfLines={2}>{item.summary}</Text> : null
  } else if (item.kind === 'post') {
    media = (
      <Pressable onPress={go} disabled={!go} accessibilityRole="link">
        <Text style={s.quote} numberOfLines={3}>“{item.title}”</Text>
      </Pressable>
    )
    caption = <Text style={s.sub}>A tap opens it on {item.post?.platform ?? 'where it was posted'}</Text>
  }
  if (!media) return null

  // A video's way on is the player's own Feed; on their page, where it already is, the video where it was posted.
  const toFeed = item.kind === 'video' && more
  return (
    <View style={s.post}>
      <View style={s.head}>
        <Pressable onPress={() => router.push({ pathname: '/player/[id]', params: { id: String(who.id) } })} accessibilityRole="link" style={s.who}>
          <Face name={who.name} sport={who.sport} photo={who.photo} />
          <View style={{ gap: 1, minWidth: 0, flex: 1 }}>
            <Text style={s.name} numberOfLines={1}>{who.name}</Text>
            <Text style={s.sub} numberOfLines={1}>{fromLine(item, now)}</Text>
          </View>
        </Pressable>
        <View style={[s.tag, { backgroundColor: own ? colour.text : tone.sport }]}>
          <Text style={[s.tagText, { color: own ? colour.bg : tone.on }]} numberOfLines={1}>{own ? `From ${short}` : item.tag}</Text>
        </View>
        {onMore ? (
          <Pressable onPress={onMore} accessibilityRole="button" accessibilityLabel="Why this is here, and feedback" hitSlop={4} style={({ pressed }) => [s.more, pressed && { backgroundColor: colour.bg }]}>
            <Dots />
          </Pressable>
        ) : null}
      </View>
      {media}
      {caption ? <View style={s.cap}>{caption}</View> : null}
      <View style={s.acts}>
        <ShareButton {...shareFeedItem(who.id, item)} text="Share" />
        {toFeed ? (
          <Text onPress={() => router.push({ pathname: '/player/[id]', params: { id: String(who.id), tab: 'feed' } })} accessibilityRole="link" style={s.link} numberOfLines={1}>More from {short}</Text>
        ) : go ? (
          <Text onPress={go} accessibilityRole="link" style={s.link} numberOfLines={1}>{openLabel(item)}</Text>
        ) : null}
      </View>
    </View>
  )
}

/** The "…" sheet (.feed-sheet): why the item is here, and the three things a fan can tell the judge about it.
 * Back, or a tap above it, closes it. */
export function ItemSheet({ item: asked, name, onClose, onHide }: { item: FeedItem | null; name: string; onClose: () => void; onHide: Hide }) {
  const insets = useSafeAreaInsets()
  const last = useRef(asked)
  if (asked) last.current = asked
  const item = asked ?? last.current // still drawn while the sheet slides away
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (asked) { setError(null); setBusy(false) } }, [asked])
  const say = async (verdict: FeedVerdict) => {
    if (!asked) return
    setBusy(true)
    setError(null)
    try {
      await onHide(asked, verdict)
      onClose()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }
  const choice = (label: string, verdict: FeedVerdict) => (
    <Btn label={label} disabled={busy} colours={{ border: colour.line }} style={s.say} onPress={() => { say(verdict) }} />
  )
  return (
    <Modal visible={asked != null} transparent animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} accessibilityLabel="Close" />
      {item ? (
        <View style={[s.sheet, { paddingBottom: 28 + insets.bottom }]} accessibilityViewIsModal>
          <Eyebrow>Why this is here</Eyebrow>
          <Text style={s.reason}>{item.reason}</Text>
          {error ? <ErrorBox>{error}</ErrorBox> : null}
          <View style={{ gap: 8, marginTop: 4 }}>
            {choice('Not interested', 'not_interested')}
            {choice(`Not about ${shortName(name)}`, 'not_about_player')}
            {choice('Report', 'report')}
            <Btn kind="primary" label="Close" style={s.sheetBtn} onPress={onClose} />
          </View>
        </View>
      ) : null}
    </Modal>
  )
}

/** The players across the top of the Feed, "All" first (.circles): a ring in their sport's colour while they have
 * something the fan hasn't seen (`lit`), grey once they have; a tap shows only theirs, and again shows everyone's. */
export function Circles({ players, picked, lit, onPick }: { players: FeedCircle[]; picked: number | null; lit: (c: FeedCircle) => boolean; onPick: (id: number | null) => void }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.circles} contentContainerStyle={s.circlesRow} accessibilityLabel="Show one player's">
      <Pressable onPress={() => onPick(null)} accessibilityRole="button" accessibilityState={{ selected: picked == null }} accessibilityLabel="All" style={s.circle}>
        <View style={[s.ring, { backgroundColor: colour.text }]}>
          <View style={[s.all]}><Text style={{ fontFamily: font[800], fontSize: 12, color: colour.bg }}>ALL</Text></View>
        </View>
        <Text style={[s.nm, picked == null && s.nmOn]}>All</Text>
      </Pressable>
      {players.map((p) => <Player key={p.id} circle={p} picked={picked === p.id} lit={lit(p)} onPress={() => onPick(picked === p.id ? null : p.id)} />)}
    </ScrollView>
  )
}

function Player({ circle, picked, lit, onPress }: { circle: FeedCircle; picked: boolean; lit: boolean; onPress: () => void }) {
  const tone = useTone(circle.sport)
  const short = shortName(circle.name)
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: picked }} accessibilityLabel={lit ? `${short}, something new` : short} style={s.circle}>
      <View style={[s.ring, { backgroundColor: lit ? tone.sport : RING_SEEN }]}>
        <Face name={circle.name} sport={circle.sport} photo={circle.photo} size={56} ring={colour.bg} />
      </View>
      <Text style={[s.nm, picked && s.nmOn]} numberOfLines={1}>{short}</Text>
    </Pressable>
  )
}

/** Today's "From your players": after Results, before Coming up; the two best of the fan's Feed right now in its
 * cards, and the way to the rest (the Feed tab). Absent while their Feed is empty. Read once the follow list is
 * in, and again when it comes back changed, when Today is pulled (`again`) and when the app comes back to the
 * front, since Today stays open where the web's page would be loaded anew. */
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
  const go = useGoToPlace()
  if (!loaded || !page?.items.length) return null
  const all = () => go('/feed')
  return (
    <View style={{ gap: 10 }} accessibilityLabel="From your players">
      <SectionHead title="From your players" right={<Text onPress={all} accessibilityRole="link" style={[t.eyebrow, { color: colour.text, textDecorationLine: 'underline' }]}>All {page.count}</Text>} />
      <View style={{ gap: 12 }}>
        {page.items.map((e) => <FeedPost key={e.item.id} who={e.player} item={e.item} moment={e.moment} now={now} onMore={() => setOpened(e)} />)}
      </View>
      <Btn label={`All ${page.count} in your Feed`} onPress={all} style={{ height: 52 }} />
      <ItemSheet item={opened?.item ?? null} name={opened?.player.name ?? ''} onClose={() => setOpened(null)} onHide={(_, verdict) => (opened ? hide(opened, verdict) : Promise.resolve())} />
    </View>
  )
}

const RING_SEEN = '#C9C9C2'

const s = StyleSheet.create({
  face: { alignItems: 'center', justifyContent: 'center', overflow: 'hidden', flexShrink: 0 },
  post: { overflow: 'hidden', borderRadius: RADIUS_CARD, backgroundColor: colour.surface },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 12, paddingRight: 6, paddingBottom: 10, paddingLeft: 14 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 },
  name: { fontFamily: font[800], fontSize: 14, lineHeight: 17, color: colour.text },
  sub: { fontFamily: font[700], fontSize: 12, lineHeight: 15.6, color: colour.muted },
  tag: { height: 22, paddingHorizontal: 9, borderRadius: 999, justifyContent: 'center', flexShrink: 0, maxWidth: 140 },
  tagText: { fontFamily: font[800], fontSize: 10, letterSpacing: 0.9, textTransform: 'uppercase' },
  more: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  media: { width: '100%', aspectRatio: 16 / 9, overflow: 'hidden', alignItems: 'center', justifyContent: 'center' },
  play: {
    width: 58, height: 58, borderRadius: 29, backgroundColor: 'rgba(243,243,239,0.95)', alignItems: 'center', justifyContent: 'center',
    shadowColor: '#000000', shadowOpacity: 0.35, shadowRadius: 12, shadowOffset: { width: 0, height: 8 }, elevation: 8,
  },
  chip: {
    position: 'absolute', right: 10, bottom: 10, height: 22, paddingHorizontal: 8, borderRadius: 999, overflow: 'hidden',
    backgroundColor: 'rgba(13,13,13,0.78)', color: '#FFFFFF', fontFamily: font[800], fontSize: 11, lineHeight: 22, letterSpacing: 0.44,
  },
  block: { aspectRatio: 4 / 3, justifyContent: 'space-between', gap: 16, padding: 18 },
  quote: { paddingTop: 18, paddingHorizontal: 18, paddingBottom: 8, fontFamily: font[600], fontStyle: 'italic', fontSize: 18, lineHeight: 25, color: '#2A2B2E' },
  cap: { gap: 6, paddingTop: 12, paddingHorizontal: 14, paddingBottom: 4 },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 20, color: colour.text },
  acts: { flexDirection: 'row', alignItems: 'center', gap: 18, paddingTop: 4, paddingHorizontal: 14, paddingBottom: 12 },
  link: { marginLeft: 'auto', flexShrink: 1, fontFamily: font[800], fontSize: 13, lineHeight: 36, color: colour.text, textDecorationLine: 'underline', textAlign: 'right' },
  backdrop: { flex: 1, backgroundColor: 'rgba(13,13,13,0.4)' },
  sheet: { gap: 12, paddingTop: 18, paddingHorizontal: 18, borderTopLeftRadius: 26, borderTopRightRadius: 26, backgroundColor: colour.surface },
  reason: { fontFamily: font[600], fontSize: 15, lineHeight: 21, color: colour.text },
  say: { height: 48, justifyContent: 'flex-start' },
  sheetBtn: { height: 48 },
  circles: { marginHorizontal: -GUTTER, flexGrow: 0 },
  circlesRow: { gap: 14, paddingHorizontal: GUTTER, paddingVertical: 2 },
  circle: { width: 64, alignItems: 'center', gap: 6 },
  ring: { width: 62, height: 62, borderRadius: 31, alignItems: 'center', justifyContent: 'center' },
  all: { width: 56, height: 56, borderRadius: 28, borderWidth: 3, borderColor: colour.bg, backgroundColor: colour.text, alignItems: 'center', justifyContent: 'center' },
  nm: { fontFamily: font[700], fontSize: 11, lineHeight: 14, color: colour.text, maxWidth: 64 },
  nmOn: { fontFamily: font[800], textDecorationLine: 'underline' },
})

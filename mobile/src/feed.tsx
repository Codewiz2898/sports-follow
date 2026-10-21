// A player's Feed, the web's components/Feed.tsx: what fans, creators and the player share about them, judged
// first. Three homes: the Feed tab on the player's page, "From the fans" on its Overview, and "From your players"
// on Today (the top of the fan's own Feed, in the Feed tab's cards: post.tsx). A video is its thumbnail behind a play button, and a tap opens it where it plays (the video app when
// the phone has it, else the browser): nothing of a player loads before that. A post is a snippet and the way to
// where it was posted; news is a headline. Each card's "…" says why it's here and takes the fan's word on it,
// which hides it from them at once. What the Feed shows is decided in web/src/feed.ts; only the drawing is here.

import { Image } from 'expo-image'
import { router } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import Svg, { Circle, Path } from 'react-native-svg'
import { ago, duration, feedOf, fromTheFans, postFrom, views, without, type FeedKind } from '../../web/src/feed'
import { safeHref, shortName } from '../../web/src/format'
import { api, type Feed, type FeedGroup, type FeedItem, type FeedVerdict, type Photo } from './api'
import { matchHref } from './blocks'
import { useTone, type Tone } from './catalog'
import { FeedPost, ItemSheet } from './post'
import { colour, font, RADIUS_CARD, t } from './theme'
import { Btn, Card as Surface, ErrorBox, Eyebrow, Pill, SectionHead, Spinner } from './ui'

/** Dev only: EXPO_PUBLIC_FEED_SAMPLE=1 when Metro starts shows the web's made-up videos and posts
 * (web/src/dev/feedSample.ts). Written out in place so a release build, where __DEV__ is false, drops it. */
function sample(): typeof import('../../web/src/dev/feedSample') | null {
  return __DEV__ && process.env.EXPO_PUBLIC_FEED_SAMPLE === '1' ? require('../../web/src/dev/feedSample') : null
}

export type Hide = (item: FeedItem, verdict: FeedVerdict) => Promise<void>

/** The player's feed, again when their card's news changes (`news`: its stories' addresses), and a way to hide an item now. */
export function useFeed(playerId: number, name: string, news: string) {
  const [feed, setFeed] = useState<Feed | null>(null)
  const [error, setError] = useState<string | null>(null)
  const current = useRef(feed)
  current.current = feed

  useEffect(() => {
    if (!Number.isFinite(playerId) || !name) return // once the card is in: the feed's news is the card's
    let stale = false
    const made = sample()
    const load = made ? Promise.resolve(made.sampleFeed(playerId, name)) : api.feed(playerId)
    load.then((f) => { if (!stale) { setFeed(f); setError(null) } }).catch((e: Error) => { if (!stale) setError(e.message) })
    return () => { stale = true }
  }, [playerId, name, news])
  useEffect(() => { setFeed(null); setError(null) }, [playerId])

  // Gone from the page straight away; back, with why, if the server didn't take it.
  const hide = useCallback<Hide>(async (item, verdict) => {
    const before = current.current
    setFeed((f) => f && without(f, item.id))
    try {
      if (!sample()) await api.feedFeedback(playerId, item.id, verdict)
    } catch (e) {
      setFeed(before)
      throw e
    }
  }, [playerId])

  return { feed, error, hide }
}

const open = (url?: string | null) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

function Play({ size, color }: { size: number; color: string }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24" fill={color}><Path d="M8 5v14l11-7z" /></Svg>
}

/** The "…" that opens an item's sheet (.feed-more). */
function More({ onPress }: { onPress: () => void }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel="Why this is here, and feedback" hitSlop={6} style={({ pressed }) => [s.more, pressed && { backgroundColor: colour.bg }]}>
      <Svg width={20} height={20} viewBox="0 0 24 24" fill={colour.muted}><Circle cx="5" cy="12" r="2" /><Circle cx="12" cy="12" r="2" /><Circle cx="19" cy="12" r="2" /></Svg>
    </Pressable>
  )
}

/** The video's own thumbnail. Its stand-in for one it has none of (gone, or never was) is a grey 120×90, and an
 * image that fails is nothing: either way the card's ink shows instead. */
function Thumb({ id, size }: { id: string; size: 'hqdefault' | 'mqdefault' }) {
  const [shown, setShown] = useState(true)
  useEffect(() => setShown(true), [id])
  if (!shown) return null
  return (
    <Image
      source={{ uri: `https://i.ytimg.com/vi/${encodeURIComponent(id)}/${size}.jpg` }}
      style={StyleSheet.absoluteFill}
      contentFit="cover"
      onError={() => setShown(false)}
      onLoad={(e) => { if (e.source.width > 0 && e.source.width <= 120) setShown(false) }}
    />
  )
}

/** A tag in the sport's colour (.feed-tag). */
function Tag({ label, tone }: { label: string; tone: Tone }) {
  return <Pill small label={label} bg={tone.sport} color={tone.on} />
}

/** Whose a post is: the player's own in ink, a fan's in the sport's outline (.pill.ink, .feed-fan). */
function Whose({ item, name, tone }: { item: FeedItem; name: string; tone: Tone }) {
  return item.post?.own
    ? <Pill small label={`From ${shortName(name)}`} bg={colour.text} color={colour.bg} />
    : <View style={[s.fan, { borderColor: tone.sport }]}><Text style={[t.tag, { fontSize: 10, color: colour.text }]}>Fan</Text></View>
}

/** A video: its thumbnail and a play button; a tap opens it where it plays. */
export function VideoCard({ item, tone, now, onMore }: { item: FeedItem; tone: Tone; now: number; onMore?: () => void }) {
  const id = item.video?.youtube_id
  if (!id) return null
  const length = duration(item.video?.duration)
  const play = open(item.url)
  return (
    <Surface style={s.video}>
      <Pressable onPress={play} disabled={!play} accessibilityRole="button" accessibilityLabel={`Play: ${item.title}`} style={s.thumb}>
        <Thumb id={id} size="hqdefault" />
        <View style={s.play}><Play size={22} color={colour.text} /></View>
        {length ? <Text style={s.dur}>{length}</Text> : null}
      </Pressable>
      <View style={s.body}>
        <View style={{ gap: 6, flex: 1 }}>
          <Tag label={item.tag} tone={tone} />
          <Text style={[t.title, s.title]} numberOfLines={2}>{item.title}</Text>
          <Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>{[item.source, views(item.video?.views), ago(item.published_at, now)].filter(Boolean).join(' · ')}</Text>
        </View>
        {onMore ? <More onPress={onMore} /> : null}
      </View>
    </Surface>
  )
}

/** A post: whose it is, their words, and the way there. */
export function PostCard({ item, name, tone, now, onMore }: { item: FeedItem; name: string; tone: Tone; now: number; onMore?: () => void }) {
  const go = open(item.url)
  return (
    <Surface style={s.post}>
      <View style={s.row}>
        <Whose item={item} name={name} tone={tone} />
        <Text style={[t.tiny, t.muted, { fontFamily: font[700], flex: 1 }]} numberOfLines={1}>{postFrom(item)}</Text>
        {onMore ? <More onPress={onMore} /> : null}
      </View>
      <Text style={s.words} numberOfLines={3}>{item.title}</Text>
      <View style={[s.row, { justifyContent: 'space-between' }]}>
        {go ? <Btn kind="link" label={`Open on ${item.post?.platform ?? ''}`} onPress={go} /> : <View />}
        <Eyebrow>{ago(item.published_at, now)}</Eyebrow>
      </View>
    </Surface>
  )
}

/** A story: its headline, where it's from and when; it opens at its site. */
export function NewsCard({ item, tone, now, onMore }: { item: FeedItem; tone: Tone; now: number; onMore?: () => void }) {
  const go = open(item.url)
  return (
    <Surface style={s.news}>
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" style={{ gap: 6, flex: 1 }}>
        <Tag label={item.tag} tone={tone} />
        <Text style={[t.title, s.title]}>{item.title}</Text>
        <Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>{[item.source, ago(item.published_at, now)].filter(Boolean).join(' · ')}</Text>
        {item.summary ? <Text style={[t.small, t.muted]}>{item.summary}</Text> : null}
      </Pressable>
      {onMore ? <More onPress={onMore} /> : null}
    </Surface>
  )
}

function ItemCard({ item, name, tone, now, onMore }: { item: FeedItem; name: string; tone: Tone; now: number; onMore?: () => void }) {
  if (item.kind === 'video') return <VideoCard item={item} tone={tone} now={now} onMore={onMore} />
  if (item.kind === 'post') return <PostCard item={item} name={name} tone={tone} now={now} onMore={onMore} />
  return <NewsCard item={item} tone={tone} now={now} onMore={onMore} />
}

/** A kind to show (.feed-chip): ink outlined, filled when it's the one. */
function KindChip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: on }} style={[s.chip, on && { backgroundColor: colour.text }]}>
      <Text style={[s.chipText, on && { color: colour.bg }]}>{label}</Text>
    </Pressable>
  )
}

/** A group's head (.feed-head): a moment's dot, title and when, and the way to its game. */
function GroupHead({ group, tone, now }: { group: FeedGroup; tone: Tone; now: number }) {
  const match = group.match // the whole path, "/match/<source>/<game>"
  return (
    <View style={s.head}>
      {group.moment ? <View style={[s.dot, { backgroundColor: tone.sport }]} /> : null}
      <Eyebrow color={colour.text} style={{ flexShrink: 1 }}>{group.head}</Eyebrow>
      {group.moment && group.when ? <Eyebrow>· {ago(group.when, now)}</Eyebrow> : null}
      {match ? (
        <Pressable onPress={() => router.push(matchHref(match.replace(/^\/match\//, '')))} accessibilityRole="link" hitSlop={8} style={{ marginLeft: 'auto' }}>
          <Text style={[t.eyebrow, { color: colour.text, textDecorationLine: 'underline' }]}>The game</Text>
        </Pressable>
      ) : null}
    </View>
  )
}

/** The Feed tab: what kind (a filter, never a sort), the groups in the order things happened, the famous ones
 * last. On a phone a group is one column in its own order, as the site's is at a phone's width. Each item in the
 * Feed's cards (post.tsx), as on the fan's own Feed. */
export function FeedTab({ feed, error, name, sport, photo, now, onHide }: { feed: Feed | null; error: string | null; name: string; sport: string; photo?: Photo | null; now: number; onHide: Hide }) {
  const tone = useTone(sport)
  const [kind, setKind] = useState<FeedKind | null>(null)
  const [opened, setOpened] = useState<FeedItem | null>(null)
  if (!feed) {
    return error ? <ErrorBox>{error}</ErrorBox> : <View style={s.row}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View>
  }
  const { present, shown, groups, evergreen } = feedOf(feed, kind)
  const who = { id: feed.player_id, name, sport, photo: photo ?? null }

  return (
    <View style={{ gap: 16 }}>
      {/* Only the kinds there are: with news alone (before the judge's videos and posts), no chips at all. */}
      {present.length > 1 ? (
        <View style={s.chips}>
          <KindChip label="All" on={!shown} onPress={() => setKind(null)} />
          {present.map(([k, label]) => <KindChip key={k} label={label} on={shown === k} onPress={() => setKind(k)} />)}
        </View>
      ) : null}

      {groups.map((g) => (
        <View key={`${g.head}:${g.when ?? ''}`} style={{ gap: 10 }} accessibilityLabel={g.head}>
          <GroupHead group={g} tone={tone} now={now} />
          <View style={{ gap: 12 }}>
            {g.items.map((i) => <FeedPost key={i.id} who={who} item={i} now={now} more={false} onMore={() => setOpened(i)} />)}
          </View>
        </View>
      ))}

      {!groups.length && !evergreen.length ? (
        <Surface pad style={{ gap: 6 }}>
          <Text style={[t.title, { fontSize: 16 }]}>Nothing here this week</Text>
          <Text style={[t.small, t.muted]}>We only show what fans are actually watching and sharing about {shortName(name)}. Nothing sponsored, nothing in passing.</Text>
        </Surface>
      ) : null}

      {evergreen.length > 0 ? (
        <View style={{ gap: 10 }}>
          <SectionHead title="The ones everyone's seen" />
          <View style={s.evergreen}>
            {evergreen.map((i, n) => <Evergreen key={i.id} item={i} last={n === evergreen.length - 1} onMore={() => setOpened(i)} />)}
          </View>
        </View>
      ) : null}

      <Text style={[t.tiny, t.muted]}>Videos play in YouTube's player. Posts open where they were posted. Nothing is copied here.</Text>
      <ItemSheet item={opened} name={name} onClose={() => setOpened(null)} onHide={onHide} />
    </View>
  )
}

/** One of the famous ones: a small thumbnail and its title, opening where it lives. */
function Evergreen({ item, last, onMore }: { item: FeedItem; last: boolean; onMore: () => void }) {
  const id = item.video?.youtube_id
  const go = open(item.url)
  return (
    <View style={[s.ever, !last && s.everLine]}>
      <Pressable onPress={go} disabled={!go} accessibilityRole="link" style={[s.row, { flex: 1, gap: 12 }]}>
        <View style={s.everThumb}>
          {id ? <Thumb id={id} size="mqdefault" /> : null}
          <Play size={18} color={colour.bg} />
        </View>
        <View style={{ gap: 3, flex: 1 }}>
          <Text style={[t.title, { fontSize: 14, lineHeight: 18 }]} numberOfLines={2}>{item.title}</Text>
          <Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>{[item.source, views(item.video?.views)].filter(Boolean).join(' · ')}</Text>
        </View>
      </Pressable>
      <More onPress={onMore} />
    </View>
  )
}

/** Overview's "From the fans": the top video or post, a line of the player's own post, and the way to the rest. */
export function FromTheFans({ feed, name, sport, now, onAll }: { feed: Feed | null; name: string; sport: string; now: number; onAll: () => void }) {
  const tone = useTone(sport)
  const lead = fromTheFans(feed)
  if (!feed || !lead) return null
  const { top, line } = lead
  return (
    <View style={{ gap: 10 }}>
      <SectionHead title="From the fans" right={<Btn kind="link" label={`All · ${feed.count}`} onPress={onAll} />} />
      <ItemCard item={top} name={name} tone={tone} now={now} />
      {line ? (
        <Pressable onPress={onAll} accessibilityRole="button" style={[s.line, { backgroundColor: colour.surface }]}>
          <Whose item={line} name={name} tone={tone} />
          <Text style={[s.words, { flex: 1, fontSize: 13, lineHeight: 18 }]} numberOfLines={1}>{line.title}</Text>
          <Eyebrow>{ago(line.published_at, now, true)}</Eyebrow>
        </Pressable>
      ) : null}
    </View>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  title: { fontSize: 16, lineHeight: 21 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { height: 36, paddingHorizontal: 14, borderRadius: 999, borderWidth: 2, borderColor: colour.text, justifyContent: 'center' },
  chipText: { fontFamily: font[800], fontSize: 13, color: colour.text },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 36, paddingHorizontal: 4 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  video: { gap: 10, paddingTop: 10, paddingHorizontal: 10, paddingBottom: 14 },
  post: { gap: 10, paddingVertical: 16, paddingHorizontal: 18 },
  news: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingVertical: 14, paddingHorizontal: 18 },
  body: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, paddingHorizontal: 6 },
  fan: { height: 22, paddingHorizontal: 9, borderRadius: 999, borderWidth: 2, justifyContent: 'center', alignSelf: 'flex-start', flexShrink: 0 },
  words: { fontFamily: font[600], fontStyle: 'italic', fontSize: 16, lineHeight: 22.4, color: colour.muted },
  more: { width: 36, height: 36, marginTop: -6, marginRight: -8, borderRadius: 18, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  thumb: { aspectRatio: 16 / 9, borderRadius: 16, overflow: 'hidden', backgroundColor: colour.text, alignItems: 'center', justifyContent: 'center' },
  play: { width: 56, height: 56, borderRadius: 28, backgroundColor: colour.bg, alignItems: 'center', justifyContent: 'center', elevation: 8 },
  dur: { position: 'absolute', right: 10, bottom: 10, height: 22, paddingHorizontal: 8, borderRadius: 999, overflow: 'hidden', backgroundColor: 'rgba(13,13,13,0.8)', color: '#FFFFFF', fontFamily: font[800], fontSize: 11, lineHeight: 22, letterSpacing: 0.55 },
  evergreen: { paddingVertical: 4, paddingHorizontal: 16, borderRadius: RADIUS_CARD, backgroundColor: colour.surface },
  ever: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 12 },
  everLine: { borderBottomWidth: 1, borderBottomColor: colour.line },
  everThumb: { width: 96, height: 56, borderRadius: 10, overflow: 'hidden', backgroundColor: colour.text, alignItems: 'center', justifyContent: 'center' },
  line: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, paddingHorizontal: 18, borderRadius: RADIUS_CARD },
})

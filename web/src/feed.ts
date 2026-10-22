// What a player's Feed shows, worked out from the API's rows (sports_follow/feed.py): which kinds there are to
// pick, a feed without an item the fan hid, how each card says when, how long and how watched, and which items
// lead "From the fans" and Today's rail. Shared by the web app (components/Feed.tsx) and the Android app
// (mobile/src/feed.tsx), so it touches nothing of a browser's.

import { dateOnly, day, parseDate } from './format'
import type { Feed, FeedCircle, FeedEntry, FeedItem } from './types'

export type FeedKind = FeedItem['kind']
export const FEED_KINDS: [FeedKind, string][] = [['video', 'Videos'], ['post', 'Posts'], ['news', 'News']]

/** The feed without one item, for one the fan just hid: its group goes when it was the last there. */
export function without(feed: Feed, id: string): Feed {
  const groups = feed.groups.map((g) => ({ ...g, items: g.items.filter((i) => i.id !== id) })).filter((g) => g.items.length)
  const evergreen = feed.evergreen.filter((i) => i.id !== id)
  return { ...feed, groups, evergreen, count: groups.reduce((n, g) => n + g.items.length, 0) + evergreen.length }
}

/**
 * What the Feed tab shows with a kind picked (a filter, never a sort): the kinds there are to pick, the one
 * picked (none once its last item was hidden), and the groups and evergreen ones of that kind.
 */
export function feedOf(feed: Feed, kind: FeedKind | null) {
  const all = [...feed.groups.flatMap((g) => g.items), ...feed.evergreen]
  const present = FEED_KINDS.filter(([k]) => all.some((i) => i.kind === k))
  const shown = kind && present.some(([k]) => k === kind) ? kind : null
  const pick = (i: FeedItem) => !shown || i.kind === shown
  const groups = feed.groups.map((g) => ({ ...g, items: g.items.filter(pick) })).filter((g) => g.items.length)
  return { present, shown, groups, evergreen: feed.evergreen.filter(pick) }
}

/** "9h ago", "3 days ago", then the date; a story known only by its day, that day. `short`: "9h". */
export function ago(at: string | null, now: number, short = false): string {
  const d = parseDate(at)
  if (!d) return ''
  if (dateOnly(at)) return day(at)
  const mins = Math.max(1, Math.floor((now - d.getTime()) / 6e4))
  if (mins < 60) return short ? `${mins}m` : `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return short ? `${hours}h` : `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return short ? `${days}d` : days === 1 ? '1 day ago' : `${days} days ago`
  return day(at)
}

/** 1122 seconds is "18:42"; an hour or more "1:02:03". */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return ''
  const s = Math.round(seconds)
  const [h, m, r] = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60]
  const two = (n: number) => String(n).padStart(2, '0')
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`
}

/** Whether this device writes numbers short ("41K"): Hermes, the Android app's engine, may not. */
const COMPACT = (() => {
  try {
    return new Intl.NumberFormat(undefined, { notation: 'compact' }).resolvedOptions().notation === 'compact'
  } catch {
    return false
  }
})()

/** 41000 is "41K", in the reader's own way of writing it where their device knows it. */
function compact(n: number): string {
  if (COMPACT) return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
  const units: [number, string][] = [[1e3, 'K'], [1e6, 'M'], [1e9, 'B']]
  const round = (x: number) => Number(x.toFixed(x >= 100 ? 0 : 1))
  let i = units.filter(([size]) => n >= size).length - 1
  if (i < 0) return String(n)
  if (round(n / units[i][0]) >= 1000 && i < units.length - 1) i += 1  // 999,999 is "1M", not "1000K"
  return `${round(n / units[i][0])}${units[i][1]}`
}

/** 41000 views is "41K views". */
export function views(n: number | null | undefined): string {
  if (n == null) return ''
  return `${compact(n)} ${n === 1 ? 'view' : 'views'}`
}

/** Where a post is from: the player's own "On X", a fan's "@fan on X". */
export function postFrom(item: FeedItem): string {
  const where = item.post?.platform ?? ''
  return item.post?.own ? `On ${where}` : `${item.source} on ${where}`
}

/** The videos and posts, the feed's top first: news never leads "From the fans" or Today's rail (the Wire has news). */
export function shares(feed: Feed | null): FeedItem[] {
  return feed ? feed.groups.flatMap((g) => g.items).filter((i) => i.kind !== 'news') : []
}

/** "From the fans": the top video or post, and under a video, a line of the player's own post (else a fan's). */
export function fromTheFans(feed: Feed | null): { top: FeedItem; line?: FeedItem } | null {
  const items = shares(feed)
  if (!items.length) return null
  const [top] = items
  const line = top.kind === 'video' ? items.find((i) => i.kind === 'post' && i.post?.own) ?? items.find((i) => i.kind === 'post') : undefined
  return { top, line }
}

/** "4 outlets", for a story more than one outlet ran; nothing otherwise. */
export function outlets(n?: number | null): string {
  return n && n > 1 ? `${n} outlets` : ''
}

// ---------------------------------------------------------------- the fan's own Feed (GET /api/me/feed/items)

/** The fan's Feed's entries as their day groups, in order: one group for each run of entries of one day. */
export function byDay(entries: FeedEntry[]): { day: string; entries: FeedEntry[] }[] {
  const out: { day: string; entries: FeedEntry[] }[] = []
  for (const e of entries) {
    const last = out.at(-1)
    if (last && last.day === e.day) last.entries.push(e)
    else out.push({ day: e.day, entries: [e] })
  }
  return out
}

/** How long something new keeps a player's circle ringed while the fan hasn't looked at them. */
export const RING_FOR = 2 * 864e5

/** Whether a player's circle is ringed in their sport's colour: something of theirs came in after the fan last
 * looked at them (`seen`, when they tapped the circle), and within RING_FOR. */
export function ringed(circle: FeedCircle, seen: string | null | undefined, now: number): boolean {
  const newest = parseDate(circle.newest)?.getTime()
  if (newest == null || now - newest > RING_FOR) return false
  const looked = parseDate(seen ?? null)?.getTime()
  return looked == null || newest > looked
}

/** Whether the Feed tab has its dot: something came in after the fan last opened it (or ever, before they first did). */
export function hasNew(newest: string | null | undefined, opened: string | null | undefined): boolean {
  const at = parseDate(newest ?? null)?.getTime()
  if (at == null) return false
  const looked = parseDate(opened ?? null)?.getTime()
  return looked == null || at > looked
}

/** A card's head line: where it's from and when ("Cover Drive Edits · 3h"), a post's account where it was posted. */
export function fromLine(item: FeedItem, now: number): string {
  const from = item.kind === 'post' ? postFrom(item) : [item.source, outlets(item.outlets)].filter(Boolean).join(' · ')
  return [from, ago(item.published_at, now, true)].filter(Boolean).join(' · ')
}

/** Feed v2's line under a post (FeedLayouts.tsx, the app's feedLayouts.tsx): "channel · 81K views · 2d", where a video
 * is from, how watched and when, a story's outlet and when; `withLength` puts the video's length after the channel
 * (full screen, where the picture has no chip). */
export function sourceLine(item: FeedItem, now: number, withLength = false): string {
  const length = withLength ? duration(item.video?.duration) : ''
  return [item.source, length, views(item.video?.views), ago(item.published_at, now, true)].filter(Boolean).join(' · ')
}

/** The channel a video's from or the outlet a story with a picture is from, as the card's header names it
 * (BHA-358). None for a post (its source is a person, named in its own line) or a story without a picture (its
 * outlet heads the headline drawn in the picture's place). */
export function channelOf(item: FeedItem): string | null {
  const named = item.kind === 'video' || (item.kind === 'news' && Boolean(item.picture))
  return named && item.source ? item.source : null
}

/** sourceLine without the channel, for a card whose header names it: "81K views · 2d", or with the video's length
 * first ("4:12 · 81K views · 2d"). */
export function statsLine(item: FeedItem, now: number, withLength = false): string {
  const length = withLength ? duration(item.video?.duration) : ''
  return [length, views(item.video?.views), ago(item.published_at, now, true)].filter(Boolean).join(' · ')
}

/** YouTube's no-cookie player for a Feed video, playing at once: the web's in the picture's place, the app's inline in
 * a WebView on the site's address. `muted` for one that starts by itself without the fan's sound on (autoplay; a
 * browser lets only a muted one start before the page is tapped). `origin`: the page's own, for one YouTube's IFrame
 * Player API drives (its errors: refusedHere; its sound turned on and off where it plays, BHA-381). `bare`: without
 * YouTube's controls (controls=0), for a Short playing as Shorts do (BHA-384): a tap on it pauses, the app's sound
 * button beside it is its sound. (BHA-354: the server will say how to play each video.) */
export function youtubeEmbed(id: string, muted = false, origin?: string, bare = false): string {
  const api = origin ? `&enablejsapi=1&origin=${encodeURIComponent(origin)}` : ''
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0&playsinline=1${muted ? '&mute=1' : ''}${bare ? '&controls=0' : ''}${api}`
}

/** Where a Short goes on a phone-shaped screen `width` × `height` (BHA-384), as the web's .reel.short lays it out: its
 * 9:16 frame from `top` down, as wide as the screen allows once `foot` (what's below it: who and what, and the tab bar
 * when there is one) is left clear; full width on a phone taller than that needs, narrower and centred on a shorter one.
 * Nothing of the app's goes over it while it plays (YouTube's rules): who and what sit in the strip under it. */
export function shortFrame(width: number, height: number, top: number, foot: number): { left: number; top: number; width: number; height: number } {
  const tall = Math.max(0, Math.min((width * 16) / 9, height - top - foot))
  const wide = (tall * 9) / 16
  return { left: (width - wide) / 2, top, width: wide, height: tall }
}

/** Whether an error of YouTube's IFrame Player API means the video won't play in any player of ours (BHA-380), so the
 * app says so, offers YouTube, and tells the server (feedUnplayable), which stops serving it to the fan, and to every
 * fan once enough networks have said so: 100, gone or private;
 * 101 and 150, its owner keeps it off other sites (a Content ID claim's, which YouTube's Data API doesn't say). */
export const refusedHere = (code: unknown): boolean => code === 100 || code === 101 || code === 150

/** Whether an item is a Short as the Feed plays one (BHA-384): a video YouTube can play here, taller than it's wide. */
export const isShort = (item?: FeedItem | null): boolean => Boolean(item && item.kind === 'video' && item.video?.youtube_id && upright(item))

/** Whether a video is taller than it's wide (a Short): framed and played upright. */
export const upright = (item: FeedItem): boolean => item.kind === 'video' && item.video?.upright === true

/** What a card's link at the bottom says: a story "Read at <outlet>", a post "Open on <platform>", a video "Watch at <channel>". */
export function openLabel(item: FeedItem): string {
  if (item.kind === 'post') return `Open on ${item.post?.platform ?? 'the post'}`
  if (item.kind === 'video') return item.source ? `Watch at ${item.source}` : 'Watch where it was posted'
  return item.source ? `Read at ${item.source}` : 'Read the story'
}

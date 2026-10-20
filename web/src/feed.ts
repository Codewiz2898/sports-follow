// What a player's Feed shows, worked out from the API's rows (sports_follow/feed.py): which kinds there are to
// pick, a feed without an item the fan hid, how each card says when, how long and how watched, and which items
// lead "From the fans" and Today's rail. Shared by the web app (components/Feed.tsx) and the Android app
// (mobile/src/feed.tsx), so it touches nothing of a browser's.

import { dateOnly, day, parseDate } from './format'
import type { Feed, FeedItem } from './types'

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

/** 41000 is "41K", in the reader's own way of writing it where their device knows it. */
function compact(n: number): string {
  try {
    const short = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
    if (n < 1000 || !/^[\d,.\s ]+$/.test(short)) return short  // all digits: compact isn't known here
  } catch { /* no compact notation here */ }
  const [size, mark] = n >= 1e9 ? [1e9, 'B'] : n >= 1e6 ? [1e6, 'M'] : n >= 1e3 ? [1e3, 'K'] : [1, '']
  return `${Number((n / size).toFixed(n / size >= 100 ? 0 : 1))}${mark}`
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

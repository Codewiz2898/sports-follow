import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api, type Card, type Feed, type FeedItem, type FeedShare, type FeedVerdict } from '../api'
import { dateOnly, day, parseDate, safeHref, shortName } from '../format'
import { useFollowing } from '../following'
import { sportKey } from '../sports'
import { SectionHead } from './Blocks'
import { Rail } from './Rail'

/**
 * A player's Feed (sports_follow/feed.py): what fans, creators and the player share about them, judged first.
 * Three homes: the Feed tab on the player's page, "From the fans" on its Overview, and "From your players" on
 * Today. A video is YouTube's thumbnail behind a play button, and nothing of YouTube's player loads until it's
 * tapped; a post is a snippet and a link to where it was posted; news is a headline. Each card's "…" says why
 * it's here and takes the fan's word on it, which hides it from them at once.
 */

type Kind = FeedItem['kind']
const KINDS: [Kind, string][] = [['video', 'Videos'], ['post', 'Posts'], ['news', 'News']]

/** Dev only: ?feed-sample on a player's page or Today shows made-up videos and posts (dev/feedSample.ts). */
const SAMPLE = import.meta.env.DEV && typeof location !== 'undefined' && new URLSearchParams(location.search).has('feed-sample')

/** The player's feed, again when their card's news changes (`news`: its stories' addresses), and a way to hide an item now. */
export function useFeed(playerId: number, name: string, news: string) {
  const [feed, setFeed] = useState<Feed | null>(null)
  const [error, setError] = useState<string | null>(null)
  const current = useRef(feed)
  current.current = feed

  useEffect(() => {
    if (!Number.isFinite(playerId) || !name) return  // once the card is in: the feed's news is the card's
    let stale = false
    const load = SAMPLE ? import('../dev/feedSample').then((m) => m.sampleFeed(playerId, name)) : api.feed(playerId)
    load.then((f) => { if (!stale) { setFeed(f); setError(null) } }).catch((e: Error) => { if (!stale) setError(e.message) })
    return () => { stale = true }
  }, [playerId, name, news])
  useEffect(() => { setFeed(null); setError(null) }, [playerId])

  // Gone from the page straight away; back, with why, if the server didn't take it.
  const hide = useCallback(async (item: FeedItem, verdict: FeedVerdict) => {
    const before = current.current
    setFeed((f) => f && without(f, item.id))
    try {
      if (!SAMPLE) await api.feedFeedback(playerId, item.id, verdict)
    } catch (e) {
      setFeed(before)
      throw e
    }
  }, [playerId])

  return { feed, error, hide }
}

function without(feed: Feed, id: string): Feed {
  const groups = feed.groups.map((g) => ({ ...g, items: g.items.filter((i) => i.id !== id) })).filter((g) => g.items.length)
  const evergreen = feed.evergreen.filter((i) => i.id !== id)
  return { ...feed, groups, evergreen, count: groups.reduce((n, g) => n + g.items.length, 0) + evergreen.length }
}

/** "9h ago", "3 days ago", then the date; a story known only by its day, that day. */
function ago(at: string | null, now: number, short = false): string {
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
function duration(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return ''
  const s = Math.round(seconds)
  const [h, m, r] = [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60]
  const two = (n: number) => String(n).padStart(2, '0')
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`
}

/** 41000 views is "41K views". */
function views(n: number | null | undefined): string {
  if (n == null) return ''
  const short = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
  return `${short} ${n === 1 ? 'view' : 'views'}`
}

/** YouTube's own thumbnail of a video. Its stand-in for one it has none for (gone, or never was) is a grey
 * 120×90, and an image that fails is nothing: either way the card's ink shows instead. */
function Thumb({ id, size }: { id: string; size: 'hqdefault' | 'mqdefault' }) {
  const [shown, setShown] = useState(true)
  const check = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth > 0 && img.naturalWidth <= 120) setShown(false) }
  if (!shown) return null
  return <img ref={check} src={`https://i.ytimg.com/vi/${encodeURIComponent(id)}/${size}.jpg`} alt="" loading="lazy" referrerPolicy="no-referrer"
    onError={() => setShown(false)} onLoad={(e) => check(e.currentTarget)} />
}

const dots = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>
)
const play = (size: number) => <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>

function More({ onClick }: { onClick: () => void }) {
  return <button type="button" className="feed-more" aria-label="Why this is here, and feedback" onClick={onClick}>{dots}</button>
}

/** A video: YouTube's thumbnail and a play button; a tap swaps in YouTube's no-cookie player, playing. */
export function VideoCard({ item, now, onMore }: { item: FeedItem; now: number; onMore?: () => void }) {
  const [playing, setPlaying] = useState(false)
  const id = item.video?.youtube_id
  if (!id) return null
  const length = duration(item.video?.duration)
  return (
    <article className="card feed-card video">
      <div className="feed-thumb">
        {playing ? (
          <iframe src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0&playsinline=1`} title={item.title}
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
        ) : (
          <>
            <Thumb id={id} size="hqdefault" />
            <button type="button" className="feed-play" aria-label={`Play: ${item.title}`} onClick={() => setPlaying(true)}>{play(22)}</button>
            {length && <span className="feed-dur">{length}</span>}
          </>
        )}
      </div>
      <div className="feed-body">
        <div className="stack" style={{ gap: 6, flex: 1 }}>
          <span><span className="pill feed-tag">{item.tag}</span></span>
          <span className="title clamp2" style={{ fontSize: 16 }}>{item.title}</span>
          <span className="tiny muted" style={{ fontWeight: 700 }}>{[item.source, views(item.video?.views), ago(item.published_at, now)].filter(Boolean).join(' · ')}</span>
        </div>
        {onMore && <More onClick={onMore} />}
      </div>
    </article>
  )
}

/** A post: whose it is (the player's own in ink, a fan's in the sport's outline), their words, and the way there. */
export function PostCard({ item, name, now, onMore }: { item: FeedItem; name: string; now: number; onMore?: () => void }) {
  const own = item.post?.own ?? false
  const where = item.post?.platform ?? ''
  const href = safeHref(item.url)
  return (
    <article className="card feed-card post">
      <div className="row" style={{ gap: 10, justifyContent: 'flex-start' }}>
        {own ? <span className="pill ink">From {shortName(name)}</span> : <span className="pill feed-fan">Fan</span>}
        <span className="tiny muted ellipsis" style={{ fontWeight: 700, flex: 1 }}>{own ? `On ${where}` : `${item.source} on ${where}`}</span>
        {onMore && <More onClick={onMore} />}
      </div>
      <p className="feed-words clamp3">{item.title}</p>
      <div className="row">
        {href ? <a href={href} target="_blank" rel="noreferrer" className="btn link">Open on {where}</a> : <span />}
        <span className="eyebrow">{ago(item.published_at, now)}</span>
      </div>
    </article>
  )
}

/** A story: its headline, where it's from and when; it opens at its site. */
export function NewsCard({ item, now, onMore }: { item: FeedItem; now: number; onMore?: () => void }) {
  return (
    <article className="card feed-card news">
      <a href={safeHref(item.url)} target="_blank" rel="noreferrer" className="stack" style={{ gap: 6, flex: 1 }}>
        <span><span className="pill feed-tag">{item.tag}</span></span>
        <span className="title" style={{ fontSize: 16 }}>{item.title}</span>
        <span className="tiny muted" style={{ fontWeight: 700 }}>{[item.source, ago(item.published_at, now)].filter(Boolean).join(' · ')}</span>
        {item.summary && <span className="small muted">{item.summary}</span>}
      </a>
      {onMore && <More onClick={onMore} />}
    </article>
  )
}

function ItemCard({ item, name, now, onMore }: { item: FeedItem; name: string; now: number; onMore?: () => void }) {
  if (item.kind === 'video') return <VideoCard item={item} now={now} onMore={onMore} />
  if (item.kind === 'post') return <PostCard item={item} name={name} now={now} onMore={onMore} />
  return <NewsCard item={item} now={now} onMore={onMore} />
}

/** The "…" sheet: why the item is here, and the three things a fan can tell the judge about it. */
function ItemSheet({ item, name, onClose, onHide }: { item: FeedItem; name: string; onClose: () => void; onHide: (item: FeedItem, verdict: FeedVerdict) => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const d = dialog.current
    if (d && !d.open) d.showModal()
  }, [])
  const say = async (verdict: FeedVerdict) => {
    setBusy(true)
    setError(null)
    try {
      await onHide(item, verdict)
      onClose()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }
  return (
    // A tap on the backdrop (the dialog itself, outside its box) closes it, as Escape does.
    <dialog ref={dialog} className="feed-sheet" aria-label="About this item" onClose={onClose} onClick={(e) => { if (e.target === dialog.current) dialog.current?.close() }}>
      <div className="feed-sheet-box">
        <span className="eyebrow">Why this is here</span>
        <p style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.4 }}>{item.reason}</p>
        {error && <span className="small" role="alert" style={{ color: 'var(--danger)' }}>{error}</span>}
        <div className="stack" style={{ gap: 8, marginTop: 4 }}>
          <button type="button" className="btn feed-say" disabled={busy} onClick={() => say('not_interested')}>Not interested</button>
          <button type="button" className="btn feed-say" disabled={busy} onClick={() => say('not_about_player')}>Not about {shortName(name)}</button>
          <button type="button" className="btn feed-say" disabled={busy} onClick={() => say('report')}>Report</button>
          <button type="button" className="btn primary" onClick={() => dialog.current?.close()}>Close</button>
        </div>
      </div>
    </dialog>
  )
}

/** The Feed tab: what kind (a filter, never a sort), the groups in the order things happened, the famous ones last. */
export function FeedTab({ feed, error, name, sport, now, onHide }: { feed: Feed | null; error: string | null; name: string; sport: string; now: number; onHide: (item: FeedItem, verdict: FeedVerdict) => Promise<void> }) {
  const [kind, setKind] = useState<Kind | null>(null)
  const [open, setOpen] = useState<FeedItem | null>(null)
  if (!feed) return error ? <div className="error-box">{error}</div> : <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>

  const all = [...feed.groups.flatMap((g) => g.items), ...feed.evergreen]
  const present = KINDS.filter(([k]) => all.some((i) => i.kind === k))
  const shown = kind && present.some(([k]) => k === kind) ? kind : null  // a kind whose last item was hidden
  const pick = (i: FeedItem) => !shown || i.kind === shown
  const groups = feed.groups.map((g) => ({ ...g, items: g.items.filter(pick) })).filter((g) => g.items.length)
  const evergreen = feed.evergreen.filter(pick)

  return (
    <div className="section feed" data-sport={sportKey(sport)}>
      {/* Only the kinds there are: with news alone (before the judge's videos and posts), no chips at all. */}
      {present.length > 1 && (
        <div className="feed-chips" role="group" aria-label="Show">
          <button type="button" className="feed-chip" aria-pressed={!shown} onClick={() => setKind(null)}>All</button>
          {present.map(([k, label]) => <button key={k} type="button" className="feed-chip" aria-pressed={shown === k} onClick={() => setKind(k)}>{label}</button>)}
        </div>
      )}

      {groups.map((g) => {
        // Videos on the left, the rest beside them on a desktop; on a phone, one column in the group's order.
        const order = new Map(g.items.map((i, n) => [i.id, n]))
        const card = (i: FeedItem) => (
          <div key={i.id} style={{ order: order.get(i.id) } as CSSProperties}>
            <ItemCard item={i} name={name} now={now} onMore={() => setOpen(i)} />
          </div>
        )
        const videos = g.items.filter((i) => i.kind === 'video')
        const rest = g.items.filter((i) => i.kind !== 'video')
        return (
          <section key={`${g.head}:${g.when ?? ''}`} className="feed-group" aria-label={g.head}>
            <div className="feed-head">
              {g.moment && <span className="feed-dot" aria-hidden="true" />}
              <span className="eyebrow" style={{ color: 'var(--text)' }}>{g.head}</span>
              {g.moment && g.when && <span className="eyebrow">· {ago(g.when, now)}</span>}
              {g.match && <Link to={g.match} className="eyebrow feed-game">The game</Link>}
            </div>
            {videos.length && rest.length ? (
              <div className="feed-cols"><div className="feed-col">{videos.map(card)}</div><div className="feed-col">{rest.map(card)}</div></div>
            ) : (
              <div className="feed-grid">{g.items.map(card)}</div>
            )}
          </section>
        )
      })}

      {!groups.length && !evergreen.length && (
        <div className="card card-pad stack" style={{ gap: 6 }}>
          <span className="title" style={{ fontSize: 16 }}>Nothing here this week</span>
          <span className="small muted">We only show what fans are actually watching and sharing about {shortName(name)}. Nothing sponsored, nothing in passing.</span>
        </div>
      )}

      {evergreen.length > 0 && (
        <section className="section" aria-label="The ones everyone's seen">
          <SectionHead title="The ones everyone's seen" />
          <div className="feed-evergreen">
            {evergreen.map((i) => <Evergreen key={i.id} item={i} onMore={() => setOpen(i)} />)}
          </div>
        </section>
      )}

      <p className="tiny muted">Videos play in YouTube's player. Posts open where they were posted. Nothing is copied here.</p>
      {open && <ItemSheet item={open} name={name} onClose={() => setOpen(null)} onHide={onHide} />}
    </div>
  )
}

/** One of the famous ones: a small thumbnail and its title, opening where it lives. */
function Evergreen({ item, onMore }: { item: FeedItem; onMore: () => void }) {
  const id = item.video?.youtube_id
  return (
    <div className="feed-ever">
      <a href={safeHref(item.url)} target="_blank" rel="noreferrer" className="row" style={{ gap: 12, flex: 1, justifyContent: 'flex-start' }}>
        <span className="feed-ever-thumb">
          {id && <Thumb id={id} size="mqdefault" />}
          {play(18)}
        </span>
        <span className="stack" style={{ gap: 3, flex: 1 }}>
          <span className="title clamp2" style={{ fontSize: 14 }}>{item.title}</span>
          <span className="tiny muted" style={{ fontWeight: 700 }}>{[item.source, views(item.video?.views)].filter(Boolean).join(' · ')}</span>
        </span>
      </a>
      <More onClick={onMore} />
    </div>
  )
}

/** The first video or post, the feed's top: news never leads "From the fans" or Today's rail (the Wire has news). */
function shares(feed: Feed | null): FeedItem[] {
  return feed ? feed.groups.flatMap((g) => g.items).filter((i) => i.kind !== 'news') : []
}

/** Overview's "From the fans": the top video or post, a line of the player's own post, and the way to the rest. */
export function FromTheFans({ feed, name, sport, now, onAll }: { feed: Feed | null; name: string; sport: string; now: number; onAll: () => void }) {
  const items = shares(feed)
  if (!feed || !items.length) return null
  const [top] = items
  const line = top.kind === 'video' ? items.find((i) => i.kind === 'post' && i.post?.own) ?? items.find((i) => i.kind === 'post') : undefined
  return (
    <section className="section" aria-label="From the fans" data-sport={sportKey(sport)}>
      <SectionHead title="From the fans">
        <button type="button" className="btn link" onClick={onAll}>All · {feed.count}</button>
      </SectionHead>
      {/* Beside each other on a desktop, as the Feed's columns are. */}
      <div className="feed-grid">
        <ItemCard item={top} name={name} now={now} />
        {line && (
          <button type="button" className="card feed-line" onClick={onAll}>
            {line.post?.own ? <span className="pill ink">From {shortName(name)}</span> : <span className="pill feed-fan">Fan</span>}
            <span className="small ellipsis feed-words" style={{ flex: 1, fontSize: 13 }}>{line.title}</span>
            <span className="eyebrow">{ago(line.published_at, now, true)}</span>
          </button>
        )}
      </div>
    </section>
  )
}

/** Today's shares (GET /api/me/feed), loaded with the follow list, each with its player's card. */
export function useShares(): { card: Card; item: FeedItem }[] {
  const { cards, loaded } = useFollowing()
  const [rows, setRows] = useState<FeedShare[]>([])
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  useEffect(() => {
    if (!loaded) return
    let stale = false
    const load = SAMPLE ? import('../dev/feedSample').then((m) => m.sampleShares(cards)) : api.myFeed().then((r) => r.shares)
    load.then((r) => { if (!stale) setRows(r) }).catch(() => {})
    return () => { stale = true }
  }, [loaded, who])  // who stands for cards
  return useMemo(() => {
    const byId = new Map(cards.map((c) => [c.player_id, c]))
    return rows.flatMap((r) => { const card = byId.get(r.player_id); return card ? [{ card, item: r.item }] : [] })
  }, [rows, cards])
}

/** Today's "From your players": after Results, before Coming up; a poster per player, in their sport's colour. */
export function FromYourPlayers({ shares: rows, now }: { shares: { card: Card; item: FeedItem }[]; now: number }) {
  if (!rows.length) return null
  return (
    <Rail title="From your players" note={`Today · ${rows.length}`}>
      {rows.map(({ card, item }) => {
        const name = shortName(card.player.name)
        const own = item.kind === 'post' && item.post?.own
        const where = item.post?.platform ?? ''
        const from = item.kind === 'post' ? (own ? `On ${where}` : `${item.source} on ${where}`) : item.source
        return (
          <Link key={`${card.player_id}:${item.id}`} to={`/player/${card.player_id}?tab=feed`} className="feed-share" data-sport={sportKey(card.player.sport)}>
            {item.kind === 'video' && <span className="feed-share-play" aria-hidden="true">{play(16)}</span>}
            <span className="poster-tag">{own ? `From ${name}` : item.tag}</span>
            <span className="stack" style={{ gap: 6 }}>
              <span className="dsp who">{name}</span>
              <span className={`line${item.kind === 'post' ? ' said' : ''}`}>{item.title}</span>
              <span className="from">{[from, ago(item.published_at, now, true)].filter(Boolean).join(' · ')}</span>
            </span>
          </Link>
        )
      })}
    </Rail>
  )
}

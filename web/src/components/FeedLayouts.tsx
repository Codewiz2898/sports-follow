import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { FeedEntry, FeedItem, FeedTabKey } from '../api'
import { ago, duration, views } from '../feed'
import { safeHref, shortName } from '../format'
import { shareFeedItem } from '../share'
import { sportKey } from '../sports'
import { Face } from './FeedPost'
import { ShareButton } from './Share'

/**
 * The fan's Feed in the two layouts the server picks between (BHA-349, GET /api/me/feed/items' `layout`, which the
 * admin panel changes live): edge to edge (the S2Edge board), full-width posts down the page; and full screen (the
 * S5Reels board), one video a screen, snapping from one to the next. In both a video is YouTube's biggest thumbnail
 * until play is tapped, and only then does YouTube's no-cookie player load, in its place. Items of players the fan
 * doesn't follow (For you's) offer Follow, which works where it is. "…" (More) opens "Why this is here".
 */

const dots = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>
)
const playIcon = <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>
const plusIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
)
const tickIcon = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
)

/** For you and Following: which of the fan's Feed shows. */
export function FeedTabs({ tab, onTab, className }: { tab: FeedTabKey; onTab: (tab: FeedTabKey) => void; className: string }) {
  return (
    <div className={className} role="group" aria-label="Whose">
      <button type="button" aria-pressed={tab === 'for_you'} onClick={() => onTab('for_you')}>For you</button>
      <button type="button" aria-pressed={tab === 'following'} onClick={() => onTab('following')}>Following</button>
    </div>
  )
}

/** What a follow button needs: whether the fan follows the entry's player now, and the way to change that. */
export interface Follows { following: (entry: FeedEntry) => boolean; toggle: (entry: FeedEntry) => void; busy: (entry: FeedEntry) => boolean }

/** "channel · 81K views · 2d": where a video is from, how watched and when; a story's outlet and when. */
function sourceLine(item: FeedItem, now: number, withLength = false): string {
  const length = withLength ? duration(item.video?.duration) : ''
  return [item.source, length, views(item.video?.views), ago(item.published_at, now, true)].filter(Boolean).join(' · ')
}

/** A still as its host serves it, lazily; nothing once it fails, or for YouTube's grey 120×90 stand-in for none. */
function Still({ src, className, onGone }: { src: string; className?: string; onGone: () => void }) {
  const check = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth > 0 && img.naturalWidth <= 120) onGone() }
  return <img ref={check} className={className} src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={onGone} onLoad={(e) => check(e.currentTarget)} />
}

/** YouTube's no-cookie player, playing, in the picture's place. */
function Player({ id, title }: { id: string; title: string }) {
  return (
    <iframe src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0&playsinline=1`} title={title}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
  )
}

// ---------------------------------------------------------------- edge to edge

/** One post edge to edge: who it's about (photo ringed in their sport's colour, name, Follow when the fan doesn't,
 * "…"), the full-width 16:9 picture with the video's length, the title, and "channel · views · when". */
export function EdgePost({ entry, now, follows, playing, onPlay, onMore }: {
  entry: FeedEntry; now: number; follows?: Follows; playing: boolean; onPlay: (id: string | null) => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const [noPicture, setNoPicture] = useState(false)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const href = safeHref(item.url)
  const length = duration(item.video?.duration)
  const mine = follows ? follows.following(entry) : true

  let media: ReactNode = null
  if (item.kind === 'video' && id) {
    media = (
      <div className="epost-pic">
        {playing ? <Player id={id} title={item.title} /> : (
          <button type="button" className="epost-play" aria-label={`Play: ${item.title}`} onClick={() => onPlay(item.id)}>
            {picture && <Still src={picture} onGone={() => setNoPicture(true)} />}
            {length && <span className="epost-dur">{length}</span>}
          </button>
        )}
      </div>
    )
  } else if (item.kind === 'news') {
    media = picture
      ? <a className="epost-pic" href={href} target="_blank" rel="noreferrer" tabIndex={-1} aria-hidden="true"><Still src={picture} onGone={() => setNoPicture(true)} /></a>
      : <a className="epost-block" href={href} target="_blank" rel="noreferrer"><span className="eyebrow">{item.source}</span><span className="dsp">{item.title}</span></a>
  } else if (item.kind === 'post') {
    media = <a className="epost-quote clamp3" href={href} target="_blank" rel="noreferrer">“{item.title}”</a>
  }
  if (!media) return null

  return (
    <article className="epost" data-sport={sportKey(who.sport)}>
      <div className="epost-head">
        <Link to={`/player/${who.id}`} className="epost-who">
          <span className="epost-ring"><Face name={who.name} sport={who.sport} photo={who.photo} size={34} /></span>
          <span className="epost-name ellipsis">{who.name}</span>
        </Link>
        {follows && (!entry.following || !mine) && (
          <>
            <span className="muted" aria-hidden="true">·</span>
            <button type="button" className="epost-follow" aria-pressed={mine} disabled={follows.busy(entry)} onClick={() => follows.toggle(entry)}>
              {mine ? 'Following' : 'Follow'}
            </button>
          </>
        )}
        <button type="button" className="epost-more" aria-label="More about this item" onClick={onMore}>{dots}</button>
      </div>
      {media}
      <div className="epost-cap">
        {(item.kind === 'video' || (item.kind === 'news' && picture)) && <span className="epost-title clamp3">{item.title}</span>}
        <span className="epost-src">{item.kind === 'post' ? `${item.source} on ${item.post?.platform ?? ''}` : sourceLine(item, now)}{entry.moment ? ` · after ${entry.moment}` : ''}</span>
      </div>
    </article>
  )
}

// ---------------------------------------------------------------- full screen

/** The full-screen Feed: one item a screen, snapping; For you and Following at the top. The next page loads as the
 * last few come up (onNear), and what plays stops once it's scrolled away. */
export function Reels({ entries, now, tab, onTab, follows, onMore, onNear, footer }: {
  entries: FeedEntry[]; now: number; tab: FeedTabKey; onTab: (tab: FeedTabKey) => void; follows: Follows
  onMore: (entry: FeedEntry) => void; onNear: () => void; footer?: ReactNode
}) {
  const box = useRef<HTMLDivElement>(null)
  const [shown, setShown] = useState(0)
  const [playing, setPlaying] = useState<string | null>(null)
  const near = useRef(onNear)
  near.current = onNear

  // The tab bar turns paper over the dark, and the page under the reels stays still.
  useEffect(() => {
    document.documentElement.dataset.reels = ''
    return () => { delete document.documentElement.dataset.reels }
  }, [])
  // Which one is on screen: what plays stops once it isn't, and the next page loads three from the end.
  useEffect(() => {
    const root = box.current
    if (!root) return
    const watch = new IntersectionObserver((rows) => {
      for (const r of rows) if (r.isIntersecting) setShown(Number((r.target as HTMLElement).dataset.n))
    }, { root, threshold: 0.6 })
    root.querySelectorAll('.reel').forEach((el) => watch.observe(el))
    return () => watch.disconnect()
  }, [entries.length])
  useEffect(() => {
    if (playing && entries[shown]?.item.id !== playing) setPlaying(null)
    if (shown >= entries.length - 3) near.current()
  }, [shown, entries, playing])
  // A new tab starts from its top.
  useEffect(() => { box.current?.scrollTo({ top: 0 }); setShown(0) }, [tab])

  return (
    <div className="reels" data-tab={tab}>
      <FeedTabs tab={tab} onTab={onTab} className="reels-tabs" />
      <div ref={box} className="reels-snap">
        {entries.map((e, n) => (
          <Reel key={e.item.id} n={n} entry={e} now={now} follows={follows} playing={playing === e.item.id}
            onPlay={(on) => setPlaying(on ? e.item.id : null)} onMore={() => onMore(e)} />
        ))}
        {footer && <section className="reel reel-end">{footer}</section>}
      </div>
    </div>
  )
}

function Reel({ n, entry, now, follows, playing, onPlay, onMore }: {
  n: number; entry: FeedEntry; now: number; follows: Follows; playing: boolean; onPlay: (on: boolean) => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const [noPicture, setNoPicture] = useState(false)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const href = safeHref(item.url)
  const mine = follows.following(entry)

  let frame: ReactNode
  if (item.kind === 'video' && id) {
    frame = playing ? <div className="reel-frame"><Player id={id} title={item.title} /></div> : (
      <button type="button" className="reel-frame" aria-label={`Play: ${item.title}`} onClick={() => onPlay(true)}>
        {picture && <Still src={picture} onGone={() => setNoPicture(true)} />}
        <span className="reel-bigplay" aria-hidden="true">{playIcon}</span>
      </button>
    )
  } else if (picture) {
    frame = <a className="reel-frame" href={href} target="_blank" rel="noreferrer" aria-label={`Read: ${item.title}`}><Still src={picture} onGone={() => setNoPicture(true)} /></a>
  } else {
    // No picture (a story without one, a post): the words on the sport's colour.
    frame = (
      <a className="reel-frame reel-words" href={href} target="_blank" rel="noreferrer">
        <span className="eyebrow">{item.source}</span>
        <span className="dsp">{item.kind === 'post' ? `“${item.title}”` : item.title}</span>
      </a>
    )
  }

  return (
    <section className="reel" data-n={n} data-sport={sportKey(who.sport)} aria-label={item.title}>
      {picture && <img className="reel-blur" src={picture} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" aria-hidden="true" />}
      {frame}
      <div className="reel-info">
        <Link to={`/player/${who.id}`} className="reel-who">
          <Face name={who.name} sport={who.sport} photo={who.photo} size={40} />
          <span className="ellipsis">{who.name}</span>
        </Link>
        <span className="reel-title clamp3">{item.kind === 'post' ? `${shortName(who.name)} on ${item.post?.platform ?? ''}` : item.title}</span>
        <span className="reel-src">{sourceLine(item, now, true)}{entry.moment ? ` · after ${entry.moment}` : ''}</span>
      </div>
      <div className="reel-side">
        {(!entry.following || !mine) && (
          <button type="button" className="reel-act" aria-pressed={mine} disabled={follows.busy(entry)} onClick={() => follows.toggle(entry)}>
            <span className="reel-ic">{mine ? tickIcon : plusIcon}</span>{mine ? 'Following' : 'Follow'}
          </button>
        )}
        <ShareButton {...shareFeedItem(who.id, item)} className="reel-act reel-share" text="Share" />
        <button type="button" className="reel-act" onClick={onMore}><span className="reel-ic">{dots}</span>More</button>
      </div>
    </section>
  )
}

/** What the "…" sheet adds in the edge-to-edge layout, where the card itself has no Share or way on: both. */
export function EdgeSheetExtras({ entry }: { entry: FeedEntry }) {
  const { player: who, item } = entry
  return (
    <div className="row" style={{ gap: 18, justifyContent: 'flex-start' } as CSSProperties}>
      <ShareButton {...shareFeedItem(who.id, item)} className="post-act" text="Share" />
      <Link to={`/player/${who.id}?tab=feed`} className="post-act link" style={{ marginLeft: 'auto' }}>More from {shortName(who.name)}</Link>
    </div>
  )
}

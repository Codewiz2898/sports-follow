import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { FeedEntry, FeedTabKey } from '../api'
import { useAutoplay } from '../autoplay'
import { channelOf, duration, statsLine, upright, youtubeEmbed } from '../feed'
import { safeHref, shortName } from '../format'
import { shareFeedItem } from '../share'
import { sportKey } from '../sports'
import { Face } from './FeedPost'
import { ShareButton } from './Share'

/**
 * The fan's Feed in the two layouts the server picks between (BHA-349, GET /api/me/feed/items' `layout`, which the
 * admin panel changes live): edge to edge (the S2Edge board), full-width posts down the page; and full screen (the
 * S5Reels board), one video a screen, snapping from one to the next. In both a video is YouTube's biggest thumbnail
 * until it plays, and only then does YouTube's no-cookie player load, in its place: an upright one (a Short) in an
 * upright frame. It starts when tapped, with its sound, or by itself (BHA-359, usePlaying), muted, once more than half
 * of it is on screen, as the fan's autoplay setting allows (autoplay.ts); one at a time, and it stops once it's off
 * the screen. Each card names its channel under whose it is (BHA-358). Items of players the fan doesn't follow
 * (For you's) offer Follow, which works where it is. "…" (More) opens "Why this is here".
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

/** A still as its host serves it, lazily; nothing once it fails, or for YouTube's grey 120×90 stand-in for none. */
function Still({ src, className, onGone }: { src: string; className?: string; onGone: () => void }) {
  const check = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth > 0 && img.naturalWidth <= 120) onGone() }
  return <img ref={check} className={className} src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={onGone} onLoad={(e) => check(e.currentTarget)} />
}

/** YouTube's no-cookie player, playing, in the picture's place; muted when it started by itself (YouTube's own
 * button turns the sound on). */
function Player({ id, title, muted }: { id: string; title: string; muted?: boolean }) {
  return (
    <iframe src={youtubeEmbed(id, muted)} title={title}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
  )
}

/** The one Feed video playing: which item, whether it started by itself (muted), and whether the fan has touched it
 * since (`kept`: perhaps turned its sound on with YouTube's button, which nothing outside the player can see). */
export interface Playing { id: string; muted: boolean; kept?: boolean }

function listenFront(heard: () => void) {
  document.addEventListener('visibilitychange', heard)
  return () => document.removeEventListener('visibilitychange', heard)
}

/** Whether this tab is the one in front. */
function useInFront(): boolean {
  return useSyncExternalStore(listenFront, () => document.visibilityState === 'visible')
}

/** Which of a list's videos plays (one at a time), the Android app's usePlaying. `play` starts one with its sound (a
 * tap on its picture). Its videos are the frames under `box` with data-video (the item's id: the picture, or the
 * player in its place), in view of `root` (the screen when null, else what scrolls them), for `entries`. While the tab
 * is in front and the fan's setting allows it now (autoplay.ts), the topmost video more than half of whose frame is on
 * screen (YouTube's rule) starts by itself, muted, once the list has been still for 250ms, unless one the fan started
 * or touched is still playing. A video goes once none of its frame is on screen, and a muted one nobody touched as soon as autoplay isn't allowed (the tab behind another, say). A
 * click into the playing player (its frame takes the focus from the page) keeps it as the fan scrolls on. */
export function usePlaying(box: HTMLElement | null, root: HTMLElement | null, entries: FeedEntry[]): { playing: Playing | null; play: (id: string) => void } {
  const [playing, setPlaying] = useState<Playing | null>(null)
  const allowed = useAutoplay()
  const front = useInFront()
  const starts = useRef(false)
  starts.current = allowed && front
  const shown = useRef(new Map<HTMLElement, number>())  // each video's frame, in the list's order: how much is on screen
  const settle = useCallback((start: boolean) => setPlaying((was) => {
    const frames = [...shown.current]
    const on = (id: string) => frames.some(([el, ratio]) => ratio > 0 && el.dataset.video === id)
    const now = was && on(was.id) && (starts.current || !was.muted || was.kept) ? was : null
    if (!start || !starts.current) return now
    const next = frames.find(([, ratio]) => ratio > 0.5)?.[0].dataset.video ?? null
    if (next && (!now || (now.muted && !now.kept)) && now?.id !== next) return { id: next, muted: true }
    return now
  }), [])
  const play = useCallback((id: string) => setPlaying({ id, muted: false }), [])

  const ids = entries.map((e) => e.item.id).join(' ')
  useEffect(() => {
    if (!box) return
    let wait = 0
    let watch: IntersectionObserver | null = null
    const build = () => {
      watch?.disconnect()
      const map = new Map<HTMLElement, number>()
      box.querySelectorAll<HTMLElement>('[data-video]').forEach((el) => map.set(el, 0))
      shown.current = map
      // On the screen, what's under the phone's floating tab bar isn't in view (measured again as the window changes).
      const bar = root ? null : document.querySelector('.tabbar')?.getBoundingClientRect()
      const under = bar?.height ? Math.max(0, Math.round(window.innerHeight - bar.top)) : 0
      watch = new IntersectionObserver((rows) => {
        for (const r of rows) map.set(r.target as HTMLElement, r.isIntersecting ? r.intersectionRatio : 0)
        settle(false)  // gone off screen: at once
        window.clearTimeout(wait)
        wait = window.setTimeout(() => settle(true), 250)  // the next one: once the scroll's settled
      }, { root, rootMargin: `0px 0px -${under}px 0px`, threshold: [0, 0.51] })
      map.forEach((_, el) => watch?.observe(el))
    }
    build()
    window.addEventListener('resize', build)
    return () => { window.removeEventListener('resize', build); watch?.disconnect(); window.clearTimeout(wait) }
  }, [box, root, ids, settle])
  useEffect(() => { settle(true) }, [allowed, front, settle])

  // A click into the muted player (YouTube's sound button, say) moves the focus into its frame: the page's blurs.
  const muted = playing?.muted && !playing.kept ? playing.id : null
  useEffect(() => {
    if (!muted) return
    let wait = 0
    const touched = () => {
      wait = window.setTimeout(() => {
        const el = document.activeElement
        if (el instanceof HTMLIFrameElement && el.closest<HTMLElement>('[data-video]')?.dataset.video === muted) {
          setPlaying((was) => (was?.id === muted ? { ...was, kept: true } : was))
        }
      })
    }
    window.addEventListener('blur', touched)
    return () => { window.removeEventListener('blur', touched); window.clearTimeout(wait) }
  }, [muted])
  return { playing, play }
}

// ---------------------------------------------------------------- edge to edge

/** One post edge to edge: who it's about (photo ringed in their sport's colour, name and under it the channel,
 * Follow when the fan doesn't, "…"), the full-width 16:9 picture with the video's length (an upright video upright
 * in the middle of a 4:5 one), the title, and "views · when". `playing`: its video plays in the picture's place,
 * `muted` when it started by itself. */
export function EdgePost({ entry, now, follows, playing, muted, onPlay, onMore }: {
  entry: FeedEntry; now: number; follows?: Follows; playing: boolean; muted?: boolean; onPlay: (id: string) => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const [noPicture, setNoPicture] = useState(false)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const href = safeHref(item.url)
  const length = duration(item.video?.duration)
  const mine = follows ? follows.following(entry) : true
  const channel = channelOf(item)

  let media: ReactNode = null
  if (item.kind === 'video' && id) {
    media = (
      <div className={upright(item) ? 'epost-pic upright' : 'epost-pic'} data-video={item.id}>
        {playing ? <Player id={id} title={item.title} muted={muted} /> : (
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
          <span className="epost-names">
            <span className="epost-name ellipsis">{who.name}</span>
            {channel && <span className="epost-chan ellipsis">{channel}</span>}
          </span>
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
        <span className="epost-src">{[item.kind === 'post' ? `${item.source} on ${item.post?.platform ?? ''}` : statsLine(item, now), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</span>
      </div>
    </article>
  )
}

// ---------------------------------------------------------------- full screen

/** The full-screen Feed: one item a screen, snapping; For you and Following at the top. The next page loads as the
 * last few come up (onNear). The video on screen plays by itself, muted, as the fan's setting allows (usePlaying), and
 * what plays stops once it's scrolled away. */
export function Reels({ entries, now, tab, onTab, follows, onMore, onNear, footer }: {
  entries: FeedEntry[]; now: number; tab: FeedTabKey; onTab: (tab: FeedTabKey) => void; follows: Follows
  onMore: (entry: FeedEntry) => void; onNear: () => void; footer?: ReactNode
}) {
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const [shown, setShown] = useState(0)
  const { playing, play } = usePlaying(box, box, entries)
  const near = useRef(onNear)
  near.current = onNear

  // The tab bar turns paper over the dark, and the page under the reels stays still.
  useEffect(() => {
    document.documentElement.dataset.reels = ''
    return () => { delete document.documentElement.dataset.reels }
  }, [])
  // Which one is on screen: the next page loads three from the end.
  const ids = entries.map((e) => e.item.id).join(' ')
  useEffect(() => {
    if (!box) return
    const watch = new IntersectionObserver((rows) => {
      for (const r of rows) if (r.isIntersecting) setShown(Number((r.target as HTMLElement).dataset.n))
    }, { root: box, threshold: 0.6 })
    box.querySelectorAll('.reel[data-n]').forEach((el) => watch.observe(el))
    return () => watch.disconnect()
  }, [box, ids])
  useEffect(() => {
    if (shown >= entries.length - 3) near.current()
  }, [shown, entries])
  // A new tab starts from its top.
  useEffect(() => { box?.scrollTo({ top: 0 }); setShown(0) }, [box, tab])

  return (
    <div className="reels" data-tab={tab}>
      <FeedTabs tab={tab} onTab={onTab} className="reels-tabs" />
      <div ref={setBox} className="reels-snap">
        {entries.map((e, n) => (
          <Reel key={e.item.id} n={n} entry={e} now={now} follows={follows} playing={playing?.id === e.item.id ? playing : null}
            onPlay={() => play(e.item.id)} onMore={() => onMore(e)} />
        ))}
        {/* A new one with each page: Chrome keeps the screen it had snapped to (the spinner) on screen as the page's
            reels come in above it, which opened the Feed at its end, or left a fast scroller at the spinner. */}
        {footer && <section key={`end-${entries.length}`} className="reel reel-end">{footer}</section>}
      </div>
    </div>
  )
}

function Reel({ n, entry, now, follows, playing, onPlay, onMore }: {
  n: number; entry: FeedEntry; now: number; follows: Follows; playing: Playing | null; onPlay: () => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const [noPicture, setNoPicture] = useState(false)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const href = safeHref(item.url)
  const mine = follows.following(entry)
  const channel = channelOf(item)
  // An upright video (a Short) in an upright frame, which stops above what's at the foot: nothing of the page may
  // sit over YouTube's player (its Required Minimum Functionality).
  const box = upright(item) ? 'reel-frame upright' : 'reel-frame'

  let frame: ReactNode
  if (item.kind === 'video' && id) {
    // One frame, the picture or the player in it, so what's watched for how much is on screen stays the same.
    frame = (
      <div className={box} data-video={item.id}>
        {playing ? <Player id={id} title={item.title} muted={playing.muted} /> : (
          <button type="button" className="reel-play" aria-label={`Play: ${item.title}`} onClick={onPlay}>
            {picture && <Still src={picture} onGone={() => setNoPicture(true)} />}
            <span className="reel-bigplay" aria-hidden="true">{playIcon}</span>
          </button>
        )}
      </div>
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
        {channel && <span className="reel-chan ellipsis">{channel}</span>}
        <span className="reel-title clamp3">{item.kind === 'post' ? `${shortName(who.name)} on ${item.post?.platform ?? ''}` : item.title}</span>
        <span className="reel-src">{[statsLine(item, now, true), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</span>
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

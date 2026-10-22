import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { FeedEntry, FeedTabKey } from '../api'
import { useAutoplay } from '../autoplay'
import { channelOf, duration, isShort, statsLine, upright } from '../feed'
import { feedKeyOf, spaceTaken } from '../feedKeys'
import { safeHref, shortName } from '../format'
import { shareFeedItem } from '../share'
import { setSound, useSound } from '../sound'
import { sportKey } from '../sports'
import { FeedPlayer, fullScreenAsItStarts } from './FeedPlayer'
import { Face } from './FeedPost'
import { BackIcon, MutedIcon, SoundIcon } from './Icons'
import { ShareButton } from './Share'
import { SourceMark } from './SourceMark'

/**
 * The fan's Feed in the two layouts the server picks between (BHA-349, GET /api/me/feed/items' `layout`, which the
 * admin panel changes live): edge to edge (the S2Edge board), full-width posts down the page; and full screen (the
 * S5Reels board), one video a screen, snapping from one to the next. In both a video is YouTube's biggest thumbnail
 * until it plays, and only then does YouTube's no-cookie player load, in its place: an upright one (a Short) in an
 * upright frame (FeedPlayer). It starts when tapped, with its sound, or by itself (BHA-359, usePlaying), once more than
 * half of it is on screen, as the fan's autoplay setting allows (autoplay.ts): muted until the fan turns the sound on,
 * then with it until they turn it off (BHA-381, sound.ts; the sound button beside a playing video). One at a time,
 * and it stops once it's off the screen. An upright video (a Short) plays as Shorts do (BHA-384): in the full-screen
 * layout its frame as wide as the phone allows under the tabs, who and what in the strip below it (ShortReel); from
 * edge to edge in a viewer of the Feed's Shorts over the page (ShortsViewer). Nothing of the page goes over its player
 * (YouTube's rules). Each card names its channel under whose it is (BHA-358). Items of players the fan doesn't follow
 * (For you's) offer Follow, which works where it is. "…" (More) opens "Why this is here". On a computer, Space, F and M
 * work the video on screen (BHA-660, feedKeys.ts).
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

/** The sound of every Feed video, near the one playing but never over it (YouTube's rules want nothing over its
 * player), the Android app's SoundButton: a speaker, crossed out while muted (or its sound held back till a tap on
 * the page, which this is); a tap turns it on or off for this video and the ones after it. */
function SoundButton({ className }: { className: string }) {
  const sound = useSound()
  const on = sound.on && !sound.held
  return (
    <button type="button" className={className} data-sound aria-label={on ? 'Mute the videos' : 'Turn the videos’ sound on'} aria-keyshortcuts="M" onClick={() => setSound(!on)}>
      {on ? <SoundIcon width={20} height={20} /> : <MutedIcon width={20} height={20} />}
    </button>
  )
}

/** The one Feed video playing: which item, whether it started by itself (`auto`), and whether the fan has touched its
 * player since (`kept`). Whether it has its sound is the fan's, for every video (sound.ts). */
export interface Playing { id: string; auto?: boolean; kept?: boolean }

function listenFront(heard: () => void) {
  document.addEventListener('visibilitychange', heard)
  return () => document.removeEventListener('visibilitychange', heard)
}

/** Whether this tab is the one in front. */
function useInFront(): boolean {
  return useSyncExternalStore(listenFront, () => document.visibilityState === 'visible')
}

/** Which of a list's videos plays (one at a time), the Android app's usePlaying. `play` starts one with its sound (a
 * tap on its picture), which turns the sound on for the ones after it. Its videos are the frames under `box` with data-video (the item's id: the picture, or the
 * player in its place), in view of `root` (the screen when null, else what scrolls them), for `entries`. While the tab
 * is in front and the fan's setting allows it now (autoplay.ts), the topmost video more than half of whose frame is on
 * screen (YouTube's rule) starts by itself, with the sound as the fan last left it, once the list has been still for
 * 250ms, unless one the fan started or touched is still playing. A video goes once none of its frame is on screen,
 * and one that started by itself and nobody touched as soon as autoplay isn't allowed (the tab behind another, say). A
 * click into the playing player (its frame takes the focus from the page) keeps it as the fan scrolls on. While not
 * `active` (the Shorts viewer over the list), nothing of it plays. One full screen (F) plays on till the fan's back. With
 * nothing playing, Space or F on a computer starts the one that would start by itself, with its sound as a tap on it
 * does, where it is (a Short too, as when it starts by itself, not in the Shorts viewer a tap opens); once one plays,
 * its keys are its player's (feedKeys.ts). */
export function usePlaying(box: HTMLElement | null, root: HTMLElement | null, entries: FeedEntry[], active = true): { playing: Playing | null; play: (id: string) => void } {
  const [playing, setPlaying] = useState<Playing | null>(null)
  const allowed = useAutoplay()
  const front = useInFront()
  const starts = useRef(false)
  starts.current = allowed && front && active
  const live = useRef(active)
  live.current = active
  const shown = useRef(new Map<HTMLElement, number>())  // each video's frame, in the list's order: how much is on screen
  const settle = useCallback((start: boolean) => setPlaying((was) => {
    if (!live.current) return null
    if (was && document.fullscreenElement?.closest<HTMLElement>('[data-video]')?.dataset.video === was.id) return was
    const frames = [...shown.current]
    const on = (id: string) => frames.some(([el, ratio]) => ratio > 0 && el.dataset.video === id)
    const now = was && on(was.id) && (starts.current || !was.auto || was.kept) ? was : null
    if (!start || !starts.current) return now
    const next = frames.find(([, ratio]) => ratio > 0.5)?.[0].dataset.video ?? null
    if (next && (!now || (now.auto && !now.kept)) && now?.id !== next) return { id: next, auto: true }
    return now
  }), [])
  const play = useCallback((id: string) => { setPlaying({ id }); setSound(true) }, [])
  const now = useRef(playing)
  now.current = playing

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
  useEffect(() => { settle(true) }, [allowed, front, active, settle])

  // A click into the player that started by itself (YouTube's sound button, say) moves the focus into its frame: the
  // page's blurs.
  const auto = playing?.auto && !playing.kept ? playing.id : null
  useEffect(() => {
    if (!auto) return
    let wait = 0
    const touched = () => {
      wait = window.setTimeout(() => {
        const el = document.activeElement
        if (el instanceof HTMLIFrameElement && el.closest<HTMLElement>('[data-video]')?.dataset.video === auto) {
          setPlaying((was) => (was?.id === auto ? { ...was, kept: true } : was))
        }
      })
    }
    window.addEventListener('blur', touched)
    return () => { window.removeEventListener('blur', touched); window.clearTimeout(wait) }
  }, [auto])

  // Space or F with nothing playing: the topmost video more than half on screen starts, full screen for F.
  useEffect(() => {
    if (!box || !active) return
    const pressed = (e: KeyboardEvent) => {
      if (now.current) return
      const does = feedKeyOf(e)
      if (does !== 'pause' && does !== 'full') return
      const id = [...shown.current].find(([, ratio]) => ratio > 0.5)?.[0].dataset.video
      if (!id) return
      e.preventDefault()
      if (e.repeat) return
      if (does === 'pause') spaceTaken()
      if (does === 'full') fullScreenAsItStarts(id)
      play(id)
    }
    window.addEventListener('keydown', pressed)
    return () => window.removeEventListener('keydown', pressed)
  }, [box, active, play])
  return { playing, play }
}

// ---------------------------------------------------------------- edge to edge

/** One post edge to edge: who it's about (photo ringed in their sport's colour, name and under it the channel,
 * Follow when the fan doesn't, "…"), the full-width 16:9 picture with the video's length (an upright video upright
 * in the middle of a 4:5 one), the title, and "views · when". `playing`: its video plays in the picture's place,
 * with the sound button under its right-hand corner. A video's source's icon (SourceMark) sits by the "…", linked to
 * it there; full screen, before the channel's name, and in a Short's row. */
export function EdgePost({ entry, now, follows, playing, onPlay, onMore }: {
  entry: FeedEntry; now: number; follows?: Follows; playing: boolean; onPlay: (id: string) => void; onMore: () => void
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
        {playing ? <FeedPlayer key={id} item={item} playerId={who.id} /> : (
          <button type="button" className="epost-play" aria-label={`Play: ${item.title}`} onClick={() => { onPlay(item.id); setSound(true) }}>
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
        <SourceMark item={item} />
        <button type="button" className="epost-more" aria-label="More about this item" onClick={onMore}>{dots}</button>
      </div>
      {media}
      <div className="epost-foot">
        <div className="epost-cap">
          {(item.kind === 'video' || (item.kind === 'news' && picture)) && <span className="epost-title clamp3">{item.title}</span>}
          <span className="epost-src">{[item.kind === 'post' ? `${item.source} on ${item.post?.platform ?? ''}` : statsLine(item, now), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</span>
        </div>
        {playing && <SoundButton className="epost-sound" />}
      </div>
    </article>
  )
}

// ---------------------------------------------------------------- full screen

/** The full-screen Feed: one item a screen, snapping; For you and Following at the top. The next page loads as the
 * last few come up (onNear). The video on screen plays by itself as the fan's setting allows (usePlaying), muted till
 * they turn the sound on (the sound button at the top right, by the tabs), and what plays stops once it's scrolled
 * away. */
export function Reels({ entries, now, tab, onTab, follows, onMore, onNear, footer, start = 0, onScrolled }: {
  entries: FeedEntry[]; now: number; tab: FeedTabKey; onTab: (tab: FeedTabKey) => void; follows: Follows
  onMore: (entry: FeedEntry) => void; onNear: () => void; footer?: ReactNode
  start?: number; onScrolled?: (top: number) => void  // where the reels were, Back to the Feed (myFeed.ts), and where they are
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
  // A Short on screen: the tab bar over it turns dark and see-through, as Shorts' own (:root[data-reels='short']).
  const short = isShort(entries[shown]?.item)
  useEffect(() => { document.documentElement.dataset.reels = short ? 'short' : '' }, [short])
  // A new tab starts from its top; Back to the Feed, where the fan was (`start`).
  useEffect(() => { box?.scrollTo({ top: start }); setShown(0) }, [box, tab])  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="reels" data-tab={tab}>
      <FeedTabs tab={tab} onTab={onTab} className="reels-tabs" />
      {playing && <SoundButton className="reels-sound" />}
      <div ref={setBox} className="reels-snap" onScroll={onScrolled && ((e) => onScrolled(e.currentTarget.scrollTop))}>
        {entries.map((e, n) => (
          isShort(e.item) ? (
            <ShortReel key={e.item.id} n={n} entry={e} now={now} follows={follows} playing={playing?.id === e.item.id}
              onPlay={() => play(e.item.id)} onMore={() => onMore(e)} />
          ) : (
            <Reel key={e.item.id} n={n} entry={e} now={now} follows={follows} playing={playing?.id === e.item.id ? playing : null}
              onPlay={() => play(e.item.id)} onMore={() => onMore(e)} />
          )
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
        {playing ? <FeedPlayer key={id} item={item} playerId={who.id} /> : (
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
        {/* The source's icon before the channel, not down the side: the buttons there grow toward the player. */}
        {channel && <span className="reel-chan-row"><SourceMark item={item} /><span className="reel-chan ellipsis">{channel}</span></span>}
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

// ---------------------------------------------------------------- Shorts

/** A Short as Shorts play (BHA-384, the Android app's ShortReel): on black, its 9:16 frame under the tabs as wide as
 * the screen allows once what's under it is clear (.reel.short: full width on a phone taller than that needs), its
 * picture there till it plays (so nothing moves as it starts), YouTube's player without its controls once it does;
 * who, the channel, Follow, Share and More in a row under it, then the title, and the numbers when there's room.
 * (In the viewer, the player's page opens over it: Back from there comes back to it, as Shorts' does.) */
function ShortReel({ n, entry, now, follows, playing, onPlay, onMore }: {
  n?: number; entry: FeedEntry; now: number; follows: Follows; playing: boolean; onPlay: () => void; onMore: () => void
}) {
  const { player: who, item } = entry
  const [noPicture, setNoPicture] = useState(false)
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const mine = follows.following(entry)
  const channel = channelOf(item)
  return (
    <section className="reel short" data-n={n} data-sport={sportKey(who.sport)} aria-label={item.title}>
      <div className="short-frame" data-video={item.id}>
        {playing ? <FeedPlayer key={item.video?.youtube_id} item={item} playerId={who.id} bare /> : (
          <button type="button" className="reel-play" aria-label={`Play: ${item.title}`} onClick={onPlay}>
            {picture && <Still src={picture} onGone={() => setNoPicture(true)} />}
            <span className="reel-bigplay" aria-hidden="true">{playIcon}</span>
          </button>
        )}
      </div>
      <div className="short-strip">
        <div className="short-row">
          <Link to={`/player/${who.id}`} className="short-who">
            <Face name={who.name} sport={who.sport} photo={who.photo} size={28} />
            <span className="ellipsis"><b>{who.name}</b>{channel && <span className="short-chan">{`  ·  ${channel}`}</span>}</span>
          </Link>
          {(!entry.following || !mine) && (
            <button type="button" className="short-follow" aria-pressed={mine} disabled={follows.busy(entry)} onClick={() => follows.toggle(entry)}>
              {mine ? 'Following' : 'Follow'}
            </button>
          )}
          <SourceMark item={item} />
          <ShareButton {...shareFeedItem(who.id, item)} className="short-icon" />
          <button type="button" className="short-icon" aria-label="More about this item" onClick={onMore}>{dots}</button>
        </div>
        <span className="short-title">{item.title}</span>
        <span className="short-src">{[statsLine(item, now, true), entry.moment ? `after ${entry.moment}` : ''].filter(Boolean).join(' · ')}</span>
      </div>
    </section>
  )
}

/** The Feed's Shorts over the page, from edge to edge (BHA-384, the Android app's ShortsViewer): opened at the one
 * tapped, `start`, one a screen, snapping, the one on screen playing as the Feed's would (the fan's autoplay and
 * sound), swiping (or scrolling) through the rest of what the Feed has loaded (`onNear` loads more). Back (the
 * browser's: it was opened as a step of its own), the arrow, or Escape closes it, and the Feed is where it was. */
export function ShortsViewer({ entries, start, now, follows, onMore, onNear, onClose }: {
  entries: FeedEntry[]; start: string; now: number; follows: Follows; onMore: (entry: FeedEntry) => void; onNear: () => void; onClose: () => void
}) {
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const list = entries.filter((e) => isShort(e.item))
  const { playing, play } = usePlaying(box, box, list)
  const near = useRef(onNear)
  near.current = onNear
  // Opened at the one tapped, playing it (a tap: with its sound); and the page under it stays still meanwhile.
  useEffect(() => {
    if (!box) return
    const at = Math.max(0, list.findIndex((e) => e.item.id === start))
    box.scrollTo({ top: at * box.clientHeight })
    if (list[at]) play(list[at].item.id)
  }, [box]) // once, as it opens
  useEffect(() => {
    document.documentElement.dataset.shorts = ''
    // The "…" sheet's own first, and full screen's (F): the browser's.
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('dialog[open]') && !document.fullscreenElement) onClose() }
    window.addEventListener('keydown', escape)
    return () => { delete document.documentElement.dataset.shorts; window.removeEventListener('keydown', escape) }
  }, [onClose])
  const ids = list.map((e) => e.item.id).join(' ')
  useEffect(() => {
    if (!box) return
    const watch = new IntersectionObserver((rows) => {
      for (const r of rows) if (r.isIntersecting && Number((r.target as HTMLElement).dataset.n) >= list.length - 3) near.current()
    }, { root: box, threshold: 0.6 })
    box.querySelectorAll('.reel[data-n]').forEach((el) => watch.observe(el))
    return () => watch.disconnect()
  }, [box, ids])

  return (
    <div className="shorts-viewer" role="dialog" aria-modal="true" aria-label="Shorts">
      <div className="reels shorts-reels">
        <button type="button" className="shorts-back" aria-label="Back to the Feed" onClick={onClose}><BackIcon width={22} height={22} /></button>
        {playing && <SoundButton className="reels-sound" />}
        <div ref={setBox} className="reels-snap">
          {list.map((e, n) => (
            <ShortReel key={e.item.id} n={n} entry={e} now={now} follows={follows} playing={playing?.id === e.item.id}
              onPlay={() => play(e.item.id)} onMore={() => onMore(e)} />
          ))}
        </div>
      </div>
    </div>
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

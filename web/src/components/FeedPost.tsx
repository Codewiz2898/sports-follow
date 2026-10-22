import { useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { FeedCircle, FeedItem, FeedWho, Photo } from '../api'
import { duration, fromLine, openLabel, upright, views } from '../feed'
import { initials, safeHref, shortName } from '../format'
import { shareFeedItem } from '../share'
import { sportKey } from '../sports'
import { FeedPlayer } from './FeedPlayer'
import { setSound } from '../sound'
import { ShareButton } from './Share'
import { SourceMark } from './SourceMark'

/**
 * The Feed's cards, one shape for every kind (the InstaCards board): who it's about with their photo, where it's
 * from and when, what it is, a picture, the words, then Share and the way to the source; the "…" opens "Why this
 * is here". A video is YouTube's thumbnail and nothing of YouTube's loads until play is tapped, then its no-cookie
 * player takes the picture's place (FeedPlayer). A story shows the image its publisher's feed attaches, as they serve it, or
 * without one its headline on the sport's colour. A post is words only, opened where it was posted.
 */

/** A player's photo in a circle: the square around their face when the photos job found it, else the photo's top,
 * and their initials on their sport's colour without one (or once it fails to load). */
export function Face({ name, sport, photo, size = 38 }: { name: string; sport?: string | null; photo?: Photo | null; size?: number }) {
  const [broken, setBroken] = useState<string | null>(null)
  const shown = photo && photo.url !== broken ? photo : null
  const c = shown?.crop
  // The face's square fills the circle: the photo scaled so the square is the circle's width, moved so it's in it.
  const framed: CSSProperties | undefined = c && c.w > 0 && c.h > 0
    ? { width: `${100 / c.w}%`, height: `${100 / c.h}%`, left: `${(-c.x / c.w) * 100}%`, top: `${(-c.y / c.h) * 100}%`, objectFit: 'fill' }
    : undefined
  return (
    <span className="face" data-sport={sportKey(sport)} style={{ '--face': `${size}px` } as CSSProperties} aria-hidden="true">
      {initials(name)}
      {/* Over the initials, which show while it loads. */}
      {shown && <img src={shown.url} alt="" loading="lazy" decoding="async" style={framed} onError={() => setBroken(shown.url)} />}
    </span>
  )
}

const dots = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" /></svg>
)
const playIcon = <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>

/** A picture as its host serves it, lazily; nothing once it fails, or for YouTube's grey 120×90 stand-in for a video it has none for. */
function Picture({ src, onGone }: { src: string; onGone: () => void }) {
  const check = (img: HTMLImageElement | null) => { if (img?.complete && img.naturalWidth > 0 && img.naturalWidth <= 120) onGone() }
  return <img ref={check} src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={onGone} onLoad={(e) => check(e.currentTarget)} />
}

/** One item of the Feed as a post. `more`: "More from …" under a video, to the player's own Feed (on their own page, the video's source instead). */
export function FeedPost({ who, item, now, moment, more = true, onMore }: { who: FeedWho; item: FeedItem; now: number; moment?: string | null; more?: boolean; onMore?: () => void }) {
  const [playing, setPlaying] = useState(false)
  const [noPicture, setNoPicture] = useState(false)
  const short = shortName(who.name)
  const own = item.kind === 'post' && Boolean(item.post?.own)
  const href = safeHref(item.url)
  const id = item.video?.youtube_id
  const picture = !noPicture && item.picture ? safeHref(item.picture) : null
  const length = duration(item.video?.duration)

  let media = null
  let caption = null
  if (item.kind === 'video' && id) {
    media = (
      <div className={upright(item) ? 'post-media video upright' : 'post-media video'}>
        {playing ? (
          <FeedPlayer item={item} playerId={who.id} />
        ) : (
          <>
            {picture && <Picture src={picture} onGone={() => setNoPicture(true)} />}
            <button type="button" className="post-play" aria-label={`Play: ${item.title}`} onClick={() => { setPlaying(true); setSound(true) }}>{playIcon}</button>
            {length && <span className="post-chip">{length}</span>}
          </>
        )}
      </div>
    )
    caption = <><span className="post-title clamp2">{item.title}</span><span className="post-sub">{[views(item.video?.views), moment && `after ${moment}`].filter(Boolean).join(' · ')}</span></>
  } else if (item.kind === 'news' && picture) {
    media = <a className="post-media" href={href} target="_blank" rel="noreferrer" tabIndex={-1} aria-hidden="true"><Picture src={picture} onGone={() => setNoPicture(true)} /></a>
    caption = <><span className="post-title">{item.title}</span>{item.summary && <span className="post-sub clamp2" style={{ fontWeight: 600 }}>{item.summary}</span>}</>
  } else if (item.kind === 'news') {
    // No image came with the story: the headline is the picture, on the sport's colour.
    media = (
      <a className="post-block" href={href} target="_blank" rel="noreferrer">
        <span className="eyebrow">{item.source}</span>
        <span className="dsp">{item.title}</span>
      </a>
    )
    caption = item.summary ? <span className="post-sub clamp2" style={{ fontWeight: 600 }}>{item.summary}</span> : null
  } else if (item.kind === 'post') {
    media = <p className="post-quote clamp3">“{item.title}”</p>
    caption = <span className="post-sub">A tap opens it on {item.post?.platform ?? 'where it was posted'}</span>
  }
  if (!media) return null

  return (
    <article className="post" data-sport={sportKey(who.sport)}>
      <div className="post-head">
        <Link to={`/player/${who.id}`} className="post-who">
          <Face name={who.name} sport={who.sport} photo={who.photo} />
          <span className="stack" style={{ gap: 1, minWidth: 0 }}>
            <span className="who ellipsis">{who.name}</span>
            <span className="post-sub ellipsis">{fromLine(item, now)}</span>
          </span>
        </Link>
        <span className={`post-tag${own ? ' own' : ''}`}>{own ? `From ${short}` : item.tag}</span>
        <SourceMark item={item} />
        {onMore && <button type="button" className="post-more" aria-label="Why this is here, and feedback" onClick={onMore}>{dots}</button>}
      </div>
      {media}
      {caption && <div className="post-cap">{caption}</div>}
      <div className="post-acts">
        <ShareButton {...shareFeedItem(who.id, item)} className="post-act" text="Share" />
        {/* A video's way on is the player's own Feed; on their page, where it already is, the video where it was posted. */}
        {item.kind === 'video' && more
          ? <Link to={`/player/${who.id}?tab=feed`} className="post-act link">More from {short}</Link>
          : href && <a href={href} target="_blank" rel="noreferrer" className="post-act link">{openLabel(item)}</a>}
      </div>
    </article>
  )
}

/** The players across the top of the Feed, "All" first: a ring in their sport's colour while they have something
 * the fan hasn't seen (`lit`), grey once they have; a tap shows only theirs, and again shows everyone's. */
export function Circles({ players, picked, lit, onPick }: { players: FeedCircle[]; picked: number | null; lit: (c: FeedCircle) => boolean; onPick: (id: number | null) => void }) {
  return (
    <div className="circles" role="group" aria-label="Show one player's">
      <button type="button" className="circle" aria-pressed={picked == null} onClick={() => onPick(null)}>
        <span className="ring all"><span className="face all" style={{ '--face': '50px' } as CSSProperties} aria-hidden="true">ALL</span></span>
        <span className="nm">All</span>
      </button>
      {players.map((p) => {
        const on = lit(p)
        return (
          <button key={p.id} type="button" className="circle" data-sport={sportKey(p.sport)} aria-pressed={picked === p.id} onClick={() => onPick(picked === p.id ? null : p.id)}>
            <span className={`ring${on ? ' lit' : ''}`}><Face name={p.name} sport={p.sport} photo={p.photo} size={50} /></span>
            <span className="nm">{shortName(p.name)}</span>
            {on && <span className="sr-only">, something new</span>}
          </button>
        )
      })}
    </div>
  )
}

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import type { MatchStream } from '../api'
import { safeHref } from '../format'
import { streamNote } from '../match'
import { SectionHead } from './Blocks'
import { CloseIcon, OutIcon } from './Icons'

// The narrowest a platform allows its player in another site's page: Twitch's rule is at least 400×300. In a narrower
// column (a phone's) its player opens over the whole screen instead, sideways while the phone is held upright.
const MIN_WIDTH: Record<string, number> = { twitch: 400 }

/** The app's own player for a stream, playing at once (it's made only once a fan taps): Twitch's, told this page's
 * host, as it must be; Kick's; YouTube's no-cookie one, as a feed video's is. None for a player this app doesn't know. */
function playerUrl(embed: { player: string; id: string }): string | null {
  const id = encodeURIComponent(embed.id)
  if (embed.player === 'twitch') return `https://player.twitch.tv/?channel=${id}&parent=${encodeURIComponent(window.location.hostname)}&autoplay=true`
  if (embed.player === 'kick') return `https://player.kick.com/${id}?autoplay=true`
  if (embed.player === 'youtube') return `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0&playsinline=1`
  return null
}

/** How wide an element is (its ref, and its width), as it changes: a window turned, or resized. Measured before it's
 * first drawn, so a wide enough column never shows the narrow one's player first. */
function useWidth() {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    if (!el) return
    setWidth(el.clientWidth)
    if (typeof ResizeObserver === 'undefined') return
    const seen = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    seen.observe(el)
    return () => seen.disconnect()
  }, [el])
  return [setEl, width] as const
}

const play = <svg width={26} height={26} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>

const frame = (src: string, title: string) => (
  <iframe src={src} title={title} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
)

/**
 * Where to watch a game while it's on or about to be (sports_follow/watch.py; match.ts's watchable says when): its
 * first stream, the organiser's main one where there is one, big in the sport's colour, and its others as links.
 * Where the app has a player for the first's platform, a tap plays it inside the page, or over the whole screen
 * where the column is narrower than the platform allows (MIN_WIDTH), with a link to the platform beside it; else
 * it's a link, "Watch on Douyu", opening the platform's own page. Which stream plays is the page's (`playing`, its
 * address), so a player started stays while the page is open, though the game is called over before its broadcast
 * has shown the end.
 */
export function Watch({ streams, title, sport, playing, onPlay }: {
  streams: MatchStream[]; title: string; sport: string; playing: string | null; onPlay: (url: string | null) => void
}) {
  const [ref, width] = useWidth()
  const location = useLocation()
  const navigate = useNavigate()
  // Playing over the whole screen: chosen at the tap, where the column is too narrow for the player, and kept till
  // it's closed, so turning the phone sideways doesn't put it back in the page.
  const [whole, setWhole] = useState(false)
  const poster = useRef<HTMLButtonElement>(null)  // where focus goes back to once that's closed
  // The stream playing stays first while it's listed, though another comes to the top of the list meanwhile.
  const first = streams.find((s) => s.url === playing) ?? streams[0]
  const on = !!first && playing === first.url
  const covering = on && whole
  const close = () => { setWhole(false); onPlay(null); poster.current?.focus() }
  // Over the whole screen it's a step in the browser's history of its own, so Back (a phone's gesture) closes it,
  // and × goes back. Closed once its step has been seen and is gone.
  const stepped = !!(location.state as { watching?: boolean } | null)?.watching
  const seen = useRef(false)
  useEffect(() => {
    if (!covering) seen.current = false
    else if (stepped) seen.current = true
    else if (seen.current) close()
  })
  if (!first) return null
  const more = streams.filter((s) => s !== first)
  const embed = first.embed
  const src = embed ? playerUrl(embed) : null
  const narrow = !!embed && width < (MIN_WIDTH[embed.player] ?? 0)  // too narrow for its player here
  const start = () => {
    setWhole(narrow)
    onPlay(first.url)
    if (narrow) navigate(location, { state: { ...(location.state as object | null), watching: true } })
  }
  const note = streamNote(first)
  const named = `${title} on ${first.platform}`
  return (
    <section ref={ref} className="section" aria-label="Watch live" data-sport={sport}>
      <SectionHead title="Watch live" />
      {src && embed ? (
        <>
          <div className={`watch-player${embed.player in MIN_WIDTH && !narrow ? ' tall' : ''}`}>
            {on && !whole ? frame(src, named) : (
              <button ref={poster} type="button" className="watch-start" onClick={start} aria-label={`Play on ${first.platform}${note ? `, ${note}` : ''}`}>
                <span className="watch-play">{play}</span>
                <span className="stack" style={{ gap: 4 }}>
                  <span className="dsp watch-big">Watch on {first.platform}</span>
                  {note && <span className="tag watch-note">{note}</span>}
                </span>
              </button>
            )}
          </div>
          <a className="link small" href={safeHref(first.url)} target="_blank" rel="noopener noreferrer" style={{ alignSelf: 'flex-start' }}>Open on {first.platform}</a>
          {covering && <WholeScreen src={src} title={named} onClose={() => { if (seen.current) navigate(-1); else close() }} />}
        </>
      ) : (
        <WatchLink stream={first} big />
      )}
      {more.length > 0 && (
        <ul className="card list" aria-label="More streams">
          {more.map((s) => <li key={s.url}><WatchLink stream={s} /></li>)}
        </ul>
      )}
    </section>
  )
}

/** A player over the whole screen, sideways while the phone is held upright (styles.css's .watch-whole), so it's
 * as wide as its platform needs on any phone, and as tall as the screen: × is in a strip beside it, not over it.
 * × or Escape closes it (Escape while the page has the keys, not the player). */
function WholeScreen({ src, title, onClose }: { src: string; title: string; onClose: () => void }) {
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose })
  useEffect(() => {
    const scrolled = document.body.style.overflow
    document.body.style.overflow = 'hidden'  // the page stays where it was behind it
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') close.current() }
    window.addEventListener('keydown', key)
    return () => { document.body.style.overflow = scrolled; window.removeEventListener('keydown', key) }
  }, [])
  return createPortal(
    <div className="watch-whole" role="dialog" aria-modal="true" aria-label={title}>
      <div className="watch-whole-frame">
        {frame(src, title)}
        <div className="watch-whole-side">
          <button type="button" className="btn icon" aria-label="Close the player" onClick={onClose} autoFocus><CloseIcon width={20} height={20} /></button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** A stream as a link to its platform's page, in a new tab: the main one big in the sport's colour, others a row. */
function WatchLink({ stream, big = false }: { stream: MatchStream; big?: boolean }) {
  const href = safeHref(stream.url)
  const note = streamNote(stream)
  if (!href) return null
  return (
    <a className={big ? 'watch-link' : 'watch-row'} href={href} target="_blank" rel="noopener noreferrer">
      <span className="stack" style={{ gap: 4, minWidth: 0 }}>
        <span className={big ? 'dsp watch-big' : 'title'}>{big ? `Watch on ${stream.platform}` : stream.platform}</span>
        {note && <span className={big ? 'tag watch-note' : 'small muted'}>{note}</span>}
      </span>
      <OutIcon width={big ? 26 : 20} height={big ? 26 : 20} />
    </a>
  )
}

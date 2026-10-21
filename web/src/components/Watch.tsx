import { useEffect, useLayoutEffect, useState } from 'react'
import type { MatchStream } from '../api'
import { safeHref } from '../format'
import { streamNote } from '../match'
import { SectionHead } from './Blocks'
import { OutIcon } from './Icons'

// Twitch's rule for a player in another site's page: at least 400×300. A narrower column (a phone's) links to the
// channel instead, as it does every platform the app has no player for.
const TWITCH_WIDTH = 400

/** The app's own player for a stream, playing at once (it's made only once a fan taps): Twitch's, told this page's
 * host, as it must be; YouTube's no-cookie one, as a feed video's is. None for a player this app doesn't know. */
function playerUrl(embed: { player: string; id: string }): string | null {
  const id = encodeURIComponent(embed.id)
  if (embed.player === 'twitch') return `https://player.twitch.tv/?channel=${id}&parent=${encodeURIComponent(window.location.hostname)}&autoplay=true`
  if (embed.player === 'youtube') return `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0&playsinline=1`
  return null
}

/** How wide an element is (its ref, and its width), as it changes: a window turned, or resized. Measured before it's
 * first drawn, so a wide enough column never shows the link first. */
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

/**
 * Where to watch a game while it's on or about to be (sports_follow/watch.py; match.ts's watchable says when): its
 * first stream, the organiser's main one where there is one, big in the sport's colour, and its others as links.
 * The first plays inside the page once tapped, where the app has a player for its platform and the column is wide
 * enough for it (Twitch's 400px); else it's a link to it, "Watch on Kick", opening the platform's own page.
 */
export function Watch({ streams, title, sport }: { streams: MatchStream[]; title: string; sport: string }) {
  const [ref, width] = useWidth()
  const [playing, setPlaying] = useState(false)
  const [first, ...more] = streams
  useEffect(() => { setPlaying(false) }, [first?.url])  // another stream to the fore: a tap plays it
  if (!first) return null
  const embed = first.embed
  const src = embed && (embed.player !== 'twitch' || width >= TWITCH_WIDTH) ? playerUrl(embed) : null
  const note = streamNote(first)
  return (
    <section ref={ref} className="section" aria-label="Watch live" data-sport={sport}>
      <SectionHead title="Watch live" />
      {src && embed ? (
        <div className={`watch-player${embed.player === 'twitch' ? ' tall' : ''}`}>
          {playing ? (
            <iframe src={src} title={`${title} on ${first.platform}`} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin" />
          ) : (
            <button type="button" className="watch-start" onClick={() => setPlaying(true)} aria-label={`Play on ${first.platform}${note ? `, ${note}` : ''}`}>
              <span className="watch-play">{play}</span>
              <span className="stack" style={{ gap: 4 }}>
                <span className="dsp watch-big">Watch on {first.platform}</span>
                {note && <span className="tag watch-note">{note}</span>}
              </span>
            </button>
          )}
        </div>
      ) : (
        <WatchLink stream={first} big />
      )}
      {playing && src && (
        <a className="link small" href={safeHref(first.url)} target="_blank" rel="noopener noreferrer" style={{ alignSelf: 'flex-start' }}>Open on {first.platform}</a>
      )}
      {more.length > 0 && (
        <ul className="card list" aria-label="More streams">
          {more.map((s) => <li key={s.url}><WatchLink stream={s} /></li>)}
        </ul>
      )}
    </section>
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

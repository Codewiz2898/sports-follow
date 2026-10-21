import type { MatchStream } from '../api'
import { safeHref } from '../format'
import { streamNote } from '../match'
import { SectionHead } from './Blocks'
import { OutIcon } from './Icons'

/** The app's own player for a stream, playing at once (it's made only once a fan taps): Twitch's, told this page's
 * host, as it must be; Kick's; YouTube's no-cookie one, as a feed video's is. None for a player this app doesn't know. */
function playerUrl(embed: { player: string; id: string }): string | null {
  const id = encodeURIComponent(embed.id)
  if (embed.player === 'twitch') return `https://player.twitch.tv/?channel=${id}&parent=${encodeURIComponent(window.location.hostname)}&autoplay=true`
  if (embed.player === 'kick') return `https://player.kick.com/${id}?autoplay=true`
  if (embed.player === 'youtube') return `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0&playsinline=1`
  return null
}

const play = <svg width={26} height={26} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>

/**
 * Where to watch a game while it's on or about to be (sports_follow/watch.py; match.ts's watchable says when): its
 * first stream, the organiser's main one where there is one, big in the sport's colour, and its others as links.
 * Where the app has a player for the first's platform, a tap plays it inside the page, at the column's width, 16:9,
 * with a link to the platform beside it; the player's own full-screen button makes it bigger. Else it's a link,
 * "Watch on Douyu", opening the platform's own page. On a phone that player is smaller than Twitch's stated minimum
 * for an embedded one (400×300): Bharath chose on 21 Oct to have it play in the page anyway, rather than over the
 * whole screen. A phone never autoplays it either, so there the fan taps the player's own play too. Which stream
 * plays is the page's (`playing`, its address), so a player started stays while the page is open, though the game
 * is called over before its broadcast has shown the end.
 */
export function Watch({ streams, title, sport, playing, onPlay }: {
  streams: MatchStream[]; title: string; sport: string; playing: string | null; onPlay: (url: string | null) => void
}) {
  // The stream playing stays first while it's listed, though another comes to the top of the list meanwhile.
  const first = streams.find((s) => s.url === playing) ?? streams[0]
  if (!first) return null
  const more = streams.filter((s) => s !== first)
  const embed = first.embed
  const src = embed ? playerUrl(embed) : null
  const note = streamNote(first)
  return (
    <section className="section" aria-label="Watch live" data-sport={sport}>
      <SectionHead title="Watch live" />
      {src ? (
        <>
          <div className="watch-player">
            {playing === first.url ? (
              <iframe src={src} title={`${title} on ${first.platform}`} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
            ) : (
              <button type="button" className="watch-start" onClick={() => onPlay(first.url)} aria-label={`Play on ${first.platform}${note ? `, ${note}` : ''}`}>
                <span className="watch-play">{play}</span>
                <span className="stack" style={{ gap: 4 }}>
                  <span className="dsp watch-big">Watch on {first.platform}</span>
                  {note && <span className="tag watch-note">{note}</span>}
                </span>
              </button>
            )}
          </div>
          <a className="link small" href={safeHref(first.url)} target="_blank" rel="noopener noreferrer" style={{ alignSelf: 'flex-start' }}>Open on {first.platform}</a>
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

import { useEffect, useRef, useState } from 'react'
import { api, type FeedItem } from '../api'
import { openLabel, refusedHere, youtubeEmbed } from '../feed'
import { safeHref } from '../format'

/**
 * YouTube's no-cookie player for a Feed video, playing in the picture's place, with YouTube's IFrame Player API
 * listening to it (BHA-380). A video YouTube won't play here (its owner keeps it off other sites, which YouTube's
 * Data API doesn't say: feed.refusedHere) says so where its player was, with the way to it on YouTube, and the server
 * is told, once, so no fan's Feed serves it again (feedUnplayable). The Android app's InlineVideo does the same.
 */

interface YTNamespace { Player: new (frame: HTMLIFrameElement, options: { events: { onError: (e: { data: unknown }) => void } }) => unknown }
declare global {
  interface Window { YT?: YTNamespace; onYouTubeIframeAPIReady?: () => void }
}

let loading: Promise<YTNamespace> | null = null

/** YouTube's IFrame Player API, loaded once, the first time a Feed video plays. */
function youtubeApi(): Promise<YTNamespace> {
  loading ??= new Promise((resolve, reject) => {
    if (window.YT?.Player) { resolve(window.YT); return }
    const before = window.onYouTubeIframeAPIReady
    window.onYouTubeIframeAPIReady = () => { before?.(); if (window.YT) resolve(window.YT) }
    const script = document.createElement('script')
    script.src = 'https://www.youtube.com/iframe_api'
    script.async = true
    script.onerror = () => { loading = null; reject(new Error("YouTube's player API didn't load")) }
    document.head.appendChild(script)
  })
  return loading
}

const told = new Set<string>() // the videos this page has told the server won't play here, once each

export function FeedPlayer({ item, playerId, muted }: { item: FeedItem; playerId: number; muted?: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [refused, setRefused] = useState(false)
  const id = item.video?.youtube_id ?? ''

  useEffect(() => {
    const el = frame.current
    if (!el || refused) return
    let gone = false
    youtubeApi().then((YT) => {
      if (gone) return
      new YT.Player(el, { // the player is its frame's: React removes the frame, and it goes with it
        events: {
          onError: (e) => {
            if (gone || !refusedHere(e.data)) return
            setRefused(true)
            if (told.has(item.id)) return
            told.add(item.id)
            api.feedUnplayable(playerId, item.id).catch(() => { told.delete(item.id) }) // said again the next time it's played
          },
        },
      })
    }).catch(() => { /* without the API, the player plays as it is: one that won't shows YouTube's own message */ })
    return () => { gone = true }
  }, [id, muted, item.id, playerId, refused])

  if (refused) {
    const href = safeHref(item.url)
    return (
      <div className="player-refused">
        <span>This one plays only on YouTube.</span>
        {href && <a className="btn link" href={href} target="_blank" rel="noreferrer">{openLabel(item)}</a>}
      </div>
    )
  }
  return (
    <iframe ref={frame} src={youtubeEmbed(id, muted, window.location.origin)} title={item.title}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
  )
}

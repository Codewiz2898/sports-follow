import { useEffect, useRef, useState } from 'react'
import { api, type FeedItem } from '../api'
import { openLabel, refusedHere, youtubeEmbed } from '../feed'
import { feedKeyOf } from '../feedKeys'
import { safeHref } from '../format'
import { holdSound, mayPlayAloud, setSound, soundIsOn, useSound } from '../sound'

/**
 * YouTube's no-cookie player for a Feed video, playing in the picture's place, with YouTube's IFrame Player API
 * listening to it (BHA-380). A video YouTube won't play here (its owner keeps it off other sites, which YouTube's
 * Data API doesn't say: feed.refusedHere) says so where its player was, with the way to it on YouTube, and the server
 * is told, once, so this fan's Feed doesn't serve it again, nor anyone's once enough networks have said so
 * (feedUnplayable). Its sound is the fan's (sound.ts, BHA-381):
 * turned on and off where it plays, through the API, and YouTube's own sound button turns it on or off for the videos
 * after it too. One that should have its sound but may not yet (the page not tapped: the browser's rule) starts muted
 * with its sound held back, as does one the browser won't start aloud, and the fan's next tap on the page lets it
 * out. `bare`: without YouTube's controls, a Short's (BHA-384). The Android app's InlineVideo does the same. On a
 * computer the Feed's keys work it (BHA-660, feedKeys.ts): Space pauses it or plays it on, F takes it full screen and
 * back, M turns its sound off or on.
 */

interface YTPlayer {
  mute: () => void; unMute: () => void; isMuted: () => boolean; getVolume: () => number; setVolume: (to: number) => void
  getPlayerState: () => number; playVideo: () => void; pauseVideo: () => void
}
interface YTNamespace {
  Player: new (frame: HTMLIFrameElement, options: { events: { onReady?: () => void; onError: (e: { data: unknown }) => void } }) => YTPlayer
}
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

const ENDED = 0
const PLAYING = 1
const BUFFERING = 3

/** The Feed's players on this page, the latest started last: its frame, and YouTube's API to it once that's ready. */
const players: { frame: HTMLIFrameElement; yt: () => YTPlayer | null }[] = []
let fullOnce: { id: string; until: number } | null = null // F with nothing playing: the video it started

function onScreen(el: Element): boolean {
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth
}

const refusedFull = () => { /* the browser said no (no full screen here, or the key was too long ago): as it was */ }

/** Full screen, or back from it. */
function full(frame: HTMLIFrameElement) {
  if (document.fullscreenElement) document.exitFullscreen().catch(refusedFull)
  else frame.requestFullscreen?.().catch(refusedFull)
}

/** A key pressed on the page, for the latest of the Feed's players on screen (feedKeys.ts). Escape, the browser's,
 * takes it out of full screen too. */
function pressed(e: KeyboardEvent) {
  const does = feedKeyOf(e)
  if (!does) return
  const p = players.findLast((q) => onScreen(q.frame))
  if (!p) return
  e.preventDefault() // Space doesn't scroll the page too
  if (does === 'sound') setSound(!soundIsOn())
  else if (does === 'full') full(p.frame)
  else {
    const yt = p.yt()
    if (!yt) return // not ready yet
    const state = yt.getPlayerState()
    if (state === PLAYING || state === BUFFERING) yt.pauseVideo()
    else yt.playVideo()
  }
}

/** F with nothing playing yet: the player `id` starts goes full screen as it comes, if it comes at once (the browser
 * lets a page go full screen only just after a key or a tap). */
export function fullScreenAsItStarts(id: string) {
  fullOnce = { id, until: Date.now() + 2000 }
}

/** Draw it once a video (`key` it by the video): a later change of sound goes through YouTube's API, not a reload. */
export function FeedPlayer({ item, playerId, bare }: { item: FeedItem; playerId: number; bare?: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [refused, setRefused] = useState(false)
  const id = item.video?.youtube_id ?? ''
  const sound = useSound()
  const muted = !sound.on || sound.held
  const [startMuted] = useState(() => !sound.on || !mayPlayAloud()) // its frame starts so; a change after goes through the API
  const want = useRef(muted) // what the page last asked of the player
  const player = useRef<{ yt: YTPlayer; quiet: number } | null>(null)
  const [self] = useState(() => ({})) // which player holds the sound back (sound.ts)

  // Muted for now though the fan has the sound on: held back till a tap on the page, here or wherever it is.
  useEffect(() => {
    if (sound.on && startMuted) holdSound(self, true)
    return () => holdSound(self, false)
  }, []) // once, as it starts
  useEffect(() => {
    if (!sound.on || !sound.held) return
    // A scroll isn't a tap; and a tap on the sound button is that button's to answer (else it would see the sound out
    // already, and mute it).
    const tapped = (e: Event) => {
      if ((e.target as Element | null)?.closest?.('[data-sound]')) return
      if (e instanceof KeyboardEvent && feedKeyOf(e) === 'sound') return // M is the sound button's key
      if (mayPlayAloud()) holdSound(self, false)
    }
    window.addEventListener('pointerup', tapped, true)
    window.addEventListener('keydown', tapped, true)
    return () => { window.removeEventListener('pointerup', tapped, true); window.removeEventListener('keydown', tapped, true) }
  }, [sound.on, sound.held])
  // The sound as it should be now; YouTube takes a moment to say it has changed, so a report meanwhile isn't heeded.
  useEffect(() => {
    want.current = muted
    const p = player.current
    if (!p) return
    p.quiet = Date.now() + 1500
    if (muted) p.yt.mute()
    else { p.yt.unMute(); if (p.yt.getVolume() === 0) p.yt.setVolume(100) }
  }, [muted])

  // One of the players the Feed's keys work, from as it comes till it goes; full screen at once if F started it.
  useEffect(() => {
    const el = frame.current
    if (!el || refused) return
    const me = { frame: el, yt: () => player.current?.yt ?? null }
    players.push(me)
    if (players.length === 1) window.addEventListener('keydown', pressed)
    if (fullOnce?.id === item.id) {
      if (Date.now() < fullOnce.until) el.requestFullscreen?.().catch(refusedFull)
      fullOnce = null
    }
    return () => {
      players.splice(players.indexOf(me), 1)
      if (!players.length) window.removeEventListener('keydown', pressed)
    }
  }, [item.id, refused])

  useEffect(() => {
    const el = frame.current
    if (!el || refused) return
    let gone = false
    let poll = 0
    youtubeApi().then((YT) => {
      if (gone) return
      const yt = new YT.Player(el, { // the player is its frame's: React removes the frame, and it goes with it
        events: {
          onReady: () => {
            if (gone) return
            const p = { yt, quiet: 0 }
            player.current = p
            if (want.current !== startMuted) { p.quiet = Date.now() + 1500; if (want.current) yt.mute(); else yt.unMute() }
            const began = Date.now()
            let aloud = false // heard playing with its sound: a mute after that is the fan's
            let fell = false // started again muted, once
            poll = window.setInterval(() => {
              const state = yt.getPlayerState()
              const isMuted = yt.isMuted()
              const going = state === PLAYING || state === BUFFERING
              if (going && !isMuted) aloud = true
              if (Date.now() < p.quiet) return
              if (!isMuted && want.current) { want.current = false; setSound(true) } // YouTube's button: sound on
              else if (isMuted && !want.current) {
                if (aloud) { want.current = true; setSound(false) } // YouTube's button: muted
                else holdSound(self, true) // the browser muted it, or wouldn't start it aloud
              } else if (!going && state !== ENDED && !want.current && !aloud && !fell && Date.now() - began > 5000) {
                fell = true; holdSound(self, true); yt.mute(); yt.playVideo() // it didn't start with its sound: muted, then
              }
            }, 400)
          },
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
    return () => { gone = true; window.clearInterval(poll); player.current = null }
  }, [id, item.id, playerId, refused, startMuted])

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
    <iframe ref={frame} src={youtubeEmbed(id, startMuted, window.location.origin, bare)} title={item.title}
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
  )
}

import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api, type WeekPoster, type WireRow } from '../api'
import { shortName } from '../format'
import { useFollowing } from '../following'
import { sportKey } from '../sports'
import { refetcher, WEEK, type Refetcher } from '../refetch'
import { longestWord, posterWhen } from '../today'
import { Rail } from './Rail'
import { sharePoster } from '../share'
import { ShareButton } from './Share'

interface Week { posters: WeekPoster[]; wire: WireRow[] }
const NONE: Week = { posters: [], wire: [] }

/**
 * This week and the Wire (GET /api/me/week): the big moments of the fan's players over the last week, biggest
 * first, as their sports word them, and the Wire's facts, line-ups and news, theirs first. A fan who follows
 * nobody still has a Wire: the best-known players' facts. Loaded when the follow list changes, and again a
 * minute or two after the stream brings a moment the fan hears about, no more than every five minutes (refetch.ts).
 */
export function useWeek(): Week {
  const { cards, loaded, heard } = useFollowing()
  const [week, setWeek] = useState<Week>(NONE)
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const asks = useRef(0)  // only the latest ask's answer is shown
  const later = useRef<Refetcher | null>(null)

  useEffect(() => {
    let gone = false
    const ask = () => {
      const n = ++asks.current
      api.week().then((r) => { if (!gone && n === asks.current) setWeek(r) }).catch(() => {})
    }
    const r = refetcher(WEEK, ask)
    later.current = r
    return () => { gone = true; r.stop() }
  }, [])

  useEffect(() => {
    if (!loaded) return
    later.current?.asked()
    const n = ++asks.current
    api.week().then((r) => { if (n === asks.current) setWeek(r) }).catch(() => {})
  }, [loaded, who])

  useEffect(() => { if (heard) later.current?.heard() }, [heard])

  return week
}

/**
 * One big moment: a block of the player's sport colour, its word big, then their name and the line. Its
 * Share button shares the game (/player/<id>?g=<event id>), whose link previews this moment.
 */
function Poster({ poster, now }: { poster: WeekPoster; now: number }) {
  return (
    <div className="poster-wrap">
      <Link to={`/player/${poster.player_id}`} className="poster" data-sport={sportKey(poster.sport)}>
        <span className="poster-tag">Yours</span>
        <span className="dsp word" style={{ '--letters': longestWord(poster.word) } as CSSProperties}>{poster.word}</span>
        <span className="stack" style={{ gap: 2 }}>
          <span className="dsp who">{shortName(poster.player)}</span>
          <span className="line">{[poster.line, posterWhen(poster.at, now)].filter(Boolean).join(' · ')}</span>
        </span>
      </Link>
      <ShareButton {...sharePoster(poster)} className="poster-share" />
    </div>
  )
}

/** "This week" on Today: the big moments of the fan's players over the last seven days, a poster each. */
export function ThisWeek({ posters, now }: { posters: WeekPoster[]; now: number }) {
  return (
    <Rail title="This week" note="The big moments" all="Every big moment this week" className="week">
      {posters.map((p) => <Poster key={`${p.player_id}:${p.event_id ?? `m${p.id}`}`} poster={p} now={now} />)}
    </Rail>
  )
}

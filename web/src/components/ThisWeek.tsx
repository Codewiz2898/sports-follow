import { useEffect, useState, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api, type WeekPoster, type WireRow } from '../api'
import { shortName } from '../format'
import { useFollowing } from '../following'
import { sportKey } from '../sports'
import { longestWord, posterWhen } from '../today'
import { Rail } from './Rail'

const SETTLE_MS = 1500  // one poll can bring several moments: fetch once they've all arrived

interface Week { posters: WeekPoster[]; wire: WireRow[] }
const NONE: Week = { posters: [], wire: [] }

/**
 * This week and the Wire for the fan's players (GET /api/me/week): the big moments of the last week,
 * biggest first, as their sports word them, and the line-ups and news, newest first. Loaded when the follow
 * list changes, and again once the stream has brought new moments.
 */
export function useWeek(): Week {
  const { cards, loaded, heard } = useFollowing()
  const [week, setWeek] = useState<Week>(NONE)
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')

  useEffect(() => {
    if (!loaded) return
    if (!who) { setWeek(NONE); return }
    let stale = false
    const timer = window.setTimeout(() => {
      api.week().then((r) => { if (!stale) setWeek(r) }).catch(() => {})
    }, heard ? SETTLE_MS : 0)
    return () => { stale = true; window.clearTimeout(timer) }
  }, [loaded, who, heard])

  return week
}

/** One big moment: a block of the player's sport colour, its word big, then their name and the line. */
function Poster({ poster, now }: { poster: WeekPoster; now: number }) {
  return (
    <Link to={`/player/${poster.player_id}`} className="poster" data-sport={sportKey(poster.sport)}>
      <span className="poster-tag">Yours</span>
      <span className="dsp word" style={{ '--letters': longestWord(poster.word) } as CSSProperties}>{poster.word}</span>
      <span className="stack" style={{ gap: 2 }}>
        <span className="dsp who">{shortName(poster.player)}</span>
        <span className="line">{[poster.line, posterWhen(poster.at, now)].filter(Boolean).join(' · ')}</span>
      </span>
    </Link>
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

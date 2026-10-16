import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Card, type LiveGame, type LiveSport } from '../api'
import { shortName } from '../format'
import { Backdrop } from '../sports'
import { gamesOn, moreOn } from '../today'
import { GameBoard, PlainScore } from './Blocks'
import { BlockError, ErrorBoundary } from './ErrorBoundary'
import { CheckIcon, ChevronIcon, PlusIcon } from './Icons'
import { Rail } from './Rail'
import { useFollowToggle } from './Starters'

const EVERY = 60_000  // the server reads a source again at most this often
const SOON = 5_000  // when it says a read is under way, the next look
const SHOWN = 6  // Also live's row: the most games before its "All N" card
const NONE: LiveSport[] = []

/** What the server last said is on: `loaded` once it has answered, `failed` while its last look failed. */
export interface LiveAnswer { sports: LiveSport[]; loaded: boolean; failed: boolean }

/**
 * Every game the sources have on, whoever follows it (GET /api/live, live.py), by sport, looked at again once
 * a minute while the page is in view. A look only reads what the server last read: it asks a source again at
 * most once a minute, whoever looks, so more fans looking costs no more of a source's allowance
 * (adapters/budget.py). A hidden tab doesn't look; showing it again does.
 */
export function useLiveAnswer(): LiveAnswer {
  const [answer, setAnswer] = useState<LiveAnswer>({ sports: NONE, loaded: false, failed: false })
  useEffect(() => {
    let current = true
    let timer: number | undefined
    let latest = 0  // the newest look: an older one's answer starts no second loop
    const look = (tries: number) => {
      window.clearTimeout(timer)
      if (document.hidden) return  // nobody's looking: no reads asked for; showing the tab again looks
      const mine = ++latest
      api.live()
        .then((r) => {
          if (!current || mine !== latest) return
          setAnswer({ sports: r.sports, loaded: true, failed: false })
          // A read was just asked for: look again in a moment, a few times, for the new scores.
          timer = window.setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => {
          if (!current || mine !== latest) return
          setAnswer((a) => (a.failed ? a : { ...a, failed: true }))
          timer = window.setTimeout(() => look(tries), EVERY)
        })
    }
    const shown = () => { if (!document.hidden) look(0) }
    document.addEventListener('visibilitychange', shown)
    look(0)
    return () => { current = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [])
  return answer
}

/** The games on now (useLiveAnswer), none until the server answers. */
export function useLive(): LiveSport[] {
  return useLiveAnswer().sports
}

type Follow = ReturnType<typeof useFollowToggle>

/** A game's players to follow, each with a tap (the server puts those already on stann first). */
function FollowPills({ game, follow }: { game: LiveGame; follow: Follow }) {
  if (!game.players.length) return null
  return (
    <div className="who" role="group" aria-label={`Follow from ${game.title}`}>
      {game.players.map((r) => {
        const on = follow.followed(r)
        return (
          <button key={r.key} type="button" className="follow-pill" aria-pressed={on} aria-busy={follow.busy(r)} aria-label={`Follow ${r.name}`} onClick={() => void follow.toggle(r)}>
            {follow.busy(r) ? <span className="spinner" /> : on ? <CheckIcon width={14} height={14} /> : <PlusIcon width={14} height={14} />}
            {shortName(r.name)}
          </button>
        )
      })}
    </div>
  )
}

/**
 * "Live now" in the welcome: a rail of every game on, with the score and a few players to follow from each
 * with a tap. Nothing while nothing is on. `first`: these sports' games come first (those a fan picked).
 */
export function LiveNow({ first = [] }: { first?: string[] }) {
  const sports = useLive()
  const follow = useFollowToggle()

  const ordered = [...sports].sort((a, b) => Number(!first.includes(a.key)) - Number(!first.includes(b.key)))
  const games = ordered.flatMap((s) => s.games.map((g) => ({ ...g, sport: s.key })))
  if (!games.length) return null
  return (
    <>
      <Rail title="Live now" note={`${games.length} on`}>
        {games.map((g) => (
          <article key={g.key} className="live-game" data-sport={g.sport} aria-label={`${g.title}, live`}>
            <span className="row" style={{ gap: 8 }}>
              <span className="badge-live">Live</span>
              {g.live.clock && <span className="eyebrow clock">{g.live.clock}</span>}
            </span>
            <span className="eyebrow">{g.competition}</span>
            <span className="dsp score">{g.live.score || g.title}</span>
            <FollowPills game={g} follow={follow} />
          </article>
        ))}
      </Rail>
      {follow.error && <div className="error-box">{follow.error}</div>}
    </>
  )
}

/** One game on Today that none of the fan's players are in: outlined, so it doesn't pass for theirs. The Live
 * page (LivePage.tsx) shows every game on as these, the fan's own under a heading of their own. */
export function OtherGame({ game, label, follow }: { game: LiveGame & { sport: string }; label: string; follow: Follow }) {
  return (
    <article className="live-other" data-sport={game.sport} aria-label={`${label}: ${game.title}, live`}>
      <Backdrop sport={game.sport} seed={game.key} />
      <span className="sport-tag">{game.competition || label}</span>
      {/* A state in a shape this app doesn't know shows as the plain score. In a row of taller cards (a chess
          board's), the board sits in the middle. */}
      <div className="game">
        <ErrorBoundary fallback={<PlainScore live={game.live} />} resetKey={game.live}>
          <GameBoard live={game.live} compact />
        </ErrorBoundary>
      </div>
      <FollowPills game={game} follow={follow} />
    </article>
  )
}

/**
 * The games on anywhere (GET /api/live) that none of the fan's players are in (gamesOn), in the order the
 * server ranks them, after the fan's own live boards (`mine`, their live cards): titled "Also live" under the
 * fan's own; with none of theirs on, it's the whole of "Live now". Today shows it to a fan who follows nobody
 * yet, to pick players from; a fan with players gets MoreLive's link instead. Nothing when nothing else is on.
 */
export function AlsoLive({ mine }: { mine: Card[] }) {
  const sports = useLive()
  const follow = useFollowToggle()
  const games = gamesOn(sports, mine).others
  if (!games.length) return null
  return (
    <ErrorBoundary fallback={<BlockError />} resetKey={sports}>
      <Rail title={mine.length ? 'Also live' : 'Live now'} note={`${games.length} on`} all="Every game on now" max={SHOWN} className="also-live">
        {games.map(({ game, label }) => <OtherGame key={game.key} game={game} label={label} follow={follow} />)}
      </Rail>
      {follow.error && <div className="error-box">{follow.error}</div>}
    </ErrorBoundary>
  )
}

/**
 * Today's way to every other game on now, under the fan's own live boards (`mine`, their live cards): a quiet
 * link to the Live page, "12 other games on now", or with none of theirs on, "12 games on now". A game of
 * theirs isn't counted (gamesOn). Nothing when nothing else is on.
 */
export function MoreLive({ mine }: { mine: Card[] }) {
  const others = gamesOn(useLive(), mine).others.length
  if (!others) return null
  return (
    <Link to="/live" className="eyebrow more-live">
      {moreOn(others, mine.length > 0)}<ChevronIcon width={16} height={16} strokeWidth={2.6} />
    </Link>
  )
}

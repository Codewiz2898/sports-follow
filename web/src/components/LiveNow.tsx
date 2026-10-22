import { Link } from 'react-router-dom'
import { api, type LiveGame, type LiveSport } from '../api'
import { shortName } from '../format'
import { sharedLook, useShared } from '../shared'
import { Backdrop } from '../sports'
import { moreOn, type GameOn } from '../today'
import { inYourOrder } from '../yourSports'
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
 * a minute while the page is in view, one look for every part of the page showing it (shared.ts). A look only
 * reads what the server last read: it asks a source again at most once a minute, whoever looks, so more fans
 * looking costs no more of a source's allowance (adapters/budget.py). A hidden tab doesn't look; showing it
 * again does.
 */
const LIVE = sharedLook(() => api.live(), EVERY, SOON, 'always')

export function useLiveAnswer(): LiveAnswer {
  const { value, failed } = useShared(LIVE)
  return { sports: value?.sports ?? NONE, loaded: value != null, failed }
}

/** The games on now (useLiveAnswer), none until the server answers. */
export function useLive(): LiveSport[] {
  return useLiveAnswer().sports
}

type Follow = ReturnType<typeof useFollowToggle>

/** A game's players to follow, each with a tap (the server puts those already on the app first): a live card's,
 * or one side's on a match page. */
export function FollowPills({ game, follow }: { game: Pick<LiveGame, 'title' | 'players'>; follow: Follow }) {
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
 * with a tap. Nothing while nothing is on. `first`: these sports' games come first, in this order (those a fan picked).
 */
export function LiveNow({ first = [] }: { first?: string[] }) {
  const sports = useLive()
  const follow = useFollowToggle()

  const ordered = inYourOrder(sports, (s) => s.key, first)
  const games = ordered.flatMap((s) => s.games.map((g) => ({ ...g, sport: s.key })))
  if (!games.length) return null
  return (
    <>
      <Rail title="Live now" note={`${games.length} on`}>
        {games.map((g) => (
          <article key={g.key} className="live-game" data-sport={g.sport} aria-label={`${g.title}, live`}>
            <MatchLink game={g} />
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

/** The whole of a live game's card opens its match page, but its follow buttons (a link can't hold them, so it
 * lies under them: .card-link). */
function MatchLink({ game }: { game: LiveGame }) {
  return game.match ? <Link to={`/match/${game.match}`} className="card-link" aria-label={`${game.title}: the match`} /> : null
}

/** One game on now that none of the fan's players are in, on Today or the Live page (LivePage.tsx):
 * outlined, so it doesn't pass for theirs. */
export function OtherGame({ game, label, follow }: { game: LiveGame & { sport: string }; label: string; follow: Follow }) {
  return (
    <article className="live-other" data-sport={game.sport} aria-label={`${label}: ${game.title}, live`}>
      <Backdrop sport={game.sport} seed={game.key} />
      <MatchLink game={game} />
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
 * The games on anywhere (GET /api/live) that none of the fan's players are in (gamesOn's others: `games`), in
 * the order the server ranks them, after the fan's own live boards (`theirsOn`): titled "Also live" under the
 * fan's own; with none of theirs on, it's the whole of "Live now". Today shows it to a fan who follows nobody
 * yet, to pick players from (narrowed to the game picked there); a fan with players gets MoreLive's link
 * instead. Nothing when nothing else is on.
 */
export function AlsoLive({ games, theirsOn = false }: { games: GameOn[]; theirsOn?: boolean }) {
  const follow = useFollowToggle()
  if (!games.length) return null
  const shownKey = games.map((x) => x.game.key).join(',')  // `games` is new each render: a card that failed tries again on another game
  return (
    <ErrorBoundary fallback={<BlockError />} resetKey={shownKey}>
      <Rail title={theirsOn ? 'Also live' : 'Live now'} note={`${games.length} on`} all="Every game on now" max={SHOWN} className="also-live">
        {games.map(({ game, label }) => <OtherGame key={game.key} game={game} label={label} follow={follow} />)}
      </Rail>
      {follow.error && <div className="error-box">{follow.error}</div>}
    </ErrorBoundary>
  )
}

/**
 * Today's way to every other game on now, under the fan's own live boards (`theirsOn`): a quiet link to the Live
 * page, "12 other games on now", or with none of theirs on, "12 games on now"; "· Valorant first" where one of the
 * fan's sports (`first`, its name) leads the page there. `games` are gamesOn's others (a game of theirs isn't
 * counted), narrowed to the game picked on Today (`game`), which the Live page is narrowed to too. Nothing when
 * nothing else is on.
 */
export function MoreLive({ games, theirsOn, game = null, first = null }: { games: GameOn[]; theirsOn: boolean; game?: string | null; first?: string | null }) {
  if (!games.length) return null
  return (
    <Link to={game ? `/live?game=${encodeURIComponent(game)}` : '/live'} className="eyebrow more-live">
      {moreOn(games.length, theirsOn)}{first ? ` · ${first} first` : ''}<ChevronIcon width={16} height={16} strokeWidth={2.6} />
    </Link>
  )
}

import { useMemo, useState, type CSSProperties } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useActivity } from '../activity'
import type { FollowedMatch, League } from '../api'
import { APP_NAME } from '../app'
import { useNow } from '../clock'
import { ComingCard, LiveScoreboard, ResultCard } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { CloseIcon, SearchIcon, UserIcon } from '../components/Icons'
import { PlayersStrip } from '../components/PlayersStrip'
import { Rail } from '../components/Rail'
import { AlsoLive, MoreLive } from '../components/LiveNow'
import { LeagueComingCard, LeagueResultCard, useLeague } from '../components/League'
import { StartersSection } from '../components/Starters'
import { ThisWeek, useWeek } from '../components/ThisWeek'
import { Wire } from '../components/Wire'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'
import { sportDrawn } from '../sports'
import { AHEAD_DAYS, comingUp, FEW, followedLive, latestIn, leagueComingUp, liveGames, notTheirs, results, resultsDay, TAIL, teamsLive, theirEvents, type TeamGame } from '../today'
import { welcomed } from './WelcomePage'

const NUDGE = 'sf-save-nudge'

/** Once a fan follows someone without an account: signing in keeps them. Dismissed for good. */
function SaveNudge() {
  const { me } = useAccount()
  const { cards, teams } = useFollowing()
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(NUDGE) === 'dismissed' } catch { return false }
  })
  if (hidden || !me || me.signed_in || cards.length + teams.length === 0) return null
  const dismiss = () => {
    setHidden(true)
    try { localStorage.setItem(NUDGE, 'dismissed') } catch { /* storage blocked */ }
  }
  return (
    <div className="notice">
      <Link to="/account" className="stack" style={{ gap: 2, flex: 1 }}>
        <span className="title">Save your players</span>
        <span className="small muted">Sign in to keep them, and their alerts, on every device.</span>
      </Link>
      <Link to="/account" className="btn small">Sign in</Link>
      <button type="button" className="btn icon" style={{ width: 32, height: 32, background: 'transparent', color: 'inherit' }} aria-label="Not now" onClick={dismiss}>
        <CloseIcon width={16} height={16} />
      </button>
    </div>
  )
}

/**
 * Today for a fan who follows nobody yet: the big results and the big games coming up in the competitions
 * stann covers (GET /api/league), outlined, each rail TAIL long and its "All N" card the rest, the sports
 * taking turns. Nothing until the server answers, and a rail with nothing in it isn't there.
 */
function BigGames({ league, now }: { league: League | null; now: number }) {
  if (!league) return null
  const done = league.results.games
  const coming = leagueComingUp(league.coming.games, new Set(), now)
  return (
    <>
      <ErrorBoundary fallback={<BlockError />} resetKey={league}>
        {done.length > 0 && (
          <Rail title="Big results" note={`${resultsDay(league.results.day)} · ${done.length}`} all="Every big result" max={TAIL}>
            {done.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />)}
          </Rail>
        )}
      </ErrorBoundary>
      <ErrorBoundary fallback={<BlockError />} resetKey={league}>
        {coming.length > 0 && (
          <Rail title="Big games coming up" note={`Next ${AHEAD_DAYS} days · ${coming.length}`} all={`Every big game in the next ${AHEAD_DAYS} days`} max={TAIL}>
            {coming.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />)}
          </Rail>
        )}
      </ErrorBoundary>
    </>
  )
}

/** The follow list didn't load: say so, rather than "Pick your players" to a fan who has some (Today, and
 * the Following page). */
export function LoadFailed() {
  const { reload } = useFollowing()
  const [trying, setTrying] = useState(false)
  const retry = () => { setTrying(true); reload().finally(() => setTrying(false)) }
  return (
    <div className="notice" role="alert">
      <span>Couldn't load your players.</span>
      <button type="button" className="btn link" onClick={retry} disabled={trying}>{trying && <span className="spinner" />}Retry</button>
    </div>
  )
}

/** The games the fan follows by themselves that are on now and none of their players is in, each its board,
 * opening its match page. */
function FollowedLive({ matches }: { matches: FollowedMatch[] }) {
  return (
    <>
      {matches.map((m) => m.live && (
        <Link key={m.event_id} to={`/match/${m.match}`} className="stack" style={{ gap: 8 }}>
          <LiveScoreboard live={m.live} playerName="" sport={m.sport} compact seed={`event:${m.event_id}`} />
          <span className="tiny muted" style={{ fontWeight: 700 }}>{m.held ? 'Following this game · alerts paused for now' : 'Following this game'}</span>
        </Link>
      ))}
    </>
  )
}

/** The games of the fan's teams that are on now and none of their players is in, nor followed by itself (those
 * are above already), each its board, opening its match page. */
function TeamsLive({ games }: { games: TeamGame[] }) {
  return (
    <>
      {games.map((g) => (
        <Link key={g.key} to={g.live.match ? `/match/${g.live.match}` : `/team/${g.team_id}`} className="stack" style={{ gap: 8 }}>
          <LiveScoreboard live={g.live} playerName="" sport={g.sport} compact seed={g.key} />
          <span className="tiny muted" style={{ fontWeight: 700 }}>Following {g.names.join(' and ')}</span>
        </Link>
      ))}
    </>
  )
}

export function TodayPage() {
  const { cards, loaded, failed, matches, matchesLoaded, teams } = useFollowing()
  const { items: alerts, seen } = useActivity()
  const week = useWeek()
  // The events the fan's players are in, once their games have loaded: a change looks at the league again.
  const theirKey = useMemo(() => (loaded ? [...theirEvents(cards, teams)].sort((a, b) => a - b).join(',') : null), [cards, teams, loaded])
  const league = useLeague(theirKey)
  const railKey = useMemo(() => [cards, teams, league], [cards, teams, league])  // a rail that failed tries again on any
  const now = useNow()
  const today = new Date(now).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })

  const header = (
    <header className="page-head home-head">
      <div className="stack home-title" style={{ gap: 6 }}>
        <span className="eyebrow">{today}</span>
        {/* The home page goes by the app's name, as the logo writes it; the date above it says which day. */}
        <h1 className="brand page-title" style={{ '--letters': APP_NAME.length } as CSSProperties}><img src="/icons/mark.svg" alt="" />{APP_NAME}</h1>
      </div>
      <div className="row mobile-only" style={{ gap: 8, paddingBottom: 4 }}>
        <InstallButton compact />
        <Link to="/search" className="btn icon" aria-label="Search players"><SearchIcon width={20} height={20} /></Link>
        <Link to="/account" className="btn icon" aria-label="Account"><UserIcon width={20} height={20} /></Link>
      </div>
    </header>
  )

  // A fan who follows only games by themselves, or only teams, has a Today of their own too.
  if (loaded && matchesLoaded && cards.length === 0 && matches.length === 0 && teams.length === 0) {
    if (!welcomed()) return <Navigate to="/welcome" replace />  // a new fan: the welcome first, once
    return (
      <>
        {header}
        <AlsoLive mine={[]} />
        <BigGames league={league} now={now} />
        {/* Who's out and who's back among the best-known players. */}
        <ErrorBoundary fallback={<BlockError />} resetKey={week}>
          <Wire rows={week.wire} now={now} />
        </ErrorBoundary>
        <section className="hero" data-sport="other" aria-label="Get started">
          <span className="dsp" style={{ fontSize: 48 }}>Pick your players</span>
          <p style={{ fontWeight: 600, maxWidth: '36ch' }}>Any athlete in any sport: the live score and their own numbers while they play, then fixtures, results and news.</p>
          <Link to="/search" className="btn" style={{ alignSelf: 'flex-start', background: 'var(--on-sport)', color: 'var(--sport)', borderColor: 'var(--on-sport)' }}><SearchIcon width={18} height={18} />Find a player</Link>
        </section>
        <StartersSection />
      </>
    )
  }

  const games = liveGames(cards)
  const followed = followedLive(matches, cards)
  const teamGames = teamsLive(teams, cards, matches)
  const done = results(cards, now, sportDrawn, teams)
  const coming = comingUp(cards, now, matches, teams)
  // After the fan's own, the big games none of their players are in, outlined. Once the follow list has
  // answered: a rail of only big games that the fan's own then come in front of would stay on the big ones
  // (a snapped row keeps its card in view), and one of the fan's own would show as a big game until then.
  // The games they follow by themselves are theirs too: on Today as such, not again among the big ones.
  const theirs = new Set([...theirEvents(cards, teams), ...matches.map((m) => m.event_id)])
  const big = loaded || failed ? league : null
  const bigDone = big ? notTheirs(big.results.games, theirs) : []
  const bigComing = big ? leagueComingUp(big.coming.games, theirs, now) : []
  const resultCount = (done?.games.length ?? 0) + bigDone.length
  // The day the rail's results are from: the fan's, the big ones', or, when those differ, the latest of each.
  const ownDay = done && (done.day ?? 'Since yesterday')
  const bigDay = bigDone.length ? resultsDay(league?.results.day ?? null) : null
  const resultsWhen = ownDay && bigDay && ownDay !== bigDay ? 'Latest' : ownDay ?? bigDay ?? ''
  const comingCount = coming.length + bigComing.length
  // This week is the whole week's big moments, games on the Results rail too.
  const posters = week.posters

  return (
    <>
      {header}
      {cards.length + teams.length > 0 && (
        <ErrorBoundary fallback={<div className="mobile-only"><BlockError /></div>} resetKey={railKey}>
          <PlayersStrip cards={cards} teams={teams} now={now} />
        </ErrorBoundary>
      )}
      {failed && !loaded && <LoadFailed />}

      {/* Each opens its match page; a live score the research agent read names no game, and opens the player's page. */}
      {games.map(([key, group]) => (
        <Link key={group[0].player_id} to={group[0].live.match ? `/match/${group[0].live.match}` : `/player/${group[0].player_id}`} className="stack" style={{ gap: 8 }}>
          <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} compact seed={key} latest={latestIn(alerts, group, seen)} />
          {group.length > 1 && <span className="tiny muted" style={{ fontWeight: 700 }}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</span>}
        </Link>
      ))}
      {/* Then the games they follow by themselves, nobody of theirs in them. */}
      <ErrorBoundary fallback={<BlockError />} resetKey={followed}>
        <FollowedLive matches={followed} />
      </ErrorBoundary>
      {/* And their teams' games, nobody of theirs in them, not followed by themselves. */}
      <ErrorBoundary fallback={<BlockError />} resetKey={teamGames}>
        <TeamsLive games={teamGames} />
      </ErrorBoundary>
      {/* Only the fan's own games are on Today; every other game on now is a tap away, on the Live page. Once the
          follow list has answered, so a game of the fan's is never counted as another. */}
      {(loaded || failed) && <MoreLive mine={games.flatMap(([, group]) => group)} />}

      <ErrorBoundary fallback={<BlockError />} resetKey={railKey}>
        {resultCount > 0 && (
          <Rail title={done ? 'Results' : 'Big results'} note={`${resultsWhen} · ${resultCount}`}
            all={done ? `Every result ${done.day ? `on ${done.day}` : 'since yesterday'}${bigDone.length ? ', and the big ones' : ''}` : 'Every big result'} max={(done?.games.length ?? 0) + TAIL}>
            {/* With nothing live, the biggest result leads the page. */}
            {done?.games.map((g, i) => <ResultCard key={g.key} game={g} hero={i === 0 && !games.length} now={now} />)}
            {bigDone.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />)}
          </Rail>
        )}
      </ErrorBoundary>

      <SaveNudge />

      <ErrorBoundary fallback={<BlockError />} resetKey={railKey}>
        {comingCount > 0 && (
          <Rail title={coming.length ? 'Coming up' : 'Big games coming up'} note={`Next ${AHEAD_DAYS} days · ${comingCount}`}
            all={`Every ${coming.length ? '' : 'big '}game in the next ${AHEAD_DAYS} days${coming.length && bigComing.length ? ', and the big ones' : ''}`} max={coming.length + TAIL}>
            {coming.map((g) => <ComingCard key={g.key} event={g.event} players={g.players} seed={g.key} sport={g.sport} teams={g.teams} />)}
            {bigComing.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />)}
          </Rail>
        )}
      </ErrorBoundary>

      {/* Side by side on a desktop, when there are both. */}
      {(posters.length > 0 || week.wire.length > 0) && (
        <div className={`week-wire${posters.length && week.wire.length ? ' pair' : ''}`}>
          <ErrorBoundary fallback={<BlockError />} resetKey={week}>
            {posters.length > 0 && <ThisWeek posters={posters} now={now} />}
          </ErrorBoundary>
          <ErrorBoundary fallback={<BlockError />} resetKey={week}>
            <Wire rows={week.wire} now={now} />
          </ErrorBoundary>
        </div>
      )}

      {cards.length > 0 && cards.length < FEW && <StartersSection />}
    </>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { useActivity } from '../activity'
import type { FollowedMatch, League, LeagueGame, MyPredictions } from '../api'
import { APP_NAME } from '../app'
import { useNow } from '../clock'
import { BoardLink, ComingCard, LiveScoreboard, ResultCard } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { FromYourPlayers } from '../components/Feed'
import { findGames, gameRow, gamesOnToday, narrow, NO_BLOCKS, nothingOn, pickedGame, theirGames, type GameRow, type TodayBlocks } from '../filter'
import { CloseIcon, SearchIcon, UserIcon } from '../components/Icons'
import { useCalls } from '../components/Calls'
import { PlayersStrip } from '../components/PlayersStrip'
import { Rail } from '../components/Rail'
import { useNarrowSide } from '../components/Side'
import { AlsoLive, MoreLive, useLive } from '../components/LiveNow'
import { LeagueComingCard, LeagueResultCard, useLeague } from '../components/League'
import { StartersSection } from '../components/Starters'
import { ThisWeek, useWeek } from '../components/ThisWeek'
import { Wire } from '../components/Wire'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'
import { listedSports, sportDrawn, sportKey, type SportInfo } from '../sports'
import { AHEAD_DAYS, comingUp, FEW, followedLive, gamesOn, latestIn, leagueComingUp, liveGames, notTheirs, results, resultsDay, TAIL, teamsLive, theirEvents, type TeamGame } from '../today'
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
 * the app covers (GET /api/league), outlined, each rail TAIL long and its "All N" card the rest, the sports
 * taking turns. `done` and `coming` are the league's, narrowed to the game picked. Nothing until the server
 * answers, and a rail with nothing in it isn't there.
 */
function BigGames({ league, done, coming, now, calls }: { league: League | null; done: LeagueGame[]; coming: LeagueGame[]; now: number; calls: MyPredictions | null }) {
  if (!league) return null
  return (
    <>
      <ErrorBoundary fallback={<BlockError />} resetKey={league}>
        {done.length > 0 && (
          <Rail title="Big results" note={`${resultsDay(league.results.day)} · ${done.length}`} all="Every big result" max={TAIL}>
            {done.map((g) => <LeagueResultCard key={g.key} game={g} now={now} calls={calls} />)}
          </Rail>
        )}
      </ErrorBoundary>
      <ErrorBoundary fallback={<BlockError />} resetKey={league}>
        {coming.length > 0 && (
          <Rail title="Big games coming up" note={`Next ${AHEAD_DAYS} days · ${coming.length}`} all={`Every big game in the next ${AHEAD_DAYS} days`} max={TAIL}>
            {coming.map((g) => <LeagueComingCard key={g.key} game={g} now={now} calls={calls} />)}
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
        <BoardLink key={m.event_id} to={`/match/${m.match}`} label={`${m.title}: the match`}>
          <LiveScoreboard live={m.live} playerName="" sport={m.sport} compact seed={`event:${m.event_id}`} />
          <span className="tiny muted" style={{ fontWeight: 700 }}>{m.held ? 'Following this game · alerts paused for now' : 'Following this game'}</span>
        </BoardLink>
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
        <BoardLink key={g.key} to={g.live.match ? `/match/${g.live.match}` : `/team/${g.team_id}`} label={g.live.match ? `${g.live.event || g.names.join(' and ')}: the match` : g.names.join(' and ')}>
          <LiveScoreboard live={g.live} playerName="" sport={g.sport} teams={g.names} compact seed={g.key} />
          <span className="tiny muted" style={{ fontWeight: 700 }}>Following {g.names.join(' and ')}</span>
        </BoardLink>
      ))}
    </>
  )
}

/**
 * The game picked on Today (?game=<a sport's key>), while it's one of the row's (`row`: a chip, or behind its
 * search); else none: All. A pick replaces the address rather than adding to the history, so Back doesn't step
 * through the chips, and nothing keeps it past the visit: Today opens on All. A game picked that has left the page
 * since (its live game over) leaves the address too, so it can't come back by itself; one not on the page yet
 * (Today still loading, or come back to from a player's page) is waited for.
 */
function useGame(row: GameRow): [SportInfo | null, (key: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const asked = params.get('game')
  const game = pickedGame(asked, row)
  const pick = useCallback((key: string | null) => {
    setParams((p) => {
      const next = new URLSearchParams(p)
      if (key) next.set('game', key)
      else next.delete('game')
      return next
    }, { replace: true })
  }, [setParams])
  const shown = useRef<string | null>(null)  // the game last shown picked
  useEffect(() => {
    if (game) shown.current = game.key
    else if (asked && asked === shown.current) {
      shown.current = null
      pick(null)
    }
  }, [game, asked, pick])
  return [game, pick]
}

/**
 * Today's game filter, under its header: All, a chip of each of the fan's own games on the page, and a search for
 * the others (filter.ts gameRow); one of those picked has its chip after the fan's own while it's picked. The game
 * picked is a block of its colour and the rest outlined, All picked is ink. Not there while the page holds one game
 * or none. A game picked off the row's edge (far along it, from a link or come back to) is brought into it: the row
 * scrolls sideways, never the page, which a fan come back to Today is put back down. All is first, so never off it.
 */
function GameChips({ row, game, onPick }: { row: GameRow; game: SportInfo | null; onPick: (key: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [finding, setFinding] = useState(false)
  const picked = game?.key
  useEffect(() => {
    const line = box.current
    const chip = line?.querySelector<HTMLElement>('[aria-pressed="true"]')
    if (!line || !chip || !picked) return
    const left = chip.getBoundingClientRect().left - line.getBoundingClientRect().left + line.scrollLeft
    if (left < line.scrollLeft || left + chip.offsetWidth > line.scrollLeft + line.clientWidth) {
      line.scrollTo({ left: left - (line.clientWidth - chip.offsetWidth) / 2 })
    }
  }, [picked])
  if (!row.chips.length && !row.more.length) return null
  const chips = game && !row.chips.includes(game) ? [...row.chips, game] : row.chips
  return (
    <div className="game-filter">
      <div ref={box} className="game-chips" role="group" aria-label="Show games">
        <button type="button" className="game-chip" aria-pressed={!game} onClick={() => onPick(null)}>All</button>
        {chips.map((s) => (
          <button key={s.key} type="button" className="game-chip" data-sport={s.key} aria-pressed={game?.key === s.key} onClick={() => onPick(s.key)}>
            <span className="dot" aria-hidden="true" />{s.label}
          </button>
        ))}
      </div>
      {/* At the row's end, outside its scroll, so it's in sight however many games the fan has. */}
      {row.more.length > 0 && (
        <button type="button" className="game-chip find" aria-label="Find another game" aria-haspopup="dialog" onClick={() => setFinding(true)}>
          <SearchIcon width={18} height={18} />
        </button>
      )}
      {finding && <FindGame games={row.more} onPick={onPick} onClose={() => setFinding(false)} />}
    </div>
  )
}

/** The row's search: the other games with something on Today, narrowed as the fan types; a tap shows that one. From
 * the bottom on a phone, a box in the middle on a desktop, as the Feed's "…" sheet. */
function FindGame({ games, onPick, onClose }: { games: SportInfo[]; onPick: (key: string) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [words, setWords] = useState('')
  useEffect(() => {
    const d = dialog.current
    if (d && !d.open) d.showModal()
  }, [])
  const found = findGames(games, words)
  const choose = (key: string) => {
    onPick(key)
    dialog.current?.close()
  }
  return (
    // A tap on the backdrop (the dialog itself, outside its box) closes it, as Escape does.
    <dialog ref={dialog} className="feed-sheet find-game" aria-label="Find a game" onClose={onClose} onClick={(e) => { if (e.target === dialog.current) dialog.current?.close() }}>
      <div className="feed-sheet-box">
        <span className="eyebrow">More games on today</span>
        <input type="search" className="field" placeholder="Search games" aria-label="Search games" value={words} onChange={(e) => setWords(e.target.value)} />
        <div className="game-list">
          {found.map((s) => (
            <button key={s.key} type="button" className="game-chip" data-sport={s.key} onClick={() => choose(s.key)}>
              <span className="dot" aria-hidden="true" />{s.label}
            </button>
          ))}
          {!found.length && <span className="muted small">No game by that name on today</span>}
        </div>
        <button type="button" className="btn primary" onClick={() => dialog.current?.close()}>Close</button>
      </div>
    </dialog>
  )
}

/** The game picked has nothing on Today but the fan's players in it (the strip, a phone's alone): say so, and the
 * way back to every game. "Games": posts of theirs may still be From your players, at the foot. */
function NothingIn({ game, onAll }: { game: SportInfo; onAll: () => void }) {
  return (
    <div className="notice" role="status">
      <span>No {game.label} games today</span>
      <button type="button" className="btn small" onClick={onAll}>All games</button>
    </div>
  )
}

export function TodayPage() {
  const { cards, loaded, failed, matches, matchesLoaded, teams } = useFollowing()
  const { items: alerts, seen } = useActivity()
  const week = useWeek()
  const live = useLive()
  // The events the fan's players are in, once their games have loaded: a change looks at the league again.
  const theirKey = useMemo(() => (loaded ? [...theirEvents(cards, teams)].sort((a, b) => a - b).join(',') : null), [cards, teams, loaded])
  const league = useLeague(theirKey)
  // The fan's calls (predictions.py), on their games' cards: "Call it" opens a game's page, where they call it.
  const calls = useCalls(theirKey)
  const railKey = useMemo(() => [cards, teams, league], [cards, teams, league])  // a rail that failed tries again on any
  const now = useNow()
  const today = new Date(now).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })

  // A fan who follows only games by themselves, or only teams, has a Today of their own too.
  const nobody = loaded && matchesLoaded && cards.length === 0 && matches.length === 0 && teams.length === 0
  const games = liveGames(cards)
  const done = results(cards, now, sportDrawn, teams)
  // After the fan's own, the big games none of their players are in, outlined. Once the follow list has
  // answered: a rail of only big games that the fan's own then come in front of would stay on the big ones
  // (a snapped row keeps its card in view), and one of the fan's own would show as a big game until then.
  // The games they follow by themselves are theirs too: on Today as such, not again among the big ones.
  const theirs = new Set([...theirEvents(cards, teams), ...matches.map((m) => m.event_id)])
  const big = loaded || failed ? league : null
  // Everything Today shows, block by block; then only the game picked's (filter.ts).
  const all: TodayBlocks = nobody ? {
    ...NO_BLOCKS,
    alsoLive: gamesOn(live, []).others,
    bigResults: league?.results.games ?? [],
    bigComing: league ? leagueComingUp(league.coming.games, new Set(), now) : [],
    wire: week.wire,
  } : {
    cards, teams,
    live: games,
    followed: followedLive(matches, cards),
    teamGames: teamsLive(teams, cards, matches),
    alsoLive: [],
    // Only once the follow list has answered, so a game of the fan's is never counted as another.
    moreLive: loaded || failed ? gamesOn(live, games.flatMap(([, group]) => group)).others : [],
    results: done?.games ?? [],
    bigResults: big ? notTheirs(big.results.games, theirs) : [],
    coming: comingUp(cards, now, matches, teams),
    bigComing: big ? leagueComingUp(big.coming.games, theirs, now) : [],
    // This week is the whole week's big moments, games on the Results rail too.
    posters: week.posters,
    wire: week.wire,
  }
  const row = gameRow(listedSports(), gamesOnToday(all, sportKey), theirGames(all, sportKey))
  const [game, pick] = useGame(row)
  const shown = narrow(all, game?.key ?? null, sportKey)
  useNarrowSide(game?.key ?? null)  // a wide screen's side column: its scores and games starting soon too
  const shownKey = useMemo(() => [railKey, game?.key], [railKey, game?.key])  // and a rail that failed tries again on a pick

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
  const filter = <GameChips row={row} game={game} onPick={pick} />

  if (nobody) {
    if (!welcomed()) return <Navigate to="/welcome" replace />  // a new fan: the welcome first, once
    return (
      <>
        {header}
        {filter}
        <AlsoLive games={shown.alsoLive} />
        <BigGames league={league} done={shown.bigResults} coming={shown.bigComing} now={now} calls={calls} />
        {/* Who's out and who's back among the best-known players. */}
        <ErrorBoundary fallback={<BlockError />} resetKey={week}>
          <Wire rows={shown.wire} now={now} />
        </ErrorBoundary>
        <section className="hero plain" aria-label="Get started">
          <span className="dsp" style={{ fontSize: 48 }}>Pick your players</span>
          <p style={{ fontWeight: 600, maxWidth: '36ch' }}>Any athlete in any sport: the live score and their own numbers while they play, then fixtures, results and news.</p>
          <Link to="/search" className="btn primary" style={{ alignSelf: 'flex-start' }}><SearchIcon width={18} height={18} />Find a player</Link>
        </section>
        <StartersSection />
      </>
    )
  }

  const own = shown.results
  const resultCount = own.length + shown.bigResults.length
  // The day the rail's results are from: the fan's, the big ones', or, when those differ, the latest of each.
  const ownDay = own.length ? done?.day ?? 'Since yesterday' : null
  const bigDay = shown.bigResults.length ? resultsDay(league?.results.day ?? null) : null
  const resultsWhen = ownDay && bigDay && ownDay !== bigDay ? 'Latest' : ownDay ?? bigDay ?? ''
  const comingCount = shown.coming.length + shown.bigComing.length
  const { posters, wire } = shown

  return (
    <>
      {header}
      {filter}
      {shown.cards.length + shown.teams.length > 0 && (
        <ErrorBoundary fallback={<div className="mobile-only"><BlockError /></div>} resetKey={shownKey}>
          <PlayersStrip cards={shown.cards} teams={shown.teams} now={now} />
        </ErrorBoundary>
      )}
      {failed && !loaded && <LoadFailed />}
      {game && nothingOn(shown) && <NothingIn game={game} onAll={() => pick(null)} />}

      {/* Each opens its match page; a live score the research agent read names no game, and opens the player's page. */}
      {shown.live.map(([key, group]) => (
        <BoardLink key={group[0].player_id} to={group[0].live.match ? `/match/${group[0].live.match}` : `/player/${group[0].player_id}`}
          label={group[0].live.match ? `${group[0].live.event || group[0].player.name}: the match` : group[0].player.name}>
          <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} lines={group.map((c) => c.live.player_stats)} compact seed={key} latest={latestIn(alerts, group, seen)} />
          {group.length > 1 && <span className="tiny muted" style={{ fontWeight: 700 }}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</span>}
        </BoardLink>
      ))}
      {/* Then the games they follow by themselves, nobody of theirs in them. */}
      <ErrorBoundary fallback={<BlockError />} resetKey={shown.followed}>
        <FollowedLive matches={shown.followed} />
      </ErrorBoundary>
      {/* And their teams' games, nobody of theirs in them, not followed by themselves. */}
      <ErrorBoundary fallback={<BlockError />} resetKey={shown.teamGames}>
        <TeamsLive games={shown.teamGames} />
      </ErrorBoundary>
      {/* Only the fan's own games are on Today; every other game on now is a tap away, on the Live page. */}
      <MoreLive games={shown.moreLive} theirsOn={shown.live.length > 0} />

      <ErrorBoundary fallback={<BlockError />} resetKey={shownKey}>
        {resultCount > 0 && (
          <Rail title={own.length ? 'Results' : 'Big results'} note={`${resultsWhen} · ${resultCount}`}
            all={own.length ? `Every result ${done?.day ? `on ${done.day}` : 'since yesterday'}${shown.bigResults.length ? ', and the big ones' : ''}` : 'Every big result'} max={own.length + TAIL}>
            {/* With nothing live, the biggest result leads the page. */}
            {own.map((g, i) => <ResultCard key={g.key} game={g} hero={i === 0 && !shown.live.length} now={now} calls={calls} />)}
            {shown.bigResults.map((g) => <LeagueResultCard key={g.key} game={g} now={now} calls={calls} />)}
          </Rail>
        )}
      </ErrorBoundary>

      <SaveNudge />

      <ErrorBoundary fallback={<BlockError />} resetKey={shownKey}>
        {comingCount > 0 && (
          <Rail title={shown.coming.length ? 'Coming up' : 'Big games coming up'} note={`Next ${AHEAD_DAYS} days · ${comingCount}`}
            all={`Every ${shown.coming.length ? '' : 'big '}game in the next ${AHEAD_DAYS} days${shown.coming.length && shown.bigComing.length ? ', and the big ones' : ''}`} max={shown.coming.length + TAIL}>
            {shown.coming.map((g) => <ComingCard key={g.key} event={g.event} players={g.players} seed={g.key} sport={g.sport} teams={g.teams} calls={calls} />)}
            {shown.bigComing.map((g) => <LeagueComingCard key={g.key} game={g} now={now} calls={calls} />)}
          </Rail>
        )}
      </ErrorBoundary>

      {/* Side by side on a desktop, when there are both. */}
      {(posters.length > 0 || wire.length > 0) && (
        <div className={`week-wire${posters.length && wire.length ? ' pair' : ''}`}>
          <ErrorBoundary fallback={<BlockError />} resetKey={week}>
            {posters.length > 0 && <ThisWeek posters={posters} now={now} />}
          </ErrorBoundary>
          <ErrorBoundary fallback={<BlockError />} resetKey={week}>
            <Wire rows={wire} now={now} />
          </ErrorBoundary>
        </div>
      )}

      {/* What fans and the players themselves shared: after the day's facts, two posts tall, so they don't push
          what's next down the page; the game picked's alone. */}
      <ErrorBoundary fallback={<BlockError />} resetKey={shownKey}>
        <FromYourPlayers now={now} game={game?.key ?? null} />
      </ErrorBoundary>

      {cards.length > 0 && cards.length < FEW && <StartersSection />}
    </>
  )
}

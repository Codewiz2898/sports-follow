import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useActivity } from '../activity'
import type { ActivityItem, Card, UpcomingEvent } from '../api'
import { useNow } from '../clock'
import { ComingCard, highlight, LiveScoreboard, ResultCard, type GameResult, type LatestMoment } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { CloseIcon, SearchIcon, UserIcon } from '../components/Icons'
import { PlayersStrip } from '../components/PlayersStrip'
import { Rail } from '../components/Rail'
import { StartersSection } from '../components/Starters'
import { ThisWeek, useWeek } from '../components/ThisWeek'
import { ahead, daysAgo, lineStats, parseDate, playedOn } from '../format'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'
import { welcomed } from './WelcomePage'

const NUDGE = 'sf-save-nudge'
const FEW = 3  // until a fan follows this many, Today keeps offering starters
const AHEAD_DAYS = 14  // how far Coming up looks

/** Once a fan follows someone without an account: signing in keeps them. Dismissed for good. */
function SaveNudge() {
  const { me } = useAccount()
  const { cards } = useFollowing()
  const [hidden, setHidden] = useState(() => {
    try { return localStorage.getItem(NUDGE) === 'dismissed' } catch { return false }
  })
  if (hidden || !me || me.signed_in || cards.length === 0) return null
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

/** Group live cards by the game they're in, so two followed players in one match show as one game. Keyed by
 * the game, which also turns its line art its own way. */
function liveGames(cards: Card[]): [string, Card[]][] {
  const games = new Map<string, Card[]>()
  for (const c of cards.filter((c) => c.status === 'ready' && c.live.is_live)) {
    // A structured source names the event; the agent's text only matches when it's written the same way.
    const key = c.live.event_id != null ? `event:${c.live.event_id}` : (c.live.event || c.live.score || String(c.player_id)).toLowerCase()
    games.set(key, [...(games.get(key) ?? []), c])
  }
  return [...games.entries()]
}

/** The newest moment of the fan's players in a live game (the alerts list is newest first), and whether the
 * fan missed it: it came after they last looked at their alerts, from a game they haven't muted. Only a
 * structured score names its game. */
function latestIn(items: ActivityItem[], group: Card[], seen: number): LatestMoment | null {
  const game = group[0].live.event_id
  if (game == null) return null
  const ids = new Set(group.map((c) => c.player_id))
  const m = items.find((i) => i.event_id === game && ids.has(i.player_id))
  return m ? { text: [m.title, m.body].filter(Boolean).join(' · '), missed: m.id > seen && !m.muted } : null
}

interface Coming { key: string; event: UpcomingEvent; players: Card[] }

/** Every game of the fan's players in the next two weeks, soonest first, each once with everyone of theirs
 * who plays in it. One that has started has left: it's live, or it's a result. */
function comingUp(cards: Card[], now: number): Coming[] {
  const merged = new Map<string, Coming>()
  for (const c of cards) {
    for (const u of c.upcoming ?? []) {
      const start = parseDate(u.start_utc)
      if (!start || !ahead(u.start_utc, now) || start.getTime() - now > AHEAD_DAYS * 864e5) continue
      const key = u.event_id != null ? `event:${u.event_id}` : `${u.title.toLowerCase()}|${start.toDateString()}`
      const found = merged.get(key)
      if (!found) merged.set(key, { key, event: u, players: [c] })
      else if (!found.players.includes(c)) found.players.push(c)
    }
  }
  return [...merged.values()].sort((a, b) => parseDate(a.event.start_utc)!.getTime() - parseDate(b.event.start_utc)!.getTime())
}

/** How big a player's line is: the sport has a word for it (a triple-double, a hundred), it's numbers, or neither. */
function weight(p: GameResult['players'][number]): number {
  const line = p.result.player_contribution
  return highlight(p.sport, line) ? 2 : lineStats(line) ? 1 : 0
}

/**
 * The fan's results for the rail: every game of their players since yesterday where the fan is, else the
 * latest day that had any (`day`, its date; null since yesterday). A game two of their players were in is one card. The biggest
 * lines go first (a triple-double or a hundred, then any game with the player's numbers), then the newest.
 * Each card carries all its results (the follow list sends them), and the stream updates it as a game ends.
 */
function results(cards: Card[], now: number): { games: GameResult[]; day: string | null } | null {
  const today = new Date(now)
  const all = cards.flatMap((c) => (c.recent_results ?? []).flatMap((r) => {
    // A game dated after today (an agent's slip) isn't a result yet.
    const days = daysAgo(playedOn(r), today)
    return days != null && days >= 0 ? [{ days, player: { result: { ...r, player_id: c.player_id, player_name: c.player.name }, sport: c.player.sport, teams: c.player.teams } }] : []
  }))
  if (!all.length) return null
  const latest = Math.min(...all.map((x) => x.days))
  const shown = latest <= 1 ? all.filter((x) => x.days <= 1) : all.filter((x) => x.days === latest)
  const games = new Map<string, GameResult>()
  for (const { player } of shown) {
    const r = player.result
    const key = r.event_id != null ? `event:${r.event_id}` : `${r.title.toLowerCase()}|${parseDate(playedOn(r))!.toDateString()}`
    const game = games.get(key)
    if (!game) games.set(key, { key, players: [player] })
    else if (!game.players.some((p) => p.result.player_id === r.player_id)) game.players.push(player)
  }
  const time = (g: GameResult) => Math.max(...g.players.map((p) => parseDate(playedOn(p.result))?.getTime() ?? 0))
  for (const g of games.values()) g.players.sort((a, b) => weight(b) - weight(a))
  const ranked = [...games.values()].sort((a, b) => weight(b.players[0]) - weight(a.players[0]) || time(b) - time(a))
  const day = latest <= 1 ? null : parseDate(playedOn(shown[0].player.result))!.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  return { games: ranked, day }
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

export function TodayPage() {
  const { cards, loaded, failed } = useFollowing()
  const { items: alerts, seen } = useActivity()
  const posters = useWeek()
  const now = useNow()
  const today = new Date(now).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })

  const header = (
    <header className="page-head">
      <div className="stack" style={{ gap: 6 }}>
        <span className="eyebrow">{today}</span>
        <h1 className="dsp page-title">Today</h1>
      </div>
      <div className="row mobile-only" style={{ gap: 8, paddingBottom: 4 }}>
        <InstallButton compact />
        <Link to="/search" className="btn icon" aria-label="Search players"><SearchIcon width={20} height={20} /></Link>
        <Link to="/account" className="btn icon" aria-label="Account"><UserIcon width={20} height={20} /></Link>
      </div>
    </header>
  )

  if (loaded && cards.length === 0) {
    if (!welcomed()) return <Navigate to="/welcome" replace />  // a new fan: the welcome first, once
    return (
      <>
        {header}
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
  const done = results(cards, now)
  const coming = comingUp(cards, now)
  // This week leaves out the games the Results rail has.
  const onResults = new Set((done?.games ?? []).flatMap((g) => g.players.flatMap((p) => (p.result.event_id != null ? [p.result.event_id] : []))))

  return (
    <>
      {header}
      {cards.length > 0 && (
        <ErrorBoundary fallback={<div className="mobile-only"><BlockError /></div>} resetKey={cards}>
          <PlayersStrip cards={cards} now={now} />
        </ErrorBoundary>
      )}
      {failed && !loaded && <LoadFailed />}

      {games.map(([key, group]) => (
        <Link key={group[0].player_id} to={`/player/${group[0].player_id}`} className="stack" style={{ gap: 8 }}>
          <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} compact seed={key} latest={latestIn(alerts, group, seen)} />
          {group.length > 1 && <span className="tiny muted" style={{ fontWeight: 700 }}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</span>}
        </Link>
      ))}

      <ErrorBoundary fallback={<BlockError />} resetKey={cards}>
        {done && (
          <Rail title="Results" note={`${done.day ?? 'Since yesterday'} · ${done.games.length}`} all={done.day ? `Every result on ${done.day}` : 'Every result since yesterday'}>
            {/* With nothing live, the biggest result leads the page. */}
            {done.games.map((g, i) => <ResultCard key={g.key} game={g} hero={i === 0 && !games.length} now={now} />)}
          </Rail>
        )}
      </ErrorBoundary>

      <SaveNudge />

      <ErrorBoundary fallback={<BlockError />} resetKey={cards}>
        {coming.length > 0 && (
          <Rail title="Coming up" note={`Next ${AHEAD_DAYS} days · ${coming.length}`} all={`Every game in the next ${AHEAD_DAYS} days`}>
            {coming.map((g) => <ComingCard key={g.key} event={g.event} players={g.players} seed={g.key} />)}
          </Rail>
        )}
      </ErrorBoundary>

      <ErrorBoundary fallback={<BlockError />} resetKey={posters}>
        <ThisWeek posters={posters} shown={onResults} now={now} />
      </ErrorBoundary>

      {cards.length > 0 && cards.length < FEW && <StartersSection />}
    </>
  )
}

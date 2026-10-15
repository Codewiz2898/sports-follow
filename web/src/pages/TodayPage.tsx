import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import type { Card, LatestResult, UpcomingEvent } from '../api'
import { useNow } from '../clock'
import { highlight, LiveScoreboard, NextStrip, PlayerTile, ResultHero, SectionHead } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { CloseIcon, PlusIcon, SearchIcon, UserIcon } from '../components/Icons'
import { StartersSection } from '../components/Starters'
import { ahead, daysAgo, lineStats, parseDate, playedOn } from '../format'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'
import { welcomed } from './WelcomePage'

const NUDGE = 'sf-save-nudge'
const FEW = 3  // until a fan follows this many, Today keeps offering starters

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

/** Group live cards by the game they're in, so two followed players in one match show as one game. */
function liveGames(cards: Card[]) {
  const games = new Map<string, Card[]>()
  for (const c of cards.filter((c) => c.status === 'ready' && c.live.is_live)) {
    // A structured source names the event; the agent's text only matches when it's written the same way.
    const key = c.live.event_id != null ? `event:${c.live.event_id}` : (c.live.event || c.live.score || String(c.player_id)).toLowerCase()
    games.set(key, [...(games.get(key) ?? []), c])
  }
  return [...games.values()]
}

type Next = UpcomingEvent & { players: string[]; card: Card }

/** The soonest game any of your players has, with everyone of yours who plays in it. Once it starts, the one after. */
function upNext(cards: Card[], now: number): Next | null {
  const merged = new Map<string, Next>()
  for (const c of cards) {
    for (const u of c.upcoming ?? []) {
      const start = parseDate(u.start_utc)
      if (!start || !ahead(u.start_utc, now)) continue
      const key = u.event_id != null ? `event:${u.event_id}` : `${u.title.toLowerCase()}|${start.toDateString()}`
      const found = merged.get(key)
      if (found) found.players.push(c.player.name)
      else merged.set(key, { ...u, players: [c.player.name], card: c })
    }
  }
  return [...merged.values()].sort((a, b) => parseDate(a.start_utc)!.getTime() - parseDate(b.start_utc)!.getTime())[0] ?? null
}

/** Live first, then whoever plays soonest, then the rest in the order they were followed. */
function tileOrder(cards: Card[], now: number) {
  const nextAt = (c: Card) => Math.min(Infinity, ...(c.upcoming ?? []).filter((u) => ahead(u.start_utc, now)).map((u) => parseDate(u.start_utc)!.getTime()))
  return [...cards].sort((a, b) => Number(b.live.is_live) - Number(a.live.is_live) || nextAt(a) - nextAt(b))
}

/**
 * With nothing live, the day's big moment: a player's game from today or yesterday, a triple-double
 * or a hundred first, then any game with the player's numbers, then the newest. Each card carries
 * its newest result, and the stream updates it the moment a game ends.
 */
function bigMoment(cards: Card[], now: number): { result: LatestResult; sport: string } | null {
  const recent = cards.flatMap((c) => {
    const r = c.recent_results?.[0]
    // Today or yesterday where the fan is; a game dated tomorrow (an agent's slip) isn't today's moment.
    const days = daysAgo(playedOn(r), new Date(now))
    return r && days != null && days >= 0 && days <= 1 ? [{ result: { ...r, player_id: c.player_id, player_name: c.player.name }, sport: c.player.sport }] : []
  })
  const rank = (m: { result: LatestResult; sport: string }) => (highlight(m.sport, m.result.player_contribution) ? 2 : lineStats(m.result.player_contribution) ? 1 : 0)
  const time = (m: { result: LatestResult }) => parseDate(playedOn(m.result))?.getTime() ?? 0
  return recent.reduce<(typeof recent)[number] | null>((best, m) => (!best || rank(m) > rank(best) || (rank(m) === rank(best) && time(m) > time(best)) ? m : best), null)
}

/** The follow list didn't load: say so, rather than "Pick your players" to a fan who has some. */
function LoadFailed() {
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
  const { cards, loaded, failed, progress } = useFollowing()
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
  const next = upNext(cards, now)
  const moment = games.length ? null : bigMoment(cards, now)

  return (
    <>
      {header}
      {failed && !loaded && <LoadFailed />}

      {games.map((group) => (
        <Link key={group[0].player_id} to={`/player/${group[0].player_id}`} className="stack" style={{ gap: 8 }}>
          <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} compact />
          {group.length > 1 && <span className="tiny muted" style={{ fontWeight: 700 }}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</span>}
        </Link>
      ))}

      <ErrorBoundary fallback={<BlockError />} resetKey={cards}>
        {moment && <ResultHero result={moment.result} sport={moment.sport} />}
      </ErrorBoundary>

      <SaveNudge />

      <ErrorBoundary fallback={<BlockError />} resetKey={cards}>
        {next && <NextStrip event={next} playerId={next.card.player_id} sport={next.card.player.sport} />}
      </ErrorBoundary>

      {cards.length > 0 && (
        <section className="section" aria-label="Your players">
          <SectionHead title="Your players"><span className="tag muted">{cards.length}</span></SectionHead>
          <ErrorBoundary fallback={<BlockError />} resetKey={cards}>
            <div className="tiles">
              {tileOrder(cards, now).map((c) => <PlayerTile key={c.player_id} card={c} progress={progress[c.player_id]} />)}
              <Link to="/search" className="tile add" aria-label="Follow another player"><PlusIcon width={22} height={22} /><span className="dsp">Follow</span></Link>
            </div>
          </ErrorBoundary>
        </section>
      )}

      {cards.length > 0 && cards.length < FEW && <StartersSection />}
    </>
  )
}

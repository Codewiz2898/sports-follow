import { Link } from 'react-router-dom'
import type { Card, NewsItem, UpcomingEvent } from '../api'
import { LiveScoreboard, NewsList, PlayerRow, UpcomingList } from '../components/Blocks'
import { BellIcon, SearchIcon } from '../components/Icons'
import { parseDate } from '../format'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'

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

function upNext(cards: Card[]) {
  const merged = new Map<string, UpcomingEvent & { players: string[] }>()
  const now = Date.now() - 3 * 3600e3
  for (const c of cards) {
    for (const u of c.upcoming ?? []) {
      const start = parseDate(u.start_utc)
      if (start && start.getTime() < now) continue
      const key = u.event_id != null ? `event:${u.event_id}` : `${u.title.toLowerCase()}|${start?.toDateString() ?? ''}`
      const found = merged.get(key)
      if (found) found.players.push(c.player.name)
      else merged.set(key, { ...u, players: [c.player.name] })
    }
  }
  return [...merged.values()]
    .sort((a, b) => (parseDate(a.start_utc)?.getTime() ?? Infinity) - (parseDate(b.start_utc)?.getTime() ?? Infinity))
    .slice(0, 6)
}

function latest(cards: Card[]) {
  const merged = new Map<string, NewsItem & { players: string[] }>()
  for (const c of cards) {
    for (const n of c.news ?? []) {
      const key = n.url || n.headline
      const found = merged.get(key)
      if (found) found.players.push(c.player.name)
      else merged.set(key, { ...n, players: [c.player.name] })
    }
  }
  return [...merged.values()]
    .sort((a, b) => (parseDate(b.published)?.getTime() ?? 0) - (parseDate(a.published)?.getTime() ?? 0))
    .slice(0, 6)
}

export function FollowingPage() {
  const { cards, loaded, progress } = useFollowing()
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })

  if (loaded && cards.length === 0) {
    return (
      <>
        <header className="stack">
          <span className="eyebrow">{today}</span>
          <h1 className="page-title">Following</h1>
        </header>
        <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <span className="title">Follow your first player</span>
          <p className="muted small" style={{ margin: 0 }}>Any athlete in any sport: live score and their own numbers while they play, fixtures, news and stats.</p>
          <Link to="/search" className="btn primary" style={{ alignSelf: 'flex-start' }}><SearchIcon width={18} height={18} />Find a player</Link>
        </div>
        <div className="mobile-only"><InstallButton compact /></div>
      </>
    )
  }

  const games = liveGames(cards)
  const nextUp = upNext(cards)
  const news = latest(cards)

  return (
    <>
      <header className="page-head">
        <div className="stack">
          <span className="eyebrow">{today}</span>
          <h1 className="page-title">Following</h1>
        </div>
        <div className="row mobile-only" style={{ gap: 8 }}>
          <InstallButton compact />
          <Link to="/notifications" className="btn icon" aria-label="Notifications"><BellIcon /></Link>
          <Link to="/search" className="btn icon" aria-label="Search players"><SearchIcon /></Link>
        </div>
      </header>

      {games.length > 0 && (
        <section className="section" aria-label="Live now">
          <div className="section-head">
            <span className="eyebrow">Live now</span>
            <span className="tiny muted">{games.flat().length} of your players</span>
          </div>
          {games.map((group) => (
            <Link key={group[0].player_id} to={`/player/${group[0].player_id}`} style={{ color: 'var(--text)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <LiveScoreboard live={group[0].live} playerName={group[0].player.name} compact />
              {group.length > 1 && <span className="tiny muted">Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</span>}
            </Link>
          ))}
        </section>
      )}

      <section className="section" aria-label="Your players">
        <span className="eyebrow">Your players</span>
        <div className="card" style={{ padding: 6 }}>
          {cards.map((c) => <PlayerRow key={c.player_id} card={c} progress={progress[c.player_id]} />)}
        </div>
      </section>

      <section className="section" aria-label="Up next">
        <span className="eyebrow">Up next</span>
        <UpcomingList items={nextUp} who empty="No upcoming games found for your players yet." />
      </section>

      <section className="section" aria-label="Latest news">
        <span className="eyebrow">Latest</span>
        <NewsList items={news} who empty="No news yet." />
      </section>
    </>
  )
}

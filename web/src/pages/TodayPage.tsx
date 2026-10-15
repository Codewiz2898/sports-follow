import { useState, type CSSProperties } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useActivity } from '../activity'
import { APP_NAME } from '../app'
import { useNow } from '../clock'
import { ComingCard, LiveScoreboard, ResultCard } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { CloseIcon, SearchIcon, UserIcon } from '../components/Icons'
import { PlayersStrip } from '../components/PlayersStrip'
import { Rail } from '../components/Rail'
import { AlsoLive } from '../components/LiveNow'
import { StartersSection } from '../components/Starters'
import { ThisWeek, useWeek } from '../components/ThisWeek'
import { Wire } from '../components/Wire'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { InstallButton } from '../pwa'
import { sportKey } from '../sports'
import { AHEAD_DAYS, comingUp, FEW, latestIn, liveGames, results } from '../today'
import { welcomed } from './WelcomePage'

const NUDGE = 'sf-save-nudge'

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
  const week = useWeek()
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

  if (loaded && cards.length === 0) {
    if (!welcomed()) return <Navigate to="/welcome" replace />  // a new fan: the welcome first, once
    return (
      <>
        {header}
        <AlsoLive mine={[]} />
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
  const done = results(cards, now, sportKey)
  const coming = comingUp(cards, now)
  // This week is the whole week's big moments, games on the Results rail too.
  const posters = week.posters

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
      {/* Then every other game on now, outlined: the section is gone when nothing is on anywhere. Once the follow
          list has answered, so a game of the fan's never shows here first. */}
      {(loaded || failed) && <AlsoLive mine={games.flatMap(([, group]) => group)} />}

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

import { Link } from 'react-router-dom'
import { SectionHead } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon } from '../components/Icons'
import { OtherGame, useLiveAnswer } from '../components/LiveNow'
import { useFollowToggle } from '../components/Starters'
import { useFollowing } from '../following'
import { bySport, gamesOn, liveGames, type GameOn } from '../today'

/** One heading's games, laid out as a grid of Today's outlined cards. */
function Games({ title, games, follow }: { title: string; games: GameOn[]; follow: ReturnType<typeof useFollowToggle> }) {
  return (
    <section className="section" aria-label={title}>
      <SectionHead title={title}><span className="eyebrow">{games.length} on</span></SectionHead>
      <div className="live-grid">
        {games.map(({ game, label }) => <OtherGame key={game.key} game={game} label={label} follow={follow} />)}
      </div>
    </section>
  )
}

/**
 * Every game on now, whoever follows it (GET /api/live): Today's link to the games none of the fan's players
 * are in opens it, and so does the desktop sidebar. The fan's own come first, under "Your players", then each
 * sport's in the order the server ranks them, as Today's outlined cards with players to follow from each.
 * Looked at again once a minute while the tab is in view, as Today does (useLiveAnswer).
 */
export function LivePage() {
  const { sports, loaded, failed } = useLiveAnswer()
  const following = useFollowing()
  const follow = useFollowToggle()
  // Once the follow list has answered too, so none of the fan's games shows under its sport first.
  const ready = loaded && (following.loaded || following.failed)
  const { theirs, others } = gamesOn(sports, liveGames(following.cards).flatMap(([, group]) => group))
  const count = theirs.length + others.length
  return (
    <>
      <header className="stack" style={{ gap: 6 }}>
        <Link to="/" className="eyebrow back-link"><BackIcon width={14} height={14} strokeWidth={2.6} />Today</Link>
        <h1 className="dsp page-title">Live now</h1>
        {ready && count > 0 && <span className="eyebrow">{count} on{theirs.length ? ` · ${theirs.length} yours` : ''}</span>}
      </header>
      {!ready && (failed
        ? <div className="notice" role="alert">Couldn't load the games on now. Trying again in a minute.</div>
        : <div className="card empty small"><span className="spinner" /> Loading…</div>)}
      {ready && count === 0 && <p className="muted">Nothing's on right now.</p>}
      {ready && (
        <ErrorBoundary fallback={<BlockError />} resetKey={sports}>
          {theirs.length > 0 && <Games title="Your players" games={theirs} follow={follow} />}
          {bySport(others).map((g) => <Games key={g.sport} title={g.label} games={g.games} follow={follow} />)}
        </ErrorBoundary>
      )}
      {follow.error && <div className="error-box">{follow.error}</div>}
    </>
  )
}

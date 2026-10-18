import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useNow } from '../clock'
import { PlayerTile, TeamTile } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon, PlusIcon } from '../components/Icons'
import { tileOrder, useFollowing } from '../following'
import { LoadFailed } from './TodayPage'

/** Every team and player the fan follows, as a grid of their sport's colours (Today's strip opens it; on a
 * desktop, the sidebar's heading): their teams first, then their players. Names only: photos are on a player's
 * own page. */
export function FollowingPage() {
  const { cards, loaded, failed, progress, teams } = useFollowing()
  const now = useNow()
  const live = cards.filter((c) => c.live.is_live).length + teams.filter((t) => t.live?.is_live).length
  const shown = useMemo(() => ({ cards, teams }), [cards, teams])  // a newer list of either tries again after a bad tile
  const counted = [cards.length && `${cards.length} ${cards.length === 1 ? 'player' : 'players'}`, teams.length && `${teams.length} ${teams.length === 1 ? 'team' : 'teams'}`].filter(Boolean)
  return (
    <>
      <header className="stack" style={{ gap: 6 }}>
        <Link to="/" className="eyebrow back-link"><BackIcon width={14} height={14} strokeWidth={2.6} />Today</Link>
        <h1 className="dsp page-title">Following</h1>
        {(loaded || cards.length > 0) && <span className="eyebrow">{counted.join(' · ') || '0 players'}{live ? ` · ${live} live` : ''}</span>}
      </header>
      {failed && !loaded && <LoadFailed />}
      {loaded && cards.length === 0 && teams.length === 0 && <p className="muted">You don't follow anyone yet. Find a player or a team to see their games here.</p>}
      {(loaded || cards.length > 0) && (
        <ErrorBoundary fallback={<BlockError />} resetKey={shown}>
          <div className="tiles">
            {teams.map((t) => <TeamTile key={`team-${t.team_id}`} team={t} />)}
            {tileOrder(cards, now).map((c) => <PlayerTile key={c.player_id} card={c} progress={progress[c.player_id]} />)}
            <Link to="/search" className="tile add" aria-label="Follow another player or a team"><PlusIcon width={22} height={22} /><span className="dsp">Follow</span></Link>
          </div>
        </ErrorBoundary>
      )}
    </>
  )
}

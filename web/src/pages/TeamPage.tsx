import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useActivity } from '../activity'
import { APP_NAME } from '../app'
import { api, ApiError, type TeamPage as Team } from '../api'
import { useNow } from '../clock'
import { FormStrip, LiveScoreboard, PlayerBand, ResultsList, SectionHead, UpcomingList } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon, BellIcon, CheckIcon, PlusIcon } from '../components/Icons'
import { FollowPills } from '../components/LiveNow'
import { ShareButton } from '../components/Share'
import { useFollowToggle } from '../components/Starters'
import { relative } from '../format'
import { useFollowing } from '../following'
import { EVERY, nextLook } from '../match'
import { AlertLevels, LEVELS, teamLevelText } from '../notifications'
import { shareTeam } from '../share'
import { sportName } from '../sports'

/**
 * A team's page from the server (GET /api/teams/<id>), looked at again while it's in view, sooner while the
 * server says a read of its games is on its way (a team nobody followed yet is read when its page opens). A team
 * the server doesn't have is an error, and isn't asked about again.
 */
function useTeam(teamId: number): { team: Team | null; failed: string | null; again: () => void } {
  const [team, setTeam] = useState<Team | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [asked, setAsked] = useState(0)
  useEffect(() => {
    setFailed(null)
    if (!Number.isFinite(teamId)) { setFailed('No such team.'); return }
    let current = true
    let gone = false
    let timer: number | undefined
    const look = (tries: number) => {
      window.clearTimeout(timer)
      if (document.hidden || gone) return
      api.team(teamId)
        .then((t) => {
          if (!current) return
          setTeam(t)
          setFailed(null)
          const next = nextLook(t.refreshing, tries)
          timer = window.setTimeout(() => look(next.tries), next.wait)
        })
        .catch((e: Error) => {
          if (!current) return
          setFailed(e.message)
          gone = e instanceof ApiError && e.status === 404
          if (!gone) timer = window.setTimeout(() => look(tries), EVERY)
        })
    }
    const shown = () => { if (!document.hidden) look(0) }
    document.addEventListener('visibilitychange', shown)
    look(0)
    return () => { current = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [teamId, asked])
  return { team: team?.team_id === teamId ? team : null, failed, again: () => setAsked((n) => n + 1) }
}

/**
 * One team (sports_follow/teams.py): a block of its sport's colour with its name, what it plays in, Follow and,
 * once followed, its alert level and "Mute this game" for its game on now. Then its game on now (the board, to
 * its match page), its games to come and its last results from its side with its form, and its squad, each
 * player followed with a tap. Following the team doesn't follow its players.
 */
export function TeamPage() {
  const params = useParams()
  const teamId = /^\d+$/.test(params.id ?? '') ? Number(params.id) : NaN
  const { team, failed, again } = useTeam(teamId)
  const following = useFollowing()
  // A followed team's games read anew (a follow's first read, a page that asked) reach Following too: its tile,
  // and which of its games the fan stream carries.
  // Once a read: Following's own functions change with every list it loads, so they're read through a ref.
  const read = team?.fetched_at
  const followingNow = useRef(following)
  followingNow.current = following
  useEffect(() => { if (read && followingNow.current.isFollowingTeam(teamId)) void followingNow.current.reload() }, [read, teamId])
  const { muted, setMuted } = useActivity()
  const follow = useFollowToggle()
  const now = useNow()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const back = <Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>

  if (!team) {
    return failed
      ? <div className="stack" style={{ gap: 12 }}>{back}<div className="error-box" role="alert">{failed}</div></div>
      : <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>
  }
  // This app's word on following it, and its level, over the page's last look: a tap shows at once.
  const mine = following.teams.find((t) => t.team_id === team.team_id)
  const isFollowing = following.loaded ? mine != null : team.following
  const level = mine?.alerts ?? team.alerts
  const live = team.live?.is_live ? team.live : null
  const attempt = (work: () => Promise<unknown>) => { setError(null); return work().catch((e: Error) => setError(e.message)) }
  const toggle = async () => {
    setBusy(true)
    await attempt(async () => {
      await following.followTeam(team.team_id, !isFollowing)
      if (isFollowing) setOpen(false)
      again()
    })
    setBusy(false)
  }

  return (
    <>
      <PlayerBand
        name={team.name}
        sport={team.sport}
        meta={[team.detail, team.followers ? `${team.followers} following` : ''].filter(Boolean).join(' · ')}
        back={back}
        actions={
          <>
            <ShareButton {...shareTeam(team.team_id, team.name, APP_NAME)} />
            {isFollowing && (
              <button type="button" className="btn icon" aria-label={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
                <BellIcon width={20} height={20} />
              </button>
            )}
            {(team.can_follow || isFollowing) && (
              <button type="button" className="btn" onClick={toggle} disabled={busy} aria-pressed={isFollowing}>
                {busy && <span className="spinner" />}
                {isFollowing ? <><CheckIcon width={16} height={16} />Following</> : <><PlusIcon width={16} height={16} />Follow</>}
              </button>
            )}
          </>
        }
      >
        {open && isFollowing && (
          <div className="alert-panel" style={{ color: 'var(--text)' }}>
            <span className="row">
              <span className="title">Alerts for {team.name}</span>
              <Link to="/notifications?tab=settings" className="link small">Settings</Link>
            </span>
            <AlertLevels label={`Alerts for ${team.name}`} value={level} onChange={(l) => { attempt(() => following.setTeamAlerts(team.team_id, l)) }} />
            <span className="small muted">{teamLevelText(level, team.sport)}</span>
            {live?.event_id != null && (() => {
              const eventId = live.event_id
              const on = muted.has(eventId)
              return (
                <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} aria-pressed={on} onClick={() => { attempt(() => setMuted(eventId, !on)) }}>
                  {on ? 'Unmute this game' : 'Mute this game'}
                </button>
              )
            })()}
          </div>
        )}
      </PlayerBand>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!team.can_follow && team.note && <p className="small muted" role="status">{team.note}</p>}
      {failed && <div className="notice" role="status">Couldn't update this team: {failed}</div>}

      <ErrorBoundary fallback={<BlockError />} resetKey={team}>
        {/* The board links to its match page and its source itself: no link around it. */}
        {live && <LiveScoreboard live={live} playerName="" sport={team.sport} />}

        <section className="section" aria-label="Coming up">
          <SectionHead title="Coming up" />
          {team.refreshing && !team.upcoming.length && !team.recent.length
            ? <div className="card empty small row" style={{ justifyContent: 'flex-start' }}><span className="spinner" />Reading its games…</div>
            : <UpcomingList items={team.upcoming.filter((u) => u.event_id !== live?.event_id)} empty="No games coming up found." />}
        </section>

        {team.form.length >= 3 && (
          <div className="row card card-pad">
            <span className="stack" style={{ gap: 2 }}>
              <span className="eyebrow">Form</span>
              <span className="small" style={{ fontWeight: 700 }}>Last {team.form.length}, newest first</span>
            </span>
            <FormStrip marks={team.form} />
          </div>
        )}

        <section className="section" aria-label="Results">
          <SectionHead title="Results" />
          <ResultsList items={team.recent} teams={[team.name]} />
        </section>

        {team.squad.length > 0 && (
          <section className="section" aria-label="Squad">
            <SectionHead title="Squad" />
            <div className="card card-pad stack" data-sport={team.sport}>
              <span className="small muted">Follow a player for their own moments, as well as the team's.</span>
              <FollowPills game={{ title: team.name, players: team.squad }} follow={follow} />
            </div>
            {follow.error && <div className="error-box">{follow.error}</div>}
          </section>
        )}
      </ErrorBoundary>

      <p className="tiny muted">
        {team.fetched_at ? `${sportName(team.sport) || 'Games'} from ${team.source}, read ${relative(team.fetched_at, now)}. ` : `From ${team.source}. `}
        <Link to="/credits" className="link">Credits</Link>
      </p>
    </>
  )
}

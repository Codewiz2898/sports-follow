import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAccount } from '../account'
import { useActivity } from '../activity'
import type { ActivityItem, AlertLevel } from '../api'
import { useNow } from '../clock'
import { Avatar } from '../components/Blocks'
import { ChevronIcon, UserIcon } from '../components/Icons'
import { dayLabel, relative } from '../format'
import { useFollowing } from '../following'
import { DEVICE, nothingYet } from '../alerts'
import { AlertLevels, gameLevelText, levelText, teamLevelText, usePush } from '../notifications'

/** Alerts: what's happened to your players (Latest), and what reaches this device (Settings). */
export function NotificationsPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'settings' ? 'settings' : 'latest'
  const show = (t: 'latest' | 'settings') => setParams(t === 'settings' ? { tab: t } : {}, { replace: true })

  return (
    <>
      <h1 className="dsp page-title">Alerts</h1>
      <div className="segmented" role="group" aria-label="Alerts">
        <button type="button" aria-pressed={tab === 'latest'} onClick={() => show('latest')}>Latest</button>
        <button type="button" aria-pressed={tab === 'settings'} onClick={() => show('settings')}>Settings</button>
      </div>
      {tab === 'latest' ? <Latest onSettings={() => show('settings')} /> : <Settings />}
      <span className="row tiny muted" style={{ justifyContent: 'flex-start' }}>
        <Link to="/credits" className="link">Credits and sources</Link>
        <Link to="/privacy" className="link">Privacy</Link>
      </span>
    </>
  )
}

/** Every moment of the fan's players, newest first, by day; what came since they last looked is marked. */
function Latest({ onSettings }: { onSettings: () => void }) {
  const { cards, matches, teams } = useFollowing()
  const activity = useActivity()
  // What was new when the list opened stays marked while it's open; the badge clears at once.
  const [seenAtOpen] = useState(activity.seen)
  const { markSeen } = activity
  useEffect(() => { markSeen() }, [markSeen])
  const now = useNow()
  const sportOf = (id: number) => cards.find((c) => c.player_id === id)?.player.sport

  if (!activity.loaded) return <div className="card empty small"><span className="spinner" /></div>
  if (!activity.items.length) {
    return (
      <div className="card empty small stack" style={{ alignItems: 'center' }}>
        {/* A fan of a team alone, or a game, hears its moments too: Settings has their levels. */}
        <span>{nothingYet(cards.length, teams.length, matches.length)}</span>
        {cards.length + teams.length + matches.length > 0 && <button type="button" className="btn link" onClick={onSettings}>Choose what reaches this phone</button>}
      </div>
    )
  }
  const days: [string, ActivityItem[]][] = []
  for (const item of activity.items) {
    const label = dayLabel(item.at, new Date(now))
    if (days[days.length - 1]?.[0] === label) days[days.length - 1][1].push(item)
    else days.push([label, [item]])
  }
  return (
    <>
      {days.map(([label, items]) => (
        <section key={label} className="section" aria-label={label}>
          <span className="eyebrow">{label}</span>
          <ul className="card list">
            {items.map((item) => <LatestRow key={item.id} item={item} sport={item.player_id != null ? sportOf(item.player_id) : item.sport} fresh={item.id > seenAtOpen} />)}
          </ul>
        </section>
      ))}
      {activity.more && <MoreButton more={activity.more} />}
    </>
  )
}

function LatestRow({ item, sport, fresh }: { item: ActivityItem; sport?: string | null; fresh: boolean }) {
  const now = useNow()
  const [context] = item.body.split('\n')
  return (
    <li className={`alert-row${fresh ? ' fresh' : ''}${item.level === 'minor' ? ' minor' : ''}`}>
      {/* A game's own moment (a game followed by itself) wears its sport, by the game's name. */}
      <Avatar name={item.player ?? item.event ?? ''} sport={sport} size="sm" />
      <Link to={item.url} className="stack" style={{ gap: 2, minWidth: 0, flex: 1 }}>
        <span className="title">{item.title}</span>
        {context && <span className="small muted">{context}</span>}
        <span className="tiny muted">{[!context?.startsWith(item.event ?? '\0') && item.event, relative(item.at, now), item.muted && 'Muted'].filter(Boolean).join(' · ')}</span>
      </Link>
      {fresh && <span className="fresh-dot" aria-label="New" />}
    </li>
  )
}

function MoreButton({ more }: { more: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  return (
    <button type="button" className="btn" style={{ alignSelf: 'center' }} disabled={busy} onClick={() => { setBusy(true); more().finally(() => alive.current && setBusy(false)) }}>
      {busy && <span className="spinner" />}Show more
    </button>
  )
}

/** Signed in or not: the way to keep these settings on every device. */
function AccountRow() {
  const { me } = useAccount()
  if (!me) return null
  return (
    <Link to="/account" className="card row" style={{ padding: '14px 18px' }}>
      <span className="row" style={{ justifyContent: 'flex-start' }}>
        <UserIcon width={20} height={20} />
        <span className="stack">
          <span className="title">{me.signed_in ? me.email : 'Sign in'}</span>
          <span className="small muted">{me.signed_in ? 'These settings are the same on every device you sign in on.' : 'Keep your players and these settings on every device.'}</span>
        </span>
      </span>
      <ChevronIcon width={18} height={18} style={{ flexShrink: 0 }} />
    </Link>
  )
}

/** On or off for this device, its quiet hours, and how much to hear about each player, each team and each game
 * followed by itself. */
function Settings() {
  const push = usePush()
  const { cards, setAlerts, matches, setMatchAlerts, teams, setTeamAlerts } = useFollowing()
  const [failed, setFailed] = useState<Record<number, string>>({})
  const [gameFailed, setGameFailed] = useState<Record<number, string>>({})
  const [teamFailed, setTeamFailed] = useState<Record<number, string>>({})
  const chooseTeam = (teamId: number, level: AlertLevel) => {
    setTeamFailed((f) => ({ ...f, [teamId]: '' }))
    setTeamAlerts(teamId, level).catch((e: Error) => setTeamFailed((f) => ({ ...f, [teamId]: e.message })))
  }
  const chooseGame = (eventId: number, level: AlertLevel) => {
    setGameFailed((f) => ({ ...f, [eventId]: '' }))
    setMatchAlerts(eventId, level).catch((e: Error) => setGameFailed((f) => ({ ...f, [eventId]: e.message })))
  }
  const choose = (playerId: number, level: AlertLevel) => {
    setFailed((f) => ({ ...f, [playerId]: '' }))
    setAlerts(playerId, level).catch((e: Error) => setFailed((f) => ({ ...f, [playerId]: e.message })))
  }
  const [start, setStart] = useState(push.quiet.start ?? '22:00')
  const [end, setEnd] = useState(push.quiet.end ?? '07:00')
  const quietOn = Boolean(push.quiet.start && push.quiet.end)

  return (
    <>
      <AccountRow />
      <section className="card" aria-label="This device" style={{ padding: '4px 18px' }}>
        <div className="row" style={{ padding: '14px 0', borderBottom: push.on ? '1px solid var(--line)' : 0 }}>
          <span className="stack">
            <span className="title">On this device</span>
            <span className="small muted">
              {push.unavailable ?? (push.on ? DEVICE.on : DEVICE.off)}
            </span>
          </span>
          {!push.unavailable && (
            <button type="button" role="switch" className="switch" aria-checked={push.on} aria-label="Alerts on this device" disabled={push.busy} onClick={push.on ? push.disable : push.enable} />
          )}
        </div>
        {push.on && (
          <>
            <div className="row" style={{ padding: '14px 0', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
              <span className="stack">
                <span className="title">Quiet hours</span>
                <span className="small muted">{DEVICE.quiet}</span>
              </span>
              <button type="button" role="switch" className="switch" aria-checked={quietOn} aria-label="Quiet hours" onClick={() => push.saveQuiet(quietOn ? { start: null, end: null } : { start, end })} />
              <span className="row" style={{ gap: 8, justifyContent: 'flex-start', width: '100%' }}>
                <input className="time" type="time" aria-label="Quiet from" value={start} onChange={(e) => setStart(e.target.value)} onBlur={() => quietOn && push.saveQuiet({ start, end })} />
                <span className="small muted">to</span>
                <input className="time" type="time" aria-label="Quiet until" value={end} onChange={(e) => setEnd(e.target.value)} onBlur={() => quietOn && push.saveQuiet({ start, end })} />
              </span>
            </div>
            <button type="button" className="btn link" style={{ height: 48, fontSize: 14 }} disabled={push.busy} onClick={push.test}>Send a test alert</button>
          </>
        )}
        {push.message && <p className="small" style={{ paddingBottom: 14, fontWeight: 700 }}>{push.message}</p>}
      </section>

      <section className="section" aria-label="Your players">
        <div className="section-head">
          <h2 className="dsp section-title">Per player</h2>
          <span className="tiny muted" style={{ fontWeight: 700 }}>Key moments is the default</span>
        </div>
        {cards.length === 0 ? (
          <div className="card empty small">Follow a player to choose what you hear about them.</div>
        ) : (
          <ul className="card list">
            {cards.map((c) => {
              const level = c.alerts ?? 'key'
              return (
                <li key={c.player_id} className="stack" style={{ gap: 10 }}>
                  <span className="row" style={{ justifyContent: 'flex-start' }}>
                    <Avatar name={c.player.name} sport={c.player.sport} size="sm" />
                    <span className="stack" style={{ gap: 0 }}>
                      <Link to={`/player/${c.player_id}`} className="title">{c.player.name}</Link>
                      <span className="tiny muted">{levelText(level, c.player.sport).brief}</span>
                    </span>
                  </span>
                  <AlertLevels label={`Alerts for ${c.player.name}`} value={level} onChange={(l) => choose(c.player_id, l)} />
                  {failed[c.player_id] && <div className="error-box" role="alert">{failed[c.player_id]}</div>}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* The teams followed (sports_follow/teams.py): each of their games, from both sides, and their results. */}
      {teams.length > 0 && (
        <section className="section" aria-label="Your teams">
          <div className="section-head">
            <h2 className="dsp section-title">Per team</h2>
            <span className="tiny muted" style={{ fontWeight: 700 }}>Every game of theirs</span>
          </div>
          <ul className="card list">
            {teams.map((t) => (
              <li key={t.team_id} className="stack" style={{ gap: 10 }}>
                <span className="row" style={{ justifyContent: 'flex-start' }}>
                  <Avatar name={t.name} sport={t.sport} size="sm" />
                  <span className="stack" style={{ gap: 0 }}>
                    <Link to={`/team/${t.team_id}`} className="title">{t.name}</Link>
                    <span className="tiny muted">{teamLevelText(t.alerts, t.sport)}</span>
                  </span>
                </span>
                <AlertLevels label={`Alerts for ${t.name}`} value={t.alerts} onChange={(l) => chooseTeam(t.team_id, l)} />
                {teamFailed[t.team_id] && <div className="error-box" role="alert">{teamFailed[t.team_id]}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* The games followed by themselves, until each ends (sports_follow/matches.py). */}
      {matches.length > 0 && (
        <section className="section" aria-label="Your games">
          <div className="section-head">
            <h2 className="dsp section-title">Per game</h2>
            <span className="tiny muted" style={{ fontWeight: 700 }}>Until each one ends</span>
          </div>
          <ul className="card list">
            {matches.map((m) => (
              <li key={m.event_id} className="stack" style={{ gap: 10 }}>
                <span className="row" style={{ justifyContent: 'flex-start' }}>
                  <Avatar name={m.title} sport={m.sport} size="sm" />
                  <span className="stack" style={{ gap: 0 }}>
                    <Link to={`/match/${m.match}`} className="title">{m.title}</Link>
                    <span className="tiny muted">{gameLevelText(m.alerts, m.sport)}</span>
                  </span>
                </span>
                <AlertLevels label={`Alerts for ${m.title}`} value={m.alerts} onChange={(l) => chooseGame(m.event_id, l)} />
                {gameFailed[m.event_id] && <div className="error-box" role="alert">{gameFailed[m.event_id]}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useActivity } from '../activity'
import type { ActivityItem, AlertLevel } from '../api'
import { useNow } from '../clock'
import { Avatar } from '../components/Blocks'
import { dayLabel, relative } from '../format'
import { useFollowing } from '../following'
import { AlertLevels, levelText, usePush } from '../notifications'

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
      <Link to="/credits" className="tiny muted link" style={{ alignSelf: 'flex-start' }}>Credits and sources</Link>
    </>
  )
}

/** Every moment of the fan's players, newest first, by day; what came since they last looked is marked. */
function Latest({ onSettings }: { onSettings: () => void }) {
  const { cards } = useFollowing()
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
        <span>{cards.length ? 'Nothing yet. Line-ups, goals, wickets and results for your players show up here as they happen.' : 'Follow a player to see their moments here.'}</span>
        {cards.length > 0 && <button type="button" className="btn link" onClick={onSettings}>Choose what reaches this phone</button>}
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
            {items.map((item) => <LatestRow key={item.id} item={item} sport={sportOf(item.player_id)} fresh={item.id > seenAtOpen} />)}
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
      <Avatar name={item.player} sport={sport} size="sm" />
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

/** On or off for this device, its quiet hours, and how much to hear about each player. */
function Settings() {
  const push = usePush()
  const { cards, setAlerts } = useFollowing()
  const [failed, setFailed] = useState<Record<number, string>>({})
  const choose = (playerId: number, level: AlertLevel) => {
    setFailed((f) => ({ ...f, [playerId]: '' }))
    setAlerts(playerId, level).catch((e: Error) => setFailed((f) => ({ ...f, [playerId]: e.message })))
  }
  const [start, setStart] = useState(push.quiet.start ?? '22:00')
  const [end, setEnd] = useState(push.quiet.end ?? '07:00')
  const quietOn = Boolean(push.quiet.start && push.quiet.end)

  return (
    <>
      <section className="card" aria-label="This device" style={{ padding: '4px 18px' }}>
        <div className="row" style={{ padding: '14px 0', borderBottom: push.on ? '1px solid var(--line)' : 0 }}>
          <span className="stack">
            <span className="title">On this device</span>
            <span className="small muted">
              {push.unavailable ?? (push.on ? 'One alert per game, updated as it goes. Only the big moments make a sound.' : 'Turn on to hear about your players even with the app closed.')}
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
                <span className="small muted">Alerts still arrive, without sound.</span>
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
    </>
  )
}

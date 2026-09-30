import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Avatar } from '../components/Blocks'
import { BackIcon } from '../components/Icons'
import { useFollowing } from '../following'
import { AlertSelect, LEVELS, usePush } from '../notifications'

/** Notifications: on or off for this device, its quiet hours, and how much to hear about each player. */
export function NotificationsPage() {
  const push = usePush()
  const { cards, setAlerts } = useFollowing()
  const [start, setStart] = useState(push.quiet.start ?? '22:00')
  const [end, setEnd] = useState(push.quiet.end ?? '07:00')
  const quietOn = Boolean(push.quiet.start && push.quiet.end)

  return (
    <>
      <div className="page-head">
        <div className="row" style={{ justifyContent: 'flex-start' }}>
          <Link to="/" className="btn icon" aria-label="Back"><BackIcon /></Link>
          <h1 className="page-title">Notifications</h1>
        </div>
      </div>

      <section className="card card-pad stack" style={{ gap: 12 }} aria-label="This device">
        <div className="row">
          <div className="stack">
            <span className="title">On this device</span>
            <span className="small muted">
              {push.unavailable ?? (push.on ? 'On. Goals, fifties, big games and results reach you even with the app closed.' : 'Off. Turn on to hear about your players even with the app closed.')}
            </span>
          </div>
          {!push.unavailable && (
            push.on
              ? <button type="button" className="btn" disabled={push.busy} onClick={push.disable}>Turn off</button>
              : <button type="button" className="btn primary" disabled={push.busy} onClick={push.enable}>{push.busy && <span className="spinner" />}Turn on</button>
          )}
        </div>
        {push.on && (
          <>
            <div className="row" style={{ flexWrap: 'wrap', justifyContent: 'flex-start', gap: 10 }}>
              <label className="row small" style={{ gap: 8, justifyContent: 'flex-start' }}>
                <input type="checkbox" checked={quietOn} onChange={(e) => push.saveQuiet(e.target.checked ? { start, end } : { start: null, end: null })} />
                Quiet hours
              </label>
              <input className="select" type="time" aria-label="Quiet from" value={start} onChange={(e) => setStart(e.target.value)} onBlur={() => quietOn && push.saveQuiet({ start, end })} />
              <span className="small muted">to</span>
              <input className="select" type="time" aria-label="Quiet until" value={end} onChange={(e) => setEnd(e.target.value)} onBlur={() => quietOn && push.saveQuiet({ start, end })} />
            </div>
            <p className="tiny muted" style={{ margin: 0 }}>During quiet hours notifications still arrive, without sound.</p>
            <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} disabled={push.busy} onClick={push.test}>Send a test notification</button>
          </>
        )}
        {push.message && <p className="small" style={{ margin: 0 }}>{push.message}</p>}
      </section>

      <section className="section" aria-label="Your players">
        <span className="eyebrow">Your players</span>
        {cards.length === 0 ? (
          <div className="card empty small">Follow a player to choose what you hear about them.</div>
        ) : (
          <ul className="card list">
            {cards.map((c) => (
              <li key={c.player_id} className="row">
                <div className="row" style={{ justifyContent: 'flex-start', minWidth: 0 }}>
                  <Avatar name={c.player.name} size="sm" />
                  <label className="title" htmlFor={`alerts-${c.player_id}`}>{c.player.name}</label>
                </div>
                <AlertSelect id={`alerts-${c.player_id}`} value={c.alerts ?? 'key'} onChange={(level) => setAlerts(c.player_id, level)} />
              </li>
            ))}
          </ul>
        )}
        <dl className="levels">
          {LEVELS.map((l) => (
            <div key={l.value}><dt>{l.label}</dt><dd className="small muted">{l.hint}</dd></div>
          ))}
        </dl>
      </section>

      <Link to="/credits" className="tiny muted">Credits and sources</Link>
    </>
  )
}

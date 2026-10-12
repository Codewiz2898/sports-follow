import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Avatar } from '../components/Blocks'
import { useFollowing } from '../following'
import { AlertLevels, LEVELS, usePush } from '../notifications'

/** Alerts: on or off for this device, its quiet hours, and how much to hear about each player. */
export function NotificationsPage() {
  const push = usePush()
  const { cards, setAlerts } = useFollowing()
  const [start, setStart] = useState(push.quiet.start ?? '22:00')
  const [end, setEnd] = useState(push.quiet.end ?? '07:00')
  const quietOn = Boolean(push.quiet.start && push.quiet.end)

  return (
    <>
      <h1 className="dsp page-title">Alerts</h1>

      <section className="card" aria-label="This device" style={{ padding: '4px 18px' }}>
        <div className="row" style={{ padding: '14px 0', borderBottom: push.on ? '1px solid var(--line)' : 0 }}>
          <span className="stack">
            <span className="title">On this device</span>
            <span className="small muted">
              {push.unavailable ?? (push.on ? 'Goals, fifties, big games and results reach you even with the app closed.' : 'Turn on to hear about your players even with the app closed.')}
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
                      <span className="tiny muted">{LEVELS.find((l) => l.value === level)?.brief}</span>
                    </span>
                  </span>
                  <AlertLevels label={`Alerts for ${c.player.name}`} value={level} onChange={(l) => setAlerts(c.player_id, l)} />
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <Link to="/credits" className="tiny muted link" style={{ alignSelf: 'flex-start' }}>Credits and sources</Link>
    </>
  )
}

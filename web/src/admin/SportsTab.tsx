import { useEffect, useState } from 'react'
import { admin, type AdminSport } from './api'
import { Note, breakable, useFailure } from './shared'

/** Every sport, in its colour while it runs, with a switch: off, it isn't searched, imported or polled, and isn't offered in the
 * app. Each esports game is a sport with a switch of its own, under "Esports". */
export function SportsTab() {
  const failed = useFailure()
  const [sports, setSports] = useState<AdminSport[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { admin.sports().then((r) => setSports(r.sports), (e) => setError(failed(e))) }, [failed])

  const turn = async (sport: AdminSport) => {
    setBusy(sport.key)
    setError(null)
    try {
      setSports((await admin.setSport(sport.key, !sport.on)).sports)
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="section" aria-label="Sports">
      <Note>
        A sport turned off isn't searched, imported or polled, its sources stop, and the app stops offering it. The pages of players
        fans already follow stay as they are. Every server process picks a change up within 10 seconds; fans' apps within a few minutes.
      </Note>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!sports ? <div className="card empty"><span className="spinner" /></div> : groups(sports).map(([group, theirs]) => (
        <div key={group} className="stack" style={{ gap: 10 }}>
          {group && <h3 className="eyebrow">{group}</h3>}
          <div className="admin-grid">
            {theirs.map((s) => (
              <article key={s.key} className="admin-sport" data-sport={s.key} data-off={s.running ? undefined : ''}>
                {/* The name has the whole width: a long one (Basketball) can't push the switch out of the card. */}
                <h2 className="dsp">{s.label}</h2>
                <div className="row" style={{ alignItems: 'flex-end' }}>
                  <span className="small">{breakable(sportStatus(s))}</span>
                  <button
                    type="button" role="switch" className="switch" aria-checked={s.allowed && s.on} aria-label={`${s.label} on`}
                    disabled={!s.allowed || busy === s.key} onClick={() => void turn(s)}
                  />
                </div>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

/** The sports, those of a group (each esports game, under "Esports") together after the rest, in the server's order. */
function groups(sports: AdminSport[]): [string, AdminSport[]][] {
  const out = new Map<string, AdminSport[]>()
  for (const s of sports) out.set(s.group ?? '', [...(out.get(s.group ?? '') ?? []), s])
  return [...out]
}

function sportStatus(s: AdminSport): string {
  if (!s.allowed) return "Off in the server's settings (SPORTS_FOLLOW_SPORTS)"
  if (!s.on) return 'Off'
  return s.sources.length ? `On · from ${s.sources.join(', ')}` : 'On · no source runs: AI-built pages only'
}

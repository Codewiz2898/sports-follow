import { useCallback, useEffect, useState } from 'react'
import { admin, type AdminSport } from './api'
import { Note, breakable, useFailure } from './shared'

const REFRESH_MS = 30_000  // read again while the tab shows, as the Sources tab is: another tab or admin may switch one

/** Every sport, in its colour while it runs, with a switch: off, it isn't searched, imported or polled, and isn't offered in the
 * app. Each esports game is a sport with a switch of its own, under "Esports". */
export function SportsTab() {
  const failed = useFailure()
  const [sports, setSports] = useState<AdminSport[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => admin.sports().then((r) => setSports(r.sports), (e) => setError(failed(e))), [failed])
  useEffect(() => {
    void load()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load() }, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const change = async (sport: AdminSport, call: () => Promise<{ sports: AdminSport[] }>) => {
    setBusy(sport.key)
    setError(null)
    try {
      setSports((await call()).sports)
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }
  const turn = (sport: AdminSport) => change(sport, () => admin.setSport(sport.key, !sport.on))
  const turnLogos = (sport: AdminSport) => change(sport, () => admin.setLogos(sport.key, !sport.logos))

  return (
    <section className="section" aria-label="Sports">
      <Note>
        A sport turned off isn't searched, imported or polled, its sources stop, and the app stops offering it. The pages of players
        fans already follow stay as they are. With a sport's logos off, they stop being served at once, and fans' apps show our badge
        (each team's short code) in their place within a minute. Every server process picks a change up within 10 seconds; fans' apps
        the rest within a few minutes.
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
                <div className="row">
                  <span className="small">{s.logos ? "Teams' logos" : "Teams' logos off: our badges"}</span>
                  <button
                    type="button" role="switch" className="switch" aria-checked={s.logos} aria-label={`${s.label} teams' logos`}
                    disabled={busy === s.key} onClick={() => void turnLogos(s)}
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

/** What a sport's switch means now. One on with no source running and research off has nothing to show: fans
 * aren't offered it (sports.offered on the server), and it should be turned off. */
function sportStatus(s: AdminSport): string {
  if (!s.allowed) return "Off in the server's settings (SPORTS_FOLLOW_SPORTS)"
  if (!s.on) return 'Off'
  if (s.sources.length) return `On · from ${s.sources.join(', ')}`
  return s.offered ? 'On · no source runs: AI-built pages only' : 'On · no source runs and research is off: nothing to show; turn it off'
}

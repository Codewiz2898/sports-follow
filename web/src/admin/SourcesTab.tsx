import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { admin, type AdminSource, type Allowance, type Limits, type SourcesView } from './api'
import { LinearCard } from './LinearCard'
import { Note, breakable, count, useFailure } from './shared'

const REFRESH_MS = 30_000
// Each window, as "a minute", "an hour", "a day".
const SPANS: [seconds: number, per: string][] = [[60, 'a minute'], [3600, 'an hour'], [86400, 'a day']]
const LANES: Record<string, string> = { live: 'Live games', fan: 'Fans', refresh: 'Refreshes', history: 'Older results', import: 'Registry import' }

/** Every source with a switch, then every allowance its sources spend: where it stands, what each lane spent, and its limits to change. */
export function SourcesTab() {
  const failed = useFailure()
  const [view, setView] = useState<SourcesView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => admin.sources().then(setView, (e) => setError(failed(e))), [failed])
  useEffect(() => {
    void load()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load() }, REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const turn = async (source: AdminSource) => {
    setBusy(source.system)
    setError(null)
    try {
      setView(await admin.setSource(source.system, !source.on))
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }

  if (!view) return error ? <div className="error-box" role="alert">{error}</div> : <div className="card empty"><span className="spinner" /></div>
  const bySport = new Map<string, AdminSource[]>()
  for (const s of view.sources) bySport.set(s.sport_label, [...(bySport.get(s.sport_label) ?? []), s])
  return (
    <>
      <section className="section" aria-labelledby="sources-title">
        <h2 id="sources-title" className="dsp section-title">Sources</h2>
        <Note>
          Where two sources serve a sport, the first that runs is used. Turn one off and its followed players move to the next on their
          next refresh, which reads their games again from it, and turning it back on moves them all back: it's not for a moment's pause.
          With no source left, their pages stop updating until it's back. The server's settings (SPORTS_FOLLOW_SOURCES, and each licensed
          source's token) stay the limit: a source they leave out can't be turned on here.
        </Note>
        {error && <div className="error-box" role="alert">{error}</div>}
        {[...bySport].map(([sport, sources]) => (
          <div key={sport} className="card">
            <ul className="list">
              <li className="eyebrow" style={{ minHeight: 0, padding: '12px 0 8px' }}>{sport}</li>
              {sources.map((s) => (
                <li key={s.system} className="admin-row">
                  <div className="stack">
                    <span className="title">{s.name} <span className="muted small">{s.provider !== s.name ? `· ${s.provider} ` : ''}{s.licensed ? '' : '· development only'}</span></span>
                    <span className="small muted">{breakable(sourceStatus(s))}</span>
                  </div>
                  <button
                    type="button" role="switch" className="switch" aria-checked={s.allowed && s.token && s.on} aria-label={`${s.name} (${sport}) on`}
                    disabled={!s.allowed || !s.token || busy === s.system} onClick={() => void turn(s)}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
      <section className="section" aria-labelledby="allowances-title">
        <h2 id="allowances-title" className="dsp section-title">Credits and limits</h2>
        <Note>
          Each API key's allowance: the requests its plan gives a minute, an hour or a day, and how they're shared. The margin is never spent
          (another process may count a moment late). The reserve is kept for live games and fans: refreshes, older results and the registry
          import stop there. The fans' share caps searches and follows on a scarce source. A change applies to the next request. Last,
          Linear's issues against its plan's limit: past it, Linear takes no new ones.
        </Note>
        <div className="admin-allowances">
          {view.budgets.map((b) => <AllowanceCard key={b.name} allowance={b} onSaved={setView} />)}
          <LinearCard />
        </div>
      </section>
    </>
  )
}

function sourceStatus(s: AdminSource): string {
  if (!s.allowed) return "Not in the server's settings (SPORTS_FOLLOW_SOURCES)"
  if (!s.token) return 'No token on the server'
  if (!s.on) return 'Off'
  if (!s.sport_on) return 'On, but its sport is off'
  return s.running ? `Running · ${s.covers}` : 'Not running'
}

function per(seconds: number): string {
  return SPANS.find(([s]) => s === seconds)?.[1] ?? `every ${seconds} s`
}

function summary(limits: Limits): string {
  if (!limits.windows.length) return 'No published limit: counted, never refused.'
  const windows = limits.windows.map((w) => `${count(w.requests)} ${per(w.seconds)}${w.margin ? ` (margin ${count(w.margin)})` : ''}`).join(', ')
  const reserve = limits.reserve ? ` · ${count(limits.reserve)} kept for live games and fans` : ''
  const fans = limits.fan != null ? ` · fans at most ${count(limits.fan)} ${per(limits.windows[0].seconds)}` : ''
  return `${windows}${reserve}${fans} · a ${limits.pause} s pause after a 429`
}

function AllowanceCard({ allowance: b, onSaved }: { allowance: Allowance; onSaved: (view: SourcesView) => void }) {
  const failed = useFailure()
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reset = async () => {
    setBusy(true)
    setError(null)
    try {
      onSaved(await admin.resetLimits(b.name))
      setEditing(false)
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="card card-pad stack admin-allowance" style={{ gap: 12 }}>
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="stack">
          <h3 className="title">{b.name}</h3>
          <span className="small muted">Spent by {b.sources.join(', ')}</span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {b.standing.paused && <span className="pill danger">Paused by the source</span>}
          {b.changed && <span className="pill ink">Changed here</span>}
          {!b.running && <span className="pill soft">Not running</span>}
        </div>
      </div>
      {b.standing.windows.map((w) => {
        const share = w.allowance ? Math.min(w.used / w.allowance, 1) : 0
        return (
          <div key={`${w.bucket}:${w.window}`} className="stack">
            <div className="row small">
              <span>This {w.window}{w.bucket ? ` · ${w.bucket}` : ''}</span>
              <span className="num" style={{ fontSize: 18 }}>{count(w.used)} <span className="small muted">of {count(w.allowance)}</span></span>
            </div>
            <div className={`meter${share >= 1 ? ' full' : ''}`} role="meter" aria-valuemin={0} aria-valuemax={w.allowance} aria-valuenow={w.used} aria-label={`Requests this ${w.window}`}>
              <span style={{ width: `${share * 100}%` }} />
            </div>
          </div>
        )
      })}
      {Object.entries(b.standing.left).map(([bucket, n]) => (
        <span key={bucket} className="small muted">The source says {count(n)} left{bucket ? ` (${bucket})` : ''}.</span>
      ))}
      <span className="small">{summary(b.limits)}</span>
      {b.changed && <span className="tiny muted">Declared: {summary(b.declared)}</span>}
      {b.usage.length > 0 && (
        <table className="usage">
          <thead><tr><th scope="col">Lane</th><th scope="col">Sent this hour</th><th scope="col">Today</th><th scope="col">Refused this hour</th><th scope="col">Today</th></tr></thead>
          <tbody>
            {b.usage.map((u) => (
              <tr key={u.lane}><th scope="row">{LANES[u.lane] ?? u.lane}</th><td>{count(u.sent_h)}</td><td>{count(u.sent_d)}</td><td>{count(u.refused_h)}</td><td>{count(u.refused_d)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <div className="error-box" role="alert">{error}</div>}
      {editing ? <LimitsForm allowance={b} onSaved={(view) => { onSaved(view); setEditing(false) }} onCancel={() => setEditing(false)} /> : (
        <div className="row" style={{ justifyContent: 'flex-start' }}>
          <button type="button" className="btn small" onClick={() => setEditing(true)}>Change limits</button>
          {b.changed && <button type="button" className="btn small" disabled={busy} onClick={() => void reset()}>Back to declared</button>}
        </div>
      )}
    </article>
  )
}

interface Draft { windows: Record<number, { on: boolean; requests: string; margin: string }>; reserve: string; fan: string; pause: string }

function draftOf(limits: Limits): Draft {
  const windows: Draft['windows'] = {}
  for (const [seconds] of SPANS) {
    const w = limits.windows.find((x) => x.seconds === seconds)
    windows[seconds] = { on: Boolean(w), requests: w ? String(w.requests) : '', margin: w ? String(w.margin) : '0' }
  }
  return { windows, reserve: String(limits.reserve), fan: limits.fan == null ? '' : String(limits.fan), pause: String(limits.pause) }
}

/** A whole number as typed, or NaN, which goes as null: the server says what's wrong with it. */
const whole = (text: string) => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : NaN)

function LimitsForm({ allowance: b, onSaved, onCancel }: { allowance: Allowance; onSaved: (view: SourcesView) => void; onCancel: () => void }) {
  const failed = useFailure()
  const [draft, setDraft] = useState(() => draftOf(b.limits))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const declared = (seconds: number) => b.declared.windows.find((w) => w.seconds === seconds)
  // The fans' share is counted in the shortest window ticked (budget.take).
  const shareWindow = SPANS.find(([s]) => draft.windows[s].on)?.[1]
  const setWindow = (seconds: number, change: Partial<Draft['windows'][number]>) =>
    setDraft((d) => ({ ...d, windows: { ...d.windows, [seconds]: { ...d.windows[seconds], ...change } } }))

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const limits: Limits = {
      windows: SPANS.filter(([s]) => draft.windows[s].on).map(([s]) => ({ seconds: s, requests: whole(draft.windows[s].requests), margin: whole(draft.windows[s].margin || '0') })),
      reserve: whole(draft.reserve || '0'),
      fan: draft.fan.trim() === '' ? null : whole(draft.fan),
      pause: whole(draft.pause),
    }
    try {
      onSaved(await admin.setLimits(b.name, limits))
    } catch (err) {
      setError(failed(err))
      setBusy(false)
    }
  }

  return (
    <form className="limits-form" onSubmit={save} aria-label={`Limits of ${b.name}`}>
      {SPANS.map(([seconds, name]) => {
        const label = name[0].toUpperCase() + name.slice(1)
        const w = draft.windows[seconds]
        const was = declared(seconds)
        return (
          <fieldset key={seconds} className="limit-row">
            <legend className="sr-only">{label}</legend>
            <label className="row small" style={{ justifyContent: 'flex-start', gap: 8 }}>
              <input type="checkbox" checked={w.on} onChange={(e) => setWindow(seconds, { on: e.target.checked, requests: w.requests || String(was?.requests ?? '') })} />
              {label}
            </label>
            <label className="stack tiny muted">
              Allowance{was ? ` (declared ${count(was.requests)})` : ''}
              <input className="field compact" inputMode="numeric" disabled={!w.on} value={w.requests} onChange={(e) => setWindow(seconds, { requests: e.target.value })} />
            </label>
            <label className="stack tiny muted">
              Margin
              <input className="field compact" inputMode="numeric" disabled={!w.on} value={w.margin} onChange={(e) => setWindow(seconds, { margin: e.target.value })} />
            </label>
          </fieldset>
        )
      })}
      <div className="limit-row">
        <label className="stack tiny muted">
          Reserve for live games and fans (declared {count(b.declared.reserve)})
          <input className="field compact" inputMode="numeric" value={draft.reserve} onChange={(e) => setDraft({ ...draft, reserve: e.target.value })} />
        </label>
        <label className="stack tiny muted">
          Fans' share: their requests {shareWindow ?? 'a window'} (empty: no cap of its own)
          <input className="field compact" inputMode="numeric" value={draft.fan} onChange={(e) => setDraft({ ...draft, fan: e.target.value })} />
        </label>
        <label className="stack tiny muted">
          Pause after a 429, seconds
          <input className="field compact" inputMode="numeric" value={draft.pause} onChange={(e) => setDraft({ ...draft, pause: e.target.value })} />
        </label>
      </div>
      {error && <div className="error-box" role="alert">{error}</div>}
      <div className="row" style={{ justifyContent: 'flex-start' }}>
        <button type="submit" className="btn primary small" disabled={busy}>{busy ? <span className="spinner" /> : 'Save'}</button>
        <button type="button" className="btn small" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

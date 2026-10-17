import { useEffect, useState } from 'react'
import { ahead, day, relative, safeHref } from '../format'
import { admin, type LinearStatus } from './api'
import { count, useFailure } from './shared'

const PLANS: Record<string, string> = { free: 'Free', basic: 'Basic', business: 'Business', enterprise: 'Enterprise' }

/** Linear, where the project's work is tracked: its plan, and its issues against the Free plan's limit, past which
 * Linear refuses new ones. Each API process asks Linear at most once an hour (sports_follow/linear.py), so it's read once a visit. */
export function LinearCard() {
  const failed = useFailure()
  const [status, setStatus] = useState<LinearStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { admin.linear().then(setStatus, (e) => setError(failed(e))) }, [failed])

  return (
    <article className="card card-pad stack admin-allowance" style={{ gap: 12 }} aria-labelledby="linear-title">
      <div className="row" style={{ alignItems: 'flex-start' }}>
        <div className="stack">
          <h3 id="linear-title" className="title">Linear issues</h3>
          {status?.issues != null && <span className="small muted">{subtitle(status)}</span>}
        </div>
        {status?.issues != null && (
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {status.state === 'full' && <span className="pill danger">New issues refused</span>}
            {status.state === 'near' && <span className="pill ink">Near the limit</span>}
            {status.limit == null && <span className="pill soft">No issue limit</span>}
          </div>
        )}
      </div>
      {error ? <div className="error-box" role="alert">{error}</div> : !status ? <span className="spinner" /> : <LinearBody status={status} />}
    </article>
  )
}

function subtitle(s: LinearStatus): string {
  const plan = s.plan == null ? "Plan unknown (the key can't read it)" : `${PLANS[s.plan] ?? s.plan} plan`
  const trial = s.trial_ends_at && ahead(s.trial_ends_at) ? ` · trial until ${day(s.trial_ends_at)}` : ''
  return `${s.workspace || 'Linear'} · ${plan}${trial}`
}

function LinearBody({ status: s }: { status: LinearStatus }) {
  if (!s.configured) {
    return (
      <span className="small">
        No Linear key on the server. In Linear, Settings › Account › Security & access, make a personal API key with the Read
        permission alone, save it on the Mac as <code>~/.config/sports-follow/linear-key</code> and send it
        with <code>deploy/push-secrets.sh</code>.
      </span>
    )
  }
  if (s.issues == null) return <div className="error-box" role="alert">Can't read Linear: {s.error}. It's tried again in 5 minutes.</div>
  const limit = s.limit
  const share = limit ? Math.min(s.issues / limit, 1) : 0
  const href = safeHref(s.url)
  return (
    <>
      {limit == null ? (
        <span className="num" style={{ fontSize: 18 }}>{count(s.issues)} <span className="small muted">issues</span></span>
      ) : (
        <div className="stack">
          <div className="row small">
            <span>Issues, against the Free plan's limit</span>
            <span className="num" style={{ fontSize: 18 }}>{count(s.issues)} <span className="small muted">of {count(limit)}</span></span>
          </div>
          <div className={`meter${s.state === 'full' ? ' full' : ''}`} role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={s.issues} aria-label="Linear issues">
            <span style={{ width: `${share * 100}%` }} />
          </div>
        </div>
      )}
      {limit != null && (
        <span className="small muted">
          Archived issues aren't counted ({s.archived ? count(s.archived) : 'none'} now).
          {s.state !== 'ok' && <> Past {count(limit)}, Linear takes no new issues: upgrade the plan, or let Linear archive closed ones sooner (each team's settings, Issue statuses & automations).</>}
        </span>
      )}
      <span className="tiny muted">
        Read {relative(s.read_at)}{href && <> · <a href={href} target="_blank" rel="noreferrer">Open Linear</a></>}
        {s.error && <> · the last try failed {relative(s.failed_at)}: {s.error}</>}
      </span>
    </>
  )
}

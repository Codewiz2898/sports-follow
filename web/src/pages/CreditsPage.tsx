import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Credit, type Credits } from '../api'
import { BackIcon } from '../components/Icons'
import { safeHref } from '../format'

/** Credits: where this build's scores, ratings, evaluations, words and code come from (sports_follow/credits.py). */
export function CreditsPage() {
  const [credits, setCredits] = useState<Credits | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.credits().then(setCredits).catch((e: Error) => setError(e.message))
  }, [])

  return (
    <>
      <div className="page-head">
        <div className="row" style={{ justifyContent: 'flex-start' }}>
          <Link to="/" className="btn icon" aria-label="Back"><BackIcon /></Link>
          <h1 className="page-title">Credits</h1>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {!credits && !error && <div className="card empty small"><span className="spinner" /> Loading…</div>}
      {credits && (
        <>
          <CreditList label="Scores and stats" items={credits.data} />
          <CreditList label="Also from" items={credits.also} />

          <section className="section" aria-label="Notices">
            <span className="eyebrow">Notices</span>
            <div className="card card-pad stack" style={{ gap: 10 }}>
              {credits.notices.map((n) => <p key={n} className="small" style={{ margin: 0 }}>{n}</p>)}
            </div>
          </section>

          <section className="section" aria-label="Software">
            <span className="eyebrow">Built with</span>
            <ul className="card list">
              {credits.software.map((s) => (
                <li key={s.name} className="row">
                  <Name credit={s} />
                  <span className="tiny muted" style={{ textAlign: 'right' }}>{s.licence}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </>
  )
}

function CreditList({ label, items }: { label: string; items: Credit[] }) {
  if (!items.length) return null
  return (
    <section className="section" aria-label={label}>
      <span className="eyebrow">{label}</span>
      <ul className="card list">
        {items.map((c) => (
          <li key={c.name} className="stack" style={{ gap: 4 }}>
            <Name credit={c} />
            {c.what && <span className="small">{c.what}</span>}
            {(c.licence || c.note) && (
              <span className="tiny muted">
                {c.licence && (safeHref(c.licence_url) ? <a href={safeHref(c.licence_url)} target="_blank" rel="noreferrer">{c.licence}</a> : c.licence)}
                {c.licence && c.note && ' · '}
                {c.note}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Name({ credit }: { credit: Credit }) {
  const href = safeHref(credit.url)
  return href
    ? <a className="title" href={href} target="_blank" rel="noreferrer">{credit.name}</a>
    : <span className="title">{credit.name}</span>
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Credit, type Credits, type PhotoCredit } from '../api'
import { SectionHead } from '../components/Blocks'
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
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/" className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">Credits</h1>
      </div>

      {error && <div className="error-box">{error}</div>}
      {!credits && !error && <div className="card empty small"><span className="spinner" /> Loading…</div>}
      {credits && (
        <>
          <CreditList label="Scores and stats" items={credits.data} />
          <CreditList label="Also from" items={credits.also} />
          <PhotoList items={credits.photos ?? []} />

          <section className="section" aria-label="Notices">
            <SectionHead title="Notices" />
            <div className="card card-pad stack" style={{ gap: 10 }}>
              {credits.notices.map((n) => <p key={n} className="small">{n}</p>)}
            </div>
          </section>

          <section className="section" aria-label="Software">
            <SectionHead title="Built with" />
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
      <span className="row tiny muted" style={{ justifyContent: 'flex-start' }}>
        <Link to="/privacy" className="link">Privacy: what we keep, and why</Link>
        <Link to="/terms" className="link">Terms</Link>
      </span>
    </>
  )
}

function CreditList({ label, items }: { label: string; items: Credit[] }) {
  if (!items.length) return null
  return (
    <section className="section" aria-label={label}>
      <SectionHead title={label} />
      <ul className="card list">
        {items.map((c) => (
          <li key={c.name} className="stack" style={{ gap: 4 }}>
            <Name credit={c} />
            {c.what && <span className="small">{c.what}</span>}
            {(c.licence || c.note) && (
              <span className="tiny muted">
                {c.licence && (safeHref(c.licence_url) ? <a href={safeHref(c.licence_url)} target="_blank" rel="noreferrer" className="link">{c.licence}</a> : c.licence)}
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

/** Every player photo shown, with its author and licence: the credit each one's licence asks for. */
function PhotoList({ items }: { items: PhotoCredit[] }) {
  if (!items.length) return null
  return (
    <section className="section" aria-label="Photos">
      <SectionHead title="Photos" />
      <ul className="card list">
        {items.map((p) => (
          <li key={p.page} className="stack" style={{ gap: 4 }}>
            <span className="title">{p.name}</span>
            <span className="small">
              {safeHref(p.page) ? <a href={safeHref(p.page)} target="_blank" rel="noreferrer" className="link">Photo</a> : 'Photo'}
              {` by ${p.author}, `}
              {safeHref(p.licence_url) ? <a href={safeHref(p.licence_url)} target="_blank" rel="noreferrer" className="link">{p.licence}</a> : p.licence}
              {', from Wikimedia Commons, cropped'}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Name({ credit }: { credit: Credit }) {
  const href = safeHref(credit.url)
  return href
    ? <a className="title link" href={href} target="_blank" rel="noreferrer">{credit.name}</a>
    : <span className="title">{credit.name}</span>
}

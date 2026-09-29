import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Card, type Form, type GameDetail, type LatestResult, type RecentResult, type ResultsPage } from '../api'
import { day, hostname, safeHref } from '../format'
import { SourceNote, StatGrid } from './Blocks'
import { ChevronIcon } from './Icons'

type Row = RecentResult & { player_id?: number; player_name?: string }

/** One finished game; tapping it opens the final score, the player's whole line and the source. */
function GameRow({ r, playerId, who }: { r: Row; playerId: number; who?: boolean }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<GameDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canOpen = r.event_id != null

  const toggle = () => {
    setOpen((o) => !o)
    if (!detail && r.event_id != null) api.result(playerId, r.event_id).then(setDetail).catch((e: Error) => setError(e.message))
  }

  return (
    <li className="game-row">
      <button type="button" className="game-head" onClick={toggle} disabled={!canOpen} aria-expanded={canOpen ? open : undefined}>
        <span className="stack" style={{ minWidth: 0 }}>
          {who && r.player_name && <span className="small" style={{ fontWeight: 700 }}>{r.player_name}</span>}
          <span className="title">{r.title}</span>
          <span className="small muted">{[r.date && day(r.date), r.competition].filter(Boolean).join(' · ')}</span>
          {r.player_contribution && <span className="small" style={{ fontWeight: 600 }}>{r.player_contribution}</span>}
        </span>
        <span className={r.result.length > 14 ? 'game-result long' : 'game-result'}>
          {r.result}
          {canOpen && <ChevronIcon className={open ? 'chev open' : 'chev'} width={16} height={16} aria-hidden />}
        </span>
      </button>
      {open && <GameView detail={detail} error={error} playerLink={who ? { id: playerId, name: r.player_name } : undefined} />}
    </li>
  )
}

function GameView({ detail, error, playerLink }: { detail: GameDetail | null; error: string | null; playerLink?: { id: number; name?: string } }) {
  if (error) return <div className="game-body small muted">{error}</div>
  if (!detail) return <div className="game-body"><span className="spinner" /></div>
  const lineNote = detail.scorecard === 'missing' ? 'The source has no scorecard for this game.'
    : detail.scorecard ? 'No line for the player in this game.' : "The player's line is still being read."
  return (
    <div className="game-body">
      {(detail.score || detail.clock) && (
        <div className="stack">
          {detail.score && <span className="title">{detail.score}</span>}
          <span className="small muted">{[detail.clock, detail.venue].filter(Boolean).join(' · ')}</span>
        </div>
      )}
      {detail.line ? (
        <>
          <span className="small" style={{ fontWeight: 600 }}>{detail.line.headline}</span>
          {detail.line.stats.length > 0 && <StatGrid stats={detail.line.stats} />}
        </>
      ) : <span className="small muted">{lineNote}</span>}
      <span className="row small" style={{ justifyContent: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
        {safeHref(detail.source_url) && <a href={safeHref(detail.source_url)} target="_blank" rel="noreferrer">Open on {hostname(detail.source_url)}</a>}
        {playerLink && <Link to={`/player/${playerLink.id}`}>{playerLink.name ? `${playerLink.name}'s page` : 'Player page'}</Link>}
      </span>
    </div>
  )
}

/** The Results tab: the player's whole results history, newest first, a page at a time. */
export function ResultsHistory({ card }: { card: Card }) {
  const [items, setItems] = useState<RecentResult[]>(card.recent_results ?? [])
  const [page, setPage] = useState<ResultsPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.results(card.player_id).then((p) => {
      if (cancelled) return
      if (p.results.length) setItems(p.results)  // an agent-built card has no stored history: keep its list
      setPage(p)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [card.player_id])

  const more = () => {
    if (!page?.next) return
    setLoading(true)
    setError(null)
    api.results(card.player_id, page.next)
      .then((p) => { setItems((xs) => [...xs, ...p.results]); setPage(p) })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  if (!items.length) {
    return <div className="card empty small">{page?.filling ? 'Reading results from the source…' : 'No recent results found.'}</div>
  }
  return (
    <>
      <ul className="card list">
        {items.map((r) => <GameRow key={r.event_id ?? `${r.title}-${r.date ?? ''}`} r={r} playerId={card.player_id} />)}
      </ul>
      {page?.next && (
        <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={more} disabled={loading}>
          {loading && <span className="spinner" />}Show more
        </button>
      )}
      {error && <p className="small" style={{ margin: 0 }}>{error}</p>}
      <p className="tiny muted" style={{ margin: 0 }}>
        {page && page.total > 0 && `${Math.min(items.length, page.total)} of ${page.total} results`}
        {page?.filling && ' · older results are still being filled in'}
      </p>
      <SourceNote provenance={card.provenance?.recent_results} />
    </>
  )
}

type View = 'overview' | 10 | 5

/** The Stats tab: the source's numbers, or form over the last 10 or 5 results. */
export function StatsWithForm({ card }: { card: Card }) {
  const [view, setView] = useState<View>('overview')
  const [form, setForm] = useState<Partial<Record<10 | 5, Form>>>({})
  const sport = (card.player.sport || '').toLowerCase()
  const overview = sport === 'cricket' || sport === 'tennis' ? 'Overview' : 'Season'

  useEffect(() => {
    if (view === 'overview' || form[view]) return
    api.form(card.player_id, view)
      .then((f) => setForm((x) => ({ ...x, [view]: f })))
      .catch(() => setForm((x) => ({ ...x, [view]: { stats: [], note: '', count: 0 } })))
  }, [view, card.player_id, form])

  const views: [View, string][] = [['overview', overview], [10, 'Last 10'], [5, 'Last 5']]
  const shown = view === 'overview' ? null : form[view]
  return (
    <>
      <div className="segmented" role="group" aria-label="Which stats">
        {views.map(([v, label]) => (
          <button key={String(v)} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{label}</button>
        ))}
      </div>
      {view === 'overview' ? (
        <>
          <StatGrid stats={card.season_stats} />
          <SourceNote provenance={card.provenance?.season_stats} note={card.season_stats_note} />
        </>
      ) : shown ? (
        <>
          <StatGrid stats={shown.stats} empty="Not enough results yet." />
          {shown.note && <p className="tiny muted" style={{ margin: 0 }}>{shown.note}, from the results history</p>}
        </>
      ) : <span className="spinner" />}
    </>
  )
}

/** The Following page's newest results across every followed player. */
export function LatestResults({ watch }: { watch: string }) {
  const [items, setItems] = useState<LatestResult[] | null>(null)
  useEffect(() => {
    api.latestResults(6).then((r) => setItems(r.results)).catch(() => setItems([]))
  }, [watch])
  if (items === null) return null
  if (!items.length) return <div className="card empty small">No results yet for your players.</div>
  return (
    <ul className="card list">
      {items.map((r) => <GameRow key={`${r.player_id}-${r.event_id}`} r={r} playerId={r.player_id} who />)}
    </ul>
  )
}

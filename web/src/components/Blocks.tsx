import { Link } from 'react-router-dom'
import type { Card, LiveStatus, NewsItem, RecentResult, Stat, UpcomingEvent } from '../api'
import { dateTime, day, hostname, initials, relative, safeHref } from '../format'

export function Avatar({ name, size }: { name: string; size?: 'sm' | 'lg' }) {
  return <span className={`avatar${size ? ` ${size}` : ''}`} aria-hidden="true">{initials(name)}</span>
}

export function StatGrid({ stats, empty = 'No stats yet.' }: { stats?: Stat[]; empty?: string }) {
  if (!stats?.length) return <p className="muted small">{empty}</p>
  return (
    <div className="stat-grid">
      {stats.map((s) => (
        <div className="stat" key={s.label}>
          <span className="num">{s.value}</span>
          <span className="tiny muted">{s.label}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * The live scoreboard. The agent reports the score as text, so this is one generic layout for every
 * sport; sport-specific boards (two team scores, set tables, clocks) arrive with structured adapters.
 */
export function LiveScoreboard({ live, playerName, compact }: { live: LiveStatus; playerName: string; compact?: boolean }) {
  const [lead, ...rest] = live.player_stats ?? []
  return (
    <section className="card live card-pad" aria-label="Live now" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="row">
        <span className="badge-live">Live</span>
        {live.event && <span className="eyebrow" style={{ textAlign: 'right' }}>{live.event}</span>}
      </div>
      <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="num" style={{ fontSize: compact ? 34 : 48, overflowWrap: 'anywhere' }}>{live.score || '—'}</div>
        {live.clock && <div className="num" style={{ fontSize: compact ? 20 : 24, color: 'var(--live)' }}>{live.clock}</div>}
      </div>
      {lead && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', borderRadius: 14, background: 'var(--bg)', flexWrap: 'wrap' }}>
          <div className="stack">
            <span className="eyebrow">{playerName} · {lead.label}</span>
            <span className="num" style={{ fontSize: 40, color: 'var(--live)' }}>{lead.value}</span>
          </div>
          {rest.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(rest.length, 4)}, minmax(0, 1fr))`, gap: 8, flex: 1, minWidth: 200 }}>
              {rest.slice(0, 4).map((s) => (
                <div key={s.label} className="stack" style={{ alignItems: 'center', textAlign: 'center' }}>
                  <span className="num" style={{ fontSize: 22 }}>{s.value}</span>
                  <span className="tiny muted">{s.label}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="row tiny muted">
        <span>{live.as_of ? `Updated ${relative(live.as_of)}` : 'Live'}{live.source_url ? ` · ${hostname(live.source_url)}` : ''}</span>
        {safeHref(live.source_url) && <a href={safeHref(live.source_url)} target="_blank" rel="noreferrer" style={{ fontWeight: 600 }}>Open source</a>}
      </div>
    </section>
  )
}

export function UpcomingList({ items, who, empty }: { items: (UpcomingEvent & { players?: string[] })[]; who?: boolean; empty: string }) {
  if (!items.length) return <div className="card empty small">{empty}</div>
  return (
    <ul className="card list">
      {items.map((u) => (
        <li key={`${u.title}-${u.start_utc ?? ''}`} className="row">
          <div className="stack">
            <span className="title">{u.title}</span>
            <span className="small muted">
              {[who && u.players?.join(', '), u.competition, u.start_utc ? dateTime(u.start_utc) : 'Date to be confirmed', u.venue].filter(Boolean).join(' · ')}
            </span>
            {u.notes && <span className="small muted">{u.notes}</span>}
          </div>
          <span className="num" style={{ fontSize: 20, color: u.start_utc ? 'var(--action)' : 'var(--muted)', whiteSpace: 'nowrap' }}>{u.start_utc ? relative(u.start_utc) : 'TBC'}</span>
        </li>
      ))}
    </ul>
  )
}

export function NewsList({ items, who, empty }: { items: (NewsItem & { players?: string[] })[]; who?: boolean; empty: string }) {
  if (!items.length) return <div className="card empty small">{empty}</div>
  return (
    <ul className="card list">
      {items.map((n) => (
        <li key={n.url || n.headline}>
          <a href={safeHref(n.url)} target="_blank" rel="noreferrer" className="stack" style={{ color: 'var(--text)' }}>
            <span className="title">{n.headline}</span>
            <span className="small muted">{[who && n.players?.join(', '), n.source, n.published && day(n.published)].filter(Boolean).join(' · ')}</span>
            {!who && n.summary && <span className="small muted">{n.summary}</span>}
          </a>
        </li>
      ))}
    </ul>
  )
}

export function ResultsList({ items }: { items?: RecentResult[] }) {
  if (!items?.length) return <div className="card empty small">No recent results found.</div>
  return (
    <ul className="card list">
      {items.map((r) => (
        <li key={`${r.title}-${r.date ?? ''}`} className="row" style={{ alignItems: 'flex-start' }}>
          <div className="stack">
            <span className="title">{r.title}</span>
            <span className="small muted">{[r.date && day(r.date), r.player_contribution].filter(Boolean).join(' · ')}</span>
          </div>
          <span style={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{r.result}</span>
        </li>
      ))}
    </ul>
  )
}

export function BuildProgress({ card, messages, onRetry }: { card: Card; messages: string[]; onRetry?: () => void }) {
  if (card.status === 'failed') {
    return (
      <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <span className="title">Couldn't build {card.player.name}'s page</span>
        <div className="error-box">{card.error || 'The research agent failed.'}</div>
        {onRetry && <button type="button" className="btn primary" style={{ alignSelf: 'flex-start' }} onClick={onRetry}>Try again</button>}
      </div>
    )
  }
  return (
    <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="row" style={{ justifyContent: 'flex-start' }}>
        <span className="spinner" />
        <span className="title">Building {card.player.name}'s page</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>The first time anyone follows a player, the agent researches them. This takes one to two minutes; every later fan gets the shared page instantly.</p>
      {messages.length > 0 && <ol className="progress-log">{messages.map((m, i) => <li key={i}>{m}</li>)}</ol>}
    </div>
  )
}

export function PlayerRow({ card, progress }: { card: Card; progress?: string[] }) {
  const line = card.status === 'building'
    ? (progress?.at(-1) ?? 'Building page…')
    : card.status === 'failed'
      ? 'Build failed, open to retry'
      : card.live.is_live
        ? [card.live.score, card.live.player_stats?.[0] && `${card.live.player_stats[0].value} ${card.live.player_stats[0].label}`].filter(Boolean).join(' · ')
        : card.upcoming?.[0]
          ? `Next · ${card.upcoming[0].title}${card.upcoming[0].start_utc ? `, ${relative(card.upcoming[0].start_utc)}` : ''}`
          : card.player.status === 'retired' ? 'Retired · news only' : card.player.sport
  return (
    <Link to={`/player/${card.player_id}`} className="row" style={{ color: 'var(--text)', padding: '10px 12px', minHeight: 44, borderRadius: 12 }}>
      <div className="row" style={{ justifyContent: 'flex-start', minWidth: 0 }}>
        <Avatar name={card.player.name} size="sm" />
        <div className="stack">
          <span className="title" style={{ fontSize: 14 }}>{card.player.name}</span>
          <span className="tiny" style={{ color: card.live.is_live ? 'var(--live)' : 'var(--muted)', fontWeight: card.live.is_live ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{line}</span>
        </div>
      </div>
      {card.status === 'building' ? <span className="spinner" /> : card.live.is_live ? <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--live)', flexShrink: 0 }} /> : null}
    </Link>
  )
}

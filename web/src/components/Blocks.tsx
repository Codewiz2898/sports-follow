import { Link } from 'react-router-dom'
import type { BasketballSide, BasketballState, Card, ChessSide, ChessState, CricketState, FootballSide, FootballState, LiveStatus, NewsItem, Provenance, RecentResult, Stat, TennisState, UpcomingEvent } from '../api'
import { dateTime, day, hostname, initials, relative, safeHref, sportName } from '../format'

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
 * The live scoreboard. Scores from a structured source carry a sport-specific state (two sides and
 * scorers for football, innings for cricket); scores the research agent read off a web page are
 * text, shown in one generic layout.
 */
export function LiveScoreboard({ live, playerName, compact }: { live: LiveStatus; playerName: string; compact?: boolean }) {
  const state = live.state
  return (
    <section className="card live card-pad" aria-label="Live now" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="row">
        <span className="badge-live">Live</span>
        {live.event && <span className="eyebrow" style={{ textAlign: 'right' }}>{live.event}</span>}
      </div>
      {state?.kind === 'football' ? (
        <FootballBoard state={state} clock={live.clock} compact={compact} />
      ) : state?.kind === 'cricket' ? (
        <CricketBoard state={state} clock={live.clock} compact={compact} />
      ) : state?.kind === 'basketball' ? (
        <BasketballBoard state={state} clock={live.clock} compact={compact} />
      ) : state?.kind === 'tennis' ? (
        <TennisBoard state={state} clock={live.clock} />
      ) : state?.kind === 'chess' && state.white && state.black ? (
        <ChessBoard state={state} clock={live.clock} flip={(live.player_stats ?? []).some((s) => s.label === 'Colour' && s.value === 'Black')} compact={compact} />
      ) : (
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="num" style={{ fontSize: compact ? 34 : 48, overflowWrap: 'anywhere' }}>{live.score || '—'}</div>
          {live.clock && <div className="num" style={{ fontSize: compact ? 20 : 24, color: 'var(--live)' }}>{live.clock}</div>}
        </div>
      )}
      <PlayerLine live={live} playerName={playerName} />
      {!compact && live.moments && live.moments.length > 0 && (
        <ul className="moments">
          {live.moments.slice(0, 5).map((m, i) => (
            <li key={`${m.clock}-${i}`}>
              <span className="num" style={{ fontSize: 15, minWidth: 44 }}>{m.clock}</span>
              <span className={`moment-kind ${m.kind.toLowerCase()}`}>{m.kind === 'GOAL' ? 'Goal' : m.kind === 'RED' ? 'Red' : 'Yellow'}</span>
              <span className="small">{m.text}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="row tiny muted">
        <span>{live.as_of ? `Updated ${relative(live.as_of)}` : 'Live'}{live.source ? ` · ${live.source}` : live.source_url ? ` · ${hostname(live.source_url)}` : ''}</span>
        {/* Compact boards sit inside a link to the player's page; a link can't hold another link. */}
        {!compact && safeHref(live.source_url) && <a href={safeHref(live.source_url)} target="_blank" rel="noreferrer" style={{ fontWeight: 600 }}>Open source</a>}
      </div>
    </section>
  )
}

function FootballBoard({ state, clock, compact }: { state: FootballState; clock?: string | null; compact?: boolean }) {
  const side = (s: FootballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title" style={{ overflowWrap: 'anywhere' }}>{s.name}</span>
      {!compact && s.scorers.map((g) => <span key={g} className="tiny muted">{g}</span>)}
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
      {side(state.home, 'left')}
      <div className="stack" style={{ alignItems: 'center' }}>
        <span className="num" style={{ fontSize: compact ? 34 : 48, whiteSpace: 'nowrap' }}>{state.home.score}–{state.away.score}</span>
        {clock && <span className="num" style={{ fontSize: compact ? 16 : 20, color: 'var(--live)' }}>{clock}</span>}
      </div>
      {side(state.away, 'right')}
    </div>
  )
}

function CricketBoard({ state, clock, compact }: { state: CricketState; clock?: string | null; compact?: boolean }) {
  if (!state.innings.length) {
    return (
      <div className="stack">
        <span className="num" style={{ fontSize: compact ? 26 : 34 }}>{state.teams.map((t) => t.name).join(' v ')}</span>
        {clock && <span className="small" style={{ color: 'var(--live)' }}>{clock}</span>}
      </div>
    )
  }
  return (
    <div className="stack" style={{ gap: 6 }}>
      {state.innings.map((i) => (
        <div key={`${i.team}-${i.period}`} className="row" style={{ alignItems: 'baseline' }}>
          <span className="title" style={{ color: i.batting ? 'var(--text)' : 'var(--muted)' }}>
            {i.abbr || i.team}
            {i.batting && <span className="tiny" style={{ color: 'var(--live)', marginLeft: 8, fontWeight: 600 }}>batting</span>}
          </span>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span className="num" style={{ fontSize: i.batting ? (compact ? 34 : 44) : 22, color: i.batting ? 'var(--text)' : 'var(--muted)' }}>
              {i.runs}{i.wickets < 10 ? `/${i.wickets}` : ''}
            </span>
            {i.overs != null && <span className="small muted">({i.overs} ov)</span>}
          </span>
        </div>
      ))}
      {(state.summary || clock) && <span className="small" style={{ color: 'var(--live)' }}>{[clock, state.summary !== clock && state.summary].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

/** Away at home, as US leagues write it, with the quarter-by-quarter line under it. */
function BasketballBoard({ state, clock, compact }: { state: BasketballState; clock?: string | null; compact?: boolean }) {
  const side = (s: BasketballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title" style={{ overflowWrap: 'anywhere' }}>{s.name}</span>
      <span className="tiny muted">{align === 'left' ? 'Away' : 'Home'}</span>
    </div>
  )
  const periods = Math.max(state.away.periods.length, state.home.periods.length)
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
        {side(state.away, 'left')}
        <div className="stack" style={{ alignItems: 'center' }}>
          <span className="num" style={{ fontSize: compact ? 34 : 48, whiteSpace: 'nowrap' }}>{state.away.score}–{state.home.score}</span>
          {clock && <span className="num" style={{ fontSize: compact ? 16 : 20, color: 'var(--live)' }}>{clock}</span>}
        </div>
        {side(state.home, 'right')}
      </div>
      {!compact && periods > 0 && (
        <table className="linescore">
          <thead><tr><th />{Array.from({ length: periods }, (_, i) => <th key={i}>{i < 4 ? `Q${i + 1}` : `OT${i > 4 ? i - 3 : ''}`}</th>)}<th>T</th></tr></thead>
          <tbody>
            {[state.away, state.home].map((s) => (
              <tr key={s.id}><td>{s.abbr || s.name}</td>{Array.from({ length: periods }, (_, i) => <td key={i}>{s.periods[i] ?? ''}</td>)}<td className="total">{s.score}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** One row per player, one column per set; the serving player is marked. */
function TennisBoard({ state, clock }: { state: TennisState; clock?: string | null }) {
  const sets = Math.max(0, ...state.players.map((p) => p.sets.length))
  return (
    <div className="stack" style={{ gap: 8 }}>
      <table className="linescore tennis">
        <tbody>
          {state.players.map((p) => (
            <tr key={p.id} className={p.winner ? 'won' : undefined}>
              <td className="who">
                <span className="title">{p.name}</span>
                {p.seed ? <span className="tiny muted"> ({p.seed})</span> : null}
                {p.serving && <span className="serve" aria-label="serving" />}
              </td>
              {Array.from({ length: sets }, (_, i) => {
                const set = p.sets[i]
                return (
                  <td key={i} className={`num${set?.won ? ' set-won' : ''}`}>
                    {set?.games ?? ''}{set?.tiebreak != null && <sup>{set.tiebreak}</sup>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {clock && <span className="small" style={{ color: 'var(--live)' }}>{[state.round, clock].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

const PIECES: Record<string, string> = { k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟' }

/** The board from the position's FEN, from the followed player's side, with the last move marked. */
function ChessBoard({ state, clock, flip, compact }: { state: ChessState; clock?: string | null; flip: boolean; compact?: boolean }) {
  const rows = (state.fen ?? '').split(' ')[0].split('/')
  const squares: (string | null)[][] = rows.map((row) => row.split('').flatMap((c) => (/\d/.test(c) ? Array(Number(c)).fill(null) : [c])))
  const moved = new Set(state.last_move ? [state.last_move.slice(0, 2), state.last_move.slice(2, 4)] : [])
  const ranks = flip ? [0, 1, 2, 3, 4, 5, 6, 7].reverse() : [0, 1, 2, 3, 4, 5, 6, 7]
  const player = (s: ChessSide, colour: 'white' | 'black') => (
    <div className="row" style={{ gap: 12 }}>
      <span className="stack" style={{ minWidth: 0 }}>
        <span className="title">{s.title ? <span className="muted">{s.title} </span> : null}{s.name}</span>
        <span className="tiny muted">{[colour === 'white' ? 'White' : 'Black', s.rating, s.fed].filter(Boolean).join(' · ')}</span>
      </span>
      {s.clock && <span className={`num chess-clock${state.turn === colour && !state.result ? ' running' : ''}`}>{s.clock}</span>}
    </div>
  )
  const top = flip ? state.white! : state.black!
  const bottom = flip ? state.black! : state.white!
  return (
    <div className="chess" style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {rows.length === 8 && (
        <div className={`chessboard${compact ? ' sm' : ''}`} role="img" aria-label={`Position after move ${state.move ?? ''}`}>
          {ranks.map((r) => (flip ? [...squares[r]].reverse() : squares[r]).map((piece, i) => {
            const file = flip ? 7 - i : i
            const name = `${'abcdefgh'[file]}${8 - r}`
            return (
              <span key={name} className={`sq ${(r + file) % 2 ? 'dark' : 'light'}${moved.has(name) ? ' moved' : ''}`}>
                {piece && <span className={`piece ${piece === piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[piece.toLowerCase()]}\uFE0E`}</span>}
              </span>
            )
          }))}
        </div>
      )}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: 180 }}>
        {player(top, flip ? 'white' : 'black')}
        <span className="num" style={{ fontSize: compact ? 22 : 28, color: 'var(--live)' }}>
          {state.match ? `Match ${state.match.score}` : state.result ? state.result.replace('-', '–') : clock}
        </span>
        {player(bottom, flip ? 'black' : 'white')}
        {(state.result || state.match) && clock && <span className="small muted">{clock}</span>}
      </div>
    </div>
  )
}

// Numbers a structured headline already says ("67* (71)", "2/34 (8 ov)", "1 goal, 1 assist",
// "Leads 1–0 in sets", "White vs Nakamura").
const IN_HEADLINE = new Set(['Runs', 'Balls', 'Wickets', 'Overs', 'Runs conceded', 'Goals', 'Assists', 'Sets', 'Colour', 'Opponent'])
const NO_LINE: Record<string, string> = { cricket: "hasn't batted or bowled yet", football: "isn't in the matchday squad", basketball: "isn't on the game's roster" }

/** The followed player's own numbers in the game. */
function PlayerLine({ live, playerName }: { live: LiveStatus; playerName: string }) {
  const stats = live.player_stats ?? []
  if (!live.headline && !stats.length) {
    if (!live.state) return null
    return <span className="small muted">{playerName} {NO_LINE[live.state.kind] ?? 'has no numbers in this game yet'}.</span>
  }
  // Structured lines lead with a headline ("67* (71)", "1 goal"); the agent's lead with the first stat.
  const lead = live.headline ? { label: 'Now', value: live.headline } : stats[0]
  const rest = (live.headline ? stats.filter((s) => !IN_HEADLINE.has(s.label)) : stats.slice(1)).slice(0, 4)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 16px', borderRadius: 14, background: 'var(--bg)', flexWrap: 'wrap' }}>
      <div className="stack">
        <span className="eyebrow">{playerName}{live.headline ? '' : ` · ${lead.label}`}</span>
        <span className="num" style={{ fontSize: 34, color: 'var(--live)' }}>{lead.value}</span>
      </div>
      {rest.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${rest.length}, minmax(0, 1fr))`, gap: 8, flex: 1, minWidth: 200 }}>
          {rest.map((s) => (
            <div key={s.label} className="stack" style={{ alignItems: 'center', textAlign: 'center' }}>
              <span className="num" style={{ fontSize: 22 }}>{s.value}</span>
              <span className="tiny muted">{s.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Where a section came from and when: "ESPNcricinfo · updated 4 min ago". */
export function SourceNote({ provenance, note }: { provenance?: Provenance; note?: string }) {
  if (!provenance && !note) return null
  return (
    <p className="tiny muted" style={{ margin: 0 }}>
      {[note, provenance && `${provenance.source} · updated ${relative(provenance.at)}`].filter(Boolean).join(' · ')}
      {provenance && safeHref(provenance.url) && <> · <a href={safeHref(provenance.url)} target="_blank" rel="noreferrer">Profile</a></>}
    </p>
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
          {u.status === 'live'
            ? <span className="badge-live">Live</span>
            : <span className="num" style={{ fontSize: 20, color: u.start_utc ? 'var(--action)' : 'var(--muted)', whiteSpace: 'nowrap' }}>{u.start_utc ? relative(u.start_utc) : 'TBC'}</span>}
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
            {safeHref(r.source_url)
              ? <a className="title" href={safeHref(r.source_url)} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>{r.title}</a>
              : <span className="title">{r.title}</span>}
            <span className="small muted">{[r.date && day(r.date), r.competition].filter(Boolean).join(' · ')}</span>
            {r.player_contribution && <span className="small" style={{ fontWeight: 600 }}>{r.player_contribution}</span>}
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
        ? [card.live.score, card.live.headline ?? (card.live.player_stats?.[0] && `${card.live.player_stats[0].value} ${card.live.player_stats[0].label}`)].filter(Boolean).join(' · ')
        : card.upcoming?.[0]
          ? `Next · ${card.upcoming[0].title}${card.upcoming[0].start_utc ? `, ${relative(card.upcoming[0].start_utc)}` : ''}`
          : card.player.status === 'retired' ? 'Retired · news only' : sportName(card.player.sport)
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

import type { CSSProperties, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { BasketballSide, BasketballState, Card, ChessSide, ChessState, CricketState, EsportsState, EsportsTeam, FootballSide, FootballState, LatestResult, LiveStatus, NewsItem, Provenance, RecentResult, Stat, TennisState, UpcomingEvent } from '../api'
import { countdown, dateTime, day, dayLabel, hostname, initials, lineStats, outcome, parseDate, relative, safeHref, shortName, shortWhen, sportKey, sportName } from '../format'
import { SportMark } from './Icons'

export function Avatar({ name, sport, size }: { name: string; sport?: string | null; size?: 'sm' | 'lg' }) {
  return <span className={`avatar${size ? ` ${size}` : ''}`} data-sport={sportKey(sport)} aria-hidden="true">{initials(name)}</span>
}

export function StatGrid({ stats, empty = 'No stats yet.' }: { stats?: Stat[]; empty?: string }) {
  if (!stats?.length) return <p className="muted small">{empty}</p>
  return (
    <div className="stat-grid">
      {stats.map((s) => (
        <div className="stat" key={s.label}>
          <span className="num">{s.value}</span>
          <span className="tiny">{s.label}</span>
        </div>
      ))}
    </div>
  )
}

/** The section heading every page uses: a leaning title and, on the right, a link or a note. */
export function SectionHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="section-head">
      <h2 className="dsp section-title">{title}</h2>
      {children}
    </div>
  )
}

/** The player's numbers from a finished game ("22 PTS · 11 REB · 10 AST") in blocks of their sport's colour. */
export function LineStats({ text, sport }: { text?: string | null; sport?: string | null }) {
  const stats = lineStats(text)
  if (!stats) return text ? <span className="title">{text}</span> : null
  return (
    <div className="line-stats" data-sport={sportKey(sport)} style={{ '--n': Math.min(stats.length, 4) } as CSSProperties}>
      {stats.slice(0, 4).map((s) => (
        <span key={s.label}><span className="num">{s.value}</span><span className="tiny">{s.label}</span></span>
      ))}
    </div>
  )
}

/** A line worth a word: a triple-double, a hundred. */
export function highlight(sport: string | null | undefined, text?: string | null): string | null {
  const key = sportKey(sport)
  if (key === 'basketball') {
    const tens = (lineStats(text) ?? []).filter((s) => /^(PTS|REB|AST|STL|BLK)$/i.test(s.label) && Number(s.value) >= 10).length
    return tens >= 3 ? 'Triple-double' : tens === 2 ? 'Double-double' : null
  }
  if (key === 'cricket') {
    const runs = Math.max(0, ...[...(text ?? '').matchAll(/(?:^|&\s*)(\d+)\*?\s*\(/g)].map((m) => Number(m[1])))
    return runs >= 100 ? 'Hundred' : runs >= 50 ? 'Fifty' : null
  }
  return null
}

/** Today's big moment: the newest finished game, in the player's colour, their numbers huge. */
export function ResultHero({ result, sport }: { result: LatestResult; sport?: string | null }) {
  const key = sportKey(sport)
  const stats = lineStats(result.player_contribution)?.slice(0, 3)
  const word = highlight(sport, result.player_contribution)
  // A line that only restates the result ("Lost in 2 sets") gives way to the score.
  const restates = /^(won|lost|drew)\b/i.test(result.player_contribution ?? '')
  const big = result.player_contribution && !restates ? result.player_contribution : result.result.replace(/^(Won|Lost|Drew)\s+/, '')
  return (
    <Link to={`/player/${result.player_id}`} className="hero" data-sport={key} aria-label={`${result.player_name}: ${result.title}, ${result.result}`}>
      <SportMark sport={key} className="hero-mark" />
      <span className="row">
        <span className="pill ink" style={{ background: 'var(--on-sport)', color: 'var(--sport)' }}>{dayLabel(result.date)}</span>
        {result.competition && <span className="tag soft" style={{ textAlign: 'right' }}>{result.competition}</span>}
      </span>
      <span className="dsp hero-name">{shortName(result.player_name)}</span>
      {stats ? (
        <span className="hero-stats" style={{ '--n': stats.length } as CSSProperties}>
          {stats.map((s) => <span key={s.label} className="stack" style={{ gap: 2 }}><span className="dsp">{s.value}</span><span className="tag">{s.label}</span></span>)}
        </span>
      ) : (
        <span className="dsp" style={{ fontSize: 56 }}>{big}</span>
      )}
      <span className="hero-foot">
        <span className="stack" style={{ gap: 2 }}>
          <span>{result.result}</span>
          <span className="small soft" style={{ fontWeight: 600 }}>{result.title}</span>
        </span>
        {word && <span className="pill outline">{word}</span>}
      </span>
    </Link>
  )
}

/** The next game for one of your players, as one ink strip with a countdown. */
export function NextStrip({ event, playerId, sport }: { event: UpcomingEvent & { players?: string[] }; playerId: number; sport?: string | null }) {
  const left = countdown(event.start_utc)
  return (
    <Link to={`/player/${playerId}`} className="strip" data-sport={sportKey(sport)}>
      <span className="tag">Next</span>
      <span className="dsp when">{shortWhen(event.start_utc)}</span>
      <span className="what">{[event.players?.map(shortName).join(', '), event.title].filter(Boolean).join(' · ')}</span>
      {left && <span className="dsp count">{left}</span>}
    </Link>
  )
}

/** What a player's tile says: live, then a game this week, then the last result, then the news. */
export function statusLine(card: Card, progress?: string[]): string {
  if (card.status === 'building') return progress?.at(-1) ?? 'Building page…'
  if (card.status === 'failed') return 'Build failed, open to retry'
  if (card.live.is_live) {
    const lead = card.live.headline ?? (card.live.player_stats?.[0] && `${card.live.player_stats[0].value} ${card.live.player_stats[0].label}`)
    return [card.live.score, lead].filter(Boolean).join(' · ') || 'Live now'
  }
  const now = Date.now()
  const next = (card.upcoming ?? []).find((u) => (parseDate(u.start_utc)?.getTime() ?? 0) > now)
  const nextAt = parseDate(next?.start_utc)?.getTime() ?? Infinity
  const last = card.recent_results?.[0]
  const lastAt = parseDate(last?.date)?.getTime() ?? 0
  if (next && nextAt - now < 7 * 864e5) return `${shortWhen(next.start_utc)} · ${next.title}`
  if (last && now - lastAt < 14 * 864e5) return last.result
  if (next) return `Next ${day(next.start_utc)} · ${next.title}`
  if (card.player.status === 'retired') return 'Retired · news only'
  return card.news?.[0]?.headline ?? sportName(card.player.sport)
}

/** One followed player on Today: a block of their sport's colour. */
export function PlayerTile({ card, progress }: { card: Card; progress?: string[] }) {
  const live = card.live.is_live
  return (
    <Link to={`/player/${card.player_id}`} className="tile" data-sport={sportKey(card.player.sport)}>
      <span className="sport">{sportName(card.player.sport) || 'Player'}</span>
      <span className="corner">
        {card.status === 'building' ? <span className="spinner" /> : live ? <span className="badge-live">Live</span> : null}
      </span>
      <span className="dsp name">{shortName(card.player.name)}</span>
      <span className="status" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{statusLine(card, progress)}</span>
    </Link>
  )
}

/**
 * The live scoreboard. Scores from a structured source carry a sport-specific state (two sides and
 * scorers for football, innings for cricket); scores the research agent read off a web page are
 * text, shown in one generic layout.
 */
export function LiveScoreboard({ live, playerName, sport, compact }: { live: LiveStatus; playerName: string; sport?: string | null; compact?: boolean }) {
  const state = live.state
  return (
    <section className={`board${compact ? ' compact' : ''}`} data-sport={sportKey(sport ?? state?.kind)} aria-label="Live now">
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
      ) : state?.kind === 'esports' && state.teams.length === 2 ? (
        <EsportsBoard state={state} clock={live.clock} compact={compact} />
      ) : state?.kind === 'chess' && state.white && state.black ? (
        <ChessBoard state={state} clock={live.clock} flip={(live.player_stats ?? []).some((s) => s.label === 'Colour' && s.value === 'Black')} compact={compact} />
      ) : (
        <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="num score" style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{live.score || '—'}</div>
          {live.clock && <div className="num clock">{live.clock}</div>}
        </div>
      )}
      <PlayerLine live={live} playerName={playerName} />
      {!compact && live.moments && live.moments.length > 0 && (
        <ul className="moments">
          {live.moments.slice(0, 5).map((m, i) => (
            <li key={`${m.clock}-${i}`}>
              <span className="num" style={{ fontSize: 17, minWidth: 44 }}>{m.clock}</span>
              <span className={`moment-kind ${m.kind.toLowerCase()}`}>{m.kind === 'GOAL' ? 'Goal' : m.kind === 'RED' ? 'Red' : 'Yellow'}</span>
              <span className="small">{m.text}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="row tiny muted">
        <span>{live.as_of ? `Updated ${relative(live.as_of)}` : 'Live'}{live.source ? ` · ${live.source}` : live.source_url ? ` · ${hostname(live.source_url)}` : ''}</span>
        {/* Compact boards sit inside a link to the player's page; a link can't hold another link. */}
        {!compact && safeHref(live.source_url) && <a href={safeHref(live.source_url)} target="_blank" rel="noreferrer" className="link" style={{ color: 'var(--text)' }}>Open source</a>}
      </div>
    </section>
  )
}

function FootballBoard({ state, clock, compact }: { state: FootballState; clock?: string | null; compact?: boolean }) {
  const side = (s: FootballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title">{s.name}</span>
      {!compact && s.scorers.map((g) => <span key={g} className="tiny muted">{g}</span>)}
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
      {side(state.home, 'left')}
      <div className="stack" style={{ alignItems: 'center' }}>
        <span className="num score">{state.home.score}–{state.away.score}</span>
        {clock && <span className="num clock">{clock}</span>}
      </div>
      {side(state.away, 'right')}
    </div>
  )
}

function CricketBoard({ state, clock, compact }: { state: CricketState; clock?: string | null; compact?: boolean }) {
  if (!state.innings.length) {
    return (
      <div className="stack">
        <span className="num" style={{ fontSize: compact ? 28 : 36 }}>{state.teams.map((t) => t.name).join(' v ')}</span>
        {clock && <span className="live-note">{clock}</span>}
      </div>
    )
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      {state.innings.map((i) => (
        <div key={`${i.team}-${i.period}`} className="row" style={{ alignItems: 'baseline' }}>
          <span className="title" style={{ color: i.batting ? 'var(--text)' : 'var(--muted)' }}>
            {i.abbr || i.team}
            {i.batting && <span className="tiny live-note" style={{ marginLeft: 8 }}>batting</span>}
          </span>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span className={i.batting ? 'num score' : 'num'} style={i.batting ? undefined : { fontSize: 26, color: 'var(--muted)' }}>
              {i.runs}{i.wickets < 10 ? `/${i.wickets}` : ''}
            </span>
            {i.overs != null && <span className="small muted">{i.overs} ov</span>}
          </span>
        </div>
      ))}
      {(state.summary || clock) && <span className="live-note">{[clock, state.summary !== clock && state.summary].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

/** Away at home, as US leagues write it, with the quarter-by-quarter line under it. */
function BasketballBoard({ state, clock, compact }: { state: BasketballState; clock?: string | null; compact?: boolean }) {
  const side = (s: BasketballSide, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title">{s.name}</span>
      <span className="tiny muted">{align === 'left' ? 'Away' : 'Home'}</span>
    </div>
  )
  const periods = Math.max(state.away.periods.length, state.home.periods.length)
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
        {side(state.away, 'left')}
        <div className="stack" style={{ alignItems: 'center' }}>
          <span className="num score">{state.away.score}–{state.home.score}</span>
          {clock && <span className="num clock">{clock}</span>}
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
                  <td key={i} className={set?.won ? 'set-won' : undefined}>
                    {set?.games ?? ''}{set?.tiebreak != null && <sup>{set.tiebreak}</sup>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {clock && <span className="live-note">{[state.round, clock].filter(Boolean).join(' · ')}</span>}
    </div>
  )
}

/** A series: the two teams and maps won, the map on now, and one chip a map with who took it. */
function EsportsBoard({ state, clock, compact }: { state: EsportsState; clock?: string | null; compact?: boolean }) {
  const [a, b] = state.teams
  const side = (t: EsportsTeam, align: 'left' | 'right') => (
    <div className="stack" style={{ alignItems: align === 'left' ? 'flex-start' : 'flex-end', textAlign: align, minWidth: 0 }}>
      <span className="title">{t.name}</span>
      {t.abbr && t.abbr !== t.name && <span className="tiny muted">{t.abbr}</span>}
    </div>
  )
  const tag = (id?: string | null) => state.teams.find((t) => t.id === id)?.abbr || state.teams.find((t) => t.id === id)?.name
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 12 }}>
        {side(a, 'left')}
        <div className="stack" style={{ alignItems: 'center' }}>
          <span className="num score">{a.score}–{b.score}</span>
          {clock && <span className="num clock">{clock}</span>}
        </div>
        {side(b, 'right')}
      </div>
      {!compact && state.maps.length > 0 && (
        <div className="maps" aria-label="Maps">
          {state.maps.map((m) => (
            <span key={m.n} className={`map${m.status === 'running' ? ' running' : ''}`}>
              Map {m.n}{m.winner ? <span className="num">{tag(m.winner)}</span> : m.status === 'running' ? ' · now' : ''}
            </span>
          ))}
        </div>
      )}
      {state.game && <span className="live-note">{state.game}</span>}
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
                {piece && <span className={`piece ${piece === piece.toUpperCase() ? 'w' : 'b'}`}>{`${PIECES[piece.toLowerCase()]}︎`}</span>}
              </span>
            )
          }))}
        </div>
      )}
      <div className="stack" style={{ gap: 10, flex: 1, minWidth: 180 }}>
        {player(top, flip ? 'white' : 'black')}
        <span className="num clock" style={{ fontSize: compact ? 24 : 30 }}>
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

/** The followed player's own numbers in the game, on their sport's colour. */
function PlayerLine({ live, playerName }: { live: LiveStatus; playerName: string }) {
  const stats = live.player_stats ?? []
  if (!live.headline && !stats.length) {
    if (!live.state || live.state.kind === 'esports') return null  // esports results are the team's: no player numbers
    return <span className="small muted">{playerName} {NO_LINE[live.state.kind] ?? 'has no numbers in this game yet'}.</span>
  }
  // Structured lines lead with a headline ("67* (71)", "1 goal"); the agent's lead with the first stat.
  const lead = live.headline ? { label: 'Now', value: live.headline } : stats[0]
  const rest = (live.headline ? stats.filter((s) => !IN_HEADLINE.has(s.label)) : stats.slice(1)).slice(0, 4)
  return (
    <div className="player-line">
      <div className="stack">
        <span className="eyebrow">{playerName}{live.headline ? '' : ` · ${lead.label}`}</span>
        <span className="num lead">{lead.value}</span>
      </div>
      {rest.length > 0 && (
        <div className="rest" style={{ '--n': rest.length } as CSSProperties}>
          {rest.map((s) => (
            <div key={s.label} className="stack" style={{ alignItems: 'center', textAlign: 'center' }}>
              <span className="num">{s.value}</span>
              <span className="tiny">{s.label}</span>
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
    <p className="tiny muted">
      {[note, provenance && `${provenance.source} · updated ${relative(provenance.at)}`].filter(Boolean).join(' · ')}
      {provenance && safeHref(provenance.url) && <> · <a href={safeHref(provenance.url)} target="_blank" rel="noreferrer" className="link">Profile</a></>}
    </p>
  )
}

/** Fixtures, the day and time in a block on the left. */
export function UpcomingList({ items, who, empty }: { items: (UpcomingEvent & { players?: string[] })[]; who?: boolean; empty: string }) {
  if (!items.length) return <div className="card empty small">{empty}</div>
  return (
    <ul className="card list">
      {items.map((u) => {
        const d = parseDate(u.start_utc)
        return (
          <li key={`${u.title}-${u.start_utc ?? ''}`} className="fixture">
            <span className="when-block">
              <span className="tag">{d ? d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }) : 'TBC'}</span>
              <span className="num">{d ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '–'}</span>
            </span>
            <span className="stack" style={{ flex: 1 }}>
              <span className="title">{u.title}</span>
              <span className="small muted">
                {[who && u.players?.join(', '), u.competition, d ? null : 'Date to be confirmed', u.venue].filter(Boolean).join(' · ')}
              </span>
              {u.notes && <span className="small muted">{u.notes}</span>}
            </span>
            {u.status === 'live'
              ? <span className="badge-live">Live</span>
              : d && <span className="tag" style={{ whiteSpace: 'nowrap' }} title={dateTime(u.start_utc)}>{relative(u.start_utc)}</span>}
          </li>
        )
      })}
    </ul>
  )
}

export function NewsList({ items, who, brief, empty }: { items: (NewsItem & { players?: string[] })[]; who?: boolean; brief?: boolean; empty: string }) {
  if (!items.length) return <div className="card empty small">{empty}</div>
  return (
    <ul className="card list">
      {items.map((n) => (
        <li key={n.url || n.headline}>
          <a href={safeHref(n.url)} target="_blank" rel="noreferrer" className="stack">
            <span className="title" style={{ fontSize: 16 }}>{n.headline}</span>
            <span className="tiny muted" style={{ fontWeight: 700 }}>{[who && n.players?.join(', '), n.source, n.published && day(n.published)].filter(Boolean).join(' · ')}</span>
            {!who && !brief && n.summary && <span className="small muted">{n.summary}</span>}
          </a>
        </li>
      ))}
    </ul>
  )
}

export function ResultsList({ items, teams }: { items?: RecentResult[]; teams?: string[] }) {
  if (!items?.length) return <div className="card empty small">No recent results found.</div>
  return (
    <ul className="card list">
      {items.map((r) => {
        const wl = outcome(r.result, teams)
        const score = /^(Won|Lost|Drew)\s+/.test(r.result) ? r.result.replace(/^(Won|Lost|Drew)\s+/, '') : null
        return (
          <li key={`${r.title}-${r.date ?? ''}`} className="row" style={{ alignItems: 'flex-start', justifyContent: 'flex-start' }}>
            {wl && <span className={`wl ${wl}`} role="img" aria-label={{ W: 'Won', D: 'Drew', L: 'Lost' }[wl]}>{wl}</span>}
            <div className="stack" style={{ flex: 1 }}>
              {safeHref(r.source_url)
                ? <a className="title" href={safeHref(r.source_url)} target="_blank" rel="noreferrer">{r.title}</a>
                : <span className="title">{r.title}</span>}
              <span className="small muted">{[r.date && day(r.date), r.competition].filter(Boolean).join(' · ')}</span>
              {!score && <span className="small" style={{ fontWeight: 800 }}>{r.result}</span>}
              {r.player_contribution && <span className="small" style={{ fontWeight: 800 }}>{r.player_contribution}</span>}
            </div>
            {score && <span className="num" style={{ fontSize: 20, whiteSpace: 'nowrap' }}>{score}</span>}
          </li>
        )
      })}
    </ul>
  )
}

/** Won, drew or lost for the last five results whose outcome is known, newest first. */
export function formMarks(results: RecentResult[], teams?: string[]): ('W' | 'D' | 'L')[] {
  return results.map((r) => outcome(r.result, teams)).filter((m): m is 'W' | 'D' | 'L' => m != null).slice(0, 5)
}

/** The last few results as a row of badges, newest first. */
export function FormStrip({ marks }: { marks: ('W' | 'D' | 'L')[] }) {
  const words = { W: 'won', D: 'drew', L: 'lost' }
  return (
    <span className="row" style={{ gap: 4, flexShrink: 0 }} role="img" aria-label={`Last ${marks.length}: ${marks.map((m) => words[m]).join(', ')}`}>
      {marks.map((m, i) => <span key={i} className={`wl ${m}`} aria-hidden="true">{m}</span>)}
    </span>
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
      <p className="small muted">The first time anyone follows a player, the agent researches them. This takes one to two minutes; every later fan gets the shared page instantly.</p>
      {messages.length > 0 && <ol className="progress-log">{messages.map((m, i) => <li key={i}>{m}</li>)}</ol>}
    </div>
  )
}

/** A followed player in the desktop sidebar. */
export function PlayerRow({ card, progress }: { card: Card; progress?: string[] }) {
  const live = card.live.is_live
  return (
    <Link to={`/player/${card.player_id}`} className="row" style={{ padding: '8px 10px', minHeight: 44, borderRadius: 12 }}>
      <div className="row" style={{ justifyContent: 'flex-start', minWidth: 0 }}>
        <Avatar name={card.player.name} sport={card.player.sport} size="sm" />
        <div className="stack" style={{ gap: 1 }}>
          <span className="title" style={{ fontSize: 14 }}>{card.player.name}</span>
          <span className="tiny" style={{ color: live ? 'var(--live-text)' : 'var(--muted)', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{statusLine(card, progress)}</span>
        </div>
      </div>
      {card.status === 'building' ? <span className="spinner" /> : live ? <span className="live-dot" /> : null}
    </Link>
  )
}

/** The top of a player's page (and an athlete's preview): a block of their sport's colour. */
export function PlayerBand({ name, sport, meta, back, actions, children }: { name: string; sport?: string | null; meta?: string; back: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="player-band" data-sport={sportKey(sport)}>
      <div className="bar">
        {back}
        <span className="row" style={{ gap: 8 }}>{actions}</span>
      </div>
      <div className="stack" style={{ gap: 10 }}>
        {sport && <span className="tag soft">{sportName(sport)}</span>}
        <h1 className="dsp player-name">{name}</h1>
        {meta && <span className="player-meta">{meta}</span>}
      </div>
      {children}
    </header>
  )
}

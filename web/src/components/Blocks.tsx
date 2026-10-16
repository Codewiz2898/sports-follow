import { useState, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useNow } from '../clock'
import type { Card, LiveStatus, NewsItem, Photo, Provenance, RecentResult, Stat, UpcomingEvent } from '../api'
import { countdown, dateOnly, dateTime, day, hostname, initials, lineStats, longestWordEms, outcome, parseDate, playedOn, relative, safeHref, shortName } from '../format'
import { comingCard, highlight as sportHighlight, liveLine, playerLine, resultCard, statusLine as sharedStatusLine, WON, wordSize, type GameResult } from '../lines'
import { Backdrop, sportKey, sportName, viewOfKind } from '../sports'
import type { LatestMoment } from '../today'
import { ErrorBoundary } from './ErrorBoundary'

/** The photo to show, if any, and a way to drop it when it doesn't load (one Commons removed, say): then
 * its credit goes too. */
function usePhoto(photo?: Photo | null): [Photo | null, () => void] {
  const [broken, setBroken] = useState<string | null>(null)
  return [photo && photo.url !== broken ? photo : null, () => setBroken(photo?.url ?? null)]
}

/** A player's photo from Wikimedia Commons, in its own colours (.photo in styles.css): on their profile
 * only, never on the home page. Their name is beside it, so it says nothing to a screen reader. The square
 * around their face, when the photos job found it, fills the picture (.photo.framed). */
function PlayerPhoto({ photo, onError }: { photo: Photo; onError: () => void }) {
  const c = photo.crop
  const square = c && ({ '--cw': c.w, '--ch': c.h, '--cx': c.x + c.w / 2, '--cy': c.y + c.h / 2 } as CSSProperties)
  return (
    <span className={`photo band-photo${c ? ' framed' : ''}`} title={photo.credit} aria-hidden="true">
      <img src={photo.url} alt="" loading="lazy" decoding="async" style={square} onError={onError} />
    </span>
  )
}

/** A shown photo's credit, as its licence asks: "Photo: <author>, <licence>, Wikimedia Commons, cropped",
 * linking to the file's page on Commons. */
function PhotoCredit({ photo }: { photo: Photo }) {
  const href = safeHref(photo.page)
  return href
    ? <a className="photo-credit" href={href} target="_blank" rel="noreferrer">{photo.credit}</a>
    : <span className="photo-credit">{photo.credit}</span>
}

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

/** A line worth a word, as its sport words it: a triple-double, a hundred (lines.ts). */
export function highlight(sport: string | null | undefined, text?: string | null): string | null {
  return sportHighlight(sportKey(sport), text)
}

export type { GameResult }

/**
 * A finished game of the fan's on Today: a block of its sport's colour with one big word (the sport's word
 * for the line, a hat-trick or a hundred, else the result), each of the fan's players in it with their line,
 * and their win, draw or loss. Players whose games went differently (opponents, or teams apart in one lobby)
 * share the card too: then it shows the score alone, or the game, and each player's line says how theirs went.
 * The hero, the first card on a day with nothing live, is bigger and has the first player's numbers.
 */
export function ResultCard({ game, hero, now }: { game: GameResult; hero?: boolean; now: number }) {
  const key = sportKey(game.players[0].sport)
  const { r, several, word, wl, when, stats, match, lines, who } = resultCard(game, Boolean(hero), now, key)
  const badge = wl && <span className={`wl ${wl}`} role="img" aria-label={WON[wl]}>{wl}</span>
  return (
    <Link to={`/player/${r.player_id}`} className={`result-card${hero ? ' hero' : ''}`} data-sport={key}>
      <Backdrop sport={key} seed={game.key} />
      {hero ? (
        <span className="row"><span className="pill" style={{ background: 'var(--on-sport)', color: 'var(--sport)' }}>{when}</span>{badge}</span>
      ) : (
        <span className="row"><span className="eyebrow">{[when, r.competition].filter(Boolean).join(' · ')}</span>{badge}</span>
      )}
      <span className="dsp word" style={{ '--word': `${wordSize(word, hero)}px` } as CSSProperties}>{word}</span>
      <span className="stack" style={{ gap: hero ? 12 : 2 }}>
        <span className="dsp who">{who}</span>
        {stats && (
          <span className="stack" style={{ gap: 4 }}>
            {several && <span className="tag soft">{shortName(r.player_name)}</span>}
            <span className="hero-stats" style={{ '--n': stats.length } as CSSProperties}>
              {stats.map((s) => <span key={s.label} className="stack" style={{ gap: 2 }}><span className="dsp">{s.value}</span><span className="tag soft">{s.label}</span></span>)}
            </span>
          </span>
        )}
        {!hero && match !== word && <span className="line">{match}</span>}
        {lines.map((l) => <span key={l} className="line strong">{l}</span>)}
      </span>
      {hero && (
        <span className="hero-foot">
          {match !== word && <span className="line">{match}</span>}
          {r.competition && <span className="line soft" style={{ textAlign: 'right', marginLeft: 'auto' }}>{r.competition}</span>}
        </span>
      )}
    </Link>
  )
}

/**
 * One of the fan's games still to come, on Today: an ink card with, big in the sport's colour, the countdown
 * once it's less than a day away (else the day), then the game and a chip naming the fan's players in it.
 */
export function ComingCard({ event, players, seed }: { event: UpcomingEvent; players: Card[]; seed: string }) {
  const now = useNow()
  const key = sportKey(players[0].player.sport)
  const { big, eyebrow } = comingCard(event, now)
  return (
    <Link to={`/player/${players[0].player_id}`} className="next-card coming" data-sport={key}>
      <Backdrop sport={key} seed={seed} />
      <span className="eyebrow muted">{eyebrow}</span>
      <span className="dsp count">{big}</span>
      <span className="stack" style={{ gap: 6 }}>
        <span className="what">{event.title}</span>
        <span className="player-chip">{players.map((c) => shortName(c.player.name)).join(' · ')}</span>
      </span>
    </Link>
  )
}

/** A player's next game, on their page: an ink card, the countdown in their sport's colour. `children` sit
 * at its foot beside when and where it is (the page's refresh button). */
export function NextCard({ event, sport, children }: { event: UpcomingEvent; sport?: string | null; children?: ReactNode }) {
  const left = countdown(event.start_utc, useNow())
  return (
    <section className="next-card" data-sport={sportKey(sport)} aria-label="Next game">
      <span className="row">
        <span className="eyebrow" style={{ color: 'var(--sport-on-ink)' }}>Next game</span>
        {event.competition && <span className="tiny muted" style={{ fontWeight: 700, textAlign: 'right' }}>{event.competition}</span>}
      </span>
      <span className="row" style={{ alignItems: 'flex-end' }}>
        <span className="title" style={{ fontSize: 20 }}>{event.title}</span>
        {left && <span className="dsp count">{left}</span>}
      </span>
      <span className="row small" style={{ paddingTop: 12, borderTop: '1px solid rgba(255,255,255,.16)', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700 }}>{[event.start_utc ? dateTime(event.start_utc) : 'Date to be confirmed', event.venue].filter(Boolean).join(' · ')}</span>
        {children}
      </span>
    </section>
  )
}

/** What a player's tile says: live, then a game this week, then the last result, then the news (lines.ts). */
export function statusLine(card: Card, progress?: string[], now = Date.now()): string {
  return sharedStatusLine(card, progress, now, sportName(card.player.sport))
}

/** One followed player on the Following page: a block of their sport's colour. */
export function PlayerTile({ card, progress }: { card: Card; progress?: string[] }) {
  const now = useNow()
  const live = card.live.is_live
  return (
    <Link to={`/player/${card.player_id}`} className="tile" data-sport={sportKey(card.player.sport)}>
      <span className="sport">{sportName(card.player.sport) || 'Player'}</span>
      <span className="corner">
        {card.status === 'building' ? <span className="spinner" /> : live ? <span className="badge-live">Live</span> : null}
      </span>
      <span className="dsp name">{shortName(card.player.name)}</span>
      <span className="status" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{statusLine(card, progress, now)}</span>
    </Link>
  )
}

export type { LatestMoment }

/**
 * The live scoreboard. Scores from a structured source carry a sport-specific state (two sides and
 * scorers for football, innings for cricket), drawn by that sport's view (sports/); scores the research
 * agent read off a web page, and states no view draws, are text in one generic layout. Today's is compact:
 * with a `seed` (its game key) the sport's line art sits faint behind it, and the player's line is one row
 * with the game's `latest` moment under it.
 */
export function LiveScoreboard({ live, playerName, sport, teams, compact, seed, latest }: { live: LiveStatus; playerName: string; sport?: string | null; teams?: string[]; compact?: boolean; seed?: string; latest?: LatestMoment | null }) {
  const now = useNow()
  const key = sportKey(sport ?? live.state?.kind)
  return (
    <section className={`board${compact ? ' compact' : ''}${seed ? ' drawn' : ''}`} data-sport={key} aria-label="Live now">
      {/* On Today, the sport's line art behind it, turned its own way for this game (seed). */}
      {seed && <Backdrop sport={key} seed={seed} />}
      <div className="row">
        <span className="badge-live">Live</span>
        {live.event && <span className="eyebrow" style={{ textAlign: 'right' }}>{live.event}</span>}
      </div>
      {/* A state in a shape this app doesn't know shows as the plain score, not a blank page. A new card tries again. */}
      <ErrorBoundary fallback={<PlainScore live={live} />} resetKey={live}>
        <GameBoard live={live} compact={compact} teams={teams} />
        {/* No player named (a campaign's game on now that the fan follows nobody in): the board alone. */}
        {playerName && (compact ? <LiveLine live={live} playerName={playerName} latest={latest} /> : <PlayerLine live={live} playerName={playerName} />)}
        {!compact && live.moments && live.moments.length > 0 && (
          <ul className="moments">
            {live.moments.slice(0, 5).map((m, i) => (
              <li key={`${m.clock}-${i}`}>
                <span className="num" style={{ fontSize: 17, minWidth: 44 }}>{m.clock}</span>
                <span className={`moment-kind ${m.kind.toLowerCase()}`}>{m.kind[0] + m.kind.slice(1).toLowerCase()}</span>
                <span className="small">{m.text}</span>
              </li>
            ))}
          </ul>
        )}
      </ErrorBoundary>
      <div className="row tiny muted">
        <span>{live.as_of ? `Updated ${relative(live.as_of, now)}` : 'Live'}{live.source ? ` · ${live.source}` : live.source_url ? ` · ${hostname(live.source_url)}` : ''}</span>
        {/* Compact boards sit inside a link to the player's page; a link can't hold another link. */}
        {!compact && safeHref(live.source_url) && <a href={safeHref(live.source_url)} target="_blank" rel="noreferrer" className="link" style={{ color: 'var(--text)' }}>Open source</a>}
      </div>
    </section>
  )
}

/** A live game's score and clock as text: a score the research agent read off a web page, or a state no view draws. */
export function PlainScore({ live }: { live: LiveStatus }) {
  return (
    <div className="row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
      <div className="num score" style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{live.score || '—'}</div>
      {live.clock && <div className="num clock">{live.clock}</div>}
    </div>
  )
}

/** A live game's score as its sport's view draws it (sports/), or the plain score where none draws this state.
 * `teams`: the followed player's, marked on a board that lists many (a battle royale lobby). */
export function GameBoard({ live, compact, teams }: { live: LiveStatus; compact?: boolean; teams?: string[] }) {
  const state = live.state
  const view = viewOfKind(state?.kind)
  return view && state && (view.draws?.(state as never) ?? true) ? <view.Board state={state as never} clock={live.clock} compact={compact} teams={teams} live={live} /> : <PlainScore live={live} />
}

/**
 * The followed player's line on Today's board, in one row: their name in the sport's colour, then their
 * numbers (the headline first, "67* (71)", without the stats it already says). Under it, the newest thing
 * that happened to the fan's players in this game, which the fan missed if it came after they last looked
 * at their alerts.
 */
function LiveLine({ live, playerName, latest }: { live: LiveStatus; playerName: string; latest?: LatestMoment | null }) {
  const { numbers, why } = liveLine(live)
  if (!numbers.length && !why && !latest) return null
  return (
    <div className="live-line">
      {(numbers.length > 0 || why) && (
        <span className="row" style={{ justifyContent: 'flex-start', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 8, rowGap: 2 }}>
          <span className="dsp who">{shortName(playerName)}</span>
          <span className="what">{numbers.length ? numbers.join(' · ') : why}</span>
        </span>
      )}
      {latest && <span className="missed"><b>{latest.missed ? 'You missed' : 'Latest'}</b> {latest.text}</span>}
    </div>
  )
}

/** The followed player's own numbers in the game, on their sport's colour. */
function PlayerLine({ live, playerName }: { live: LiveStatus; playerName: string }) {
  const line = playerLine(live)
  if ('none' in line) return line.none ? <span className="small muted">{playerName} {line.none}.</span> : null
  const { lead, rest } = line
  return (
    <div className="player-line">
      <div className="stack">
        <span className="eyebrow">{playerName}{line.headline ? '' : ` · ${lead.label}`}</span>
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
  const now = useNow()
  if (!provenance && !note) return null
  return (
    <p className="tiny muted">
      {[note, provenance && `${provenance.source} · updated ${relative(provenance.at, now)}`].filter(Boolean).join(' · ')}
      {provenance && safeHref(provenance.url) && <> · <a href={safeHref(provenance.url)} target="_blank" rel="noreferrer" className="link">Profile</a></>}
    </p>
  )
}

/** Fixtures, the day and time in a block on the left. */
export function UpcomingList({ items, who, empty }: { items: (UpcomingEvent & { players?: string[] })[]; who?: boolean; empty: string }) {
  const now = useNow()
  if (!items.length) return <div className="card empty small">{empty}</div>
  return (
    <ul className="card list">
      {items.map((u) => {
        const d = parseDate(u.start_utc)
        const timed = d && !dateOnly(u.start_utc)
        return (
          <li key={`${u.title}-${u.start_utc ?? ''}`} className="fixture">
            <span className="when-block">
              <span className="tag">{d ? d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }) : 'TBC'}</span>
              <span className="num">{timed ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '–'}</span>
            </span>
            <span className="stack" style={{ flex: 1 }}>
              <span className="title">{u.title}</span>
              <span className="small muted">
                {[who && u.players?.join(', '), u.competition, d ? (timed ? null : 'Time to be confirmed') : 'Date to be confirmed', u.venue].filter(Boolean).join(' · ')}
              </span>
              {u.notes && <span className="small muted">{u.notes}</span>}
            </span>
            {u.status === 'live'
              ? <span className="badge-live">Live</span>
              : d && <span className="tag" style={{ whiteSpace: 'nowrap' }} title={dateTime(u.start_utc)}>{relative(u.start_utc, now)}</span>}
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
              <span className="small muted">{[day(playedOn(r)), r.competition].filter(Boolean).join(' · ')}</span>
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

/** A page being built, or one that failed. Without the research agent (research false) it's a name being
 * looked up in the live sources, and a failed one can't be tried again. */
export function BuildProgress({ card, messages, onRetry, research = true }: { card: Card; messages: string[]; onRetry?: () => void; research?: boolean }) {
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
      <p className="small muted">{research
        ? 'The first time anyone follows a player, the agent researches them. This takes one to two minutes; every later fan gets the shared page instantly.'
        : 'Looking them up in the live sources.'}</p>
      {messages.length > 0 && <ol className="progress-log">{messages.map((m, i) => <li key={i}>{m}</li>)}</ol>}
    </div>
  )
}

/** A followed player in the desktop sidebar. */
export function PlayerRow({ card, progress }: { card: Card; progress?: string[] }) {
  const now = useNow()
  const live = card.live.is_live
  return (
    <Link to={`/player/${card.player_id}`} className="row" style={{ padding: '8px 10px', minHeight: 44, borderRadius: 12 }}>
      <div className="row" style={{ justifyContent: 'flex-start', minWidth: 0 }}>
        <Avatar name={card.player.name} sport={card.player.sport} size="sm" />
        <div className="stack" style={{ gap: 1 }}>
          <span className="title" style={{ fontSize: 14 }}>{card.player.name}</span>
          <span className="tiny" style={{ color: live ? 'var(--live-text)' : 'var(--muted)', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{statusLine(card, progress, now)}</span>
        </div>
      </div>
      {card.status === 'building' ? <span className="spinner" /> : live ? <span className="live-dot" /> : null}
    </Link>
  )
}

/** The top of a player's page (and an athlete's preview): a block of their sport's colour, with their
 * photo and its credit when there is one (.with-photo keeps the words off it). */
export function PlayerBand({ name, sport, meta, photo, back, actions, children }: { name: string; sport?: string | null; meta?: string; photo?: Photo | null; back: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  const [shown, drop] = usePhoto(photo)
  return (
    <header className={`player-band${shown ? ' with-photo' : ''}`} data-sport={sportKey(sport)}>
      {shown && <PlayerPhoto photo={shown} onError={drop} />}
      <div className="bar">
        {back}
        <span className="row" style={{ gap: 8 }}>{actions}</span>
      </div>
      <div className="stack" style={{ gap: 10 }}>
        {sport && <span className="tag soft">{sportName(sport)}</span>}
        <h1 className="dsp player-name" style={{ '--ems': longestWordEms(name) } as CSSProperties}>{name}</h1>
        {meta && <span className="player-meta">{meta}</span>}
      </div>
      {children}
      {shown && <PhotoCredit photo={shown} />}
    </header>
  )
}

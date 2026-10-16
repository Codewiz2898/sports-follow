import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Match } from '../api'
import { useNow } from '../clock'
import { GameBoard, PlainScore, SectionHead } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon } from '../components/Icons'
import { FollowPills } from '../components/LiveNow'
import { useFollowToggle } from '../components/Starters'
import { dateTime, relative } from '../format'
import { sportKey, sportName, viewOf } from '../sports'

const EVERY = 30_000  // a look again: a followed player's game is polled every few seconds, any other read once a minute
const SOON = 5_000  // when the server says a read is on its way, the next look
const TRIES = 3  // and how many such looks before waiting the usual time again

/**
 * A game's match page from the server (GET /api/match/<source>/<game id>), looked at again while the page is in
 * view, as Live now is (useLive): a look only reads what the server has, which asks the game's source at most
 * once a minute whoever looks, so more fans looking costs a source nothing more. A hidden tab doesn't look;
 * showing it again does. A game the server doesn't know is an error, and isn't asked about again.
 */
function useMatch(source: string, game: string): { match: Match | null; failed: string | null } {
  const [match, setMatch] = useState<Match | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  useEffect(() => {
    setMatch(null)
    setFailed(null)
    let current = true
    let gone = false  // the server doesn't know the game: nothing to look at again, shown again or not
    let timer: number | undefined
    let latest = 0  // the newest look: an older one's answer starts no second loop
    const look = (tries: number) => {
      window.clearTimeout(timer)
      if (document.hidden || gone) return
      const mine = ++latest
      api.match(source, game)
        .then((m) => {
          if (!current || mine !== latest) return
          setMatch(m)
          setFailed(null)
          timer = window.setTimeout(() => look(m.refreshing ? tries + 1 : 0), m.refreshing && tries < TRIES ? SOON : EVERY)
        })
        .catch((e: Error) => {
          if (!current || mine !== latest) return
          setFailed(e.message)
          gone = e instanceof ApiError && e.status === 404
          if (!gone) timer = window.setTimeout(() => look(tries), EVERY)
        })
    }
    const shown = () => { if (!document.hidden) look(0) }
    document.addEventListener('visibilitychange', shown)
    look(0)
    return () => { current = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [source, game])
  return { match, failed }
}

/** Where the game is: live, over, called off, or when it starts. */
function Status({ match }: { match: Match }) {
  if (match.status === 'live') return <span className="badge-live">Live</span>
  if (match.status === 'final') return <span className="pill" style={{ background: 'var(--on-sport)', color: 'var(--sport)' }}>Result</span>
  if (match.status === 'postponed') return <span className="pill outline">Off</span>
  return match.start_utc ? <span className="pill outline">{dateTime(match.start_utc)}</span> : null
}

interface KeyEvent { clock: string; kind: string; text: string }

/**
 * One game, whoever follows anyone in it, live, to come or over (sports_follow/match.py): a head in its sport's
 * colour with the competition and its board (the sides and the big score, as its sport draws them), then what
 * its sport shows under the board (a timeline, a scorecard, its maps), what was found for the players followed in
 * it, and both sides' players to follow with a tap. Every live game on Today opens it.
 */
export function MatchPage() {
  const { source = '', game = '' } = useParams()
  const { match, failed } = useMatch(source, game)
  const follow = useFollowToggle()
  const now = useNow()
  const back = <Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>

  if (!match) {
    return failed
      ? <div className="stack" style={{ gap: 12 }}>{back}<div className="error-box" role="alert">{failed}</div></div>
      : <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>
  }
  const key = sportKey(match.sport)
  const view = viewOf(key)
  const live = match.live
  const events = (match.page.events ?? []) as KeyEvent[]

  return (
    <>
      <header className="player-band match-band" data-sport={key}>
        <div className="bar">
          {back}
          {/* What a fan can do with the game itself goes here: following it (BHA-270). */}
          <span className="row" style={{ gap: 8 }} />
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <span className="row" style={{ justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
            <Status match={match} />
            <span className="tag soft">{[match.competition, match.detail].filter(Boolean).join(' · ') || sportName(match.sport)}</span>
          </span>
          {/* The board names the sides; without one yet, the title does. */}
          <h1 className={live ? 'sr-only' : 'dsp match-title'}>{match.title}</h1>
        </div>
        {live && (
          <div className="match-board">
            {/* A state in a shape this app doesn't know shows as the plain score. */}
            <ErrorBoundary fallback={<PlainScore live={live} />} resetKey={live}>
              <GameBoard live={live} />
            </ErrorBoundary>
          </div>
        )}
        {live?.as_of && <span className="tiny soft">Updated {relative(live.as_of, now)}{live.source ? ` · ${live.source}` : ''}</span>}
      </header>
      {failed && <div className="notice" role="status">Couldn't update this game: {failed}</div>}

      {view?.Match && (
        <ErrorBoundary fallback={<BlockError />} resetKey={match.page}>
          <view.Match page={match.page as never} live={live} />
        </ErrorBoundary>
      )}
      {events.length > 0 && (
        <section className="section" aria-label="Key moments">
          <SectionHead title="Key moments" />
          <ul className="card list">
            {events.map((e, i) => (
              <li key={`${e.clock}-${i}`} className="row" style={{ justifyContent: 'flex-start' }}>
                <span className="num" style={{ fontSize: 17, minWidth: 44 }}>{e.clock}</span>
                <span className="small">{e.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {match.moments.length > 0 && (
        <section className="section" aria-label="Moments">
          <SectionHead title="Moments" />
          <ul className="card list">
            {match.moments.map((m) => (
              <li key={m.id} className="row" style={{ alignItems: 'flex-start' }}>
                <Link to={`/player/${m.player_id}`} className="stack" style={{ flex: 1 }}>
                  <span className="title">{m.title}</span>
                  {m.body && <span className="small muted">{m.body}</span>}
                </Link>
                <span className="tiny muted" style={{ whiteSpace: 'nowrap' }}>{relative(m.at, now)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {match.sides.length > 0 && (
        <section className="section" aria-label="Players">
          <SectionHead title="Players" />
          <div className="card card-pad stack match-sides" data-sport={key}>
            {match.sides.map((side) => (
              <div key={side.name} className="stack" style={{ gap: 8 }}>
                {side.name && <span className="eyebrow">{side.name}</span>}
                <FollowPills game={{ title: side.name || (match.title ?? ''), players: side.players }} follow={follow} />
              </div>
            ))}
          </div>
          {follow.error && <div className="error-box">{follow.error}</div>}
        </section>
      )}

      <p className="tiny muted">{live?.lag_note ? `${live.lag_note} ` : ''}<Link to="/credits" className="link">Credits</Link></p>
    </>
  )
}

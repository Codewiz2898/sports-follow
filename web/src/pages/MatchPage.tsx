import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useActivity } from '../activity'
import { gameLevelText, LEVELS } from '../alerts'
import { api, ApiError, type Match, type MatchFollowState } from '../api'
import { useNow } from '../clock'
import { GameBoard, PlainScore, SectionHead, StatGrid } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon, BellIcon, CheckIcon, PlusIcon } from '../components/Icons'
import { FollowPills } from '../components/LiveNow'
import { useFollowToggle } from '../components/Starters'
import { dateOnly, dateTime, parseDate, relative } from '../format'
import { useFollowing } from '../following'
import { comingCard } from '../lines'
import { AlertLevels } from '../notifications'
import { sportDrawn, sportKey, sportName, viewOf } from '../sports'

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

/** When a game to come (or about to start, armed, with no board yet) starts, under its title: its countdown
 * within a day, else its day, as Coming up's cards say it; "Starting soon" once its start has passed without
 * it (a delayed start), for a few hours; else nothing. */
function startsIn(match: Match, now: number): string | null {
  const at = parseDate(match.start_utc)?.getTime()
  if (at == null) return null
  // A day with no time yet: its day, whatever the hour.
  if (at > now || dateOnly(match.start_utc)) return comingCard({ title: match.title ?? '', competition: '', start_utc: match.start_utc }, now).big
  return now - at < LATE ? 'Starting soon' : null
}

const LATE = 6 * 36e5  // how long after its start a game that hasn't begun is still "starting soon"

/** A game's board means something once it's under way: before, the head says when it starts instead. */
const UNDER_WAY = ['live', 'final', 'armed']

/** Where the game is: live, over, called off, or when it starts. */
function Status({ match }: { match: Match }) {
  if (match.status === 'live') return <span className="badge-live">Live</span>
  if (match.status === 'final') return <span className="pill" style={{ background: 'var(--on-sport)', color: 'var(--sport)' }}>Result</span>
  if (match.status === 'postponed') return <span className="pill outline">Off</span>
  return match.start_utc ? <span className="pill outline">{dateTime(match.start_utc)}</span> : null
}

interface KeyEvent { clock: string; kind: string; text: string }

/**
 * Following the game by itself (sports_follow/matches.py): its key moments from both sides reach the fan until
 * it ends, whoever they follow. The page's answer says whether they do and whether its moments can reach them
 * at all; a toggle shows at once, and stays until the page's next look agrees. Once followed, the fan's level
 * for it and "Mute this game", as a player's page has them.
 */
function useFollowGame(match: Match | null) {
  const { followMatch, matches, setMatchAlerts } = useFollowing()
  const { muted, setMuted } = useActivity()
  const [mine, setMine] = useState<MatchFollowState | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const said = match?.follow ?? null
  useEffect(() => { setMine(null); setOpen(false); setError(null) }, [match?.key])
  useEffect(() => { if (mine && said && said.following === mine.following) setMine(null) }, [said, mine])
  const state = mine ?? said
  const followed = match ? matches.find((m) => match.event_ids.includes(m.event_id)) : undefined
  const attempt = (work: () => Promise<unknown>) => { setError(null); return work().catch((e: Error) => setError(e.message)) }
  const toggle = async () => {
    if (!match || !state) return
    setBusy(true)
    await attempt(async () => {
      await followMatch(match.match, !state.following)
      setMine({ ...state, following: !state.following })
      if (state.following) setOpen(false)
    })
    setBusy(false)
  }
  return { state, busy, open, setOpen, error, toggle, followed, muted, setMuted, setMatchAlerts, attempt }
}

/**
 * One game, whoever follows anyone in it, live, to come or over (sports_follow/match.py): a head in its sport's
 * colour with the competition and its board (the sides and the big score, as its sport draws them), and over
 * a finished game's board who won, big; a game to come has its countdown there instead. Then what its sport
 * shows under the board (a timeline, a scorecard, its maps, a chess game's moves), the fan's players in it with
 * their lines, what was found for the players followed in it, and both sides' players to follow with a tap.
 * Every game on Today opens it, live, over or to come, and so does a game on a player's Matches tab.
 */
export function MatchPage() {
  const { source = '', game = '' } = useParams()
  const { match, failed } = useMatch(source, game)
  const follow = useFollowToggle()
  const watch = useFollowGame(match)
  const now = useNow()
  const back = <Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>

  if (!match) {
    return failed
      ? <div className="stack" style={{ gap: 12 }}>{back}<div className="error-box" role="alert">{failed}</div></div>
      : <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>
  }
  const key = sportKey(match.sport)
  const view = viewOf(sportDrawn(match.sport)) // an esports game is drawn as esports
  const live = match.live
  const board = live && UNDER_WAY.includes(match.status) ? live : null
  const soon = !board && (match.status === 'scheduled' || match.status === 'armed') ? startsIn(match, now) : null
  const events = (match.page.events ?? []) as KeyEvent[]
  const yours = match.yours ?? []
  const { state, followed } = watch
  const level = followed?.alerts ?? 'key'
  const eventId = followed?.event_id ?? match.event_ids[0]

  return (
    <>
      <header className="player-band match-band" data-sport={key}>
        <div className="bar">
          {back}
          {/* Following the game itself: none for one that's over, and no promise of alerts its source can't send. */}
          <span className="row" style={{ gap: 8 }}>
            {state?.following && (
              <button type="button" className="btn icon" aria-label={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} aria-expanded={watch.open} onClick={() => watch.setOpen((o) => !o)}>
                <BellIcon width={20} height={20} />
              </button>
            )}
            {state && (state.following || state.alerts) && (
              <button type="button" className="btn" onClick={watch.toggle} disabled={watch.busy} aria-pressed={state.following}>
                {watch.busy && <span className="spinner" />}
                {state.following ? <><CheckIcon width={16} height={16} />Following</> : <><PlusIcon width={16} height={16} />Follow</>}
              </button>
            )}
            {state && !state.following && !state.alerts && match.status !== 'final' && (
              <span className="pill outline" title={state.note ?? undefined}>No live alerts</span>
            )}
          </span>
        </div>
        {watch.open && state?.following && (
          <div className="alert-panel" style={{ color: 'var(--text)' }}>
            <span className="row">
              <span className="title">Alerts for this game</span>
              <Link to="/notifications?tab=settings" className="link small">Settings</Link>
            </span>
            {followed && <AlertLevels label="Alerts for this game" value={level} onChange={(l) => { watch.attempt(() => watch.setMatchAlerts(followed.event_id, l)) }} />}
            <span className="small muted">{gameLevelText(level)}</span>
            {eventId != null && (
              <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} aria-pressed={watch.muted.has(eventId)} onClick={() => { watch.attempt(() => watch.setMuted(eventId, !watch.muted.has(eventId))) }}>
                {watch.muted.has(eventId) ? 'Unmute this game' : 'Mute this game'}
              </button>
            )}
          </div>
        )}
        <div className="stack" style={{ gap: 10 }}>
          <span className="row" style={{ justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
            <Status match={match} />
            <span className="tag soft">{[match.competition, match.detail].filter(Boolean).join(' · ') || sportName(match.sport)}</span>
          </span>
          {/* The board names the sides; without one yet, the title does. */}
          <h1 className={board ? 'sr-only' : 'dsp match-title'}>{match.title}</h1>
          {soon && <span className="dsp match-count">{soon}</span>}
        </div>
        {board && (
          <div className="match-board">
            {/* A state in a shape this app doesn't know shows as the plain score. */}
            <ErrorBoundary fallback={<PlainScore live={board} />} resetKey={board}>
              <GameBoard live={board} />
            </ErrorBoundary>
          </div>
        )}
        {/* Over: who won, big, as its sport says it. */}
        {match.status === 'final' && match.verdict && <p className="dsp match-verdict">{match.verdict}</p>}
        {board?.as_of && <span className="tiny soft">Updated {relative(board.as_of, now)}{board.source ? ` · ${board.source}` : ''}</span>}
      </header>
      {watch.error && <div className="error-box" role="alert">{watch.error}</div>}
      {state && !state.following && !state.alerts && state.note && match.status !== 'final' && <p className="small muted">{state.note}</p>}
      {failed && <div className="notice" role="status">Couldn't update this game: {failed}</div>}

      {yours.length > 0 && (
        <section className="section" aria-label="Your players">
          <SectionHead title="Your players" />
          <ul className="card list">
            {yours.map((p) => (
              <li key={p.player_id}>
                <Link to={`/player/${p.player_id}`} className="stack" style={{ gap: 6 }}>
                  <span className="row">
                    <span className="title">{p.name}</span>
                    {p.result && <span className="small" style={{ fontWeight: 800 }}>{p.result}</span>}
                  </span>
                  {p.headline && <span className="small" style={{ fontWeight: 800 }}>{p.headline}</span>}
                  {p.stats.length > 0 && <StatGrid stats={p.stats} />}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

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

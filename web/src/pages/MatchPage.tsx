import { useEffect, useState, type CSSProperties } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useActivity } from '../activity'
import { LEVELS } from '../alerts'
import { api, ApiError, type GameTeam, type Match, type MatchFollowState } from '../api'
import { useNow } from '../clock'
import { GameBoard, PlainScore, SectionHead, StatGrid } from '../components/Blocks'
import { Crest } from '../components/Crest'
import { ClipsSection } from '../components/Clips'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { BackIcon, BellIcon, CheckIcon, PlusIcon } from '../components/Icons'
import { FollowPills } from '../components/LiveNow'
import { Picks, usePredictions } from '../components/Picks'
import { InSide } from '../components/Side'
import { useFollowToggle } from '../components/Starters'
import { Watch } from '../components/Watch'
import { dateTime, longestWordEms, relative } from '../format'
import { useFollowing } from '../following'
import { discFor, sidesOf, useLooks, type Look, type Side } from '../look'
import { boardOf, EVERY, followedOf, followHead, keyEvents, nextLook, ownResult, pageResult, startsIn, watchable } from '../match'
import { AlertLevels, gameLevelText } from '../notifications'
import { sportDrawn, sportKey, sportName, viewOf } from '../sports'

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
          const next = nextLook(m.refreshing, tries)
          timer = window.setTimeout(() => look(next.tries), next.wait)
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
  const followed = match ? followedOf(match, matches, state) : undefined
  const attempt = (work: () => Promise<unknown>) => { setError(null); return work().catch((e: Error) => setError(e.message)) }
  const toggle = async () => {
    if (!match || !state) return
    setBusy(true)
    await attempt(async () => {
      await followMatch(match.match, !state.following, state.event_id ?? followed?.event_id)
      setMine({ ...state, following: !state.following })
      if (state.following) setOpen(false)
    })
    setBusy(false)
  }
  return { state, busy, open, setOpen, error, toggle, followed, muted, setMuted, setMatchAlerts, attempt }
}

/**
 * The game's teams to follow (sports_follow/teams.py): each its name, opening its page, and Follow, which
 * follows every game of the team from now on, not this one only. This app's word on following one shows over
 * the page's last look; a tap shows at once.
 */
function GameTeams({ teams }: { teams: GameTeam[] }) {
  const looks = useLooks(teams.map((t) => t.key))
  const following = useFollowing()
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const follows = (t: GameTeam) => (following.loaded ? following.isFollowingTeam(t.team_id) : t.following)
  const toggle = async (t: GameTeam) => {
    setBusy(t.team_id)
    setError(null)
    try { await following.followTeam(t.team_id, !follows(t)) } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  return (
    <section className="section" aria-label="Teams">
      <SectionHead title="Teams" />
      <ul className="card list results">
        {teams.map((t) => {
          const on = follows(t)
          return (
            <li key={t.team_id} className="result">
              <Link to={`/team/${t.team_id}`} className="result-main">
                <Crest look={t.key ? looks[t.key] : null} name={t.name} size={44} disc={discFor(t.key ? looks[t.key] : null, '#ffffff')} />
                <span className="stack">
                  <span className="title">{t.name}</span>
                  <span className="small muted">{on ? 'Every game of theirs, from both sides' : t.can_follow ? 'Follow every game of theirs' : "Its games can't be followed here now"}</span>
                </span>
              </Link>
              {(t.can_follow || on) && (
                <button type="button" className={`btn${on ? '' : ' primary'}`} disabled={busy != null} onClick={() => { void toggle(t) }} aria-pressed={on} aria-label={`${on ? 'Following' : 'Follow'} ${t.name}`}>
                  {busy === t.team_id ? <span className="spinner" /> : on ? <CheckIcon width={16} height={16} /> : <PlusIcon width={16} height={16} />}
                  {on ? 'Following' : 'Follow'}
                </button>
              )}
            </li>
          )
        })}
      </ul>
      {error && <div className="error-box" role="alert">{error}</div>}
    </section>
  )
}

/** A game's two sides, as the team registry has them, in its board's order (home first, or away first where the
 * sport's board draws it so): none for a game of more or fewer (a lobby, a chess board's players), or of a side the
 * registry hasn't. */
function facing(match: Match | null): [GameTeam, GameTeam] | null {
  const teams = match?.teams ?? []
  if (!match || teams.length !== 2 || !teams.every((t) => t.key)) return null
  return viewOf(sportDrawn(match.sport))?.awayFirst ? [teams[1], teams[0]] : [teams[0], teams[1]]
}

/**
 * The two sides face to face at the top of the band (BHA-667): each crest slides in from its side, the V pops and
 * the names rise. On the teams' colours (sides), each crest in a disc where it would vanish on its colour; on the
 * sport's, each logo in a white disc.
 */
function FaceOff({ teams, looks, sides, v }: { teams: [GameTeam, GameTeam]; looks: Record<string, Look | null | undefined>; sides: [Side, Side] | null; v: boolean }) {
  return (
    <div className="faceoff">
      {teams.map((t, n) => (
        <Link key={t.team_id} to={`/team/${t.team_id}`} className={`faceoff-side ${n ? 'r' : 'l'}`}>
          <Crest look={looks[t.key]} name={t.name} size={104} disc={sides ? sides[n].disc : (discFor(looks[t.key], '#ffffff') ?? '#ffffff')} eager />
          <span className="dsp faceoff-name" style={{ '--ems': longestWordEms(t.name) } as CSSProperties}>{t.name}</span>
        </Link>
      ))}
      {v && <span className="faceoff-v" aria-hidden="true"><span>V</span></span>}
    </div>
  )
}

/**
 * One game, whoever follows anyone in it, live, to come or over (sports_follow/match.py): a head in its sport's
 * colour with the competition and its board (the sides and the big score, as its sport draws them), and over
 * a finished game's board who won, big; a game to come has its countdown there instead. Then where to watch it,
 * while it's on or about to be, where its source says (an esports series' streams). Then what its sport
 * shows under the board (a timeline, a scorecard, its maps, a chess game's moves), the fan's players in it with
 * their lines, its teams to follow, what was found for the players followed in it, and both sides' players to
 * follow with a tap.
 * Every game on Today opens it, live, over or to come, and so does a game on a player's Matches tab.
 */
export function MatchPage() {
  const { source = '', game = '' } = useParams()
  const { match, failed } = useMatch(source, game)
  const follow = useFollowToggle()
  const watch = useFollowGame(match)
  const now = useNow()
  const [playing, setPlaying] = useState<string | null>(null)  // the stream a fan has started here (Watch), by its address
  const calls = usePredictions(match?.predictions)  // kept here: a screen narrowed or widened moves the cards, not their picks
  const faced = facing(match)
  const looks = useLooks(faced ? faced.map((t) => t.key) : [])
  useEffect(() => { setPlaying(null) }, [source, game])
  const back = <Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>

  if (!match) {
    return failed
      ? <div className="stack" style={{ gap: 12 }}>{back}<div className="error-box" role="alert">{failed}</div></div>
      : <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>
  }
  const key = sportKey(match.sport)
  const view = viewOf(sportDrawn(match.sport)) // an esports game is drawn as esports
  const live = match.live
  const board = boardOf(match)
  const soon = startsIn(match, now)
  // Its sides' looks: the band in their colours where both have them, else the sport's; until they're known, neither.
  const pending = faced != null && faced.some((t) => looks[t.key] === undefined)
  // Home and away as the server sends them, for the clash rule (the away side changes), then in the board's order.
  const flipped = faced != null && faced[0] !== match.teams?.[0]
  const paired = faced && !pending ? sidesOf(looks[(flipped ? faced[1] : faced[0]).key], looks[(flipped ? faced[0] : faced[1]).key]) : null
  const sides: [Side, Side] | null = paired && flipped ? [paired[1], paired[0]] : paired
  const colours = sides
    ? ({ '--l': sides[0].fill, '--la': sides[0].accent, '--ra': sides[1].accent, '--r': sides[1].fill, '--l-ink': sides[0].ink, '--r-ink': sides[1].ink } as CSSProperties)
    : undefined
  const events = keyEvents(match)
  // Where to watch it: while it's on or about to be, and while a fan's player is on though it's been called over.
  const streams = watchable(match, now)
  const watching = streams.length > 0 ? streams : playing ? match.streams ?? [] : []
  const yours = match.yours ?? []
  const { state, followed } = watch
  const head = followHead(match, state)
  const level = followed?.alerts ?? 'key'
  const eventId = followed?.event_id ?? match.event_ids[0]

  return (
    <>
      <header className={`player-band match-band${sides ? ' duo' : ''}`} data-sport={key} style={colours}>
        <div className="bar">
          {back}
          {/* Following the game itself: none for one that's over, and no promise of alerts its source can't send. */}
          <span className="row" style={{ gap: 8 }}>
            {state?.following && (
              <button type="button" className="btn icon" aria-label={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} aria-expanded={watch.open} onClick={() => watch.setOpen((o) => !o)}>
                <BellIcon width={20} height={20} />
              </button>
            )}
            {state && head.button && (
              <button type="button" className="btn" onClick={watch.toggle} disabled={watch.busy} aria-pressed={state.following}>
                {watch.busy && <span className="spinner" />}
                {state.following ? <><CheckIcon width={16} height={16} />Following</> : <><PlusIcon width={16} height={16} />Follow</>}
              </button>
            )}
            {head.noAlerts && (
              <span className="pill outline" title={state?.note ?? undefined}>No live alerts</span>
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
            <span className="small muted">{gameLevelText(level, match.sport)}</span>
            {eventId != null && (
              <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} aria-pressed={watch.muted.has(eventId)} onClick={() => { watch.attempt(() => watch.setMuted(eventId, !watch.muted.has(eventId))) }}>
                {watch.muted.has(eventId) ? 'Unmute this game' : 'Mute this game'}
              </button>
            )}
          </div>
        )}
        {sides ? (
          <>
            {/* On the teams' colours, what's the game's sits in a pill of the sport's colour; its time in an ink one. */}
            <span className="match-meta">
              {match.status === 'live' && <span className="badge-live">Live</span>}
              {[match.status === 'final' ? 'Result' : match.status === 'postponed' ? 'Off' : '', match.competition, match.detail].filter(Boolean).join(' · ') || sportName(match.sport)}
            </span>
            <h1 className="sr-only">{match.title}</h1>
            {faced && <FaceOff teams={faced} looks={looks} sides={sides} v={!board} />}
            {!board && (match.status === 'scheduled' || match.status === 'armed') && (soon || match.start_utc) && <span className="dsp match-time">{soon ?? dateTime(match.start_utc ?? '')}</span>}
          </>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            <span className="row" style={{ justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
              <Status match={match} />
              <span className="tag soft">{[match.competition, match.detail].filter(Boolean).join(' · ') || sportName(match.sport)}</span>
            </span>
            {/* The board names the sides; without one yet, the faces or the title do. */}
            <h1 className={board || faced ? 'sr-only' : 'dsp match-title'}>{match.title}</h1>
            {faced && !pending && <FaceOff teams={faced} looks={looks} sides={null} v={!board} />}
            {faced && pending && <div className="faceoff" aria-hidden="true" style={{ minHeight: 168 }} />}
            {soon && <span className="dsp match-count">{soon}</span>}
          </div>
        )}
        {board && (
          <div className="match-board">
            {/* A state in a shape this app doesn't know shows as the plain score. The board's live block names no
                player of the fan's: their lines in the game do (a chess board from their side). The fan may explore
                it, where its sport has a way to (a chess board's Play from here). */}
            <ErrorBoundary fallback={<PlainScore live={board} />} resetKey={board}>
              <GameBoard live={board} lines={yours.map((p) => p.stats)} explore page={match.page} />
            </ErrorBoundary>
          </div>
        )}
        {/* Over: who won, big, as its sport says it, unless the board's line under the score says it already. */}
        {pageResult(match) && <p className="dsp match-verdict">{pageResult(match)}</p>}
        {board?.as_of && <span className="tiny soft">Updated {relative(board.as_of, now)}{board.source ? ` · ${board.source}` : ''}</span>}
      </header>
      {watch.error && <div className="error-box" role="alert">{watch.error}</div>}
      {head.note && <p className="small muted" role="status">{head.note}</p>}
      {failed && <div className="notice" role="status">Couldn't update this game: {failed}</div>}

      {/* Where to watch it, while it's on or about to be (sports_follow/watch.py). */}
      {watching.length > 0 && (
        <ErrorBoundary fallback={<BlockError />} resetKey={match.streams}>
          <Watch streams={watching} title={match.title ?? ''} sport={key} playing={playing} onPlay={setPlaying} />
        </ErrorBoundary>
      )}

      {/* What fans call before it starts (sports_follow/predictions.py): none for a sport that asks nothing. On a
          wide screen, in the side column beside the game. */}
      <InSide>
        <ErrorBoundary fallback={<BlockError />} resetKey={match.predictions}>
          <Picks {...calls} />
        </ErrorBoundary>
      </InSide>

      {(match.teams ?? []).length > 0 && (
        <ErrorBoundary fallback={<BlockError />} resetKey={match.teams}>
          <GameTeams teams={match.teams ?? []} />
        </ErrorBoundary>
      )}

      {yours.length > 0 && (
        <section className="section" aria-label="Your players">
          <SectionHead title="Your players" />
          <ul className="card list">
            {yours.map((p) => (
              <li key={p.player_id}>
                <Link to={`/player/${p.player_id}`} className="stack" style={{ gap: 6 }}>
                  <span className="row">
                    <span className="title">{p.name}</span>
                    {ownResult(p.result, match) && <span className="small" style={{ fontWeight: 800 }}>{ownResult(p.result, match)}</span>}
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
          {/* In the sport's colours, as the board is (its marks and the fan's players). */}
          <div data-sport={key} style={{ display: 'contents' }}>
            <view.Match page={match.page as never} live={live} yours={yours.map((p) => p.name)} />
          </div>
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

      {/* The game feed: clips of it, published from the Mac app. */}
      <ClipsSection of={{ match: `${source}/${game}` }} showGame={false} />

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

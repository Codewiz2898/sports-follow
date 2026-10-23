import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type League, type LeagueGame, type MyPredictions } from '../api'
import { dayLabel, endedOn, shortWhen } from '../format'
import { wordSize } from '../lines'
import { callNote, callWords } from '../predictions'
import { sharedLook, useShared } from '../shared'
import { leagueAbout, leagueComing } from '../today'
import { Backdrop, sportKey } from '../sports'
import { CallTag } from './Calls'
import { CrestPair } from './Crest'

const SOON = 5_000  // when the server says a read is under way, the next look
const EVERY = 15 * 60_000  // otherwise, while the page is in view: results come in through the day

/**
 * The big results and big games coming up in the competitions the sources cover, whoever follows them (GET
 * /api/league, league.py): the same for every fan. Looked at once, again in a moment while the server says
 * a source is being read, and every EVERY while the page is in view, one look for every part of the page
 * showing it (shared.ts). Null until the first answer.
 *
 * `theirs` says which events the fan's players are in (theirEvents, as a key), once their games have
 * loaded: when it changes (a player just followed, from Today), it looks again past any kept copy, so a
 * league game that's now theirs, whose event was only just made, shows once, as theirs.
 */
const LEAGUE = sharedLook((fresh) => api.league(fresh), EVERY, SOON, 'stale')

export function useLeague(theirs: string | null = null): League | null {
  const league = useShared(LEAGUE).value
  const known = useRef<string | null>(null)
  useEffect(() => {
    if (theirs == null) return
    if (known.current != null && known.current !== theirs) LEAGUE.again(true)
    known.current = theirs
  }, [theirs])
  return league
}

/** A big game's card: a link to its match page where the server gave it one (match), else the card alone. */
function LeagueCard({ game, className, label, children }: { game: LeagueGame; className: string; label: string; children: ReactNode }) {
  const key = sportKey(game.sport)
  return game.match
    ? <Link to={`/match/${game.match}`} className={className} data-sport={key} aria-label={label}>{children}</Link>
    : <article className={className} data-sport={key} aria-label={label}>{children}</article>
}

/**
 * A finished game on Today none of the fan's players are in: outlined, as Also live's games are, so it doesn't
 * pass for theirs. Its competition, when, its sport's big word for it (the score, the margin, a lobby's
 * winner), who played, and what made it big. It opens the game's match page: what happened in it.
 */
export function LeagueResultCard({ game, now, calls = null }: { game: LeagueGame; now: number; calls?: MyPredictions | null }) {
  const key = sportKey(game.sport)
  const word = game.word || game.title
  const call = callNote(calls, game.match, now)
  // The label is read in place of what's on the card: the fan's call goes in it too.
  return (
    <LeagueCard game={game} className="league-card league-result" label={[`${game.competition}: ${game.line || game.title}, ${word}`, callWords(call)].filter(Boolean).join('. ')}>
      <Backdrop sport={key} seed={game.key} />
      <span className="card-top"><span className="sport-tag">{game.competition}</span><CrestPair keys={game.team_keys} /></span>
      <span className="dsp word" style={{ '--word': `${wordSize(word)}px` } as CSSProperties}>{word}</span>
      <span className="stack" style={{ gap: 2 }}>
        {word !== game.title && <span className="line strong">{game.line || game.title}</span>}
        <span className="line soft one">{leagueAbout(game, dayLabel(endedOn(game), new Date(now)))}</span>
        <CallTag note={call} />
      </span>
    </LeagueCard>
  )
}

/** A big game to come on Today, outlined: the countdown within a day, else the day, big; then the game. It
 * opens the game's match page: its line-ups as they come, and both sides' players to follow. */
export function LeagueComingCard({ game, now, calls = null }: { game: LeagueGame; now: number; calls?: MyPredictions | null }) {
  const key = sportKey(game.sport)
  const { big, when } = leagueComing(game, now)
  const call = callNote(calls, game.match, now)
  return (
    <LeagueCard game={game} className="league-card league-coming" label={[`${game.competition}: ${game.title}, ${shortWhen(game.start_utc, now)}`, callWords(call)].filter(Boolean).join('. ')}>
      <Backdrop sport={key} seed={game.key} />
      <span className="card-top"><span className="sport-tag">{game.competition}</span><CrestPair keys={game.team_keys} /></span>
      <span className="dsp count">{big}</span>
      <span className="stack" style={{ gap: 2 }}>
        <span className="line strong">{game.title}</span>
        <span className="line soft">{leagueAbout(game, when)}</span>
        <CallTag note={call} />
      </span>
    </LeagueCard>
  )
}

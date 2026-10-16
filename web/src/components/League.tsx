import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { api, type League, type LeagueGame } from '../api'
import { dayLabel, shortWhen } from '../format'
import { wordSize } from '../lines'
import { leagueAbout, leagueComing } from '../today'
import { Backdrop, sportKey } from '../sports'

const SOON = 5_000  // when the server says a read is under way, the next look
const EVERY = 15 * 60_000  // otherwise, while the page is in view: results come in through the day

/**
 * The big results and big games coming up in the competitions the sources cover, whoever follows them (GET
 * /api/league, league.py): the same for every fan. Looked at once, again in a moment while the server says
 * a source is being read, and every EVERY while the page is in view. Null until the first answer.
 *
 * `theirs` says which events the fan's players are in (theirEvents, as a key), once their games have
 * loaded: when it changes (a player just followed, from Today), it looks again past any kept copy, so a
 * league game that's now theirs, whose event was only just made, shows once, as theirs.
 */
export function useLeague(theirs: string | null = null): League | null {
  const [league, setLeague] = useState<League | null>(null)
  const again = useRef<((tries: number, fresh?: boolean) => void) | null>(null)
  useEffect(() => {
    let current = true
    let timer: number | undefined
    let latest = 0  // the newest look: an older one's answer starts no second loop
    let at = 0
    const look = (tries: number, fresh = false) => {
      window.clearTimeout(timer)
      if (document.hidden) return  // nobody's looking; showing the tab again looks, if it's been a while
      const mine = ++latest
      at = Date.now()
      api.league(fresh)
        .then((r) => {
          if (!current || mine !== latest) return
          setLeague(r)
          timer = window.setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => { if (current && mine === latest) timer = window.setTimeout(() => look(tries), EVERY) })
    }
    const shown = () => { if (!document.hidden && Date.now() - at > EVERY) look(0) }
    document.addEventListener('visibilitychange', shown)
    again.current = look
    look(0)
    return () => { current = false; again.current = null; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [])
  const known = useRef<string | null>(null)
  useEffect(() => {
    if (theirs == null) return
    if (known.current != null && known.current !== theirs) again.current?.(0, true)
    known.current = theirs
  }, [theirs])
  return league
}

/**
 * A finished game on Today none of the fan's players are in: outlined, as Also live's games are, so it doesn't
 * pass for theirs. Its competition, when, its sport's big word for it (the score, the margin, a lobby's
 * winner), who played, and what made it big. Not a link: nobody here need follow anyone in it.
 */
export function LeagueResultCard({ game, now }: { game: LeagueGame; now: number }) {
  const key = sportKey(game.sport)
  const word = game.word || game.title
  return (
    <article className="league-card league-result" data-sport={key} aria-label={`${game.competition}: ${game.line || game.title}, ${word}`}>
      <Backdrop sport={key} seed={game.key} />
      <span className="head"><span className="sport-tag">{game.competition}</span><span className="eyebrow">{dayLabel(game.start_utc, new Date(now))}</span></span>
      <span className="dsp word" style={{ '--word': `${wordSize(word)}px` } as CSSProperties}>{word}</span>
      <span className="stack" style={{ gap: 2 }}>
        {word !== game.title && <span className="line strong">{game.line || game.title}</span>}
        <span className="line soft">{leagueAbout(game)}</span>
      </span>
    </article>
  )
}

/** A big game to come on Today, outlined: the countdown within a day, else the day, big; then the game. */
export function LeagueComingCard({ game, now }: { game: LeagueGame; now: number }) {
  const key = sportKey(game.sport)
  const { big, when } = leagueComing(game, now)
  return (
    <article className="league-card league-coming" data-sport={key} aria-label={`${game.competition}: ${game.title}, ${shortWhen(game.start_utc, now)}`}>
      <Backdrop sport={key} seed={game.key} />
      <span className="sport-tag">{game.competition}</span>
      <span className="dsp count">{big}</span>
      <span className="stack" style={{ gap: 2 }}>
        <span className="line strong">{game.title}</span>
        <span className="line soft">{leagueAbout(game, when)}</span>
      </span>
    </article>
  )
}

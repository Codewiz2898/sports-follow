import { useEffect, useState, type CSSProperties } from 'react'
import { api, type Card, type League, type LeagueGame } from '../api'
import { ahead, countdown, dateOnly, dayLabel, parseDate, shortWhen } from '../format'
import { Backdrop, sportKey } from '../sports'
import { wordSize } from './Blocks'

const SOON = 5_000  // when the server says a read is under way, the next look
const EVERY = 15 * 60_000  // otherwise, while the page is in view: results come in through the day
export const TAIL = 6  // league games in a rail after the fan's own, before its "All N" card

/**
 * The big results and big games coming up in the competitions the sources cover, whoever follows them (GET
 * /api/league, league.py): the same for every fan. Looked at once, again in a moment while the server says
 * a source is being read, and every EVERY while the page is in view. Null until the first answer.
 */
export function useLeague(): League | null {
  const [league, setLeague] = useState<League | null>(null)
  useEffect(() => {
    let current = true
    let timer: number | undefined
    let latest = 0  // the newest look: an older one's answer starts no second loop
    let at = 0
    const look = (tries: number) => {
      window.clearTimeout(timer)
      if (document.hidden) return  // nobody's looking; showing the tab again looks, if it's been a while
      const mine = ++latest
      at = Date.now()
      api.league()
        .then((r) => {
          if (!current || mine !== latest) return
          setLeague(r)
          timer = window.setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => { if (current && mine === latest) timer = window.setTimeout(() => look(tries), EVERY) })
    }
    const shown = () => { if (!document.hidden && Date.now() - at > EVERY) look(0) }
    document.addEventListener('visibilitychange', shown)
    look(0)
    return () => { current = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [])
  return league
}

/** The events the fan's players are in: a league game that's one of them is theirs, and shows once, as theirs. */
export function theirEvents(cards: Card[]): Set<number> {
  const ids = cards.flatMap((c) => [
    ...(c.recent_results ?? []).map((r) => r.event_id),
    ...(c.upcoming ?? []).map((u) => u.event_id),
    c.live.event_id,
  ])
  return new Set(ids.filter((id): id is number => id != null))
}

/** The league games none of the fan's players are in (by event, never by name). */
export function notTheirs(games: LeagueGame[], events: Set<number>): LeagueGame[] {
  return games.filter((g) => !g.event_ids.some((id) => events.has(id)))
}

/** The big games still to come: one that has started since the server's answer is live, or over. */
export function stillToCome(games: LeagueGame[], now: number): LeagueGame[] {
  return games.filter((g) => ahead(g.start_utc, now))
}

/** "Since yesterday", or the day the latest results are from ("Tue 13 Oct"). */
export function resultsDay(day: string | null): string {
  return day ? parseDate(day)!.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : 'Since yesterday'
}

const letters = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '')

/** Under the game: its stage and what makes it big, once ("Final" when the stage already says "Final"). */
function about(game: LeagueGame, ...first: string[]): string {
  const detail = game.detail ?? ''
  const why = game.why && !letters(detail).includes(letters(game.why)) ? game.why : ''
  return [...first, why, detail].filter(Boolean).join(' · ')
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
      <span className="dsp word" style={{ '--word': wordSize(word) } as CSSProperties}>{word}</span>
      <span className="stack" style={{ gap: 2 }}>
        {word !== game.title && <span className="line strong">{game.line || game.title}</span>}
        <span className="line soft">{about(game)}</span>
      </span>
    </article>
  )
}

/** A big game to come on Today, outlined: the countdown within a day, else the day, big; then the game. */
export function LeagueComingCard({ game, now }: { game: LeagueGame; now: number }) {
  const key = sportKey(game.sport)
  const start = parseDate(game.start_utc)
  const left = start && start.getTime() - now < 864e5 ? countdown(game.start_utc, now) : ''
  const big = left || (start && start.getTime() - now < 6 * 864e5 ? start.toLocaleDateString(undefined, { weekday: 'short' }) : start?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })) || 'TBC'
  const when = left ? shortWhen(game.start_utc, now) : start && !dateOnly(game.start_utc) ? start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''
  return (
    <article className="league-card league-coming" data-sport={key} aria-label={`${game.competition}: ${game.title}, ${shortWhen(game.start_utc, now)}`}>
      <Backdrop sport={key} seed={game.key} />
      <span className="sport-tag">{game.competition}</span>
      <span className="dsp count">{big}</span>
      <span className="stack" style={{ gap: 2 }}>
        <span className="line strong">{game.title}</span>
        <span className="line soft">{about(game, when)}</span>
      </span>
    </article>
  )
}

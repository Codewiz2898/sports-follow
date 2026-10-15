import { Link } from 'react-router-dom'
import type { Card, LiveStatus } from '../api'
import { ahead, dateOnly, parseDate, shortName } from '../format'
import { tileOrder } from '../following'
import { sportKey, viewOfKind } from '../sports'
import { ChevronIcon } from './Icons'
import { Rail } from './Rail'

/** A live player's game in a few characters, as their sport says it ("1–0 · 67'"), else their own line
 * ("1 goal"); nothing when neither is known (the dot says they're live). */
function liveShort(live: LiveStatus): string {
  const view = viewOfKind(live.state?.kind)
  const state = live.state as never
  const short = live.state && (view?.draws?.(state) ?? true) ? view?.short?.(state, live) : null
  return short || live.headline || ''
}

/** When a player's next game is, in a pill: the time if it's today ("21:00"), the day within the week
 * ("Sat"), else the date ("12 Nov"). Nothing when no game is known. */
function nextWhen(card: Card, now: number): string {
  const starts = (card.upcoming ?? []).map((u) => u.start_utc).filter((s): s is string => ahead(s, now))
  if (!starts.length) return ''
  const at = (s: string) => parseDate(s)!.getTime()
  const next = starts.reduce((a, b) => (at(b) < at(a) ? b : a))
  const d = parseDate(next)!
  if (d.toDateString() === new Date(now).toDateString()) return dateOnly(next) ? 'Today' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (d.getTime() - now < 6 * 864e5) return d.toLocaleDateString(undefined, { weekday: 'short' })
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** One followed player in Today's strip: a pill of their sport's colour, their surname, and a pulsing dot
 * with the score while they play, else when they next do; a spinner while their page is being built. */
function PlayerPill({ card, now }: { card: Card; now: number }) {
  const building = card.status === 'building'
  const live = !building && card.live.is_live
  const said = building ? '' : live ? liveShort(card.live) : nextWhen(card, now)
  return (
    <Link to={`/player/${card.player_id}`} className="strip-pill" data-sport={sportKey(card.player.sport)}>
      {live && <span className="live-dot" aria-hidden="true" />}
      <span className="dsp">{shortName(card.player.name)}</span>
      {live && <span className="sr-only">, live</span>}
      {said && <span className="when">{said}</span>}
      {building && <><span className="spinner" aria-hidden="true" /><span className="sr-only">, page being built</span></>}
    </Link>
  )
}

/**
 * The fan's players along the top of Today on a phone, one row of pills in the order the Following page
 * has them, then "+ Follow". Its heading, all of it, opens the Following page. A desktop has them in the
 * sidebar instead (styles.css hides it there).
 */
export function PlayersStrip({ cards, now }: { cards: Card[]; now: number }) {
  const head = (
    <Link to="/following" className="strip-head">
      <span>Your players · {cards.length}<span className="sr-only">, see them all</span></span>
      <ChevronIcon width={18} height={18} strokeWidth={2.6} />
    </Link>
  )
  return (
    <Rail title="Your players" head={head} className="strip mobile-only">
      {tileOrder(cards, now).map((c) => <PlayerPill key={c.player_id} card={c} now={now} />)}
      <Link to="/search" className="strip-pill add" aria-label="Follow another player"><span className="dsp">+ Follow</span></Link>
    </Rail>
  )
}

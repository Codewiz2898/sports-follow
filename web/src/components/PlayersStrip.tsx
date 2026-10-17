import { Link } from 'react-router-dom'
import type { Card } from '../api'
import { shortName } from '../format'
import { sportKey } from '../sports'
import { liveShort, nextWhen, tileOrder } from '../today'
import { ChevronIcon } from './Icons'
import { Rail } from './Rail'

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

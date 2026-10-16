// What a fan shares, for the web app and the Android app alike: a page's link on the site, tagged as shared,
// with a title, and the button's label. The server gives the link its preview in WhatsApp, X and the rest
// (sports_follow/share.py): the player, the moment and the score, as a picture in the sport's colour. So a
// link and a title are all that's shared. Free of the browser: the Android app shares from it (mobile/).

import { shortName } from './format'
import type { WeekPoster } from './types'

/** A page to share: its path on the site, the title it's shared with, and what its button says. */
export interface Shared { path: string; title: string; label: string }

/**
 * The link a fan shares: `path` on the site at `site`, tagged as shared (s=share), so the app can count shares
 * once it counts visits. `path` may carry its own query: a game's is /player/<id>?g=<event id>.
 */
export function shareLink(site: string, path: string): string {
  return `${site.replace(/\/$/, '')}${path}${path.includes('?') ? '&' : '?'}s=share`
}

/** A player's page, shared as "<name> on <app>". */
export function sharePlayer(id: number, name: string, app: string): Shared {
  return { path: `/player/${id}`, title: `${name} on ${app}`, label: `Share ${name}` }
}

/** A This week poster: its game (/player/<id>?g=<event id>), whose link previews this moment; the player's page when it has no game. */
export function sharePoster(poster: WeekPoster): Shared {
  const game = poster.event_id != null ? `?g=${poster.event_id}` : ''
  return {
    path: `/player/${poster.player_id}${game}`,
    title: `${poster.word}: ${poster.player}`,
    label: `Share ${shortName(poster.player)}'s ${poster.word.toLowerCase()}`,
  }
}

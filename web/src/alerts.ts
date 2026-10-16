// How much a fan hears about a player: the four levels, what each says for a player's sport, and whether a
// moment reaches them. Shared by the web app (notifications.tsx) and the Android app (mobile/), so it touches
// nothing of a browser's.

import type { AlertLevel, PlayerMoment, SportInfo } from './types'

export const LEVELS: { value: AlertLevel; label: string; short: string }[] = [
  { value: 'key', label: 'Key moments', short: 'Key' },
  { value: 'everything', label: 'Everything', short: 'All' },
  { value: 'results', label: 'Results only', short: 'Results' },
  { value: 'off', label: 'Off', short: 'Off' },
]

/** A level in a line (`brief`) and in full (`hint`), for a player of this sport: what key moments are, and
 * what everything adds, as the sport says (its alerts, from GET /api/sports; sports_follow/sports/). */
export function levelText(level: AlertLevel, sport: SportInfo): { brief: string; hint: string } {
  const { key, more } = sport.alerts
  if (level === 'key') return { brief: key[0], hint: key[1] }
  if (level === 'everything') return { brief: `Plus ${more[0]}, without sound`, hint: `Key moments, plus ${more[1]}. Those arrive without sound` }
  if (level === 'results') return { brief: 'Just the final result', hint: 'Just the final result' }
  return { brief: 'Nothing for this player', hint: 'No notifications for this player' }
}

/** Whether a fan who chose `level` for a player hears about a moment (moments.wanted on the server). */
export function wanted(level: AlertLevel, moment: Pick<PlayerMoment, 'level' | 'kind'>): boolean {
  if (level === 'everything') return true
  if (level === 'results') return moment.kind === 'final'
  if (level === 'off') return false
  return moment.level === 'key'
}

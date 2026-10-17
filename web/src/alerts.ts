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

/** A level for a game followed by itself (sports_follow/matches.py), in full, as the game's sport says what a
 * whole game's key moments are and what everything adds (its game_alerts). */
export function gameLevelText(level: AlertLevel, sport: SportInfo): string {
  const { key, more } = sport.game_alerts ?? { key: ['', 'Its key moments and the result'], more: ['', ''] }
  if (level === 'key') return `${key[1]}, from both sides, until it ends`
  if (level === 'everything') return more[1] ? `Key moments, plus ${more[1]}. Those arrive without sound` : `${key[1]}: every moment it tells is a key one`
  if (level === 'results') return 'Just the final result'
  return 'No notifications for this game'
}

/** A level for a team the fan follows (sports_follow/teams.py), in full: each of its games' key moments from both
 * sides, as a game followed by itself has them (its sport's game_alerts), and the result from the team's side. */
export function teamLevelText(level: AlertLevel, sport: SportInfo): string {
  const { key, more } = sport.game_alerts ?? { key: ['', 'Its key moments and the result'], more: ['', ''] }
  if (level === 'key') return `${key[1]}, from both sides, in each of its games, and the result from its side`
  if (level === 'everything') return more[1] ? `Key moments, plus ${more[1]}. Those arrive without sound` : `${key[1]}, in each of its games: every moment it tells is a key one`
  if (level === 'results') return 'Just each result, from its side'
  return 'No notifications for this team'
}

/** Whether a fan who chose `level` for a player hears about a moment (moments.wanted on the server). */
export function wanted(level: AlertLevel, moment: Pick<PlayerMoment, 'level' | 'kind'>): boolean {
  if (level === 'everything') return true
  if (level === 'results') return moment.kind === 'final'
  if (level === 'off') return false
  return moment.level === 'key'
}

/** What Alerts' Settings says about notifications on this device: the web's (notifications.tsx,
 * NotificationsPage.tsx) and the Android app's (mobile/src/push.ts, app/notifications.tsx). */
export const DEVICE = {
  on: 'One alert per game, updated as it goes. Only the big moments make a sound.',
  off: 'Turn on to hear about your players even with the app closed.',
  quiet: 'Alerts still arrive, without sound.',
  noServer: "Notifications aren't set up on this server yet.",
}

/** What saving a device's quiet hours says. */
export function quietSaid(start: string | null, end: string | null): string {
  return start && end ? `Quiet from ${start} to ${end}: notifications arrive without sound.` : 'Quiet hours off.'
}

/** What a test alert says, by how many of the fan's devices took it. */
export function testSaid(sent: number): string {
  return sent ? 'Sent. It should arrive in a few seconds.' : 'No device took it. Turn notifications off and on again here.'
}

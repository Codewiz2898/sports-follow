// What an open app takes from the server when it catches up (its stream connecting again, coming back to the app):
// who the device is signed in as, and the fan's players. Shared by the web app and the Android app (mobile/): no
// browser here, so tests/test_web_resync.py runs it in Node.

import type { Card, Me } from './types'

/** Who an answer of GET /api/me/account says the device is: an account's address, or null for no account. */
export function signedInAs(me: Me): string | null {
  return me.signed_in ? me.email : null
}

/**
 * Whether the device is someone else now than the app has been showing (BUG-162): signed out elsewhere (everywhere
 * from another device, the account deleted or blocked) or signed in, or in to another account, in another tab. Then
 * every list and cached answer it shows was that other fan's, and it starts over.
 */
export function changedHands(shown: Me | null, now: Me): boolean {
  return shown !== null && signedInAs(shown) !== signedInAs(now)
}

/**
 * The fan's players as the server lists them now (BUG-165): who comes and who goes is its word, a player followed or
 * dropped on another device too, and so is each one's level, unless one is being changed here (`changing`): then
 * it stays as shown till the server has it. A card shown at a newer version than the list's (a stream's update that
 * came first) stays as shown.
 */
export function listed(players: Card[], shown: Card[], changing: (playerId: number) => boolean): Card[] {
  const had = new Map(shown.map((c) => [c.player_id, c]))
  return players.map((p) => {
    const c = had.get(p.player_id)
    if (!c) return p
    return { ...(p.version >= c.version ? { ...c, ...p } : c), alerts: changing(p.player_id) ? c.alerts : p.alerts }
  })
}

/**
 * The fan's players with a player's card as a follow answered it (POST /api/follows): in place of the one shown, at
 * the level the fan chose for them, which that answer doesn't carry (BUG-170: Retry on a failed page, which follows
 * again, showed "Key moments" and let toasts through at key); else added.
 */
export function withFollowed(shown: Card[], card: Card): Card[] {
  if (!shown.some((c) => c.player_id === card.player_id)) return [...shown, card]
  return shown.map((c) => (c.player_id === card.player_id ? { ...card, alerts: card.alerts ?? c.alerts } : c))
}

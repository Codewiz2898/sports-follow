// What an open app takes from the server when it catches up (its stream connecting again, coming back to the app):
// who the device is signed in as, and the fan's players; and how a level the fan taps here is saved, which those
// lists leave as shown till it is. Shared by the web app and the Android app (mobile/): no browser here, so
// tests/test_web_resync.py and tests/test_web_following.py run it in Node.

import type { AlertLevel, Card, Me } from './types'

/** Who an answer of GET /api/me/account says the device is: an account's address, or null for no account. */
export function signedInAs(me: Me): string | null {
  return me.signed_in ? me.email : null
}

/**
 * Whether the device is someone else now than the app has been showing (BUG-162): signed out elsewhere (everywhere
 * from another device, the account deleted or blocked) or signed in, or in to another account, in another tab. Then
 * every list and cached answer it shows was that other fan's, and it starts over. Not from unknown (`shown` null):
 * a first answer is who the device is, not a change (BUG-306).
 */
export function changedHands(shown: Me | null, now: Me): boolean {
  return shown !== null && signedInAs(shown) !== signedInAs(now)
}

const ASK_FIRST_MS = 2000
const ASK_MOST_MS = 60_000

/** How long to wait to ask again who the device is, after `failures` asks in a row failed before this one: 2 s,
 * twice as long each time up to a minute, somewhere in the second half of that, so a restart isn't met by every
 * app at once. */
export function askAgainIn(failures: number, random: () => number = Math.random): number {
  const wait = Math.min(ASK_FIRST_MS * 2 ** failures, ASK_MOST_MS)
  return wait / 2 + (random() * wait) / 2
}

export interface WhoAsker {
  /** Ask now, unless an ask is under way (then it's that one); `fresh`: anew, an answer under way dropped (a sign-in
   * or out here: it was asked as who the device was). Settles as the ask does, answered or failed. */
  ask: (fresh?: boolean) => Promise<void>
  /** No more asks, and an answer under way is dropped. */
  stop: () => void
}

/**
 * Asks the server who the device is (`ask`: GET /api/me/account, the Android app's first POST /api/me/start) and hands
 * each answer to `heard`. A failed ask (offline, the API restarting in a deploy) says nothing of who the device is:
 * it stays as the app has shown it, or unknown till a first answer, never signed out, which took the account's
 * answer after it for a change of hands (BUG-306). It's asked again after a while (askAgainIn), or sooner if the app
 * asks first (it woke, its stream connected again).
 */
export function whoAsker(ask: () => Promise<Me>, heard: (me: Me) => void, wait: (failures: number) => number = askAgainIn): WhoAsker {
  let failures = 0
  let round = 0  // the ask whose answer counts; an earlier one's is dropped
  let under: Promise<void> | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  const go = (fresh = false): Promise<void> => {
    if (round < 0) return Promise.resolve()
    if (under && !fresh) return under
    clearTimeout(timer)
    const mine = ++round
    under = ask().then((me) => {
      if (mine !== round) return
      under = null
      failures = 0
      heard(me)
    }, () => {
      if (mine !== round) return
      under = null
      timer = setTimeout(() => { void go() }, wait(failures++))
    })
    return under
  }
  return {
    ask: go,
    stop: () => {
      round = -1
      clearTimeout(timer)
    },
  }
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

/** A level's saves under way for one player, team or game: the newest tap's number, the level last saved, and the
 * saves queued behind each other. */
export interface LevelSaves { latest: number; saved: AlertLevel; queue: Promise<void> }

/**
 * A level the fan tapped for a player, team or game (`key`): it shows at once (`show`), and its saves (`save`: the
 * PUT) go one at a time per key, one a newer tap has replaced not sent, so fast taps can't land out of order: the
 * server ends on the last one (BUG-307: a team's and a game's each went at once, and the server kept whichever
 * came last). If that fails, the level goes back to the last one saved (`shown`: as shown before these taps), and
 * the error is the caller's. A key is in `writes` till its last tap is saved or fails: a list from the server
 * meanwhile leaves its level as shown.
 */
export function saveLevel<K>(writes: Map<K, LevelSaves>, key: K, level: AlertLevel, shown: () => AlertLevel, show: (level: AlertLevel) => void,
  save: (level: AlertLevel) => Promise<unknown>): Promise<void> {
  const saves = writes.get(key) ?? { latest: 0, saved: shown(), queue: Promise.resolve() }
  writes.set(key, saves)
  const n = ++saves.latest
  show(level)
  const done = saves.queue.then(async () => {
    if (n !== saves.latest) return
    try {
      await save(level)
      saves.saved = level
    } catch (e) {
      if (n === saves.latest) show(saves.saved)
      throw e
    } finally {
      if (n === saves.latest) writes.delete(key)
    }
  })
  saves.queue = done.catch(() => {})
  return done
}

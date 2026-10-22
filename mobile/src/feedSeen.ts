// What this phone remembers of the fan's Feed, the web's feedSeen.ts: when they last opened the Feed tab (its
// dot: something came in since, web/src/feed.ts hasNew) and when they last tapped each player's circle (its
// ring, feed.ts ringed). Kept on the phone, as the Alerts tab's last look is (activity.tsx); a fresh phone
// rings what's new.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { useEffect, useSyncExternalStore } from 'react'
import { AppState } from 'react-native'
import { hasNew } from '../../web/src/feed'
import { api } from './api'

const OPENED = 'stann.feed-opened'
const SEEN = 'stann.feed-seen'
const LOOK_AGAIN = 5 * 6e4 // the dot asks again when the app comes back to the front, at most this often

let newest: string | null = null
let opened: string | null = null
let seen: Record<string, string> = {}
let version = 0
let asked = 0
const listeners = new Set<() => void>()

function changed() {
  version += 1
  for (const l of listeners) l()
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

// Read once, as the app starts; a look or a tap before it's in wins over what was kept.
const read = AsyncStorage.multiGet([OPENED, SEEN]).then((rows) => {
  const kept = Object.fromEntries(rows)
  opened ??= kept[OPENED] ?? null
  try {
    seen = { ...(JSON.parse(kept[SEEN] ?? '{}') as Record<string, string>), ...seen }
  } catch { /* a value from elsewhere */ }
  changed()
}).catch(() => {})

/** When the newest item of the fan's Feed came in, as a page of it said. */
export function noteNewest(at: string | null) {
  if (at !== newest) {
    newest = at
    changed()
  }
}

/** The fan opened the Feed tab: its dot goes until something newer comes in. */
export function markOpened() {
  opened = new Date().toISOString()
  AsyncStorage.setItem(OPENED, opened).catch(() => {})
  changed()
}

/** When the fan last tapped a player's circle. */
export function seenAt(playerId: number): string | undefined {
  return seen[playerId]
}

/** The fan tapped a player's circle: its ring goes until something of theirs newer comes in. */
export function markSeen(playerId: number) {
  seen = { ...seen, [playerId]: new Date().toISOString() }
  AsyncStorage.setItem(SEEN, JSON.stringify(seen)).catch(() => {})
  changed()
}

/** The phone is another fan now (account.tsx, which forgets the kept copies): what it showed as the last one goes. */
export function forgetFeedSeen() {
  newest = null
  opened = null
  seen = {}
  asked = 0
  changed()
}

/** Whether the Feed tab has its dot now (newer than the fan's last look). */
export function feedHasNew(): boolean {
  return hasNew(newest, opened)
}

/** Re-renders on any change here (a look, a tap, a newer item). */
export function useFeedSeen(): number {
  return useSyncExternalStore(subscribe, () => version)
}

/** Whether the Feed tab has its dot. The tab bar asks the server once, and again when the app comes back to the
 * front; `who` (the fan's players), changed, asks again at once. */
export function useFeedDot(who: string | null): boolean {
  useFeedSeen()
  useEffect(() => {
    if (who == null) return
    const ask = (force: boolean) => {
      if (!force && Date.now() - asked < LOOK_AGAIN) return
      asked = Date.now()
      read.then(() => api.myFeedPage({ limit: 0 })).then((p) => noteNewest(p.newest)).catch(() => {})
    }
    ask(true)
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') ask(false) })
    return () => sub.remove()
  }, [who])
  return feedHasNew()
}

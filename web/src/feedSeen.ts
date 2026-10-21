// What this device remembers of the fan's Feed: when they last opened the Feed tab (its dot: something came in
// since, feed.ts hasNew) and when they last tapped each player's circle (its ring, feed.ts ringed). Kept on the
// device, as the Alerts tab's last look is (activity.tsx); a fresh device rings what's new.

import { useEffect, useSyncExternalStore } from 'react'
import { api } from './api'
import { hasNew } from './feed'

const OPENED = 'sf-feed-opened'
const SEEN = 'sf-feed-seen'
const LOOK_AGAIN = 5 * 6e4  // the dot asks again when the app comes back to the front, at most this often

function read<T>(key: string, fallback: T): T {
  try {
    const saved = localStorage.getItem(key)
    return saved ? (JSON.parse(saved) as T) : fallback
  } catch {
    return fallback  // storage blocked, or a value from elsewhere
  }
}

function write(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage blocked */ }
}

let newest: string | null = null
let opened: string | null = read<string | null>(OPENED, null)
let seen: Record<string, string> = read<Record<string, string>>(SEEN, {})
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
  write(OPENED, opened)
  changed()
}

/** When the fan last tapped a player's circle. */
export function seenAt(playerId: number): string | undefined {
  return seen[playerId]
}

/** The fan tapped a player's circle: its ring goes until something of theirs newer comes in. */
export function markSeen(playerId: number) {
  seen = { ...seen, [playerId]: new Date().toISOString() }
  write(SEEN, seen)
  changed()
}

/** Re-renders on any change here (a look, a tap, a newer item). */
export function useFeedSeen(): number {
  return useSyncExternalStore(subscribe, () => version)
}

/** Whether the Feed tab has its dot. The app's frame asks the server once, and again when it comes back to the front. */
export function useFeedDot(): boolean {
  useSyncExternalStore(subscribe, () => version)
  useEffect(() => {
    const ask = () => {
      if (document.visibilityState !== 'visible' || Date.now() - asked < LOOK_AGAIN) return
      asked = Date.now()
      api.myFeedPage({ limit: 0 }).then((p) => noteNewest(p.newest)).catch(() => {})
    }
    ask()
    document.addEventListener('visibilitychange', ask)
    return () => document.removeEventListener('visibilitychange', ask)
  }, [])
  return hasNew(newest, opened)
}

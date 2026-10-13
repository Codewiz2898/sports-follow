import { useSyncExternalStore } from 'react'

const MINUTE = 60_000
let ticks = 0
const listeners = new Set<() => void>()
let timer: number | undefined

function tick() {
  ticks++
  for (const listener of listeners) listener()
  window.clearTimeout(timer)
  timer = window.setTimeout(tick, MINUTE - (Date.now() % MINUTE))
}

// Coming back to a phone that slept, the time jumps: catch up at once rather than at the next minute.
const wake = () => { if (document.visibilityState === 'visible') tick() }

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (listeners.size === 1) {
    timer = window.setTimeout(tick, MINUTE - (Date.now() % MINUTE))
    document.addEventListener('visibilitychange', wake)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size) return
    window.clearTimeout(timer)
    document.removeEventListener('visibilitychange', wake)
  }
}

/**
 * The time now, for a render that shows times: every screen using it renders again on the minute,
 * together, so countdowns, "updated 4m ago" and the next game move on while a screen stays open.
 */
export function useNow(): number {
  useSyncExternalStore(subscribe, () => ticks)
  return Date.now()
}

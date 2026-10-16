// The time now, for a render that shows times (the web's clock.ts): every screen using it renders again on
// the minute, together, so countdowns, "updated 4m ago" and the next game move on while a screen stays open.

import { useSyncExternalStore } from 'react'
import { AppState, type NativeEventSubscription } from 'react-native'

const MINUTE = 60_000
let ticks = 0
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | undefined
let wake: NativeEventSubscription | undefined

function tick() {
  ticks++
  for (const listener of listeners) listener()
  clearTimeout(timer)
  timer = setTimeout(tick, MINUTE - (Date.now() % MINUTE))
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (listeners.size === 1) {
    timer = setTimeout(tick, MINUTE - (Date.now() % MINUTE))
    // Coming back to a phone that slept, the time jumps: catch up at once rather than at the next minute.
    wake = AppState.addEventListener('change', (state) => { if (state === 'active') tick() })
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size) return
    clearTimeout(timer)
    wake?.remove()
  }
}

export function useNow(): number {
  useSyncExternalStore(subscribe, () => ticks)
  return Date.now()
}

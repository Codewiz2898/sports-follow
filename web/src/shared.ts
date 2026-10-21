import { useSyncExternalStore } from 'react'

/** What a shared look last got: the server's answer (null until its first), and whether its last try failed. */
export interface Looked<T> { value: T | null; failed: boolean }

export interface SharedLook<T> {
  subscribe: (listener: () => void) => () => void
  get: () => Looked<T>
  /** Look now, past any kept copy when `fresh`. */
  again: (fresh?: boolean) => void
}

/**
 * One look at the server shared by every part of the page that shows it (Today's rails and the side column both
 * read /api/live), so a second reader asks nothing more: a loop that runs while any of them is on screen. It looks
 * as the first one comes; again `soon` while the server says a read is under way (a few times), else every `every`;
 * and not while the tab is hidden. Showing the tab again looks `always`, or only once `every` has passed (`stale`).
 * Its last answer is kept for the next reader, which looks again too.
 */
export function sharedLook<T extends object>(read: (fresh: boolean) => Promise<T>, every: number, soon: number, whenShown: 'always' | 'stale'): SharedLook<T> {
  let shown: Looked<T> = { value: null, failed: false }
  const listeners = new Set<() => void>()
  let timer: number | undefined
  let latest = 0  // the newest look: an older one's answer, or one from before the last reader left, starts no second loop
  let at = 0
  const set = (next: Looked<T>) => {
    shown = next
    listeners.forEach((l) => l())
  }
  const look = (tries: number, fresh = false) => {
    window.clearTimeout(timer)
    if (!listeners.size || document.hidden) return  // nobody's looking; showing the tab again looks
    const mine = ++latest
    at = Date.now()
    read(fresh)
      .then((r) => {
        if (mine !== latest) return
        set({ value: r, failed: false })
        // A read was just asked for (the answer says it's `refreshing`): look again in a moment, a few times, for what it brings.
        const refreshing = 'refreshing' in r && r.refreshing === true
        timer = window.setTimeout(() => look(refreshing ? tries + 1 : 0), refreshing && tries < 3 ? soon : every)
      })
      .catch(() => {
        if (mine !== latest) return
        if (!shown.failed) set({ ...shown, failed: true })
        timer = window.setTimeout(() => look(tries), every)
      })
  }
  const onShow = () => {
    if (!document.hidden && (whenShown === 'always' || Date.now() - at > every)) look(0)
  }
  return {
    subscribe(listener) {
      listeners.add(listener)
      if (listeners.size === 1) {
        document.addEventListener('visibilitychange', onShow)
        look(0)
      }
      return () => {
        listeners.delete(listener)
        if (listeners.size) return
        window.clearTimeout(timer)
        latest++
        document.removeEventListener('visibilitychange', onShow)
      }
    },
    get: () => shown,
    again: (fresh = false) => look(0, fresh),
  }
}

/** A shared look's last answer, kept up to date: reading it keeps its loop going. */
export function useShared<T>(look: SharedLook<T>): Looked<T> {
  return useSyncExternalStore(look.subscribe, look.get)
}

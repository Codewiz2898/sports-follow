import { useSyncExternalStore } from 'react'

const JOINED = 2_000  // readers that come within this of a look (a page's parts, mounting together) share it

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
 * read /api/live), so readers that come together ask once: a loop that runs while any of them is on screen. It
 * looks as a reader comes (a page opened beside a side column that's been reading all along sees what's new, as it
 * did with a loop of its own), unless a look began within JOINED or its answer is still to come; again `soon` while
 * the server says a read is under way (a few times), else every `every`; and not while the tab is hidden. Showing the
 * tab again looks `always`, or (`stale`) only once `every` has passed, or when a look came due while it was hidden.
 * Its last answer is kept, so a page that comes back shows it at once.
 */
export function sharedLook<T extends object>(read: (fresh: boolean) => Promise<T>, every: number, soon: number, whenShown: 'always' | 'stale'): SharedLook<T> {
  let shown: Looked<T> = { value: null, failed: false }
  const listeners = new Set<() => void>()
  let timer: number | undefined
  let latest = 0  // the newest look: an older one's answer, or one from before the last reader left, starts no second loop
  let at = 0
  let reading = false  // the newest look's answer is still to come: a reader that joins waits for it
  let due: { fresh: boolean } | null = null  // a look that came due while the tab was hidden: showing it looks
  const set = (next: Looked<T>) => {
    shown = next
    listeners.forEach((l) => l())
  }
  const look = (tries: number, fresh = false) => {
    window.clearTimeout(timer)
    if (!listeners.size) return  // nobody's reading
    if (document.hidden) {
      due = { fresh: fresh || Boolean(due?.fresh) }  // nobody's looking: showing the tab again looks
      return
    }
    due = null
    const mine = ++latest
    at = Date.now()
    reading = true
    read(fresh)
      .then((r) => {
        if (mine !== latest) return
        reading = false
        set({ value: r, failed: false })
        // A read was just asked for (the answer says it's `refreshing`): look again in a moment, a few times, for what it brings.
        const refreshing = 'refreshing' in r && r.refreshing === true
        timer = window.setTimeout(() => look(refreshing ? tries + 1 : 0), refreshing && tries < 3 ? soon : every)
      })
      .catch(() => {
        if (mine !== latest) return
        reading = false
        if (!shown.failed) set({ ...shown, failed: true })
        timer = window.setTimeout(() => look(tries), every)
      })
  }
  const onShow = () => {
    if (!document.hidden && (due || whenShown === 'always' || Date.now() - at > every)) look(0, due?.fresh)
  }
  return {
    subscribe(listener) {
      listeners.add(listener)
      if (listeners.size === 1) document.addEventListener('visibilitychange', onShow)
      if (listeners.size === 1 || (!reading && Date.now() - at > JOINED)) look(0)
      return () => {
        listeners.delete(listener)
        if (listeners.size) return
        window.clearTimeout(timer)
        latest++
        reading = false
        due = null
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

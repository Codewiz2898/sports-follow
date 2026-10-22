// When an open app asks the server again after hearing there may be news (a moment at the fan's level, a
// notification, coming back to the app): the activity list and This week, on the web (activity.tsx,
// components/ThisWeek.tsx) and on Android (mobile/src/activity.tsx, today/week.tsx). Every app following a player
// hears of the same goal at the same instant, so each waits a while of its own before it asks, and This week is
// asked again only every few minutes (BUG-265). Shared by both apps, so it touches nothing of a browser's.

/** How long an app waits before asking again, in ms. */
export interface Wait {
  after: number // at least: one poll can bring several moments, so it asks once they've all arrived
  spread: number // and up to this much more, at random: the apps that heard together ask apart
  every?: number // and never sooner than this after its last ask
}

/** The activity list: 2 to 12 s after hearing. */
export const ACTIVITY: Wait = { after: 2_000, spread: 10_000 }

/** This week: a minute or two after hearing, as the server keeps a fan's week a minute (sports_follow/week.mine:
 * an ask sooner could be answered from before the moment), and no more than every five minutes. */
export const WEEK: Wait = { after: 60_000, spread: 60_000, every: 5 * 60_000 }

/** How long to wait from `now` before asking again, having last asked at `last` (ms, on the same clock). */
export function waitFor(wait: Wait, now: number, last: number, random: () => number = Math.random): number {
  return Math.max(wait.after, last + (wait.every ?? 0) - now) + Math.floor(random() * wait.spread)
}

export interface Clock {
  now: () => number
  later: (run: () => void, ms: number) => unknown
  cancel: (timer: unknown) => void
}

const CLOCK: Clock = {
  now: () => Date.now(),
  later: (run, ms) => setTimeout(run, ms),
  cancel: (timer) => clearTimeout(timer as ReturnType<typeof setTimeout>),
}

export interface Refetcher {
  heard: () => void // there may be news: ask once, after a wait (waitFor)
  asked: () => void // an ask made otherwise (the list loading), which `every` counts from
  stop: () => void
}

/** Asks (`ask`) once after hearing there may be news, after its wait. Hearing again while it waits changes
 * nothing: that ask comes after whatever was heard since, and putting it off again could put it off for good. */
export function refetcher(wait: Wait, ask: () => void, random: () => number = Math.random, clock: Clock = CLOCK): Refetcher {
  let last = -Infinity
  let timer: unknown
  let waiting = false
  return {
    heard() {
      if (waiting) return
      waiting = true
      timer = clock.later(() => {
        waiting = false
        last = clock.now()
        ask()
      }, waitFor(wait, clock.now(), last, random))
    },
    asked() { last = clock.now() },
    stop() {
      if (waiting) clock.cancel(timer)
      waiting = false
    },
  }
}

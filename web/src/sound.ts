import { useSyncExternalStore } from 'react'

/**
 * Whether the Feed's videos play with their sound (BHA-381), the Android app's sound.ts, as Instagram's and YouTube
 * Shorts' do: off until the fan turns it on (the sound button beside a playing video, YouTube's own, or a tap to play
 * one, or M on a computer: feedKeys.ts), then on for every video after it until they turn it off; kept in this
 * browser for the rest of the day.
 *
 * A browser lets a video start with its sound only once the page has been tapped (its autoplay rules), so one that
 * starts by itself before that plays muted with its sound held back (`held`), and the fan's next tap on the page lets
 * it out (FeedPlayer).
 */

const KEY = 'sf-sound'
const today = () => new Date().toDateString() // the browser's own day

function stored(): boolean {
  try {
    const was = JSON.parse(localStorage.getItem(KEY) ?? 'null') as { on?: unknown; day?: unknown } | null
    return was?.on === true && was.day === today()
  } catch {
    return false // storage blocked, or not ours
  }
}

let on = stored()
let holder: object | null = null // the player whose sound the browser kept back, if any
const listeners = new Set<() => void>()
const told = () => listeners.forEach((heard) => heard())
const listen = (heard: () => void) => { listeners.add(heard); return () => { listeners.delete(heard) } }

/** Turn the sound on or off, for the video playing and every one after it, and keep it for today. */
export function setSound(to: boolean) {
  if (on === to && !holder) return
  on = to
  holder = null
  told()
  try {
    localStorage.setItem(KEY, JSON.stringify({ on: to, day: today() }))
  } catch {
    // storage blocked: it holds till the page reloads
  }
}

/** The browser kept `by`'s sound back (`true`), or let it out; another player's hold is that one's to let go. */
export function holdSound(by: object, to: boolean) {
  if (to ? holder === by : holder !== by) return
  holder = to ? by : null
  told()
}

/** Whether videos play with their sound now, as the sound button shows it: on, and not held back. */
export const soundIsOn = (): boolean => on && holder === null

/** Whether videos play with their sound now: the fan's choice, and the browser letting them. */
export function useSound(): { on: boolean; held: boolean } {
  const wanted = useSyncExternalStore(listen, () => on)
  const back = useSyncExternalStore(listen, () => holder !== null)
  return { on: wanted, held: back }
}

/** Whether the page has been tapped, so a video may start with its sound (true where the browser doesn't say). */
export const mayPlayAloud = (): boolean => (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation?.hasBeenActive ?? true

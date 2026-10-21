import { useSyncExternalStore } from 'react'

/**
 * Whether the Feed's videos start by themselves as they come on screen (BHA-359), the Android app's autoplay.ts:
 * always (the default, Bharath's call), only on Wi-Fi, or never, the fan's choice on the Account page, kept in this
 * browser. Muted when they do until the fan turns the sound on (sound.ts), one at a time (components/FeedLayouts.tsx
 * usePlaying). Never while the browser asks to save data; and "On Wi-Fi" is "not on mobile data or a slow
 * connection", where the browser says which it's on (Chrome does, Safari and Firefox don't: there it's as Always).
 */

export type Autoplay = 'always' | 'wifi' | 'off'
export const AUTOPLAY: [Autoplay, string][] = [['always', 'Always'], ['wifi', 'On Wi-Fi'], ['off', 'Off']]
const KEY = 'sf-autoplay'

function stored(): Autoplay {
  try {
    const was = localStorage.getItem(KEY)
    return was === 'wifi' || was === 'off' ? was : 'always'
  } catch {
    return 'always'  // storage blocked
  }
}

let chosen: Autoplay = stored()
const listeners = new Set<() => void>()
const told = () => listeners.forEach((heard) => heard())

/** Change it, here and on every page showing it, and keep it for the next visit. */
export function setAutoplay(to: Autoplay) {
  chosen = to
  told()
  try {
    localStorage.setItem(KEY, to)
  } catch {
    // storage blocked: it holds till the page reloads
  }
}

function listen(heard: () => void) {
  listeners.add(heard)
  // Changed in another tab.
  const other = (e: StorageEvent) => { if (e.key === KEY) { chosen = stored(); heard() } }
  window.addEventListener('storage', other)
  return () => { listeners.delete(heard); window.removeEventListener('storage', other) }
}

/** The fan's choice. */
export function useAutoplaySetting(): Autoplay {
  return useSyncExternalStore(listen, () => chosen)
}

/** What the browser says of its connection (the Network Information API; Chrome's, not Safari's or Firefox's). */
interface Connection extends EventTarget { saveData?: boolean; type?: string; effectiveType?: string }
const connection = (): Connection | undefined => (navigator as Navigator & { connection?: Connection }).connection

/** The connection now, as a word that changes when it does: 'save' (the fan asked for less data), 'slow' (mobile
 * data, or slower than 4G) or 'fine' (Wi-Fi, a cable, or the browser doesn't say). */
function readConnection(): 'save' | 'slow' | 'fine' {
  const c = connection()
  if (!c) return 'fine'
  if (c.saveData) return 'save'
  if (c.type === 'cellular' || c.type === 'none') return 'slow'
  if (c.effectiveType === 'slow-2g' || c.effectiveType === '2g' || c.effectiveType === '3g') return 'slow'
  return 'fine'
}

function listenConnection(heard: () => void) {
  const c = connection()
  c?.addEventListener('change', heard)
  return () => c?.removeEventListener('change', heard)
}

/** Whether a video may start by itself now: the fan's choice, the browser not saving data, and for "On Wi-Fi" the
 * connection this moment. `wanted`: false where nothing autoplays. */
export function useAutoplay(wanted = true): boolean {
  const setting = useAutoplaySetting()
  const net = useSyncExternalStore(listenConnection, readConnection)
  if (!wanted || setting === 'off' || net === 'save') return false
  return setting === 'always' || net === 'fine'
}

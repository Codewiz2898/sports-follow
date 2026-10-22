/**
 * The Feed's keys on a computer (BHA-660), as YouTube's own on its site: Space pauses the video on screen or plays it
 * on, F takes it full screen and back, M turns its sound off or on (for the videos after it too, as the sound button
 * does). Which video, and doing it, is the player's (components/FeedPlayer.tsx) and the list's when none plays yet
 * (components/FeedLayouts.tsx usePlaying). Once YouTube's player itself has the focus (the fan clicked into it), the
 * keys are its own, which do the same.
 */

export type FeedKey = 'pause' | 'full' | 'sound'

/** What of a key press decides: a KeyboardEvent's. */
export interface Pressed { key: string; code?: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }

/** Where the focus is: in something the fan types into, on a button or link they reached with Tab, or neither. */
export type Focus = 'typing' | 'control' | null

/** What a key does to the Feed's video: nothing with Ctrl, ⌘ or Alt held (the browser's and the system's own), nor
 * while the fan types; Space on a button or link reached with Tab presses that, as it always does. A letter is the
 * key's own on a Latin keyboard, else the one in its place (KeyF) on another (a Hindi one, say). Held down, its
 * repeats are the video's too, and do nothing (the page doesn't scroll a moment after Space pauses). */
export function feedKey(e: Pressed, focus: Focus): FeedKey | null {
  if (e.ctrlKey || e.metaKey || e.altKey || focus === 'typing') return null
  if (e.key === ' ') return focus === 'control' ? null : 'pause'
  const letter = /^[a-z]$/i.test(e.key) ? e.key.toLowerCase() : e.code === 'KeyF' ? 'f' : e.code === 'KeyM' ? 'm' : ''
  return letter === 'f' ? 'full' : letter === 'm' ? 'sound' : null
}

const TEXT = /^(text|search|email|url|tel|password|number|date|datetime-local|month|week|time)$/
const CONTROL = 'button, a[href], summary, input, [role="button"], [role="checkbox"], [role="switch"], [role="tab"]'

// Whether the focus got where it is by Tab, not by a click or a tap (which leaves a button focused in Chrome).
// :focus-visible can't say: Chrome turns it on for a clicked button at the first key pressed after.
let tabbed = false
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e) => { if (e.key === 'Tab') tabbed = true }, true)
  window.addEventListener('pointerdown', () => { tabbed = false }, true)
}

/** Where the page's focus is, for feedKey. */
export function focusOf(el: Element | null): Focus {
  if (!el || el === document.body) return null
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return 'typing'
  if (el instanceof HTMLInputElement && TEXT.test(el.type)) return 'typing'
  if (el instanceof HTMLElement && el.isContentEditable) return 'typing'
  return tabbed && el.matches(CONTROL) ? 'control' : null
}

/** Space is the video's: a button the fan clicked lets go of the focus, so it isn't pressed as the key comes up (as
 * Firefox would) nor by the next Space. */
export function spaceTaken() {
  const el = document.activeElement
  if (el instanceof HTMLElement && el.matches(CONTROL)) el.blur()
}

/** What a key pressed on the page does to the Feed's video now: nothing while a sheet is open over it ("…"). */
export function feedKeyOf(e: KeyboardEvent): FeedKey | null {
  if (e.defaultPrevented || document.querySelector('dialog[open]')) return null
  return feedKey(e, focusOf(document.activeElement))
}

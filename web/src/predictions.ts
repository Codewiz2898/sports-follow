// Calling games (sports_follow/predictions.py): what a question's card says, in the app's words. Pure, so the
// match page, Today's cards, a league's page and the campaign page say the same.

import { APP_NAME } from './app'
import type { MyPredictions, Prediction } from './types'

/** Whether a question still takes picks: open, and its start not come (the server refuses one after that anyway). */
export function isOpen(q: Prediction, now: number): boolean {
  return q.status === 'open' && !(q.locks_at && Date.parse(q.locks_at) <= now)
}

export function labelOf(q: Prediction, key: string | null | undefined): string {
  return q.options.find((o) => o.key === key)?.label ?? ''
}

/** The share of fans with this fan's pick, once the split's shown. */
export function shareOf(q: Prediction, key: string | null | undefined): number | null {
  return q.options.find((o) => o.key === key)?.share ?? null
}

/** The line under a fan's pick: whom they're with ("You and 71% of Slam fans say Gukesh D"), or how few. */
export function withWhom(q: Prediction): string {
  const share = shareOf(q, q.mine)
  const label = labelOf(q, q.mine)
  if (share == null || !label) return ''
  if (q.picks <= 1) return `You're the first ${APP_NAME} fan to call it: ${label}.`
  if (share <= 15) return `Only ${share}% of ${APP_NAME} fans say ${label} with you.`
  return `You and ${share}% of ${APP_NAME} fans say ${label}.`
}

export type Outcome = { kind: 'right' | 'wrong' | 'void' | 'missed'; head: string; line: string }

/** A settled (or voided) question from this fan's side. */
export function outcomeOf(q: Prediction): Outcome | null {
  if (q.status === 'void') return { kind: 'void', head: 'No result', line: 'It ended without an answer: no points either way.' }
  if (q.status !== 'settled') return null
  const answer = labelOf(q, q.answer)
  const share = shareOf(q, q.answer)
  const few = share != null ? `${share}% of fans called it.` : ''
  if (!q.mine) return { kind: 'missed', head: answer, line: few }
  if (q.mine === q.answer) return { kind: 'right', head: 'You got it right', line: `${answer}. ${few}`.trim() }
  return { kind: 'wrong', head: 'Not this time', line: `${answer}. You said ${labelOf(q, q.mine)}.` }
}

/** The biggest answer of the split: "71% say Gukesh D", for a share. */
export function leading(q: Prediction): { label: string; share: number } | null {
  const shown = q.options.filter((o) => o.share != null)
  if (!shown.length || !q.picks) return null
  const top = shown.reduce((a, b) => ((b.share ?? 0) > (a.share ?? 0) ? b : a))
  return { label: top.label, share: top.share ?? 0 }
}

/** A question's own address, whose link preview shows its split (server.py, share.of_question). */
export function questionPath(q: Prediction): string {
  return `/match/${q.match}?q=${q.id}`
}

/** What sharing a question says, before its link. */
export function shareText(q: Prediction): string {
  const top = leading(q)
  const outcome = outcomeOf(q)
  if (outcome?.kind === 'right') return `I called it: ${labelOf(q, q.answer)}. ${q.title} ${q.game}`
  if (top) return `${top.share}% of ${APP_NAME} fans say ${top.label}. ${q.title} ${q.game}. Call it:`
  return `${q.title} ${q.game}. Call it on ${APP_NAME}:`
}

/** What a game's card says of the fan's calls on it: how they went, once one's settled; else that there's one to make;
 * else their pick. */
export type CallNote = { kind: 'call'; left: number } | { kind: 'said'; label: string } | { kind: 'right'; points: number } | { kind: 'wrong'; label: string }

/** A call note in words, as its tag says it: for a card whose label is read in place of what's on it. */
export function callWords(note: CallNote | null): string {
  if (!note) return ''
  if (note.kind === 'call') return 'Call it'
  if (note.kind === 'right') return `You called it · +${note.points}`
  return `You said ${note.label}`
}

/**
 * A game's note from the fan's calls (GET /api/me/predictions), by its address (`match`, after /match/): the
 * points of every right call once its questions settle ("You called it · +38"), or what they said when none was
 * right; before it starts, the questions still open that they haven't called, else their pick. None for a game
 * they've nothing to call on, or whose calls were voided.
 */
export function callNote(calls: MyPredictions | null, match: string | null | undefined, now: number): CallNote | null {
  if (!calls || !match) return null
  const settled = calls.settled.filter((q) => q.match === match && q.status === 'settled' && q.mine)
  const right = settled.filter((q) => q.mine === q.answer)
  if (right.length) return { kind: 'right', points: right.reduce((n, q) => n + (q.points ?? 0), 0) }
  if (settled.length) return { kind: 'wrong', label: labelOf(settled[0], settled[0].mine) }
  const open = calls.open.filter((q) => q.match === match && isOpen(q, now))
  const left = open.filter((q) => !q.mine).length
  if (left) return { kind: 'call', left }
  const picked = open.find((q) => q.mine)
  return picked ? { kind: 'said', label: labelOf(picked, picked.mine) } : null
}

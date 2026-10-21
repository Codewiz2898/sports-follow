import { useEffect, useRef } from 'react'
import { api, type MyPredictions } from '../api'
import type { CallNote } from '../predictions'
import { sharedLook, useShared } from '../shared'
import { CheckIcon } from './Icons'

const EVERY = 5 * 60_000  // questions open a day before a game and close as it starts: a look this often keeps up

/**
 * The fan's calls (GET /api/me/predictions): the questions still open on their games and on any they've picked,
 * and their picks settled lately. One look for every card showing them (shared.ts): Today's games and the side
 * column's. Calling a game (PickCard) looks again, and so does a change in the fan's games (`theirs`, Today's key
 * of their events, as useLeague's).
 */
const CALLS = sharedLook(() => api.myPredictions(), EVERY, EVERY, 'stale')

export function useCalls(theirs: string | null = null): MyPredictions | null {
  const calls = useShared(CALLS).value
  const known = useRef<string | null>(null)
  useEffect(() => {
    if (theirs == null) return
    if (known.current != null && known.current !== theirs) CALLS.again()
    known.current = theirs
  }, [theirs])
  return calls
}

/** A pick was made or taken back: the cards that show the fan's calls look again. */
export function callsChanged() {
  CALLS.again()
}

/**
 * A game card's word on the fan's calls (predictions.callNote): "Call it" while there's a question they haven't
 * called, then their pick; once it's settled, the points of a right call or what they said. Its game's page is
 * where they call it: the card it's on opens it.
 */
export function CallTag({ note }: { note: CallNote | null }) {
  if (!note) return null
  if (note.kind === 'call') return <span className="call-tag" aria-label={note.left > 1 ? `${note.left} questions to call` : undefined}>Call it</span>
  if (note.kind === 'right') return <span className="call-tag right"><CheckIcon width={12} height={12} strokeWidth={3.4} />You called it · +{note.points}</span>
  return <span className="call-tag said">{note.kind === 'said' && <CheckIcon width={12} height={12} strokeWidth={3.4} />}You said {note.label}</span>
}

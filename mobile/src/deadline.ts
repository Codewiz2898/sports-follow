// A request's time limit (api.ts runs every request through it). React Native's fetch never gives up on a
// connection gone quiet (a weak signal, a carrier dropping a pooled connection while the app was away): a match
// page's live score froze with no error, and its next look never came, as the page asks again only once a
// request settles (BUG-155). React Native's AbortSignal has neither timeout() nor any(), so the time limit and the
// caller's own abort (a search typed over) are joined here by hand. No imports: tests/test_mobile_deadline.py
// runs it in Node.

export const REQUEST_MS = 15_000 // a few times the longest answer the API gives (a search waits 2.5 s on its sources)

/** The name of the error `within` throws when the time ran out; the caller's own abort keeps its AbortError. */
export const TIMED_OUT = 'TimeoutError'

/**
 * Runs `work` with a signal that aborts after `ms`, or as soon as the caller's `signal` does; the time limit stops
 * once `work` settles. Out of time, it throws an Error named TIMED_OUT rather than the AbortError the work gave up
 * with, which callers take for their own abort and say nothing about.
 */
export async function within<T>(work: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal | null, ms = REQUEST_MS): Promise<T> {
  const ctrl = new AbortController()
  let late = false
  const timer = setTimeout(() => { late = true; ctrl.abort() }, ms)
  const forward = () => ctrl.abort()
  if (signal?.aborted) ctrl.abort()
  else signal?.addEventListener('abort', forward)
  try {
    return await work(ctrl.signal)
  } catch (e) {
    if (late && !signal?.aborted) throw Object.assign(new Error(`No answer in ${Math.round(ms / 1000)} s`), { name: TIMED_OUT })
    throw e
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', forward)
  }
}

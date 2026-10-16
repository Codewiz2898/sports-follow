import { api } from './api'

const TAGS = ['s', 'utm_source', 'utm_medium', 'utm_campaign']
const NOTED = 'sf-source-noted'
const WAIT = 1500  // the longest the first render waits for it

/**
 * Where this fan came from: the tags on the link they opened (s=share, utm_source=whatsapp…) and its page,
 * told to the server once a visit (it keeps a fan's first, server.visit_source). Told before the app's
 * other calls: a first visit has no fan cookie yet, and the answer to this one sets it, so the rows the
 * app's own calls make are the same fan's. Never holds the first render up for long, and a failure is
 * only a source not counted.
 */
export function noteVisit(): Promise<void> {
  const params = new URLSearchParams(window.location.search)
  const tags = Object.fromEntries(TAGS.flatMap((k) => { const v = params.get(k); return v ? [[k, v.slice(0, 60)]] : [] }))
  if (!Object.keys(tags).length) return Promise.resolve()
  try {
    if (sessionStorage.getItem(NOTED)) return Promise.resolve()
    sessionStorage.setItem(NOTED, '1')
  } catch { /* storage blocked: told each load */ }
  const told = api.visitSource(tags, window.location.pathname).then(() => {}, () => {})
  return Promise.race([told, new Promise<void>((done) => window.setTimeout(done, WAIT))])
}

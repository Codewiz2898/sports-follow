import { api } from './api'
import { linkTags } from './visitTags'

const NOTED = 'sf-source-noted'
const WAIT = 1500  // the longest the first render waits for it

/**
 * Where this fan came from: the tags on the link they opened (s=share, utm_source=whatsapp…: visitTags.ts) and
 * its page, told to the server once a device (it keeps a fan's first, server.visit_source), so a fan back from
 * each day's post doesn't wait on it again; marked told once the server has it, so one that didn't get there
 * (offline) is told on the next tagged visit. The app's page made this device's fan (its cookie), which the
 * server keeps it under. Never holds the first render up for long, and a failure is only a source not counted.
 */
export function noteVisit(): Promise<void> {
  const tags = linkTags(window.location.search)
  if (!Object.keys(tags).length) return Promise.resolve()
  try {
    if (localStorage.getItem(NOTED)) return Promise.resolve()
  } catch { /* storage blocked: told each load */ }
  const told = api.visitSource(tags, window.location.pathname).then(() => {
    try { localStorage.setItem(NOTED, '1') } catch { /* told again next load */ }
  }, () => {})
  return Promise.race([told, new Promise<void>((done) => window.setTimeout(done, WAIT))])
}

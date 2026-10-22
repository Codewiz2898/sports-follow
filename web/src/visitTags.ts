// The tags a link carries that say where a fan came from (s=share, utm_source=whatsapp…), read by the web app from
// the page's own address (visit.ts) and by the Android app from the site's link that opened it (mobile/src/visit.ts),
// and told to the server, which keeps a fan's first (server.visit_source). Free of the browser and of React Native.

export const TAGS = ['s', 'utm_source', 'utm_medium', 'utm_campaign'] as const
const MOST = 60 // characters a tag keeps (server.TAG_MOST)

/** A link's query's tags ("?utm_source=whatsapp&utm_campaign=Gukesh+v+Sindarov"): each its first value, trimmed, at
 * most MOST characters; one not encoded right is left out. */
export function linkTags(query: string): Record<string, string> {
  const found: Record<string, string> = {}
  for (const part of query.replace(/^\?/, '').split('&')) {
    const at = part.indexOf('=')
    if (at < 0) continue
    let key: string, value: string
    try {
      key = decodeURIComponent(part.slice(0, at).replace(/\+/g, ' '))
      value = decodeURIComponent(part.slice(at + 1).replace(/\+/g, ' ')).trim()
    } catch {
      continue
    }
    if ((TAGS as readonly string[]).includes(key) && value && !(key in found)) found[key] = value.slice(0, MOST)
  }
  return found
}

/** A link to the site's page and its tags ("https://stann.in/gukesh?utm_source=whatsapp"); null for one with no
 * tags, or not a web link (the app's own, slam://…, a notification's). */
export function linkVisit(url: string): { path: string; tags: Record<string, string> } | null {
  const parts = /^https?:\/\/[^/?#]*(\/[^?#]*)?(\?[^#]*)?/i.exec(url)
  if (!parts) return null
  const tags = linkTags(parts[2] ?? '')
  return Object.keys(tags).length ? { path: parts[1] || '/', tags } : null
}

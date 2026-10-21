// The fan's own Feed (GET /api/me/feed/items, sports_follow/feed.py `page`), the web's myFeed.ts: a page at a
// time, the next when the list nears its end, and hiding an item at once. The Feed tab (app/feed.tsx) and
// Today's "From your players" (its top two, post.tsx) read it.

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type FeedEntry, type FeedPage as Page, type FeedVerdict } from './api'
import { noteNewest } from './feedSeen'

/** The fan's Feed, a page at a time, one player's when `player` is set; and a way to hide an item now. `size`:
 * items a page; `again`: anything that, changed, reads it again from the start (who the fan follows, a pull),
 * and null waits. */
export function useMyFeed(player: number | null, size?: number, again?: string | null) {
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const current = useRef<{ player: number | null; page: Page | null; loading: boolean }>({ player, page, loading })
  current.current = { player, page, loading }

  useEffect(() => {
    if (again === null) return
    let stale = false
    setError(null)
    setLoading(true)
    api.myFeedPage({ player, limit: size })
      .then((p) => { if (!stale) { setPage(p); noteNewest(p.newest) } })
      .catch((e: Error) => { if (!stale) setError(e.message) })
      .finally(() => { if (!stale) setLoading(false) })
    return () => { stale = true }
  }, [player, size, again])
  // Another player's (or everyone's) starts from nothing; the same one read again keeps its page until the new one's in.
  useEffect(() => { setPage(null) }, [player])

  const more = useCallback(() => {
    const { page: was, player: who, loading: busy } = current.current
    if (!was?.cursor || busy) return
    current.current.loading = true // a second call before the render sees `loading`
    setLoading(true)
    setError(null)
    api.myFeedPage({ player: who, cursor: was.cursor, limit: size })
      .then((p) => {
        if (current.current.player !== who) return // the fan picked someone else meanwhile
        setPage((now) => now && { ...p, items: [...now.items, ...p.items.filter((e) => !now.items.some((n) => n.item.id === e.item.id))] })
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [size])

  // Gone from the page straight away; back, with why, if the server didn't take it.
  const hide = useCallback(async (entry: FeedEntry, verdict: FeedVerdict) => {
    const before = current.current.page
    setPage((p) => p && { ...p, items: p.items.filter((e) => e.item.id !== entry.item.id), count: p.count - 1 })
    try {
      await api.feedFeedback(entry.player.id, entry.item.id, verdict)
    } catch (e) {
      setPage(before)
      throw e
    }
  }, [])

  return { page, error, loading, more, hide }
}

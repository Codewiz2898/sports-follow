// The fan's own Feed (GET /api/me/feed/items, sports_follow/feed.py `page`), the web's myFeed.ts: a page at a
// time, the next when the list nears its end, and hiding an item at once. The Feed tab (app/feed.tsx) and
// Today's "From your players" (its top two, post.tsx) read it.

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type FeedEntry, type FeedPage as Page, type FeedVerdict } from './api'
import { noteNewest } from './feedSeen'

/** The fan's Feed, a page at a time, one player's when `player` is set; and a way to hide an item now. `size`:
 * items a page; `again`: anything that, changed, reads it again from the start (who the fan follows, a pull),
 * and null waits. A page read again stays up until the new one's in. `failed`: which read the error is from,
 * the first page's (try it again from the start) or a later one's (try `more` again). */
export function useMyFeed(player: number | null, size?: number, again?: string | null) {
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [failed, setFailed] = useState<'first' | 'more' | null>(null)
  const [loading, setLoading] = useState(false)
  const current = useRef<{ player: number | null; page: Page | null; busy: boolean; read: number }>({ player, page, busy: false, read: 0 })
  current.current.player = player
  current.current.page = page

  useEffect(() => {
    if (again === null) return
    const read = ++current.current.read // a later page still on its way from an older read is dropped
    current.current.busy = true
    setError(null)
    setFailed(null)
    setLoading(true)
    api.myFeedPage({ player, limit: size })
      .then((p) => { if (current.current.read === read) { setPage(p); noteNewest(p.newest) } })
      .catch((e: Error) => { if (current.current.read === read) { setError(e.message); setFailed('first') } })
      .finally(() => { if (current.current.read === read) { current.current.busy = false; setLoading(false) } })
  }, [player, size, again])
  // Another player's (or everyone's) starts from nothing.
  useEffect(() => { setPage(null) }, [player])

  const more = useCallback(() => {
    const { page: was, player: who, busy, read } = current.current
    if (!was?.cursor || busy) return
    current.current.busy = true // a second call before the render sees `loading`
    setLoading(true)
    setError(null)
    setFailed(null)
    api.myFeedPage({ player: who, cursor: was.cursor, limit: size })
      .then((p) => {
        if (current.current.read !== read) return // read again from the start meanwhile (another player, a pull)
        setPage((now) => now && { ...p, items: [...now.items, ...p.items.filter((e) => !now.items.some((n) => n.item.id === e.item.id))] })
      })
      .catch((e: Error) => { if (current.current.read === read) { setError(e.message); setFailed('more') } })
      .finally(() => { if (current.current.read === read) { current.current.busy = false; setLoading(false) } })
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

  return { page, error, failed, loading, more, hide }
}

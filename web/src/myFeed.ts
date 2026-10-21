// The fan's own Feed in the web app (GET /api/me/feed/items, sports_follow/feed.py `page`): a page at a time, with
// the next as the end comes into view, and hiding an item at once. The Feed tab (pages/FeedPage.tsx) and Today's
// "From your players" (its top two) read it.

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type FeedEntry, type FeedPage as Page, type FeedTabKey, type FeedVerdict } from './api'
import { noteNewest } from './feedSeen'

/** The fan's Feed, a page at a time, one player's when `player` is set; and a way to hide an item now. `size`: items a
 * page; `again`: anything that, changed, reads it again from the start (who the fan follows), and null waits; `tab`:
 * Following (their players alone, the default) or For you (others' videos mixed in). */
export function useMyFeed(player: number | null, size?: number, again?: string | null, tab: FeedTabKey = 'following') {
  const [page, setPage] = useState<Page | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const current = useRef<{ player: number | null; tab: FeedTabKey; page: Page | null }>({ player, tab, page })
  current.current = { player, tab, page }

  useEffect(() => {
    if (again === null) return
    let stale = false
    setPage(null)
    setError(null)
    setLoading(true)
    api.myFeedPage({ player, limit: size, tab })
      .then((p) => { if (!stale) { setPage(p); noteNewest(p.newest) } })
      .catch((e: Error) => { if (!stale) setError(e.message) })
      .finally(() => { if (!stale) setLoading(false) })
    return () => { stale = true }
  }, [player, size, again, tab])

  const more = useCallback(() => {
    const { page: was, player: who, tab: whose } = current.current
    if (!was?.cursor || loading) return
    setLoading(true)
    api.myFeedPage({ player: who, cursor: was.cursor, limit: size, tab: whose })
      .then((p) => {
        if (current.current.player !== who || current.current.tab !== whose) return  // the fan picked someone else, or the other tab, meanwhile
        setPage((now) => now && { ...p, items: [...now.items, ...p.items.filter((e) => !now.items.some((n) => n.item.id === e.item.id))] })
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [loading, size])

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

/** Calls `onSeen` once the element it's put on comes near the screen: the next page then loads. */
export function useNearEnd(onSeen: () => void, active: boolean) {
  const node = useRef<HTMLDivElement | null>(null)
  const seen = useRef(onSeen)
  seen.current = onSeen
  useEffect(() => {
    const el = node.current
    if (!el || !active) return
    const watch = new IntersectionObserver((rows) => { if (rows.some((r) => r.isIntersecting)) seen.current() }, { rootMargin: '800px 0px' })
    watch.observe(el)
    return () => watch.disconnect()
  }, [active])
  return node
}


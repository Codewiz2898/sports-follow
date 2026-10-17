import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type ActivityItem } from './api'
import { useFollowing } from './following'

interface ActivityState {
  items: ActivityItem[]
  loaded: boolean
  more: (() => Promise<void>) | null  // the next page, when there is one
  unread: number  // items newer than the last time the fan looked at the list
  seen: number
  markSeen: () => void
  muted: Set<number>  // games the fan muted
  setMuted: (eventId: number, muted: boolean) => Promise<void>
}

const Ctx = createContext<ActivityState | null>(null)
const SEEN = 'sf-alerts-seen'
const SETTLE_MS = 1500  // one poll can bring several moments: fetch once they've all arrived

function readSeen(): number | null {
  try {
    const saved = localStorage.getItem(SEEN)
    return saved ? Number(saved) || 0 : null
  } catch { return null }
}

function writeSeen(id: number) {
  try { localStorage.setItem(SEEN, String(id)) } catch { /* storage blocked */ }
}

/**
 * The fan's activity list (every moment of their players, and of the games they follow by themselves, pushed
 * or not, newest first), how much of it is new, and the games they muted. It loads when the follow list or a
 * level changes and picks up new moments as the stream brings them. "Seen" is per device, like quiet hours.
 */
export function ActivityProvider({ children }: { children: ReactNode }) {
  const { cards, loaded: following, heard, matches } = useFollowing()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [seen, setSeen] = useState<number | null>(readSeen)
  const [muted, setMutedIds] = useState<Set<number>>(new Set())
  // Following someone new, or turning a player off, changes the whole list.
  const who = [...cards.map((c) => `${c.player_id}:${c.alerts ?? 'key'}`), ...matches.map((m) => `game${m.event_id}:${m.alerts}`)].sort().join(',')

  useEffect(() => {
    if (!following) return
    if (!who) { setItems([]); setNext(null); setLoaded(true); return }
    let stale = false
    api.activity().then((r) => {
      if (stale) return
      setItems(r.items)
      setNext(r.next)
      // The first time on this device, what's already there isn't news.
      setSeen((s) => { if (s != null) return s; const top = r.items[0]?.id ?? 0; writeSeen(top); return top })
    }).catch(() => {}).finally(() => { if (!stale) setLoaded(true) })
    api.mutes().then((r) => { if (!stale) setMutedIds(new Set(r.event_ids)) }).catch(() => {})
    return () => { stale = true }
  }, [following, who])

  useEffect(() => {
    if (!heard) return
    const timer = window.setTimeout(() => {
      api.activity().then((r) => setItems((prev) => {
        const newest = prev[0]?.id ?? 0
        return [...r.items.filter((i) => i.id > newest), ...prev]
      })).catch(() => {})
    }, SETTLE_MS)
    return () => window.clearTimeout(timer)
  }, [heard])

  const more = useCallback(async () => {
    if (next == null) return
    const r = await api.activity(next)
    setItems((prev) => [...prev, ...r.items.filter((i) => i.id < (prev[prev.length - 1]?.id ?? Infinity))])
    setNext(r.next)
  }, [next])

  const markSeen = useCallback(() => {
    const top = items[0]?.id
    if (top != null && top !== seen) { setSeen(top); writeSeen(top) }
  }, [items, seen])

  const setMuted = useCallback(async (eventId: number, on: boolean) => {
    const apply = (value: boolean) => {
      setMutedIds((prev) => { const s = new Set(prev); if (value) s.add(eventId); else s.delete(eventId); return s })
      setItems((prev) => prev.map((i) => (i.event_id === eventId ? { ...i, muted: value } : i)))
    }
    apply(on)
    try { await api.mute(eventId, on) } catch (e) { apply(!on); throw e }
  }, [])

  const value = useMemo<ActivityState>(() => ({
    items,
    loaded,
    more: next != null ? more : null,
    unread: items.filter((i) => i.id > (seen ?? Infinity)).length,
    seen: seen ?? 0,
    markSeen,
    muted,
    setMuted,
  }), [items, loaded, next, more, seen, markSeen, muted, setMuted])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useActivity(): ActivityState {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useActivity outside ActivityProvider')
  return ctx
}

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type ActivityItem } from './api'
import { useFollowing } from './following'
import { ACTIVITY, refetcher, type Refetcher } from './refetch'

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
    // Asked even when the fan follows nothing now: a game they followed by itself has lapsed since it ended, and
    // its moments stay in the list (30 days), as a player's do.
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

  // New moments, once the stream has brought one the fan hears about (following.tsx's heard): asked after a wait
  // of this app's own (refetch.ts), so the apps that heard the same goal don't all ask at once.
  const later = useRef<Refetcher | null>(null)
  useEffect(() => {
    const r = refetcher(ACTIVITY, () => {
      api.activity().then((res) => setItems((prev) => {
        const newest = prev[0]?.id ?? 0
        return [...res.items.filter((i) => i.id > newest), ...prev]
      })).catch(() => {})
    })
    later.current = r
    return () => r.stop()
  }, [])
  useEffect(() => { if (heard) later.current?.heard() }, [heard])

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

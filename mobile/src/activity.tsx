// The fan's activity list (every moment of their players, pushed or not, newest first), how much of it is new,
// and the games they muted, as the web app's ActivityProvider (web/src/activity.tsx). "Seen" is per device,
// kept on the phone.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAccount } from './account'
import { api, type ActivityItem } from './api'
import { useFollowing } from './following'

interface Activity {
  items: ActivityItem[]
  loaded: boolean
  more: (() => Promise<void>) | null // the next page, when there is one
  unread: number // items newer than the last time the fan looked at the list
  seen: number
  markSeen: () => void
  muted: Set<number> // games the fan muted
  setMuted: (eventId: number, muted: boolean) => Promise<void>
}

const ActivityContext = createContext<Activity | null>(null)
const SEEN = 'stann.alerts-seen'

export function ActivityProvider({ children }: { children: ReactNode }) {
  const { cards, loaded: following, heard } = useFollowing()
  const { epoch } = useAccount()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [seen, setSeen] = useState<number | null>(null)
  const [seenRead, setSeenRead] = useState(false)
  const [muted, setMutedIds] = useState<Set<number>>(new Set())
  // Following someone new, or turning a player off, changes the whole list.
  const who = cards.map((c) => `${c.player_id}:${c.alerts ?? 'key'}`).sort().join(',')

  // What this phone last saw, read again for each fan (a sign-in or out forgets it: account.tsx).
  useEffect(() => {
    setItems([])
    setSeen(null)
    setSeenRead(false)
    AsyncStorage.getItem(SEEN).then((v) => { if (v) setSeen(Number(v) || 0) }).catch(() => {}).finally(() => setSeenRead(true))
  }, [epoch])

  useEffect(() => {
    if (!following || !seenRead) return
    if (!who) { setItems([]); setNext(null); setLoaded(true); return }
    let stale = false
    api.activity().then((r) => {
      if (stale) return
      setItems(r.items)
      setNext(r.next)
      // The first time on this phone, what's already there isn't news.
      setSeen((s) => {
        if (s != null) return s
        const top = r.items[0]?.id ?? 0
        AsyncStorage.setItem(SEEN, String(top)).catch(() => {})
        return top
      })
    }).catch(() => {}).finally(() => { if (!stale) setLoaded(true) })
    api.mutes().then((r) => { if (!stale) setMutedIds(new Set(r.event_ids)) }).catch(() => {})
    return () => { stale = true }
  }, [following, who, seenRead])

  // New moments, when the follow list came back changed.
  useEffect(() => {
    if (!heard || !who) return
    api.activity().then((r) => setItems((prev) => {
      const newest = prev[0]?.id ?? 0
      return [...r.items.filter((i) => i.id > newest), ...prev]
    })).catch(() => {})
  }, [heard, who])

  const more = useCallback(async () => {
    if (next == null) return
    const r = await api.activity(next)
    setItems((prev) => [...prev, ...r.items.filter((i) => i.id < (prev[prev.length - 1]?.id ?? Infinity))])
    setNext(r.next)
  }, [next])

  const markSeen = useCallback(() => {
    const top = items[0]?.id
    if (top != null && top !== seen) { setSeen(top); AsyncStorage.setItem(SEEN, String(top)).catch(() => {}) }
  }, [items, seen])

  const setMuted = useCallback(async (eventId: number, on: boolean) => {
    const apply = (value: boolean) => {
      setMutedIds((prev) => { const s = new Set(prev); if (value) s.add(eventId); else s.delete(eventId); return s })
      setItems((prev) => prev.map((i) => (i.event_id === eventId ? { ...i, muted: value } : i)))
    }
    apply(on)
    try { await api.mute(eventId, on) } catch (e) { apply(!on); throw e }
  }, [])

  const value = useMemo<Activity>(() => ({
    items, loaded, more: next != null ? more : null,
    unread: items.filter((i) => i.id > (seen ?? Infinity)).length,
    seen: seen ?? 0, markSeen, muted, setMuted,
  }), [items, loaded, next, more, seen, markSeen, muted, setMuted])

  return <ActivityContext value={value}>{children}</ActivityContext>
}

export function useActivity(): Activity {
  const value = use(ActivityContext)
  if (!value) throw new Error('useActivity outside ActivityProvider')
  return value
}

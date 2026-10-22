// The fan's activity list (every moment of their players, and of the games they follow by themselves, pushed or
// not, newest first), how much of it is new, and the games they muted, as the web app's ActivityProvider
// (web/src/activity.tsx). "Seen" is per device, kept on the phone.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AppState } from 'react-native'
import { followsKey } from '../../web/src/alerts'
import { ACTIVITY, refetcher, type Refetcher } from '../../web/src/refetch'
import StannNotifications from '../modules/stann-notifications'
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
  const { cards, loaded: following, heard, matches, teams } = useFollowing()
  const { epoch } = useAccount()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [next, setNext] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [seen, setSeen] = useState<number | null>(null)
  const [seenRead, setSeenRead] = useState(false)
  const [muted, setMutedIds] = useState<Set<number>>(new Set())
  // Following someone new, or turning a player off, changes the whole list; a game or a team too.
  const who = followsKey(cards, matches, teams)
  // Which fan this is (account.tsx's epoch), and the mutes and unmutes under way here and how many have begun or
  // finished: a read of the muted games asked by an earlier fan, or that overlapped one, is dropped (it may be from
  // before it), and the next one wins.
  const fan = useRef(epoch)
  fan.current = epoch
  const muteWrites = useRef({ busy: 0, seen: 0 })

  // The games the fan muted, as the server has them now: one muted from its notification's button (straight from
  // the phone's own code, the app away or not) or on another device too (BUG-279).
  const readMutes = useCallback(() => {
    const before = muteWrites.current.seen
    const asked = fan.current
    api.mutes().then((r) => {
      if (fan.current !== asked || muteWrites.current.busy > 0 || muteWrites.current.seen !== before) return
      setMutedIds(new Set(r.event_ids))
    }).catch(() => {})
  }, [])

  // What this phone last saw, read again for each fan (a sign-in or out forgets it: account.tsx).
  useEffect(() => {
    setItems([])
    setNext(null)
    setLoaded(false)
    setMutedIds(new Set())
    setSeen(null)
    setSeenRead(false)
    AsyncStorage.getItem(SEEN).then((v) => { if (v) setSeen(Number(v) || 0) }).catch(() => {}).finally(() => setSeenRead(true))
  }, [epoch])

  useEffect(() => {
    if (!following || !seenRead) return
    // Asked even when the fan follows nothing now: a game they followed by itself has lapsed since it ended, and
    // its moments stay in the list (30 days), as a player's do.
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
    readMutes()
    return () => { stale = true }
  }, [following, who, seenRead, readMutes])

  // New moments, when there may be some (following.tsx's heard): the follow list came back changed, a followed
  // game's status or score moved on, a notification came, or the app came back to the front. Asked after a wait
  // of this phone's own (web/src/refetch.ts): every phone that got the same goal's notification hears it at once.
  // Not while the app is away: coming back says there's news, and asks. The muted games are read with them (one
  // muted on another device), and at once on coming back (one muted from a notification meanwhile).
  const later = useRef<Refetcher | null>(null)
  useEffect(() => {
    let stale = false
    const r = refetcher(ACTIVITY, () => {
      if (AppState.currentState !== 'active') return // nobody's looking; coming back asks
      api.activity().then((res) => {
        if (stale) return
        setItems((prev) => {
          const newest = prev[0]?.id ?? 0
          return [...res.items.filter((i) => i.id > newest), ...prev]
        })
      }).catch(() => {})
      readMutes()
    })
    later.current = r
    // Back in front: a wait begun while away (React Native held its timer) goes, as coming back's news starts one.
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') { r.stop(); readMutes() } })
    return () => { stale = true; r.stop(); sub.remove() }
  }, [epoch, readMutes])
  useEffect(() => { if (heard) later.current?.heard() }, [heard])

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
    const w = muteWrites.current
    w.busy++
    w.seen++
    apply(on)
    try { await api.mute(eventId, on) } catch (e) { apply(!on); throw e } finally { w.busy--; w.seen++ }
    // Muted: its live card goes now, as the card's own Mute button takes it down, not with the server's next.
    if (on) StannNotifications?.takeDownLive(String(eventId))
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

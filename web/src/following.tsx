import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, subscribe, type AlertLevel, type Card, type FollowTarget, type PlayerMoment, type StreamEvent } from './api'
import { wanted } from './notifications'

interface FollowingState {
  cards: Card[]
  loaded: boolean
  failed: boolean  // the follow list couldn't be loaded (yet): not the same as following nobody
  reload: () => Promise<void>
  progress: Record<number, string[]>
  follow: (target: FollowTarget) => Promise<Card>
  unfollow: (playerId: number) => Promise<void>
  isFollowing: (playerId: number) => boolean
  byId: (playerId: number) => Card | undefined
  setAlerts: (playerId: number, level: AlertLevel) => Promise<void>
  moments: PlayerMoment[]  // moments to show as toasts while the app is open
  dismissMoment: (id: number) => void
  heard: number  // moments the stream has brought, any level, and its reconnects: the activity list refreshes on it
  listen: (onEvent: (e: StreamEvent) => void) => () => void  // every event the fan stream brings, for a followed player's page
}

const Ctx = createContext<FollowingState | null>(null)
const MAX_PROGRESS = 8
const TOAST_MS = 8000
const RETRY_MS = 2000
const RETRY_MAX_MS = 60_000

/** The fan's players as the server lists them, and any this app followed that the list doesn't have yet. */
function withLocal(players: Card[], prev: Card[]): Card[] {
  return [...players, ...prev.filter((c) => !players.some((p) => p.player_id === c.player_id))]
}

/**
 * The fan's followed players, kept live over ONE stream (/api/me/stream) for all of them.
 * The stream's channel set is fixed at connect time, so it reconnects when the follow list changes.
 * Every connect opens with the players' cards as they are now, so a reconnect is also a resync.
 */
export function FollowingProvider({ children }: { children: ReactNode }) {
  const [cards, setCards] = useState<Card[]>([])
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const [progress, setProgress] = useState<Record<number, string[]>>({})
  const [moments, setMoments] = useState<PlayerMoment[]>([])
  const [heard, setHeard] = useState(0)
  const idsKey = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const cardsRef = useRef(cards)
  cardsRef.current = cards
  const loadedRef = useRef(loaded)
  loadedRef.current = loaded
  const listeners = useRef(new Set<(e: StreamEvent) => void>())
  const alertWrites = useRef(new Map<number, { latest: number; saved: AlertLevel; queue: Promise<void> }>())

  const reload = useCallback(() => api.following().then((r) => {
    setCards((prev) => withLocal(r.players, prev))
    setLoaded(true)
    setFailed(false)
  }, () => setFailed(true)), [])

  useEffect(() => { reload() }, [reload])

  // Until the list loads, try again: soon, then less often, and at once on coming back online or to the app.
  useEffect(() => {
    if (loaded || !failed) return
    let wait = RETRY_MS
    let stopped = false
    let timer: number | undefined
    const again = () => reload().then(() => {
      wait = Math.min(wait * 2, RETRY_MAX_MS)
      if (!stopped) timer = window.setTimeout(again, wait)
    })
    timer = window.setTimeout(again, wait)
    const wake = () => { if (document.visibilityState === 'visible') reload() }
    window.addEventListener('online', wake)
    document.addEventListener('visibilitychange', wake)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.removeEventListener('online', wake)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [loaded, failed, reload])

  const apply = useCallback((e: StreamEvent) => {
    if (e.type === 'following') {
      // First on every (re)connect: each card as it is now, so a score or result published while the
      // stream was down (a locked phone, a restart) isn't missed, and the activity list catches up.
      // Who the fan follows, and their alert levels, stay as this app has them, unless the list never
      // loaded (a player was followed since): then this is the list.
      const fresh = new Map(e.players.map((c) => [c.player_id, c]))
      if (!loadedRef.current) {
        setCards((prev) => withLocal(e.players, prev))
        setLoaded(true)
        setFailed(false)
      } else setCards((prev) => prev.map((c) => {
        const f = fresh.get(c.player_id)
        return f && f.version >= c.version ? { ...c, ...f, alerts: c.alerts } : c
      }))
      setProgress((p) => ({ ...p, ...Object.fromEntries(e.players.filter((c) => c.status !== 'building').map((c) => [c.player_id, []])) }))
      setHeard((n) => n + 1)
    } else if (e.type === 'card') {
      setCards((prev) => prev.map((c) => (c.player_id === e.player_id && e.card.version >= c.version ? { ...c, ...e.card } : c)))
      if (e.card.status !== 'building') setProgress((p) => ({ ...p, [e.player_id]: [] }))
    } else if (e.type === 'moment') {
      setHeard((n) => n + 1)
      // With the app in front the service worker skips the system notification, so the toast
      // follows the same per-player level the push does.
      const level = cardsRef.current.find((c) => c.player_id === e.moment.player_id)?.alerts ?? 'key'
      if (!wanted(level, e.moment)) return
      setMoments((m) => (m.some((x) => x.id === e.moment.id) ? m : [...m, e.moment].slice(-3)))
      window.setTimeout(() => setMoments((m) => m.filter((x) => x.id !== e.moment.id)), TOAST_MS)
    } else if (e.type === 'progress') {
      setProgress((p) => ({ ...p, [e.player_id]: [...(p[e.player_id] ?? []), e.message].slice(-MAX_PROGRESS) }))
    } else if (e.type === 'moved') {
      // The build found this name already belongs to another player: swap in that player's card.
      api.card(e.to).then((card) => setCards((prev) => {
        const without = prev.filter((c) => c.player_id !== e.from && c.player_id !== e.to)
        return [...without, card]
      }))
    }
    for (const onEvent of listeners.current) onEvent(e)
  }, [])

  const listen = useCallback((onEvent: (e: StreamEvent) => void) => {
    listeners.current.add(onEvent)
    return () => { listeners.current.delete(onEvent) }
  }, [])

  useEffect(() => {
    if (!idsKey) return
    return subscribe('/api/me/stream', apply)
  }, [idsKey, apply])

  const follow = useCallback(async (target: FollowTarget) => {
    const { card } = await api.follow(target)
    setCards((prev) => (prev.some((c) => c.player_id === card.player_id) ? prev.map((c) => (c.player_id === card.player_id ? card : c)) : [...prev, card]))
    return card
  }, [])

  const unfollow = useCallback(async (playerId: number) => {
    await api.unfollow(playerId)
    setCards((prev) => prev.filter((c) => c.player_id !== playerId))
  }, [])

  // A level shows the moment it's tapped. Its PUTs go one at a time per player, and one already
  // replaced by a newer tap isn't sent, so fast taps can't land out of order: the server ends on the
  // last one. If that fails, the level goes back to the last one saved, and the error is the caller's.
  const setAlerts = useCallback((playerId: number, level: AlertLevel) => {
    const show = (value: AlertLevel) => setCards((prev) => prev.map((c) => (c.player_id === playerId ? { ...c, alerts: value } : c)))
    const writes = alertWrites.current.get(playerId) ?? { latest: 0, saved: cardsRef.current.find((c) => c.player_id === playerId)?.alerts ?? 'key', queue: Promise.resolve() }
    alertWrites.current.set(playerId, writes)
    const n = ++writes.latest
    show(level)
    const done = writes.queue.then(async () => {
      if (n !== writes.latest) return
      try {
        await api.setAlerts(playerId, level)
        writes.saved = level
      } catch (e) {
        if (n === writes.latest) show(writes.saved)
        throw e
      } finally {
        if (n === writes.latest) alertWrites.current.delete(playerId)
      }
    })
    writes.queue = done.catch(() => {})
    return done
  }, [])

  const dismissMoment = useCallback((id: number) => setMoments((m) => m.filter((x) => x.id !== id)), [])

  const value = useMemo<FollowingState>(() => ({
    cards,
    loaded,
    failed,
    reload,
    progress,
    follow,
    unfollow,
    isFollowing: (id) => cardsRef.current.some((c) => c.player_id === id),
    byId: (id) => cardsRef.current.find((c) => c.player_id === id),
    setAlerts,
    moments,
    dismissMoment,
    heard,
    listen,
  }), [cards, loaded, failed, reload, progress, follow, unfollow, setAlerts, moments, dismissMoment, heard, listen])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useFollowing(): FollowingState {
  const value = useContext(Ctx)
  if (!value) throw new Error('useFollowing outside FollowingProvider')
  return value
}

export { tileOrder } from './today'

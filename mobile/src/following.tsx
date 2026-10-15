// The fan's players (GET /api/me/following), shared by every screen: Today's tiles, Search's Follow buttons
// and a player's page agree on who is followed. While a screen is in front it reloads every 30 s; live
// updates over the fan stream come later (BHA-231).

import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AppState } from 'react-native'
import { api, type Card, type FollowTarget } from './api'

const EVERY_MS = 30_000

interface Following {
  cards: Card[]
  loaded: boolean
  error: string | null
  reload: () => Promise<void>
  follow: (target: FollowTarget) => Promise<Card>
  unfollow: (playerId: number) => Promise<void>
  isFollowing: (playerId?: number | null) => boolean
}

const FollowingContext = createContext<Following | null>(null)

export function FollowingProvider({ children }: { children: ReactNode }) {
  const [cards, setCards] = useState<Card[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  // Counts follows and unfollows begun and finished: a reload that overlapped one may have read the list
  // from before it, so its answer is dropped and the next one wins.
  const writes = useRef(0)

  const reload = useCallback(async () => {
    const before = writes.current
    try {
      const { players } = await api.following()
      if (writes.current !== before) return
      setCards(players)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoaded(true)
    }
  }, [])

  // Every 30 s while the app is in front; at once on coming back to it.
  useEffect(() => {
    const start = () => {
      clearInterval(timer.current)
      timer.current = setInterval(reload, EVERY_MS)
    }
    reload()
    start()
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') { reload(); start() } else clearInterval(timer.current)
    })
    return () => { clearInterval(timer.current); sub.remove() }
  }, [reload])

  const follow = useCallback(async (target: FollowTarget) => {
    writes.current++
    const { card } = await api.follow(target).finally(() => { writes.current++ })
    setCards((prev) => (prev.some((c) => c.player_id === card.player_id) ? prev.map((c) => (c.player_id === card.player_id ? card : c)) : [...prev, card]))
    return card
  }, [])

  const unfollow = useCallback(async (playerId: number) => {
    writes.current++
    await api.unfollow(playerId).finally(() => { writes.current++ })
    setCards((prev) => prev.filter((c) => c.player_id !== playerId))
  }, [])

  const value = useMemo<Following>(() => {
    const ids = new Set(cards.map((c) => c.player_id))
    return { cards, loaded, error, reload, follow, unfollow, isFollowing: (id) => id != null && ids.has(id) }
  }, [cards, loaded, error, reload, follow, unfollow])

  return <FollowingContext value={value}>{children}</FollowingContext>
}

export function useFollowing(): Following {
  const value = use(FollowingContext)
  if (!value) throw new Error('useFollowing outside FollowingProvider')
  return value
}

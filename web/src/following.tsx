import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, subscribe, type Card, type FollowTarget, type StreamEvent } from './api'

interface FollowingState {
  cards: Card[]
  loaded: boolean
  progress: Record<number, string[]>
  follow: (target: FollowTarget) => Promise<Card>
  unfollow: (playerId: number) => Promise<void>
  isFollowing: (playerId: number) => boolean
  byId: (playerId: number) => Card | undefined
}

const Ctx = createContext<FollowingState | null>(null)
const MAX_PROGRESS = 8

/**
 * The fan's followed players, kept live over ONE stream (/api/me/stream) for all of them.
 * The stream's channel set is fixed at connect time, so it reconnects when the follow list changes.
 */
export function FollowingProvider({ children }: { children: ReactNode }) {
  const [cards, setCards] = useState<Card[]>([])
  const [loaded, setLoaded] = useState(false)
  const [progress, setProgress] = useState<Record<number, string[]>>({})
  const idsKey = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const cardsRef = useRef(cards)
  cardsRef.current = cards

  useEffect(() => {
    api.following().then((r) => { setCards(r.players); setLoaded(true) }).catch(() => setLoaded(true))
  }, [])

  const apply = useCallback((e: StreamEvent) => {
    if (e.type === 'card') {
      setCards((prev) => prev.map((c) => (c.player_id === e.player_id && e.card.version >= c.version ? { ...c, ...e.card } : c)))
      if (e.card.status !== 'building') setProgress((p) => ({ ...p, [e.player_id]: [] }))
    } else if (e.type === 'progress') {
      setProgress((p) => ({ ...p, [e.player_id]: [...(p[e.player_id] ?? []), e.message].slice(-MAX_PROGRESS) }))
    } else if (e.type === 'moved') {
      // The build found this name already belongs to another player: swap in that player's card.
      api.card(e.to).then((card) => setCards((prev) => {
        const without = prev.filter((c) => c.player_id !== e.from && c.player_id !== e.to)
        return [...without, card]
      }))
    }
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

  const value = useMemo<FollowingState>(() => ({
    cards,
    loaded,
    progress,
    follow,
    unfollow,
    isFollowing: (id) => cardsRef.current.some((c) => c.player_id === id),
    byId: (id) => cardsRef.current.find((c) => c.player_id === id),
  }), [cards, loaded, progress, follow, unfollow])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useFollowing(): FollowingState {
  const value = useContext(Ctx)
  if (!value) throw new Error('useFollowing outside FollowingProvider')
  return value
}

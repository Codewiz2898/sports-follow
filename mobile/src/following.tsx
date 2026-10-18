// The fan's players and teams (GET /api/me/following) and the games they follow by themselves (GET /api/me/matches),
// shared by every screen, as the web app's FollowingProvider (web/src/following.tsx) has them. The web keeps
// them live over the fan stream; here they're read again every 30 s while the app is in front, and at once on
// coming back to it (the stream comes with BHA-231). `heard` says when there may be new moments: a changed
// list, a followed game's score or status moving on, a notification arriving, the app coming back to the front.

import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AppState } from 'react-native'
import { useAccount } from './account'
import { api, type AlertLevel, type Card, type FollowedMatch, type FollowedTeam, type FollowTarget } from './api'
import { useNotificationsArriving } from './push'

const EVERY_MS = 30_000

interface Following {
  cards: Card[]
  loaded: boolean
  failed: boolean // the follow list couldn't be loaded (yet): not the same as following nobody
  reload: () => Promise<boolean> // true when the list came back changed (heard)
  progress: Record<number, string[]> // a page being built's progress lines (the stream's; none yet here)
  follow: (target: FollowTarget) => Promise<Card>
  unfollow: (playerId: number) => Promise<void>
  isFollowing: (playerId?: number | null) => boolean
  byId: (playerId: number) => Card | undefined
  setAlerts: (playerId: number, level: AlertLevel) => Promise<void>
  heard: number // how often the list has come back changed: the activity list and This week refresh on it
  // The games the fan follows by themselves (sports_follow/matches.py), each board as last read, and whether
  // the list has come; following one, or stopping, by its address after /match/; its level.
  matches: FollowedMatch[]
  matchesLoaded: boolean
  reloadMatches: () => Promise<boolean>
  followMatch: (address: string, on: boolean, eventId?: number | null) => Promise<void> // stopped by its event where known
  setMatchAlerts: (eventId: number, level: AlertLevel) => Promise<void>
  // The teams the fan follows (sports_follow/teams.py), each with its next game, last result and game on now, as
  // the players' list brings them; following one or stopping, and its level.
  teams: FollowedTeam[]
  followTeam: (teamId: number, on: boolean) => Promise<void>
  isFollowingTeam: (teamId: number) => boolean
  setTeamAlerts: (teamId: number, level: AlertLevel) => Promise<void>
}

const FollowingContext = createContext<Following | null>(null)
const NO_PROGRESS: Record<number, string[]> = {}

export function FollowingProvider({ children }: { children: ReactNode }) {
  const { epoch } = useAccount()
  const [cards, setCards] = useState<Card[]>([])
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const [heard, setHeard] = useState(0)
  const [matches, setMatches] = useState<FollowedMatch[]>([])
  const [matchesLoaded, setMatchesLoaded] = useState(false)
  const [teams, setTeams] = useState<FollowedTeam[]>([])
  const teamsRef = useRef(teams)
  teamsRef.current = teams
  const cardsRef = useRef(cards)
  cardsRef.current = cards
  const matchesRef = useRef(matches)
  matchesRef.current = matches
  const timer = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  // Counts follows and unfollows begun and finished: a reload that overlapped one may have read the list
  // from before it, so its answer is dropped and the next one wins.
  const writes = useRef(0)
  // Which fan this is (account.tsx's epoch): an answer asked for by an earlier one is dropped.
  const fan = useRef(epoch)
  fan.current = epoch
  const versions = useRef('')
  // Each followed game's status and score as last read (null before the first read); and the game follows and
  // levels under way, and how many have begun or finished: a re-read that started while one was under way, or
  // that one began or finished during, may have read the list from before it, so its answer is dropped.
  const games = useRef<Map<number, string> | null>(null)
  const matchWrites = useRef({ busy: 0, seen: 0 })
  const alertWrites = useRef(new Map<number, { latest: number; saved: AlertLevel; queue: Promise<void> }>())

  // The list as the server has it now: a player unfollowed elsewhere (or a whole account signed out) goes.
  // True when it came back changed (heard).
  const reload = useCallback(async (): Promise<boolean> => {
    const before = writes.current
    const asked = fan.current
    try {
      const { players, teams: theirs = [] } = await api.following()
      if (writes.current !== before || fan.current !== asked) return false
      setCards(players)
      setTeams(theirs)
      setLoaded(true)
      setFailed(false)
      // A team's game moving on (its board, its next game, a result) is news too.
      const now = [...players.map((c) => `${c.player_id}:${c.version}`), ...theirs.map((x) => `t${x.team_id}:${x.live?.score ?? ''}:${x.next?.match ?? ''}:${x.last?.event_id ?? ''}`)].sort().join(',')
      if (now === versions.current) return false
      versions.current = now
      setHeard((n) => n + 1)
      return true
    } catch {
      if (fan.current === asked) setFailed(true)
      return false
    }
  }, [])

  // The games followed by themselves, as the server has them now. A followed game whose status or score moved
  // on since the last read (it started, a goal, a map, it ended) counts as the list changed (heard): the
  // activity list looks for its moments then, as the web's stream brings each. Not every new board, not a game
  // just followed or dropped (the activity list loads again for those), nor the first read. True when heard.
  const reloadMatches = useCallback(async (): Promise<boolean> => {
    const before = matchWrites.current.seen
    const busy = matchWrites.current.busy > 0
    const asked = fan.current
    let moved = false
    try {
      const r = await api.matches()
      if (fan.current !== asked || busy || matchWrites.current.seen !== before) return false
      setMatches(r.matches)
      const was = games.current
      const now = new Map<number, string>(r.matches.map((m) => [m.event_id, `${m.status}:${m.live?.score ?? ''}`]))
      games.current = now
      moved = Boolean(was && [...now].some(([id, said]) => was.has(id) && was.get(id) !== said))
      if (moved) setHeard((n) => n + 1)
    } catch { /* the last list stays */ }
    if (fan.current === asked) setMatchesLoaded(true)
    return moved
  }, [])

  // Every 30 s while the app is in front; at once on coming back to it. A sign-in or out (a new epoch) is
  // another fan: what was shown goes, and their list loads.
  useEffect(() => {
    setCards([])
    setLoaded(false)
    setFailed(false)
    setMatches([])
    setMatchesLoaded(false)
    setTeams([])
    versions.current = ''
    games.current = null
    const both = () => Promise.all([reload(), reloadMatches()])
    const start = () => {
      clearInterval(timer.current)
      timer.current = setInterval(both, EVERY_MS)
    }
    both()
    start()
    // Back in front: what came while it was away (a notification in the tray) may be in the activity list,
    // once (unless the lists coming back changed have said so already).
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'active') { clearInterval(timer.current); return }
      start()
      const asked = fan.current
      both().then((said) => { if (!said.some(Boolean) && fan.current === asked) setHeard((n) => n + 1) })
    })
    return () => { clearInterval(timer.current); sub.remove() }
  }, [reload, reloadMatches, epoch])

  // A notification while the app is in front: one of the fan's moments, a player's or a game's, is new.
  useNotificationsArriving(useCallback(() => setHeard((n) => n + 1), []))

  // A game follow or level on its way to the server (matchWrites: re-reads overlapping it are dropped).
  const writing = useCallback(async <T,>(work: () => Promise<T>): Promise<T> => {
    const w = matchWrites.current
    w.busy++
    w.seen++
    try { return await work() } finally { w.busy--; w.seen++ }
  }, [])

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

  // As the web's: a level shows the moment it's tapped; its PUTs go one at a time per player, and one already
  // replaced by a newer tap isn't sent, so the server ends on the last one. If that fails, the level goes
  // back to the last one saved, and the error is the caller's.
  const setAlerts = useCallback((playerId: number, level: AlertLevel) => {
    const show = (value: AlertLevel) => setCards((prev) => prev.map((c) => (c.player_id === playerId ? { ...c, alerts: value } : c)))
    const pending = alertWrites.current.get(playerId) ?? { latest: 0, saved: cardsRef.current.find((c) => c.player_id === playerId)?.alerts ?? 'key', queue: Promise.resolve() }
    alertWrites.current.set(playerId, pending)
    const n = ++pending.latest
    show(level)
    const done = pending.queue.then(async () => {
      if (n !== pending.latest) return
      try {
        await api.setAlerts(playerId, level)
        pending.saved = level
      } catch (e) {
        if (n === pending.latest) show(pending.saved)
        throw e
      } finally {
        if (n === pending.latest) alertWrites.current.delete(playerId)
      }
    })
    pending.queue = done.catch(() => {})
    return done
  }, [])

  // As the web's: stopping goes by the event the follow is on where that's known (it may be the game's other
  // address, a chess board's); then the list as the server has it.
  const followMatch = useCallback(async (address: string, on: boolean, eventId?: number | null) => {
    const at = address.indexOf('/')
    await writing(() => (!on && eventId != null ? api.unfollowMatch(eventId) : api.followMatch(address.slice(0, at), address.slice(at + 1), on)))
    await reloadMatches()
  }, [writing, reloadMatches])

  // As the web's: following a team or stopping, then the list as the server has it (its next game, its board).
  const followTeam = useCallback(async (teamId: number, on: boolean) => {
    writes.current++
    await api.followTeam(teamId, on).finally(() => { writes.current++ })
    if (!on) setTeams((prev) => prev.filter((x) => x.team_id !== teamId))
    await reload()
  }, [reload])

  // A team's level shows the moment it's tapped, and goes back to what it was if the server refuses it.
  const setTeamAlerts = useCallback(async (teamId: number, level: AlertLevel) => {
    const was = teamsRef.current.find((x) => x.team_id === teamId)?.alerts ?? 'key'
    const show = (value: AlertLevel) => setTeams((prev) => prev.map((x) => (x.team_id === teamId ? { ...x, alerts: value } : x)))
    show(level)
    writes.current++
    try { await api.setTeamAlerts(teamId, level) } catch (e) { show(was); throw e } finally { writes.current++ }
  }, [])

  // A game's level shows the moment it's tapped, and goes back to what it was if the server refuses it.
  const setMatchAlerts = useCallback(async (eventId: number, level: AlertLevel) => {
    const was = matchesRef.current.find((m) => m.event_id === eventId)?.alerts ?? 'key'
    const show = (value: AlertLevel) => setMatches((prev) => prev.map((m) => (m.event_id === eventId ? { ...m, alerts: value } : m)))
    show(level)
    try { await writing(() => api.setMatchAlerts(eventId, level)) } catch (e) { show(was); throw e }
  }, [writing])

  const value = useMemo<Following>(() => {
    const ids = new Set(cards.map((c) => c.player_id))
    return {
      cards, loaded, failed, reload, progress: NO_PROGRESS, follow, unfollow, setAlerts, heard,
      isFollowing: (id) => id != null && ids.has(id),
      byId: (id) => cards.find((c) => c.player_id === id),
      matches, matchesLoaded, reloadMatches, followMatch, setMatchAlerts,
      teams, followTeam, setTeamAlerts,
      isFollowingTeam: (id) => teams.some((x) => x.team_id === id),
    }
  }, [cards, loaded, failed, reload, follow, unfollow, setAlerts, heard, matches, matchesLoaded, reloadMatches, followMatch, setMatchAlerts, teams, followTeam, setTeamAlerts])

  return <FollowingContext value={value}>{children}</FollowingContext>
}

export function useFollowing(): Following {
  const value = use(FollowingContext)
  if (!value) throw new Error('useFollowing outside FollowingProvider')
  return value
}

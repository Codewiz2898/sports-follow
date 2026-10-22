import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, subscribe, type AlertLevel, type Card, type FollowedMatch, type FollowedTeam, type FollowTarget, type PlayerMoment, type StreamEvent } from './api'
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
  heard: number  // moments the stream has brought at the fan's level, and coming back to the app: the activity list and This week look again on it
  listen: (onEvent: (e: StreamEvent) => void) => () => void  // every event the fan stream brings, for a followed player's page
  // The games the fan follows by themselves (sports_follow/matches.py), each board as last polled, and whether
  // the list has come; following one, or stopping, by its address after /match/; its level.
  matches: FollowedMatch[]
  matchesLoaded: boolean
  followMatch: (address: string, on: boolean, eventId?: number | null) => Promise<void>  // stopped by its event where known
  setMatchAlerts: (eventId: number, level: AlertLevel) => Promise<void>
  // The teams the fan follows (sports_follow/teams.py), each with its next game, last result and game on now;
  // following one or stopping, and its level.
  teams: FollowedTeam[]
  followTeam: (teamId: number, on: boolean) => Promise<void>
  isFollowingTeam: (teamId: number) => boolean
  setTeamAlerts: (teamId: number, level: AlertLevel) => Promise<void>
}

const Ctx = createContext<FollowingState | null>(null)
const MAX_PROGRESS = 8
const TOAST_MS = 8000
const RETRY_MS = 2000
const RETRY_MAX_MS = 60_000
const SAME_MS = 10_000  // a game's own moment this soon after one of the fan's players' of its kind in the game is that one
const AWAY_MS = 30_000  // away from the app this long (a phone that slept): what came meanwhile is looked for on coming back

/** A team's tag, as a game's own moment told from its side names it (PlayerMoment.sides). */
const teamTag = (teamId: number) => `team:${teamId}`

/** The level a game's own moment reaches the fan at, as alerts.plan has it for a push: the game's, followed by
 * itself, else the loudest of their teams' in it (the moment says which teams fans follow in it); null where they
 * follow neither, for a result told from a side that isn't theirs (or one they set to off), and for the game's own
 * result where their team's side tells it too: theirs comes instead. */
export function gameLevel(m: PlayerMoment, matches: FollowedMatch[], teams: FollowedTeam[]): AlertLevel | null {
  const heard = new Set(teams.filter((t) => t.alerts !== 'off').map((t) => teamTag(t.team_id)))
  if (m.sides?.length && !m.sides.some((s) => heard.has(s))) return null
  if (m.told_by?.some((s) => heard.has(s))) return null
  const game = matches.find((g) => g.event_id === m.event_id)?.alerts
  if (game) return game  // the game's own follow sets its level, as notify._plan's does
  const order: AlertLevel[] = ['off', 'results', 'key', 'everything']
  const levels = teams.filter((t) => m.teams?.includes(teamTag(t.team_id))).map((t) => t.alerts)
  return levels.length ? levels.reduce((a, b) => (order.indexOf(b) > order.indexOf(a) ? b : a)) : null
}

// A team's games this soon are on the fan stream: the server's 36 h (sports_follow/teams.event_ids), less an hour
// here, so a clock running fast never connects again before the server counts the game.
const STREAM_AHEAD_MS = 35 * 3600_000
const RESULT_AFTER_MS = 90_000  // a team's game over: its result is in Following by the server's next tick or two

/** The games of the fan's teams the stream carries now: each team's next game (its game on now, while it's on)
 * from a day and a half before it starts. A game ending changes nothing here, so its result still comes on the
 * stream it's on; the list moves on when Following next loads (the stream's next connect, a follow). */
export function streamedGames(teams: FollowedTeam[], now: number): string[] {
  return teams.map((t) => t.next).filter((n) => n?.match && (n.status === 'live' || n.status === 'armed' || (Date.parse(n.start_utc ?? '') - now < STREAM_AHEAD_MS))).map((n) => n!.match!).sort()
}

/** When the soonest of the fan's teams' next games comes within the stream's reach, or null: then it connects again. */
export function nextInReach(teams: FollowedTeam[], now: number): number | null {
  const at = teams.map((t) => Date.parse(t.next?.start_utc ?? '') - STREAM_AHEAD_MS).filter((ms) => Number.isFinite(ms) && ms > now)
  return at.length ? Math.min(...at) : null
}

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
  const [matches, setMatches] = useState<FollowedMatch[]>([])
  const [matchesLoaded, setMatchesLoaded] = useState(false)
  const [teams, setTeams] = useState<FollowedTeam[]>([])
  const idsKey = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  const gamesKey = matches.map((m) => m.event_id).sort((a, b) => a - b).join(',')
  // A followed team's games are on the stream from a day and a half before (teams.event_ids): a follow, or one of
  // its games coming within reach (a timer for the soonest), connects it again; a game ending doesn't.
  const [reach, setReach] = useState(0)
  // `reach` is the timer's tick: the games in reach are worked out again by the clock then.
  const teamsKey = useMemo(() => (teams.length ? `${teams.map((t) => t.team_id).sort((a, b) => a - b).join(',')}|${streamedGames(teams, Date.now()).join(',')}|${reach}` : ''), [teams, reach])
  useEffect(() => {
    const at = nextInReach(teams, Date.now())
    if (at == null) return
    const timer = window.setTimeout(() => setReach((n) => n + 1), Math.min(at - Date.now() + 1000, 2 ** 31 - 1))
    return () => window.clearTimeout(timer)
  }, [teams, reach])
  const cardsRef = useRef(cards)
  cardsRef.current = cards
  const matchesRef = useRef(matches)
  matchesRef.current = matches
  const teamsRef = useRef(teams)
  teamsRef.current = teams
  const ended = useRef(new Set<number>())  // the team games whose end has asked for Following again
  const toasted = useRef<{ event: number | null; kind: string; at: number }[]>([])
  const loadedRef = useRef(loaded)
  loadedRef.current = loaded
  const listeners = useRef(new Set<(e: StreamEvent) => void>())
  const alertWrites = useRef(new Map<number, { latest: number; saved: AlertLevel; queue: Promise<void> }>())

  const reload = useCallback(() => api.following().then((r) => {
    setCards((prev) => withLocal(r.players, prev))
    setTeams(r.teams ?? [])
    setLoaded(true)
    setFailed(false)
  }, () => setFailed(true)), [])

  useEffect(() => { reload() }, [reload])
  const reloadRef = useRef(reload)
  reloadRef.current = reload

  const reloadMatches = useCallback(() => api.matches().then((r) => setMatches(r.matches), () => {}).finally(() => setMatchesLoaded(true)), [])
  useEffect(() => { reloadMatches() }, [reloadMatches])

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

  // Back in the app after a while: what came while it was away may be in the activity list. Not on the stream's
  // reconnect, which every open app makes at once when the server restarts (BUG-265).
  useEffect(() => {
    let away = document.visibilityState === 'hidden' ? Date.now() : null
    const seen = () => {
      if (document.visibilityState === 'hidden') { away ??= Date.now(); return }
      if (away != null && Date.now() - away >= AWAY_MS) setHeard((n) => n + 1)
      away = null
    }
    document.addEventListener('visibilitychange', seen)
    return () => document.removeEventListener('visibilitychange', seen)
  }, [])

  const apply = useCallback((e: StreamEvent) => {
    if (e.type === 'following') {
      // First on every (re)connect: each card as it is now, so a score or result published while the
      // stream was down (a locked phone, a restart) isn't missed. Who the fan follows, and their alert
      // levels, stay as this app has them, unless the list never loaded (a player was followed since):
      // then this is the list.
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
      if (e.matches) { setMatches(e.matches); setMatchesLoaded(true) }
      // Each team's games and results as they are now; its level as this app has it.
      if (e.teams) {
        const theirs = new Map(e.teams.map((t) => [t.team_id, t]))
        setTeams((prev) => (loadedRef.current ? prev.map((t) => { const f = theirs.get(t.team_id); return f ? { ...t, ...f, alerts: t.alerts } : t }) : e.teams ?? prev))
      }
    } else if (e.type === 'match') {
      // A followed game's board and where it's at, as its poll found it; the rest stays as the list has it.
      setMatches((prev) => prev.map((m) => (m.event_id === e.event_id ? { ...m, live: e.match.live, status: e.match.status } : m)))
      // And a followed team's, on now or about to be: its board, until it's over. Then its result reaches Following
      // once the server has read the team's games again (teams.ended: the next tick), so the list loads again then.
      const ofTeam = (t: FollowedTeam) => t.live?.event_id === e.event_id || (t.next?.match != null && t.next.match === e.match.match)
      setTeams((prev) => prev.map((t) => (ofTeam(t) ? { ...t, live: e.match.status === 'live' || e.match.status === 'armed' ? e.match.live : null } : t)))
      if (e.match.status === 'final' && teamsRef.current.some(ofTeam) && !ended.current.has(e.event_id)) {
        ended.current.add(e.event_id)
        window.setTimeout(() => { void reloadRef.current() }, RESULT_AFTER_MS)
      }
    } else if (e.type === 'card') {
      setCards((prev) => prev.map((c) => (c.player_id === e.player_id && e.card.version >= c.version ? { ...c, ...e.card } : c)))
      if (e.card.status !== 'building') setProgress((p) => ({ ...p, [e.player_id]: [] }))
    } else if (e.type === 'moment') {
      // With the app in front the service worker skips the system notification, so the toast
      // follows the same level the push does: the player's, or the game's for a game's own moment.
      const m = e.moment
      const level = m.player_id != null ? cardsRef.current.find((c) => c.player_id === m.player_id)?.alerts ?? 'key' : gameLevel(m, matchesRef.current, teamsRef.current)
      if (level == null || !wanted(level, m)) return
      // One the fan hears about: the activity list looks again (one below their level waits for its next look).
      setHeard((n) => n + 1)
      // A board's moment its other side tells, where the fan follows that player too: theirs shows, as the push does.
      const voice = m.voice != null ? cardsRef.current.find((c) => c.player_id === m.voice) : undefined
      if (voice && wanted(voice.alerts ?? 'key', m)) return
      // A result told from both sides the fan follows shows once.
      const sideFinal = m.kind === 'final' && Boolean(m.sides?.length)
      if (sideFinal && toasted.current.some((t) => t.event === (m.event_id ?? null) && t.kind === 'final:side')) return
      if (sideFinal) toasted.current.push({ event: m.event_id ?? null, kind: 'final:side', at: Date.now() })
      // One the fan's player's said a moment ago (their goal, the result) shows once, as theirs: the push does too.
      const now = Date.now()
      toasted.current = toasted.current.filter((t) => now - t.at < SAME_MS)
      if (m.player_id == null && toasted.current.some((t) => t.event === (m.event_id ?? null) && t.kind === m.kind)) return
      if (m.player_id != null) toasted.current.push({ event: m.event_id ?? null, kind: m.kind, at: now })
      setMoments((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m].slice(-3)))
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

  // The stream's channels are the fan's players, followed games and teams' games as it connects: a change reconnects it.
  useEffect(() => {
    if (!idsKey && !gamesKey && !teamsKey) return
    return subscribe('/api/me/stream', apply)
  }, [idsKey, gamesKey, teamsKey, apply])

  const followMatch = useCallback(async (address: string, on: boolean, eventId?: number | null) => {
    const at = address.indexOf('/')
    // Stopping goes by the event the follow is on where that's known: it may be the game's other address.
    if (!on && eventId != null) await api.unfollowMatch(eventId)
    else await api.followMatch(address.slice(0, at), address.slice(at + 1), on)
    await reloadMatches()
  }, [reloadMatches])

  const setMatchAlerts = useCallback(async (eventId: number, level: AlertLevel) => {
    const was = matchesRef.current.find((m) => m.event_id === eventId)?.alerts ?? 'key'
    const show = (value: AlertLevel) => setMatches((prev) => prev.map((m) => (m.event_id === eventId ? { ...m, alerts: value } : m)))
    show(level)
    try { await api.setMatchAlerts(eventId, level) } catch (e) { show(was); throw e }
  }, [])

  const followTeam = useCallback(async (teamId: number, on: boolean) => {
    await api.followTeam(teamId, on)
    if (!on) setTeams((prev) => prev.filter((t) => t.team_id !== teamId))
    await reload()
  }, [reload])

  const setTeamAlerts = useCallback(async (teamId: number, level: AlertLevel) => {
    const was = teamsRef.current.find((t) => t.team_id === teamId)?.alerts ?? 'key'
    const show = (value: AlertLevel) => setTeams((prev) => prev.map((t) => (t.team_id === teamId ? { ...t, alerts: value } : t)))
    show(level)
    try { await api.setTeamAlerts(teamId, level) } catch (e) { show(was); throw e }
  }, [])

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
    matches,
    matchesLoaded,
    followMatch,
    setMatchAlerts,
    teams,
    followTeam,
    isFollowingTeam: (id) => teamsRef.current.some((t) => t.team_id === id),
    setTeamAlerts,
  }), [cards, loaded, failed, reload, progress, follow, unfollow, setAlerts, moments, dismissMoment, heard, listen, matches, matchesLoaded, followMatch, setMatchAlerts, teams, followTeam, setTeamAlerts])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useFollowing(): FollowingState {
  const value = useContext(Ctx)
  if (!value) throw new Error('useFollowing outside FollowingProvider')
  return value
}

export { tileOrder } from './today'

// What a game's match page decides (GET /api/match/<source>/<game id>, sports_follow/match.py): when to look
// again, what its head shows (the board once it's under way, else when it starts), and what it offers about
// following the game by itself (sports_follow/matches.py). Shared by the web app (pages/MatchPage.tsx) and
// the Android app (mobile/src/app/match/), so it touches nothing of a browser's.

import { dateOnly, parseDate } from './format'
import { comingCard } from './lines'
import type { FollowedMatch, LiveStatus, Match, MatchFollowState, MatchStream } from './types'

// A look again: a followed player's game is polled every few seconds, any other read once a minute. A look only
// reads what the server has, which asks the game's source at most once a minute whoever looks.
export const EVERY = 30_000
const SOON = 5_000  // when the server says a read is on its way, the next look
const TRIES = 3  // and how many such looks before waiting the usual time again

/** After a look whose answer said `refreshing` (a newer read on its way) or not: how long until the next, and
 * how many quick looks that one will have been. */
export function nextLook(refreshing: boolean, tries: number): { wait: number; tries: number } {
  return { wait: refreshing && tries < TRIES ? SOON : EVERY, tries: refreshing ? tries + 1 : 0 }
}

/** A game's board means something once it's under way: before, the head says when it starts instead. */
const UNDER_WAY = ['live', 'final', 'armed']

/** The board the head draws: the game's live block once it's under way, else none. */
export function boardOf(match: Pick<Match, 'live' | 'status'>): LiveStatus | null {
  return match.live && UNDER_WAY.includes(match.status) ? match.live : null
}

/** Who won, big over a finished game's board, as its sport says it ("Arsenal won" over 2-1 FT): "" while it's on,
 * and where the board's line under the score says it already (a cricket match's summary is both), which a phone
 * showed again, four lines tall (BUG-088). */
export function pageResult(match: Pick<Match, 'status' | 'verdict' | 'live'>): string {
  const verdict = match.status === 'final' ? match.verdict ?? '' : ''
  return verdict && verdict !== boardOf(match)?.clock ? verdict : ''
}

/** A fan's player's result in the game, as Your players lists it: theirs ("Won 2-1"), not the game's said again
 * (the board's line, or who won over it). */
export function ownResult(result: string | null | undefined, match: Pick<Match, 'status' | 'verdict' | 'live'>): string | null {
  return result && result !== match.verdict && result !== boardOf(match)?.clock ? result : null
}

const LATE = 6 * 36e5  // how long after its start a game that hasn't begun is still "starting soon"

/** When a game to come (or about to start, armed, with no board yet) starts, under its title: its countdown
 * within a day, else its day, as Coming up's cards say it; "Starting soon" once its start has passed without
 * it (a delayed start), for a few hours; else nothing. Nothing once it has a board, or is over or off. */
export function startsIn(match: Match, now: number): string | null {
  if (boardOf(match) || (match.status !== 'scheduled' && match.status !== 'armed')) return null
  const at = parseDate(match.start_utc)?.getTime()
  if (at == null) return null
  // A day with no time yet: its day, whatever the hour.
  if (at > now || dateOnly(match.start_utc)) return comingCard({ title: match.title ?? '', competition: '', start_utc: match.start_utc }, now).big
  return now - at < LATE ? 'Starting soon' : null
}

const WATCH_BEFORE = 30 * 6e4  // how long before its start a game's streams are offered: the build-up is on by then

/** Where to watch the game (its streams, best first), while it's on or about to start: from WATCH_BEFORE before
 * its start, and after it while it hasn't begun (a delayed start), for a few hours. None once it's over or off,
 * or for a game whose time isn't known. */
export function watchable(match: Match, now: number): MatchStream[] {
  const streams = match.streams ?? []
  if (match.status === 'live' || !streams.length) return streams
  if (match.status !== 'scheduled' && match.status !== 'armed') return []
  const at = parseDate(match.start_utc)?.getTime()
  if (at == null || dateOnly(match.start_utc)) return []
  return at - now <= WATCH_BEFORE && now - at < LATE ? streams : []
}

/** What a stream is, after its platform: its language, and whether it's the organiser's ("English · Official"). */
export function streamNote(stream: MatchStream): string {
  return [stream.language_name, stream.official ? 'Official' : ''].filter(Boolean).join(' · ')
}

/** A key event the source names in a game none of the fan's players' boards draw (the page's `events`). */
export interface KeyEvent { clock: string; kind: string; text: string }

export function keyEvents(match: Match): KeyEvent[] {
  return (match.page.events ?? []) as KeyEvent[]
}

/** The game among those the fan follows by themselves: by any of its events, at any of its addresses (a chess
 * board's other one), or by the event the follow is on. */
export function followedOf(match: Match, matches: FollowedMatch[], state: MatchFollowState | null): FollowedMatch | undefined {
  return matches.find((m) => match.event_ids.includes(m.event_id) || (m.addresses ?? []).includes(match.match) || m.event_id === state?.event_id)
}

/**
 * What the head offers about following the game by itself: the Follow button (once followed, or where its
 * moments can reach the fan), "No live alerts" where they can't and it isn't over (no promise of alerts its
 * source can't send), and the note under the band: why its alerts are paused for now, or can't come.
 */
export function followHead(match: Match, state: MatchFollowState | null): { button: boolean; noAlerts: boolean; note: string | null } {
  if (!state) return { button: false, noAlerts: false, note: null }
  const over = match.status === 'final'
  const said = (state.following ? state.held : !state.alerts) && !over ? state.note : null
  return { button: state.following || state.alerts, noAlerts: !state.following && !state.alerts && !over, note: said ?? null }
}

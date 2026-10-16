// What Today shows, worked out from the fan's players: their live games, results, the games coming up, the
// order of their pills, and how each piece says when. Shared by the web app (pages/TodayPage.tsx and its
// components) and the Android app (mobile/), so it touches nothing of a browser's. A sport is passed by
// its key (each app finds it its own way).

import { ahead, countdown, dateOnly, daysAgo, day, lineStats, parseDate, playedOn, shortWhen } from './format'
import { highlight, type GameResult } from './lines'
import { drawable } from './sports/facts'
import type { ActivityItem, Card, LeagueGame, LiveStatus, Starter, UpcomingEvent } from './types'

export const FEW = 3  // until a fan follows this many, Today keeps offering starters
export const AHEAD_DAYS = 14  // how far Coming up looks
export const TAIL = 6  // league games in a rail after the fan's own, before its "All N" card

/** Group live cards by the game they're in, so two followed players in one match show as one game. Keyed by
 * the game, which also turns its line art its own way. */
export function liveGames(cards: Card[]): [string, Card[]][] {
  const games = new Map<string, Card[]>()
  for (const c of cards.filter((c) => c.status === 'ready' && c.live.is_live)) {
    // A structured source names the event; the agent's text only matches when it's written the same way.
    const key = c.live.event_id != null ? `event:${c.live.event_id}` : (c.live.event || c.live.score || String(c.player_id)).toLowerCase()
    games.set(key, [...(games.get(key) ?? []), c])
  }
  return [...games.entries()]
}

/** The newest moment of a game on Today's board ("Goal: Saka · 2–1 · 52'"), and whether it came after the
 * fan last looked at their alerts. */
export interface LatestMoment { text: string; missed: boolean }

/** The newest moment of the fan's players in a live game (the alerts list is newest first), and whether the
 * fan missed it: it came after they last looked at their alerts, from a game they haven't muted. Only a
 * structured score names its game. */
export function latestIn(items: ActivityItem[], group: Card[], seen: number): LatestMoment | null {
  const game = group[0].live.event_id
  if (game == null) return null
  const ids = new Set(group.map((c) => c.player_id))
  const m = items.find((i) => i.event_id === game && ids.has(i.player_id))
  return m ? { text: [m.title, m.body].filter(Boolean).join(' · '), missed: m.id > seen && !m.muted } : null
}

export interface Coming { key: string; event: UpcomingEvent; players: Card[] }

/** Every game of the fan's players in the next two weeks, soonest first, each once with everyone of theirs
 * who plays in it. One that has started has left: it's live, or it's a result. */
export function comingUp(cards: Card[], now: number): Coming[] {
  const merged = new Map<string, Coming>()
  for (const c of cards) {
    for (const u of c.upcoming ?? []) {
      const start = parseDate(u.start_utc)
      if (!start || !ahead(u.start_utc, now) || start.getTime() - now > AHEAD_DAYS * 864e5) continue
      const key = u.event_id != null ? `event:${u.event_id}` : `${u.title.toLowerCase()}|${start.toDateString()}`
      const found = merged.get(key)
      if (!found) merged.set(key, { key, event: u, players: [c] })
      else if (!found.players.includes(c)) found.players.push(c)
    }
  }
  return [...merged.values()].sort((a, b) => parseDate(a.event.start_utc)!.getTime() - parseDate(b.event.start_utc)!.getTime())
}

/**
 * The fan's results for the rail: every game of their players since yesterday where the fan is, else the
 * latest day that had any (`day`, its date; null since yesterday). A game two of their players were in is one card. The biggest
 * lines go first (a triple-double or a hundred, then any game with the player's numbers), then the newest.
 * Each card carries all its results (the follow list sends them), and the stream updates it as a game ends.
 */
export function results(cards: Card[], now: number, keyOf: (sport?: string | null) => string): { games: GameResult[]; day: string | null } | null {
  // How big a player's line is: the sport has a word for it (a triple-double, a hundred), it's numbers, or neither.
  const weight = (p: GameResult['players'][number]) => {
    const line = p.result.player_contribution
    return highlight(keyOf(p.sport), line) ? 2 : lineStats(line) ? 1 : 0
  }
  const today = new Date(now)
  const all = cards.flatMap((c) => (c.recent_results ?? []).flatMap((r) => {
    // A game dated after today (an agent's slip) isn't a result yet.
    const days = daysAgo(playedOn(r), today)
    return days != null && days >= 0 ? [{ days, player: { result: { ...r, player_id: c.player_id, player_name: c.player.name }, sport: c.player.sport, teams: c.player.teams } }] : []
  }))
  if (!all.length) return null
  const latest = Math.min(...all.map((x) => x.days))
  const shown = latest <= 1 ? all.filter((x) => x.days <= 1) : all.filter((x) => x.days === latest)
  const games = new Map<string, GameResult>()
  for (const { player } of shown) {
    const r = player.result
    const key = r.event_id != null ? `event:${r.event_id}` : `${r.title.toLowerCase()}|${parseDate(playedOn(r))!.toDateString()}`
    const game = games.get(key)
    if (!game) games.set(key, { key, players: [player] })
    else if (!game.players.some((p) => p.result.player_id === r.player_id)) game.players.push(player)
  }
  const time = (g: GameResult) => Math.max(...g.players.map((p) => parseDate(playedOn(p.result))?.getTime() ?? 0))
  for (const g of games.values()) g.players.sort((a, b) => weight(b) - weight(a))
  const ranked = [...games.values()].sort((a, b) => weight(b.players[0]) - weight(a.players[0]) || time(b) - time(a))
  const day = latest <= 1 ? null : parseDate(playedOn(shown[0].player.result))!.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  return { games: ranked, day }
}

/** The fan's players as Today's strip and the Following page show them: live first, then whoever plays
 * soonest, then the rest in the order they were followed. */
export function tileOrder(cards: Card[], now: number): Card[] {
  const nextAt = (c: Card) => Math.min(Infinity, ...(c.upcoming ?? []).filter((u) => ahead(u.start_utc, now)).map((u) => parseDate(u.start_utc)!.getTime()))
  return [...cards].sort((a, b) => Number(b.live.is_live) - Number(a.live.is_live) || nextAt(a) - nextAt(b))
}

/** A live player's game in a few characters, as their sport says it ("1–0 · 67'"), else their own line
 * ("1 goal"); nothing when neither is known (the dot says they're live). */
export function liveShort(live: LiveStatus): string {
  const facts = drawable(live)
  return (facts && live.state ? facts.short?.(live.state as never, live) : null) || live.headline || ''
}

/** When a player's next game is, in a pill: the time if it's today ("21:00"), the day within the week
 * ("Sat"), else the date ("12 Nov"). Nothing when no game is known. */
export function nextWhen(card: Card, now: number): string {
  const starts = (card.upcoming ?? []).map((u) => u.start_utc).filter((s): s is string => ahead(s, now))
  if (!starts.length) return ''
  const at = (s: string) => parseDate(s)!.getTime()
  const next = starts.reduce((a, b) => (at(b) < at(a) ? b : a))
  const d = parseDate(next)!
  if (d.toDateString() === new Date(now).toDateString()) return dateOnly(next) ? 'Today' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (d.getTime() - now < 6 * 864e5) return d.toLocaleDateString(undefined, { weekday: 'short' })
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** Why a starter is offered, as the top of their tile says it. */
export function starterReason(r: Starter): string {
  const start = r.next?.start_utc
  switch (r.why) {
    case 'live': return r.next?.live ? `Live · ${r.next.live}` : 'Live now'
    case 'started': return `Started ${shortWhen(start)}`
    case 'soon': return `Plays ${shortWhen(start)}`
    case 'followed': return `Followed by ${r.followers}`
    default: return start ? `Next game ${shortWhen(start)}` : r.why === 'pinned' ? 'Our pick' : 'Popular'
  }
}

/** When a big moment of this week was: "Today", the weekday within the week ("Mon"), else the date. */
export function posterWhen(at: string, now: number): string {
  const days = daysAgo(at, new Date(now))
  if (days === 0) return 'Today'
  return days != null && days > 0 && days < 7 ? parseDate(at)!.toLocaleDateString(undefined, { weekday: 'short' }) : day(at)
}

/** The longest word in a poster's word, in letters: the poster sizes the word so that one fits on a line. */
export function longestWord(word: string): number {
  return Math.max(...word.split(/(?<=-)|\s+/).map((w) => w.length))
}

/** When a Wire row is from: the time today ("07:40"), the weekday within the week ("Wed"), else the date. */
export function wireWhen(at: string, now: number): string {
  const d = parseDate(at)
  const days = daysAgo(at, new Date(now))
  if (!d || days == null) return ''
  if (days <= 0) return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return days < 7 ? d.toLocaleDateString(undefined, { weekday: 'short' }) : day(at)
}

// ---------------------------------------------------------------- the big games (GET /api/league)

/** The events the fan's players are in: a league game that's one of them is theirs, and shows once, as theirs. */
export function theirEvents(cards: Card[]): Set<number> {
  const ids = cards.flatMap((c) => [
    ...(c.recent_results ?? []).map((r) => r.event_id),
    ...(c.upcoming ?? []).map((u) => u.event_id),
    c.live.event_id,
  ])
  return new Set(ids.filter((id): id is number => id != null))
}

/** The league games none of the fan's players are in (by event, never by name). */
export function notTheirs(games: LeagueGame[], events: Set<number>): LeagueGame[] {
  return games.filter((g) => !g.event_ids.some((id) => events.has(id)))
}

/** The big games still to come: one that has started since the server's answer is live, or over. */
export function stillToCome(games: LeagueGame[], now: number): LeagueGame[] {
  return games.filter((g) => ahead(g.start_utc, now))
}

/** Coming up's big games, after the fan's own (all of it for a fan who follows nobody): none their players
 * are in, none that has started. The server lists them biggest first, the sports taking turns (league.py),
 * so the first TAIL, the ones the rail shows, are the biggest of every sport: they go soonest first, and
 * the rest after them, soonest first. */
export function leagueComingUp(games: LeagueGame[], theirs: Set<number>, now: number): LeagueGame[] {
  const left = stillToCome(notTheirs(games, theirs), now)
  const at = (g: LeagueGame) => parseDate(g.start_utc)?.getTime() ?? Infinity
  const soonest = (list: LeagueGame[]) => [...list].sort((a, b) => at(a) - at(b))
  return [...soonest(left.slice(0, TAIL)), ...soonest(left.slice(TAIL))]
}

/** "Since yesterday", or the day the latest results are from ("Tue 13 Oct"). */
export function resultsDay(day: string | null): string {
  return day ? parseDate(day)!.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : 'Since yesterday'
}

const letters = (text: string) => text.toLowerCase().replace(/[^a-z0-9]/g, '')

/** Under a big game: its stage and what makes it big, once ("Final" when the stage already says "Final"). */
export function leagueAbout(game: LeagueGame, ...first: string[]): string {
  const detail = game.detail ?? ''
  const why = game.why && !letters(detail).includes(letters(game.why)) ? game.why : ''
  return [...first, why, detail].filter(Boolean).join(' · ')
}

/** A big game to come: big, the countdown within a day, else the day; `when`, the time it starts. */
export function leagueComing(game: LeagueGame, now: number): { big: string; when: string } {
  const start = parseDate(game.start_utc)
  const left = start && start.getTime() - now < 864e5 ? countdown(game.start_utc, now) : ''
  const big = left || (start && start.getTime() - now < 6 * 864e5 ? start.toLocaleDateString(undefined, { weekday: 'short' }) : start?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })) || 'TBC'
  const when = left ? shortWhen(game.start_utc, now) : start && !dateOnly(game.start_utc) ? start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''
  return { big, when }
}

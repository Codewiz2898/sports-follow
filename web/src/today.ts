// What Today shows, worked out from the fan's players and teams: their live games, results, the games coming up,
// the order of their pills, and how each piece says when; and which of the games on now are theirs. Shared by the web app (pages/TodayPage.tsx and its
// components) and the Android app (mobile/), so it touches nothing of a browser's. A sport is passed by
// its key (each app finds it its own way).

import { ahead, countdown, dateOnly, daysAgo, day, lineStats, parseDate, playedOn, shortName, shortWhen } from './format'
import { highlight, type GameResult } from './lines'
import { fold } from './search'
import { drawable } from './sports/facts'
import type { ActivityItem, Card, FollowedMatch, FollowedTeam, LeagueGame, LiveGame, LiveSport, LiveStatus, Starter, UpcomingEvent, WireRow } from './types'

export const FEW = 3  // until a fan follows this many, Today keeps offering starters
export const AHEAD_DAYS = 14  // how far Coming up looks
export const TAIL = 6  // league games in a rail after the fan's own, before its "All N" card

/** The game a live card or a result is of, by what the server says: the game its fixture is one of, where a game
 * has several (a chess board is one of each player's), else its event; null where it names neither. */
export function gameOf(x: { game_key?: string | null; event_id?: number | null }): string | null {
  return x.game_key ? `game:${x.game_key}` : x.event_id != null ? `event:${x.event_id}` : null
}

/** Group live cards by the game they're in, so two followed players in one match show as one game, and two
 * playing each other at chess one board. Keyed by the game, which also turns its line art its own way. */
export function liveGames(cards: Card[]): [string, Card[]][] {
  const games = new Map<string, Card[]>()
  for (const c of cards.filter((c) => c.status === 'ready' && c.live.is_live)) {
    // A structured source names the game; the agent's text only matches when it's written the same way.
    const key = gameOf(c.live) ?? (c.live.event || c.live.score || String(c.player_id)).toLowerCase()
    games.set(key, [...(games.get(key) ?? []), c])
  }
  return [...games.entries()]
}

/** The newest moment of a game on Today's board ("Goal: Saka · 2–1 · 52'"), and whether it came after the
 * fan last looked at their alerts. */
export interface LatestMoment { text: string; missed: boolean }

/** The newest moment of the fan's players in a live game (the alerts list is newest first), and whether the
 * fan missed it: it came after they last looked at their alerts, from a game they haven't muted. Only a
 * structured score names its game, under each player's event of it (a chess board is one of each player's). */
export function latestIn(items: ActivityItem[], group: Card[], seen: number): LatestMoment | null {
  const events = new Set(group.flatMap((c) => (c.live.event_id != null ? [c.live.event_id] : [])))
  if (!events.size) return null
  const ids = new Set(group.map((c) => c.player_id))
  const m = items.find((i) => i.event_id != null && events.has(i.event_id) && i.player_id != null && ids.has(i.player_id))
  return m ? { text: [m.title, m.body].filter(Boolean).join(' · '), missed: m.id > seen && !m.muted } : null
}

/** A game coming up: its fan's players in it, the teams of theirs that play it (by name), and its sport where
 * none of their players is (a game followed by itself, or a team's). */
export interface Coming { key: string; event: UpcomingEvent; players: Card[]; sport?: string; teams?: string[] }

/** Every game of the fan's players and teams in the next two weeks, soonest first, each once with everyone of
 * theirs who plays in it (a chess board two of their players are on too, a fixture of each player's), and the
 * games they follow by themselves (`matches`) that none of those is in. One that has started has left: it's live,
 * or it's a result. */
export function comingUp(cards: Card[], now: number, matches: FollowedMatch[] = [], teams: FollowedTeam[] = []): Coming[] {
  const soon = (s: string | null | undefined) => {
    const start = parseDate(s)
    return start && ahead(s, now) && start.getTime() - now <= AHEAD_DAYS * 864e5 ? start : null
  }
  const merged = new Map<string, Coming>()
  // Each game by every event it's under (a chess board's two): a team's game, or a game followed by itself, names one.
  // And its players' match pages: a board followed by itself may be at the other player's until the tick moves it.
  const events = new Map<number, Coming>()
  const addresses = new Set<string>()
  for (const c of cards) {
    for (const u of c.upcoming ?? []) {
      const start = soon(u.start_utc)
      if (!start) continue
      const key = gameOf(u) ?? `${u.title.toLowerCase()}|${start.toDateString()}`
      let game = merged.get(key)
      if (!game) merged.set(key, (game = { key, event: u, players: [] }))
      if (!game.players.includes(c)) game.players.push(c)
      if (u.event_id != null) events.set(u.event_id, game)
      if (u.match) addresses.add(u.match)
    }
  }
  for (const t of teams) {
    for (const u of t.upcoming ?? []) {
      const start = soon(u.start_utc)
      if (!start || u.event_id == null) continue
      const found = events.get(u.event_id)
      if (found) {
        if (!(found.teams ?? []).includes(t.name)) found.teams = [...(found.teams ?? []), t.name]
        continue
      }
      const game: Coming = { key: `event:${u.event_id}`, event: u, players: [], sport: t.sport, teams: [t.name] }
      merged.set(game.key, game)
      events.set(u.event_id, game)
    }
  }
  for (const m of matches) {
    if (events.has(m.event_id) || (m.addresses ?? []).some((a) => addresses.has(a)) || !soon(m.start_utc) || !['scheduled', 'armed'].includes(m.status)) continue
    const key = `event:${m.event_id}`
    merged.set(key, { key, event: { title: m.title, competition: m.competition, start_utc: m.start_utc, status: m.status, event_id: m.event_id, match: m.match }, players: [], sport: m.sport })
  }
  return [...merged.values()].sort((a, b) => parseDate(a.event.start_utc)!.getTime() - parseDate(b.event.start_utc)!.getTime())
}

/** The games the fan follows by themselves that are on now, none their players are in (those are on Today as
 * their players' games already: by event, or at any of the game's addresses, a chess board's other one), in the
 * order they were listed. */
export function followedLive(matches: FollowedMatch[], cards: Card[]): FollowedMatch[] {
  const live = cards.filter((c) => c.live.is_live)
  const events = new Set(live.flatMap((c) => (c.live.event_id != null ? [c.live.event_id] : [])))
  const addresses = new Set(live.flatMap((c) => (c.live.match ? [c.live.match] : [])))
  return matches.filter((m) => m.live?.is_live && !events.has(m.event_id) && !(m.addresses ?? []).some((a) => addresses.has(a)))
}

/** A team's game on now, on Today: its board, once, with the fan's teams in it by name. */
export interface TeamGame { key: string; live: LiveStatus; sport: string; names: string[]; team_id: number }

/** The games of the fan's teams that are on now, none their players are in or that they follow by themselves
 * (those are on Today as such already), each once with every team of theirs in it, in the order they follow them. */
export function teamsLive(teams: FollowedTeam[], cards: Card[], matches: FollowedMatch[]): TeamGame[] {
  const shown = new Set([
    ...cards.filter((c) => c.live.is_live).flatMap((c) => (c.live.event_id != null ? [c.live.event_id] : [])),
    ...matches.filter((m) => m.live?.is_live).map((m) => m.event_id),
  ])
  const games = new Map<number, TeamGame>()
  for (const t of teams) {
    const live = t.live
    if (!live?.is_live || live.event_id == null || shown.has(live.event_id)) continue
    const found = games.get(live.event_id)
    if (found) found.names.push(t.name)
    else games.set(live.event_id, { key: `event:${live.event_id}`, live, sport: t.sport, names: [t.name], team_id: t.team_id })
  }
  return [...games.values()]
}

/**
 * The fan's results for the rail: every game of their players since yesterday where the fan is, else the
 * latest day that had any (`day`, its date; null since yesterday). A game two of their players were in is one card (a
 * chess board too, a fixture of each player's). The biggest
 * lines go first (a triple-double or a hundred, then any game with the player's numbers), then the newest.
 * Each card carries all its results (the follow list sends them), and the stream updates it as a game ends.
 */
export function results(cards: Card[], now: number, keyOf: (sport?: string | null) => string, teams: FollowedTeam[] = []): { games: GameResult[]; day: string | null } | null {
  // How big a player's line is: the sport has a word for it (a triple-double, a hundred), it's numbers, or neither.
  const weight = (p: GameResult['players'][number]) => {
    const line = p.result.player_contribution
    return highlight(keyOf(p.sport), line) ? 2 : lineStats(line) ? 1 : 0
  }
  const today = new Date(now)
  const all = cards.flatMap((c) => (c.recent_results ?? []).flatMap((r) => {
    // A game dated after today (an agent's slip) isn't a result yet.
    const days = daysAgo(playedOn(r), today)
    return days != null && days >= 0 ? [{ days, player: { result: { ...r, player_id: c.player_id, player_name: c.player.name }, sport: c.player.sport, teams: c.player.teams } as GameResult['players'][number] }] : []
  }))
  // And each of their teams' last result, from its side ("Won 2–1"), the team standing in its card for a player.
  for (const t of teams) {
    const days = t.last ? daysAgo(playedOn(t.last), today) : null
    if (t.last && days != null && days >= 0) all.push({ days, player: { result: { ...t.last, player_id: 0, player_name: t.name }, sport: t.sport, teams: [t.name], team: t.team_id } })
  }
  if (!all.length) return null
  const latest = Math.min(...all.map((x) => x.days))
  const shown = latest <= 1 ? all.filter((x) => x.days <= 1) : all.filter((x) => x.days === latest)
  const games = new Map<string, GameResult>()
  for (const { player } of shown) {
    const r = player.result
    const key = gameOf(r) ?? `${r.title.toLowerCase()}|${parseDate(playedOn(r))!.toDateString()}`
    const game = games.get(key)
    const who = (p: GameResult['players'][number]) => (p.team != null ? `team:${p.team}` : `player:${p.result.player_id}`)
    if (!game) games.set(key, { key, players: [player] })
    else if (!game.players.some((p) => who(p) === who(player))) game.players.push(player)
  }
  const time = (g: GameResult) => Math.max(...g.players.map((p) => parseDate(playedOn(p.result))?.getTime() ?? 0))
  for (const g of games.values()) g.players.sort((a, b) => weight(b) - weight(a))
  const ranked = [...games.values()].sort((a, b) => weight(b.players[0]) - weight(a.players[0]) || time(b) - time(a))
  const day = latest <= 1 ? null : parseDate(playedOn(shown[0].player.result))!.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  return { games: ranked, day }
}

/** The fan's teams as Today's strip shows them, as its players: live first, then whichever plays soonest. */
export function teamOrder(teams: FollowedTeam[], now: number): FollowedTeam[] {
  const nextAt = (t: FollowedTeam) => Math.min(Infinity, ...(t.upcoming ?? []).filter((u) => ahead(u.start_utc, now)).map((u) => parseDate(u.start_utc)!.getTime()))
  return [...teams].sort((a, b) => Number(Boolean(b.live?.is_live)) - Number(Boolean(a.live?.is_live)) || nextAt(a) - nextAt(b))
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

/** When a team's next game is, in a pill, as a player's (nextWhen). */
export function teamNextWhen(team: FollowedTeam, now: number): string {
  const starts = (team.upcoming ?? []).map((u) => u.start_utc).filter((s): s is string => ahead(s, now))
  return starts.length ? whenIn(starts, now) : ''
}

/** When a player's next game is, in a pill: the time if it's today ("21:00"), the day within the week
 * ("Sat"), else the date ("12 Nov"). Nothing when no game is known. */
export function nextWhen(card: Card, now: number): string {
  const starts = (card.upcoming ?? []).map((u) => u.start_utc).filter((s): s is string => ahead(s, now))
  return starts.length ? whenIn(starts, now) : ''
}

/** The soonest of some starts, in a pill: the time if it's today, the day within the week, else the date. */
function whenIn(starts: string[], now: number): string {
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

/** A Wire row's line as it reads: with the player's name first ("Saka: …") when it doesn't say it (a story's
 * headline), however it writes its accents ("Jokic" names Jokić); a fact or a line-up always does. */
export function wireText(row: WireRow): string {
  const name = shortName(row.player)
  return fold(row.text).includes(fold(name)) ? row.text : `${name}: ${row.text}`
}

/** When a Wire row is from: the time today ("07:40"), the weekday within the week ("Wed"), else the date. */
export function wireWhen(at: string, now: number): string {
  const d = parseDate(at)
  const days = daysAgo(at, new Date(now))
  if (!d || days == null) return ''
  if (days <= 0) return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return days < 7 ? d.toLocaleDateString(undefined, { weekday: 'short' }) : day(at)
}

// ---------------------------------------------------------------- every game on now (GET /api/live)

/** A game on now with its sport's key, and the sport's name to call it by when it has no competition. */
export interface GameOn { game: LiveGame & { sport: string }; label: string }

/**
 * Every game the sources have on (GET /api/live), in the order the server ranks them, split into the fan's
 * and the rest. A game is the fan's when one of their live cards (`mine`) names one of its events, or a player
 * it offers is one of theirs who's live; never by a name. So a game shows once, as theirs.
 */
export function gamesOn(sports: LiveSport[], mine: Card[]): { theirs: GameOn[]; others: GameOn[] } {
  const events = new Set(mine.flatMap((c) => (c.live.event_id != null ? [c.live.event_id] : [])))
  const players = new Set(mine.map((c) => c.player_id))
  const isTheirs = (g: LiveGame) => (g.event_ids ?? []).some((id) => events.has(id)) || g.players.some((p) => p.player_id != null && players.has(p.player_id))
  const all = sports.flatMap((s) => s.games.map((g) => ({ game: { ...g, sport: s.key }, label: s.label })))
  return { theirs: all.filter((x) => isTheirs(x.game)), others: all.filter((x) => !isTheirs(x.game)) }
}

/** Games on now by sport, the sports in the order the server gives them; a sport with none isn't there. */
export function bySport(games: GameOn[]): { sport: string; label: string; games: GameOn[] }[] {
  const sports = new Map<string, { sport: string; label: string; games: GameOn[] }>()
  for (const x of games) {
    const found = sports.get(x.game.sport)
    if (found) found.games.push(x)
    else sports.set(x.game.sport, { sport: x.game.sport, label: x.label, games: [x] })
  }
  return [...sports.values()]
}

/** Today's way to the games on now that none of the fan's players are in: "12 other games on now" under the
 * fan's own live games (`theirsOn`), "12 games on now" with none of theirs on. */
export function moreOn(count: number, theirsOn: boolean): string {
  return `${count} ${theirsOn ? 'other ' : ''}${count === 1 ? 'game' : 'games'} on now`
}

// ---------------------------------------------------------------- the big games (GET /api/league)

/** The events the fan's players and teams are in: a league game that's one of them is theirs, and shows once, as theirs. */
export function theirEvents(cards: Card[], teams: FollowedTeam[] = []): Set<number> {
  const ids = [
    ...cards.flatMap((c) => [...(c.recent_results ?? []).map((r) => r.event_id), ...(c.upcoming ?? []).map((u) => u.event_id), c.live.event_id]),
    ...teams.flatMap((t) => [...(t.upcoming ?? []).map((u) => u.event_id), t.last?.event_id, t.live?.event_id]),
  ]
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

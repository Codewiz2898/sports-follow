// How the app words a player's game: the line on a tile, the numbers on a live board, the big word and lines
// of a result card, the countdown of a game to come. Shared by the web app (components/Blocks.tsx) and the
// Android app (mobile/), so it touches nothing of a browser's. A sport is passed by its key (each app finds
// it its own way: sports/catalog.ts, mobile/src/sports.tsx).

import { ahead, countdown, dateOnly, day, dayLabel, lineStats, parseDate, playedOn, resultOutcome, shortName, shortWhen } from './format'
import { factsOf, factsOfKind } from './sports/facts'
import type { Card, FollowedTeam, LatestResult, LiveStatus, NewsItem, Stat, UpcomingEvent } from './types'

export const WON = { W: 'Won', D: 'Drew', L: 'Lost' } as const

/** A line worth a word, as its sport words it: a triple-double, a hundred. */
export function highlight(sportKey: string, text?: string | null): string | null {
  return factsOf(sportKey)?.highlight?.(text) ?? null
}

/** What a player's tile says: live, then a game this week, then the last result, then a story of the last
 * FRESH_NEWS_DAYS (the tile shows it without its date); `sport`, the player's sport as the app writes it, when
 * there's nothing else. */
export function statusLine(card: Card, progress: string[] | undefined, now: number, sport: string): string {
  if (card.status === 'building') return progress?.at(-1) ?? 'Building page…'
  if (card.status === 'failed') return "Page couldn't be built, open to see why"
  if (card.live.is_live) {
    const lead = card.live.headline ?? (card.live.player_stats?.[0] && `${card.live.player_stats[0].value} ${card.live.player_stats[0].label}`)
    return [card.live.score, lead].filter(Boolean).join(' · ') || 'Live now'
  }
  const next = (card.upcoming ?? []).find((u) => ahead(u.start_utc, now))
  const nextAt = parseDate(next?.start_utc)?.getTime() ?? Infinity
  const last = card.recent_results?.[0]
  const lastAt = parseDate(playedOn(last))?.getTime() ?? 0
  if (next && nextAt - now < 7 * 864e5) return `${shortWhen(next.start_utc, now)} · ${next.title}`
  if (last && now - lastAt < 14 * 864e5) return last.result
  if (next) return `Next ${day(next.start_utc)} · ${next.title}`
  if (card.player.status === 'retired') return 'Retired · news only'
  return newsOf(card, now).find((n) => recent(n, now))?.headline ?? sport
}

/** How long a story is news on a player's tile, which shows it without its date (the server's week.NEWS_DAYS). */
const FRESH_NEWS_DAYS = 3

/** A card's news: the publishers' stories the curator served (the server keeps those current), else the research
 * agent's of the last FRESH_NEWS_DAYS: the agent writes a page's news only when it runs, and a page it doesn't run
 * for again keeps what it wrote then. */
export function newsOf(card: Pick<Card, 'news' | 'stories'>, now = Date.now()): NewsItem[] {
  return card.stories?.length ? card.stories : (card.news ?? []).filter((n) => recent(n, now))
}

/** Whether a story was published in the last FRESH_NEWS_DAYS; one with no date can't say it was. */
function recent(n: NewsItem, now: number): boolean {
  const at = parseDate(n.published)?.getTime()
  return at !== undefined && now - at <= FRESH_NEWS_DAYS * 864e5
}

/** The line over a ready page still being filled in: a page from a live source has its scores; one from
 * Wikipedia alone (no source) its profile. */
export function pendingLine(card: Pick<Card, 'pending' | 'provenance'>): string {
  const what = card.pending?.includes('news') ? 'news and background, about two minutes' : 'their background'
  return `${card.provenance ? 'Scores and fixtures are live. ' : ''}Still gathering ${what}.`
}

/** "5 November 1988"; a year alone as it is. */
export function bornOn(value: string): string {
  if (/^\d{4}$/.test(value)) return value
  const d = new Date(`${value}T12:00:00Z`)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
}

/** Who they are in a line, from Wikidata (sports_follow/wiki.py): born, country, at most four honours. */
export function profileFacts(p: Pick<Card['player'], 'born' | 'nationality' | 'honours'>): string[] {
  return [p.born && `Born ${bornOn(p.born)}`, p.nationality, ...(p.honours ?? []).slice(0, 4)].filter((f): f is string => Boolean(f))
}

/** What a followed team's tile says, as a player's does: its game on now, then a game this week, then its last
 * result from its side ("Won 2–1 v Chelsea"), then its next game, then what it plays in. */
export function teamLine(team: FollowedTeam, now: number): string {
  if (team.live?.is_live) return [team.live.score, team.live.clock].filter(Boolean).join(' · ') || 'Live now'
  const next = team.next && ahead(team.next.start_utc, now) ? team.next : null
  const nextAt = parseDate(next?.start_utc)?.getTime() ?? Infinity
  const last = team.last
  const lastAt = parseDate(last ? playedOn(last) : null)?.getTime() ?? 0
  const versus = (v?: string | null) => (v ? `v ${v}` : '')
  if (next && nextAt - now < 7 * 864e5) return `${shortWhen(next.start_utc, now)} · ${versus(next.versus) || next.title}`
  if (last && now - lastAt < 14 * 864e5) return [last.result, versus(last.versus)].filter(Boolean).join(' ')
  if (next) return `Next ${day(next.start_utc)} · ${versus(next.versus) || next.title}`
  return team.detail
}

/** A team tile's name size: the tile's 30 px, smaller as its longest word grows past what fits a phone's half
 * width, so a long name ("Mönchengladbach") is never broken inside a word. */
export function teamNameSize(name: string): number {
  const longest = Math.max(...name.split(/\s+/).map((w) => w.length))
  return longest <= 9 ? 30 : longest <= 12 ? 24 : 19
}

/** A stat in words: "3 shots", "1 key pass"; an abbreviation stays as it is ("22 PTS", "2 4s"), and a value
 * that isn't a number follows its label ("Colour Black"). */
export function statWords(s: Stat): string {
  if (!/^[\d.,½*/–-]/.test(s.value)) return `${s.label} ${s.value}`
  if (s.label === s.label.toUpperCase()) return `${s.value} ${s.label}`
  const label = s.label.toLowerCase()
  // One of them: "passes" → "pass", "shots" → "shot"; "4s" stays.
  const one = /(ss|sh|ch|x)es$/.test(label) ? label.slice(0, -2) : /[a-z]{2}(?<!s)s$/.test(label) ? label.slice(0, -1) : label
  return `${s.value} ${s.value === '1' ? one : label}`
}

/** The followed player's line on Today's board, in one row: their numbers (the headline first, "67* (71)",
 * without the stats it already says), or, with none yet, why (unless the sport has no player lines at all:
 * esports, where the team's result says it). */
export function liveLine(live: LiveStatus): { numbers: string[]; why: string | null } {
  const stats = (live.player_stats ?? []).filter((s) => !/^[–—-]*$/.test(s.value.trim()))  // a dash is no number yet
  const facts = factsOfKind(live.state?.kind)
  const said = new Set(facts?.inHeadline ?? [])
  const numbers = live.headline ? [live.headline, ...stats.filter((s) => !said.has(s.label)).slice(0, 4).map(statWords)] : stats.slice(0, 5).map(statWords)
  const why = !numbers.length && live.state && facts?.noLine !== null ? facts?.noLine ?? 'has no numbers in this game yet' : null
  return { numbers, why }
}

/** The followed player's own numbers on their page's board: the lead number (the headline, or the first
 * stat) and up to four more; or, with none, what to say instead (null: nothing, a sport with no lines). */
export function playerLine(live: LiveStatus): { lead: Stat; rest: Stat[]; headline: boolean } | { none: string | null } {
  const stats = live.player_stats ?? []
  const facts = factsOfKind(live.state?.kind)
  if (!live.headline && !stats.length) {
    if (!live.state || facts?.noLine === null) return { none: null }
    return { none: facts?.noLine ?? 'has no numbers in this game yet' }
  }
  // Structured lines lead with a headline ("67* (71)", "1 goal"), which already says some of the stats
  // (its sport's inHeadline); the agent's lead with the first stat.
  const said = new Set(facts?.inHeadline ?? [])
  const lead = live.headline ? { label: 'Now', value: live.headline } : stats[0]
  const rest = (live.headline ? stats.filter((s) => !said.has(s.label)) : stats.slice(1)).slice(0, 4)
  return { lead, rest, headline: Boolean(live.headline) }
}

/** One finished game on Today: each of the fan's players in it, the one with the biggest line first. */
export interface GameResult {
  key: string  // the game: the one its fixtures share (a chess board's), else its event id, else its title and day
  // The fan's players in it, each with their result; a team of theirs stands in as one, by its id (`team`), its
  // result from its side and its name as the player's.
  players: { result: LatestResult; sport?: string | null; teams?: string[]; team?: number }[]
}

/** The big word's size in a result card: as large as the card's width takes it in a line or two. */
export function wordSize(word: string, hero?: boolean): number {
  const steps = hero ? [64, 50, 40, 32] : [46, 36, 28, 22]
  return steps[word.length <= 10 ? 0 : word.length <= 16 ? 1 : word.length <= 24 ? 2 : 3]
}

/** How a result card names one of the fan's in it: a player by their surname, a team by its whole name. */
const nameOf = (p: GameResult['players'][number]) => (p.team != null ? p.result.player_name : shortName(p.result.player_name))

/**
 * What a result card on Today says: one big word (the sport's word for the line, a hat-trick or a hundred,
 * else the result), each of the fan's players in it with their line, and their win, draw or loss. Players
 * whose games went differently (opponents, or teams apart in one lobby) share the card too: then it shows
 * the score alone, or the game, and each player's line says how theirs went. The hero, the first card on a
 * day with nothing live, has the first player's numbers too. `key` is the lead player's sport.
 */
export function resultCard(game: GameResult, hero: boolean, now: number, key: string) {
  const lead = game.players[0]
  const r = lead.result
  const several = game.players.length > 1
  const marks = game.players.map((p) => resultOutcome(p.result, p.teams))
  // Their games went differently: they played each other (one won), or finished apart in a lobby ("3rd of
  // 16" and "9th of 16"). Then no one result is everyone's.
  const split = new Set(marks).size > 1 || new Set(game.players.map((p) => p.result.result)).size > 1
  // The card's result from nobody's side: the score without "Won" when each side's is a win, draw or loss,
  // else the game itself.
  const score = r.result.replace(/^(won|lost|drew)\s+/i, '')
  const result = !split ? r.result : marks.every(Boolean) && score && !/^by\b/i.test(score) ? score : r.title  // "by forfeit" says nothing alone
  const word = highlight(key, r.player_contribution) ?? (result || r.player_contribution || r.title)
  const wl = split ? null : marks[0]
  const when = dayLabel(playedOn(r), new Date(now))
  const stats = hero ? lineStats(r.player_contribution)?.slice(0, 3) : undefined
  const match = result && result !== r.title && word !== result ? `${result} · ${r.title}` : r.title
  // Each player's own line (with how their game went, when the card can't say it once); the hero's numbers
  // say the first one's.
  const lines = game.players.flatMap((p, i) => {
    const line = stats && p === lead ? null : p.result.player_contribution
    const mark = marks[i]
    const how = split ? (mark ? WON[mark] : p.result.result) : null
    const said = [how, line].filter(Boolean).join(' · ')
    return said ? [several ? `${nameOf(p)}: ${said}` : said] : []
  })
  return { r, several, word, wl, when, stats, match, lines, who: game.players.map(nameOf).join(' · ') }
}

/** What a coming game's card on Today says: big, the countdown once it's less than a day away (else the
 * day); above it, what the big line doesn't say (the day and time over a countdown, the time over a day). */
export function comingCard(event: UpcomingEvent, now: number): { big: string; eyebrow: string } {
  const start = parseDate(event.start_utc)
  const left = start && start.getTime() - now < 864e5 ? countdown(event.start_utc, now) : ''
  const big = left || (start && start.getTime() - now < 6 * 864e5 ? start.toLocaleDateString(undefined, { weekday: 'short' }) : start?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })) || 'TBC'
  const time = start && !dateOnly(event.start_utc) ? start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : 'Time TBC'
  return { big, eyebrow: [left ? shortWhen(event.start_utc, now) : time, event.competition].filter(Boolean).join(' · ') }
}

// ---------------------------------------------------------------- a player's page

/** The next game that hasn't started (a fixture with no date yet counts; one with a day but no time, all that day). */
export function nextGame(card: Card, now: number): UpcomingEvent | undefined {
  return (card.upcoming ?? []).find((u) => !parseDate(u.start_utc) || ahead(u.start_utc, now))
}

/** When the page last looked for a game: for a player bound to a source, the newest of its schedule read
 * (every half hour) and its live polls; otherwise the agent's live check. */
export function lastChecked(card: Card): string | null {
  const f = card.freshness ?? {}
  const stamps = (card.provenance ? [f.live, f.fixtures, card.provenance.upcoming?.at] : [f.live]).filter((at): at is string => Boolean(parseDate(at)))
  return stamps.sort((a, b) => parseDate(b)!.getTime() - parseDate(a)!.getTime())[0] ?? null
}

/** Under a live player's board: how its scores come in, in the source's words where it has its own. */
export function liveUpdates(card: Card): string {
  return card.live.live_note
    || (card.provenance ? `Scores update live from ${card.provenance.upcoming?.source ?? 'the source'} while the game is on.`
      : 'Live games refresh on their own about every minute.')
}

/** At the foot of a player's page: how late its live data can be, in the source's words where it has its own. */
export function liveLag(card: Card): string {
  return card.live.lag_note || 'Live data can lag the game by a minute or two.'
}

/** A result's score once a badge says won, drew or lost ("Won 133–128" is 133–128); null for a sentence
 * ("West Indies won by 5 wkts"). */
export function bareScore(result: string): string | null {
  return /^(Won|Lost|Drew)\s+/.test(result) ? result.replace(/^(Won|Lost|Drew)\s+/, '') : null
}

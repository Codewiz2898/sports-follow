// What a campaign page decides (GET /api/campaigns/<slug>, campaigns.py): who it offers, when its games are in
// its zone and the fan's own, and which games on now are its. Shared by the web app and the Android app
// (mobile/): no browser here.

import type { Campaign, CampaignGame, CampaignTeam, Card, LiveGame, LiveSport, SearchResult, SportInfo } from './types'

/** Every row a campaign offers to follow: its own players, then each team's. */
export function campaignRows(c: Campaign): SearchResult[] {
  return [...c.players, ...c.teams.flatMap((t) => t.players)]
}

/** A moment in a zone, from its parts (never a formatted string's order or spacing): "Tue 24 Nov", "3:00 pm",
 * and its date as "2026-11-24", which sorts. */
function parts(at: Date, zone: string): { day: string; time: string; date: string } {
  const bits = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: zone, weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(at).map((p) => [p.type, p.value]))
  const hour = Number(bits.hour) % 24
  const month = new Intl.DateTimeFormat('en-GB', { timeZone: zone, month: '2-digit' }).format(at)
  return {
    day: `${bits.weekday} ${bits.day} ${bits.month}`,
    time: `${hour % 12 || 12}:${bits.minute} ${hour < 12 ? 'am' : 'pm'}`,
    date: `${bits.year}-${month}-${String(bits.day).padStart(2, '0')}`,
  }
}

export interface ScheduledGame extends CampaignGame {
  day: string  // "Tue 24 Nov", in the campaign's zone
  time: string  // "3:00 pm"
  zone: string  // "IST"
  yours: string | null  // the time where the fan is, when that's another zone: "11:30 am your time" (and the day, if another)
  next: boolean  // the first game not yet started
  past: boolean  // started before today, in the campaign's zone
  today: boolean
}

/**
 * The campaign's games as its page lists them: each one's day and time in the campaign's zone (the home
 * audience's), and the fan's own time beside it when they're somewhere else (`here`: their zone), with the
 * next game marked and those of past days marked as done.
 */
export function schedule(c: Campaign, now: number, here: string): ScheduledGame[] {
  const today = parts(new Date(now), c.zone.name).date
  const nextAt = c.games.find((g) => Date.parse(g.start_utc) > now)?.start_utc
  return c.games.map((g) => {
    const at = new Date(g.start_utc)
    const there = parts(at, c.zone.name)
    const mine = parts(at, here)
    const elsewhere = mine.time !== there.time || mine.day !== there.day
    return {
      ...g, day: there.day, time: there.time, zone: c.zone.label,
      yours: elsewhere ? `${mine.time}${mine.day !== there.day ? ` ${mine.day}` : ''} your time` : null,
      next: g.start_utc === nextAt, past: there.date < today && Date.parse(g.start_utc) < now, today: there.date === today,
    }
  })
}

/**
 * The campaign's games on now: the fan's own live cards of its players, one per game (by its event: two of
 * them in one game are one board), then the games on anywhere (GET /api/live) that a player it offers is in,
 * by the source's id or as a player on Sports Follow, never by a name, less those the fan's cards show.
 */
export function campaignLive(c: Campaign, cards: Card[], sports: LiveSport[]): { mine: Card[]; others: LiveGame[] } {
  const rows = campaignRows(c)
  const keys = new Set(rows.map((r) => r.key))
  const ids = new Set(rows.flatMap((r) => (r.player_id != null ? [r.player_id] : [])))
  const seen = new Set<number>()
  const mine = cards.filter((card) => {
    if (!ids.has(card.player_id) || !card.live?.is_live) return false
    const event = card.live.event_id
    if (event == null) return true
    if (seen.has(event)) return false
    seen.add(event)
    return true
  })
  // A campaign of a group (esports, an event of several games) takes the live games of each of its sports.
  const others = sports.filter((s) => s.key === c.sport || (s.group && s.group === c.sport)).flatMap((s) => s.games).filter((g) =>
    !(g.event_ids ?? []).some((id) => seen.has(id)) && g.players.some((p) => keys.has(p.key) || (p.player_id != null && ids.has(p.player_id))))
  return { mine, others }
}

/** Whether every row is followed (`followed` says of one), and there's one at least. */
export function allFollowed(rows: SearchResult[], followed: (r: SearchResult) => boolean): boolean {
  return rows.length > 0 && rows.every(followed)
}

/** What a follow-all button says, and asks before it unfollows them all: the campaign's own players ("Follow
 * both" for two), or a team's whole roster ("Follow T1 · 5 players"). */
export function followAllWords(c: Campaign, team?: CampaignTeam): { label: string; done: string; ask: string } {
  if (team) {
    const n = team.players.length
    return { label: `Follow ${team.name} · ${n} players`, done: `Following ${team.name}`, ask: `Unfollow all ${n} of ${team.name}'s players?` }
  }
  const both = c.players.length === 2
  return { label: both ? 'Follow both' : 'Follow all', done: both ? 'Following both' : 'Following all', ask: both ? 'Unfollow both?' : `Unfollow all ${c.players.length}?` }
}

/** What a follow-all says when some of its players couldn't be followed (the follow's `failed`), or null. */
export function followAllFailed(failed: number): string | null {
  return failed ? `Couldn't follow ${failed === 1 ? 'one of them' : `${failed} of them`}. Try again in a moment.` : null
}

/** The line under the follow buttons: what a follow's key alerts are, in the sport's words. */
export function keyAlertsLine(info: SportInfo): string {
  const hint = info.alerts.key[1]
  return `Key alerts: ${hint ? hint[0].toLowerCase() + hint.slice(1) : hint}.`
}

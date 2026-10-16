// What a campaign page decides (GET /api/campaigns/<slug>, campaigns.py): who it offers, when its games are in
// its zone and the fan's own, and which games on now are its. Shared by the web app and the Android app
// (mobile/): no browser here.

import type { Campaign, CampaignGame, Card, LiveGame, LiveSport, SearchResult } from './types'

/** Every row a campaign offers to follow: its own players, then each team's. */
export function campaignRows(c: Campaign): SearchResult[] {
  return [...c.players, ...c.teams.flatMap((t) => t.players)]
}

function parts(at: Date, zone: string): { day: string; time: string; date: string } {
  const get = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-GB', { timeZone: zone, ...o }).format(at)
  const hm = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', minute: '2-digit', hour12: true }).format(at)
  return { day: `${get({ weekday: 'short' })} ${get({ day: 'numeric', month: 'short' })}`, time: hm.replace(' AM', ' am').replace(' PM', ' pm'), date: get({ year: 'numeric', month: '2-digit', day: '2-digit' }) }
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
  const others = sports.find((s) => s.key === c.sport)?.games.filter((g) =>
    !(g.event_ids ?? []).some((id) => seen.has(id)) && g.players.some((p) => keys.has(p.key) || (p.player_id != null && ids.has(p.player_id)))) ?? []
  return { mine, others }
}

/** Whether every row is followed (`followed` says of one), and there's one at least. */
export function allFollowed(rows: SearchResult[], followed: (r: SearchResult) => boolean): boolean {
  return rows.length > 0 && rows.every(followed)
}

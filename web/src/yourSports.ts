// Your sports (BHA-653): the sports a fan said they watch, in their order, kept on the server (GET and PUT
// /api/me/sports, sports_follow/fansports.py). What the welcome's tiles and Following's Edit sheet show, what the sheet
// saves, which starters Today keeps offering, and the fan's sports first in the rails and lists that hold several
// sports. Shared by the web app and the Android app (mobile/), so it touches nothing of a browser's.

import type { Card, FollowedTeam, SportInfo, StarterSport } from './types'

type KeyOf = (sport?: string | null) => string

/** The sports a fan can pick, in the order the app lists them: the sports that are on (GET /api/sports), those of no
 * group first, then each group's ("esports": each game its own) under the group's name. */
export function tileGroups(sports: SportInfo[]): { group: string; sports: SportInfo[] }[] {
  const out: { group: string; sports: SportInfo[] }[] = []
  for (const s of sports.filter((x) => x.on)) {
    const group = s.group ?? ''
    const found = out.find((g) => g.group === group)
    if (found) found.sports.push(s)
    else out.push({ group, sports: [s] })
  }
  return out.sort((a, b) => Number(Boolean(a.group)) - Number(Boolean(b.group)))
}

/** The sports of who the fan follows, players and teams, by key. */
export function followedSports(cards: Card[], teams: FollowedTeam[], keyOf: KeyOf): Set<string> {
  return new Set([...cards.map((c) => keyOf(c.player.sport)), ...teams.map((t) => keyOf(t.sport))])
}

/** What the Edit sheet ticks as it opens: the fan's sports it has a tile for, or, with none saved yet, the sports of
 * who they follow (`followed`), in the tiles' order. Nothing is saved until Save. */
export function tickedAtFirst(saved: string[], followed: Set<string>, tiles: SportInfo[]): string[] {
  return saved.length ? saved.filter((k) => tiles.some((s) => s.key === k)) : tiles.filter((s) => followed.has(s.key)).map((s) => s.key)
}

/** The fan's sports as this app knows them (`info`: its copy of GET /api/sports), on: what Following's row shows. */
export function yoursShown(saved: string[], info: (key: string) => SportInfo): SportInfo[] {
  return saved.map(info).filter((s, i) => s.on && s.key === saved[i])
}

/** A tile tapped: ticked at the end of the fan's order, or unticked. */
export function toggled(picked: string[], key: string): string[] {
  return picked.includes(key) ? picked.filter((k) => k !== key) : [...picked, key]
}

/** What Save sends: the sports ticked, in the fan's order, and any of theirs the server sent that this app shows no
 * tile for (its copy of the sports, kept a few minutes, still has one off that's on again): the fan didn't drop it. */
export function toSave(picked: string[], saved: string[], tiles: SportInfo[]): string[] {
  const shown = new Set(tiles.map((s) => s.key))
  return [...picked, ...saved.filter((k) => !shown.has(k) && !picked.includes(k))]
}

/** The Edit sheet's line over Save: what changed since the sports saved (`before`), and how many are ticked
 * ("Football added · 4 sports"). */
export function sheetSummary(picked: string[], before: string[], label: (key: string) => string): string {
  const added = picked.filter((k) => !before.includes(k))
  const dropped = before.filter((k) => !picked.includes(k))
  const said = (keys: string[], what: string) => (keys.length === 1 ? `${label(keys[0])} ${what}` : keys.length ? `${keys.length} ${what}` : '')
  const count = picked.length ? `${picked.length} ${picked.length === 1 ? 'sport' : 'sports'}` : 'No sports picked'
  return [said(added, 'added'), said(dropped, 'dropped'), count].filter(Boolean).join(' · ')
}

/** A list's items of the fan's sports first, then the rest, each lot as it was, or as `arrange` puts it: a rail the
 * server ranks keeps its own order within each (league.py's sports taking turns), and Coming up's big games pick
 * each lot's biggest (today.leagueComingUp). */
export function yoursFirst<T>(items: T[], sportOf: (item: T) => string | null | undefined, yours: readonly string[], keyOf: KeyOf, arrange: (lot: T[]) => T[] = (lot) => lot): T[] {
  if (!yours.length) return arrange(items)
  const mine = (item: T) => yours.includes(keyOf(sportOf(item)))
  return [...arrange(items.filter(mine)), ...arrange(items.filter((item) => !mine(item)))]
}

/** Lists of one sport each (a sport's games on the Live page, its starters), the fan's sports first in their order,
 * then the rest as they were. */
export function inYourOrder<T>(items: T[], keyOfItem: (item: T) => string, yours: readonly string[]): T[] {
  const at = (item: T) => {
    const i = yours.indexOf(keyOfItem(item))
    return i < 0 ? yours.length : i
  }
  return [...items].sort((a, b) => at(a) - at(b))
}

/** Whether a list has some of the fan's sports and some not: its order put theirs first ("Yours first"). */
export function yoursMovedUp<T>(items: T[], sportOf: (item: T) => string | null | undefined, yours: readonly string[], keyOf: KeyOf): boolean {
  const mine = items.map((item) => yours.includes(keyOf(sportOf(item))))
  return mine.some(Boolean) && mine.some((m) => !m)
}

/**
 * Today's "Players to follow": each sport's starters (GET /api/starters), the fan's sports first. While the fan
 * follows few players (`few`), every sport's; after, only those of the fan's sports they follow nobody in
 * (`followed`), so a fan who picked football and followed three cricketers is still offered footballers, and those of
 * theirs it already offered on this visit (`stays`), so following one from a sport doesn't take the rest away. With a
 * game picked on Today, its own alone.
 */
export function startersOffered(starters: StarterSport[], yours: readonly string[], followed: Set<string>, few: boolean, game: string | null,
  stays: ReadonlySet<string> = new Set()): StarterSport[] {
  const offered = inYourOrder(starters, (s) => s.key, yours).filter((s) => few || (yours.includes(s.key) && (!followed.has(s.key) || stays.has(s.key))))
  return game ? offered.filter((s) => s.key === game) : offered
}

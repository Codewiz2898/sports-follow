// Today's game filter: which games have something on Today, its chips, and Today narrowed to one of them. A game
// is a sport (each esports title is one of its own), by its key; `keyOf` finds an item's from the sport it says
// ("Women's football" is football's, "CS2" is cs2's), each app its own way. Shared by the web app
// (pages/TodayPage.tsx) and, later, the Android app (mobile/), so it touches nothing of a browser's.

import type { GameResult } from './lines'
import type { Coming, GameOn, TeamGame } from './today'
import type { Card, FollowedMatch, FollowedTeam, LeagueGame, SportInfo, WeekPoster, WireRow } from './types'

type KeyOf = (sport?: string | null) => string

/** What Today shows, block by block, each list as its block has it (today.ts worked them out). From your players
 * isn't one: it asks for its own posts, and narrows them itself (Feed.tsx); its players are the strip's. */
export interface TodayBlocks {
  cards: Card[]  // the strip: the fan's players
  teams: FollowedTeam[]  // and teams
  live: [string, Card[]][]  // their players' games on now (liveGames)
  followed: FollowedMatch[]  // the games they follow by themselves, on now (followedLive)
  teamGames: TeamGame[]  // their teams' games on now (teamsLive)
  alsoLive: GameOn[]  // Live now, every game on, for a fan who follows nobody (AlsoLive)
  moreLive: GameOn[]  // the other games on now, a link to the Live page for a fan who follows someone (MoreLive)
  results: GameResult[]  // Results: their own
  bigResults: LeagueGame[]  // and the big ones after them, or alone (Big results)
  coming: Coming[]  // Coming up: their own
  bigComing: LeagueGame[]  // and the big ones after them, or alone (Big games coming up)
  posters: WeekPoster[]  // This week
  wire: WireRow[]  // the Wire
}

/** Today with nothing on it, for a page that has only some of the blocks (a fan who follows nobody yet). */
export const NO_BLOCKS: TodayBlocks = {
  cards: [], teams: [], live: [], followed: [], teamGames: [], alsoLive: [], moreLive: [], results: [], bigResults: [], coming: [],
  bigComing: [], posters: [], wire: [],
}

/**
 * The sport each block's items are of, as their data says it. A live game, a result or a game coming up with
 * several of the fan's players in it is one game, so its first player's; a game coming up that none of their
 * players is in (a team's, or one followed by itself) says its own.
 */
const SPORT: { [K in keyof TodayBlocks]: (item: TodayBlocks[K][number]) => string | null | undefined } = {
  cards: (c) => c.player.sport,
  teams: (t) => t.sport,
  live: ([, group]) => group[0]?.player.sport,
  followed: (m) => m.sport,
  teamGames: (g) => g.sport,
  alsoLive: (x) => x.game.sport,
  moreLive: (x) => x.game.sport,
  results: (g) => g.players[0]?.sport,
  bigResults: (g) => g.sport,
  coming: (g) => g.sport ?? g.players[0]?.player.sport,
  bigComing: (g) => g.sport,
  posters: (p) => p.sport,
  wire: (r) => r.sport,
}

const BLOCKS = Object.keys(SPORT) as (keyof TodayBlocks)[]

/** One block's items of a game. */
function of<K extends keyof TodayBlocks>(blocks: TodayBlocks, block: K, game: string, keyOf: KeyOf): TodayBlocks[K] {
  const sport: (item: TodayBlocks[K][number]) => string | null | undefined = SPORT[block]
  return (blocks[block] as TodayBlocks[K][number][]).filter((item) => keyOf(sport(item)) === game) as TodayBlocks[K]
}

/** Today narrowed to one game (its key): each block its items of that game alone. No game is all of it. */
export function narrow(blocks: TodayBlocks, game: string | null, keyOf: KeyOf): TodayBlocks {
  if (!game) return blocks
  const out = { ...blocks }
  const keep = <K extends keyof TodayBlocks>(block: K) => { out[block] = of(blocks, block, game, keyOf) }
  BLOCKS.forEach(keep)
  return out
}

/** The keys of the games `blocks` items are of. */
function gamesIn(blocks: TodayBlocks, which: (keyof TodayBlocks)[], keyOf: KeyOf): Set<string> {
  const keys = new Set<string>()
  const add = <K extends keyof TodayBlocks>(block: K) => {
    const sport: (item: TodayBlocks[K][number]) => string | null | undefined = SPORT[block]
    for (const item of blocks[block] as TodayBlocks[K][number][]) keys.add(keyOf(sport(item)))
  }
  which.forEach(add)
  return keys
}

/** The games with something on Today, by key. The link to the other games on now isn't one: it names none, and
 * those games are on the Live page (a fan of one sport would get a chip for every sport on anywhere). */
export function gamesOnToday(blocks: TodayBlocks, keyOf: KeyOf): Set<string> {
  return gamesIn(blocks, BLOCKS.filter((b) => b !== 'moreLive'), keyOf)
}

/** The fan's own games on Today, by key: their players', their teams' and the games they follow by themselves. */
export function theirGames(blocks: TodayBlocks, keyOf: KeyOf): Set<string> {
  return gamesIn(blocks, ['cards', 'teams', 'followed'], keyOf)
}

/**
 * Today's chips after All: each game with something on it (`here`, gamesOnToday), the fan's own first (`theirs`,
 * theirGames), each lot in the order the sports list (`sports`: GET /api/sports's sports, then its groups) gives
 * them; the big games' and the Wire's after, so a cricket fan's Cricket isn't behind every esports final on. None
 * while it holds one game or none. A sport no module knows ("other": golf, from an AI-built page), or one turned
 * off (not `on`), has no chip, and shows under All alone.
 */
export function gameChips(sports: SportInfo[], here: Set<string>, theirs: Set<string> = new Set()): SportInfo[] {
  const chips = sports.filter((s) => s.on && here.has(s.key))
  if (chips.length < 2) return []
  return [...chips.filter((s) => theirs.has(s.key)), ...chips.filter((s) => !theirs.has(s.key))]
}

/** The game picked (`?game=` on the web), while it has a chip; anything else is All (null). */
export function pickedGame(key: string | null | undefined, chips: SportInfo[]): SportInfo | null {
  return (key && chips.find((s) => s.key === key)) || null
}

/** Whether Today shows nothing but who the fan follows (the strip): nothing on, nothing done or to come, nothing
 * said. */
export function nothingOn(blocks: TodayBlocks): boolean {
  return BLOCKS.every((b) => b === 'cards' || b === 'teams' || blocks[b].length === 0)
}

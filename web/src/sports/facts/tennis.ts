import type { SportFacts } from '.'

export interface TennisSet { games: number; tiebreak?: number | null; won?: boolean | null }
export interface TennisPlayer { id: string; name: string; short: string; country?: string | null; seed?: number | null; serving: boolean; winner?: boolean | null; sets: TennisSet[] }
export interface TennisState { kind: 'tennis'; players: TennisPlayer[]; set?: number | null; round?: string | null; tournament?: string | null }

export const tennis: SportFacts = {
  key: 'tennis',
  kinds: ['tennis'],
  inHeadline: ['Sets'],
}

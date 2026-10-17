import { lineStats } from '../../format'
import type { SportFacts } from '.'

export interface BasketballSide { id: string; name: string; abbr?: string | null; score: string; periods: string[]; winner?: boolean | null }
export interface BasketballState { kind: 'basketball'; home: BasketballSide; away: BasketballSide; period?: number | null; clock?: string | null; detail?: string | null; note?: string | null }

/** A period's name in the line score: Q1 to Q4, then OT, OT2… */
export function periodName(i: number): string {
  return i < 4 ? `Q${i + 1}` : `OT${i > 4 ? i - 3 : ''}`
}

export const basketball: SportFacts = {
  key: 'basketball',
  kinds: ['basketball'],
  highlight: (line) => {
    const tens = (lineStats(line) ?? []).filter((s) => /^(PTS|REB|AST|STL|BLK)$/i.test(s.label) && Number(s.value) >= 10).length
    return tens >= 3 ? 'Triple-double' : tens === 2 ? 'Double-double' : null
  },
  noLine: "isn't on the game's roster",
}

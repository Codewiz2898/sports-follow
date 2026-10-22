// Which sport a free-text sport name is, and how to write it, by what GET /api/sports says. Shared by the web
// app (catalog.ts) and the Android app (mobile/), so it touches nothing of the browser's.

import type { SportCatalog, SportInfo } from '../types'

export interface Sports {
  catalog: SportCatalog
  /** The sport a free-text sport name is ("Women's football", "CS2"), or its key ("cs2"), or "other". Where two
   * match, the one matching earliest in the name wins: "eFootball" is esports, "American football" is ruled out
   * of football. A group ("Esports") is matched as a sport is, after the listed ones. */
  info(name?: string | null): SportInfo
  /** The key a sport is drawn as (its board, line art, how its line reads: sports/facts/): its group's for one
   * of a group ("CS2" is drawn as esports), else its own. */
  drawn(name?: string | null): string
  /** Models report the sport in whatever case they like ("cricket", "CRICKET"); show it one way. A game's
   * own name stays as written ("CS2", "League of Legends", "PUBG Mobile"). */
  name(sport?: string | null): string
  /** The sports this build runs, in the order the app lists them: the search filters. */
  running(): SportInfo[]
  /** Every sport the app lists, in its order, then the groups that stand for several of them (an esports pro whose
   * game can't be told is esports'): Today's game chips, of the ones on it (filter.ts). */
  listed(): SportInfo[]
}

export function sportsOf(catalog: SportCatalog): Sports {
  const all = [...catalog.sports, ...(catalog.groups ?? [])]
  const byKey = new Map(all.map((s) => [s.key, s]))
  const matchers = all
    .filter((s) => s.pattern)
    .map((info) => ({ info, pattern: new RegExp(info.pattern), exclude: info.exclude ? new RegExp(info.exclude) : null }))

  const info = (name?: string | null): SportInfo => {
    const s = (name ?? '').toLowerCase()
    const keyed = byKey.get(s.trim())
    if (keyed) return keyed
    let best: { info: SportInfo; at: number; length: number } | null = null
    for (const { info, pattern, exclude } of matchers) {
      const m = pattern.exec(s)
      if (!m || exclude?.test(s)) continue
      if (!best || m.index < best.at || (m.index === best.at && m[0].length > best.length)) best = { info, at: m.index, length: m[0].length }
    }
    return best?.info ?? catalog.other
  }

  const name = (sport?: string | null): string => {
    if (!sport) return ''
    if (byKey.has(sport.trim().toLowerCase())) return info(sport).label // a key ("cs2"): its name ("CS2")
    if (info(sport).keeps_names && /[A-Z]/.test(sport) && sport.toLowerCase() !== info(sport).key) return sport
    return sport.replace(/\S+/g, (w) => (w === w.toUpperCase() && w.length <= 4 ? w : w[0].toUpperCase() + w.slice(1).toLowerCase()))
  }

  const drawn = (name?: string | null): string => {
    const i = info(name)
    return i.group || i.key
  }

  return { catalog, info, drawn, name, running: () => catalog.sports.filter((s) => s.on), listed: () => all }
}

/** Before the server has answered: no sports, and any name is "other". */
export const NO_SPORTS: SportCatalog = {
  sports: [],
  other: {
    key: 'other', label: '', on: true, live: false, pattern: '', exclude: '', stats_title: 'Season', team_results: false, keeps_names: false, group: '', colour: null,
    alerts: { key: ['Big news', 'Injuries, transfers and retirements, from the news'], more: ['milestones', 'milestones in the news'] },
  },
}

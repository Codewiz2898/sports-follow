// What following a search result sends. Shared by the web app and the Android app (mobile/): no browser here.

import type { FollowTarget, SearchResult } from './types'

/** What following a result sends: the player already here, the athlete in their live source, or a name. */
export function followTarget(r: SearchResult): FollowTarget {
  return r.player_id ? { player_id: r.player_id }
    : r.system && r.athlete_id ? { system: r.system, athlete_id: r.athlete_id, league: r.league, qid: r.qid }
      : r.qid ? { qid: r.qid, sport: r.sport.toLowerCase() }  // the server finds them in the live source
        : { query: r.name }  // a sport only the agent covers: this is an AI research
}

// When Today's "From your players" (feedLayouts.tsx) reads the fan's Feed again, which the server works out whole
// even for its top two (sports_follow/feed.py `page`). At once when the follow list comes in or changes, on a pull
// and on coming back to the front: the fan is looking then. After news (following.tsx's heard: a notification, a
// followed card or game moving on in the 30 s re-read, which in a live game is nearly every one), only after a wait
// of this phone's own (web/src/refetch.ts), as the activity list and This week: every phone following a player hears
// of a goal at the same instant (BUG-265). No React here: tests/test_mobile_feed_refetch.py runs it in Node.

import { refetcher, type Clock, type Wait } from '../../web/src/refetch'

/** The Feed: 2 to 10 minutes after hearing, and no more than every 20 minutes. Its sources are read every 20 or 30
 * minutes (sports_follow/curator.py), so a moment brings nothing new to it sooner. */
export const FEED: Wait = { after: 2 * 60_000, spread: 8 * 60_000, every: 20 * 60_000 }

export interface FeedReads {
  now: () => void // a read at once (the follow list in or changed, a pull, back in front): one waiting is this one, and the 20 minutes count from it
  heard: () => void // there may be news: read once, after FEED's wait
  stop: () => void
}

/** Reads (`read`) once after news, after FEED's wait; not if the app is away from the front by then (`front`), as
 * coming back to it reads at once. */
export function feedReads(read: () => void, front: () => boolean, random: () => number = Math.random, clock?: Clock): FeedReads {
  const later = refetcher(FEED, () => { if (front()) read() }, random, clock)
  return {
    now() { later.stop(); later.asked() },
    heard: () => later.heard(),
    stop: () => later.stop(),
  }
}

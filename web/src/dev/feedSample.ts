// Dev only: made-up videos, posts and news for a player's Feed and the Android app's Today rail, to see the cards before the
// feed pipeline fills them (sports_follow/feed.py, `judged`). Loaded by components/Feed.tsx when the dev
// server's page has ?feed-sample; a production build never includes it (import.meta.env.DEV is false there).
// The YouTube ids name no real video: the cards fall back to their ink thumbnail.

import type { Card, Feed, FeedItem, FeedShare } from '../types'
import { shortName } from '../format'

const HOUR = 36e5

function at(hoursAgo: number): string {
  return new Date(Date.now() - hoursAgo * HOUR).toISOString()
}

function item(id: string, fields: Omit<FeedItem, 'id' | 'url' | 'reason'> & { reason?: string }): FeedItem {
  return { id, url: `https://example.com/${id}`, reason: 'A sample item, made up to show the card.', ...fields }
}

export function sampleFeed(playerId: number, name: string): Feed {
  const who = shortName(name || 'the player')
  const groups = [
    {
      head: `${who} wins the decider`, moment: true, when: at(30), match: null,
      items: [
        item('a1b2c3d4e5f60001', { kind: 'video', tag: 'Analysis', title: `The decider, move by move: where ${who} turned it`, source: 'Sample Creator', published_at: at(9),
          video: { youtube_id: 'SAMPLEvid01', duration: 1122, views: 41000 }, reason: 'A creator on this sport\'s list, breaking down the game the day after.' }),
        item('a1b2c3d4e5f60002', { kind: 'post', tag: 'Post', title: 'Proud of the whole team. One more to go, see you on Friday.', source: `@${who.toLowerCase()}`, published_at: at(6),
          post: { platform: 'X', own: true }, reason: `${who}'s own account, after the game.` }),
        item('a1b2c3d4e5f60003', { kind: 'news', tag: 'News', title: `The decider annotated: the plan behind ${who}'s sacrifice`, source: 'Sample Outlet', published_at: at(3) }),
      ],
    },
    {
      head: 'This week', moment: false, when: null, match: null,
      items: [
        item('a1b2c3d4e5f60004', { kind: 'video', tag: 'Fan edit', title: `${who} this season so far: the best of it`, source: 'Sample Edits', published_at: at(50),
          video: { youtube_id: 'SAMPLEvid02', duration: 365, views: 412000 } }),
        item('a1b2c3d4e5f60005', { kind: 'post', tag: 'Post', title: "A fan's caption, as written, up to three lines. A snippet here, the post where it lives.", source: 'Sample Fan', published_at: at(26),
          post: { platform: 'Instagram', own: false } }),
        item('a1b2c3d4e5f60006', { kind: 'video', tag: 'Interview', title: 'Sit-down before the match: preparation, sleep, and the second\'s chair', source: 'Sample Pod', published_at: at(96),
          video: { youtube_id: 'SAMPLEvid03', duration: 2470, views: 88000 } }),
      ],
    },
  ]
  const evergreen = [
    item('a1b2c3d4e5f60007', { kind: 'video', tag: 'Classic', title: 'The title: the final moments', source: 'Sample Cast', published_at: at(9000), video: { youtube_id: 'SAMPLEvid04', duration: 640, views: 3100000 } }),
    item('a1b2c3d4e5f60008', { kind: 'video', tag: 'Classic', title: 'Every brilliancy, ranked', source: 'Sample Edits', published_at: at(12000), video: { youtube_id: 'SAMPLEvid05', duration: 905, views: 1400000 } }),
    item('a1b2c3d4e5f60009', { kind: 'video', tag: 'Classic', title: 'At twelve: the year it started', source: 'Sample Creator', published_at: at(20000), video: { youtube_id: 'SAMPLEvid06', duration: 1210, views: 2200000 } }),
  ]
  return { player_id: playerId, groups, evergreen, count: groups.reduce((n, g) => n + g.items.length, 0) + evergreen.length }
}

/** Today's rail: a video for the first followed player, their own post for the second, a fan edit for the third. */
export function sampleShares(cards: Card[]): FeedShare[] {
  const kinds: Omit<FeedItem, 'id' | 'url' | 'reason'>[] = [
    { kind: 'video', tag: 'Analysis', title: 'The last game, move by move: where it turned', source: 'Sample Creator', published_at: at(9), video: { youtube_id: 'SAMPLEvid01', duration: 1122, views: 41000 } },
    { kind: 'post', tag: 'Post', title: 'Proud of the whole team. One more to go.', source: '@player', published_at: at(11), post: { platform: 'Instagram', own: true } },
    { kind: 'video', tag: 'Fan edit', title: 'The run so far: every big moment', source: 'Sample Edits', published_at: at(26), video: { youtube_id: 'SAMPLEvid02', duration: 365, views: 412000 } },
  ]
  return cards.slice(0, kinds.length).map((c, n) => ({ player_id: c.player_id, item: item(`b1b2c3d4e5f6000${n}`, kinds[n]) }))
}

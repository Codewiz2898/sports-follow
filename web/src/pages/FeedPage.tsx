import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { FeedCircle, FeedEntry, FeedItem, FeedLayout, FeedPage as Page, FeedTabKey, FeedVerdict } from '../api'
import { useNow } from '../clock'
import { ItemSheet } from '../components/Feed'
import { EdgePost, EdgeSheetExtras, FeedTabs, Reels, usePlaying, type Follows } from '../components/FeedLayouts'
import { Circles } from '../components/FeedPost'
import { ringed } from '../feed'
import { markOpened, markSeen, seenAt, useFeedSeen } from '../feedSeen'
import { useFollowing } from '../following'
import { SearchIcon } from '../components/Icons'
import { useMyFeed, useNearEnd } from '../myFeed'

/**
 * The Feed tab (BHA-349): For you, the fan's players' videos, posts and news with videos of players they don't
 * follow mixed in (3 of every 5 theirs), and Following, theirs alone (GET /api/me/feed/items, sports_follow/feed.py
 * `page`). The server says how to draw it (`layout`, which the admin panel changes live): edge to edge, or full
 * screen (components/FeedLayouts.tsx). Its videos play in their cards: on a tap, with sound, or by themselves, muted,
 * the topmost more than half on screen, as the fan's setting on the Account page allows (BHA-359, autoplay.ts).
 * Following's circles across the top filter it to one player
 * (/me/feed?tab=following&player=<id>), ringed in their sport's colour while they have something the fan hasn't
 * seen. The next page loads as the end comes near. Opening it takes away the tab's dot (feedSeen.ts).
 */

const LAYOUT_KEY = 'feed-layout'  // the last layout the server said, so a reload draws it before the first page is in

function remembered(): FeedLayout {
  try {
    return localStorage.getItem(LAYOUT_KEY) === 'full' ? 'full' : 'edge'
  } catch {
    return 'edge'
  }
}

/** The layout the server says, the last one it said until a page is in. */
function useLayout(page: Page | null): FeedLayout {
  const [layout, setLayout] = useState<FeedLayout>(remembered)
  const said = page?.layout
  useEffect(() => {
    if (said !== 'edge' && said !== 'full') return
    setLayout(said)
    try {
      localStorage.setItem(LAYOUT_KEY, said)
    } catch {
      // private mode: it's only for the next reload
    }
  }, [said])
  return layout
}

/** Following a card's player where it is: whether the fan follows them (their follow list once it's in, the page's
 * word before), and a tap that follows or stops following them. */
function useFollows(): { follows: Follows; error: string | null } {
  const { cards, loaded, follow, unfollow } = useFollowing()
  const ids = useMemo(() => new Set(cards.map((c) => c.player_id)), [cards])
  const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const toggle = useCallback((e: FeedEntry) => {
    const id = e.player.id
    const on = loaded ? ids.has(id) : e.following
    setBusy((b) => new Set(b).add(id))
    setError(null)
    ;(on ? unfollow(id) : follow({ player_id: id }).then(() => undefined))
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy((b) => { const left = new Set(b); left.delete(id); return left }))
  }, [ids, loaded, follow, unfollow])
  const follows = useMemo<Follows>(() => ({
    following: (e) => (loaded ? ids.has(e.player.id) : e.following),
    busy: (e) => busy.has(e.player.id),
    toggle,
  }), [ids, loaded, busy, toggle])
  return { follows, error }
}

export function FeedPage() {
  const now = useNow()
  const { cards, loaded } = useFollowing()
  const [params, setParams] = useSearchParams()
  const wanted = Number(params.get('player'))
  const player = Number.isInteger(wanted) && wanted > 0 ? wanted : null
  const tab: FeedTabKey = player != null || params.get('tab') === 'following' ? 'following' : 'for_you'
  const { page, error, loading, more, hide } = useMyFeed(player, undefined, undefined, tab)
  const layout = useLayout(page)
  const { follows, error: followError } = useFollows()
  const [open, setOpen] = useState<FeedEntry | null>(null)
  const [circles, setCircles] = useState<FeedCircle[]>([])
  const [edge, setEdge] = useState<HTMLDivElement | null>(null)
  useFeedSeen()
  const end = useNearEnd(more, layout === 'edge' && Boolean(page?.cursor))

  // Opened: the tab's dot goes, and again once the page says what's newest (it may be newer than the dot knew).
  useEffect(() => { markOpened() }, [page?.newest])
  // The circles stay put while one player's is shown (each page lists them all).
  useEffect(() => { if (page) setCircles(page.players) }, [page])

  const showTab = (t: FeedTabKey) => setParams(t === 'following' ? { tab: 'following' } : {}, { replace: true })
  const pick = (id: number | null) => {
    if (id != null) markSeen(id)
    setParams(id == null ? { tab: 'following' } : { tab: 'following', player: String(id) }, { replace: true })
  }
  const picked = circles.find((c) => c.id === player)
  const items = page?.items ?? []
  const { playing, play } = usePlaying(layout === 'edge' ? edge : null, null, items)
  const sheet = open && (
    <ItemSheet item={open.item} name={open.player.name} onClose={() => setOpen(null)} onHide={(_: FeedItem, verdict: FeedVerdict) => hide(open, verdict)}>
      {layout === 'edge' && <EdgeSheetExtras entry={open} />}
    </ItemSheet>
  )

  // Nothing (yet): why, and the way on.
  const followsNobody = loaded && cards.length === 0
  const empty = page && items.length === 0 && (
    tab === 'following' && followsNobody ? (
      <div className="card card-pad stack feed-empty" style={{ gap: 10 }}>
        <span className="title" style={{ fontSize: 16 }}>Follow a player to fill Following</span>
        <span className="small muted">What fans, creators and the players themselves share about them shows here, newest first. For you has the top players' meanwhile.</span>
        <div className="row" style={{ gap: 10, justifyContent: 'flex-start' }}>
          <button type="button" className="btn primary" onClick={() => showTab('for_you')}>See For you</button>
          <Link to="/search" className="btn"><SearchIcon width={18} height={18} />Find a player</Link>
        </div>
      </div>
    ) : (
      <div className="card card-pad stack feed-empty" style={{ gap: 6 }}>
        <span className="title" style={{ fontSize: 16 }}>Nothing here yet</span>
        <span className="small muted">We only show what's on the official channels and the top creators' about {picked ? picked.name : tab === 'following' ? 'your players' : 'the players fans follow'}. Nothing sponsored, nothing in passing.</span>
      </div>
    )
  )
  // The end: Following offers For you once its players have run out.
  const done = page && !page.cursor && items.length > 0 && (
    tab === 'following' ? (
      <div className="feed-done stack" style={{ gap: 10, alignItems: 'center' }}>
        <span className="eyebrow">That's everything from {picked ? picked.name : 'your players'}</span>
        {!picked && <button type="button" className="btn" onClick={() => showTab('for_you')}>See For you</button>}
      </div>
    ) : <p className="eyebrow feed-done" style={{ textAlign: 'center' }}>You're up to date · newer ones arrive through the day</p>
  )

  if (layout === 'full') {
    return (
      <>
        <Reels entries={items} now={now} tab={tab} onTab={showTab} follows={follows} onMore={setOpen} onNear={more}
          footer={error ? (
            <div className="stack" style={{ gap: 12, alignItems: 'center', textAlign: 'center' }}>
              <span className="eyebrow">{error}</span>
              {page && <button type="button" className="btn" onClick={more}>Try again</button>}
            </div>
          ) : !page ? <span className="spinner" /> : items.length === 0 ? empty : page.cursor ? <span className="spinner" /> : done} />
        {followError && <div className="error-box reels-error" role="alert">{followError}</div>}
        {sheet}
      </>
    )
  }

  return (
    <>
      <header className="page-head">
        <h1 className="dsp page-title">Feed</h1>
      </header>
      <FeedTabs tab={tab} onTab={showTab} className="feed-tabs" />

      {tab === 'following' && circles.length > 0 && (
        <Circles players={circles} picked={player} lit={(c) => ringed(c, seenAt(c.id), now)} onPick={pick} />
      )}

      {error && !page && <div className="error-box">{error}</div>}
      {!page && !error && <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>}
      {followError && <div className="error-box" role="alert">{followError}</div>}
      {empty}

      {items.length > 0 && (
        <div ref={setEdge} className="edge">
          {items.map((e) => (
            <EdgePost key={e.item.id} entry={e} now={now} follows={follows} playing={playing?.id === e.item.id} muted={playing?.muted} onPlay={play} onMore={() => setOpen(e)} />
          ))}
        </div>
      )}

      {page?.cursor && <div ref={end} className="row" style={{ justifyContent: 'center', minHeight: 48 }}>{loading && <span className="spinner" />}</div>}
      {error && page && <div className="error-box">{error} <button type="button" className="btn link" onClick={more}>Try again</button></div>}
      {done}
      {sheet}
    </>
  )
}

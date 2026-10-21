import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { FeedCircle, FeedEntry, FeedItem, FeedVerdict } from '../api'
import { useNow } from '../clock'
import { ItemSheet } from '../components/Feed'
import { Circles, FeedPost } from '../components/FeedPost'
import { byDay, ringed } from '../feed'
import { markOpened, markSeen, seenAt, useFeedSeen } from '../feedSeen'
import { useFollowing } from '../following'
import { SearchIcon } from '../components/Icons'
import { useMyFeed, useNearEnd } from '../myFeed'

/**
 * The Feed tab (the InstaFeed board): every followed player's videos, posts and news as one feed, by day, players
 * taking turns (GET /api/me/feed/items, sports_follow/feed.py `page`). Their circles across the top filter it to
 * one of them (/me/feed?player=<id>), ringed in their sport's colour while they have something the fan hasn't seen. The
 * next page loads as the end comes into view. Opening it takes away the tab's dot (feedSeen.ts).
 */

export function FeedPage() {
  const now = useNow()
  const { cards, loaded } = useFollowing()
  const [params, setParams] = useSearchParams()
  const wanted = Number(params.get('player'))
  const player = Number.isInteger(wanted) && wanted > 0 ? wanted : null
  const { page, error, loading, more, hide } = useMyFeed(player)
  const [open, setOpen] = useState<FeedEntry | null>(null)
  const [circles, setCircles] = useState<FeedCircle[]>([])
  useFeedSeen()
  const end = useNearEnd(more, Boolean(page?.cursor))

  // Opened: the tab's dot goes, and again once the page says what's newest (it may be newer than the dot knew).
  useEffect(() => { markOpened() }, [page?.newest])
  // The circles stay put while one player's is shown (each page lists them all).
  useEffect(() => { if (page) setCircles(page.players) }, [page])

  const pick = (id: number | null) => {
    if (id != null) markSeen(id)
    setParams(id == null ? {} : { player: String(id) }, { replace: true })
  }
  const picked = circles.find((c) => c.id === player)

  return (
    <>
      <header className="page-head">
        <h1 className="dsp page-title">Feed</h1>
        <span className="eyebrow" style={{ paddingBottom: 8 }}>From your players</span>
      </header>

      {circles.length > 0 && (
        <Circles players={circles} picked={player} lit={(c) => ringed(c, seenAt(c.id), now)} onPick={pick} />
      )}

      {error && !page && <div className="error-box">{error}</div>}
      {!page && !error && <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>}

      {page && page.items.length === 0 && (
        loaded && cards.length === 0 ? (
          <div className="card card-pad stack" style={{ gap: 10 }}>
            <span className="title" style={{ fontSize: 16 }}>Follow a player to fill your Feed</span>
            <span className="small muted">What fans, creators and the players themselves share about them shows here, newest first.</span>
            <Link to="/search" className="btn primary" style={{ alignSelf: 'flex-start' }}><SearchIcon width={18} height={18} />Find a player</Link>
          </div>
        ) : (
          <div className="card card-pad stack" style={{ gap: 6 }}>
            <span className="title" style={{ fontSize: 16 }}>Nothing here yet</span>
            <span className="small muted">We only show what fans are actually watching and sharing about {picked ? picked.name : 'your players'}. Nothing sponsored, nothing in passing.</span>
          </div>
        )
      )}

      {page && byDay(page.items).map((d) => (
        <section key={d.day} className="feed-day" aria-label={d.day}>
          <h2 className="feed-day-head eyebrow">{d.day}</h2>
          <div className="posts">
            {d.entries.map((e) => <FeedPost key={e.item.id} who={e.player} item={e.item} moment={e.moment} now={now} onMore={() => setOpen(e)} />)}
          </div>
        </section>
      ))}

      {page?.cursor && <div ref={end} className="row" style={{ justifyContent: 'center', minHeight: 48 }}>{loading && <span className="spinner" />}</div>}
      {error && page && <div className="error-box">{error} <button type="button" className="btn link" onClick={more}>Try again</button></div>}
      {page && !page.cursor && page.items.length > 0 && <p className="eyebrow" style={{ textAlign: 'center' }}>You're up to date · newer ones arrive through the day</p>}

      {open && <ItemSheet item={open.item} name={open.player.name} onClose={() => setOpen(null)} onHide={(_: FeedItem, verdict: FeedVerdict) => hide(open, verdict)} />}
    </>
  )
}

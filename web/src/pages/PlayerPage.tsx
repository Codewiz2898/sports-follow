import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, subscribe, type Card } from '../api'
import { Avatar, BuildProgress, LiveScoreboard, NewsList, ResultsList, StatGrid, UpcomingList } from '../components/Blocks'
import { BackIcon, CheckIcon, PlusIcon, RefreshIcon } from '../components/Icons'
import { relative, sportName } from '../format'
import { useFollowing } from '../following'

type Tab = 'live' | 'fixtures' | 'news' | 'stats' | 'results'

/**
 * One player's page. Always the full card plus the player's own stream: the Following list only
 * carries summaries (no stats, sources or results), so it can't stand in for the page.
 */
export function PlayerPage() {
  const params = useParams()
  const playerId = Number(params.id)
  const navigate = useNavigate()
  const following = useFollowing()
  const isFollowing = following.isFollowing(playerId)
  const [card, setCard] = useState<Card | null>(null)
  const [progress, setProgress] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('live')
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!Number.isFinite(playerId)) return
    setCard(null)
    setProgress([])
    setError(null)
    let stop = () => {}
    let cancelled = false
    api.card(playerId).then((c) => {
      if (cancelled) return
      setCard(c)
      stop = subscribe(`/api/players/${playerId}/stream`, (e) => {
        if (e.type === 'card') {
          setCard((prev) => (!prev || e.card.version >= prev.version ? e.card : prev))
          if (e.card.status !== 'building') setProgress([])
        } else if (e.type === 'progress') setProgress((p) => [...p, e.message].slice(-8))
        else if (e.type === 'moved') navigate(`/player/${e.to}`, { replace: true })
      })
    }).catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true; stop() }
  }, [playerId, navigate])

  useEffect(() => {
    if (card?.player.status === 'retired') setTab((t) => (t === 'live' || t === 'fixtures' ? 'stats' : t))
  }, [card?.player.status])

  if (error) return <div className="error-box">{error}</div>
  if (!card) return <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>

  const p = card.player
  const retired = p.status === 'retired'

  const retry = async () => {
    const fresh = await following.follow(p.name)
    setCard(fresh)
  }

  const toggleFollow = async () => {
    setBusy(true)
    try {
      if (isFollowing) await following.unfollow(playerId)
      else await following.follow(p.name)
    } finally {
      setBusy(false)
    }
  }

  const refresh = async () => {
    setRefreshing('Checking…')
    try {
      const r = await api.refresh(playerId)
      setRefreshing(r.queued ? 'Checking the live score…' : r.reason ?? null)
    } catch (e) {
      setRefreshing((e as Error).message)
    }
    setTimeout(() => setRefreshing(null), 45000)
  }

  const tabs: [Tab, string][] = retired
    ? [['stats', 'Career'], ['news', 'News'], ['results', 'Results']]
    : [['live', 'Live'], ['fixtures', 'Fixtures'], ['news', 'News'], ['stats', 'Stats'], ['results', 'Results']]

  return (
    <>
      <header className="player-head">
        <Link to="/" className="btn icon" aria-label="Back to Following"><BackIcon /></Link>
        <div className="player-id">
          <Avatar name={p.name} size="lg" />
          <div className="stack">
            <h1 className="num" style={{ fontSize: 30 }}>{p.name}</h1>
            <span className="small muted">{[sportName(p.sport), ...(p.teams ?? []).slice(0, 2), retired && 'Retired'].filter(Boolean).join(' · ')}</span>
          </div>
        </div>
        <button type="button" className="btn follow" onClick={toggleFollow} disabled={busy} aria-pressed={isFollowing}>
          {isFollowing ? <><CheckIcon width={16} height={16} />Following</> : <><PlusIcon width={16} height={16} />Follow</>}
        </button>
      </header>

      {card.status !== 'ready' ? (
        <BuildProgress card={card} messages={progress} onRetry={retry} />
      ) : (
        <>
          {p.summary && <p className="muted" style={{ margin: 0, maxWidth: '70ch' }}>{p.summary}</p>}
          {p.disambiguation && <p className="tiny muted" style={{ margin: 0 }}>{p.disambiguation}</p>}

          <div className="tabs" role="tablist" aria-label="Player sections">
            {tabs.map(([id, label]) => (
              <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>

          {tab === 'live' && (
            <div className="section">
              {card.live.is_live ? (
                <LiveScoreboard live={card.live} playerName={p.name.split(' ').at(-1) ?? p.name} />
              ) : (
                <div className="card card-pad row">
                  <div className="stack">
                    <span className="title">Not playing right now</span>
                    <span className="small muted">{card.freshness?.live ? `Checked ${relative(card.freshness.live)}` : ''}</span>
                  </div>
                </div>
              )}
              <div className="row">
                <span className="tiny muted">{refreshing ?? 'Live games refresh on their own about every minute.'}</span>
                <button type="button" className="btn" onClick={refresh} disabled={Boolean(refreshing)}><RefreshIcon width={16} height={16} />Refresh</button>
              </div>
              <span className="eyebrow" style={{ marginTop: 8 }}>Up next</span>
              <UpcomingList items={(card.upcoming ?? []).slice(0, 2)} empty="No upcoming games found." />
            </div>
          )}
          {tab === 'fixtures' && <UpcomingList items={card.upcoming ?? []} empty="No upcoming games found." />}
          {tab === 'news' && <NewsList items={card.news ?? []} empty="No recent news found." />}
          {tab === 'stats' && <StatGrid stats={card.season_stats} />}
          {tab === 'results' && <ResultsList items={card.recent_results} />}

          <p className="tiny muted" style={{ margin: 0 }}>
            Page built {relative(card.built_at)} from {card.sources?.length ?? 0} sources. Live data can lag the game by a minute or two.
          </p>
        </>
      )}
    </>
  )
}

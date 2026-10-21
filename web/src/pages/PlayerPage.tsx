import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAccount } from '../account'
import { APP_NAME } from '../app'
import { useActivity } from '../activity'
import { api, subscribe, type Card, type Feed, type StreamEvent } from '../api'
import { useNow } from '../clock'
import { BuildProgress, formMarks, FormStrip, highlight, LineStats, LiveScoreboard, NewsList, NextCard, PlayerBand, SectionHead, SourceNote, StatGrid, UpcomingList } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { FeedTab, FromTheFans, useFeed } from '../components/Feed'
import { BackIcon, BellIcon, CheckIcon, PlusIcon, RefreshIcon } from '../components/Icons'
import { ResultsHistory, StatsWithForm } from '../components/Results'
import { ShareButton } from '../components/Share'
import { day, outcome, playedOn, relative, safeHref, shortName } from '../format'
import { bareScore, lastChecked, liveLag, liveUpdates, newsOf, nextGame, pendingLine, profileFacts } from '../lines'
import { sharePlayer } from '../share'
import { sportName } from '../sports'
import { useFollowing } from '../following'
import { AlertLevels, LEVELS, levelText } from '../notifications'

type Tab = 'overview' | 'matches' | 'stats' | 'feed'
// ?tab=feed opens the Feed (Today's "From your players" links there).
const firstTab = (asked: string | null): Tab => (asked === 'feed' ? 'feed' : 'overview')

/**
 * One player's page, always from the full card: the Following list only carries summaries (no stats,
 * sources or results), so it can't stand in for the page. Its updates come over the fan's one stream
 * for a player they follow, and over the player's own stream otherwise.
 */
export function PlayerPage() {
  const params = useParams()
  const playerId = /^\d+$/.test(params.id ?? '') ? Number(params.id) : NaN
  const navigate = useNavigate()
  const [search] = useSearchParams()
  const asked = search.get('tab')
  const following = useFollowing()
  const isFollowing = following.isFollowing(playerId)
  const { listen } = following
  const research = useAccount().config?.research !== false  // without the agent a failed page isn't tried again
  const [card, setCard] = useState<Card | null>(null)
  const [shown, setShown] = useState<number | null>(null)  // the player whose card has loaded
  const version = useRef(0)
  version.current = card?.version ?? 0
  const [progress, setProgress] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)  // a follow, retry or alert change the server refused
  const [tab, setTab] = useState<Tab>(() => firstTab(asked))
  const [matches, setMatches] = useState<MatchesView | null>(null)
  const [alertsOpen, setAlertsOpen] = useState(false)
  const { muted, setMuted } = useActivity()
  const [busy, setBusy] = useState(false)
  const now = useNow()

  useEffect(() => {
    setCard(null)
    setShown(null)
    setProgress([])
    setError(null)
    setFailure(null)
    setTab(firstTab(asked))
    setMatches(null)
    if (!Number.isFinite(playerId)) {
      setError('No such player.')
      return
    }
    let cancelled = false
    api.card(playerId).then((c) => {
      if (cancelled) return
      setCard(c)
      setShown(playerId)
    }).catch((e: Error) => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [playerId])  // not on asked: that only picks the tab a new page opens on
  useEffect(() => { if (asked === 'feed') setTab('feed') }, [asked])  // a link to this page's Feed, while on it

  // Once the card is in. A followed player's updates ride the fan stream the app holds anyway: a
  // second stream per page would let three tabs use all six of a browser's HTTP/1.1 connections.
  // Either stream starts with how things are now, so every reconnect catches the page up.
  useEffect(() => {
    if (shown !== playerId) return
    let stale = false
    const take = (c: Card) => {
      setCard((prev) => (!prev || c.version >= prev.version ? c : prev))
      if (c.status !== 'building') setProgress([])
    }
    const onEvent = (e: StreamEvent) => {
      if (e.type === 'following') {
        // The fan stream (re)connected. Its cards are summaries: a newer one is fetched in full.
        const f = e.players.find((c) => c.player_id === playerId)
        if (f && f.version > version.current) api.card(playerId).then((c) => { if (!stale) take(c) }).catch(() => {})
      } else if (e.type === 'card' && e.player_id === playerId) take(e.card)
      else if (e.type === 'progress' && e.player_id === playerId) setProgress((p) => [...p, e.message].slice(-8))
      else if (e.type === 'moved' && e.from === playerId) navigate(`/player/${e.to}`, { replace: true })
    }
    const stop = isFollowing ? listen(onEvent) : subscribe(`/api/players/${playerId}/stream`, onEvent)
    return () => { stale = true; stop() }
  }, [shown, playerId, isFollowing, listen, navigate])

  // The Feed reads rows, never a model: it loads with the page, for Overview's "From the fans" and the tab.
  const feed = useFeed(playerId, card?.player.name ?? '', (card ? newsOf(card) : []).map((n) => n.url).join(' '))

  if (error) return <div className="error-box">{error}</div>
  if (!card) return <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>

  const p = card.player
  const retired = p.status === 'retired'
  const level = following.byId(playerId)?.alerts ?? card.alerts ?? 'key'
  // A follow, retry or alert change the server turns down says why, under the band.
  const attempt = (work: () => Promise<unknown>) => {
    setFailure(null)
    return work().catch((e: Error) => setFailure(e.message))
  }

  const retry = () => attempt(async () => setCard(await following.follow({ player_id: playerId })))

  const toggleFollow = async () => {
    setBusy(true)
    await attempt(async () => {
      if (isFollowing) { await following.unfollow(playerId); setAlertsOpen(false) }
      else await following.follow({ player_id: playerId })
    })
    setBusy(false)
  }

  const tabs: [Tab, string][] = retired
    ? [['overview', 'Overview'], ['stats', 'Career'], ['matches', 'Results'], ['feed', 'Feed']]
    : [['overview', 'Overview'], ['matches', 'Matches'], ['stats', 'Stats'], ['feed', 'Feed']]

  return (
    <>
      <PlayerBand
        name={p.name}
        sport={p.sport}
        photo={p.photo}
        meta={[...(p.teams ?? []).slice(0, 2), p.role, retired && 'Retired'].filter(Boolean).join(' · ')}
        back={<Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>}
        actions={
          <>
            <ShareButton {...sharePlayer(playerId, p.name, APP_NAME)} />
            {isFollowing && (
              <button type="button" className="btn icon" aria-label={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} aria-expanded={alertsOpen} onClick={() => setAlertsOpen((o) => !o)}>
                <BellIcon width={20} height={20} />
              </button>
            )}
            <button type="button" className="btn" onClick={toggleFollow} disabled={busy} aria-pressed={isFollowing}>
              {isFollowing ? <><CheckIcon width={16} height={16} />Following</> : <><PlusIcon width={16} height={16} />Follow</>}
            </button>
          </>
        }
      >
        {alertsOpen && isFollowing && (
          <div className="alert-panel" style={{ color: 'var(--text)' }}>
            <span className="row">
              <span className="title">Alerts for {shortName(p.name)}</span>
              <Link to="/notifications?tab=settings" className="link small">Settings</Link>
            </span>
            <AlertLevels label={`Alerts for ${p.name}`} value={level} onChange={(l) => { attempt(() => following.setAlerts(playerId, l)) }} />
            <span className="small muted">{levelText(level, p.sport).hint}</span>
            {card.live.is_live && card.live.event_id != null && (() => {
              const eventId = card.live.event_id
              const on = muted.has(eventId)
              return (
                <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} aria-pressed={on} onClick={() => { attempt(() => setMuted(eventId, !on)) }}>
                  {on ? 'Unmute this game' : 'Mute this game'}
                </button>
              )
            })()}
          </div>
        )}
      </PlayerBand>
      {failure && <div className="error-box" role="alert">{failure}</div>}

      {card.status !== 'ready' ? (
        <BuildProgress card={card} messages={progress} onRetry={research ? retry : undefined} research={research} />
      ) : (
        <>
          {card.pending && (
            <p className="small muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="spinner" />{pendingLine(card)}
            </p>
          )}
          {card.pending_error && <p className="small muted">News and background couldn't be gathered: {card.pending_error}</p>}

          <div className="tabs" role="tablist" aria-label="Player sections">
            {tabs.map(([id, label]) => (
              <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => { setTab(id); setMatches(null) }}>{label}</button>
            ))}
          </div>

          <ErrorBoundary fallback={<BlockError />} resetKey={`${tab}:${card.version}`}>
            {tab === 'overview' && <Overview card={card} feed={feed.feed} show={(t, view) => { setTab(t); setMatches(view ?? null) }} onError={setFailure} />}
            {tab === 'matches' && <Matches card={card} view={matches} setView={setMatches} />}
            {tab === 'stats' && <div className="section"><StatsWithForm card={card} /></div>}
            {tab === 'feed' && <FeedTab feed={feed.feed} error={feed.error} name={p.name} sport={p.sport} photo={p.photo} now={now} onHide={feed.hide} />}
          </ErrorBoundary>

          <p className="tiny muted">
            {/* A page from a live source alone (no research agent yet, or none in this build) was never "built". */}
            {card.built_at && `Page built ${relative(card.built_at, now)} from ${card.sources?.length ?? 0} sources. `}{liveLag(card)} <Link to="/credits" className="link">Credits</Link>
          </p>
        </>
      )}
    </>
  )
}

/** The first tab: what's happening now or next, the last game, form, the key numbers and the news. */
function Overview({ card, feed, show, onError }: { card: Card; feed: Feed | null; show: (t: Tab, view?: MatchesView) => void; onError: (message: string) => void }) {
  // Without the research agent (GET /api/config) a page no source updates stays as it was found.
  const research = useAccount().config?.research !== false
  const p = card.player
  const retired = p.status === 'retired'
  const now = useNow()
  const next = nextGame(card, now)
  const checked = lastChecked(card)
  const results = card.recent_results ?? []
  const marks = formMarks(results, p.teams)
  const last = results[0]
  const wl = last && outcome(last.result, p.teams)
  const word = last && highlight(p.sport, last.player_contribution)
  const { muted, setMuted } = useActivity()
  // "Won 133–128" is a score once the badge says won; "West Indies won by 5 wkts" stays a sentence.
  const score = last && bareScore(last.result)
  const facts = profileFacts(p)

  return (
    <>
      {card.live.is_live ? (
        <>
          <LiveScoreboard live={card.live} playerName={shortName(p.name)} sport={p.sport} teams={p.teams} />
          {card.live.event_id != null && muted.has(card.live.event_id) && (
            <span className="row small" style={{ justifyContent: 'flex-start', gap: 8 }}>
              <span className="muted">Alerts from this game are muted.</span>
              <button type="button" className="btn link" onClick={() => { setMuted(card.live.event_id!, false).catch((e: Error) => onError(e.message)) }}>Unmute</button>
            </span>
          )}
          <span className="row tiny muted">
            <span>{liveUpdates(card)}</span>
            <RefreshButton card={card} style={{ flexShrink: 0 }} />
          </span>
        </>
      ) : retired ? null : next ? (
        <NextCard event={next} sport={p.sport}><RefreshButton card={card} /></NextCard>
      ) : card.live.available === false ? (
        <div className="card card-pad stack">
          <span className="title">No live scores for {sportName(p.sport) || 'this sport'} yet</span>
          <span className="small muted">{research ? 'News, results and upcoming games on this page update a few times a day.' : "News, results and upcoming games on this page are as they were found, and aren't updated now."}</span>
        </div>
      ) : (
        <div className="card card-pad row">
          <span className="stack">
            <span className="title">Not playing right now</span>
            <span className="small muted">{checked ? `Checked ${relative(checked, now)}` : 'No upcoming games found'}</span>
          </span>
          <RefreshButton card={card} style={{ flexShrink: 0 }} />
        </div>
      )}
      {!card.live.is_live && !retired && next && card.live.available === false && (
        <p className="tiny muted">No live scores for {sportName(p.sport) || 'this sport'} yet: {research ? 'results and news update a few times a day.' : "results and news aren't updated now."}</p>
      )}

      {last && (
        <section className="game-card" aria-label="Last game">
          <span className="row">
            <span className="eyebrow">Last game</span>
            <span className="tiny muted" style={{ fontWeight: 700, textAlign: 'right' }}>{[last.competition, day(playedOn(last))].filter(Boolean).join(' · ')}</span>
          </span>
          <span className="row" style={{ justifyContent: 'flex-start' }}>
            {wl && <span className={`wl ${wl}`} role="img" aria-label={{ W: 'Won', D: 'Drew', L: 'Lost' }[wl]}>{wl}</span>}
            <span className="title" style={{ flex: 1 }}>{last.title}</span>
            {score && <span className="num" style={{ fontSize: 26, whiteSpace: 'nowrap' }}>{score}</span>}
          </span>
          {!score && <span className="small" style={{ fontWeight: 800 }}>{last.result}</span>}
          <LineStats text={last.player_contribution} sport={p.sport} />
          <span className="row">
            {word ? <span className="pill outline">{word}</span> : <span />}
            <button type="button" className="btn link" onClick={() => show('matches', 'results')}>All results</button>
          </span>
        </section>
      )}

      <FromTheFans feed={feed} name={p.name} sport={p.sport} now={now} onAll={() => show('feed')} />

      {marks.length >= 3 && (
        <div className="row card card-pad">
          <span className="stack" style={{ gap: 2 }}>
            <span className="eyebrow">Form</span>
            <span className="small" style={{ fontWeight: 700 }}>Last {marks.length}, newest first</span>
          </span>
          <FormStrip marks={marks} />
        </div>
      )}

      {card.season_stats && card.season_stats.length > 0 && (
        <section className="section" aria-label="Key numbers">
          <SectionHead title={retired ? 'Career' : 'Numbers'}>
            <button type="button" className="btn link" onClick={() => show('stats')}>All stats</button>
          </SectionHead>
          <StatGrid stats={card.season_stats.slice(0, 6)} />
          <SourceNote provenance={card.provenance?.season_stats} note={card.season_stats_note} />
        </section>
      )}

      {newsOf(card).length > 0 && (
        <section className="section" aria-label="News">
          <SectionHead title="News">
            {newsOf(card).length > 2 && <button type="button" className="btn link" onClick={() => show('feed')}>All news</button>}
          </SectionHead>
          <NewsList items={newsOf(card).slice(0, 2)} brief empty="" />
        </section>
      )}

      {(p.summary || facts.length > 0) && (
        <section className="section" aria-label="About">
          <SectionHead title="About" />
          {facts.length > 0 && <p className="small muted">{facts.join(' · ')}</p>}
          {p.summary && <p style={{ maxWidth: '70ch' }}>{p.summary}</p>}
          {p.summary && p.about && (
            <p className="tiny muted">
              From {safeHref(p.about.url) ? <a href={safeHref(p.about.url)} target="_blank" rel="noreferrer" className="link">{p.about.source}</a> : p.about.source}
              {', '}<a href={safeHref(p.about.licence_url)} target="_blank" rel="noreferrer license" className="link">{p.about.licence}</a>
            </p>
          )}
          {p.disambiguation && <p className="tiny muted">{p.disambiguation}</p>}
        </section>
      )}
    </>
  )
}

/** Ask the source again, for when a game should be on and isn't showing. */
function RefreshButton({ card, style }: { card: Card; style?: CSSProperties }) {
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const refresh = async () => {
    setRefreshing('Checking…')
    try {
      const r = await api.refresh(card.player_id)
      setRefreshing(r.queued ? (card.provenance ? 'Re-reading fixtures and stats…' : 'Checking the live score…') : r.reason ?? null)
    } catch (e) {
      setRefreshing((e as Error).message)
    }
    setTimeout(() => setRefreshing(null), 45000)
  }
  return (
    <button type="button" className="btn link" style={{ color: 'inherit', display: 'inline-flex', gap: 6, ...style }} onClick={refresh} disabled={Boolean(refreshing)}>
      <RefreshIcon width={14} height={14} />{refreshing ?? 'Refresh'}
    </button>
  )
}

type MatchesView = 'upcoming' | 'results'

/** Fixtures and results, one at a time, results when asked for. With no fixtures, a line says so above the results. */
function Matches({ card, view: chosen, setView }: { card: Card; view: MatchesView | null; setView: (v: MatchesView) => void }) {
  const retired = card.player.status === 'retired'
  if (retired || !(card.upcoming ?? []).length) {
    return (
      <div className="section">
        {!retired && <p className="small muted">No games scheduled.</p>}
        <ResultsHistory card={card} />
      </div>
    )
  }
  const view = chosen ?? 'upcoming'
  return (
    <div className="section">
      <div className="segmented" role="group" aria-label="Which matches">
        <button type="button" aria-pressed={view === 'upcoming'} onClick={() => setView('upcoming')}>Upcoming</button>
        <button type="button" aria-pressed={view === 'results'} onClick={() => setView('results')}>Results</button>
      </div>
      {view === 'upcoming' ? (
        <>
          <UpcomingList items={card.upcoming ?? []} empty="No upcoming games found." />
          <SourceNote provenance={card.provenance?.upcoming} />
        </>
      ) : <ResultsHistory card={card} />}
    </div>
  )
}

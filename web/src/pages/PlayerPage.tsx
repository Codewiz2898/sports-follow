import { useEffect, useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, subscribe, type Card, type UpcomingEvent } from '../api'
import { BuildProgress, formMarks, FormStrip, highlight, LineStats, LiveScoreboard, NewsList, PlayerBand, SectionHead, SourceNote, StatGrid, UpcomingList } from '../components/Blocks'
import { BackIcon, BellIcon, CheckIcon, PlusIcon, RefreshIcon } from '../components/Icons'
import { ResultsHistory, StatsWithForm } from '../components/Results'
import { countdown, dateTime, day, outcome, parseDate, relative, shortName, sportKey, sportName } from '../format'
import { useFollowing } from '../following'
import { AlertLevels, LEVELS } from '../notifications'

type Tab = 'overview' | 'matches' | 'stats' | 'news'

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
  const [tab, setTab] = useState<Tab>('overview')
  const [matches, setMatches] = useState<MatchesView | null>(null)
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!Number.isFinite(playerId)) return
    setCard(null)
    setProgress([])
    setError(null)
    setTab('overview')
    setMatches(null)
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

  if (error) return <div className="error-box">{error}</div>
  if (!card) return <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted">Loading…</span></div>

  const p = card.player
  const retired = p.status === 'retired'
  const level = following.byId(playerId)?.alerts ?? card.alerts ?? 'key'

  const retry = async () => {
    const fresh = await following.follow({ player_id: playerId })
    setCard(fresh)
  }

  const toggleFollow = async () => {
    setBusy(true)
    try {
      if (isFollowing) { await following.unfollow(playerId); setAlertsOpen(false) }
      else await following.follow({ player_id: playerId })
    } finally {
      setBusy(false)
    }
  }

  const tabs: [Tab, string][] = retired
    ? [['overview', 'Overview'], ['stats', 'Career'], ['matches', 'Results'], ['news', 'News']]
    : [['overview', 'Overview'], ['matches', 'Matches'], ['stats', 'Stats'], ['news', 'News']]

  return (
    <>
      <PlayerBand
        name={p.name}
        sport={p.sport}
        meta={[...(p.teams ?? []).slice(0, 2), p.role, retired && 'Retired'].filter(Boolean).join(' · ')}
        back={<Link to="/" className="btn icon" aria-label="Back to Today"><BackIcon width={20} height={20} /></Link>}
        actions={
          <>
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
              <Link to="/notifications" className="link small">Settings</Link>
            </span>
            <AlertLevels label={`Alerts for ${p.name}`} value={level} onChange={(l) => following.setAlerts(playerId, l)} />
            <span className="small muted">{LEVELS.find((l) => l.value === level)?.hint}</span>
          </div>
        )}
      </PlayerBand>

      {card.status !== 'ready' ? (
        <BuildProgress card={card} messages={progress} onRetry={retry} />
      ) : (
        <>
          {card.pending && (
            <p className="small muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="spinner" />Scores and fixtures are live. Still gathering news and background, about two minutes.
            </p>
          )}
          {card.pending_error && <p className="small muted">News and background couldn't be gathered: {card.pending_error}</p>}

          <div className="tabs" role="tablist" aria-label="Player sections">
            {tabs.map(([id, label]) => (
              <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => { setTab(id); setMatches(null) }}>{label}</button>
            ))}
          </div>

          {tab === 'overview' && <Overview card={card} show={(t, view) => { setTab(t); setMatches(view ?? null) }} />}
          {tab === 'matches' && <Matches card={card} view={matches} setView={setMatches} />}
          {tab === 'stats' && <div className="section"><StatsWithForm card={card} /></div>}
          {tab === 'news' && <NewsList items={card.news ?? []} empty="No recent news found." />}

          <p className="tiny muted">
            Page built {relative(card.built_at)} from {card.sources?.length ?? 0} sources. Live data can lag the game by a minute or two. <Link to="/credits" className="link">Credits</Link>
          </p>
        </>
      )}
    </>
  )
}

/** The next game that hasn't started (a fixture with no time yet counts). */
function nextGame(card: Card) {
  const now = Date.now()
  return (card.upcoming ?? []).find((u) => (parseDate(u.start_utc)?.getTime() ?? Infinity) > now)
}

/** The first tab: what's happening now or next, the last game, form, the key numbers and the news. */
function Overview({ card, show }: { card: Card; show: (t: Tab, view?: MatchesView) => void }) {
  const p = card.player
  const retired = p.status === 'retired'
  const next = nextGame(card)
  const results = card.recent_results ?? []
  const marks = formMarks(results, p.teams)
  const last = results[0]
  const wl = last && outcome(last.result, p.teams)
  const word = last && highlight(p.sport, last.player_contribution)
  // "Won 133–128" is a score once the badge says won; "West Indies won by 5 wkts" stays a sentence.
  const score = last && /^(Won|Lost|Drew)\s+/.test(last.result) ? last.result.replace(/^(Won|Lost|Drew)\s+/, '') : null

  return (
    <>
      {card.live.is_live ? (
        <>
          <LiveScoreboard live={card.live} playerName={shortName(p.name)} sport={p.sport} />
          <span className="row tiny muted">
            <span>{card.provenance ? `Scores update live from ${card.provenance.upcoming?.source ?? 'the source'} while the game is on.` : 'Live games refresh on their own about every minute.'}</span>
            <RefreshButton card={card} style={{ flexShrink: 0 }} />
          </span>
        </>
      ) : retired ? null : next ? (
        <NextCard card={card} next={next} />
      ) : card.live.available === false ? (
        <div className="card card-pad stack">
          <span className="title">No live scores for {sportName(p.sport) || 'this sport'} yet</span>
          <span className="small muted">News, results and upcoming games on this page update a few times a day.</span>
        </div>
      ) : (
        <div className="card card-pad row">
          <span className="stack">
            <span className="title">Not playing right now</span>
            <span className="small muted">{card.freshness?.live ? `Checked ${relative(card.freshness.live)}` : 'No upcoming games found'}</span>
          </span>
          <RefreshButton card={card} style={{ flexShrink: 0 }} />
        </div>
      )}
      {!card.live.is_live && !retired && next && card.live.available === false && (
        <p className="tiny muted">No live scores for {sportName(p.sport) || 'this sport'} yet: results and news update a few times a day.</p>
      )}

      {last && (
        <section className="game-card" aria-label="Last game">
          <span className="row">
            <span className="eyebrow">Last game</span>
            <span className="tiny muted" style={{ fontWeight: 700, textAlign: 'right' }}>{[last.competition, last.date && day(last.date)].filter(Boolean).join(' · ')}</span>
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

      {(card.news ?? []).length > 0 && (
        <section className="section" aria-label="News">
          <SectionHead title="News">
            {(card.news ?? []).length > 2 && <button type="button" className="btn link" onClick={() => show('news')}>All news</button>}
          </SectionHead>
          <NewsList items={(card.news ?? []).slice(0, 2)} brief empty="" />
        </section>
      )}

      {p.summary && (
        <section className="section" aria-label="About">
          <SectionHead title="About" />
          <p style={{ maxWidth: '70ch' }}>{p.summary}</p>
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

/** The next game, an ink card with a countdown. */
function NextCard({ card, next }: { card: Card; next: UpcomingEvent }) {
  const left = countdown(next.start_utc)

  return (
    <section className="next-card" data-sport={sportKey(card.player.sport)} aria-label="Next game">
      <span className="row">
        <span className="eyebrow" style={{ color: 'var(--sport-on-ink)' }}>Next game</span>
        {next.competition && <span className="tiny muted" style={{ fontWeight: 700, textAlign: 'right' }}>{next.competition}</span>}
      </span>
      <span className="row" style={{ alignItems: 'flex-end' }}>
        <span className="title" style={{ fontSize: 20 }}>{next.title}</span>
        {left && <span className="dsp count">{left}</span>}
      </span>
      <span className="row small" style={{ paddingTop: 12, borderTop: '1px solid rgba(255,255,255,.16)', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700 }}>{[next.start_utc ? dateTime(next.start_utc) : 'Date to be confirmed', next.venue].filter(Boolean).join(' · ')}</span>
        <RefreshButton card={card} />
      </span>
    </section>
  )
}

type MatchesView = 'upcoming' | 'results'

/** Fixtures and results, one at a time; results first when there are no fixtures, or when asked for. */
function Matches({ card, view: chosen, setView }: { card: Card; view: MatchesView | null; setView: (v: MatchesView) => void }) {
  const retired = card.player.status === 'retired'
  const view = chosen ?? (retired || !(card.upcoming ?? []).length ? 'results' : 'upcoming')
  return (
    <div className="section">
      {!retired && (
        <div className="segmented" role="group" aria-label="Which matches">
          <button type="button" aria-pressed={view === 'upcoming'} onClick={() => setView('upcoming')}>Upcoming</button>
          <button type="button" aria-pressed={view === 'results'} onClick={() => setView('results')}>Results</button>
        </div>
      )}
      {view === 'upcoming' ? (
        <>
          <UpcomingList items={card.upcoming ?? []} empty="No upcoming games found." />
          <SourceNote provenance={card.provenance?.upcoming} />
        </>
      ) : <ResultsHistory card={card} />}
    </div>
  )
}

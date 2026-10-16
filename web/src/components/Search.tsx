import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type Research, type SearchResponse, type SearchResult } from '../api'
import { APP_NAME } from '../app'
import { followTarget } from '../follow'
import { allowance, exactName as namesakesName, highlightParts, liveNote as sportsNote, nextLine, OTHER_SCRIPT, sections as sectionsOf, type Section } from '../search'
import { runningSports, sportInfo } from '../sports'
import { useFollowing } from '../following'
import { Avatar } from './Blocks'
import { CheckIcon, PlusIcon, SparkIcon } from './Icons'

/**
 * Search as you type: one request per 250 ms pause, the previous one cancelled. Keeps the last answer
 * on screen while the next loads; `fresh` says it answers what's typed now, so Enter never acts on the
 * previous query's results.
 */
export function useSearch(query: string, sport: string) {
  const [answer, setAnswer] = useState<{ asked: string; data: SearchResponse } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim()
  const asked = `${sport}|${q}`

  useEffect(() => {
    if (q.length < 2) {
      setAnswer(null)
      setLoading(false)
      setError(null)
      return
    }
    const ctrl = new AbortController()
    setLoading(true)
    const t = setTimeout(() => {
      api.search(q, sport || null, ctrl.signal)
        .then((r) => { setAnswer({ asked: `${sport}|${q}`, data: r }); setError(null) })
        .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    }, 250)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q, sport])

  const data = q.length >= 2 ? answer?.data ?? null : null
  return { data, fresh: data != null && answer?.asked === asked, loading, error }
}

export type { Section }

/** The order results are shown (and arrowed through) in (search.ts). */
export function sections(data: SearchResponse): Section[] {
  return sectionsOf(data, APP_NAME)
}

/** Opening and following results, the same from the search page, the sidebar and a preview. */
export function useSearchActions(onDone?: () => void) {
  const following = useFollowing()
  const navigate = useNavigate()
  const location = useLocation()
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)

  const open = useCallback((r: SearchResult) => {
    const state = { result: r, back: `${location.pathname}${location.search}` }
    if (r.player_id) navigate(`/player/${r.player_id}`)
    else if (r.system && r.athlete_id) {
      const league = r.league ? `?league=${encodeURIComponent(r.league)}` : ''
      navigate(`/athlete/${r.system}/${r.athlete_id}${league}`, { state })
    } else if (r.qid) navigate(`/athlete/wikidata/${r.qid}?sport=${encodeURIComponent(r.sport.toLowerCase())}`, { state })
    else return
    onDone?.()
  }, [navigate, location.pathname, location.search, onDone])

  const run = useCallback(async (key: string, go: () => ReturnType<typeof following.follow>) => {
    // One follow or research at a time: a second press (a held Shift+Enter) must not send another.
    if (inFlight.current) return
    inFlight.current = true
    setError(null)
    setPending(key)
    try {
      const card = await go()
      onDone?.()
      navigate(`/player/${card.player_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      inFlight.current = false
      setPending(null)
    }
  }, [navigate, onDone])

  const follow = useCallback((r: SearchResult) => run(r.key, () => following.follow(followTarget(r))), [following, run])

  const research = useCallback((query: string) => run(`research:${query}`, () => following.follow({ query })), [following, run])

  return { open, follow, research, pending, error, isFollowing: following.isFollowing }
}

/** The name with the typed start of each word in bold: "Caitl" in Caitlin Clark (search.ts). */
export function Highlight({ name, query }: { name: string; query: string }) {
  return <>{highlightParts(name, query).map((p, i) => (p.bold ? <b key={i} className="hl">{p.text}</b> : <span key={i}>{p.text}</span>))}</>
}

export function ResultRow({ r, query, active, actions, id }: { r: SearchResult; query: string; active?: boolean; actions: ReturnType<typeof useSearchActions>; id?: string }) {
  const following = r.following || (r.player_id != null && actions.isFollowing(r.player_id))
  const next = nextLine(r)
  const openable = Boolean(r.player_id || (r.system && r.athlete_id) || r.qid)
  const busy = actions.pending === r.key
  const body = (
    <>
      <Avatar name={r.name} sport={r.sport} />
      <span className="stack">
        <span className="title"><Highlight name={r.name} query={query} /></span>
        <span className="small muted">
          {/* Found by another name ("jonathan" is Cije's real name, Christian Jonathan): say which. */}
          {r.aka && <><Highlight name={r.aka} query={query} />{' · '}</>}
          {[r.sport, r.detail, r.followers ? `${r.followers} following` : ''].filter(Boolean).join(' · ')}
        </span>
        {next && <span className="tiny" style={{ color: next.live ? 'var(--live-text)' : 'var(--text)', fontWeight: 800 }}>{next.text}</span>}
        {!r.live_scores && !r.player_id && <span className="tiny muted">AI research · no live scores yet</span>}
      </span>
    </>
  )
  return (
    <li className={`result${active ? ' active' : ''}`} id={id}>
      {openable
        ? <button type="button" className="result-main" onClick={() => actions.open(r)}>{body}</button>
        : <div className="result-main">{body}</div>}
      {following ? (
        <span className="btn" aria-disabled="true"><CheckIcon width={16} height={16} />Following</span>
      ) : (
        <button type="button" className={`btn${r.live_scores || r.player_id ? ' primary' : ''}`} disabled={Boolean(actions.pending)} onClick={() => actions.follow(r)} aria-label={`${r.live_scores || r.player_id ? 'Follow' : 'Research'} ${r.name}`}>
          {busy ? <span className="spinner" /> : r.live_scores || r.player_id ? <PlusIcon width={16} height={16} /> : <SparkIcon width={16} height={16} />}
          {r.live_scores || r.player_id ? 'Follow' : 'Research'}
        </button>
      )}
    </li>
  )
}

/** The research agent, offered last: any sport, 1 to 3 minutes, a few a day per fan. */
export function ResearchOffer({ query, research, full, actions }: { query: string; research?: Research; full?: boolean; actions: ReturnType<typeof useSearchActions> }) {
  const q = query.trim()
  const { out, text: left } = allowance(research)
  const busy = actions.pending === `research:${q}`
  if (!full) {
    return (
      <div className="research-link">
        <button type="button" className="btn link" disabled={out || Boolean(actions.pending)} onClick={() => actions.research(q)}>
          {busy ? <span className="spinner" /> : <SparkIcon width={18} height={18} />}Not here? Research “{q}” with AI
        </button>
        <span className="tiny muted">Any sport · 1 to 3 minutes{left ? ` · ${left}` : ''}</span>
      </div>
    )
  }
  return (
    <div className="card card-pad research-card">
      <div className="row" style={{ justifyContent: 'flex-start', gap: 10 }}>
        <SparkIcon width={22} height={22} />
        <span className="dsp" style={{ fontSize: 26 }}>Research it with AI</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>Our research agent searches the web for “{q}”, works out who it is and builds the page. It takes 1 to 3 minutes and works for any sport.</p>
      <p className="small muted" style={{ margin: 0 }}>{liveNote()}</p>
      <button type="button" className="btn primary" disabled={out || Boolean(actions.pending)} onClick={() => actions.research(q)}>
        {busy && <span className="spinner" />}Research “{q}”
      </button>
      {left && <span className="tiny muted" style={{ textAlign: 'center' }}>{left}</span>}
    </div>
  )
}

/** Everything under the search box: namesake picker, sections, did-you-mean and the AI offer. */
export function SearchResults({ data, query, loading, actions, activeKey, idFor }: {
  data: SearchResponse
  query: string
  loading?: boolean
  actions: ReturnType<typeof useSearchActions>
  activeKey?: string
  idFor?: (r: SearchResult) => string
}) {
  const parts = sections(data)
  const exactName = namesakesName(data)
  const nothing = !data.results.length
  return (
    <div className="search-results" aria-busy={loading || undefined}>
      {data.namesakes > 1 && (
        <div className="stack" style={{ gap: 6 }}>
          <h2 className="dsp" style={{ fontSize: 32 }}>{data.namesakes === 2 ? 'Two' : data.namesakes} athletes are called {exactName}</h2>
          <p className="small muted" style={{ margin: 0 }}>Pick the one you mean. We follow exactly that player, so their scores are never mixed up with another's.</p>
        </div>
      )}
      {nothing && data.suggestions.length > 0 && (
        <p className="small muted" style={{ margin: 0 }}>No athlete in the live sources is called “{data.query}”. The closest spelling:</p>
      )}
      {nothing && !data.suggestions.length && !loading && (
        <p className="small muted" style={{ margin: 0 }}>No athlete in the live sources is called “{data.query}”.</p>
      )}
      {parts.map((s) => (
        <section key={s.key} className="section" aria-label={s.title || 'Athletes with this name'}>
          {s.title && <span className="eyebrow">{s.title}</span>}
          <ul className="card list results">
            {s.items.map((r) => <ResultRow key={r.key} r={r} query={query} active={activeKey === r.key} actions={actions} id={idFor?.(r)} />)}
          </ul>
        </section>
      ))}
      {data.partial.length > 0 && (
        <p className="tiny muted" style={{ margin: 0 }}>{data.partial.join(' and ')} didn't answer in time, so some athletes may be missing. Keep typing or try again.</p>
      )}
      {data.researchable
        ? <ResearchOffer query={data.query} research={data.research} full={nothing} actions={actions} />
        : OTHER_SCRIPT.test(data.query) && <p className="small muted" style={{ margin: 0 }}>Search reads names in Latin letters only: type the name as it's spelled in English, like “Virat Kohli”.</p>}
    </div>
  )
}

/** Which sports have live scores here (each from its sources; see Credits), and what the rest get. */
function liveNote(): string {
  return sportsNote(runningSports())
}

export function SportChips({ sport, onChange }: { sport: string; onChange: (s: string) => void }) {
  return (
    <div className="chips" role="group" aria-label="Sport">
      {/* The sports this build runs (GET /api/sports), after All. */}
      {[{ key: '', label: 'All' }, ...runningSports()].map(({ key, label }) => (
        <button key={key || 'all'} type="button" className="chip" data-sport={key ? sportInfo(key).key : 'other'} aria-pressed={sport === key} onClick={() => onChange(key)}>
          {key && <span className="dot" />}{label}
        </button>
      ))}
    </div>
  )
}


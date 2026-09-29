import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type Research, type SearchResponse, type SearchResult } from '../api'
import { relative } from '../format'
import { useFollowing } from '../following'
import { Avatar } from './Blocks'
import { CheckIcon, PlusIcon, SparkIcon } from './Icons'

export const SPORTS: [string, string][] = [['', 'All'], ['football', 'Football'], ['cricket', 'Cricket'], ['basketball', 'Basketball'], ['tennis', 'Tennis'], ['chess', 'Chess']]

/** Search as you type: one request per 250 ms pause, the previous one cancelled. Keeps the last answer while the next loads. */
export function useSearch(query: string, sport: string) {
  const [data, setData] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim()

  useEffect(() => {
    if (q.length < 2) {
      setData(null)
      setLoading(false)
      setError(null)
      return
    }
    const ctrl = new AbortController()
    setLoading(true)
    const t = setTimeout(() => {
      api.search(q, sport || null, ctrl.signal)
        .then((r) => { setData(r); setError(null) })
        .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    }, 250)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q, sport])

  return { data: q.length >= 2 ? data : null, loading, error }
}

export interface Section { key: string; title: string; items: SearchResult[] }

/** The order results are shown (and arrowed through) in. */
export function sections(data: SearchResponse): Section[] {
  const { results } = data
  if (data.namesakes > 1) {
    return [
      { key: 'namesakes', title: '', items: results.filter((r) => r.exact) },
      { key: 'similar', title: 'Similar names', items: results.filter((r) => !r.exact) },
    ].filter((s) => s.items.length)
  }
  return [
    { key: 'ours', title: 'On Sports Follow', items: results.filter((r) => r.player_id) },
    { key: 'athletes', title: 'Athletes', items: results.filter((r) => !r.player_id && r.live_scores) },
    { key: 'other', title: 'Other sports · AI research only', items: results.filter((r) => !r.player_id && !r.live_scores) },
    { key: 'suggestions', title: 'Did you mean', items: data.suggestions },
  ].filter((s) => s.items.length)
}

/** Opening and following results, the same from the search page, the sidebar and a preview. */
export function useSearchActions(onDone?: () => void) {
  const following = useFollowing()
  const navigate = useNavigate()
  const location = useLocation()
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    setError(null)
    setPending(key)
    try {
      const card = await go()
      onDone?.()
      navigate(`/player/${card.player_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending(null)
    }
  }, [navigate, onDone])

  const follow = useCallback((r: SearchResult) => run(r.key, () => following.follow(
    r.player_id ? { player_id: r.player_id }
      : r.system && r.athlete_id ? { system: r.system, athlete_id: r.athlete_id, league: r.league, qid: r.qid }
        : r.qid ? { qid: r.qid, sport: r.sport.toLowerCase() }  // the server finds them in the live source
          : { query: r.name },  // a sport only the agent covers: this is an AI research
  )), [following, run])

  const research = useCallback((query: string) => run(`research:${query}`, () => following.follow({ query })), [following, run])

  return { open, follow, research, pending, error, isFollowing: following.isFollowing }
}

const fold = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()

/** The name with the typed start of each word in bold: "Caitl" in Caitlin Clark. */
export function Highlight({ name, query }: { name: string; query: string }) {
  const typed = fold(query).split(/[\s.'-]+/).filter(Boolean)
  return (
    <>
      {name.split(/(\s+)/).map((part, i) => {
        const word = fold(part)
        const hit = typed.filter((t) => word.startsWith(t)).sort((a, b) => b.length - a.length)[0]
        if (!hit || !part.trim()) return part
        let n = 0
        while (n < part.length && fold(part.slice(0, n)).length < hit.length) n++
        return <span key={i}><b className="hl">{part.slice(0, n)}</b>{part.slice(n)}</span>
      })}
    </>
  )
}

function nextLine(r: SearchResult): { text: string; live: boolean } | null {
  if (r.status === 'building') return { text: 'Page being set up…', live: false }
  if (r.next?.live) return { text: `Live · ${r.next.live}`, live: true }
  if (r.next?.title) return { text: `Next: ${r.next.title}${r.next.start_utc ? ` · ${relative(r.next.start_utc)}` : ''}`, live: false }
  return null
}

export function ResultRow({ r, query, active, actions, id }: { r: SearchResult; query: string; active?: boolean; actions: ReturnType<typeof useSearchActions>; id?: string }) {
  const following = r.following || (r.player_id != null && actions.isFollowing(r.player_id))
  const next = nextLine(r)
  const openable = Boolean(r.player_id || (r.system && r.athlete_id) || r.qid)
  const busy = actions.pending === r.key
  const body = (
    <>
      <Avatar name={r.name} />
      <span className="stack">
        <span className="title"><Highlight name={r.name} query={query} /></span>
        <span className="small muted">{[r.sport, r.detail, r.followers ? `${r.followers} following` : ''].filter(Boolean).join(' · ')}</span>
        {next && <span className="tiny" style={{ color: next.live ? 'var(--live)' : 'var(--action)', fontWeight: 600 }}>{next.text}</span>}
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

function resetTime(research: Research): string {
  return new Date(research.resets_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** The research agent, offered last: any sport, 1 to 3 minutes, a few a day per fan. */
export function ResearchOffer({ query, research, full, actions }: { query: string; research?: Research; full?: boolean; actions: ReturnType<typeof useSearchActions> }) {
  const q = query.trim()
  const out = research && research.left <= 0
  const allowance = research && (out
    ? `You've used today's ${research.limit} AI researches. More from ${resetTime(research)}.`
    : `${research.left} of ${research.limit} left today`)
  const busy = actions.pending === `research:${q}`
  if (!full) {
    return (
      <div className="research-link">
        <button type="button" className="btn link" disabled={Boolean(out || actions.pending)} onClick={() => actions.research(q)}>
          {busy ? <span className="spinner" /> : <SparkIcon width={18} height={18} />}Not here? Research “{q}” with AI
        </button>
        <span className="tiny muted">Any sport · 1 to 3 minutes{allowance ? ` · ${allowance}` : ''}</span>
      </div>
    )
  }
  return (
    <div className="card card-pad research-card">
      <div className="row" style={{ justifyContent: 'flex-start', gap: 10 }}>
        <SparkIcon width={22} height={22} style={{ color: 'var(--action)' }} />
        <span style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 700 }}>Research it with AI</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>Our research agent searches the web for “{q}”, works out who it is and builds the page. It takes 1 to 3 minutes and works for any sport.</p>
      <p className="small muted" style={{ margin: 0 }}>Live scores come from football, cricket, basketball, tennis and chess sources. Other sports get news and results from the agent.</p>
      <button type="button" className="btn research-btn" disabled={Boolean(out || actions.pending)} onClick={() => actions.research(q)}>
        {busy && <span className="spinner" />}Research “{q}”
      </button>
      {allowance && <span className="tiny muted" style={{ textAlign: 'center' }}>{allowance}</span>}
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
  const exactName = data.results.find((r) => r.exact)?.name ?? query
  const nothing = !data.results.length
  return (
    <div className="search-results" aria-busy={loading || undefined}>
      {data.namesakes > 1 && (
        <div className="stack" style={{ gap: 6 }}>
          <h2 style={{ fontFamily: 'var(--display)', fontSize: 24, lineHeight: 1.1 }}>{data.namesakes === 2 ? 'Two' : data.namesakes} athletes are called {exactName}</h2>
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
      <ResearchOffer query={data.query} research={data.research} full={nothing} actions={actions} />
    </div>
  )
}

export function SportChips({ sport, onChange }: { sport: string; onChange: (s: string) => void }) {
  return (
    <div className="chips" role="group" aria-label="Sport">
      {SPORTS.map(([value, label]) => (
        <button key={value || 'all'} type="button" className="chip" aria-pressed={sport === value} onClick={() => onChange(value)}>{label}</button>
      ))}
    </div>
  )
}


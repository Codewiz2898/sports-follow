import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SearchIcon } from '../components/Icons'
import { SearchResults, SportChips, sections, useSearch, useSearchActions } from '../components/Search'

const SUGGESTIONS = ['Virat Kohli', 'Magnus Carlsen', 'Caitlin Clark', 'Carlos Alcaraz', 'Nikola Jokić', 'Mohamed Salah']

/** Follow a player: search every source as you type, pick the exact athlete, or hand a name to the research agent. */
export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [sport, setSport] = useState(params.get('sport') ?? '')
  const { data, loading, error } = useSearch(query, sport)
  const actions = useSearchActions()

  // The query lives in the URL, so back from a preview returns to the same results.
  useEffect(() => {
    const next = new URLSearchParams()
    if (query) next.set('q', query)
    if (sport) next.set('sport', sport)
    setParams(next, { replace: true })
  }, [query, sport, setParams])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const first = data && sections(data)[0]?.items[0]
    if (first && (first.exact || data.results.length === 1)) actions.open(first)
  }

  return (
    <>
      <h1 className="page-title">Follow a player</h1>
      <form onSubmit={submit} className="search" role="search">
        <SearchIcon width={20} height={20} style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <label htmlFor="player-query" className="sr-only">Player name</label>
        <input id="player-query" type="search" autoFocus autoComplete="off" spellCheck={false} placeholder="Any athlete, any sport" value={query} onChange={(e) => setQuery(e.target.value)} />
        {loading && <span className="spinner" aria-label="Searching" />}
      </form>
      <SportChips sport={sport} onChange={setSport} />
      {(actions.error || error) && <div className="error-box">{actions.error || error}</div>}

      {data ? (
        <SearchResults data={data} query={query} loading={loading} actions={actions} />
      ) : (
        <section className="section" aria-label="Try">
          <span className="eyebrow">Try</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {SUGGESTIONS.map((name) => (
              <button key={name} type="button" className="btn" onClick={() => setQuery(name)}>{name}</button>
            ))}
          </div>
          <p className="tiny muted" style={{ margin: 0 }}>Pick an athlete and their live scores start at once; the research agent adds news and background in a couple of minutes. Names no source knows can be researched with AI, a few a day.</p>
        </section>
      )}
    </>
  )
}

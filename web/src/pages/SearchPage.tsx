import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAccount } from '../account'
import { SectionHead } from '../components/Blocks'
import { SearchIcon } from '../components/Icons'
import { SearchResults, SportChips, sections, useSearch, useSearchActions } from '../components/Search'
import { starterNames, useStarters } from '../components/Starters'

/** Follow a player: search every source as you type, pick the exact athlete, or hand a name to the research agent. */
export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [sport, setSport] = useState(params.get('sport') ?? '')
  const { data, fresh, loading, error } = useSearch(query, sport)
  const actions = useSearchActions()
  const tries = starterNames(useStarters().sports, 6)  // the starters, a sport at a time (starters.py)
  const research = useAccount().config?.research !== false

  // The query lives in the URL, so back from a preview returns to the same results.
  useEffect(() => {
    const next = new URLSearchParams()
    if (query) next.set('q', query)
    if (sport) next.set('sport', sport)
    setParams(next, { replace: true })
  }, [query, sport, setParams])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    // Still showing the previous query's results: they aren't what was asked for.
    if (!fresh) return
    const first = data && sections(data)[0]?.items[0]
    if (first && (first.exact || data.results.length === 1)) actions.open(first)
  }

  return (
    <>
      <h1 className="dsp page-title">Search</h1>
      <form onSubmit={submit} className="search" role="search">
        <SearchIcon width={22} height={22} style={{ flexShrink: 0 }} />
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
          {tries.length > 0 && (
            <>
              <SectionHead title="Try" />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {tries.map((name) => (
                  <button key={name} type="button" className="btn small" onClick={() => setQuery(name)}>{name}</button>
                ))}
              </div>
            </>
          )}
          <p className="small muted" style={{ marginTop: 4 }}>
            {research
              ? 'Pick an athlete and their live scores start at once; the research agent adds news and background in a couple of minutes. Names no source knows can be researched with AI, a few a day.'
              : 'Pick an athlete and their live scores, games and stats start at once.'}
          </p>
        </section>
      )}
    </>
  )
}

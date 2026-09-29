import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { Avatar } from '../components/Blocks'
import { CheckIcon, PlusIcon, SearchIcon } from '../components/Icons'
import { sportName } from '../format'
import { useFollowing } from '../following'

const SUGGESTIONS = ['Virat Kohli', 'Magnus Carlsen', 'Cristiano Ronaldo', 'Carlos Alcaraz', 'Nikola Jokić', 'Max Verstappen']

type Match = Awaited<ReturnType<typeof api.search>>['players'][number]

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<Match[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const following = useFollowing()
  const navigate = useNavigate()

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setMatches([])
      return
    }
    const t = setTimeout(() => api.search(q).then((r) => setMatches(r.players)).catch(() => setMatches([])), 200)
    return () => clearTimeout(t)
  }, [query])

  const follow = async (name: string) => {
    setError(null)
    setPending(name)
    try {
      const card = await following.follow(name)
      navigate(`/player/${card.player_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending(null)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (query.trim().length >= 2) follow(query.trim())
  }

  return (
    <>
      <h1 className="page-title">Follow a player</h1>
      <form onSubmit={submit} className="search" role="search">
        <SearchIcon width={20} height={20} style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <label htmlFor="player-query" className="sr-only">Player name</label>
        <input id="player-query" type="search" autoFocus autoComplete="off" placeholder="Any athlete, any sport" value={query} onChange={(e) => setQuery(e.target.value)} />
        {query.trim().length >= 2 && <button type="submit" className="btn primary" disabled={Boolean(pending)} style={{ height: 36 }}>Follow</button>}
      </form>
      {error && <div className="error-box">{error}</div>}

      {matches.length > 0 && (
        <section className="section" aria-label="Already tracked">
          <span className="eyebrow">Already tracked</span>
          <ul className="card list">
            {matches.map((m) => {
              const on = following.isFollowing(m.player_id)
              return (
                <li key={m.player_id} className="row">
                  <button type="button" onClick={() => navigate(`/player/${m.player_id}`)} className="row" style={{ justifyContent: 'flex-start', border: 0, background: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', minWidth: 0 }}>
                    <Avatar name={m.name} />
                    <span className="stack">
                      <span className="title">{m.name}</span>
                      <span className="small muted">{[sportName(m.sport), ...(m.teams ?? []).slice(0, 2), m.status === 'retired' && 'retired'].filter(Boolean).join(' · ')}{m.followers ? ` · ${m.followers} following` : ''}</span>
                    </span>
                  </button>
                  {on ? (
                    <span className="btn" aria-disabled="true"><CheckIcon width={16} height={16} />Following</span>
                  ) : (
                    <button type="button" className="btn primary" disabled={Boolean(pending)} onClick={() => follow(m.name)}><PlusIcon width={16} height={16} />Follow</button>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <section className="section" aria-label="Try">
        <span className="eyebrow">Try</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {SUGGESTIONS.map((name) => (
            <button key={name} type="button" className="btn" disabled={Boolean(pending)} onClick={() => follow(name)}>
              {pending === name ? <span className="spinner" /> : null}{name}
            </button>
          ))}
        </div>
        <p className="tiny muted" style={{ margin: 0 }}>A player nobody has followed yet takes a minute or two to set up the first time. After that, their page is shared by every fan who follows them.</p>
      </section>
    </>
  )
}

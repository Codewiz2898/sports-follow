import { StrictMode, useState, type FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { PlayerRow } from './components/Blocks'
import { ListIcon, SearchIcon } from './components/Icons'
import { FollowingProvider, useFollowing } from './following'
import { FollowingPage } from './pages/FollowingPage'
import { PlayerPage } from './pages/PlayerPage'
import { SearchPage } from './pages/SearchPage'
import './styles.css'

function Sidebar() {
  const { cards, progress, follow } = useFollowing()
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (q.length < 2) return
    const card = await follow(q)
    setQuery('')
    navigate(`/player/${card.player_id}`)
  }
  const live = cards.filter((c) => c.live.is_live)
  return (
    <aside className="sidebar" aria-label="Followed players">
      <NavLink to="/" className="brand">Sports Follow</NavLink>
      <form onSubmit={submit} className="search" role="search" style={{ height: 44 }}>
        <SearchIcon width={18} height={18} style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <label htmlFor="side-query" className="sr-only">Follow a player</label>
        <input id="side-query" type="search" placeholder="Follow a player…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ fontSize: 14 }} />
      </form>
      <span className="eyebrow">Following · {cards.length}{live.length ? ` · ${live.length} live` : ''}</span>
      <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {[...cards].sort((a, b) => Number(b.live.is_live) - Number(a.live.is_live)).map((c) => (
          <PlayerRow key={c.player_id} card={c} progress={progress[c.player_id]} />
        ))}
      </nav>
    </aside>
  )
}

function App() {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<FollowingPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/player/:id" element={<PlayerPage />} />
        </Routes>
      </main>
      <nav className="bottom-nav" aria-label="Main">
        <NavLink to="/" end><ListIcon />Following</NavLink>
        <NavLink to="/search"><SearchIcon />Search</NavLink>
      </nav>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <FollowingProvider>
        <App />
      </FollowingProvider>
    </BrowserRouter>
  </StrictMode>,
)

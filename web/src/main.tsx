import { StrictMode, useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { PlayerRow } from './components/Blocks'
import { ListIcon, SearchIcon } from './components/Icons'
import { SearchResults, sections, useSearch, useSearchActions } from './components/Search'
import { FollowingProvider, useFollowing } from './following'
import { AthletePage } from './pages/AthletePage'
import { FollowingPage } from './pages/FollowingPage'
import { PlayerPage } from './pages/PlayerPage'
import { SearchPage } from './pages/SearchPage'
import './styles.css'

/** Desktop search: results drop down under the sidebar box. ↑ ↓ move, Enter opens, Shift+Enter follows, Esc closes, / focuses. */
function SidebarSearch() {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [top, setTop] = useState(0)
  const box = useRef<HTMLFormElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const close = useCallback(() => { setOpen(false); setQuery(''); input.current?.blur() }, [])
  const actions = useSearchActions(close)
  const { data, loading } = useSearch(query, '')
  const flat = data ? sections(data).flatMap((s) => s.items) : []
  const current = flat[Math.min(active, flat.length - 1)]

  useEffect(() => setActive(0), [data])
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) && !target.isContentEditable) {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => {
    if (!open) return
    const place = () => setTop((box.current?.getBoundingClientRect().bottom ?? 0) + 8)
    const outside = (e: MouseEvent) => {
      const panel = document.getElementById('side-results')
      if (!box.current?.contains(e.target as Node) && !panel?.contains(e.target as Node)) setOpen(false)
    }
    place()
    window.addEventListener('resize', place)
    document.addEventListener('mousedown', outside)
    return () => { window.removeEventListener('resize', place); document.removeEventListener('mousedown', outside) }
  }, [open])

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') close()
    else if (e.key === 'ArrowDown' && flat.length) { e.preventDefault(); setActive((i) => Math.min(i + 1, flat.length - 1)) }
    else if (e.key === 'ArrowUp' && flat.length) { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') {
      e.preventDefault()
      if (!current) {
        if (query.trim().length >= 2) { navigate(`/search?q=${encodeURIComponent(query.trim())}`); close() }
      } else if (e.shiftKey && !(current.player_id && actions.isFollowing(current.player_id))) actions.follow(current)
      else if (current.player_id || current.system) actions.open(current)
    }
  }

  const showing = open && query.trim().length >= 2
  return (
    <>
      <form ref={box} onSubmit={(e) => e.preventDefault()} className="search" role="search" style={{ height: 44 }}>
        <SearchIcon width={18} height={18} style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <label htmlFor="side-query" className="sr-only">Search players</label>
        <input
          ref={input} id="side-query" type="search" autoComplete="off" spellCheck={false} placeholder="Search players" value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onKeyDown={onKeyDown} style={{ fontSize: 14 }}
        />
        {loading ? <span className="spinner" /> : <kbd className="key">/</kbd>}
      </form>
      {showing && data && (
        <div id="side-results" className="search-panel" style={{ top }}>
          <SearchResults data={data} query={query} loading={loading} actions={actions} activeKey={current?.key} idFor={(r) => `side-${r.key}`} />
          {actions.error && <div className="error-box">{actions.error}</div>}
          <span className="tiny muted">↑ ↓ move · Enter open · Shift+Enter follow · Esc close</span>
        </div>
      )}
    </>
  )
}

function Sidebar() {
  const { cards, progress } = useFollowing()
  const live = cards.filter((c) => c.live.is_live)
  return (
    <aside className="sidebar" aria-label="Followed players">
      <NavLink to="/" className="brand">Sports Follow</NavLink>
      <SidebarSearch />
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
          <Route path="/athlete/:system/:id" element={<AthletePage />} />
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

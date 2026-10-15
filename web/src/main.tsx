import { StrictMode, useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AccountNotice, AccountProvider, useAccount } from './account'
import { APP_NAME } from './app'
import { ActivityProvider, useActivity } from './activity'
import { PlayerRow } from './components/Blocks'
import { ErrorBoundary } from './components/ErrorBoundary'
import { BellIcon, ChevronIcon, HomeIcon, SearchIcon, UserIcon } from './components/Icons'
import { SearchResults, sections, useSearch, useSearchActions } from './components/Search'
import { report, watchErrors } from './errors'
import { FollowingProvider, useFollowing } from './following'
import { AccountPage } from './pages/AccountPage'
import { AthletePage } from './pages/AthletePage'
import { NotificationsPage } from './pages/NotificationsPage'
import { CreditsPage } from './pages/CreditsPage'
import { FollowingPage } from './pages/FollowingPage'
import { MomentToasts, PushResync } from './notifications'
import { PlayerPage } from './pages/PlayerPage'
import { PrivacyPage } from './pages/PrivacyPage'
import { InstallButton, PwaNotices, PwaProvider } from './pwa'
import { SearchPage } from './pages/SearchPage'
import { loadSports } from './sports'
import { TodayPage } from './pages/TodayPage'
import { WelcomePage } from './pages/WelcomePage'
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
  const { data, fresh, loading } = useSearch(query, '')
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
      // The list may still be the previous query's while this one loads: wait for its own results.
      if (!fresh && loading) return
      if (!fresh || !current) {
        if (query.trim().length >= 2) { navigate(`/search?q=${encodeURIComponent(query.trim())}`); close() }
      } else if (e.shiftKey) {
        if (!actions.pending && !(current.player_id && actions.isFollowing(current.player_id))) actions.follow(current)
      } else if (current.player_id || current.system) actions.open(current)
    }
  }

  const showing = open && query.trim().length >= 2
  return (
    <>
      <form ref={box} onSubmit={(e) => e.preventDefault()} className="search" role="search" style={{ height: 46 }}>
        <SearchIcon width={18} height={18} style={{ flexShrink: 0 }} />
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
  const { me } = useAccount()
  const live = cards.filter((c) => c.live.is_live)
  return (
    <aside className="sidebar" aria-label="Followed players">
      <NavLink to="/" className="brand"><img src="/icons/mark.svg" alt="" width={36} height={36} />{APP_NAME}</NavLink>
      <SidebarSearch />
      <nav className="side-nav" aria-label="Main">
        <NavLink to="/" end><HomeIcon width={18} height={18} />Today</NavLink>
        <NavLink to="/notifications"><BellIcon width={18} height={18} />Alerts<Unread /></NavLink>
        <NavLink to="/account"><UserIcon width={18} height={18} />{me?.signed_in ? 'Account' : 'Sign in'}</NavLink>
      </nav>
      <InstallButton compact />
      {/* The heading opens the Following page, every player as a tile. */}
      <NavLink to="/following" className="eyebrow side-head">Following · {cards.length}{live.length ? ` · ${live.length} live` : ''}<ChevronIcon width={16} height={16} strokeWidth={2.6} /></NavLink>
      <nav aria-label="Your players" style={{ display: 'flex', flexDirection: 'column', gap: 2, margin: '-6px -10px 0' }}>
        <ErrorBoundary fallback={<span className="tiny muted" style={{ padding: '0 10px' }}>Your players couldn't be listed here.</span>} resetKey={cards}>
          {[...cards].sort((a, b) => Number(b.live.is_live) - Number(a.live.is_live)).map((c) => (
            <PlayerRow key={c.player_id} card={c} progress={progress[c.player_id]} />
          ))}
        </ErrorBoundary>
      </nav>
      <span className="row tiny muted" style={{ marginTop: 'auto', justifyContent: 'flex-start' }}>
        <NavLink to="/credits" className="link">Credits and sources</NavLink>
        <NavLink to="/privacy" className="link">Privacy</NavLink>
      </span>
    </aside>
  )
}

/** A new page opens at its top, not wherever the last one was scrolled to. */
function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [pathname])
  return null
}

function Toasts() {
  const { moments, dismissMoment, byId } = useFollowing()
  const { muted } = useActivity()
  return <MomentToasts moments={moments.filter((m) => !m.event_id || !muted.has(m.event_id))} dismiss={dismissMoment} playerOf={byId} />
}

/** How many moments have come since the fan last looked at Alerts. */
function Unread() {
  const { unread } = useActivity()
  if (!unread) return null
  return <span className="nav-count" aria-label={`${unread} new`}>{unread > 9 ? '9+' : unread}</span>
}

/** An address that isn't a page here (an old link, a typo): say so, with the way back. */
function NotFound() {
  return (
    <>
      <h1 className="dsp page-title">Not found</h1>
      <div className="card card-pad stack" style={{ gap: 12 }}>
        <span className="title">There's no page at this address.</span>
        <Link to="/" className="btn primary" style={{ alignSelf: 'flex-start' }}>Back to Today</Link>
      </div>
    </>
  )
}

function App() {
  const { pathname } = useLocation()
  return (
    <div className="shell">
      <ScrollToTop />
      <PushResync />
      <ErrorBoundary fallback={null} resetKey={pathname}>
        <Toasts />
      </ErrorBoundary>
      <Sidebar />
      <main className="main">
        <PwaNotices />
        <AccountNotice />
        {/* A page that fails to render shows a message, and the tab bar still works; another page tries again. */}
        <ErrorBoundary resetKey={pathname}>
          <Routes>
            <Route path="/" element={<TodayPage />} />
            <Route path="/welcome" element={<WelcomePage />} />
            <Route path="/following" element={<FollowingPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/player/:id" element={<PlayerPage />} />
            <Route path="/athlete/:system/:id" element={<AthletePage />} />
            <Route path="/notifications" element={<NotificationsPage />} />
            <Route path="/credits" element={<CreditsPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/privacy" element={<PrivacyPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </ErrorBoundary>
      </main>
      <nav className="tabbar" aria-label="Main">
        {/* The Following page is part of Today here (its strip opens it): Today stays lit there. */}
        <NavLink to="/" end className={({ isActive }) => (isActive || pathname === '/following' ? 'active' : '')}><HomeIcon width={20} height={20} />Today</NavLink>
        <NavLink to="/search"><SearchIcon width={20} height={20} />Search</NavLink>
        <NavLink to="/notifications"><BellIcon width={20} height={20} />Alerts<Unread /></NavLink>
      </nav>
    </div>
  )
}

watchErrors()

// A render that failed is reported, whether an ErrorBoundary showed its fallback (caught) or nothing did.
function failedRender(error: unknown, info: { componentStack?: string }) {
  console.error(error)
  report({ kind: 'render', componentStack: info.componentStack }, error)
}

// The sports this app knows come from the server; the first render waits for them (from this device's
// copy when there is one, so only a first visit waits on the network).
void loadSports().then(() => createRoot(document.getElementById('root')!, { onCaughtError: failedRender, onUncaughtError: failedRender }).render(
  <StrictMode>
    <BrowserRouter>
      <PwaProvider>
        <AccountProvider>
          <FollowingProvider>
            <ActivityProvider>
              <App />
            </ActivityProvider>
          </FollowingProvider>
        </AccountProvider>
      </PwaProvider>
    </BrowserRouter>
  </StrictMode>,
))

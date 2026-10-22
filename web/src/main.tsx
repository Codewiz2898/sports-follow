import { lazy, StrictMode, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { AccountNotice, AccountProvider, useAccount } from './account'
import { APP_NAME } from './app'
import { ActivityProvider, useActivity } from './activity'
import { PlayerRow, TeamRow } from './components/Blocks'
import { ErrorBoundary } from './components/ErrorBoundary'
import { SideColumn, SideSlot, useWide } from './components/Side'
import { BellIcon, ChevronIcon, FeedIcon, HomeIcon, LiveIcon, SearchIcon, UserIcon } from './components/Icons'
import { entries, SearchResults, useSearch, useSearchActions } from './components/Search'
import { report, watchErrors } from './errors'
import { useFeedDot } from './feedSeen'
import { FollowingProvider, useFollowing } from './following'
import { AccountPage } from './pages/AccountPage'
import { AthletePage } from './pages/AthletePage'
import { CampaignPage } from './pages/CampaignPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { CreditsPage } from './pages/CreditsPage'
import { FeedPage } from './pages/FeedPage'
import { FollowingPage } from './pages/FollowingPage'
import { LivePage } from './pages/LivePage'
import { MatchPage } from './pages/MatchPage'
import { ClipPage, ClipsPage } from './pages/ClipsPage'
import { TeamPage } from './pages/TeamPage'
import { MomentToasts, PushResync } from './notifications'
import { PlayerPage } from './pages/PlayerPage'
import { PrivacyPage } from './pages/PrivacyPage'
import { TermsPage } from './pages/TermsPage'
import { FriendLeaguePage, InvitePage, LeaguesPage } from './pages/LeaguesPage'
import { InstallButton, PwaNotices, PwaProvider } from './pwa'
import { SearchPage } from './pages/SearchPage'
import { loadSports, useSports } from './sports'
import { TodayPage } from './pages/TodayPage'
import { WelcomePage } from './pages/WelcomePage'
import { noteVisit } from './visit'
import './styles.css'

// The admin panel: its own chunk, so fans never download it.
const AdminApp = lazy(() => import('./admin/AdminApp'))

/** Desktop search: results drop down under the sidebar box, athletes and teams. ↑ ↓ move, Enter opens, Shift+Enter
 * follows, Esc closes, / focuses. */
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
  const flat = data ? entries(data) : []
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
      } else if (current.team) {
        if (!e.shiftKey) actions.openTeam(current.team)
        else if (!actions.pending && !(current.team.following || actions.isFollowingTeam(current.team.team_id))) void actions.followTeam(current.team)
      } else if (e.shiftKey) {
        const r = current.result
        if (!actions.pending && !(r.player_id && actions.isFollowing(r.player_id))) actions.follow(r)
      } else if (current.result.player_id || current.result.system) actions.open(current.result)
    }
  }

  const showing = open && query.trim().length >= 2
  return (
    <>
      <form ref={box} onSubmit={(e) => e.preventDefault()} className="search" role="search" style={{ height: 46 }}>
        <SearchIcon width={18} height={18} style={{ flexShrink: 0 }} />
        <label htmlFor="side-query" className="sr-only">Search players and teams</label>
        <input
          ref={input} id="side-query" type="search" autoComplete="off" spellCheck={false} placeholder="Search players and teams" value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onKeyDown={onKeyDown} style={{ fontSize: 14 }}
        />
        {loading ? <span className="spinner" /> : <kbd className="key">/</kbd>}
      </form>
      {showing && data && (
        <div id="side-results" className="search-panel" style={{ top }}>
          <SearchResults data={data} query={query} loading={loading} actions={actions} activeKey={current?.key} idFor={(key) => `side-${key}`} />
          {actions.error && <div className="error-box">{actions.error}</div>}
          <span className="tiny muted">↑ ↓ move · Enter open · Shift+Enter follow · Esc close</span>
        </div>
      )}
    </>
  )
}

function Sidebar() {
  const { cards, progress, teams } = useFollowing()
  const { me } = useAccount()
  const live = [...cards.filter((c) => c.live.is_live), ...teams.filter((t) => t.live?.is_live)]
  const listed = useMemo(() => ({ cards, teams }), [cards, teams])  // a newer list of either tries again after a bad row
  return (
    <aside className="sidebar" aria-label="Followed players and teams">
      <NavLink to="/" className="brand"><img src="/icons/mark.svg" alt="" width={36} height={36} />{APP_NAME}</NavLink>
      <SidebarSearch />
      <nav className="side-nav" aria-label="Main">
        <NavLink to="/" end><HomeIcon width={18} height={18} />Today</NavLink>
        <NavLink to="/me/feed"><FeedIcon width={18} height={18} />Feed<FeedDot /></NavLink>
        <NavLink to="/live"><LiveIcon width={18} height={18} />Live now</NavLink>
        <NavLink to="/notifications"><BellIcon width={18} height={18} />Alerts<Unread /></NavLink>
        <NavLink to="/account"><UserIcon width={18} height={18} />{me?.signed_in ? 'Account' : 'Sign in'}</NavLink>
      </nav>
      <InstallButton compact />
      {/* The heading opens the Following page, every player as a tile. */}
      <NavLink to="/following" className="eyebrow side-head">Following · {cards.length + teams.length}{live.length ? ` · ${live.length} live` : ''}<ChevronIcon width={16} height={16} strokeWidth={2.6} /></NavLink>
      <nav aria-label="Your players and teams" style={{ display: 'flex', flexDirection: 'column', gap: 2, margin: '-6px -10px 0' }}>
        <ErrorBoundary fallback={<span className="tiny muted" style={{ padding: '0 10px' }}>Your players couldn't be listed here.</span>} resetKey={listed}>
          {/* Teams first, as on the Following page. */}
          {teams.map((t) => <TeamRow key={`team-${t.team_id}`} team={t} />)}
          {[...cards].sort((a, b) => Number(b.live.is_live) - Number(a.live.is_live)).map((c) => (
            <PlayerRow key={c.player_id} card={c} progress={progress[c.player_id]} />
          ))}
        </ErrorBoundary>
      </nav>
      <span className="row tiny muted" style={{ marginTop: 'auto', justifyContent: 'flex-start' }}>
        <NavLink to="/credits" className="link">Credits and sources</NavLink>
        <NavLink to="/privacy" className="link">Privacy</NavLink>
        <NavLink to="/terms" className="link">Terms</NavLink>
      </span>
    </aside>
  )
}

// Where each page the fan has been on was scrolled to, by its step in the browser's history (location.key).
const scrolled = new Map<string, number>()
// What the fan does that says they've taken over the scrolling.
const TAKEN_OVER = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const

/** A new page opens at its top, not wherever the last one was scrolled to; Back (or Forward) to one goes back to
 * where the fan was on it, rather than to its top (BUG-163): its lists come from memory (the Feed's pages: myFeed.ts),
 * so it's tall enough again at once or nearly. The browser's own restoring is off: it ran before the page was drawn. */
function ScrollToTop() {
  const { pathname, key } = useLocation()
  const how = useNavigationType()
  const at = useRef(key)
  useLayoutEffect(() => { at.current = key }, [key])
  useEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual'
    const note = () => { scrolled.set(at.current, window.scrollY) }
    window.addEventListener('scroll', note, { passive: true })
    return () => window.removeEventListener('scroll', note)
  }, [])
  useEffect(() => {
    if (how !== 'POP') { window.scrollTo(0, 0); return }
    return scrollBack(scrolled.get(key) ?? 0)
  }, [pathname])  // eslint-disable-line react-hooks/exhaustive-deps -- a new page, as it came (how, key)
  return null
}

/** Scrolls the window to `y` as soon as the page is tall enough to: at once, or over the next two seconds as what it
 * shows comes in; not once the fan scrolls, taps or types first. Returns a way to stop. */
function scrollBack(y: number): () => void {
  const until = performance.now() + 2000
  let frame = 0
  const stop = () => {
    cancelAnimationFrame(frame)
    for (const e of TAKEN_OVER) window.removeEventListener(e, stop)
  }
  const step = () => {
    window.scrollTo(0, y)
    if (Math.abs(window.scrollY - y) < 2 || performance.now() > until) stop()
    else frame = requestAnimationFrame(step)
  }
  for (const e of TAKEN_OVER) window.addEventListener(e, stop, { passive: true })
  step()
  return stop
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

/** The Feed tab's dot: something came into the fan's Feed since they last opened it (feedSeen.ts). */
function FeedDot() {
  if (!useFeedDot()) return null
  return <span className="nav-dot" aria-label="new in your Feed" />
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
  useSports()  // every page draws again when a sport is switched on or off (sports/catalog.ts)
  // A wide screen's side column (Side.tsx), and where in it a page stands its own part.
  const wide = useWide()
  const [slot, setSlot] = useState<HTMLDivElement | null>(null)
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
          <SideSlot.Provider value={wide ? slot : null}>
            <Routes>
              <Route path="/" element={<TodayPage />} />
              <Route path="/welcome" element={<WelcomePage />} />
              <Route path="/following" element={<FollowingPage />} />
              <Route path="/me/feed" element={<FeedPage />} />
              <Route path="/live" element={<LivePage />} />
              <Route path="/search" element={<SearchPage />} />
              <Route path="/player/:id" element={<PlayerPage />} />
              <Route path="/match/:source/:game" element={<MatchPage />} />
              {/* Two words or more, so the Android app (which opens one-word addresses) leaves them to the browser. */}
              <Route path="/competition/:name/clips" element={<ClipsPage />} />
              <Route path="/clip/:id" element={<ClipPage />} />
              <Route path="/team/:id" element={<TeamPage />} />
              <Route path="/athlete/:system/:id" element={<AthletePage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/credits" element={<CreditsPage />} />
              <Route path="/account" element={<AccountPage />} />
              <Route path="/privacy" element={<PrivacyPage />} />
              <Route path="/terms" element={<TermsPage />} />
              <Route path="/me/leagues" element={<LeaguesPage />} />
              <Route path="/league/:code" element={<FriendLeaguePage />} />
              <Route path="/j/:code" element={<InvitePage />} />
              {/* A campaign page (stann.in/gukesh): any other one-word address; the server says if it's one. */}
              <Route path="/:slug" element={<CampaignPage notFound={<NotFound />} />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </SideSlot.Provider>
        </ErrorBoundary>
      </main>
      {wide && <SideColumn slot={setSlot} />}
      <nav className="tabbar" aria-label="Main">
        {/* The Following and Live pages are part of Today here (Today opens them): Today stays lit there. */}
        <NavLink to="/" end className={({ isActive }) => (isActive || pathname === '/following' || pathname === '/live' ? 'active' : '')}><HomeIcon width={20} height={20} />Today</NavLink>
        <NavLink to="/me/feed"><FeedIcon width={20} height={20} />Feed<FeedDot /></NavLink>
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

// A build replaced while this tab still runs it (another tab took the new one: sw.js drops the old build's files): a
// page this tab hadn't opened yet is gone from the server and the cache, so loading it fails. The tab reloads onto the
// new build, at most once in a while, so a failure that isn't that can't reload it over and over.
window.addEventListener('vite:preloadError', (event) => {
  try {
    const last = Number(sessionStorage.getItem('sf-reloaded-for-build') || 0)
    if (Date.now() - last < 60_000) return
    sessionStorage.setItem('sf-reloaded-for-build', String(Date.now()))
  } catch { /* no storage: reload all the same */ }
  event.preventDefault()
  window.location.reload()
})

// The sports this app knows come from the server; the first render waits for them (from this device's
// copy when there is one, so only a first visit waits on the network), and for a tagged link's source to be
// told (visit.ts), which a first visit's fan cookie comes back with.
// The admin panel (/admin) is apart from the fans' app: none of its providers, so no following or live stream.
void Promise.all([loadSports(), noteVisit()]).then(() => createRoot(document.getElementById('root')!, { onCaughtError: failedRender, onUncaughtError: failedRender }).render(
  <StrictMode>
    <BrowserRouter>
      {window.location.pathname === '/admin' || window.location.pathname.startsWith('/admin/') ? (
        <Suspense fallback={null}>
          <ErrorBoundary>
            <Routes><Route path="/admin/*" element={<AdminApp />} /></Routes>
          </ErrorBoundary>
        </Suspense>
      ) : (
        <PwaProvider>
          <AccountProvider>
            <FollowingProvider>
              <ActivityProvider>
                <App />
              </ActivityProvider>
            </FollowingProvider>
          </AccountProvider>
        </PwaProvider>
      )}
    </BrowserRouter>
  </StrictMode>,
))

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { ApiError } from '../api'
import { APP_NAME } from '../app'
import { admin } from './api'
import { ClipsTab } from './ClipsTab'
import { FeedTab } from './FeedTab'
import { Failure } from './shared'
import { SourcesTab } from './SourcesTab'
import { SportsTab } from './SportsTab'
import { TeamsTab } from './TeamsTab'
import { UserPage, UsersTab } from './UsersTab'
import './admin.css'

/** The admin panel (/admin), apart from the fans' app: no sidebar, no following, no live stream. It opens with
 * its own password and a code from an authenticator app; a server with no password set has no panel, and
 * says only that there's no page here. */
export default function AdminApp() {
  const [state, setState] = useState<'checking' | 'in' | 'out' | 'off'>('checking')
  const [code, setCode] = useState(true)  // sign-in asks for an authenticator code
  const failed = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) setState('out')
    return error instanceof Error ? error.message : 'Something went wrong.'
  }, [])
  const check = useCallback(() => {
    admin.me().then((me) => { setCode(me.code); setState('in') }, (e) => setState(e instanceof ApiError && e.status === 404 ? 'off' : 'out'))
  }, [])

  useEffect(() => {
    document.title = `${APP_NAME} admin`
    // Search engines are told not to list the panel (the server says so too, X-Robots-Tag).
    const robots = document.createElement('meta')
    robots.name = 'robots'
    robots.content = 'noindex, nofollow'
    document.head.append(robots)
    check()
    return () => robots.remove()
  }, [check])

  if (state === 'checking') return <div className="admin"><span className="spinner" /></div>
  if (state === 'off') return <NoPanel />
  if (state === 'out') return <SignIn onIn={check} />
  const signOut = () => { void admin.logout().finally(() => setState('out')) }
  return (
    <Failure.Provider value={failed}>
      <div className="admin">
        <header className="admin-head">
          <a href="/" className="brand"><img src="/icons/mark.svg" alt="" width={32} height={32} />{APP_NAME}</a>
          <span className="pill ink">Admin</span>
          <button type="button" className="btn small" onClick={signOut}>Sign out</button>
        </header>
        {!code && (
          <div className="error-box" role="status">
            Sign-in asks only for the password. Add a code from an authenticator app: run <code>python -m sports_follow.admin code</code>,
            then send it to the server with <code>deploy/push-secrets.sh</code> (deploy/README.md, "The admin panel").
          </div>
        )}
        <nav className="admin-tabs" aria-label="Admin">
          <NavLink to="/admin" end>Sports</NavLink>
          <NavLink to="/admin/sources">Sources and limits</NavLink>
          <NavLink to="/admin/teams">Teams</NavLink>
          <NavLink to="/admin/users">Users</NavLink>
          <NavLink to="/admin/feed">Feed</NavLink>
          <NavLink to="/admin/clips">Clips</NavLink>
        </nav>
        <Routes>
          <Route index element={<SportsTab />} />
          <Route path="sources" element={<SourcesTab />} />
          <Route path="teams" element={<TeamsTab />} />
          <Route path="users" element={<UsersTab />} />
          <Route path="users/:id" element={<UserPage />} />
          <Route path="feed" element={<FeedTab />} />
          <Route path="clips" element={<ClipsTab />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </div>
    </Failure.Provider>
  )
}

function SignIn({ onIn }: { onIn: () => void }) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await admin.login(password, code)
      onIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign in.")
      setCode('')  // a code works once, and a new one comes every 30 seconds
      setBusy(false)
    }
  }
  return (
    <div className="admin admin-sign-in">
      <form className="card card-pad stack" style={{ gap: 14 }} onSubmit={submit}>
        <h1 className="dsp page-title">Admin</h1>
        <label htmlFor="admin-password" className="small muted">The admin password</label>
        <input id="admin-password" className="field" type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        {/* Always here: the page doesn't say whether the password alone would do. The server ignores it until a key is set. */}
        <label htmlFor="admin-code" className="small muted">The code from your authenticator app</label>
        <input
          id="admin-code" className="field code" inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
          value={code} onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))}
        />
        {error && <div className="error-box" role="alert">{error}</div>}
        <button type="submit" className="btn primary" disabled={busy || !password}>{busy ? <span className="spinner" /> : 'Sign in'}</button>
        <a href="/" className="tiny muted link" style={{ alignSelf: 'flex-start' }}>Back to {APP_NAME}</a>
      </form>
    </div>
  )
}

function NoPanel() {
  return (
    <div className="admin admin-sign-in">
      <div className="card card-pad stack" style={{ gap: 12 }}>
        <h1 className="dsp page-title">Not found</h1>
        <span className="title">There's no page at this address.</span>
        <a href="/" className="btn primary" style={{ alignSelf: 'flex-start' }}>Back to Today</a>
      </div>
    </div>
  )
}

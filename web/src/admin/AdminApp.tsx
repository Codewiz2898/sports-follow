import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { ApiError } from '../api'
import { APP_NAME } from '../app'
import { admin } from './api'
import { Failure } from './shared'
import { SourcesTab } from './SourcesTab'
import { SportsTab } from './SportsTab'
import { UserPage, UsersTab } from './UsersTab'
import './admin.css'

/** The admin panel (/admin), apart from the fans' app: no sidebar, no following, no live stream. It opens with
 * its own password; a server with none set has no panel, and says only that there's no page here. */
export default function AdminApp() {
  const [state, setState] = useState<'checking' | 'in' | 'out' | 'off'>('checking')
  const failed = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) setState('out')
    return error instanceof Error ? error.message : 'Something went wrong.'
  }, [])

  useEffect(() => {
    document.title = `${APP_NAME} admin`
    admin.me().then(() => setState('in'), (e) => setState(e instanceof ApiError && e.status === 404 ? 'off' : 'out'))
  }, [])

  if (state === 'checking') return <div className="admin"><span className="spinner" /></div>
  if (state === 'off') return <NoPanel />
  if (state === 'out') return <SignIn onIn={() => setState('in')} />
  const signOut = () => { void admin.logout().finally(() => setState('out')) }
  return (
    <Failure.Provider value={failed}>
      <div className="admin">
        <header className="admin-head">
          <a href="/" className="brand"><img src="/icons/mark.svg" alt="" width={32} height={32} />{APP_NAME}</a>
          <span className="pill ink">Admin</span>
          <button type="button" className="btn small" onClick={signOut}>Sign out</button>
        </header>
        <nav className="admin-tabs" aria-label="Admin">
          <NavLink to="/admin" end>Sports</NavLink>
          <NavLink to="/admin/sources">Sources and limits</NavLink>
          <NavLink to="/admin/users">Users</NavLink>
        </nav>
        <Routes>
          <Route index element={<SportsTab />} />
          <Route path="sources" element={<SourcesTab />} />
          <Route path="users" element={<UsersTab />} />
          <Route path="users/:id" element={<UserPage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </div>
    </Failure.Provider>
  )
}

function SignIn({ onIn }: { onIn: () => void }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await admin.login(password)
      onIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't sign in.")
      setBusy(false)
    }
  }
  return (
    <div className="admin admin-sign-in">
      <form className="card card-pad stack" style={{ gap: 14 }} onSubmit={submit}>
        <h1 className="dsp page-title">Admin</h1>
        <label htmlFor="admin-password" className="small muted">The admin password</label>
        <input id="admin-password" className="field" type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
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

import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { BackIcon, SearchIcon } from '../components/Icons'
import { day, relative } from '../format'
import { admin, type AccountDetail, type AccountRow, type Overview } from './api'
import { Note, count, useFailure } from './shared'

const TYPING_MS = 250

/** How many fans there are, and every account: newest first, or found by address. */
export function UsersTab() {
  const failed = useFailure()
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const [typed, setTyped] = useState(query)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [rows, setRows] = useState<AccountRow[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => { if (typed !== query) setParams(typed ? { q: typed } : {}, { replace: true }) }, TYPING_MS)
    return () => clearTimeout(timer)
  }, [typed, query, setParams])
  useEffect(() => {
    let current = true
    admin.users(query).then((r) => {
      if (!current) return
      setOverview(r.overview)
      setRows(r.accounts)
      setNext(r.next)
    }, (e) => current && setError(failed(e)))
    return () => { current = false }
  }, [query, failed])

  const more = async () => {
    if (next == null) return
    try {
      const r = await admin.users(query, next)
      setRows((old) => [...(old ?? []), ...r.accounts])
      setNext(r.next)
    } catch (e) {
      setError(failed(e))
    }
  }

  return (
    <>
      {overview && (
        <div className="stat-grid" aria-label="Fans">
          <Stat value={overview.accounts} label="Accounts" />
          <Stat value={overview.active_week} label="Signed in this week" />
          <Stat value={overview.blocked} label="Blocked" />
          <Stat value={overview.anonymous} label="Anonymous fans following someone" />
          <Stat value={overview.devices} label="Devices with alerts on" />
        </div>
      )}
      <Note>Fans without an account are anonymous (a cookie on their device) and aren't listed. Blocking an account signs it out everywhere and stops it signing in; its device can still use the app anonymously.</Note>
      <form className="search" role="search" onSubmit={(e) => e.preventDefault()}>
        <SearchIcon width={18} height={18} style={{ flexShrink: 0 }} />
        <label htmlFor="admin-users-q" className="sr-only">Find an account by email address</label>
        <input id="admin-users-q" type="search" autoComplete="off" spellCheck={false} placeholder="Find by email address" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </form>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!rows ? <div className="card empty"><span className="spinner" /></div> : !rows.length ? (
        <div className="card empty small">{query ? `No account's address has "${query}" in it.` : 'No accounts yet.'}</div>
      ) : (
        <div className="card">
          <ul className="list">
            {rows.map((a) => (
              <li key={a.id}>
                <Link to={`/admin/users/${a.id}`} className="admin-row">
                  <div className="stack">
                    <span className="title">{a.email}</span>
                    <span className="small muted">
                      Joined {day(a.created_at)} · {a.last_seen_at ? `seen ${relative(a.last_seen_at)}` : 'not signed in now'} · {count(a.players)} {a.players === 1 ? 'player' : 'players'} · {count(a.devices)} with alerts
                    </span>
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    {a.google && <span className="pill soft">Google</span>}
                    {a.blocked_at && <span className="pill danger">Blocked</span>}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      {next != null && <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} onClick={() => void more()}>More</button>}
    </>
  )
}

function Stat({ value, label }: { value: number; label: string }) {
  return <div className="stat"><span className="num">{count(value)}</span><span className="tiny">{label}</span></div>
}

type Asking = 'signout' | 'block' | 'delete' | null

/** One account: who they follow, their devices, and signing them out, blocking or deleting them. */
export function UserPage() {
  const failed = useFailure()
  const navigate = useNavigate()
  const id = Number(useParams().id)
  const [account, setAccount] = useState<AccountDetail | null>(null)
  const [asking, setAsking] = useState<Asking>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => admin.user(id).then(setAccount, (e) => setError(failed(e))), [id, failed])
  useEffect(() => { void load() }, [load])

  const act = async () => {
    if (!account || !asking) return
    setBusy(true)
    setError(null)
    try {
      if (asking === 'delete') {
        await admin.remove(account.id)
        navigate('/admin/users', { replace: true })
        return
      }
      if (asking === 'signout') {
        const r = await admin.signOut(account.id)
        setDone(`Signed out of ${r.signed_out} ${r.signed_out === 1 ? 'device' : 'devices'}.`)
      } else {
        const r = await admin.block(account.id, !account.blocked_at)
        setDone(r.blocked ? `Blocked, and signed out of ${r.signed_out} ${r.signed_out === 1 ? 'device' : 'devices'}.` : 'Unblocked: it can sign in again.')
      }
      await load()
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(false)
      setAsking(null)
    }
  }

  const back = <Link to="/admin/users" className="btn icon" aria-label="All accounts"><BackIcon width={20} height={20} /></Link>
  if (!account) return <>{back}{error ? <div className="error-box" role="alert">{error}</div> : <div className="card empty"><span className="spinner" /></div>}</>
  const question: Record<Exclude<Asking, null>, string> = {
    signout: `Sign ${account.email} out on every device? Their alerts stop until they sign in again.`,
    block: account.blocked_at ? `Let ${account.email} sign in again?` : `Block ${account.email}? They're signed out everywhere and can't sign in.`,
    delete: `Delete ${account.email}, with everyone they follow, their alert levels and devices? This can't be undone.`,
  }
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        {back}
        <div className="stack">
          <h2 className="title" style={{ fontSize: 22 }}>{account.email}</h2>
          <span className="small muted">
            Joined {day(account.created_at)}
            {account.last_sign_in_at ? ` · last signed in ${relative(account.last_sign_in_at)}` : ''}
            {account.last_seen_at ? ` · seen ${relative(account.last_seen_at)}` : ''}
          </span>
        </div>
        <div className="row" style={{ gap: 6, marginLeft: 'auto' }}>
          {account.google && <span className="pill soft">Google</span>}
          {account.blocked_at && <span className="pill danger">Blocked {day(account.blocked_at)}</span>}
        </div>
      </div>
      {done && <div className="notice" role="status">{done}</div>}
      {error && <div className="error-box" role="alert">{error}</div>}
      {asking ? (
        <div className="card card-pad stack" style={{ gap: 12 }}>
          <span className="title">{question[asking]}</span>
          <div className="row" style={{ justifyContent: 'flex-start' }}>
            <button type="button" className={`btn small primary${asking === 'signout' ? '' : ' danger'}`} disabled={busy} onClick={() => void act()}>
              {busy ? <span className="spinner" /> : asking === 'signout' ? 'Sign out everywhere' : asking === 'delete' ? 'Delete' : account.blocked_at ? 'Unblock' : 'Block'}
            </button>
            <button type="button" className="btn small" disabled={busy} onClick={() => setAsking(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap' }}>
          <button type="button" className="btn small" disabled={!account.sessions.length} onClick={() => { setDone(null); setAsking('signout') }}>Sign out everywhere</button>
          <button type="button" className="btn small danger" onClick={() => { setDone(null); setAsking('block') }}>{account.blocked_at ? 'Unblock' : 'Block'}</button>
          <button type="button" className="btn small danger" onClick={() => { setDone(null); setAsking('delete') }}>Delete account</button>
        </div>
      )}
      <section className="section" aria-label="Following">
        <h3 className="eyebrow">Following · {account.following.length}</h3>
        <div className="card">
          {account.following.length ? (
            <ul className="list">
              {account.following.map((p) => (
                <li key={p.id} className="admin-row">
                  <a href={`/player/${p.id}`} className="title">{p.name}</a>
                  <span className="small muted">{p.sport} · since {day(p.since)}</span>
                </li>
              ))}
            </ul>
          ) : <div className="empty small">Nobody yet.</div>}
        </div>
      </section>
      <section className="section" aria-label="Signed-in devices">
        <h3 className="eyebrow">Signed in on · {account.sessions.length}</h3>
        <div className="card">
          {account.sessions.length ? (
            <ul className="list">
              {account.sessions.map((s, i) => (
                <li key={i} className="admin-row small">
                  <span>Since {day(s.created_at)}</span>
                  <span className="muted">last seen {relative(s.last_seen_at)} · ends {day(s.expires_at)}</span>
                </li>
              ))}
            </ul>
          ) : <div className="empty small">Not signed in anywhere.</div>}
        </div>
      </section>
      <section className="section" aria-label="Devices with alerts">
        <h3 className="eyebrow">Alerts on · {account.alert_devices.length}</h3>
        <div className="card">
          {account.alert_devices.length ? (
            <ul className="list">
              {account.alert_devices.map((d, i) => (
                <li key={i} className="admin-row small">
                  <span>Since {day(d.created_at)} · {d.timezone}</span>
                  <span className="muted">{d.last_ok_at ? `last delivered ${relative(d.last_ok_at)}` : 'nothing delivered yet'}{d.failures ? ` · ${d.failures} failed` : ''}</span>
                </li>
              ))}
            </ul>
          ) : <div className="empty small">No device has alerts on.</div>}
        </div>
      </section>
    </>
  )
}

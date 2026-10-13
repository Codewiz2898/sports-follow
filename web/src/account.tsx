import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type Account, type Me, type ServerConfig } from './api'
import { CloseIcon } from './components/Icons'

/**
 * Who this device is signed in as (sports_follow/accounts.py), asked once at start, which also renews
 * the session. Signing in, out or deleting the account starts the app over: every list, stream and
 * cached answer belonged to the fan this device was a moment ago.
 */
interface AccountState {
  me: Me | null  // null until the server answers
  config: ServerConfig | null
  /** After a code (or Google) signed this device in. */
  signedIn: (account: Account) => void
  signOut: (everywhere?: boolean) => Promise<void>
  deleteAccount: () => Promise<void>
}

const AccountContext = createContext<AccountState | null>(null)
const NOTICE = 'sf-account-notice'  // what to say once the app has started over

/** Forget what this device showed as the fan it was, and start the app over at `path`. */
async function startOver(path: string, notice: string | null, stopPush: boolean) {
  if (stopPush) {
    // The server has dropped this device's alerts with its session; the browser forgets them too.
    try {
      const reg = await navigator.serviceWorker?.getRegistration()
      await (await reg?.pushManager.getSubscription())?.unsubscribe()
    } catch { /* no service worker or push here */ }
  }
  await forgetCachedAnswers()
  try {
    localStorage.removeItem('sf-alerts-seen')
    if (notice) sessionStorage.setItem(NOTICE, notice)
  } catch { /* storage blocked */ }
  window.location.replace(path)
}

/** The service worker keeps the last follow list and cards for offline use: they were the other fan's. */
async function forgetCachedAnswers() {
  try {
    for (const key of await caches.keys()) if (key.startsWith('data-')) await caches.delete(key)
  } catch { /* no Cache Storage here */ }
}

export function AccountProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [config, setConfig] = useState<ServerConfig | null>(null)

  useEffect(() => {
    api.account().then((answer) => {
      setMe(answer)
      if (answer.signed_in === false && answer.reason === 'signed_out') forgetCachedAnswers()
    }).catch(() => setMe({ signed_in: false }))
    api.config().then(setConfig).catch(() => {})
  }, [])

  const signedIn = useCallback((account: Account) => {
    const added = account.players_added ?? 0
    startOver('/account', added ? `Signed in. ${added === 1 ? 'The player' : `The ${added} players`} you followed on this device ${added === 1 ? 'is' : 'are'} in your account now.` : 'Signed in.', false)
  }, [])
  const signOut = useCallback(async (everywhere = false) => {
    await api.signOut(everywhere)
    await startOver('/', everywhere ? 'Signed out on every device.' : 'Signed out. Your players are kept in your account.', true)
  }, [])
  const deleteAccount = useCallback(async () => {
    await api.deleteAccount()
    await startOver('/', 'Your account and everything kept with it are deleted.', true)
  }, [])

  const value = useMemo<AccountState>(() => ({ me, config, signedIn, signOut, deleteAccount }), [me, config, signedIn, signOut, deleteAccount])
  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>
}

export function useAccount(): AccountState {
  const ctx = useContext(AccountContext)
  if (!ctx) throw new Error('useAccount outside AccountProvider')
  return ctx
}

/** What happened to this device's sign-in: said once, at the top of the page. */
export function AccountNotice() {
  const { me } = useAccount()
  const [flash, setFlash] = useState<string | null>(() => {
    try {
      const said = sessionStorage.getItem(NOTICE)
      sessionStorage.removeItem(NOTICE)
      return said
    } catch {
      return null
    }
  })
  const [closed, setClosed] = useState(false)
  const text = flash ?? (me?.signed_in === false && me.reason === 'signed_out' ?'This device was signed out. Sign in again to see your players.' : null)
  if (!text || closed) return null
  return (
    <div className="notice" role="status">
      <span>{text}</span>
      <button type="button" className="btn icon" style={{ width: 32, height: 32, background: 'transparent', color: 'inherit' }} aria-label="Dismiss" onClick={() => { setFlash(null); setClosed(true) }}>
        <CloseIcon width={16} height={16} />
      </button>
    </div>
  )
}

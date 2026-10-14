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
  /** What just happened to this device's sign-in, said once at the top of the page. */
  notice: string | null
  dismissNotice: () => void
  /** After a code signed this device in. */
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

function welcome(added: number): string {
  if (!added) return 'Signed in.'
  return `Signed in. ${added === 1 ? 'The player' : `The ${added} players`} you followed on this device ${added === 1 ? 'is' : 'are'} in your account now.`
}

export function AccountProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [config, setConfig] = useState<ServerConfig | null>(null)
  const [notice, setNotice] = useState<string | null>(() => {
    try {
      const said = sessionStorage.getItem(NOTICE)
      sessionStorage.removeItem(NOTICE)
      return said
    } catch {
      return null
    }
  })

  useEffect(() => {
    // Back from Google (/account?welcome=N, server.google_callback): the server signed this device in.
    const back = new URLSearchParams(window.location.search).get('welcome')
    if (back !== null) {
      forgetCachedAnswers()
      try { localStorage.removeItem('sf-alerts-seen') } catch { /* storage blocked */ }
      setNotice(welcome(Number(back) || 0))
    }
    api.account().then((answer) => {
      setMe(answer)
      if (answer.signed_in === false && answer.reason === 'signed_out') {
        forgetCachedAnswers()
        setNotice((said) => said ?? 'This device was signed out. Sign in again to see your players.')
      }
    }).catch(() => setMe({ signed_in: false }))
    api.config().then(setConfig).catch(() => {})
  }, [])

  const dismissNotice = useCallback(() => setNotice(null), [])
  const signedIn = useCallback((account: Account) => {
    startOver('/account', welcome(account.players_added ?? 0), false)
  }, [])
  const signOut = useCallback(async (everywhere = false) => {
    await api.signOut(everywhere)
    await startOver('/', everywhere ? 'Signed out on every device.' : 'Signed out. Your players are kept in your account.', true)
  }, [])
  const deleteAccount = useCallback(async () => {
    await api.deleteAccount()
    await startOver('/', 'Your account and everything kept with it are deleted.', true)
  }, [])

  const value = useMemo<AccountState>(
    () => ({ me, config, notice, dismissNotice, signedIn, signOut, deleteAccount }),
    [me, config, notice, dismissNotice, signedIn, signOut, deleteAccount],
  )
  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>
}

export function useAccount(): AccountState {
  const ctx = useContext(AccountContext)
  if (!ctx) throw new Error('useAccount outside AccountProvider')
  return ctx
}

/** What happened to this device's sign-in: said once, at the top of the page. */
export function AccountNotice() {
  const { notice, dismissNotice } = useAccount()
  if (!notice) return null
  return (
    <div className="notice" role="status">
      <span>{notice}</span>
      <button type="button" className="btn icon" style={{ width: 32, height: 32, background: 'transparent', color: 'inherit' }} aria-label="Dismiss" onClick={dismissNotice}>
        <CloseIcon width={16} height={16} />
      </button>
    </div>
  )
}

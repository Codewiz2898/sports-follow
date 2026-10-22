import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type Account, type Me, type ServerConfig } from './api'
import { CloseIcon } from './components/Icons'
import { WEB_KEPT, welcome } from './fanChange'
import { changedHands, whoAsker, type WhoAsker } from './resync'

/**
 * Who this device is signed in as (sports_follow/accounts.py), asked at start, which also renews the session,
 * and again when the app wakes or its stream connects again (`recheck`): till the server answers, a failed ask
 * asked again (resync.whoAsker). Signing in, out or deleting the account starts the app over: every list, stream
 * and cached answer belonged to the fan this device was a moment ago. So does finding the device signed out
 * elsewhere, or in to another account in another tab.
 */
interface AccountState {
  me: Me | null  // null until the server answers: unknown, not signed out (BUG-306)
  config: ServerConfig | null
  /** What just happened to this device's sign-in, said once at the top of the page. */
  notice: string | null
  dismissNotice: () => void
  /** After a code signed this device in: the app starts over at `back` (a page of its own), else at /account. */
  signedIn: (account: Account, back?: string) => void
  signOut: (everywhere?: boolean) => Promise<void>
  deleteAccount: () => Promise<void>
  /** Ask again who this device is (the app woke, its stream connected again), at most once in RECHECK_MS; `now`:
   * at once (its stream said its session ended). */
  recheck: (now?: boolean) => void
}

const AccountContext = createContext<AccountState | null>(null)
const NOTICE = 'sf-account-notice'  // what to say once the app has started over
const AFTER = 'sf-after-signin'  // the page to come back to after Google's sign-in, which comes back to /account
const RECHECK_MS = 10_000
const SIGNED_OUT = 'This device was signed out. Sign in again to see your players.'

/** A path of this app's own ("/j/K7Q2M"), or null: nothing to leave the site for. */
function ownPage(path: string | null | undefined): string | null {
  return path && /^\/(?!\/)[A-Za-z0-9/_-]*$/.test(path) ? path : null
}

/** Before leaving for Google: come back to `path`, not /account, once signed in. */
export function afterSignIn(path: string) {
  try { sessionStorage.setItem(AFTER, path) } catch { /* storage blocked: /account it is */ }
}

function takeAfterSignIn(): string | null {
  try {
    const path = sessionStorage.getItem(AFTER)
    sessionStorage.removeItem(AFTER)
    return ownPage(path)
  } catch {
    return null
  }
}

/** Forget what this device kept of the fan it was (fanChange.ts: BUG-169), as it becomes another. */
function forgetKept() {
  try {
    for (const key of WEB_KEPT) localStorage.removeItem(key)
  } catch { /* storage blocked */ }
}

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
  forgetKept()
  try {
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
  const meRef = useRef(me)
  meRef.current = me
  const asked = useRef(Date.now())  // the last time who this device is was asked
  const who = useRef<WhoAsker | null>(null)
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
    // Back from Google (/account?welcome=N, with &teams_dropped=M if some didn't fit: server.google_callback): the
    // server signed this device in.
    const query = new URLSearchParams(window.location.search)
    const back = query.get('welcome')
    if (back !== null) {
      forgetCachedAnswers()
      forgetKept()
      const said = welcome(Number(back) || 0, Number(query.get('teams_dropped')) || 0)
      // Signed in from a page that asked to be come back to (a league's invite): there, saying so.
      const after = takeAfterSignIn()
      if (after) {
        try { sessionStorage.setItem(NOTICE, said) } catch { /* storage blocked */ }
        window.location.replace(after)
        return
      }
      setNotice(said)
    }
    // Turned back at Google's (/account?error=…): no page to come back to after a later sign-in.
    if (new URLSearchParams(window.location.search).has('error')) takeAfterSignIn()
    // Signed out elsewhere (everywhere from another device, the account deleted or blocked), the app kept showing the
    // account, its players and its alerts (BUG-162): asked again as it wakes and as its stream connects again, a
    // device that's someone else now starts over as who it is, here. A first answer is who it is: signed out while
    // the app was closed, it says so.
    const asker = whoAsker(() => api.account(), (now) => {
      const shown = meRef.current
      if (changedHands(shown, now)) {
        const out = !now.signed_in
        startOver(window.location.pathname + window.location.search, out ? SIGNED_OUT : null, out)
        return
      }
      meRef.current = now
      setMe(now)
      if (shown === null && now.signed_in === false && now.reason === 'signed_out') {
        forgetCachedAnswers()
        setNotice((said) => said ?? SIGNED_OUT)
      }
    })
    who.current = asker
    asker.ask()
    api.config().then(setConfig).catch(() => {})
    return () => asker.stop()
  }, [])

  const recheck = useCallback((now = false) => {
    // Not known yet: asked at once (a stream connecting again says the server is back). `now`: anew, an answer under
    // way from before the session ended dropped.
    if (!now && meRef.current !== null && Date.now() - asked.current < RECHECK_MS) return
    asked.current = Date.now()
    who.current?.ask(now)
  }, [])

  useEffect(() => {
    const wake = () => { if (document.visibilityState === 'visible') recheck() }
    document.addEventListener('visibilitychange', wake)
    return () => document.removeEventListener('visibilitychange', wake)
  }, [recheck])

  const dismissNotice = useCallback(() => setNotice(null), [])
  const signedIn = useCallback((account: Account, back?: string) => {
    startOver(ownPage(back) ?? '/account', welcome(account.players_added ?? 0, account.teams_dropped ?? 0), false)
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
    () => ({ me, config, notice, dismissNotice, signedIn, signOut, deleteAccount, recheck }),
    [me, config, notice, dismissNotice, signedIn, signOut, deleteAccount, recheck],
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

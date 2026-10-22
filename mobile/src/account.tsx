// Who this phone is signed in as (sports_follow/accounts.py), as the web's account.tsx: asked at start, which also
// renews the session, and again each time the app comes back to the front. Signing in, out or deleting the account
// starts the app over: every list and cached answer belonged to the fan this phone was a moment ago (the fan's
// providers start over on `epoch`, and this phone's alerts are registered again as who it is now: push.ts). So does
// finding it signed out elsewhere (everywhere from another device, the account deleted or blocked).
//
// The first request at start is POST /api/me/start, which also makes this phone's anonymous fan (its cookie) if it
// has none: a read makes none (server._fan), so until a phone has its fan the screens wait for that answer, and the
// requests they send together are all one fan's (BUG-152).

import AsyncStorage from '@react-native-async-storage/async-storage'
import { router } from 'expo-router'
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AppState, Pressable, Text } from 'react-native'
import { changedHands } from '../../web/src/resync'
import StannNotifications from '../modules/stann-notifications'
import { api, type Account, type Me, type ServerConfig } from './api'
import { CloseIcon } from './icons'
import { t } from './theme'
import { Notice } from './ui'

const HAS_FAN = 'stann.has-fan' // this phone has had its fan made: its screens needn't wait at start
const LONGEST_MS = 4000 // the most a first start waits for its fan (offline: the screens start without one)
const SIGNED_OUT = 'This device was signed out. Sign in again to see your players.'

interface AccountState {
  me: Me | null // null until the server answers
  config: ServerConfig | null
  epoch: number // which fan this run of the app is showing: a sign-in or out starts a new one
  notice: string | null // what just happened to this phone's sign-in, said once at the top of the page
  dismissNotice: () => void
  signedIn: (account: Account) => void // after a code signed this phone in
  signOut: (everywhere?: boolean) => Promise<void>
  deleteAccount: () => Promise<void>
}

const AccountContext = createContext<AccountState | null>(null)

function welcome(added: number): string {
  if (!added) return 'Signed in.'
  return `Signed in. ${added === 1 ? 'The player' : `The ${added} players`} you followed on this device ${added === 1 ? 'is' : 'are'} in your account now.`
}

export function AccountProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const meRef = useRef(me)
  meRef.current = me
  const [ready, setReady] = useState(false) // the fan's screens wait for this phone's fan, the first time
  const [config, setConfig] = useState<ServerConfig | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [epoch, setEpoch] = useState(0)

  /** Who this phone is, its fan made if it has none (POST /api/me/start). */
  const start = useCallback(() => api.start().then((answer) => {
    setMe(answer)
    AsyncStorage.setItem(HAS_FAN, '1').catch(() => {})
    if (answer.signed_in === false && answer.reason === 'signed_out') setNotice((said) => said ?? SIGNED_OUT)
  }).catch(() => setMe((was) => was ?? { signed_in: false })), [])

  useEffect(() => {
    let waiting = true
    const go = () => { if (waiting) { waiting = false; setReady(true) } }
    const timer = setTimeout(go, LONGEST_MS)
    AsyncStorage.getItem(HAS_FAN).then((had) => { if (had) go() }).catch(go)
    start().finally(go)
    api.config().then(setConfig).catch(() => {})
    return () => clearTimeout(timer)
  }, [start])

  /** Forget what this phone showed as the fan it was, and start the app over at `path`, as who it is now. */
  const startOver = useCallback((path: string, said: string | null) => {
    AsyncStorage.removeItem('stann.alerts-seen').catch(() => {}).then(start).finally(() => {
      setNotice(said)
      setEpoch((n) => n + 1)
      router.replace(path as never)
    })
  }, [start])

  // Back in front: asked again who this phone is. Signed out elsewhere, the account page kept the old account and
  // this phone's alerts stopped without a word (BUG-162): it starts over as who it is now, alerts registered again.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'active' || meRef.current === null) return
      api.account().then((now) => {
        if (!changedHands(meRef.current, now)) setMe(now)
        else {
          // Signed out elsewhere, this phone's devices went with its session: no end would take its live cards down.
          if (!now.signed_in) StannNotifications?.takeDownLive(null)
          startOver('/', now.signed_in ? null : SIGNED_OUT)
        }
      }).catch(() => { /* offline: asked again next time */ })
    })
    return () => sub.remove()
  }, [startOver])

  const dismissNotice = useCallback(() => setNotice(null), [])
  const signedIn = useCallback((account: Account) => startOver('/account', welcome(account.players_added ?? 0)), [startOver])
  // Signed out or deleted, the phone is off the server (its session's devices go): its live cards, the account's
  // games, would stay on the lock screen with nothing to end them, so they go now.
  const signOut = useCallback(async (everywhere = false) => {
    await api.signOut(everywhere)
    StannNotifications?.takeDownLive(null)
    startOver('/', everywhere ? 'Signed out on every device.' : 'Signed out. Your players are kept in your account.')
  }, [startOver])
  const deleteAccount = useCallback(async () => {
    await api.deleteAccount()
    StannNotifications?.takeDownLive(null)
    startOver('/', 'Your account and everything kept with it are deleted.')
  }, [startOver])

  const value = useMemo<AccountState>(
    () => ({ me, config, epoch, notice, dismissNotice, signedIn, signOut, deleteAccount }),
    [me, config, epoch, notice, dismissNotice, signedIn, signOut, deleteAccount],
  )
  return <AccountContext value={value}>{ready ? children : null}</AccountContext>
}

export function useAccount(): AccountState {
  const value = use(AccountContext)
  if (!value) throw new Error('useAccount outside AccountProvider')
  return value
}

/** What happened to this phone's sign-in: said once, at the top of the page. */
export function AccountNotice() {
  const { notice, dismissNotice } = useAccount()
  if (!notice) return null
  return (
    <Notice>
      <Text style={[t.small, { fontFamily: 'Manrope_600SemiBold', flex: 1 }]}>{notice}</Text>
      <Pressable onPress={dismissNotice} accessibilityLabel="Dismiss" hitSlop={8} style={{ width: 32, height: 32, alignItems: 'center', justifyContent: 'center' }}>
        <CloseIcon size={16} />
      </Pressable>
    </Notice>
  )
}

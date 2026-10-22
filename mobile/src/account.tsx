// Who this phone is signed in as (sports_follow/accounts.py), as the web's account.tsx: asked at start, which also
// renews the session, and again each time the app comes back to the front; till the server answers, a failed ask
// asked again (resync.whoAsker: unknown, never signed out, BUG-306). Signing in, out or deleting the account
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
import { PHONE_KEPT, welcome } from '../../web/src/fanChange'
import { changedHands, whoAsker, type WhoAsker } from '../../web/src/resync'
import StannNotifications from '../modules/stann-notifications'
import { api, type Account, type Me, type ServerConfig } from './api'
import { forgetFeedSeen } from './feedSeen'
import { CloseIcon } from './icons'
import { t } from './theme'
import { Notice } from './ui'

const HAS_FAN = 'stann.has-fan' // this phone has had its fan made: its screens needn't wait at start
const LONGEST_MS = 4000 // the most a first start waits for its fan (offline: the screens start without one)
const SIGNED_OUT = 'This device was signed out. Sign in again to see your players.'

interface AccountState {
  me: Me | null // null until the server answers: unknown, not signed out
  config: ServerConfig | null
  epoch: number // which fan this run of the app is showing: a sign-in or out starts a new one
  notice: string | null // what just happened to this phone's sign-in, said once at the top of the page
  dismissNotice: () => void
  signedIn: (account: Account) => void // after a code signed this phone in
  signOut: (everywhere?: boolean) => Promise<void>
  deleteAccount: () => Promise<void>
}

const AccountContext = createContext<AccountState | null>(null)

export function AccountProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const meRef = useRef(me)
  meRef.current = me
  const [ready, setReady] = useState(false) // the fan's screens wait for this phone's fan, the first time
  const [config, setConfig] = useState<ServerConfig | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [epoch, setEpoch] = useState(0)
  const who = useRef<WhoAsker | null>(null)
  // A sign-out or deletion here under way: it starts the app over itself, saying so, so an answer meanwhile (the app
  // back in front as it waits) isn't taken for this phone signed out elsewhere, a second start over.
  const leaving = useRef(false)

  /** Forget what this phone showed and kept as the fan it was (fanChange.ts: BUG-169), and start the app over at
   * `path`, as who it is now: unknown till the server says, not the account it was (BUG-281). */
  const startOver = useCallback((path: string, said: string | null) => {
    forgetFeedSeen()
    meRef.current = null
    setMe(null)
    AsyncStorage.multiRemove(PHONE_KEPT).catch(() => {}).then(() => who.current?.ask(true)).finally(() => {
      setNotice(said)
      setEpoch((n) => n + 1)
      router.replace(path as never)
    })
  }, [])

  useEffect(() => {
    let waiting = true
    const go = () => { if (waiting) { waiting = false; setReady(true) } }
    const timer = setTimeout(go, LONGEST_MS)
    AsyncStorage.getItem(HAS_FAN).then((had) => { if (had) go() }).catch(go)
    // Not known yet: who this phone is, its fan made if it has none (POST /api/me/start). Known: asked again.
    const ask = () => (meRef.current === null ? api.start().then((answer) => {
      AsyncStorage.setItem(HAS_FAN, '1').catch(() => {})
      return answer
    }) : api.account())
    const asker = whoAsker(ask, (now) => {
      const shown = meRef.current
      if (changedHands(shown, now)) {
        if (leaving.current) return
        // Signed out elsewhere, this phone's devices went with its session: no end would take its live cards down.
        if (!now.signed_in) StannNotifications?.takeDownLive(null)
        startOver('/', now.signed_in ? null : SIGNED_OUT)
        return
      }
      meRef.current = now
      setMe(now)
      if (shown === null && now.signed_in === false && now.reason === 'signed_out') setNotice((said) => said ?? SIGNED_OUT)
    })
    who.current = asker
    asker.ask().finally(go)
    api.config().then(setConfig).catch(() => {})
    return () => {
      clearTimeout(timer)
      asker.stop()
    }
  }, [startOver])

  // Back in front: asked again who this phone is. Signed out elsewhere, the account page kept the old account and
  // this phone's alerts stopped without a word (BUG-162): it starts over as who it is now, alerts registered again.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') who.current?.ask()
    })
    return () => sub.remove()
  }, [])

  const dismissNotice = useCallback(() => setNotice(null), [])
  const signedIn = useCallback((account: Account) => startOver('/account', welcome(account.players_added ?? 0, account.teams_dropped ?? 0)), [startOver])
  // Signed out or deleted, the phone is off the server (its session's devices go): its live cards, the account's
  // games, would stay on the lock screen with nothing to end them, so they go now. If the call fails, the phone is as
  // it was, and who it is is asked again: the server may have ended its session all the same.
  const leave = useCallback(async (end: () => Promise<unknown>, said: string) => {
    if (leaving.current) return
    leaving.current = true
    try {
      await end()
    } catch (e) {
      leaving.current = false
      who.current?.ask(true)
      throw e
    }
    StannNotifications?.takeDownLive(null)
    startOver('/', said)  // as who it is now from here on: unknown till the server says (no change of hands from that)
    leaving.current = false
  }, [startOver])
  const signOut = useCallback((everywhere = false) => leave(() => api.signOut(everywhere), everywhere ? 'Signed out on every device.' : 'Signed out. Your players are kept in your account.'), [leave])
  const deleteAccount = useCallback(() => leave(() => api.deleteAccount(), 'Your account and everything kept with it are deleted.'), [leave])

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

// Who this phone is signed in as (sports_follow/accounts.py), as the web's account.tsx: asked once at start,
// which also renews the session. Signing in, out or deleting the account starts the app over: every list and
// cached answer belonged to the fan this phone was a moment ago (the fan's providers start over on `epoch`).

import AsyncStorage from '@react-native-async-storage/async-storage'
import { router } from 'expo-router'
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Pressable, Text } from 'react-native'
import { api, type Account, type Me, type ServerConfig } from './api'
import { CloseIcon } from './icons'
import { t } from './theme'
import { Notice } from './ui'

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
  const [config, setConfig] = useState<ServerConfig | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [epoch, setEpoch] = useState(0)

  const ask = useCallback(() => api.account().then((answer) => {
    setMe(answer)
    if (answer.signed_in === false && answer.reason === 'signed_out') setNotice((said) => said ?? 'This device was signed out. Sign in again to see your players.')
  }).catch(() => setMe({ signed_in: false })), [])

  useEffect(() => {
    ask()
    api.config().then(setConfig).catch(() => {})
  }, [ask])

  /** Forget what this phone showed as the fan it was, and start the app over at `path`. */
  const startOver = useCallback((path: string, said: string | null) => {
    AsyncStorage.removeItem('stann.alerts-seen').catch(() => {}).finally(() => {
      setNotice(said)
      setEpoch((n) => n + 1)
      ask()
      router.replace(path as never)
    })
  }, [ask])

  const dismissNotice = useCallback(() => setNotice(null), [])
  const signedIn = useCallback((account: Account) => startOver('/account', welcome(account.players_added ?? 0)), [startOver])
  const signOut = useCallback(async (everywhere = false) => {
    await api.signOut(everywhere)
    startOver('/', everywhere ? 'Signed out on every device.' : 'Signed out. Your players are kept in your account.')
  }, [startOver])
  const deleteAccount = useCallback(async () => {
    await api.deleteAccount()
    startOver('/', 'Your account and everything kept with it are deleted.')
  }, [startOver])

  const value = useMemo<AccountState>(
    () => ({ me, config, epoch, notice, dismissNotice, signedIn, signOut, deleteAccount }),
    [me, config, epoch, notice, dismissNotice, signedIn, signOut, deleteAccount],
  )
  return <AccountContext value={value}>{children}</AccountContext>
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

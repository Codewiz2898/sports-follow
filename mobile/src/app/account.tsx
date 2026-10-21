// Account, the web's AccountPage.tsx: signing in with a code by email, and once signed in, who, signing out
// and deleting the account. (Google sign-in comes when the app has a Google client of its own.) And, for everyone,
// whether the Feed's videos start by themselves (autoplay.ts; this phone's choice, so not kept with the account).

import AsyncStorage from '@react-native-async-storage/async-storage'
import { router } from 'expo-router'
import { useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, TextInput, View } from 'react-native'
import { useAccount } from '../account'
import { AUTOPLAY, setAutoplay, useAutoplaySetting } from '../autoplay'
import { ApiError, api, type Account } from '../api'
import { Empty } from '../blocks'
import { colour, dsp, font, t } from '../theme'
import { BackHead, Btn, Card, ErrorBox, Eyebrow, ListCard, Section, Spinner } from '../ui'
import { Page } from '../page'

export default function AccountScreen() {
  const { me } = useAccount()
  return (
    <Page>
      <BackHead title="Account" />
      {me === null ? <Empty><Spinner /></Empty> : me.signed_in ? <SignedIn account={me} /> : <SignIn />}
      <Videos />
      <Text onPress={() => router.push('/privacy')} style={[t.tiny, t.muted, t.link]}>What we keep, and why</Text>
    </Page>
  )
}

/** Whether the Feed's videos play by themselves as they come on screen, muted: Always (the default), On Wi-Fi, Off. */
function Videos() {
  const autoplay = useAutoplaySetting()
  return (
    <Section title="Videos">
      <Card pad style={{ gap: 12 }}>
        <View style={{ gap: 4 }}>
          <Text style={t.title}>Play videos as you scroll</Text>
          <Text style={[t.small, t.muted]}>In your Feed, the video on screen starts by itself, muted. A tap on it turns the sound on.</Text>
        </View>
        <View style={s.choices} accessibilityRole="radiogroup" accessibilityLabel="Play videos as you scroll">
          {AUTOPLAY.map(([key, label]) => (
            <Btn key={key} small kind={autoplay === key ? 'primary' : 'outline'} label={label} accessibilityLabel={`${label}${autoplay === key ? ', chosen' : ''}`}
              onPress={() => setAutoplay(key)} />
          ))}
        </View>
      </Card>
    </Section>
  )
}

// ---------------------------------------------------------------- signing in

const PENDING = 'stann.signin' // the address a code went to, so the app can come back while the fan is in Mail
const CODE_LIFE_MS = 10 * 60_000

function SignIn() {
  const { config, signedIn } = useAccount()
  const [email, setEmail] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [wait, setWait] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const codeInput = useRef<TextInput>(null)

  useEffect(() => {
    AsyncStorage.getItem(PENDING).then((raw) => {
      const saved = raw ? (JSON.parse(raw) as { email: string; sentAt: number }) : null
      if (saved && Date.now() - saved.sentAt < CODE_LIFE_MS) {
        setEmail(saved.email)
        setSentTo(saved.email)
        setWait(Math.max(0, 60 - Math.round((Date.now() - saved.sentAt) / 1000)))
      }
    }).catch(() => {})
  }, [])
  useEffect(() => {
    if (wait <= 0) return
    const timer = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(timer)
  }, [wait])
  useEffect(() => { if (sentTo) codeInput.current?.focus() }, [sentTo])

  const send = async (to: string) => {
    setBusy(true)
    setError(null)
    try {
      const { resend_in } = await api.emailStart(to)
      setSentTo(to)
      setCode('')
      setWait(resend_in)
      AsyncStorage.setItem(PENDING, JSON.stringify({ email: to, sentAt: Date.now() })).catch(() => {})
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const startAgain = () => {
    setSentTo(null)
    setCode('')
    AsyncStorage.removeItem(PENDING).catch(() => {})
  }
  const verify = async (value: string) => {
    if (!sentTo || busy) return
    setBusy(true)
    setError(null)
    try {
      const account: Account = await api.emailVerify(sentTo, value)
      AsyncStorage.removeItem(PENDING).catch(() => {})
      signedIn(account)
    } catch (e) {
      setError((e as Error).message)
      setCode('')
      if (e instanceof ApiError && e.status === 429) startAgain()
      setBusy(false)
    }
  }
  const onCode = (value: string) => {
    const digits = value.replace(/\D/g, '').slice(0, 6)
    setCode(digits)
    if (digits.length === 6) verify(digits)
  }

  const emailOff = config !== null && !config.email_sign_in
  return (
    <>
      <View style={s.hero}>
        <Text style={[dsp(44), { color: '#FFFFFF' }]}>Save your players</Text>
        <Text style={s.heroText}>Sign in to keep your players, alerts and muted games on every phone and computer. Everything you follow on this device comes with you.</Text>
      </View>

      {emailOff ? (
        <Empty>Signing in isn't set up on this server yet.</Empty>
      ) : sentTo === null ? (
        <Card pad style={{ gap: 12 }}>
          <Text style={t.title}>Your email</Text>
          <TextInput value={email} onChangeText={setEmail} onSubmitEditing={() => email.trim() && send(email.trim())} autoCapitalize="none" autoCorrect={false} autoComplete="email" keyboardType="email-address" placeholder="you@example.com" placeholderTextColor={colour.muted} style={s.field} />
          {error ? <ErrorBox>{error}</ErrorBox> : null}
          <Btn kind="primary" label="Email me a code" busy={busy} disabled={!email.trim()} onPress={() => send(email.trim())} />
          <Text style={[t.small, t.muted]}>No password: we email you a 6-digit code, and that's the sign-in. A new address gets an account.</Text>
        </Card>
      ) : (
        <Card pad style={{ gap: 12 }}>
          <Text style={t.title}>Enter the code we sent to {sentTo}</Text>
          <TextInput ref={codeInput} value={code} onChangeText={onCode} editable={!busy} keyboardType="number-pad" autoComplete="one-time-code" textContentType="oneTimeCode" maxLength={6} placeholder="000000" placeholderTextColor={colour.line} style={[s.field, s.code]} />
          {error ? <ErrorBox>{error}</ErrorBox> : null}
          <Btn kind="primary" label="Sign in" busy={busy} disabled={code.length !== 6} onPress={() => verify(code)} />
          <Text style={[t.small, t.muted]}>It works for 10 minutes. Not there? Look in spam, or send a new one.</Text>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 12 }}>
            <Btn kind="link" label={wait > 0 ? `Send a new code in ${wait}s` : 'Send a new code'} disabled={busy || wait > 0} onPress={() => send(sentTo)} />
            <Btn kind="link" label="Use another email" disabled={busy} onPress={startAgain} />
          </View>
        </Card>
      )}
    </>
  )
}

// ---------------------------------------------------------------- signed in

function SignedIn({ account }: { account: Account }) {
  const { signOut, deleteAccount } = useAccount()
  const [busy, setBusy] = useState<'out' | 'everywhere' | 'delete' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const since = new Date(account.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
  const run = async (what: 'out' | 'everywhere' | 'delete', work: () => Promise<void>) => {
    setBusy(what)
    setError(null)
    try {
      await work() // the app starts over when it's done
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }
  return (
    <>
      <Card pad style={{ gap: 6 }}>
        <Eyebrow>Signed in</Eyebrow>
        <Text style={[t.title, { fontSize: 19, lineHeight: 25 }]}>{account.email}</Text>
        <Text style={[t.small, t.muted]}>{account.google ? 'With Google, or a code sent to this address' : 'With a code sent to this address'} · since {since}</Text>
      </Card>
      {error ? <ErrorBox>{error}</ErrorBox> : null}

      <Section title="Sign out">
        <ListCard>
          {[
            <View key="this" style={s.row}>
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={t.title}>This device</Text>
                <Text style={[t.small, t.muted]}>Your players stay in your account. This device starts fresh, and its alerts stop.</Text>
              </View>
              <Btn small label="Sign out" busy={busy === 'out'} disabled={busy !== null} onPress={() => run('out', () => signOut())} />
            </View>,
            <View key="all" style={s.row}>
              <View style={{ flex: 1, gap: 4 }}>
                <Text style={t.title}>Every device</Text>
                <Text style={[t.small, t.muted]}>Every phone and computer signed in to this account, this one too.</Text>
              </View>
              <Btn small label="Sign out all" busy={busy === 'everywhere'} disabled={busy !== null} onPress={() => run('everywhere', () => signOut(true))} />
            </View>,
          ]}
        </ListCard>
      </Section>

      <Section title="Delete account">
        <Card pad style={{ gap: 12 }}>
          {!confirming ? (
            <>
              <Text style={t.small}>Deletes your account and everything kept with it.</Text>
              <Btn kind="danger" label="Delete account" style={{ alignSelf: 'flex-start' }} onPress={() => setConfirming(true)} />
            </>
          ) : (
            <>
              <Text style={t.title}>Delete {account.email} for good?</Text>
              <View style={{ gap: 4 }}>
                {['The players you follow and how much you hear about each', 'Muted games and your alert history', 'Alerts on every device signed in to it', 'Your email address'].map((x) => <Text key={x} style={t.small}>•  {x}</Text>)}
              </View>
              <Text style={[t.small, t.muted]}>This can't be undone. Players' pages stay: they're everyone's. Our daily backups are kept for 14 days.</Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
                <Btn kind="primary" label="Delete for good" colours={{ bg: colour.danger, fg: '#FFFFFF', border: colour.danger }} busy={busy === 'delete'} disabled={busy !== null} onPress={() => run('delete', deleteAccount)} />
                <Btn label="Keep my account" disabled={busy !== null} onPress={() => setConfirming(false)} />
              </View>
            </>
          )}
        </Card>
      </Section>
    </>
  )
}

const s = StyleSheet.create({
  hero: { overflow: 'hidden', gap: 14, padding: 18, borderRadius: 26, backgroundColor: '#0D0D0D' },
  heroText: { fontFamily: font[600], fontSize: 15, lineHeight: 22, color: '#FFFFFF' },
  field: { height: 54, paddingHorizontal: 16, borderRadius: 16, borderWidth: 2, borderColor: colour.text, backgroundColor: colour.surface, fontFamily: font[600], fontSize: 17, color: colour.text },
  code: { height: 64, fontFamily: font.display, fontSize: 36, letterSpacing: 11, textAlign: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
})

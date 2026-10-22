import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { afterSignIn, useAccount } from '../account'
import { ApiError, api, type Account } from '../api'
import { AUTOPLAY, setAutoplay, useAutoplaySetting } from '../autoplay'
import { SectionHead } from '../components/Blocks'
import { BackIcon } from '../components/Icons'

/** Signing in (a code by email), and once signed in: who, signing out, deleting the account. And, for everyone,
 * whether the Feed's videos start by themselves (autoplay.ts; this browser's choice, so not kept with the account). */
export function AccountPage() {
  const { me } = useAccount()
  const [params, setParams] = useSearchParams()
  const [failed] = useState(() => GOOGLE_ERRORS[params.get('error') ?? ''] ?? null)
  // Back from Google (?welcome=N, said by the account notice) or turned back there (?error=…): said once.
  useEffect(() => { if (params.has('welcome') || params.has('error')) setParams({}, { replace: true }) }, [params, setParams])
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/" className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">Account</h1>
      </div>
      {me === null ? <div className="card empty small"><span className="spinner" /></div> : me.signed_in ? <SignedIn account={me} /> : <SignIn failed={failed} />}
      <Videos />
      <span className="row tiny muted" style={{ justifyContent: 'flex-start' }}>
        <Link to="/privacy" className="link">What we keep, and why</Link>
        <Link to="/terms" className="link">Terms</Link>
      </span>
    </>
  )
}

/** Whether the Feed's videos play by themselves as they come on screen, muted until the fan turns the sound on
 * (sound.ts): Always (the default), On Wi-Fi, Off. */
function Videos() {
  const autoplay = useAutoplaySetting()
  return (
    <section className="section" aria-label="Videos">
      <SectionHead title="Videos" />
      <div className="card card-pad stack" style={{ gap: 12 }}>
        <span className="stack" style={{ gap: 4 }}>
          <span className="title" id="autoplay-label">Play videos as you scroll</span>
          <span className="small muted">In your Feed, the video on screen starts by itself, muted until you turn the sound on. Then the next ones play with it, until you mute. Never while your browser is saving data.</span>
        </span>
        <div className="segmented" role="radiogroup" aria-labelledby="autoplay-label">
          {AUTOPLAY.map(([key, label]) => (
            <button key={key} type="button" role="radio" aria-checked={autoplay === key} onClick={() => setAutoplay(key)}>{label}</button>
          ))}
        </div>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- signing in

const GOOGLE_ERRORS: Record<string, string> = {
  google: "Couldn't sign in with Google. Try again, or get a code by email.",
  google_linked: "This email address's account is linked to a different Google account. Sign in with a code by email instead.",
  google_off: "Signing in with Google isn't set up on this server yet.",
  blocked: 'This account has been blocked.',
}

/** An iPhone home-screen app: Google's sign-in leaves it for a browser sheet, not yet tried there. */
const IPHONE_APP = /iPhone|iPad|iPod/.test(navigator.userAgent) && window.matchMedia('(display-mode: standalone)').matches

function GoogleButton({ back }: { back?: string }) {
  return (
    <a href="/api/auth/google/start" className="btn google" onClick={() => { if (back) afterSignIn(back) }}>
      <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
      </svg>
      Continue with Google
    </a>
  )
}

const PENDING = 'sf-signin'  // the address a code went to, so the app can reload while the fan is in Mail
const CODE_LIFE_MS = 10 * 60_000

function readPending(): { email: string; sentAt: number } | null {
  try {
    const saved = JSON.parse(localStorage.getItem(PENDING) || 'null')
    if (saved && typeof saved.email === 'string' && Date.now() - saved.sentAt < CODE_LIFE_MS) return saved
  } catch { /* storage blocked or empty */ }
  return null
}

/**
 * Signing in with a code by email (or Google): the account page's, and a league invite's, which comes back to
 * `back` once signed in and says why in `intro` instead of the account page's hero.
 */
export function SignIn({ failed, back, intro }: { failed: string | null; back?: string; intro?: ReactNode }) {
  const { config, signedIn } = useAccount()
  const pending = readPending()
  const [email, setEmail] = useState(pending?.email ?? '')
  const [sentTo, setSentTo] = useState<string | null>(pending?.email ?? null)
  const [code, setCode] = useState('')
  const [wait, setWait] = useState(() => (pending ? Math.max(0, 60 - Math.round((Date.now() - pending.sentAt) / 1000)) : 0))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(failed)
  const codeInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(t)
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
      try { localStorage.setItem(PENDING, JSON.stringify({ email: to, sentAt: Date.now() })) } catch { /* storage blocked */ }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const verify = async (value: string) => {
    if (!sentTo || busy) return
    setBusy(true)
    setError(null)
    try {
      const account: Account = await api.emailVerify(sentTo, value)
      try { localStorage.removeItem(PENDING) } catch { /* storage blocked */ }
      signedIn(account, back)
    } catch (e) {
      setError((e as Error).message)
      setCode('')
      if (e instanceof ApiError && e.status === 429) startAgain()
      setBusy(false)
    }
  }

  const startAgain = () => {
    setSentTo(null)
    setCode('')
    try { localStorage.removeItem(PENDING) } catch { /* storage blocked */ }
  }

  const onEmail = (e: FormEvent) => {
    e.preventDefault()
    if (email.trim()) send(email.trim())
  }
  const onCode = (value: string) => {
    const digits = value.replace(/\D/g, '').slice(0, 6)
    setCode(digits)
    if (digits.length === 6) verify(digits)
  }

  const emailOff = config !== null && !config.email_sign_in
  const google = Boolean(config?.google_sign_in) && !IPHONE_APP
  return (
    <>
      {intro ?? (
        <section className="hero plain" aria-label="Sign in">
          <span className="dsp" style={{ fontSize: 44 }}>Save your players</span>
          <p style={{ fontWeight: 600, maxWidth: '38ch' }}>
            Sign in to keep your players, alerts and muted games on every phone and computer. Everything you follow on this device comes with you.
          </p>
        </section>
      )}

      {emailOff && !google ? (
        <div className="card empty small">Signing in isn't set up on this server yet.</div>
      ) : sentTo === null ? (
        <form className="card card-pad stack" style={{ gap: 12 }} onSubmit={onEmail}>
          {google && <GoogleButton back={back} />}
          {google && !emailOff && <span className="or small muted">or</span>}
          {!emailOff && (
            <>
              <label htmlFor="signin-email" className="title">Your email</label>
              <input
                id="signin-email" className="field" type="email" autoComplete="email" inputMode="email" spellCheck={false} required
                placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)}
              />
            </>
          )}
          {error && <div className="error-box">{error}</div>}
          {!emailOff && (
            <>
              <button type="submit" className="btn primary" disabled={busy || !email.trim()}>{busy && <span className="spinner" />}Email me a code</button>
              <span className="small muted">No password: we email you a 6-digit code, and that's the sign-in. A new address gets an account.</span>
            </>
          )}
        </form>
      ) : (
        <form className="card card-pad stack" style={{ gap: 12 }} onSubmit={(e) => { e.preventDefault(); if (code.length === 6) verify(code) }}>
          <label htmlFor="signin-code" className="title">Enter the code we sent to {sentTo}</label>
          <input
            id="signin-code" ref={codeInput} className="field code" type="text" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]*"
            maxLength={6} placeholder="000000" value={code} onChange={(e) => onCode(e.target.value)} disabled={busy} aria-describedby="signin-code-help"
          />
          {error && <div className="error-box">{error}</div>}
          <button type="submit" className="btn primary" disabled={busy || code.length !== 6}>{busy && <span className="spinner" />}Sign in</button>
          <span id="signin-code-help" className="small muted">It works for 10 minutes. Not there? Look in spam, or send a new one.</span>
          <div className="row">
            <button type="button" className="btn link" disabled={busy || wait > 0} onClick={() => send(sentTo)}>
              {wait > 0 ? `Send a new code in ${wait}s` : 'Send a new code'}
            </button>
            <button type="button" className="btn link" disabled={busy} onClick={startAgain}>Use another email</button>
          </div>
        </form>
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
      await work()  // the app starts over when it's done
    } catch (e) {
      setError((e as Error).message)
      setBusy(null)
    }
  }

  return (
    <>
      <section className="card card-pad stack" style={{ gap: 6 }} aria-label="Signed in as">
        <span className="eyebrow">Signed in</span>
        <span className="title" style={{ fontSize: 19 }}>{account.email}</span>
        <span className="small muted">{account.google ? 'With Google, or a code sent to this address' : 'With a code sent to this address'} · since {since}</span>
      </section>
      {error && <div className="error-box">{error}</div>}

      <section className="section" aria-label="Signing out">
        <SectionHead title="Sign out" />
        <ul className="card list">
          <li className="row">
            <span className="stack">
              <span className="title">This device</span>
              <span className="small muted">Your players stay in your account. This device starts fresh, and its alerts stop.</span>
            </span>
            <button type="button" className="btn small" disabled={busy !== null} onClick={() => run('out', () => signOut())}>{busy === 'out' && <span className="spinner" />}Sign out</button>
          </li>
          <li className="row">
            <span className="stack">
              <span className="title">Every device</span>
              <span className="small muted">Every phone and computer signed in to this account, this one too.</span>
            </span>
            <button type="button" className="btn small" disabled={busy !== null} onClick={() => run('everywhere', () => signOut(true))}>{busy === 'everywhere' && <span className="spinner" />}Sign out all</button>
          </li>
        </ul>
      </section>

      <section className="section" aria-label="Delete account">
        <SectionHead title="Delete account" />
        <div className="card card-pad stack" style={{ gap: 12 }}>
          {!confirming ? (
            <>
              <span className="small">Deletes your account and everything kept with it.</span>
              <button type="button" className="btn danger" style={{ alignSelf: 'flex-start' }} onClick={() => setConfirming(true)}>Delete account</button>
            </>
          ) : (
            <>
              <span className="title">Delete {account.email} for good?</span>
              <ul className="small" style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <li>The players you follow and how much you hear about each</li>
                <li>Muted games and your alert history</li>
                <li>Alerts on every device signed in to it</li>
                <li>Your email address</li>
              </ul>
              <span className="small muted">This can't be undone. Players' pages stay: they're everyone's. Our daily backups are kept for 14 days.</span>
              <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap' }}>
                <button type="button" className="btn danger primary" disabled={busy !== null} onClick={() => run('delete', deleteAccount)}>{busy === 'delete' && <span className="spinner" />}Delete for good</button>
                <button type="button" className="btn" disabled={busy !== null} onClick={() => setConfirming(false)}>Keep my account</button>
              </div>
            </>
          )}
        </div>
      </section>
    </>
  )
}

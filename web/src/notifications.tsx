import { useCallback, useEffect, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, type AlertLevel, type Card, type PlayerMoment } from './api'
import { APP_NAME } from './app'
import { Avatar } from './components/Blocks'
import { CloseIcon } from './components/Icons'
import { sportInfo } from './sports'

export const LEVELS: { value: AlertLevel; label: string; short: string }[] = [
  { value: 'key', label: 'Key moments', short: 'Key' },
  { value: 'everything', label: 'Everything', short: 'All' },
  { value: 'results', label: 'Results only', short: 'Results' },
  { value: 'off', label: 'Off', short: 'Off' },
]

/** A level in a line (`brief`) and in full (`hint`), for a player of this sport: what key moments are, and
 * what everything adds, as the sport says (its alerts, from GET /api/sports; sports_follow/sports/). */
export function levelText(level: AlertLevel, sport?: string | null): { brief: string; hint: string } {
  const { key, more } = sportInfo(sport).alerts
  if (level === 'key') return { brief: key[0], hint: key[1] }
  if (level === 'everything') return { brief: `Plus ${more[0]}, without sound`, hint: `Key moments, plus ${more[1]}. Those arrive without sound` }
  if (level === 'results') return { brief: 'Just the final result', hint: 'Just the final result' }
  return { brief: 'Nothing for this player', hint: 'No notifications for this player' }
}

/** Whether a fan who chose `level` for a player hears about a moment (moments.wanted on the server). */
export function wanted(level: AlertLevel, moment: Pick<PlayerMoment, 'level' | 'kind'>): boolean {
  if (level === 'everything') return true
  if (level === 'results') return moment.kind === 'final'
  if (level === 'off') return false
  return moment.level === 'key'
}

const QUIET = 'sf-quiet-hours'
type Quiet = { start: string | null; end: string | null }

function readQuiet(): Quiet {
  try {
    const saved = JSON.parse(localStorage.getItem(QUIET) || 'null')
    if (saved && typeof saved === 'object') return { start: saved.start ?? null, end: saved.end ?? null }
  } catch { /* storage blocked or empty */ }
  return { start: null, end: null }
}

function keyBytes(base64url: string): Uint8Array {
  const b64 = base64url.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (base64url.length % 4)) % 4)
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
}

/**
 * Notifications on this device: Web Push through the service worker (production builds only).
 * Returns why it's unavailable when it is (a dev build, an iPhone browser tab, a server without keys).
 */
export function usePush() {
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  const [subscription, setSubscription] = useState<PushSubscription | null>(null)
  const [permission, setPermission] = useState<NotificationPermission>(() => (supported ? Notification.permission : 'denied'))
  const [serverOn, setServerOn] = useState<boolean | null>(null)
  const [quiet, setQuiet] = useState<Quiet>(readQuiet)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    api.pushInfo().then((r) => setServerOn(r.enabled)).catch(() => setServerOn(false))
    if (supported && import.meta.env.PROD) {
      navigator.serviceWorker.ready.then((reg) => reg.pushManager.getSubscription()).then(setSubscription).catch(() => {})
    }
  }, [supported])

  const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent)
  const standalone = window.matchMedia('(display-mode: standalone)').matches
  const unavailable = !import.meta.env.PROD
    ? 'Notifications work in the built app: run `npm run build` in web/ and open http://localhost:8421.'
    : !supported && iphone && !standalone
      ? `On iPhone, add ${APP_NAME} to your Home Screen first (Share, then Add to Home Screen), and turn notifications on from the app.`
      : !supported
        ? "This browser can't show notifications."
        : serverOn === false
          ? "Notifications aren't set up on this server yet."
          : null

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true)
    setMessage(null)
    try {
      await work()
    } catch (e) {
      setMessage((e as Error).message || 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }, [])

  const enable = () => run(async () => {
    const granted = await Notification.requestPermission()
    setPermission(granted)
    if (granted !== 'granted') {
      setMessage("Notifications are blocked for this site. Allow them in the browser's site settings, then try again.")
      return
    }
    const { public_key } = await api.pushInfo()
    if (!public_key) throw new Error("Notifications aren't set up on this server yet.")
    const reg = await navigator.serviceWorker.ready
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) as BufferSource })
    await api.pushSubscribe(sub.toJSON(), quiet)
    setSubscription(sub)
  })

  const disable = () => run(async () => {
    if (!subscription) return
    await api.pushUnsubscribe(subscription.endpoint)
    await subscription.unsubscribe()
    setSubscription(null)
  })

  const saveQuiet = (next: Quiet) => run(async () => {
    setQuiet(next)
    try { localStorage.setItem(QUIET, JSON.stringify(next)) } catch { /* storage blocked */ }
    if (subscription) await api.pushSubscribe(subscription.toJSON(), next)
    setMessage(next.start && next.end ? `Quiet from ${next.start} to ${next.end}: notifications arrive without sound.` : 'Quiet hours off.')
  })

  const test = () => run(async () => {
    const { sent } = await api.pushTest()
    setMessage(sent ? 'Sent. It should arrive in a few seconds.' : 'No device took it. Turn notifications off and on again here.')
  })

  return { unavailable, on: Boolean(subscription), permission, quiet, busy, message, enable, disable, saveQuiet, test }
}

/**
 * Hands the server this device's subscription once each time the app starts, so the device belongs to
 * whoever the app is now (after signing in or out), and one the server dropped (trimmed for a newer
 * device, or signed out somewhere else) comes back while the browser still shows notifications as on.
 */
export function PushResync() {
  useEffect(() => {
    if (!import.meta.env.PROD || !('serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window)) return
    if (Notification.permission !== 'granted') return
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => sub && api.pushSubscribe(sub.toJSON(), readQuiet()))
      .catch(() => { /* offline: next start */ })
  }, [])
  return null
}

/** How much to hear about one player: four choices side by side. Arrow keys move between them. */
export function AlertLevels({ value, onChange, label }: { value: AlertLevel; onChange: (level: AlertLevel) => void; label: string }) {
  const move = (e: KeyboardEvent<HTMLButtonElement>) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const buttons = Array.from(e.currentTarget.parentElement?.querySelectorAll('button') ?? [])
    const i = (buttons.indexOf(e.currentTarget) + step + LEVELS.length) % LEVELS.length
    onChange(LEVELS[i].value)
    buttons[i]?.focus()
  }
  return (
    <div className="segmented full" role="radiogroup" aria-label={label}>
      {LEVELS.map((l) => (
        <button key={l.value} type="button" role="radio" aria-checked={value === l.value} tabIndex={value === l.value ? 0 : -1} title={l.label} onClick={() => onChange(l.value)} onKeyDown={move}>
          {l.short}
        </button>
      ))}
    </div>
  )
}

/** Key moments for followed players, as toasts while the app is open, each with the player's tile. */
export function MomentToasts({ moments, dismiss, playerOf }: { moments: PlayerMoment[]; dismiss: (id: number) => void; playerOf: (id: number) => Card | undefined }) {
  if (!moments.length) return null
  return (
    <div className="toasts" role="status" aria-live="polite">
      {moments.map((m) => {
        const player = playerOf(m.player_id)?.player
        return (
          <div key={m.id} className="toast">
            {player && <Avatar name={player.name} sport={player.sport} photo={player.photo} />}
            <Link to={m.url} onClick={() => dismiss(m.id)} className="stack" style={{ minWidth: 0, flex: 1 }}>
              <span className="title">{m.title}</span>
              {m.body && <span className="small muted">{m.body}</span>}
            </Link>
            <button type="button" className="btn icon" style={{ width: 32, height: 32, background: 'transparent', color: 'inherit' }} onClick={() => dismiss(m.id)} aria-label="Dismiss"><CloseIcon width={16} height={16} /></button>
          </div>
        )
      })}
    </div>
  )
}

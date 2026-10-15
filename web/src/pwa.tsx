import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { APP_NAME } from './app'
import { PlusIcon } from './components/Icons'

/** Chrome's install offer (Android, desktop); Safari has none: iPhone installs from the Share menu. */
interface InstallPrompt extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

interface Pwa {
  canInstall: boolean
  install: () => Promise<void>
  updateReady: boolean
  applyUpdate: () => void
  online: boolean
}

const Ctx = createContext<Pwa | null>(null)
const HOUR = 3600e3

/**
 * The installable app: registers the service worker (public/sw.js) in production builds, keeps
 * Chrome's install offer for an "Install app" button, and says when a new version is waiting or the
 * device is offline. A new version waits until the fan taps Reload, so a live game isn't cut off.
 */
export function PwaProvider({ children }: { children: ReactNode }) {
  const [offer, setOffer] = useState<InstallPrompt | null>(null)
  const [waiting, setWaiting] = useState<ServiceWorker | null>(null)
  // The new version took over from another tab's Reload: this tab still runs the old app until it reloads.
  const [replaced, setReplaced] = useState(false)
  const [online, setOnline] = useState(() => navigator.onLine)
  const updating = useRef(false)

  useEffect(() => {
    const onOffer = (e: Event) => { e.preventDefault(); setOffer(e as InstallPrompt) }
    const onInstalled = () => setOffer(null)
    const onOnline = () => setOnline(true)
    const onOffline = () => setOnline(false)
    window.addEventListener('beforeinstallprompt', onOffer)
    window.addEventListener('appinstalled', onInstalled)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    return () => {
      window.removeEventListener('beforeinstallprompt', onOffer)
      window.removeEventListener('appinstalled', onInstalled)
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
    }
  }, [])

  useEffect(() => {
    // Not in `vite dev`: a worker caching the dev server's modules would serve stale code.
    if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
    const sw = navigator.serviceWorker
    let controlled = Boolean(sw.controller)
    const onChange = () => {
      if (updating.current) return window.location.reload()
      // Another tab's Reload: the worker that was waiting is the one in charge now. The very first
      // worker taking over a fresh page isn't a new version.
      setWaiting(null)
      if (controlled) setReplaced(true)
      controlled = true
    }
    sw.addEventListener('controllerchange', onChange)
    let timer: number | undefined
    sw.register('/sw.js').then((reg) => {
      const watch = (worker: ServiceWorker | null) => {
        if (!worker) return
        const check = () => {
          if (worker.state === 'installed' && sw.controller) setWaiting(worker)
          // A newer version replaced it before it was used: its Reload would do nothing.
          if (worker.state === 'redundant') setWaiting((w) => (w === worker ? null : w))
        }
        check()
        worker.addEventListener('statechange', check)
      }
      if (reg.waiting && sw.controller) setWaiting(reg.waiting)
      // One may already be on its way: the browser checks for a new version as the page loads.
      watch(reg.installing)
      reg.addEventListener('updatefound', () => watch(reg.installing))
      // An installed app can stay open for days; look for a new version every hour.
      timer = window.setInterval(() => reg.update().catch(() => {}), HOUR)
    }).catch(() => {})
    return () => { sw.removeEventListener('controllerchange', onChange); window.clearInterval(timer) }
  }, [])

  const install = useCallback(async () => {
    if (!offer) return
    await offer.prompt()
    await offer.userChoice
    setOffer(null)  // an offer can be used once
  }, [offer])

  const applyUpdate = useCallback(() => {
    if (!waiting) return window.location.reload()  // already in charge (another tab's Reload): just load it
    updating.current = true
    waiting.postMessage('skip-waiting')
  }, [waiting])

  const value = useMemo<Pwa>(() => ({ canInstall: Boolean(offer), install, updateReady: Boolean(waiting) || replaced, applyUpdate, online }), [offer, install, waiting, replaced, applyUpdate, online])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function usePwa(): Pwa {
  const value = useContext(Ctx)
  if (!value) throw new Error('usePwa outside PwaProvider')
  return value
}

export function InstallButton({ compact }: { compact?: boolean }) {
  const { canInstall, install } = usePwa()
  if (!canInstall) return null
  return (
    <button type="button" className={compact ? 'btn small' : 'btn primary'} onClick={install}>
      <PlusIcon width={16} height={16} />Install app
    </button>
  )
}

/** Offline and new-version notices, above every page. */
export function PwaNotices() {
  const { online, updateReady, applyUpdate } = usePwa()
  return (
    <>
      {!online && (
        <div className="notice" role="status">You're offline. Showing the last update; scores refresh when you're back online.</div>
      )}
      {updateReady && (
        <div className="notice action" role="status">
          <span>A new version of {APP_NAME} is ready.</span>
          <button type="button" className="btn link" onClick={applyUpdate}>Reload</button>
        </div>
      )}
    </>
  )
}

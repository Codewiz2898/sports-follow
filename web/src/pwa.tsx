import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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
    const onChange = () => { if (updating.current) window.location.reload() }
    sw.addEventListener('controllerchange', onChange)
    let timer: number | undefined
    sw.register('/sw.js').then((reg) => {
      const watch = (worker: ServiceWorker | null) => worker?.addEventListener('statechange', () => {
        if (worker.state === 'installed' && sw.controller) setWaiting(worker)
      })
      if (reg.waiting && sw.controller) setWaiting(reg.waiting)
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
    if (!waiting) return
    updating.current = true
    waiting.postMessage('skip-waiting')
  }, [waiting])

  const value = useMemo<Pwa>(() => ({ canInstall: Boolean(offer), install, updateReady: Boolean(waiting), applyUpdate, online }), [offer, install, waiting, applyUpdate, online])
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
    <button type="button" className={compact ? 'btn' : 'btn primary'} onClick={install} style={compact ? { height: 36 } : undefined}>
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
          <span>A new version of Sports Follow is ready.</span>
          <button type="button" className="btn link" onClick={applyUpdate}>Reload</button>
        </div>
      )}
    </>
  )
}

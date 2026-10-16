import { useEffect, useRef, useState } from 'react'
import { CheckIcon, ShareIcon } from './Icons'

/**
 * The link a fan shares: the page on this site, tagged as shared (s=share), so the app can count shares
 * once it counts visits. `path` may carry its own query: a game's is /player/<id>?g=<event id>.
 */
export function shareLink(path: string): string {
  const url = new URL(path, window.location.origin)
  url.searchParams.set('s', 'share')
  return url.toString()
}

/**
 * Share a page: the phone's share sheet where the browser has one (Web Share), else the link copied. The
 * server gives the link its preview in WhatsApp, X and the rest (sports_follow/share.py): the player, the
 * moment and the score, as a picture in the sport's colour. So all that's shared is the title and the link.
 */
export function ShareButton({ path, title, label = 'Share', className = 'btn icon' }: { path: string; title: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(0)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const share = async () => {
    const url = shareLink(path)
    if (navigator.share && (!navigator.canShare || navigator.canShare({ title, url }))) {
      try {
        await navigator.share({ title, url })
        return
      } catch (e) {
        if ((e as Error).name === 'AbortError') return  // the fan closed the sheet
      }
    }
    try {
      await navigator.clipboard.writeText(url)
    } catch {
      window.prompt('Copy this link', url)  // no clipboard here (a page not on HTTPS)
      return
    }
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), 2500)
  }

  return (
    <span className="share">
      <button type="button" className={className} aria-label={label} title={label} onClick={() => { share().catch(() => {}) }}>
        {copied ? <CheckIcon width={18} height={18} /> : <ShareIcon width={18} height={18} />}
      </button>
      <span className={copied ? 'share-note' : 'sr-only'} role="status">{copied ? 'Link copied' : ''}</span>
    </span>
  )
}

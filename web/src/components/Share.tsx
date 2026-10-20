import { useEffect, useRef, useState } from 'react'
import { shareLink, type Shared } from '../share'
import { CheckIcon, ShareIcon } from './Icons'

/**
 * Share a page (../share.ts says which link, and its title): the phone's share sheet where the browser has
 * one (Web Share), else the link copied. The link is on this site, which gives it its preview.
 */
export function ShareButton({ path, title, label, className = 'btn icon', text, onShared }: Shared & { className?: string; text?: string; onShared?: () => void }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(0)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const share = async () => {
    const url = shareLink(window.location.origin, path)
    onShared?.()
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
      <button type="button" className={className} aria-label={text ? undefined : label} title={label} onClick={() => { share().catch(() => {}) }}>
        {copied ? <CheckIcon width={18} height={18} /> : <ShareIcon width={18} height={18} />}
        {text}
      </button>
      <span className={copied ? 'share-note' : 'sr-only'} role="status">{copied ? 'Link copied' : ''}</span>
    </span>
  )
}

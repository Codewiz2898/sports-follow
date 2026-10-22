import { useEffect, useRef, useState } from 'react'
import { shareLink, type Shared } from '../share'
import { sendLink } from '../shareSheet'
import { CheckIcon, ShareIcon } from './Icons'

/**
 * Share a page (../share.ts says which link, and its title): the phone's share sheet where the browser has
 * one (Web Share), else the link copied (../shareSheet.ts). The link is on this site, which gives it its preview.
 * `onShared` once it went: not for a sheet the fan closed.
 */
export function ShareButton({ path, title, label, className = 'btn icon', text, onShared }: Shared & { className?: string; text?: string; onShared?: () => void }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(0)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const share = async () => {
    const url = shareLink(window.location.origin, path)
    const sent = await sendLink(navigator, title, url, (message, value) => window.prompt(message, value))
    if (sent !== 'cancelled') onShared?.()
    if (sent !== 'copied') return
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

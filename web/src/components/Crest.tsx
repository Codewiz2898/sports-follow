import type { CSSProperties } from 'react'
import type { Look } from '../look'
import { initials } from '../format'

/**
 * A team's crest (look.ts): its logo from our own origin, in a disc where it would vanish on the colour under it, or,
 * where it has none or its sport's logos are off, our badge: its short code in a white ring. Beside its name, so it says
 * nothing to a screen reader unless given a label.
 */
export function Crest({ look, name, size = 40, disc = null, label }: { look: Look | null | undefined; name: string; size?: number; disc?: string | null; label?: string }) {
  const style = { '--crest': `${size}px` } as CSSProperties
  const named = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true }
  if (look?.logo) {
    return (
      <span className={`crest${disc ? ' disc' : ''}`} style={disc ? { ...style, background: disc } : style} {...named}>
        <img src={look.logo} alt="" loading="lazy" decoding="async" />
      </span>
    )
  }
  const short = (look?.short || initials(name)).slice(0, 4)
  return <span className={`crest badge${short.length > 3 ? ' long' : ''}`} style={style} {...named}>{short}</span>
}

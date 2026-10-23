import { useState, type CSSProperties } from 'react'
import { useLooks, type Look } from '../look'
import { discFor } from '../teamColours'
import { initials } from '../format'

/**
 * A team's crest (look.ts): its logo from our own origin, in a disc where it would vanish on the colour under it, or,
 * where it has none, its sport's logos are off or it doesn't load (taken down since the page asked), our badge: its
 * short code in a white ring. Beside its name, so it says nothing to a screen reader unless given a label. A logo
 * wider than tall (a national side's flag) is marked wide, for a page that draws a flag as one (the team banner).
 */
export function Crest({ look, name, size = 40, disc = null, label, eager }: { look: Look | null | undefined; name: string; size?: number; disc?: string | null; label?: string; eager?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null)  // the address that didn't load
  const [wide, setWide] = useState(false)
  const style = { '--crest': `${size}px`, ...(disc ? { '--disc': disc } : {}) } as CSSProperties
  const named = label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true }
  if (look?.logo && failed !== look.logo) {
    return (
      <span className={`crest${disc ? ' disc' : ''}${wide ? ' wide' : ''}`} style={style} {...named}>
        <img
          src={look.logo} alt="" loading={eager ? 'eager' : 'lazy'} decoding="async" onError={() => setFailed(look.logo)}
          onLoad={(e) => setWide(e.currentTarget.naturalWidth > e.currentTarget.naturalHeight * 1.25)}
        />
      </span>
    )
  }
  const short = (look?.short || initials(name)).slice(0, 4)
  return <span className={`crest badge${short.length > 3 ? ' long' : ''}`} style={style} {...named}>{short}</span>
}

/** A game's two crests, overlapping, at a card's corner: each logo in a white disc (it sits on the sport's colour, or a
 * white card), or our badge. Nothing while they're asked for, or for a game with a side the registry hasn't. */
export function CrestPair({ keys, size = 30 }: { keys?: (string | null | undefined)[] | null; size?: number }) {
  const looks = useLooks(keys ?? [])
  const found = (keys ?? []).map((k) => (k ? looks[k] : null)).filter((l): l is Look => !!l)
  if (found.length !== 2) return null
  return (
    <span className="crest-pair" aria-hidden="true">
      {found.map((l, n) => <Crest key={n} look={l} name="" size={size} disc={onColour(l)} />)}
    </span>
  )
}

/** The disc a logo sits in on a colour (a card's, a pill's): white, or ink for a light logo. */
export function onColour(look: Look | null | undefined): string {
  return discFor(look, '#ffffff') ?? '#ffffff'
}

/** The disc a logo needs on white (a list, a board, a white card): none, or ink for a light logo. */
export function onWhite(look: Look | null | undefined): string | null {
  return discFor(look, '#ffffff')
}

/** One team's crest by its key (a board's side), on white: nothing while it's asked for, or for a team the registry
 * hasn't. A logo sits in a disc only where it would vanish. */
export function TeamCrest({ teamKey, name, size }: { teamKey: string | null | undefined; name: string; size?: number }) {
  const look = useLooks([teamKey])[teamKey ?? '']
  return look ? <Crest look={look} name={name} size={size} disc={onWhite(look)} /> : null
}

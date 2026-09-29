export function parseDate(value?: string | null): Date | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

/** "in 3d", "in 4h 12m", "12m ago". */
export function relative(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d) return ''
  const diff = d.getTime() - now
  const abs = Math.abs(diff)
  const days = Math.floor(abs / 864e5)
  const hours = Math.floor((abs % 864e5) / 36e5)
  const mins = Math.floor((abs % 36e5) / 6e4)
  const text = days ? `${days}d` : hours ? `${hours}h ${mins}m` : `${Math.max(mins, 1)}m`
  return diff >= 0 ? `in ${text}` : `${text} ago`
}

export function dateTime(value?: string | null): string {
  const d = parseDate(value)
  if (!d) return value ?? ''
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function day(value?: string | null): string {
  const d = parseDate(value)
  if (!d) return value ?? ''
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' })
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

export function hostname(url?: string | null): string {
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function safeHref(url?: string | null): string | undefined {
  return url && /^https?:\/\//i.test(url) ? url : undefined
}

/** Models report the sport in whatever case they like ("cricket", "CRICKET"); show it one way. */
export function sportName(sport?: string | null): string {
  if (!sport) return ''
  return sport.replace(/\S+/g, (w) => (w === w.toUpperCase() && w.length <= 4 ? w : w[0].toUpperCase() + w.slice(1).toLowerCase()))
}

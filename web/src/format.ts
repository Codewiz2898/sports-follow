export function parseDate(value?: string | null): Date | null {
  if (!value) return null
  // A bare date ("2026-10-12", a result's day) is a calendar day here, not midnight UTC, which
  // west of UTC would be the evening before.
  const ymd = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  const d = ymd ? new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3])) : new Date(value)
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

/** "2d 11h", "4h 12m", "12m" until a start; empty once it has passed. */
export function countdown(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d) return ''
  const diff = d.getTime() - now
  if (diff <= 0) return ''
  const days = Math.floor(diff / 864e5)
  const hours = Math.floor((diff % 864e5) / 36e5)
  const mins = Math.floor((diff % 36e5) / 6e4)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${mins}m` : `${Math.max(mins, 1)}m`
}

/** Whole calendar days from a date to today: 0 today, 1 yesterday. Null when there's no date. */
export function daysAgo(value?: string | null, now = new Date()): number | null {
  const d = parseDate(value)
  if (!d) return null
  return Math.round((new Date(now.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 864e5)
}

/** "Today", "Yesterday", or the date. */
export function dayLabel(value?: string | null, now = new Date()): string {
  const days = daysAgo(value, now)
  return days === 0 ? 'Today' : days === 1 ? 'Yesterday' : day(value)
}

/** "Thu 08:00": a start within the week; further out, the date. */
export function shortWhen(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d) return 'TBC'
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (d.getTime() - now < 6 * 864e5) return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export type SportKey = 'football' | 'cricket' | 'basketball' | 'tennis' | 'chess' | 'esports' | 'other'

const ESPORTS = /e-?sports?\b|e-?football|valorant|counter-strike|\bcs2\b|league of legends|dota|mobile legends|overwatch|rainbow six|rocket league|call of duty|bgmi|pubg|free fire/

/** Which colour a sport wears (styles.css, [data-sport]). Other codes of football, table tennis and padel wear none. */
export function sportKey(sport?: string | null): SportKey {
  const s = (sport ?? '').toLowerCase()
  if (ESPORTS.test(s)) return 'esports'  // the agent may name the game ("Valorant") rather than the sport
  if (/american|australian|gaelic|canadian|rugby|rules/.test(s)) return 'other'
  if (s.includes('soccer') || s.includes('football')) return 'football'
  if (/table tennis|padel|beach tennis/.test(s)) return 'other'
  for (const k of ['cricket', 'basketball', 'tennis', 'chess'] as const) if (s.includes(k)) return k
  return 'other'
}

const SUFFIXES = /^(jr|sr|ii|iii|iv|júnior|junior|filho|neto)\.?$/i
const PARTICLES = new Set(['de', 'van', 'von', 'der', 'den', 'di', 'da', 'das', 'do', 'dos', 'del', 'della', 'le', 'la', 'du', 'ten', 'ter', 'bin', 'al', 'el'])

/**
 * The name a scoreboard calls a player: the family name with its particles ("De Bruyne", "van Dijk"),
 * without "Jr." or "III"; the given name when the last word is an initial ("Gukesh D").
 */
export function shortName(name: string): string {
  const words = name.trim().split(/\s+/)
  while (words.length > 1 && SUFFIXES.test(words.at(-1)!)) words.pop()
  if (words.length < 2) return words[0] ?? name
  if (/^\p{Lu}\.?$/u.test(words.at(-1)!)) return words[0]
  let start = words.length - 1
  while (start > 1 && PARTICLES.has(words[start - 1].toLowerCase())) start--
  return words.slice(start).join(' ')
}

/**
 * Win, draw or loss from the player's side. Structured results say "Won…"/"Lost…"; cricket names
 * the winning side ("India won by 8 wkts"), so the player's teams decide, the same way the server
 * counts form (form._cricket_outcome).
 */
export function outcome(result: string, teams: string[] = []): 'W' | 'D' | 'L' | null {
  const r = result.trim().toLowerCase()
  if (/^won\b/.test(r)) return 'W'
  if (/^lost\b/.test(r)) return 'L'
  if (/^(drew|draw|drawn|tied)\b/.test(r)) return 'D'
  if (!r.includes(' won ')) return /drawn|tied|no result/.test(r) ? 'D' : null
  const winner = r.split(' won ', 1)[0]
  if (teams.some((t) => t && winner.includes(t.toLowerCase()))) return 'W'
  return teams.length ? 'L' : null
}

/** "22 PTS · 11 REB · 10 AST" as numbers and labels, when the whole line reads that way. */
export function lineStats(text?: string | null): { value: string; label: string }[] | null {
  if (!text) return null
  const parts = text.split(' · ').map((p) => p.trim().match(/^([\d.,½*/–-]+)\s+([A-Za-z%]+(?:\s[A-Za-z%]+)?)$/))
  if (parts.length < 2 || parts.some((m) => !m)) return null
  return parts.map((m) => ({ value: m![1], label: m![2] }))
}

/** Models report the sport in whatever case they like ("cricket", "CRICKET"); show it one way. */
export function sportName(sport?: string | null): string {
  if (!sport) return ''
  return sport.replace(/\S+/g, (w) => (w === w.toUpperCase() && w.length <= 4 ? w : w[0].toUpperCase() + w.slice(1).toLowerCase()))
}

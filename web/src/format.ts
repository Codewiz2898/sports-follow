const DAY = /^(\d{4})-(\d{2})-(\d{2})$/
const ZONELESS = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

export function parseDate(value?: string | null): Date | null {
  if (!value) return null
  // A bare date ("2026-10-12", an agent's result day, a fixture with no time yet) is a calendar day
  // here, not midnight UTC, which west of UTC would be the evening before.
  const ymd = value.match(DAY)
  // A time without a zone is UTC, as the server reads it (pipeline._parse_time), never the phone's time.
  const d = ymd ? new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3])) : new Date(ZONELESS.test(value) ? `${value.replace(' ', 'T')}Z` : value)
  return Number.isNaN(d.getTime()) ? null : d
}

/** A day with no time of day: a fixture whose start isn't known yet ("time TBC"). */
export function dateOnly(value?: string | null): boolean {
  return Boolean(value && DAY.test(value))
}

/** Whether a start is still to come. A day without a time counts until that day is over. */
export function ahead(value?: string | null, now = Date.now()): boolean {
  const d = parseDate(value)
  if (!d) return false
  return (dateOnly(value) ? new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1) : d).getTime() > now
}

/** When a result was played: its start, so it's dated by the fan's own day, else the agent's bare date. */
export function playedOn(r?: { date?: string | null; start_utc?: string | null } | null): string | null {
  return r?.start_utc || r?.date || null
}

/** "in 3d", "in 4h 12m", "12m ago"; for a day without a time, whole days ("today", "tomorrow", "in 3d"). */
export function relative(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d) return ''
  if (dateOnly(value)) {
    const n = -daysAgo(value, new Date(now))!
    return n === 0 ? 'today' : n === 1 ? 'tomorrow' : n === -1 ? 'yesterday' : n > 0 ? `in ${n}d` : `${-n}d ago`
  }
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
  if (dateOnly(value)) return `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })} · time TBC`
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/**
 * A start's time of day on the fan's clock, in pieces: "17:00" is one, "12:30 AM" is two ("12:30", then the
 * period). A narrow column draws the period small beside the time, so the time never breaks inside itself.
 * The pieces keep the locale's order (some write the period first) and carry no spaces of their own.
 */
export function clockPieces(d: Date): { text: string; period: boolean }[] {
  const pieces: { text: string; period: boolean }[] = []
  for (const p of new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).formatToParts(d)) {
    const period = p.type === 'dayPeriod'
    const last = pieces[pieces.length - 1]
    if (last && !last.period && !period) last.text += p.value
    else pieces.push({ text: p.value, period })
  }
  return pieces.map((p) => ({ ...p, text: p.text.trim() })).filter((p) => p.text)
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

/** "2d 11h", "4h 12m", "12m" until a start; empty once it has passed, or when its time isn't known. */
export function countdown(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d || dateOnly(value)) return ''
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

/** "Thu 08:00": a start within the week ("Thu" when its time isn't known); further out, the date. */
export function shortWhen(value?: string | null, now = Date.now()): string {
  const d = parseDate(value)
  if (!d) return 'TBC'
  const time = dateOnly(value) ? '' : ` ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
  if (d.getTime() - now < 6 * 864e5) return `${d.toLocaleDateString(undefined, { weekday: 'short' })}${time}`
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

const SUFFIXES = /^(jr|sr|ii|iii|iv|júnior|junior|filho|neto)\.?$/i
const PARTICLES = new Set(['de', 'van', 'von', 'der', 'den', 'di', 'da', 'das', 'do', 'dos', 'del', 'della', 'le', 'la', 'du', 'ten', 'ter', 'bin', 'al', 'el'])

// Chinese, Korean and Taiwanese names are written family name first ("Ding Liren", "Son Heung-min",
// "Hsieh Su-wei"). A name is read that way only when it is two words, the first a common family name
// and the second a given name of that kind: one or two pinyin syllables, or two syllables joined by a
// hyphen. Japanese names, written given name first, rarely pass: their family names are longer.
const PINYIN_FAMILY = new Set('li wang zhang liu chen yang zhao huang zhou wu xu sun hu zhu gao lin he guo ma luo liang song zheng xie han tang feng yu dong xiao cheng cao yuan deng fu shen zeng peng lu su jiang cai jia ding wei xue ye yan pan du dai xia zhong tian ren fan fang shi yao tan liao zou xiong jin hao kong bai cui kang mao qiu qin gu hou shao meng long wan duan lei qian yin yi bu ju qi qu ni an ai ou mei rong ruan shang niu wen geng mo lai zhan hong guan chang ji bao zhai ouyang lou miao'.split(' '))
const HYPHEN_FAMILY = new Set('kim lee yi park pak choi choe jung jeong chung kang cho jo yoon yun jang chang lim im han oh seo suh seol shin kwon hwang ahn an song yoo yu ryu ryoo hong hyun jeon jun jon ko go moon mun yang ryang son bae baek paik heo huh nam shim noh roh ha kwak sung suk cha joo woo koo ku min na jin ji ki ri jong chong um eom yeom byun chae won bang gong pyo ma hsieh chan chen lin lu chuang huang wang wu tsai cheng hsu kuo chou chiang liu lai tseng su yeh ho tsang wong leung cheung lam ng chow tse yip kwok fung chiu mak tang lau kwan fong lo tam hui chu sim sin yeo ro'.split(' '))
const SYLLABLE = [
  '[bpm](?:a|o|ai|ei|ao|ou|an|en|ang|eng|i|ie|iao|iu|ian|in|ing|u)',
  'f(?:a|o|ei|ou|an|en|ang|eng|u)',
  '[dt](?:a|e|ai|ei|ao|ou|an|en|ang|eng|ong|i|ie|iao|iu|ian|ing|u|uo|ui|uan|un)',
  '[nl](?:a|e|ai|ei|ao|ou|an|en|ang|eng|ong|i|ia|ie|iao|iu|ian|in|iang|ing|u|uo|uan|un|v|ve|ü|üe)',
  '[gkh](?:a|e|ai|ei|ao|ou|an|en|ang|eng|ong|u|ua|uo|uai|ui|uan|un|uang)',
  '[jqx](?:i|ia|ie|iao|iu|ian|in|iang|ing|iong|u|ue|uan|un)',
  '(?:zh|ch|sh|[rzcs])(?:a|e|i|ai|ei|ao|ou|an|en|ang|eng|ong|u|ua|uo|uai|ui|uan|un|uang)',
  'y(?:a|o|e|i|ao|ou|an|in|ang|ing|ong|u|ue|uan|un)',
  'w(?:a|o|ai|ei|an|en|ang|eng|u)',
].join('|')
// A syllable with no initial ("an", "ao") starts the name; inside one, pinyin would write an apostrophe.
const PINYIN = new RegExp(`^(?:a|o|e|ai|ei|ao|ou|an|en|ang|eng|er|${SYLLABLE})(?:${SYLLABLE})?$`)
// Two romanized syllables, each one vowel sound ("Heung-min", "Chun-Hsin"): not a double-barrelled
// family name ("Alexander-Arnold", "Jerray-Silver").
const HYPHENED = /^[^aeiouy-]{0,3}[aeiouy]{1,4}(?:ng|[nmlkpthrg])?-[^aeiouy-]{0,3}[aeiouy]{1,4}(?:ng|[nmlkpthrg])?$/

function familyFirst(words: string[]): boolean {
  if (words.length !== 2) return false
  const [family, given] = [words[0].toLowerCase(), words[1].toLowerCase()]
  return PINYIN_FAMILY.has(family) ? PINYIN.test(given) || HYPHENED.test(given) : HYPHEN_FAMILY.has(family) && HYPHENED.test(given)
}

/**
 * The name a scoreboard calls a player: the family name with its particles ("De Bruyne", "van Dijk"),
 * without "Jr." or "III"; the given name when the last word is an initial ("Gukesh D"); the first word
 * when the family name comes first ("Ding", "Son").
 */
export function shortName(name: string): string {
  const words = name.trim().split(/\s+/)
  while (words.length > 1 && SUFFIXES.test(words.at(-1)!)) words.pop()
  if (words.length < 2) return words[0] ?? name
  if (/^\p{Lu}\.?$/u.test(words.at(-1)!)) return words[0]
  if (familyFirst(words)) return words[0]
  let start = words.length - 1
  while (start > 1 && PARTICLES.has(words[start - 1].toLowerCase())) start--
  return words.slice(start).join(' ')
}

// Letters Unicode doesn't decompose into a base letter plus an accent (espn._LETTERS on the server).
const LETTERS: Record<string, string> = { ø: 'o', Ø: 'O', æ: 'ae', Æ: 'Ae', œ: 'oe', Œ: 'Oe', ß: 'ss', đ: 'd', Đ: 'D', ł: 'l', Ł: 'L', þ: 'th', Þ: 'Th', ð: 'd', ı: 'i' }

// Each capital's width in the display face, in ems: Anton's own advance widths (Anton_400Regular.ttf). The
// Android app's slanted copy sets the letters of a word the same, with up to 0.13 em more at its ends for
// the lean (mobile/scripts/slant_font.py): LEAN holds that. SPACING is .dsp's letter spacing. A letter the
// table doesn't have counts as the widest.
const ANTON: Record<string, number> = {
  A: 0.485, B: 0.479, C: 0.474, D: 0.493, E: 0.412, F: 0.399, G: 0.485, H: 0.499, I: 0.227, J: 0.466, K: 0.472, L: 0.397, M: 0.746,
  N: 0.498, O: 0.486, P: 0.472, Q: 0.494, R: 0.477, S: 0.461, T: 0.396, U: 0.474, V: 0.469, W: 0.712, X: 0.484, Y: 0.446, Z: 0.41,
  0: 0.494, 1: 0.331, 2: 0.494, 3: 0.494, 4: 0.494, 5: 0.494, 6: 0.494, 7: 0.494, 8: 0.494, 9: 0.494, '-': 0.311, "'": 0.214, '.': 0.229,
}
const WIDEST = 0.746
const LEAN = 0.15
const SPACING = 0.005

/**
 * How wide a name's longest word is in the display face, in ems. A player's band sets their name at the size
 * that fits it on a line (PlayerBand in both apps: their room divided by this, up to the band's own size),
 * so the name breaks between words, never inside one. A hyphen ends a word, as a line may break after it.
 */
export function longestWordEms(name: string): number {
  const plain = name.replace(/[øØæÆœŒßđĐłŁþÞðı]/g, (c) => LETTERS[c]).normalize('NFKD').replace(/\p{M}/gu, '').toUpperCase()
  const ems = (word: string) => [...word].reduce((sum, c) => sum + (ANTON[c] ?? WIDEST) + SPACING, LEAN)
  return Math.max(1, ...plain.split(/(?<=-)|\s+/).filter(Boolean).map(ems))
}

/** A team name reduced for exact comparison, as the server's espn.team_key: "Australia 'A'" is "australia a". */
function teamKey(name: string): string {
  return name
    .replace(/[øØæÆœŒßđĐłŁþÞðı]/g, (c) => LETTERS[c])
    .normalize('NFKD')
    .replace(/[\u0080-\uffff]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

/**
 * Win, draw or loss from the player's side. Structured results say "Won…"/"Lost…"; cricket names
 * the winning side ("India won by 8 wkts", "Match tied (India won the Super Over)"), so the player's
 * teams decide by whole name ("India A" and "Mumbai Indians" aren't India), the same way the server
 * counts form (sports/cricket.py: winner_outcome).
 */
export function outcome(result: string, teams: string[] = []): 'W' | 'D' | 'L' | null {
  const r = result.trim()
  if (/^won\b/i.test(r)) return 'W'
  if (/^lost\b/i.test(r)) return 'L'
  if (/^(drew|draw|drawn|tied)\b/i.test(r)) return 'D'
  const winner = r.match(/(?:^|\()([^()]+?) won\b/i)
  if (!winner) return /drawn|tied|no result/i.test(r) ? 'D' : null
  const named = teamKey(winner[1])
  if (named && teams.some((t) => teamKey(t) === named)) return 'W'
  return teams.length ? 'L' : null
}

/** "22 PTS · 11 REB · 10 AST" as numbers and labels, when the whole line reads that way. */
export function lineStats(text?: string | null): { value: string; label: string }[] | null {
  if (!text) return null
  const parts = text.split(' · ').map((p) => p.trim().match(/^([\d.,½*/–-]+)\s+([A-Za-z%]+(?:\s[A-Za-z%]+)?)$/))
  if (parts.length < 2 || parts.some((m) => !m)) return null
  return parts.map((m) => ({ value: m![1], label: m![2] }))
}

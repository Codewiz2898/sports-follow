// How search shows its answer: the sections results come in, the typed start of each name in bold, the line
// under a result, and what the research agent's allowance says. Shared by the web app (components/Search.tsx)
// and the Android app (mobile/), so it touches nothing of a browser's.

import { relative } from './format'
import type { AthletePreview, Research, SearchResponse, SearchResult, SportInfo, StarterSport } from './types'

export interface Section { key: string; title: string; items: SearchResult[] }

/** The order results are shown (and arrowed through) in; `app`, the app's name ("On stann"). */
export function sections(data: SearchResponse, app: string): Section[] {
  const { results } = data
  if (data.namesakes > 1) {
    return [
      { key: 'namesakes', title: '', items: results.filter((r) => r.exact) },
      { key: 'similar', title: 'Similar names', items: results.filter((r) => !r.exact) },
    ].filter((s) => s.items.length)
  }
  return [
    { key: 'ours', title: `On ${app}`, items: results.filter((r) => r.player_id) },
    { key: 'athletes', title: 'Athletes', items: results.filter((r) => !r.player_id && r.live_scores) },
    { key: 'other', title: 'Other sports · AI research only', items: results.filter((r) => !r.player_id && !r.live_scores) },
    { key: 'suggestions', title: 'Did you mean', items: data.suggestions },
  ].filter((s) => s.items.length)
}

// Accents and apostrophes dropped, as the server compares names: "aja" is the start of A'ja.
export const fold = (s: string) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/['’`]/g, '').toLowerCase()

/** The name in parts, the typed start of each word bold: "Caitl" in Caitlin Clark. */
export function highlightParts(name: string, query: string): { text: string; bold: boolean }[] {
  const typed = fold(query).split(/[\s.'-]+/).filter(Boolean)
  return name.split(/(\s+)/).flatMap((part) => {
    const word = fold(part)
    const hit = typed.filter((t) => word.startsWith(t)).sort((a, b) => b.length - a.length)[0]
    if (!hit || !part.trim()) return [{ text: part, bold: false }]
    let n = 0
    while (n < part.length && fold(part.slice(0, n)).length < hit.length) n++
    return [{ text: part.slice(0, n), bold: true }, { text: part.slice(n), bold: false }]
  })
}

/** The line under a result: its page being set up, its game on now, or its next game. */
export function nextLine(r: SearchResult): { text: string; live: boolean } | null {
  if (r.status === 'building') return { text: 'Page being set up…', live: false }
  if (r.next?.live) return { text: `Live · ${r.next.live}`, live: true }
  if (r.next?.title) return { text: `Next: ${r.next.title}${r.next.start_utc ? ` · ${relative(r.next.start_utc)}` : ''}`, live: false }
  return null
}

/** The name the namesakes go by ("Ronaldo", not "Ronaldo da Silva Souza", whose alias it is); when only
 * aliases have it ("Silva"), the name as typed. */
export function exactName(data: SearchResponse): string {
  const plain = (s: string) => fold(s).split(/[^a-z0-9]+/).filter(Boolean).sort().join(' ')
  return data.results.find((r) => r.exact && plain(r.name) === plain(data.query))?.name ?? data.query.replace(/(^|\s)\S/g, (c) => c.toUpperCase())
}

/** What's left of the fan's AI researches today, or when more come; and whether they're out. */
export function allowance(research?: Research): { text: string | undefined; out: boolean } {
  if (!research) return { text: undefined, out: false }
  const out = research.left <= 0
  const reset = new Date(research.resets_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return { out, text: out ? `You've used today's ${research.limit} AI researches. More from ${reset}.` : `${research.left} of ${research.limit} left today` }
}

/** Which sports have live scores here (each from its sources; see Credits), and what the rest get. A group's sports
 * are named once, by the group ("esports" for the games). */
export function liveNote(running: SportInfo[]): string {
  const live = [...new Set(running.filter((s) => s.live).map((s) => s.group || s.label.toLowerCase()))]
  if (!live.length) return 'Every sport gets news and results from the agent.'
  const listed = live.length > 1 ? `${live.slice(0, -1).join(', ')} and ${live.at(-1)}` : live[0]
  return `Live scores come from sources for ${listed} (see Credits). Other sports get news and results from the agent.`
}

// A letter of a script other than Latin (Devanagari, Tamil, Chinese, Cyrillic…), which search can't read yet.
export const OTHER_SCRIPT = /[^\P{L}\p{Script=Latin}]/u

/** Up to n starters' names, a sport at a time in turn: search's "Try" names. */
export function starterNames(sports: StarterSport[] | null, n: number): string[] {
  const out: string[] = []
  for (let i = 0; out.length < n && (sports ?? []).some((s) => i < s.players.length); i++) {
    for (const s of sports ?? []) if (i < s.players.length && out.length < n) out.push(s.players[i].name)
  }
  return out
}

/** An athlete's preview, who they are after the sport (the band names the sport on its own): their teams, the
 * league when it's one fans know by its letters (the NBA, the WTA), their country, and when they were born
 * (`born`: the preview's, else the search result's year). Before the preview loads, the result's detail. */
export function previewAbout(preview: AthletePreview | null, hint: SearchResult | null): string {
  const born = preview?.born ? `born ${preview.born.slice(0, 4)}` : hint?.born ? `born ${hint.born}` : null
  if (!preview) return hint?.detail ?? ''
  const league = preview.league && ['nba', 'wnba', 'atp', 'wta'].includes(preview.league) ? preview.league.toUpperCase() : null
  return [...preview.teams, league, preview.country, born].filter(Boolean).join(' · ')
}

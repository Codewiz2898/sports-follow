// A player's results history and stats, as the web's components/Results.tsx: every finished game newest first, a
// page at a time, each opening to the final score, the player's whole line, the game's match page and the
// source; and the Stats tab, the source's numbers or form over the last 10 or 5 results.

import { useEffect, useState } from 'react'
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { day, hostname, playedOn, resultOutcome, safeHref } from '../../web/src/format'
import { api, type Card, type Form, type GameDetail, type RecentResult, type ResultsPage } from './api'
import { Empty, openMatch, SourceNote, StatGrid } from './blocks'
import { useSports } from './catalog'
import { ChevronIcon } from './icons'
import { colour, font, num, t } from './theme'
import { Btn, Card as Surface, Spinner, WL } from './ui'

/** A choice of views side by side (.segmented); `full`, four equal columns stretched across. */
export function Segmented<T extends string | number>({ options, value, onChange, full }: { options: [T, string][]; value: T; onChange: (v: T) => void; full?: boolean }) {
  return (
    <View style={[s.segmented, full && { alignSelf: 'stretch' }]} accessibilityRole="radiogroup">
      {options.map(([v, label]) => (
        <Pressable key={String(v)} accessibilityRole="radio" accessibilityState={{ checked: value === v }} onPress={() => onChange(v)} style={[s.segment, full && { flex: 1, paddingHorizontal: 4 }, value === v && s.segmentOn]}>
          <Text style={[s.segmentText, value === v && { color: colour.bg }]} numberOfLines={1}>{label}</Text>
        </Pressable>
      ))}
    </View>
  )
}

/** teamOnly: a sport whose source has no player numbers (esports), so a result is the team's. match: the
 * game's match page (what happened in it: the timeline, the scorecard, the maps), where a source has the game. */
function GameView({ detail, error, teamOnly, match }: { detail: GameDetail | null; error: string | null; teamOnly?: boolean; match?: string | null }) {
  if (error) return <View style={s.body}><Text style={[t.small, t.muted]}>{error}</Text></View>
  if (!detail) return <View style={s.body}><Spinner /></View>
  const page = match ?? detail.match
  const lineNote = teamOnly ? "The team's result: the source has no player numbers."
    : detail.scorecard === 'missing' ? 'The source has no scorecard for this game.'
      : detail.scorecard ? 'No line for the player in this game.' : "The player's line is still being read."
  const url = safeHref(detail.source_url)
  return (
    <View style={s.body}>
      {detail.score || detail.clock ? (
        <View style={{ gap: 4 }}>
          {detail.score ? <Text style={t.title}>{detail.score}</Text> : null}
          <Text style={[t.small, t.muted]}>{[detail.clock, detail.venue].filter(Boolean).join(' · ')}</Text>
        </View>
      ) : null}
      {detail.line ? (
        <>
          <Text style={[t.small, { fontFamily: font[800] }]}>{detail.line.headline}</Text>
          {detail.line.stats.length > 0 ? <StatGrid stats={detail.line.stats} paper /> : null}
        </>
      ) : <Text style={[t.small, t.muted]}>{lineNote}</Text>}
      {page || url ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 16 }}>
          {page ? <Text onPress={openMatch(page)} accessibilityRole="link" style={[t.small, t.link]}>The match</Text> : null}
          {url ? <Text onPress={() => Linking.openURL(url)} style={[t.small, t.link]}>Open on {hostname(detail.source_url)}</Text> : null}
        </View>
      ) : null}
    </View>
  )
}

/** One finished game; tapping it opens the final score, the player's whole line, the game's match page and the source. */
function GameRow({ r, playerId, teams, teamOnly, last }: { r: RecentResult; playerId: number; teams?: string[]; teamOnly?: boolean; last: boolean }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<GameDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const canOpen = r.event_id != null
  const wl = resultOutcome(r, teams)
  // The badge says won or lost, so the score stands alone ("Won 133–128" → "133–128").
  const score = wl ? r.result.replace(/^(Won|Lost|Drew)\s+/, '') : r.result
  const long = score.length > 14
  const toggle = () => {
    setOpen((o) => !o)
    if (!detail && r.event_id != null) api.result(playerId, r.event_id).then(setDetail).catch((e: Error) => setError(e.message))
  }
  return (
    <View style={!last && s.line}>
      <Pressable onPress={toggle} disabled={!canOpen} accessibilityState={canOpen ? { expanded: open } : undefined} style={s.head}>
        <View style={wl ? undefined : { opacity: 0 }}><WL mark={wl ?? 'D'} /></View>
        <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
          <Text style={t.title}>{r.title}</Text>
          <Text style={[t.small, t.muted]}>{[day(playedOn(r)), r.competition].filter(Boolean).join(' · ')}</Text>
          {r.player_contribution ? <Text style={[t.small, { fontFamily: font[800] }]}>{r.player_contribution}</Text> : null}
        </View>
        <View style={[{ flexDirection: 'row', alignItems: 'center', gap: 6 }, long && { flexShrink: 1, maxWidth: '45%' }]}>
          <Text style={long ? [t.small, { fontFamily: font[800], textAlign: 'right' }] : num(20)}>{score}</Text>
          {canOpen ? <View style={{ transform: [{ rotate: open ? '90deg' : '0deg' }] }}><ChevronIcon size={16} color={colour.muted} /></View> : null}
        </View>
      </Pressable>
      {open ? <GameView detail={detail} error={error} teamOnly={teamOnly} match={r.match} /> : null}
    </View>
  )
}

/** The player's whole results history, newest first, a page at a time. */
export function ResultsHistory({ card }: { card: Card }) {
  const sports = useSports()
  const [items, setItems] = useState<RecentResult[]>(card.recent_results ?? [])
  const [page, setPage] = useState<ResultsPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.results(card.player_id).then((p) => {
      if (cancelled) return
      if (p.results.length) setItems(p.results) // an agent-built card has no stored history: keep its list
      setPage(p)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [card.player_id])

  const more = () => {
    if (!page?.next) return
    setLoading(true)
    setError(null)
    api.results(card.player_id, page.next)
      .then((p) => { setItems((xs) => [...xs, ...p.results]); setPage(p) })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  if (!items.length) return <Empty>{page?.filling ? 'Reading results from the source…' : 'No recent results found.'}</Empty>
  const teamOnly = sports.info(card.player.sport).team_results
  return (
    <>
      <Surface style={{ paddingVertical: 4, paddingHorizontal: 16 }}>
        {items.map((r, i) => <GameRow key={r.event_id ?? `${r.title}-${r.date ?? ''}`} r={r} playerId={card.player_id} teams={card.player.teams} teamOnly={teamOnly} last={i === items.length - 1} />)}
      </Surface>
      {page?.next ? <Btn label="Show more" busy={loading} onPress={more} style={{ alignSelf: 'flex-start' }} /> : null}
      {error ? <Text style={t.small}>{error}</Text> : null}
      <Text style={[t.tiny, t.muted]}>
        {page && page.total > 0 ? `${Math.min(items.length, page.total)} of ${page.total} results` : ''}
        {page?.filling ? ' · older results are still being filled in' : ''}
      </Text>
      <SourceNote provenance={card.provenance?.recent_results} />
    </>
  )
}

type View_ = 'overview' | 10 | 5

/** The Stats tab: the source's numbers, or form over the last 10 or 5 results. */
export function StatsWithForm({ card }: { card: Card }) {
  const sports = useSports()
  const [view, setView] = useState<View_>('overview')
  const [form, setForm] = useState<Partial<Record<10 | 5, Form>>>({})
  useEffect(() => {
    if (view === 'overview' || form[view]) return
    api.form(card.player_id, view)
      .then((f) => setForm((x) => ({ ...x, [view]: f })))
      .catch(() => setForm((x) => ({ ...x, [view]: { stats: [], note: '', count: 0 } })))
  }, [view, card.player_id, form])
  const shown = view === 'overview' ? null : form[view]
  return (
    <>
      <Segmented<string> options={[['overview', sports.info(card.player.sport).stats_title], ['10', 'Last 10'], ['5', 'Last 5']]} value={String(view)} onChange={(v) => setView(v === 'overview' ? 'overview' : (Number(v) as 10 | 5))} />
      {view === 'overview' ? (
        <>
          <StatGrid stats={card.season_stats} />
          <SourceNote provenance={card.provenance?.season_stats} note={card.season_stats_note} />
        </>
      ) : shown ? (
        <>
          <StatGrid stats={shown.stats} empty="Not enough results yet." />
          {shown.note ? <Text style={[t.tiny, t.muted]}>{shown.note}, from the results history</Text> : null}
        </>
      ) : <Spinner />}
    </>
  )
}

const s = StyleSheet.create({
  segmented: { flexDirection: 'row', alignSelf: 'flex-start', gap: 2, padding: 3, borderRadius: 12, backgroundColor: colour.raised },
  segment: { height: 34, paddingHorizontal: 14, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  segmentOn: { backgroundColor: colour.text },
  segmentText: { fontFamily: font[700], fontSize: 13, color: colour.muted },
  line: { borderBottomWidth: 1, borderBottomColor: colour.line },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, minHeight: 44 },
  body: { gap: 10, paddingBottom: 16, paddingLeft: 42 },
})

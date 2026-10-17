// Search as the web's components/Search.tsx has it: as you type, one request per 250 ms pause; the answer in
// sections (shared web/src/search.ts), each result opening the player or a preview of the athlete, and Follow
// or Research beside it; teams by that name, each opening its page; the research agent offered last.

import Constants from 'expo-constants'
import { router } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ScrollView, StyleSheet, Text, View, Pressable } from 'react-native'
import { followTarget } from '../../web/src/follow'
import { allowance, exactName, fold, highlightParts, liveNote, nextLine, OTHER_SCRIPT, sections, teamNext } from '../../web/src/search'
import { api, type Research, type SearchResponse, type SearchResult, type TeamSummary } from './api'
import { Avatar } from './blocks'
import { toneOf, useSports } from './catalog'
import { useFollowing } from './following'
import { CheckIcon, PlusIcon, SparkIcon } from './icons'
import { colour, dsp, font, GUTTER, t } from './theme'
import { Btn, Card, Chip, Eyebrow, Spinner } from './ui'

const APP = Constants.expoConfig?.name ?? ''

/** Search as you type; keeps the last answer on screen while the next loads (`fresh`: it answers what's typed now). */
export function useSearch(query: string, sport: string) {
  const [answer, setAnswer] = useState<{ asked: string; data: SearchResponse } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim()
  const asked = `${sport}|${q}`

  useEffect(() => {
    if (q.length < 2) {
      setAnswer(null)
      setLoading(false)
      setError(null)
      return
    }
    const ctrl = new AbortController()
    setLoading(true)
    const timer = setTimeout(() => {
      api.search(q, sport || null, ctrl.signal)
        .then((r) => { setAnswer({ asked: `${sport}|${q}`, data: r }); setError(null) })
        .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    }, 250)
    return () => { clearTimeout(timer); ctrl.abort() }
  }, [q, sport])

  const data = q.length >= 2 ? answer?.data ?? null : null
  return { data, fresh: data != null && answer?.asked === asked, loading, error }
}

/** Opening and following results, the same from search and an athlete's preview. */
export function useSearchActions() {
  const following = useFollowing()
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)

  const open = useCallback((r: SearchResult) => {
    if (r.player_id) router.push(`/player/${r.player_id}`)
    else if (r.system && r.athlete_id) router.push({ pathname: '/athlete/[system]/[id]', params: { system: r.system, id: r.athlete_id, ...(r.league ? { league: r.league } : {}), result: JSON.stringify(r) } })
    else if (r.qid) router.push({ pathname: '/athlete/[system]/[id]', params: { system: 'wikidata', id: r.qid, sport: r.sport.toLowerCase(), result: JSON.stringify(r) } })
  }, [])

  const run = useCallback(async (key: string, go: () => ReturnType<typeof following.follow>) => {
    // One follow or research at a time: a second press must not send another.
    if (inFlight.current) return
    inFlight.current = true
    setError(null)
    setPending(key)
    try {
      const card = await go()
      router.push(`/player/${card.player_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      inFlight.current = false
      setPending(null)
    }
  }, [])

  const follow = useCallback((r: SearchResult) => run(r.key, () => following.follow(followTarget(r))), [following, run])
  const research = useCallback((query: string) => run(`research:${query}`, () => following.follow({ query })), [following, run])

  // A team (sports_follow/teams.py): its page, and following it there, as a player's.
  const openTeam = useCallback((x: TeamSummary) => router.push(`/team/${x.team_id}`), [])
  const followTeam = useCallback(async (x: TeamSummary) => {
    if (inFlight.current) return
    inFlight.current = true
    setError(null)
    setPending(`team:${x.team_id}`)
    try {
      await following.followTeam(x.team_id, true)
      router.push(`/team/${x.team_id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      inFlight.current = false
      setPending(null)
    }
  }, [following])
  return { open, follow, research, pending, error, isFollowing: following.isFollowing, openTeam, followTeam, isFollowingTeam: following.isFollowingTeam }
}

type Actions = ReturnType<typeof useSearchActions>

/** The name with the typed start of each word in bold (and underlined, .hl). */
function Highlight({ name, query }: { name: string; query: string }) {
  return <>{highlightParts(name, query).map((p, i) => (p.bold ? <Text key={i} style={s.hl}>{p.text}</Text> : p.text))}</>
}

function ResultRow({ r, query, actions, last }: { r: SearchResult; query: string; actions: Actions; last: boolean }) {
  const following = r.following || actions.isFollowing(r.player_id)
  const next = nextLine(r)
  const openable = Boolean(r.player_id || (r.system && r.athlete_id) || r.qid)
  const busy = actions.pending === r.key
  const live = r.live_scores || Boolean(r.player_id)
  return (
    <View style={[s.result, !last && s.line]}>
      <Pressable disabled={!openable} onPress={() => actions.open(r)} style={s.main}>
        <Avatar name={r.name} sport={r.sport} />
        <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
          <Text style={t.title}><Highlight name={r.name} query={query} /></Text>
          <Text style={[t.small, t.muted]} numberOfLines={2}>
            {/* Found by another name: say which. */}
            {r.aka ? <><Highlight name={r.aka} query={query} />{' · '}</> : null}
            {[r.sport, r.detail, r.followers ? `${r.followers} following` : ''].filter(Boolean).join(' · ')}
          </Text>
          {next ? <Text style={[t.tiny, { fontFamily: font[800], color: next.live ? colour.liveText : colour.text }]}>{next.text}</Text> : null}
          {!r.live_scores && !r.player_id ? <Text style={[t.tiny, t.muted]}>AI research · no live scores yet</Text> : null}
        </View>
      </Pressable>
      {following ? (
        <Btn label="Following" icon={<CheckIcon size={16} />} disabled style={{ opacity: 1 }} />
      ) : (
        <Btn
          kind={live ? 'primary' : 'outline'}
          label={live ? 'Follow' : 'Research'}
          accessibilityLabel={`${live ? 'Follow' : 'Research'} ${r.name}`}
          icon={live ? <PlusIcon size={16} color={colour.bg} /> : <SparkIcon size={16} />}
          busy={busy}
          disabled={Boolean(actions.pending) && !busy}
          onPress={() => actions.follow(r)}
        />
      )}
    </View>
  )
}

/** A team by that name, the web's TeamResultRow: its sport and what it plays in, its next game, and Follow. */
function TeamRow({ x, query, actions, last }: { x: TeamSummary; query: string; actions: Actions; last: boolean }) {
  const sports = useSports()
  const following = x.following || actions.isFollowingTeam(x.team_id)
  const busy = actions.pending === `team:${x.team_id}`
  const next = teamNext(x)
  return (
    <View style={[s.result, !last && s.line]}>
      <Pressable onPress={() => actions.openTeam(x)} style={s.main}>
        <Avatar name={x.name} sport={x.sport} />
        <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
          <Text style={t.title}><Highlight name={x.name} query={query} /></Text>
          <Text style={[t.small, t.muted]} numberOfLines={2}>{[sports.name(x.sport), x.detail, x.followers ? `${x.followers} following` : ''].filter(Boolean).join(' · ')}</Text>
          {next ? <Text style={[t.tiny, { fontFamily: font[800], color: next.live ? colour.liveText : colour.text }]}>{next.text}</Text> : null}
        </View>
      </Pressable>
      {following ? (
        <Btn label="Following" icon={<CheckIcon size={16} />} disabled style={{ opacity: 1 }} />
      ) : (
        <Btn kind="primary" label="Follow" accessibilityLabel={`Follow ${x.name}`} icon={<PlusIcon size={16} color={colour.bg} />} busy={busy} disabled={Boolean(actions.pending) && !busy} onPress={() => actions.followTeam(x)} />
      )}
    </View>
  )
}

/** The research agent, offered last: any sport, 1 to 3 minutes, a few a day per fan. */
function ResearchOffer({ query, research, full, actions }: { query: string; research?: Research; full?: boolean; actions: Actions }) {
  const sports = useSports()
  const q = query.trim()
  const { out, text: left } = allowance(research)
  const busy = actions.pending === `research:${q}`
  if (!full) {
    return (
      <View style={{ gap: 2, paddingVertical: 4, paddingHorizontal: 2 }}>
        <Pressable disabled={out || Boolean(actions.pending)} onPress={() => actions.research(q)} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 44, opacity: out ? 0.55 : 1 }}>
          {busy ? <Spinner /> : <SparkIcon size={18} />}
          <Text style={[t.link, { fontSize: 15 }]}>Not here? Research “{q}” with AI</Text>
        </Pressable>
        <Text style={[t.tiny, t.muted]}>Any sport · 1 to 3 minutes{left ? ` · ${left}` : ''}</Text>
      </View>
    )
  }
  return (
    <View style={s.researchCard}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <SparkIcon size={22} />
        <Text style={dsp(26)}>Research it with AI</Text>
      </View>
      <Text style={[t.small, t.muted]}>Our research agent searches the web for “{q}”, works out who it is and builds the page. It takes 1 to 3 minutes and works for any sport.</Text>
      <Text style={[t.small, t.muted]}>{liveNote(sports.running())}</Text>
      <Btn kind="primary" label={`Research “${q}”`} busy={busy} disabled={out || Boolean(actions.pending)} onPress={() => actions.research(q)} />
      {left ? <Text style={[t.tiny, t.muted, { textAlign: 'center' }]}>{left}</Text> : null}
    </View>
  )
}

/** Everything under the search box: namesake picker, sections, did-you-mean and the AI offer. */
export function SearchResults({ data, query, loading, actions }: { data: SearchResponse; query: string; loading?: boolean; actions: Actions }) {
  const parts = sections(data, APP)
  const teams = data.teams ?? []
  const nothing = !data.results.length && !teams.length
  // As the web's: teams first when one is called just what was typed, or no athlete is; else after the athletes.
  const teamsFirst = teams.some((x) => fold(x.name) === fold(data.query)) || (teams.length > 0 && !data.results.length)
  const teamSection = teams.length > 0 ? (
    <View style={{ gap: 10 }}>
      <Eyebrow>Teams</Eyebrow>
      <Card style={s.list}>
        {teams.map((x, i) => <TeamRow key={`team-${x.team_id}`} x={x} query={query} actions={actions} last={i === teams.length - 1} />)}
      </Card>
    </View>
  ) : null
  return (
    <View style={{ gap: 18, opacity: loading ? 0.7 : 1 }}>
      {data.namesakes > 1 ? (
        <View style={{ gap: 6 }}>
          <Text style={dsp(32)}>{data.namesakes === 2 ? 'Two' : data.namesakes} athletes are called {exactName(data)}</Text>
          <Text style={[t.small, t.muted]}>Pick the one you mean. We follow exactly that player, so their scores are never mixed up with another's.</Text>
        </View>
      ) : null}
      {nothing && data.suggestions.length > 0 ? <Text style={[t.small, t.muted]}>No athlete in the live sources is called “{data.query}”. The closest spelling:</Text> : null}
      {nothing && !data.suggestions.length && !loading ? <Text style={[t.small, t.muted]}>No athlete in the live sources is called “{data.query}”.</Text> : null}
      {teamsFirst ? teamSection : null}
      {parts.map((x) => (
        <View key={x.key} style={{ gap: 10 }}>
          {x.title ? <Eyebrow>{x.title}</Eyebrow> : null}
          <Card style={s.list}>
            {x.items.map((r, i) => <ResultRow key={r.key} r={r} query={query} actions={actions} last={i === x.items.length - 1} />)}
          </Card>
        </View>
      ))}
      {!teamsFirst ? teamSection : null}
      {data.partial.length > 0 ? <Text style={[t.tiny, t.muted]}>{data.partial.join(' and ')} didn't answer in time, so some athletes may be missing. Keep typing or try again.</Text> : null}
      {data.researchable
        ? <ResearchOffer query={data.query} research={data.research} full={nothing} actions={actions} />
        : OTHER_SCRIPT.test(data.query) ? <Text style={[t.small, t.muted]}>Search reads names in Latin letters only: type the name as it's spelled in English, like “Virat Kohli”.</Text> : null}
    </View>
  )
}

/** All, then the sports this build runs (GET /api/sports). */
export function SportChips({ sport, onChange }: { sport: string; onChange: (s: string) => void }) {
  const sports = useSports()
  const ink = toneOf(sports, null)
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -GUTTER, flexGrow: 0 }} contentContainerStyle={{ paddingHorizontal: GUTTER, gap: 8 }}>
      <Chip label="All" bare dot={ink.sport} on={ink.on} pressed={sport === ''} onPress={() => onChange('')} />
      {sports.running().map((x) => {
        const tone = toneOf(sports, x.key)
        return <Chip key={x.key} label={x.label} dot={tone.sport} on={tone.on} pressed={sport === x.key} onPress={() => onChange(x.key)} />
      })}
    </ScrollView>
  )
}

const s = StyleSheet.create({
  hl: { fontFamily: font[800], textDecorationLine: 'underline' },
  list: { paddingVertical: 4, paddingHorizontal: 16 },
  result: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingVertical: 12 },
  line: { borderBottomWidth: 1, borderBottomColor: colour.line },
  main: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 12, minWidth: 0 },
  researchCard: { gap: 12, padding: 16, paddingHorizontal: 18, borderRadius: 22, borderWidth: 2, borderStyle: 'dashed', borderColor: colour.text },
})

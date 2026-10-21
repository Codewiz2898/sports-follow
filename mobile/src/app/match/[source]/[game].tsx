// One game's match page, the web's MatchPage.tsx: a band of its sport's colour edge to edge with the way back and
// Follow (the game by itself, with its bell: its level, "Mute this game"), where the game is and its competition,
// the board in the sport's ink, or the title and when it starts; a finished game's verdict big. Then where to watch
// it, while it's on or about to be (Twitch, Kick and YouTube played over the whole screen, others in their platform's
// app or the browser). Then the fan's
// players in it with their lines, what its sport shows under the board (its part: a timeline, a scorecard, maps
// or moves), its key events, the moments found for the players followed in it, and both sides' players to
// follow. Looked at again while the screen is in front (as the web's while its tab is in view).

import { router, useFocusEffect, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { gameLevelText, LEVELS } from '../../../../../web/src/alerts'
import { dateTime, relative } from '../../../../../web/src/format'
import { boardOf, EVERY, followedOf, followHead, keyEvents, nextLook, startsIn, watchable } from '../../../../../web/src/match'
import { useActivity } from '../../../activity'
import { api, ApiError, type GameTeam, type Match, type MatchFollowState } from '../../../api'
import { Avatar, b as blocks, GameBoard, PlainScore, StatGrid } from '../../../blocks'
import { toneOf, useSports, type Tone } from '../../../catalog'
import { useNow } from '../../../clock'
import { useFollowing } from '../../../following'
import { BackIcon, BellIcon, CheckIcon, PlusIcon } from '../../../icons'
import { Page } from '../../../page'
import { Segmented } from '../../../results'
import { matchPartFor, onSport } from '../../../sports'
import { colour, font, num, t } from '../../../theme'
import { FollowPills } from '../../../today/live'
import { useFollowToggle } from '../../../today/starters'
import { breakable, Btn, Card, Dsp, ErrorBoundary, ErrorBox, Eyebrow, ListCard, LiveBadge, Notice, Pill, SectionHead, Spinner } from '../../../ui'
import { Watch } from '../../../watch'

/**
 * A game's match page from the server (GET /api/match/<source>/<game id>), looked at again while the screen is
 * in front and the app is too (match.ts says how often): a look only reads what the server has, which asks the
 * game's source at most once a minute whoever looks. Leaving the screen, or the app, stops it; coming back looks
 * at once. A game the server doesn't know is an error, and isn't asked about again.
 */
function useMatch(source: string, game: string): { match: Match | null; failed: string | null } {
  const [match, setMatch] = useState<Match | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const gone = useRef(false) // the server doesn't know the game: nothing to look at again, come back or not
  useEffect(() => {
    setMatch(null)
    setFailed(null)
    gone.current = false
  }, [source, game])
  useFocusEffect(useCallback(() => {
    let current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let latest = 0 // the newest look: an older one's answer starts no second loop
    const look = (tries: number) => {
      clearTimeout(timer)
      if (AppState.currentState !== 'active' || gone.current) return
      const mine = ++latest
      api.match(source, game)
        .then((m) => {
          if (!current || mine !== latest) return
          setMatch(m)
          setFailed(null)
          const next = nextLook(m.refreshing, tries)
          timer = setTimeout(() => look(next.tries), next.wait)
        })
        .catch((e: Error) => {
          if (!current || mine !== latest) return
          setFailed(e.message)
          gone.current = e instanceof ApiError && e.status === 404
          if (!gone.current) timer = setTimeout(() => look(tries), EVERY)
        })
    }
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') look(0); else clearTimeout(timer) })
    look(0)
    return () => { current = false; clearTimeout(timer); sub.remove() }
  }, [source, game]))
  return { match, failed }
}

/**
 * Following the game by itself (sports_follow/matches.py), as the web's: its key moments from both sides reach
 * the fan until it ends, whoever they follow. The page's answer says whether they do and whether its moments
 * can reach them at all; a tap shows at once, and stays until the page's next look agrees. Once followed, the
 * fan's level for it and "Mute this game", as a player's page has them.
 */
function useFollowGame(match: Match | null) {
  const { followMatch, matches, setMatchAlerts } = useFollowing()
  const { muted, setMuted } = useActivity()
  const [mine, setMine] = useState<MatchFollowState | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const said = match?.follow ?? null
  useEffect(() => { setMine(null); setOpen(false); setError(null) }, [match?.key])
  useEffect(() => { if (mine && said && said.following === mine.following) setMine(null) }, [said, mine])
  const state = mine ?? said
  const followed = match ? followedOf(match, matches, state) : undefined
  const attempt = (work: () => Promise<unknown>) => { setError(null); return work().catch((e: Error) => setError(e.message)) }
  const toggle = async () => {
    if (!match || !state) return
    setBusy(true)
    await attempt(async () => {
      await followMatch(match.match, !state.following, state.event_id ?? followed?.event_id)
      setMine({ ...state, following: !state.following })
      if (state.following) setOpen(false)
    })
    setBusy(false)
  }
  return { state, busy, open, setOpen, error, toggle, followed, muted, setMuted, setMatchAlerts, attempt }
}

/** Where the game is: live, over, called off, or when it starts. */
function Status({ match, tone }: { match: Match; tone: Tone }) {
  if (match.status === 'live') return <LiveBadge />
  if (match.status === 'final') return <Pill label="Result" bg={tone.on} color={tone.sport} />
  if (match.status === 'postponed') return <Pill label="Off" color={tone.on} outline />
  return match.start_utc ? <Pill label={dateTime(match.start_utc)} color={tone.on} outline /> : null
}

const back = () => (router.canGoBack() ? router.back() : router.replace('/'))

/** The game's teams to follow (the web's GameTeams): each its name, opening its page, and Follow, which follows
 * every game of the team from now on, not this one only. This app's word on following one shows over the page's
 * last look; a tap shows at once. */
function GameTeams({ teams }: { teams: GameTeam[] }) {
  const following = useFollowing()
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const follows = (x: GameTeam) => (following.loaded ? following.isFollowingTeam(x.team_id) : x.following)
  const toggle = async (x: GameTeam) => {
    setBusy(x.team_id)
    setError(null)
    try { await following.followTeam(x.team_id, !follows(x)) } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  return (
    <View style={s.section}>
      <SectionHead title="Teams" />
      <ListCard>
        {teams.map((x) => {
          const on = follows(x)
          return (
            <View key={x.team_id} style={s.row}>
              <Pressable onPress={() => router.push(`/team/${x.team_id}`)} accessibilityRole="link" style={[s.row, { flex: 1, justifyContent: 'flex-start' }]}>
                <Avatar name={x.name} sport={x.sport} />
                <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                  <Text style={t.title}>{x.name}</Text>
                  <Text style={[t.small, t.muted]}>{on ? 'Every game of theirs, from both sides' : x.can_follow ? 'Follow every game of theirs' : "Its games can't be followed here now"}</Text>
                </View>
              </Pressable>
              {x.can_follow || on ? (
                <Btn kind={on ? 'outline' : 'primary'} label={on ? 'Following' : 'Follow'} accessibilityLabel={`${on ? 'Following' : 'Follow'} ${x.name}`}
                  icon={on ? <CheckIcon size={16} /> : <PlusIcon size={16} color={colour.bg} />} busy={busy === x.team_id} disabled={busy != null && busy !== x.team_id} onPress={() => { void toggle(x) }} />
              ) : null}
            </View>
          )
        })}
      </ListCard>
      {error ? <ErrorBox>{error}</ErrorBox> : null}
    </View>
  )
}

export default function MatchScreen() {
  const params = useLocalSearchParams<{ source: string; game: string }>()
  const { match, failed } = useMatch(params.source ?? '', params.game ?? '')
  const insets = useSafeAreaInsets()
  const sports = useSports()
  const follow = useFollowToggle()
  const watch = useFollowGame(match)
  const now = useNow()
  const [playing, setPlaying] = useState<string | null>(null) // the stream a fan has started here (Watch), by its address
  useEffect(() => { setPlaying(null) }, [params.source, params.game])

  if (!match) {
    return (
      <Page>
        {failed ? (
          <View style={{ gap: 12 }}>
            <Btn kind="icon" accessibilityLabel="Back" icon={<BackIcon size={20} color={colour.bg} />} onPress={back} />
            <ErrorBox>{failed}</ErrorBox>
          </View>
        ) : <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View>}
      </Page>
    )
  }

  const tone = toneOf(sports, match.sport)
  const pal = onSport(tone)
  const Part = matchPartFor(sports.drawn(match.sport)) // an esports game is drawn as esports
  const live = match.live
  const board = boardOf(match)
  const soon = startsIn(match, now)
  const events = keyEvents(match)
  // Where to watch it: while it's on or about to be, and while a fan's player is on though it's been called over.
  const streams = watchable(match, now)
  const watching = streams.length > 0 ? streams : playing ? match.streams ?? [] : []
  const yours = match.yours ?? []
  const { state, followed } = watch
  const head = followHead(match, state)
  const level = followed?.alerts ?? 'key'
  const eventId = followed?.event_id ?? match.event_ids[0]
  const onBand = { fg: tone.on, border: tone.on, bg: 'transparent' }
  const filled = { fg: tone.sport, border: tone.on, bg: tone.on }

  return (
    <Page top={false} bar={tone.sport}>
      <View style={[blocks.band, { backgroundColor: tone.sport, paddingTop: insets.top + 16 }]}>
        <View style={s.row}>
          <Btn kind="icon" accessibilityLabel="Back" colours={{ bg: tone.on }} icon={<BackIcon size={20} color={tone.sport} />} onPress={back} />
          {/* Following the game itself: none for one that's over, and no promise of alerts its source can't send. */}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 }}>
            {state?.following ? (
              <Btn kind="icon" accessibilityLabel={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} colours={{ bg: tone.on }} icon={<BellIcon size={20} color={tone.sport} />} onPress={() => watch.setOpen((o) => !o)} style={watch.open ? { borderWidth: 3, borderColor: tone.sport } : undefined} />
            ) : null}
            {state && head.button ? (
              <Btn label={state.following ? 'Following' : 'Follow'} icon={state.following ? <CheckIcon size={16} color={tone.sport} /> : <PlusIcon size={16} color={tone.on} />} colours={state.following ? filled : onBand} busy={watch.busy} onPress={watch.toggle} />
            ) : null}
            {head.noAlerts ? <Pill label="No live alerts" color={tone.on} outline /> : null}
          </View>
        </View>
        {watch.open && state?.following ? (
          <View style={s.alertPanel}>
            <View style={s.row}>
              <Text style={t.title}>Alerts for this game</Text>
              <Text onPress={() => router.push({ pathname: '/notifications', params: { tab: 'settings' } })} style={[t.small, t.link]}>Settings</Text>
            </View>
            {followed ? <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={level} onChange={(l) => { watch.attempt(() => watch.setMatchAlerts(followed.event_id, l)) }} /> : null}
            <Text style={[t.small, t.muted]}>{gameLevelText(level, sports.info(match.sport))}</Text>
            {eventId != null ? (
              <Btn small kind={watch.muted.has(eventId) ? 'primary' : 'outline'} label={watch.muted.has(eventId) ? 'Unmute this game' : 'Mute this game'} style={{ alignSelf: 'flex-start' }} onPress={() => { watch.attempt(() => watch.setMuted(eventId, !watch.muted.has(eventId))) }} />
            ) : null}
          </View>
        ) : null}
        <View style={{ gap: 10 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <Status match={match} tone={tone} />
            <Text style={[t.tag, { color: tone.onSoft, flexShrink: 1 }]}>{[match.competition, match.detail].filter(Boolean).join(' · ') || sports.name(match.sport)}</Text>
          </View>
          {/* The board names the sides; without one yet, the title does. */}
          {board ? null : <Dsp size={44} color={tone.on}>{breakable(match.title ?? '')}</Dsp>}
          {soon ? <Dsp size={56} color={tone.on}>{soon}</Dsp> : null}
        </View>
        {board ? (
          <View accessibilityLabel={match.title ?? undefined}>
            {/* A state in a shape this app doesn't know shows as the plain score. The board's live block names no
                player of the fan's: their lines in the game do (a chess board from their side). The fan may explore
                it, where its sport has a way to (a chess board's Play from here). */}
            <ErrorBoundary fallback={<PlainScore live={board} pal={pal} />} resetKey={board}>
              <GameBoard live={board} lines={yours.map((p) => p.stats)} explore pal={pal} />
            </ErrorBoundary>
          </View>
        ) : null}
        {/* Over: who won, big, as its sport says it. */}
        {match.status === 'final' && match.verdict ? <Dsp size={36} color={tone.on}>{breakable(match.verdict)}</Dsp> : null}
        {board?.as_of ? <Text style={[t.tiny, { color: tone.onSoft }]}>Updated {relative(board.as_of, now)}{board.source ? ` · ${board.source}` : ''}</Text> : null}
      </View>
      {watch.error ? <ErrorBox>{watch.error}</ErrorBox> : null}
      {head.note ? <Text style={[t.small, t.muted]}>{head.note}</Text> : null}
      {failed ? <Notice><Text style={[t.small, { flex: 1 }]}>Couldn't update this game: {failed}</Text></Notice> : null}

      {/* Where to watch it, while it's on or about to be (sports_follow/watch.py). */}
      {watching.length > 0 ? (
        <ErrorBoundary resetKey={match.streams}>
          <Watch streams={watching} tone={tone} playing={playing} onPlay={setPlaying} />
        </ErrorBoundary>
      ) : null}

      {(match.teams ?? []).length > 0 ? (
        <ErrorBoundary resetKey={match.teams}>
          <GameTeams teams={match.teams ?? []} />
        </ErrorBoundary>
      ) : null}

      {yours.length > 0 ? (
        <View style={s.section}>
          <SectionHead title="Your players" />
          <ListCard>
            {yours.map((p) => (
              <Pressable key={p.player_id} onPress={() => router.push({ pathname: '/player/[id]', params: { id: String(p.player_id) } })} accessibilityRole="link" style={{ gap: 6 }}>
                <View style={s.row}>
                  <Text style={[t.title, { flexShrink: 1 }]}>{p.name}</Text>
                  {p.result ? <Text style={[t.small, s.strong]}>{p.result}</Text> : null}
                </View>
                {p.headline ? <Text style={[t.small, s.strong]}>{p.headline}</Text> : null}
                {p.stats.length > 0 ? <StatGrid stats={p.stats} paper /> : null}
              </Pressable>
            ))}
          </ListCard>
        </View>
      ) : null}

      {Part ? (
        <ErrorBoundary resetKey={match.page}>
          <Part page={match.page as never} live={live} yours={yours.map((p) => p.name)} tone={tone} />
        </ErrorBoundary>
      ) : null}
      {events.length > 0 ? (
        <View style={s.section}>
          <SectionHead title="Key moments" />
          <ListCard>
            {events.map((e, i) => (
              <View key={`${e.clock}-${i}`} style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10 }}>
                <Text style={[num(17), { minWidth: 44 }]}>{e.clock}</Text>
                <Text style={[t.small, { flex: 1 }]}>{e.text}</Text>
              </View>
            ))}
          </ListCard>
        </View>
      ) : null}

      {match.moments.length > 0 ? (
        <View style={s.section}>
          <SectionHead title="Moments" />
          <ListCard>
            {match.moments.map((m) => (
              <View key={m.id} style={[s.row, { alignItems: 'flex-start' }]}>
                <Pressable onPress={() => router.push({ pathname: '/player/[id]', params: { id: String(m.player_id) } })} accessibilityRole="link" style={{ flex: 1, gap: 2 }}>
                  <Text style={t.title}>{m.title}</Text>
                  {m.body ? <Text style={[t.small, t.muted]}>{m.body}</Text> : null}
                </Pressable>
                <Text style={[t.tiny, t.muted]}>{relative(m.at, now)}</Text>
              </View>
            ))}
          </ListCard>
        </View>
      ) : null}

      {match.sides.length > 0 ? (
        <View style={s.section}>
          <SectionHead title="Players" />
          <Card pad style={{ gap: 16 }}>
            {match.sides.map((side) => (
              <View key={side.name} style={{ gap: 8 }}>
                {side.name ? <Eyebrow>{side.name}</Eyebrow> : null}
                <FollowPills game={{ title: side.name || (match.title ?? ''), players: side.players }} sport={match.sport} follow={follow} />
              </View>
            ))}
          </Card>
          {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
        </View>
      ) : null}

      <Text style={[t.tiny, t.muted]}>
        {live?.lag_note ? `${live.lag_note} ` : ''}
        <Text onPress={() => router.push('/credits')} style={t.link}>Credits</Text>
      </Text>
    </Page>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  section: { gap: 10 },
  strong: { fontFamily: font[800] },
  alertPanel: { gap: 8, padding: 14, borderRadius: 18, backgroundColor: colour.surface },
})

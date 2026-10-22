// One team's page, the web's TeamPage.tsx: a band of its sport's colour with share, alerts and follow, then its
// game on now (to its match page), its games to come, its form and last results from its side, and its squad to
// follow player by player. Read again every 30 s while the page is open and the app in front, sooner while a read of
// its games is on its way (a team nobody followed yet is read when its page opens). Following a team doesn't follow
// its players.

import Constants from 'expo-constants'
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { LEVELS, teamLevelText } from '../../../../web/src/alerts'
import { relative } from '../../../../web/src/format'
import { EVERY, nextLook } from '../../../../web/src/match'
import { shareTeam } from '../../../../web/src/share'
import { useActivity } from '../../activity'
import { api, ApiError, type TeamPage } from '../../api'
import { FormStrip, LiveScoreboard, PlayerBand, ResultsList, UpcomingList } from '../../blocks'
import { toneOf, useSports } from '../../catalog'
import { useNow } from '../../clock'
import { useFollowing } from '../../following'
import { BackIcon, BellIcon, CheckIcon, PlusIcon } from '../../icons'
import { Page } from '../../page'
import { Segmented } from '../../results'
import { ShareButton } from '../../share'
import { colour, t } from '../../theme'
import { FollowPills } from '../../today/live'
import { useFollowToggle } from '../../today/starters'
import { Btn, Card, ErrorBoundary, ErrorBox, Eyebrow, SectionHead, Spinner } from '../../ui'

const APP = Constants.expoConfig?.name ?? ''

export default function TeamScreen() {
  const params = useLocalSearchParams<{ id: string }>()
  const teamId = /^\d+$/.test(params.id ?? '') ? Number(params.id) : NaN
  const insets = useSafeAreaInsets()
  const sports = useSports()
  const following = useFollowing()
  const { muted, setMuted } = useActivity()
  const follow = useFollowToggle()
  const now = useNow()
  const [team, setTeam] = useState<TeamPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null) // a follow or alert change the server refused
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [asked, setAsked] = useState(0)
  // As the web's: a followed team's games read anew reach Following too (its tile). Once a read: Following's own
  // functions change with every list it loads, so they're read through a ref.
  const read = team?.fetched_at
  const followingNow = useRef(following)
  followingNow.current = following
  useEffect(() => { if (read && followingNow.current.isFollowingTeam(teamId)) void followingNow.current.reload() }, [read, teamId])

  // The page, and again while it's in front: soon while its games are being read, else every 30 s (match.ts). Not
  // while the app is away, as the match page: back in front, at once (BUG-161). A team the server doesn't have isn't
  // asked about again.
  useFocusEffect(useCallback(() => {
    if (!Number.isFinite(teamId)) { setError('No such team.'); return }
    let gone = false
    let missing = false // the server doesn't have it: nothing to look at again, come back or not
    let timer: ReturnType<typeof setTimeout> | undefined
    let latest = 0 // the newest look: an older one's answer starts no second loop
    const look = (tries: number) => {
      clearTimeout(timer)
      if (AppState.currentState !== 'active' || missing) return // nobody's looking; coming back looks
      const mine = ++latest
      api.team(teamId)
        .then((x) => {
          if (gone || mine !== latest) return
          setTeam(x)
          setError(null)
          const next = nextLook(x.refreshing, tries)
          timer = setTimeout(() => look(next.tries), next.wait)
        })
        .catch((e: Error) => {
          if (gone || mine !== latest) return
          setError(e.message)
          missing = e instanceof ApiError && e.status === 404
          if (!missing) timer = setTimeout(() => look(tries), EVERY)
        })
    }
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') look(0); else clearTimeout(timer) })
    look(0)
    return () => { gone = true; clearTimeout(timer); sub.remove() }
  }, [teamId, asked]))

  if (!team || team.team_id !== teamId) {
    return (
      <Page>
        {error ? <ErrorBox>{error}</ErrorBox> : <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View>}
      </Page>
    )
  }

  const tone = toneOf(sports, team.sport)
  // This app's word on following it, and its level, over the page's last look: a tap shows at once.
  const mine = following.teams.find((x) => x.team_id === team.team_id)
  const isFollowing = following.loaded ? mine != null : team.following
  const level = mine?.alerts ?? team.alerts
  const live = team.live?.is_live ? team.live : null
  const eventId = live?.event_id ?? null
  const onBand = { fg: tone.on, border: tone.on, bg: 'transparent' }
  const filled = { fg: tone.sport, border: tone.on, bg: tone.on }
  const attempt = (work: () => Promise<unknown>) => {
    setFailure(null)
    return work().catch((e: Error) => setFailure(e.message))
  }
  const toggle = async () => {
    setBusy(true)
    await attempt(async () => {
      await following.followTeam(team.team_id, !isFollowing)
      if (isFollowing) setOpen(false)
      setAsked((n) => n + 1)
    })
    setBusy(false)
  }

  return (
    <Page top={false} bar={tone.sport}>
      <PlayerBand
        topInset={insets.top}
        name={team.name}
        sport={team.sport}
        meta={[team.detail, team.followers ? `${team.followers} following` : ''].filter(Boolean).join(' · ')}
        back={<Btn kind="icon" accessibilityLabel="Back" colours={{ bg: tone.on }} icon={<BackIcon size={20} color={tone.sport} />} onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} />}
        actions={
          <>
            <ShareButton {...shareTeam(team.team_id, team.name, APP)} colours={{ bg: tone.on, fg: tone.sport }} />
            {isFollowing ? (
              <Btn kind="icon" accessibilityLabel={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} colours={{ bg: tone.on }} icon={<BellIcon size={20} color={tone.sport} />} onPress={() => setOpen((o) => !o)} style={open ? { borderWidth: 3, borderColor: tone.sport } : undefined} />
            ) : null}
            {team.can_follow || isFollowing ? (
              <Btn label={isFollowing ? 'Following' : 'Follow'} icon={isFollowing ? <CheckIcon size={16} color={tone.sport} /> : <PlusIcon size={16} color={tone.on} />} colours={isFollowing ? filled : onBand} busy={busy} onPress={toggle} />
            ) : null}
          </>
        }
      >
        {open && isFollowing ? (
          <View style={s.alertPanel}>
            <View style={s.row}>
              <Text style={[t.title, { flexShrink: 1 }]}>Alerts for {team.name}</Text>
              <Text onPress={() => router.push({ pathname: '/notifications', params: { tab: 'settings' } })} style={[t.small, t.link]}>Settings</Text>
            </View>
            <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={level} onChange={(l) => { attempt(() => following.setTeamAlerts(team.team_id, l)) }} />
            <Text style={[t.small, t.muted]}>{teamLevelText(level, sports.info(team.sport))}</Text>
            {eventId != null ? (
              <Btn small label={muted.has(eventId) ? 'Unmute this game' : 'Mute this game'} style={{ alignSelf: 'flex-start' }} onPress={() => { attempt(() => setMuted(eventId, !muted.has(eventId))) }} />
            ) : null}
          </View>
        ) : null}
      </PlayerBand>
      {failure ? <ErrorBox>{failure}</ErrorBox> : null}
      {!team.can_follow && team.note ? <Text style={[t.small, t.muted]}>{team.note}</Text> : null}
      {error ? <Text style={[t.small, t.muted]}>Couldn't update this team: {error}</Text> : null}

      <ErrorBoundary resetKey={team}>
      {/* The board links to its match page and its source itself, as the web's. */}
      {live ? <LiveScoreboard live={live} playerName="" sport={team.sport} /> : null}

      <View style={s.section}>
        <SectionHead title="Coming up" />
        {team.refreshing && !team.upcoming.length && !team.recent.length ? (
          <Card pad style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 }}><Spinner /><Text style={[t.small, t.muted]}>Reading its games…</Text></Card>
        ) : (
          <UpcomingList items={team.upcoming.filter((u) => u.event_id !== eventId)} empty="No games coming up found." />
        )}
      </View>

      {team.form.length >= 3 ? (
        <Card pad style={s.row}>
          <View style={{ gap: 2 }}>
            <Eyebrow>Form</Eyebrow>
            <Text style={t.small}>Last {team.form.length}, newest first</Text>
          </View>
          <FormStrip marks={team.form} />
        </Card>
      ) : null}

      <View style={s.section}>
        <SectionHead title="Results" />
        <ResultsList items={team.recent} teams={[team.name]} />
      </View>

      {team.squad.length > 0 ? (
        <View style={s.section}>
          <SectionHead title="Squad" />
          <Card pad style={{ gap: 10 }}>
            <Text style={[t.small, t.muted]}>Follow a player for their own moments, as well as the team's.</Text>
            <FollowPills game={{ title: team.name, players: team.squad }} sport={team.sport} follow={follow} />
          </Card>
          {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
        </View>
      ) : null}
      </ErrorBoundary>

      <Text style={[t.tiny, t.muted]}>
        {team.fetched_at ? `${sports.name(team.sport) || 'Games'} from ${team.source}, read ${relative(team.fetched_at, now)}. ` : `From ${team.source}. `}
        <Text onPress={() => router.push('/credits')} style={t.link}>Credits</Text>
      </Text>
    </Page>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  alertPanel: { gap: 8, padding: 14, borderRadius: 18, backgroundColor: colour.surface },
  section: { gap: 10 },
})

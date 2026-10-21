// A campaign page, /<slug> (the web's CampaignPage.tsx, campaigns.py): the page a launch post links to, which a
// stann.in link opens here (App Links, app.config.ts). Its event in a block of the sport's colour; the games on
// now, when one is; its players, or whole teams, to follow with a tap, with key alerts; then turning alerts on;
// and its games, in the home audience's time. What it shows is web/src/campaign.ts's. The web's "Get the
// alerts" (installing from the browser) isn't here: this is the app.

import Constants from 'expo-constants'
import { router, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Alert, Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { allFollowed, campaignLive, campaignRows, followAllFailed, followAllWords, keyAlertsLine, schedule } from '../../../web/src/campaign'
import { countdown, shortName } from '../../../web/src/format'
import { api, ApiError, type Campaign, type CampaignTeam, type SearchResult } from '../api'
import { Backdrop } from '../art'
import { LiveScoreboard } from '../blocks'
import { toneOf, useSports, type Tone } from '../catalog'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { CheckIcon, PlusIcon } from '../icons'
import { Page } from '../page'
import { usePush } from '../push'
import { colour, font, RADIUS_CARD, t } from '../theme'
import { useLive } from '../today/live'
import { useGoToPlace } from '../tabbar'
import { PickTile, useFollowToggle } from '../today/starters'
import { alpha, Btn, Card as Surface, Dsp, ErrorBox, NotFound, Pill, Spinner } from '../ui'

const APP = Constants.expoConfig?.name ?? ''
const here = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

export default function CampaignScreen() {
  const { slug = '' } = useLocalSearchParams<{ slug: string }>()
  const [campaign, setCampaign] = useState<Campaign | null>(null)
  const [missing, setMissing] = useState(false)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => api.campaign(slug).then((c) => { setCampaign(c); setFailed(false) }, (e) => {
    if (e instanceof ApiError && e.status === 404) setMissing(true)
    else setFailed(true)
  }), [slug])
  useEffect(() => { setCampaign(null); setMissing(false); void load() }, [load])

  if (missing) return <Page><NotFound /></Page>
  if (!campaign) {
    return (
      <Page>
        {failed ? (
          <View style={{ gap: 12 }}>
            <ErrorBox>Couldn't load this page. Check your connection.</ErrorBox>
            <Btn kind="primary" label="Try again" style={{ alignSelf: 'flex-start' }} onPress={() => void load()} />
          </View>
        ) : <Spinner />}
      </Page>
    )
  }
  return <CampaignView c={campaign} reload={load} />
}

function CampaignView({ c, reload }: { c: Campaign; reload: () => Promise<void> }) {
  const now = useNow()
  const insets = useSafeAreaInsets()
  const { width } = useWindowDimensions()
  const sports = useSports()
  const following = useFollowing()
  const follow = useFollowToggle()
  const live = useLive()
  const go = useGoToPlace()
  const tone = toneOf(sports, c.sport)
  const games = schedule(c, now, here())
  const next = games.find((g) => g.next)
  const left = next ? countdown(next.start_utc, now) : ''
  const { mine, others } = campaignLive(c, following.cards, live)
  const anyFollowed = campaignRows(c).some(follow.followed)

  return (
    <Page top={false} bar={tone.sport}>
      <View style={{ gap: 18 }}>
        <View style={[s.band, { backgroundColor: tone.sport, paddingTop: insets.top + 14 }]}>
          <Backdrop sport={tone.key} seed={c.slug} colour={tone.on} opacity={0.16} />
          <View style={s.bar}>
            <Pressable accessibilityRole="link" accessibilityLabel={`${APP}, Today`} hitSlop={8} onPress={() => go('/')}>
              <Text style={[s.brand, { color: tone.on }]}>{APP}</Text>
            </Pressable>
            {left ? <Pill label={`In ${left}`} bg={tone.on} color={tone.sport} /> : null}
          </View>
          <View style={{ gap: 10 }}>
            <Text style={[t.tag, { color: tone.onSoft }]}>{c.event || sports.info(c.sport).label}</Text>
            {/* As the web's clamp(54px, 16vw, 84px). */}
            <Dsp size={Math.round(Math.min(84, Math.max(54, width * 0.16)))} color={tone.on} style={{ paddingRight: 6 }}>{c.title}</Dsp>
          </View>
          {c.lines.length ? (
            <View style={{ gap: 6 }}>{c.lines.map((line) => <Text key={line} style={[s.line, { color: tone.on }]}>{line}</Text>)}</View>
          ) : null}
          {next ? (
            <Text style={[s.next, { color: tone.on, borderTopColor: alpha(tone.on, 0.22) }]}>
              <Text style={{ fontFamily: font[800] }}>{next.label}</Text> · {next.day}, {next.time} {next.zone}
            </Text>
          ) : null}
        </View>

        {mine.length || others.length ? (
          <View style={s.section} accessibilityLabel="Live now">
            {mine.map((card) => (
              <Pressable key={card.player_id} onPress={() => router.push({ pathname: '/player/[id]', params: { id: String(card.player_id) } })}>
                <LiveScoreboard live={card.live} playerName={card.player.name} sport={card.player.sport} teams={card.player.teams} compact seed={`${c.slug}:${card.live.event_id ?? card.player_id}`} />
              </Pressable>
            ))}
            {others.map((g) => <LiveScoreboard key={g.key} live={g.live} playerName="" sport={c.sport} compact seed={g.key} />)}
          </View>
        ) : null}

        <FollowBlocks c={c} tone={tone} follow={follow} reload={reload} keyLine={keyAlertsLine(sports.info(c.sport))} />

        {anyFollowed ? <AlertsPrompt /> : null}

        <View style={s.section} accessibilityLabel="Games">
          <Dsp size={28}>{games.length === 1 ? 'The game' : 'Games'}</Dsp>
          {games.length ? (
            <Surface style={s.games}>
              {games.map((g, i) => (
                <View key={`${g.start_utc}-${g.label}`} style={[s.game, i < games.length - 1 && s.gameLine]}>
                  {g.next ? <View style={[s.nextMark, { backgroundColor: tone.sport }]} /> : null}
                  <Dsp size={24} color={g.past ? colour.muted : undefined} style={{ flexShrink: 1 }}>{g.label}</Dsp>
                  <View style={{ gap: 2, alignItems: 'flex-end', flexShrink: 1 }}>
                    <Text style={[t.body, { fontFamily: font[700], textAlign: 'right' }, g.past && t.muted]}>{g.today ? 'Today' : g.day} · {g.time} {g.zone}</Text>
                    {g.yours ? <Text style={[t.tiny, t.muted, { textAlign: 'right' }]}>{g.yours}</Text> : null}
                  </View>
                </View>
              ))}
            </Surface>
          ) : (
            <Text style={[t.body, t.muted, { fontFamily: font[600] }]}>The schedule is coming soon.</Text>
          )}
        </View>
      </View>
    </Page>
  )
}

type Follow = ReturnType<typeof useFollowToggle>

/** Asks before one tap unfollows several players (the web's window.confirm). */
function confirm(question: string): Promise<boolean> {
  return new Promise((answer) => Alert.alert(question, undefined, [
    { text: 'Cancel', style: 'cancel', onPress: () => answer(false) },
    { text: 'Unfollow', style: 'destructive', onPress: () => answer(true) },
  ], { cancelable: true, onDismiss: () => answer(false) }))
}

/**
 * The players to follow, side by side in the sport's colour, each with a tap, and all of them with one; then
 * each whole team: its roster's players, and its whole roster in one tap (POST /api/campaigns/<slug>/follow).
 * Following is with key alerts, which the sport words (`keyLine`). A tap on all of them followed unfollows them.
 */
function FollowBlocks({ c, tone, follow, reload, keyLine }: { c: Campaign; tone: Tone; follow: Follow; reload: () => Promise<void>; keyLine: string }) {
  const following = useFollowing()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const asking = useRef(false) // a follow-all under way, from the first tap: a second can't open another dialog

  const toggleOne = (r: SearchResult) => void follow.toggle(r).then(reload)
  const toggleAll = async (key: string, rows: SearchResult[], which: { team: string } | { players: true }, ask: string) => {
    if (asking.current) return
    asking.current = true
    const undo = allFollowed(rows, follow.followed)
    try {
      // Some may have been followed elsewhere first, with their own alerts: ask before they all go.
      if (undo && !(await confirm(ask))) return
      setBusy(key)
      setError(null)
      if (undo) {
        for (const r of rows) if (r.player_id != null && following.isFollowing(r.player_id)) await following.unfollow(r.player_id)
      } else {
        const done = await api.campaignFollow(c.slug, which)
        await following.reload()
        setError(followAllFailed(done.failed))
      }
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      asking.current = false
      setBusy(null)
    }
  }

  if (!c.players.length && !c.teams.length) {
    return <Surface pad><Text style={t.title}>Who to follow is coming soon.</Text></Surface>
  }
  const own = followAllWords(c)
  const versus = c.players.length === 2
  return (
    <View style={s.section} accessibilityLabel="Follow">
      {c.players.length ? (
        <>
          <View style={versus ? s.versus : s.players}>
            {c.players.map((r) => (
              <View key={r.key} style={versus ? { flex: 1 } : undefined}>
                <PickTile sport={c.sport} name={r.name} detail={r.detail} on={follow.followed(r)} busy={follow.busy(r)} onPress={() => toggleOne(r)} big tall />
              </View>
            ))}
          </View>
          {c.players.length > 1 ? (
            <FollowAll on={allFollowed(c.players, follow.followed)} busy={busy === 'players'} label={own.label} done={own.done}
              onPress={() => void toggleAll('players', c.players, { players: true }, own.ask)} />
          ) : null}
        </>
      ) : null}
      {c.teams.map((team) => {
        const words = followAllWords(c, team)
        return (
          <TeamBlock key={team.team_id} team={team} tone={tone} follow={follow} onPress={toggleOne}>
            <FollowAll on={allFollowed(team.players, follow.followed)} busy={busy === team.team_id} label={words.label} done={words.done} tone={tone}
              onPress={() => void toggleAll(team.team_id, team.players, { team: team.team_id }, words.ask)} />
          </TeamBlock>
        )
      })}
      <Text style={[t.small, t.muted, { fontFamily: font[600] }]}>{keyLine}</Text>
      {error || follow.error ? <ErrorBox>{error || follow.error}</ErrorBox> : null}
    </View>
  )
}

/** A whole team (.campaign-team), in the sport's colour: its name, its roster's players to follow one by one
 * (ringed pills, filled once followed), and its follow-all (`children`) once it has a roster. */
function TeamBlock({ team, tone, follow, onPress, children }: { team: CampaignTeam; tone: Tone; follow: Follow; onPress: (r: SearchResult) => void; children: ReactNode }) {
  return (
    <View style={[s.team, { backgroundColor: tone.sport }]} accessibilityLabel={team.name}>
      <View style={s.teamHead}>
        <Dsp size={44} color={tone.on} style={{ flexShrink: 1 }}>{team.name}</Dsp>
        {team.game ? <Text style={[t.tag, { color: tone.onSoft, flexShrink: 0, paddingBottom: 6 }]}>{team.game}</Text> : null}
      </View>
      {team.players.length ? (
        <View style={s.who} accessibilityLabel={`${team.name}'s players`}>
          {team.players.map((r) => {
            const on = follow.followed(r)
            const busy = follow.busy(r)
            const ink = on ? tone.sport : tone.on
            return (
              <Pressable key={r.key} accessibilityRole="button" accessibilityLabel={`Follow ${r.name}`} accessibilityState={{ selected: on, busy }} onPress={() => onPress(r)}
                style={[s.pill, { borderColor: tone.on }, on && { backgroundColor: tone.on }]}>
                {busy ? <Spinner size={12} color={ink} /> : on ? <CheckIcon size={14} color={ink} /> : <PlusIcon size={14} color={ink} />}
                <Text style={[s.pillText, { color: ink }]}>{shortName(r.name)}</Text>
              </Pressable>
            )
          })}
        </View>
      ) : (
        <Text style={[t.small, { color: tone.onSoft }]}>The line-up is coming soon.</Text>
      )}
      {team.players.length ? children : null}
    </View>
  )
}

/** Follow them all (.btn.campaign-all): filled in ink, or in a team block (`tone`) in the block's ink; once
 * they're all followed, its ring alone. */
function FollowAll({ on, busy, onPress, label, done, tone }: { on: boolean; busy: boolean; onPress: () => void; label: string; done: string; tone?: Tone }) {
  const ink = tone ? tone.on : colour.text
  const ground = tone ? tone.sport : colour.bg
  return on ? (
    <Btn label={done} busy={busy} icon={<CheckIcon size={18} color={ink} />} colours={{ fg: ink, border: ink }} onPress={onPress} style={s.all} />
  ) : (
    <Btn kind="primary" label={label} busy={busy} icon={<PlusIcon size={18} color={ground} />} colours={{ fg: ground, bg: ink }} onPress={onPress} style={s.all} />
  )
}

/** After a follow here: turn alerts on, on this phone (push.ts). */
function AlertsPrompt() {
  const push = usePush()
  if (push.on) {
    return (
      <View style={[s.alerts, s.alertsOn]} accessibilityRole="summary">
        <CheckIcon size={18} />
        <Text style={t.title}>Alerts are on for this device.</Text>
      </View>
    )
  }
  return (
    <View style={s.alerts} accessibilityLabel="Alerts">
      <Text style={t.title}>Now turn alerts on</Text>
      {push.unavailable ? (
        <Text style={t.small}>{push.unavailable}</Text>
      ) : (
        <>
          <Text style={t.small}>So key alerts reach you, with {APP} closed too.</Text>
          <Btn kind="primary" label="Turn on alerts" busy={push.busy} style={{ alignSelf: 'flex-start' }} onPress={() => void push.enable()} />
        </>
      )}
      {push.message ? <Text style={t.small}>{push.message}</Text> : null}
    </View>
  )
}

const s = StyleSheet.create({
  band: { overflow: 'hidden', gap: 14, marginHorizontal: -16, paddingHorizontal: 16, paddingBottom: 22, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 },
  bar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, minHeight: 36 },
  brand: { fontFamily: font.logo, fontSize: 26, lineHeight: 30, letterSpacing: -0.26, textTransform: 'lowercase' }, // as the logo writes the name
  line: { fontFamily: font[700], fontSize: 16, lineHeight: 22.4 },
  next: { paddingTop: 12, borderTopWidth: 2, fontFamily: font[700], fontSize: 15, lineHeight: 21.75 },
  section: { gap: 10 },
  players: { gap: 8 },
  versus: { flexDirection: 'row', gap: 8 },
  all: { alignSelf: 'stretch', height: 52 },
  team: { gap: 12, paddingTop: 16, paddingHorizontal: 18, paddingBottom: 18, borderRadius: RADIUS_CARD },
  teamHead: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  who: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 34, paddingHorizontal: 12, borderRadius: 999, borderWidth: 2 },
  pillText: { fontFamily: font[700], fontSize: 13 },
  alerts: { gap: 8, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_CARD, backgroundColor: colour.surface, borderWidth: 2, borderColor: colour.text },  // white, ringed in ink: it asks for a tap
  alertsOn: { flexDirection: 'row', alignItems: 'center', borderColor: 'transparent' },
  games: { paddingVertical: 4, paddingHorizontal: 16 },
  game: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 56, paddingVertical: 12 },
  gameLine: { borderBottomWidth: 1, borderBottomColor: colour.line },
  nextMark: { position: 'absolute', left: -16, top: 10, bottom: 10, width: 5, borderTopRightRadius: 3, borderBottomRightRadius: 3 },
})

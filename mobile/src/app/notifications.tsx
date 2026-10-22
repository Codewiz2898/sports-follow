// Alerts, the web's NotificationsPage.tsx: what's happened to the fan's players, teams and games (Latest), and
// what reaches this phone (on or off, quiet hours: push.ts) and how much to hear about each player, each team
// and each game followed by itself (Settings).

import { router, useLocalSearchParams } from 'expo-router'
import { useEffect, useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { DEVICE, gameLevelText, LEVELS, levelText, nothingYet, teamLevelText } from '../../../web/src/alerts'
import { dayLabel, relative } from '../../../web/src/format'
import { useAccount } from '../account'
import { useActivity } from '../activity'
import type { ActivityItem, AlertLevel } from '../api'
import { Avatar, Empty, openMatch } from '../blocks'
import { useSports } from '../catalog'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { ChevronIcon, UserIcon } from '../icons'
import { Segmented } from '../results'
import { colour, dsp, font, t } from '../theme'
import { Btn, Card, ErrorBox, Eyebrow, SectionHead, Spinner, Switch, TimeField } from '../ui'
import { Page } from '../page'
import { usePush } from '../push'

export default function AlertsScreen() {
  const params = useLocalSearchParams<{ tab?: string }>()
  const [tab, setTab] = useState<'latest' | 'settings'>(params.tab === 'settings' ? 'settings' : 'latest')
  useEffect(() => { if (params.tab === 'settings') setTab('settings') }, [params.tab])
  return (
    <Page>
      <Text style={dsp(54)}>Alerts</Text>
      <Segmented<'latest' | 'settings'> options={[['latest', 'Latest'], ['settings', 'Settings']]} value={tab} onChange={setTab} />
      {tab === 'latest' ? <Latest onSettings={() => setTab('settings')} /> : <Settings />}
      <View style={{ flexDirection: 'row', gap: 12 }}>
        <Text onPress={() => router.push('/credits')} style={[t.tiny, t.muted, t.link]}>Credits and sources</Text>
        <Text onPress={() => router.push('/privacy')} style={[t.tiny, t.muted, t.link]}>Privacy</Text>
        <Text onPress={() => router.push('/terms')} style={[t.tiny, t.muted, t.link]}>Terms</Text>
      </View>
    </Page>
  )
}

/** Every moment of the fan's players, newest first, by day; what came since they last looked is marked. */
function Latest({ onSettings }: { onSettings: () => void }) {
  const { cards, matches, teams } = useFollowing()
  const activity = useActivity()
  // What was new when the list opened stays marked while it's open; the badge clears at once.
  const [seenAtOpen] = useState(activity.seen)
  const { markSeen } = activity
  useEffect(() => { markSeen() }, [markSeen])
  const now = useNow()
  const sportOf = (id: number) => cards.find((c) => c.player_id === id)?.player.sport

  if (!activity.loaded) return <Empty><Spinner /></Empty>
  if (!activity.items.length) {
    // A fan of a team alone, or a game, hears its moments too: Settings turns this phone's alerts on for them.
    const follows = cards.length + teams.length + matches.length > 0
    return (
      <Card style={{ paddingVertical: 28, paddingHorizontal: 20, alignItems: 'center', gap: 4 }}>
        <Text style={[t.small, t.muted, { textAlign: 'center' }]}>{nothingYet(cards.length, teams.length, matches.length)}</Text>
        {follows ? <Btn kind="link" label="Choose what reaches this phone" onPress={onSettings} /> : null}
      </Card>
    )
  }
  const days: [string, ActivityItem[]][] = []
  for (const item of activity.items) {
    const label = dayLabel(item.at, new Date(now))
    if (days[days.length - 1]?.[0] === label) days[days.length - 1][1].push(item)
    else days.push([label, [item]])
  }
  return (
    <>
      {days.map(([label, items]) => (
        <View key={label} style={{ gap: 10 }}>
          <Eyebrow>{label}</Eyebrow>
          <Card style={s.list}>
            {items.map((item, i) => <LatestRow key={item.id} item={item} sport={item.player_id != null ? sportOf(item.player_id) : item.sport} fresh={item.id > seenAtOpen} last={i === items.length - 1} />)}
          </Card>
        </View>
      ))}
      {activity.more ? <MoreButton more={activity.more} /> : null}
    </>
  )
}

function LatestRow({ item, sport, fresh, last }: { item: ActivityItem; sport?: string | null; fresh: boolean; last: boolean }) {
  const now = useNow()
  const [context] = item.body.split('\n')
  const inside = item.url.startsWith('/') && !item.url.startsWith('//')
  return (
    <View style={[s.row, !last && s.line]}>
      {/* A game's own moment (a game followed by itself, BHA-270) wears its sport, by the game's name. */}
      <Avatar name={item.player ?? item.event ?? ''} sport={sport} size="sm" />
      <Pressable disabled={!inside} onPress={() => router.push(item.url as never)} style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text style={[t.title, item.level === 'minor' && { fontFamily: font[600] }]}>{item.title}</Text>
        {context ? <Text style={[t.small, t.muted]}>{context}</Text> : null}
        <Text style={[t.tiny, t.muted]}>{[!context?.startsWith(item.event ?? '\0') && item.event, relative(item.at, now), item.muted && 'Muted'].filter(Boolean).join(' · ')}</Text>
      </Pressable>
      {fresh ? <View accessibilityLabel="New" style={s.fresh} /> : null}
    </View>
  )
}

function MoreButton({ more }: { more: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return <Btn label="Show more" busy={busy} style={{ alignSelf: 'center' }} onPress={() => { setBusy(true); more().finally(() => setBusy(false)) }} />
}

/** Signed in or not: the way to keep these settings on every device. */
function AccountRow() {
  const { me } = useAccount()
  if (!me) return null
  return (
    <Pressable onPress={() => router.push('/account')}>
      <Card style={[s.accountRow]}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
          <UserIcon size={20} />
          <View style={{ flex: 1, gap: 4 }}>
            <Text style={t.title}>{me.signed_in ? me.email : 'Sign in'}</Text>
            <Text style={[t.small, t.muted]}>{me.signed_in ? 'These settings are the same on every device you sign in on.' : 'Keep your players and these settings on every device.'}</Text>
          </View>
        </View>
        <ChevronIcon size={18} />
      </Card>
    </Pressable>
  )
}

/** On this phone, and how much to hear about each player, each team and each game followed by itself. */
function Settings() {
  const sports = useSports()
  const { cards, setAlerts, matches, setMatchAlerts, teams, setTeamAlerts } = useFollowing()
  const [failed, setFailed] = useState<Record<number, string>>({})
  const [gameFailed, setGameFailed] = useState<Record<number, string>>({})
  const [teamFailed, setTeamFailed] = useState<Record<number, string>>({})
  const chooseTeam = (teamId: number, level: AlertLevel) => {
    setTeamFailed((f) => ({ ...f, [teamId]: '' }))
    setTeamAlerts(teamId, level).catch((e: Error) => setTeamFailed((f) => ({ ...f, [teamId]: e.message })))
  }
  const choose = (playerId: number, level: AlertLevel) => {
    setFailed((f) => ({ ...f, [playerId]: '' }))
    setAlerts(playerId, level).catch((e: Error) => setFailed((f) => ({ ...f, [playerId]: e.message })))
  }
  const chooseGame = (eventId: number, level: AlertLevel) => {
    setGameFailed((f) => ({ ...f, [eventId]: '' }))
    setMatchAlerts(eventId, level).catch((e: Error) => setGameFailed((f) => ({ ...f, [eventId]: e.message })))
  }
  return (
    <>
      <AccountRow />
      <Device />

      <View style={{ gap: 10 }}>
        <SectionHead title="Per player" right={<Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>Key moments is the default</Text>} />
        {cards.length === 0 ? (
          <Empty>Follow a player to choose what you hear about them.</Empty>
        ) : (
          <Card style={s.list}>
            {cards.map((c, i) => {
              const level = c.alerts ?? 'key'
              return (
                <View key={c.player_id} style={[{ gap: 10, paddingVertical: 14 }, i < cards.length - 1 && s.line]}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                    <Avatar name={c.player.name} sport={c.player.sport} size="sm" />
                    <View style={{ flex: 1 }}>
                      <Text onPress={() => router.push(`/player/${c.player_id}`)} style={t.title}>{c.player.name}</Text>
                      <Text style={[t.tiny, t.muted]}>{levelText(level, sports.info(c.player.sport)).brief}</Text>
                    </View>
                  </View>
                  <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={level} onChange={(l) => choose(c.player_id, l)} />
                  {failed[c.player_id] ? <ErrorBox>{failed[c.player_id]}</ErrorBox> : null}
                </View>
              )
            })}
          </Card>
        )}
      </View>

      {/* The teams followed (sports_follow/teams.py): each of their games, from both sides, and their results. */}
      {teams.length > 0 ? (
        <View style={{ gap: 10 }}>
          <SectionHead title="Per team" right={<Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>Every game of theirs</Text>} />
          <Card style={s.list}>
            {teams.map((x, i) => (
              <View key={x.team_id} style={[{ gap: 10, paddingVertical: 14 }, i < teams.length - 1 && s.line]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                  <Avatar name={x.name} sport={x.sport} size="sm" />
                  <View style={{ flex: 1 }}>
                    <Text onPress={() => router.push(`/team/${x.team_id}`)} accessibilityRole="link" style={t.title}>{x.name}</Text>
                    <Text style={[t.tiny, t.muted]}>{teamLevelText(x.alerts, sports.info(x.sport))}</Text>
                  </View>
                </View>
                <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={x.alerts} onChange={(l) => chooseTeam(x.team_id, l)} />
                {teamFailed[x.team_id] ? <ErrorBox>{teamFailed[x.team_id]}</ErrorBox> : null}
              </View>
            ))}
          </Card>
        </View>
      ) : null}

      {/* The games followed by themselves, until each ends (sports_follow/matches.py). */}
      {matches.length > 0 ? (
        <View style={{ gap: 10 }}>
          <SectionHead title="Per game" right={<Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>Until each one ends</Text>} />
          <Card style={s.list}>
            {matches.map((m, i) => (
              <View key={m.event_id} style={[{ gap: 10, paddingVertical: 14 }, i < matches.length - 1 && s.line]}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                  <Avatar name={m.title} sport={m.sport} size="sm" />
                  <View style={{ flex: 1 }}>
                    <Text onPress={openMatch(m.match)} accessibilityRole="link" style={t.title}>{m.title}</Text>
                    <Text style={[t.tiny, t.muted]}>{gameLevelText(m.alerts, sports.info(m.sport))}</Text>
                  </View>
                </View>
                <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={m.alerts} onChange={(l) => chooseGame(m.event_id, l)} />
                {gameFailed[m.event_id] ? <ErrorBox>{gameFailed[m.event_id]}</ErrorBox> : null}
              </View>
            ))}
          </Card>
        </View>
      ) : null}
    </>
  )
}

/** On or off for this phone, its quiet hours, its live cards and sport colours, and a test alert (the web's "On
 * this device"; live cards and colours are the app's alone). */
function Device() {
  const push = usePush()
  const [start, setStart] = useState(push.quiet.start ?? '22:00')
  const [end, setEnd] = useState(push.quiet.end ?? '07:00')
  useEffect(() => {
    if (push.quiet.start && push.quiet.end) {
      setStart(push.quiet.start)
      setEnd(push.quiet.end)
    }
  }, [push.quiet.start, push.quiet.end])
  const quietOn = Boolean(push.quiet.start && push.quiet.end)
  return (
    <Card style={{ paddingVertical: 4, paddingHorizontal: 18 }}>
      <View style={[s.setting, push.on && s.line]}>
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={t.title}>On this device</Text>
          <Text style={[t.small, t.muted]}>{push.unavailable ?? (push.on ? DEVICE.on : DEVICE.off)}</Text>
        </View>
        {push.unavailable ? null : <Switch label="Alerts on this device" on={push.on} disabled={push.busy} onChange={(on) => (on ? push.enable() : push.disable())} />}
      </View>
      {push.on ? (
        <>
          <View style={[s.setting, s.line, { flexWrap: 'wrap' }]}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text style={t.title}>Quiet hours</Text>
              <Text style={[t.small, t.muted]}>{DEVICE.quiet}</Text>
            </View>
            <Switch label="Quiet hours" on={quietOn} onChange={(next) => push.saveQuiet(next ? { start, end } : { start: null, end: null })} />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%' }}>
              <TimeField label="Quiet from" value={start} onChange={(time) => { setStart(time); if (quietOn) push.saveQuiet({ start: time, end }) }} />
              <Text style={[t.small, t.muted]}>to</Text>
              <TimeField label="Quiet until" value={end} onChange={(time) => { setEnd(time); if (quietOn) push.saveQuiet({ start, end: time }) }} />
            </View>
          </View>
          {push.live === null ? null : (
            <View style={[s.setting, s.line]}>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={t.title}>Live scores</Text>
                <Text style={[t.small, t.muted]}>A card on your lock screen for each game you follow while it’s on, kept up to date, without sound.</Text>
              </View>
              <Switch label="Live scores" on={push.live} onChange={push.saveLive} />
            </View>
          )}
          {push.colours === null ? null : (
            <View style={[s.setting, s.line]}>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={t.title}>Sport colours</Text>
                <Text style={[t.small, t.muted]}>Each sport’s alerts in its own colour. Off, they’re all in black: quieter beside other apps.</Text>
              </View>
              <Switch label="Sport colours" on={push.colours} onChange={push.saveColours} />
            </View>
          )}
          <Btn kind="link" label="Send a test alert" disabled={push.busy} onPress={push.test} style={{ height: 48 }} />
        </>
      ) : null}
      {push.message ? <Text style={[t.small, { fontFamily: font[700], paddingBottom: 14 }]}>{push.message}</Text> : null}
    </Card>
  )
}

const s = StyleSheet.create({
  setting: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 },
  list: { paddingVertical: 4, paddingHorizontal: 16 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingVertical: 14, minHeight: 44 },
  line: { borderBottomWidth: 1, borderBottomColor: colour.line },
  fresh: { width: 8, height: 8, marginTop: 8, borderRadius: 4, backgroundColor: colour.live },
  accountRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingVertical: 14, paddingHorizontal: 18 },
})

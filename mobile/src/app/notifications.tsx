// Alerts, the web's NotificationsPage.tsx: what's happened to the fan's players (Latest), and what reaches this
// phone and how much to hear about each player (Settings). Notifications on the phone itself come with push
// (BHA-230); the per-player levels already apply to the fan's other devices.

import { router, useLocalSearchParams } from 'expo-router'
import { useEffect, useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { LEVELS, levelText } from '../../../web/src/alerts'
import { dayLabel, relative } from '../../../web/src/format'
import { useAccount } from '../account'
import { useActivity } from '../activity'
import type { ActivityItem, AlertLevel } from '../api'
import { Avatar, Empty } from '../blocks'
import { useSports } from '../catalog'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { ChevronIcon, UserIcon } from '../icons'
import { Segmented } from '../results'
import { colour, dsp, font, t } from '../theme'
import { Btn, Card, ErrorBox, Eyebrow, SectionHead, Spinner } from '../ui'
import { Page } from '../page'

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
      </View>
    </Page>
  )
}

/** Every moment of the fan's players, newest first, by day; what came since they last looked is marked. */
function Latest({ onSettings }: { onSettings: () => void }) {
  const { cards } = useFollowing()
  const activity = useActivity()
  // What was new when the list opened stays marked while it's open; the badge clears at once.
  const [seenAtOpen] = useState(activity.seen)
  const { markSeen } = activity
  useEffect(() => { markSeen() }, [markSeen])
  const now = useNow()
  const sportOf = (id: number) => cards.find((c) => c.player_id === id)?.player.sport

  if (!activity.loaded) return <Empty><Spinner /></Empty>
  if (!activity.items.length) {
    return (
      <Card style={{ paddingVertical: 28, paddingHorizontal: 20, alignItems: 'center', gap: 4 }}>
        <Text style={[t.small, t.muted, { textAlign: 'center' }]}>{cards.length ? 'Nothing yet. Line-ups, goals, wickets and results for your players show up here as they happen.' : 'Follow a player to see their moments here.'}</Text>
        {cards.length > 0 ? <Btn kind="link" label="Choose what reaches this phone" onPress={onSettings} /> : null}
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
            {items.map((item, i) => <LatestRow key={item.id} item={item} sport={sportOf(item.player_id)} fresh={item.id > seenAtOpen} last={i === items.length - 1} />)}
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
      <Avatar name={item.player} sport={sport} size="sm" />
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

/** On this phone, and how much to hear about each player. */
function Settings() {
  const sports = useSports()
  const { cards, setAlerts } = useFollowing()
  const [failed, setFailed] = useState<Record<number, string>>({})
  const choose = (playerId: number, level: AlertLevel) => {
    setFailed((f) => ({ ...f, [playerId]: '' }))
    setAlerts(playerId, level).catch((e: Error) => setFailed((f) => ({ ...f, [playerId]: e.message })))
  }
  return (
    <>
      <AccountRow />
      <Card style={{ paddingVertical: 4, paddingHorizontal: 18 }}>
        <View style={{ paddingVertical: 14, gap: 4 }}>
          <Text style={t.title}>On this device</Text>
          <Text style={[t.small, t.muted]}>Notifications on this phone come with the next version of the app. What you choose below already applies on your other devices.</Text>
        </View>
      </Card>

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
    </>
  )
}

const s = StyleSheet.create({
  list: { paddingVertical: 4, paddingHorizontal: 16 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingVertical: 14, minHeight: 44 },
  line: { borderBottomWidth: 1, borderBottomColor: colour.line },
  fresh: { width: 8, height: 8, marginTop: 8, borderRadius: 4, backgroundColor: colour.live },
  accountRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingVertical: 14, paddingHorizontal: 18 },
})

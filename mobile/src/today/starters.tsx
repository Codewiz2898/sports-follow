// The players a new fan is offered in each sport (GET /api/starters), as the web's Starters.tsx: tiles in each
// sport's colour, followed or unfollowed with one tap, in the welcome and on Today while a fan follows few.

import { useEffect, useState, type ReactNode } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { followTarget } from '../../../web/src/follow'
import { starterReason } from '../../../web/src/today'
import { api, type SearchResult, type StarterSport } from '../api'
import { toneOf, useSports } from '../catalog'
import { useFollowing } from '../following'
import { CheckIcon, PlusIcon } from '../icons'
import { dsp, font, GUTTER, RADIUS_TILE } from '../theme'
import { Chip, ErrorBox, Section, Spinner } from '../ui'

// What this run of the app knows, shared by every picker: the last answer (shown at once by the next picker,
// while it asks again), the player each starter followed here became, and the sport last picked by chip.
let lastAnswer: StarterSport[] | null = null
const followedHere: Record<string, number> = {}
let lastChip = ''

/** The starters this server offers a new fan, by sport: null while loading. */
export function useStarters(): { sports: StarterSport[] | null; failed: boolean } {
  const [sports, setSports] = useState<StarterSport[] | null>(lastAnswer)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let current = true
    api.starters()
      .then((r) => { lastAnswer = r.sports; if (current) setSports(r.sports) })
      .catch(() => { if (current) setFailed(true) })
    return () => { current = false }
  }, [])
  return { sports, failed }
}

/**
 * Follow, or unfollow, a result with one tap: whether it's followed, whether a tap is on its way, and the last
 * error. What was followed here is kept for the whole run, by result key, so a starter tile and a live game's
 * pill for one player agree.
 */
export function useFollowToggle() {
  const following = useFollowing()
  const [, setVersion] = useState(0)
  const [pending, setPending] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const playerOf = (r: SearchResult) => followedHere[r.key] ?? r.player_id ?? null
  const followed = (r: SearchResult) => following.isFollowing(playerOf(r))

  const toggle = async (r: SearchResult) => {
    if (pending.includes(r.key)) return
    setError(null)
    setPending((p) => [...p, r.key])
    try {
      const id = playerOf(r)
      if (id != null && following.isFollowing(id)) await following.unfollow(id)
      else {
        const card = await following.follow(followTarget(r))
        followedHere[r.key] = card.player_id
        setVersion((v) => v + 1)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending((p) => p.filter((k) => k !== r.key))
    }
  }

  return { followed, toggle, busy: (r: SearchResult) => pending.includes(r.key), error }
}

/** A tile to tap rather than open (.tile.pick): a starter to follow, or a sport in the welcome; ringed once picked. */
export function PickTile({ sport, top, name, detail, on, busy, onPress, big }: { sport: string; top?: string; name: string; detail?: string; on: boolean; busy?: boolean; onPress: () => void; big?: boolean }) {
  const tone = toneOf(useSports(), sport)
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Follow ${name}`} accessibilityState={{ selected: on, busy }} onPress={onPress} style={[s.tile, { backgroundColor: tone.sport }, on && { borderWidth: 3, borderColor: tone.on }]}>
      <View style={s.corner}>{busy ? <Spinner color={tone.on} /> : on ? <CheckIcon size={20} color={tone.on} /> : <PlusIcon size={20} color={tone.on} />}</View>
      {top ? <Text style={[s.top, { color: tone.onSoft }]} numberOfLines={1}>{top}</Text> : null}
      <Text style={[dsp(big ? 30 : 21), { color: tone.on, paddingRight: 24 }]}>{name}</Text>
      {detail ? <Text style={[s.detail, { color: tone.on }]} numberOfLines={2}>{detail}</Text> : null}
    </Pressable>
  )
}

/** Tiles two across (.tiles). */
export function Tiles({ children }: { children: ReactNode[] }) {
  return <View style={s.tiles}>{children.map((c, i) => <View key={i} style={s.cell}>{c}</View>)}</View>
}

/**
 * The starter picker: a sport's starters as tiles in its colour, each followed, or unfollowed, with one tap.
 * In the welcome's second step (each sport the fan picked, under its name) and on Today while a fan follows
 * few players (one sport at a time, by chips).
 */
export function StarterPicker({ sports, chips = false }: { sports: StarterSport[]; chips?: boolean }) {
  const all = useSports()
  const [shown, setShown] = useState(sports.some((x) => x.key === lastChip) ? lastChip : sports[0]?.key ?? '')
  const { followed, toggle, busy, error } = useFollowToggle()
  const groups = chips ? sports.filter((x) => x.key === shown) : sports
  return (
    <View style={{ gap: 12 }}>
      {chips && sports.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -GUTTER }} contentContainerStyle={{ paddingHorizontal: GUTTER, gap: 8 }}>
          {sports.map((x) => {
            const tone = toneOf(all, x.key)
            return <Chip key={x.key} label={x.label} dot={tone.sport} on={tone.on} pressed={shown === x.key} onPress={() => { lastChip = x.key; setShown(x.key) }} />
          })}
        </ScrollView>
      ) : null}
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      {groups.map((x) => (
        <Section key={x.key} title={chips ? undefined : x.label}>
          <Tiles>
            {x.players.map((r) => <PickTile key={r.key} sport={x.key} top={starterReason(r)} name={r.name} detail={r.detail} on={followed(r)} busy={busy(r)} onPress={() => void toggle(r)} />)}
          </Tiles>
        </Section>
      ))}
    </View>
  )
}

/** Today's "Players to follow": the starter picker, a sport at a time, while a fan follows few players. */
export function StartersSection() {
  const { sports } = useStarters()
  if (!sports?.length) return null
  return (
    <Section title="Players to follow">
      <StarterPicker sports={sports} chips />
    </Section>
  )
}

const s = StyleSheet.create({
  tiles: { flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -4 },
  cell: { width: '50%', padding: 4 },
  tile: { flex: 1, justifyContent: 'space-between', gap: 8, minHeight: 104, padding: 14, borderRadius: RADIUS_TILE },
  corner: { position: 'absolute', top: 12, right: 12, zIndex: 1 },
  top: { fontFamily: font[800], fontSize: 10, letterSpacing: 0.9, textTransform: 'uppercase', paddingRight: 26 },
  detail: { fontFamily: font[800], fontSize: 12, lineHeight: 15.6 },
})


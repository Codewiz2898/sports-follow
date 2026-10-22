// The players a new fan is offered in each sport (GET /api/starters), as the web's Starters.tsx: tiles in each
// sport's colour, followed or unfollowed with one tap, in the welcome and on Today while a fan follows few, and of
// their sports they follow nobody in after (Your sports, BHA-653).

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { followTarget } from '../../../web/src/follow'
import { starterReason } from '../../../web/src/today'
import { followedSports, startersOffered } from '../../../web/src/yourSports'
import { api, type SearchResult, type StarterSport } from '../api'
import { toneOf, useSports } from '../catalog'
import { useFollowing } from '../following'
import { CheckIcon, PlusIcon } from '../icons'
import { colour, dsp, font, GUTTER, RADIUS_TILE } from '../theme'
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

/** A tile to tap rather than open (.tile.pick): a starter to follow, or a sport in the welcome; ringed once picked.
 * `big`, a name at 30px (.tile.pick.sport); `tall`, a campaign's player (.tile.pick.campaign-player). */
export function PickTile({ sport, top, name, detail, on, busy, onPress, big, tall }: { sport: string; top?: string; name: string; detail?: string; on: boolean; busy?: boolean; onPress: () => void; big?: boolean; tall?: boolean }) {
  const tone = toneOf(useSports(), sport)
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Follow ${name}`} accessibilityState={{ selected: on, busy }} onPress={onPress} style={[s.tile, tall && { minHeight: 148 }, { backgroundColor: tone.sport }, on && { borderWidth: 3, borderColor: tone.on }]}>
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
 * In the welcome's second step (each sport the fan picked, under its name) and on Today (one sport at a time, by
 * chips), with a line about the sport shown where `note` has one.
 */
export function StarterPicker({ sports, chips = false, note }: { sports: StarterSport[]; chips?: boolean; note?: (sport: StarterSport) => string | null }) {
  const all = useSports()
  const [shown, setShown] = useState(sports.some((x) => x.key === lastChip) ? lastChip : sports[0]?.key ?? '')
  const { followed, toggle, busy, error } = useFollowToggle()
  // The list can change under it (a game picked on Today, a third player followed): a chip no longer in it shows the first.
  const current = sports.some((x) => x.key === shown) ? shown : sports[0]?.key ?? ''
  const groups = chips ? sports.filter((x) => x.key === current) : sports
  return (
    <View style={{ gap: 12 }}>
      {chips && sports.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -GUTTER }} contentContainerStyle={{ paddingHorizontal: GUTTER, gap: 8 }}>
          {sports.map((x) => {
            const tone = toneOf(all, x.key)
            return <Chip key={x.key} label={x.label} dot={tone.sport} on={tone.on} pressed={current === x.key} onPress={() => { lastChip = x.key; setShown(x.key) }} />
          })}
        </ScrollView>
      ) : null}
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      {groups.map((x) => (
        <Section key={x.key} title={chips ? undefined : x.label}>
          {note?.(x) ? <Text style={s.note}>{note(x)}</Text> : null}
          <Tiles>
            {x.players.map((r) => <PickTile key={r.key} sport={x.key} top={starterReason(r)} name={r.name} detail={r.detail} on={followed(r)} busy={busy(r)} onPress={() => void toggle(r)} />)}
          </Tiles>
        </Section>
      ))}
    </View>
  )
}

/**
 * Today's "Players to follow" (the web's StartersSection): the starter picker, a sport at a time, the fan's sports
 * first. Every sport's while Today says they follow few players (`few`); after, those of their sports they follow
 * nobody in yet, and those of theirs it offered on this visit (yourSports.startersOffered). With a game picked on
 * Today (`game`), its own alone. Nothing when that leaves none.
 */
export function StartersSection({ few, game = null }: { few: boolean; game?: string | null }) {
  const { sports } = useStarters()
  const catalog = useSports()
  const { cards, teams, sports: yours } = useFollowing()
  const stays = useRef(new Set<string>()) // the fan's sports offered on this visit (Today keys it by visit and fan): following one from it keeps it
  if (!sports?.length) return null
  const followed = followedSports(cards, teams, (x) => catalog.info(x).key)
  const offered = startersOffered(sports, yours, followed, few, game, stays.current)
  for (const x of offered) if (yours.includes(x.key)) stays.current.add(x.key)
  if (!offered.length) return null
  const note = (x: StarterSport) => (yours.includes(x.key) && !followed.has(x.key) ? `In ${x.label}, one of your sports. You follow nobody in it yet.` : null)
  return (
    <Section title="Players to follow">
      <StarterPicker sports={offered} chips note={note} />
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
  note: { fontFamily: font[600], fontSize: 13, lineHeight: 18, color: colour.muted },
})


// Every game the sources have on, whoever follows it (GET /api/live), as the web's LiveNow.tsx: the welcome's
// "Live now" and an empty Today's, each game with a few of its players to follow with a tap, and Today's link
// to the Live page (app/live.tsx) for a fan with players.

import { router } from 'expo-router'
import { useEffect, useState } from 'react'
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native'
import { shortName } from '../../../web/src/format'
import { gamesOn, moreOn, type GameOn } from '../../../web/src/today'
import { api, type Card, type LiveGame, type LiveSport } from '../api'
import { OtherGameCard } from '../blocks'
import { toneOf, useSports } from '../catalog'
import { CheckIcon, ChevronIcon, PlusIcon } from '../icons'
import { Rail, type RailCard } from '../rail'
import { colour, dsp, font, RADIUS_TILE } from '../theme'
import { ErrorBox, Eyebrow, LiveBadge, Spinner } from '../ui'
import { useFollowToggle } from './starters'

const EVERY = 60_000 // the server reads a source again at most this often
const SOON = 5_000 // when it says a read is under way, the next look
const SHOWN = 6 // Also live's row: the most games before its "All N" card
const NONE: LiveSport[] = []

/** What the server last said is on: `loaded` once it has answered, `failed` while its last look failed. */
export interface LiveAnswer { sports: LiveSport[]; loaded: boolean; failed: boolean }

/** What the sources have on, looked at again once a minute while the app is in front (a look only reads
 * what the server last read: more fans looking costs no more of a source's allowance). */
export function useLiveAnswer(): LiveAnswer {
  const [answer, setAnswer] = useState<LiveAnswer>({ sports: NONE, loaded: false, failed: false })
  useEffect(() => {
    let current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let latest = 0 // the newest look: an older one's answer starts no second loop
    const look = (tries: number) => {
      clearTimeout(timer)
      if (AppState.currentState !== 'active') return // nobody's looking; coming back looks
      const mine = ++latest
      api.live()
        .then((r) => {
          if (!current || mine !== latest) return
          setAnswer({ sports: r.sports, loaded: true, failed: false })
          // A read was just asked for: look again in a moment, a few times, for the new scores.
          timer = setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => {
          if (!current || mine !== latest) return
          setAnswer((a) => (a.failed ? a : { ...a, failed: true }))
          timer = setTimeout(() => look(tries), EVERY)
        })
    }
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') look(0) })
    look(0)
    return () => { current = false; clearTimeout(timer); sub.remove() }
  }, [])
  return answer
}

/** The games on now (useLiveAnswer), none until the server answers. */
export function useLive(): LiveSport[] {
  return useLiveAnswer().sports
}

type Follow = ReturnType<typeof useFollowToggle>

/** A game's players to follow, each with a tap (.follow-pill): filled in their sport's colour once followed. */
function FollowPills({ game, sport, follow }: { game: LiveGame; sport: string; follow: Follow }) {
  const tone = toneOf(useSports(), sport)
  if (!game.players.length) return null
  return (
    <View style={s.who}>
      {game.players.map((r) => {
        const on = follow.followed(r)
        const busy = follow.busy(r)
        return (
          <Pressable key={r.key} accessibilityRole="button" accessibilityLabel={`Follow ${r.name}`} accessibilityState={{ selected: on, busy }} onPress={() => void follow.toggle(r)} style={[s.pill, on && { backgroundColor: tone.sport }]}>
            {busy ? <Spinner size={12} color={on ? tone.on : colour.text} /> : on ? <CheckIcon size={14} color={tone.on} /> : <PlusIcon size={14} />}
            <Text style={[s.pillText, on && { color: tone.on }]}>{shortName(r.name)}</Text>
          </Pressable>
        )
      })}
    </View>
  )
}

/** One game in the welcome's "Live now" (.live-game): outlined in its sport's colour. */
function LiveGameCard({ game, sport, follow, fill }: { game: LiveGame; sport: string; follow: Follow } & RailCard) {
  const tone = toneOf(useSports(), sport)
  return (
    <View style={[s.liveGame, { borderColor: tone.sport }, fill && { width: undefined }]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <LiveBadge />
        {game.live.clock ? <Eyebrow color={colour.text}>{game.live.clock}</Eyebrow> : null}
      </View>
      <Eyebrow lines={1}>{game.competition}</Eyebrow>
      <Text style={dsp(24)}>{game.live.score || game.title}</Text>
      <View style={{ marginTop: 'auto' }}><FollowPills game={game} sport={sport} follow={follow} /></View>
    </View>
  )
}

/** "Live now" in the welcome: every game on, the sports the fan picked (`first`) first. Nothing while nothing is on. */
export function LiveNow({ first = [] }: { first?: string[] }) {
  const sports = useLive()
  const follow = useFollowToggle()
  const ordered = [...sports].sort((a, b) => Number(!first.includes(a.key)) - Number(!first.includes(b.key)))
  const games = ordered.flatMap((x) => x.games.map((g) => ({ ...g, sport: x.key })))
  if (!games.length) return null
  return (
    <>
      <Rail title="Live now" note={`${games.length} on`}>
        {games.map((g) => <LiveGameCard key={g.key} game={g} sport={g.sport} follow={follow} />)}
      </Rail>
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
    </>
  )
}

/** One game on now that none of the fan's players are in (the web's OtherGame): outlined, with players to
 * follow from it. The Live page shows every game on as these, the fan's own under a heading of their own. */
export function OtherGame({ game, label, follow, fill }: GameOn & { follow: Follow } & RailCard) {
  return (
    <OtherGameCard live={game.live} sport={game.sport} seed={game.key} tag={game.competition || label} fill={fill}>
      <FollowPills game={game} sport={game.sport} follow={follow} />
    </OtherGameCard>
  )
}

/**
 * The games on anywhere that none of the fan's players are in (gamesOn), after the fan's own live boards
 * (`mine`, their live cards; the web's AlsoLive): titled "Also live" under the fan's own; with none of theirs
 * on, it's the whole of "Live now". Today shows it to a fan who follows nobody yet, to pick players from; a
 * fan with players gets MoreLive's link instead.
 */
export function AlsoLive({ mine }: { mine: Card[] }) {
  const sports = useLive()
  const follow = useFollowToggle()
  const games = gamesOn(sports, mine).others
  if (!games.length) return null
  return (
    <>
      <Rail title={mine.length ? 'Also live' : 'Live now'} note={`${games.length} on`} all="Every game on now" max={SHOWN}>
        {games.map(({ game, label }) => <OtherGame key={game.key} game={game} label={label} follow={follow} />)}
      </Rail>
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
    </>
  )
}

/**
 * Today's way to every other game on now, under the fan's own live boards (`mine`, their live cards; the
 * web's MoreLive): a quiet link to the Live page, "12 other games on now", or with none of theirs on, "12
 * games on now". A game of theirs isn't counted (gamesOn). Nothing when nothing else is on.
 */
export function MoreLive({ mine }: { mine: Card[] }) {
  const others = gamesOn(useLive(), mine).others.length
  if (!others) return null
  return (
    <Pressable onPress={() => router.push('/live')} accessibilityRole="link" hitSlop={8} style={s.more}>
      <Eyebrow>{moreOn(others, mine.length > 0)}</Eyebrow>
      <ChevronIcon size={16} strokeWidth={2.6} color={colour.muted} />
    </Pressable>
  )
}

const s = StyleSheet.create({
  who: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 30, paddingHorizontal: 10, borderRadius: 999, backgroundColor: colour.bg },
  pillText: { fontFamily: font[700], fontSize: 12, color: colour.text },
  more: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', minHeight: 36, marginVertical: -6 },
  liveGame: { gap: 8, width: 236, minHeight: 172, flexGrow: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: RADIUS_TILE, backgroundColor: colour.surface, borderWidth: 3 },
})

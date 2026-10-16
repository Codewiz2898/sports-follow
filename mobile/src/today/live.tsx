// Every game the sources have on, whoever follows it (GET /api/live), as the web's LiveNow.tsx: Today's "Also
// live" (or "Live now" when none of the fan's are on) and the welcome's "Live now", each game with a few of
// its players to follow with a tap.

import { useEffect, useState } from 'react'
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native'
import { shortName } from '../../../web/src/format'
import { api, type Card, type LiveGame, type LiveSport } from '../api'
import { OtherGameCard } from '../blocks'
import { toneOf, useSports } from '../catalog'
import { CheckIcon, PlusIcon } from '../icons'
import { Rail, type RailCard } from '../rail'
import { colour, dsp, font, RADIUS_TILE } from '../theme'
import { ErrorBox, Eyebrow, LiveBadge, Spinner } from '../ui'
import { useFollowToggle } from './starters'

const EVERY = 60_000 // the server reads a source again at most this often
const SOON = 5_000 // when it says a read is under way, the next look
const SHOWN = 6 // Also live's row: the most games before its "All N" card

/** What the sources have on, looked at again once a minute while the app is in front (a look only reads
 * what the server last read: more fans looking costs no more of a source's allowance). */
export function useLive(): LiveSport[] {
  const [sports, setSports] = useState<LiveSport[]>([])
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
          setSports(r.sports)
          // A read was just asked for: look again in a moment, a few times, for the new scores.
          timer = setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => { if (current && mine === latest) timer = setTimeout(() => look(tries), EVERY) })
    }
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active') look(0) })
    look(0)
    return () => { current = false; clearTimeout(timer); sub.remove() }
  }, [])
  return sports
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

/**
 * Today's tail of the live section, for every fan (the web's AlsoLive): the games on anywhere that none of the
 * fan's players are in, after the fan's own live boards (`mine`, their live cards). A game is the fan's when
 * one of their live cards names one of its events, or a player it offers is one of theirs who's live; never
 * by a name. Titled "Also live" under the fan's own; with none of theirs on, it's the whole of "Live now".
 */
export function AlsoLive({ mine }: { mine: Card[] }) {
  const sports = useLive()
  const follow = useFollowToggle()
  const events = new Set(mine.flatMap((c) => (c.live.event_id != null ? [c.live.event_id] : [])))
  const players = new Set(mine.map((c) => c.player_id))
  const theirs = (g: LiveGame) => (g.event_ids ?? []).some((id) => events.has(id)) || g.players.some((p) => p.player_id != null && players.has(p.player_id))
  const games = sports.flatMap((x) => x.games.filter((g) => !theirs(g)).map((g) => ({ game: { ...g, sport: x.key }, label: x.label })))
  if (!games.length) return null
  return (
    <>
      <Rail title={mine.length ? 'Also live' : 'Live now'} note={`${games.length} on`} all="Every game on now" max={SHOWN}>
        {games.map(({ game, label }) => (
          <OtherGameCard key={game.key} live={game.live} sport={game.sport} seed={game.key} tag={game.competition || label}>
            <FollowPills game={game} sport={game.sport} follow={follow} />
          </OtherGameCard>
        ))}
      </Rail>
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
    </>
  )
}

const s = StyleSheet.create({
  who: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 4, height: 30, paddingHorizontal: 10, borderRadius: 999, backgroundColor: colour.bg },
  pillText: { fontFamily: font[700], fontSize: 12, color: colour.text },
  liveGame: { gap: 8, width: 236, minHeight: 172, flexGrow: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: RADIUS_TILE, backgroundColor: colour.surface, borderWidth: 3 },
})

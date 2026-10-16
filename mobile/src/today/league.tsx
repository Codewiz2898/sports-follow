// The big results and big games coming up in the competitions the sources cover, whoever follows them (GET
// /api/league), as the web's components/League.tsx: outlined cards, so they don't pass for the fan's own, after
// the fan's in Today's rails, and alone for a fan with none.

import { useEffect, useRef, useState } from 'react'
import { AppState, StyleSheet, Text, View } from 'react-native'
import { dayLabel } from '../../../web/src/format'
import { wordSize } from '../../../web/src/lines'
import { leagueAbout, leagueComing } from '../../../web/src/today'
import { api, type League, type LeagueGame } from '../api'
import { Backdrop } from '../art'
import { toneOf, useSports } from '../catalog'
import type { RailCard } from '../rail'
import { colour, dsp, font, RADIUS_TILE } from '../theme'
import { breakable, Eyebrow, Pill } from '../ui'

const SOON = 5_000 // when the server says a read is under way, the next look
const EVERY = 15 * 60_000 // otherwise, while the app is in front: results come in through the day

/**
 * The league's big games, the same for every fan: looked at once, again in a moment while the server says a
 * source is being read, and every EVERY while the app is in front. Null until the first answer. When `theirs`
 * (the events the fan's players are in, as a key) changes, it looks again past any kept copy, so a league game
 * that's now theirs shows once, as theirs.
 */
export function useLeague(theirs: string | null = null): League | null {
  const [league, setLeague] = useState<League | null>(null)
  const again = useRef<((tries: number, fresh?: boolean) => void) | null>(null)
  useEffect(() => {
    let current = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let latest = 0 // the newest look: an older one's answer starts no second loop
    let at = 0
    const look = (tries: number, fresh = false) => {
      clearTimeout(timer)
      if (AppState.currentState !== 'active') return // nobody's looking; coming back looks, if it's been a while
      const mine = ++latest
      at = Date.now()
      api.league(fresh)
        .then((r) => {
          if (!current || mine !== latest) return
          setLeague(r)
          timer = setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => { if (current && mine === latest) timer = setTimeout(() => look(tries), EVERY) })
    }
    const sub = AppState.addEventListener('change', (state) => { if (state === 'active' && Date.now() - at > EVERY) look(0) })
    again.current = look
    look(0)
    return () => { current = false; again.current = null; clearTimeout(timer); sub.remove() }
  }, [])
  const known = useRef<string | null>(null)
  useEffect(() => {
    if (theirs == null) return
    if (known.current != null && known.current !== theirs) again.current?.(0, true)
    known.current = theirs
  }, [theirs])
  return league
}

/** A finished game none of the fan's players are in: outlined, its competition, when, its sport's big word for
 * it (the score, the margin, a lobby's winner), who played, and what made it big. Not a link. */
export function LeagueResultCard({ game, now, fill }: { game: LeagueGame; now: number } & RailCard) {
  const tone = toneOf(useSports(), game.sport)
  const word = game.word || game.title
  return (
    <View style={[s.card, s.result, fill && { width: undefined }]}>
      <Backdrop sport={tone.key} seed={game.key} colour={tone.sport} opacity={0.2} />
      <View style={s.head}>
        <Pill small label={game.competition} bg={tone.sport} color={tone.on} style={{ flexShrink: 1 }} />
        <Eyebrow>{dayLabel(game.start_utc, new Date(now))}</Eyebrow>
      </View>
      <Text style={dsp(wordSize(word))}>{breakable(word)}</Text>
      <View style={{ gap: 2 }}>
        {word !== game.title ? <Text style={[s.line, { fontFamily: font[800] }]} numberOfLines={2}>{game.line || game.title}</Text> : null}
        <Text style={[s.line, { color: colour.muted }]} numberOfLines={2}>{leagueAbout(game)}</Text>
      </View>
    </View>
  )
}

/** A big game to come, outlined: the countdown within a day, else the day, big; then the game. */
export function LeagueComingCard({ game, now, fill }: { game: LeagueGame; now: number } & RailCard) {
  const tone = toneOf(useSports(), game.sport)
  const { big, when } = leagueComing(game, now)
  return (
    <View style={[s.card, s.coming, fill && { width: undefined }]}>
      <Backdrop sport={tone.key} seed={game.key} colour={tone.sport} opacity={0.2} />
      <Pill small label={game.competition} bg={tone.sport} color={tone.on} />
      <Text style={dsp(44)} numberOfLines={1}>{big}</Text>
      <View style={{ gap: 2 }}>
        <Text style={[s.line, { fontFamily: font[800] }]} numberOfLines={2}>{game.title}</Text>
        <Text style={[s.line, { color: colour.muted }]} numberOfLines={2}>{leagueAbout(game, when)}</Text>
      </View>
    </View>
  )
}

const s = StyleSheet.create({
  card: { overflow: 'hidden', justifyContent: 'space-between', gap: 10, flexGrow: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: RADIUS_TILE, backgroundColor: colour.surface, borderWidth: 2, borderColor: colour.text },
  result: { width: 236, minHeight: 268 },
  coming: { width: 204, minHeight: 176, gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  line: { fontFamily: font[700], fontSize: 13, lineHeight: 17, color: colour.text },
})

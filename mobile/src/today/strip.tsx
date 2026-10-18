// The fan's teams and players along the top of Today (the web's PlayersStrip.tsx): one row of pills in each one's
// sport colour, teams first as the Following page has them, then "+ Follow". Its heading, all of it, opens the
// Following page.

import { router } from 'expo-router'
import { Pressable, StyleSheet, Text } from 'react-native'
import { shortName } from '../../../web/src/format'
import { liveShort, nextWhen, teamNextWhen, teamOrder, tileOrder } from '../../../web/src/today'
import type { Card, FollowedTeam } from '../api'
import { toneOf, useSports } from '../catalog'
import { ChevronIcon } from '../icons'
import { Rail } from '../rail'
import { colour, dsp, font } from '../theme'
import { LiveDot, Spinner } from '../ui'

/** One followed player: a pill of their sport's colour, their surname, and a pulsing dot with the score while
 * they play, else when they next do; a spinner while their page is being built. */
function PlayerPill({ card, now }: { card: Card; now: number }) {
  const tone = toneOf(useSports(), card.player.sport)
  const building = card.status === 'building'
  const live = !building && card.live.is_live
  const said = building ? '' : live ? liveShort(card.live) : nextWhen(card, now)
  return (
    <Pressable onPress={() => router.push(`/player/${card.player_id}`)} accessibilityLabel={`${card.player.name}${live ? ', live' : ''}`} style={[s.pill, { backgroundColor: tone.sport }]}>
      {live ? <LiveDot ring={tone.on} /> : null}
      <Text style={[dsp(19), { color: tone.on, lineHeight: 20 }]}>{shortName(card.player.name)}</Text>
      {said ? <Text style={[s.when, { color: tone.on }]} numberOfLines={1}>{said}</Text> : null}
      {building ? <Spinner size={14} color={tone.on} /> : null}
    </Pressable>
  )
}

/** One followed team, as a player's pill: its name in its sport's colour, the score while it plays (a pulsing
 * dot), else when it next does. */
function TeamPill({ team, now }: { team: FollowedTeam; now: number }) {
  const tone = toneOf(useSports(), team.sport)
  const live = Boolean(team.live?.is_live)
  const said = live && team.live ? liveShort(team.live) : teamNextWhen(team, now)
  return (
    <Pressable onPress={() => router.push(`/team/${team.team_id}`)} accessibilityLabel={`${team.name}${live ? ', live' : ''}`} style={[s.pill, { backgroundColor: tone.sport }]}>
      {live ? <LiveDot ring={tone.on} /> : null}
      <Text style={[dsp(19), { color: tone.on, lineHeight: 20 }]}>{team.name}</Text>
      {said ? <Text style={[s.when, { color: tone.on }]} numberOfLines={1}>{said}</Text> : null}
    </Pressable>
  )
}

export function PlayersStrip({ cards, teams = [], now }: { cards: Card[]; teams?: FollowedTeam[]; now: number }) {
  const title = teams.length ? 'Following' : 'Your players'
  const head = (
    <Pressable onPress={() => router.push('/following')} accessibilityRole="link" accessibilityLabel={`${title}, ${cards.length + teams.length}, see them all`} style={s.head}>
      <Text style={s.headText}>{title} · {cards.length + teams.length}</Text>
      <ChevronIcon size={18} strokeWidth={2.6} />
    </Pressable>
  )
  return (
    <Rail title={title} head={head} gap={8}>
      {[
        ...teamOrder(teams, now).map((x) => <TeamPill key={`team-${x.team_id}`} team={x} now={now} />),
        ...tileOrder(cards, now).map((c) => <PlayerPill key={c.player_id} card={c} now={now} />),
        <Pressable key="add" onPress={() => router.push('/search')} accessibilityLabel="Follow another player" style={[s.pill, s.add]}>
          <Text style={[dsp(19), { lineHeight: 20 }]}>+ Follow</Text>
        </Pressable>,
      ]}
    </Rail>
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 44, marginTop: -8, marginBottom: -10 },
  headText: { fontFamily: font[800], fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: colour.text },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 7, height: 40, paddingHorizontal: 14, borderRadius: 999 },
  when: { maxWidth: 140, fontFamily: font[800], fontSize: 12, letterSpacing: 0.5, textTransform: 'uppercase' },
  add: { backgroundColor: 'transparent', borderWidth: 2, borderColor: colour.text },
})

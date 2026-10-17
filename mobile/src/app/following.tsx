// Following, the web's FollowingPage.tsx: every team and player the fan follows as a grid of their sport's
// colours (Today's strip opens it), their teams first. Names only: photos are on a player's own page.

import { router } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { tileOrder } from '../../../web/src/today'
import { PlayerTile, TeamTile } from '../blocks'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { BackIcon, PlusIcon } from '../icons'
import { Tiles } from '../today/starters'
import { colour, dsp, RADIUS_TILE, t } from '../theme'
import { Eyebrow } from '../ui'
import { Page } from '../page'
import { LoadFailed } from '../today/load'

export default function FollowingScreen() {
  const { cards, loaded, failed, progress, teams } = useFollowing()
  const now = useNow()
  const live = cards.filter((c) => c.live.is_live).length + teams.filter((x) => x.live?.is_live).length
  const counted = [cards.length && `${cards.length} ${cards.length === 1 ? 'player' : 'players'}`, teams.length && `${teams.length} ${teams.length === 1 ? 'team' : 'teams'}`].filter(Boolean)
  return (
    <Page>
      <View style={{ gap: 6 }}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} style={s.back}>
          <BackIcon size={14} strokeWidth={2.6} color={colour.muted} />
          <Eyebrow>Today</Eyebrow>
        </Pressable>
        <Text style={dsp(54)}>Following</Text>
        {loaded || cards.length > 0 ? <Eyebrow>{counted.join(' · ') || '0 players'}{live ? ` · ${live} live` : ''}</Eyebrow> : null}
      </View>
      {failed && !loaded ? <LoadFailed /> : null}
      {loaded && cards.length === 0 && teams.length === 0 ? <Text style={[t.body, t.muted]}>You don't follow anyone yet. Find a player or a team to see their games here.</Text> : null}
      {loaded || cards.length > 0 ? (
        <Tiles>
          {[
            ...teams.map((x) => <TeamTile key={`team-${x.team_id}`} team={x} />),
            ...tileOrder(cards, now).map((c) => <PlayerTile key={c.player_id} card={c} progress={progress[c.player_id]} />),
            <Pressable key="add" onPress={() => router.push('/search')} accessibilityLabel="Follow another player or a team" style={s.add}>
              <PlusIcon size={22} />
              <Text style={dsp(26)}>Follow</Text>
            </Pressable>,
          ]}
        </Tiles>
      ) : null}
    </Page>
  )
}

const s = StyleSheet.create({
  back: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', minHeight: 32 },
  add: { flex: 1, minHeight: 116, alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: RADIUS_TILE, borderWidth: 2, borderColor: colour.text, padding: 14 },
})


// Live now, the web's LivePage.tsx: the games on now (GET /api/live) that none of the fan's players are in.
// Today, which shows the fan's own, links here. By sport, the fan's sports first (yourSports.ts), each in the order
// the server ranks them, as Today's outlined cards with players to follow from each; one game's alone with its `game`
// param (Today's link, with a game picked there). Looked at again once a minute while the app is in front, as Today
// does (useLiveAnswer).

import { router, useLocalSearchParams } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { bySport, gamesOn, liveGames, type GameOn } from '../../../web/src/today'
import { inYourOrder } from '../../../web/src/yourSports'
import { Empty } from '../blocks'
import { useSports } from '../catalog'
import { useFollowing } from '../following'
import { BackIcon } from '../icons'
import { Page } from '../page'
import { colour, dsp, font, t } from '../theme'
import { OtherGame, useLiveAnswer } from '../today/live'
import { useFollowToggle } from '../today/starters'
import { Btn, ErrorBox, Eyebrow, Notice, Section, Spinner } from '../ui'

/** One heading's games, one under another, each as wide as the page. */
function Games({ title, games, follow }: { title: string; games: GameOn[]; follow: ReturnType<typeof useFollowToggle> }) {
  return (
    <Section title={title} right={<Eyebrow>{games.length} on</Eyebrow>}>
      {games.map(({ game, label }) => <OtherGame key={game.key} game={game} label={label} follow={follow} fill />)}
    </Section>
  )
}

export default function LiveScreen() {
  const { sports, loaded, failed } = useLiveAnswer()
  const following = useFollowing()
  const follow = useFollowToggle()
  const catalog = useSports()
  const { game } = useLocalSearchParams<{ game?: string }>()
  // Once the follow list has answered too, so a game of the fan's never shows here first.
  const ready = loaded && (following.loaded || following.failed)
  const { theirs, others: every } = gamesOn(sports, liveGames(following.cards).flatMap(([, group]) => group))
  const others = game ? every.filter((x) => catalog.info(x.game.sport).key === game) : every
  const label = game ? catalog.info(game).label : ''
  return (
    <Page>
      <View style={{ gap: 6 }}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} style={s.back}>
          <BackIcon size={14} strokeWidth={2.6} color={colour.muted} />
          <Eyebrow>Today</Eyebrow>
        </Pressable>
        <Text style={dsp(54)}>Live now</Text>
        {ready && others.length > 0 ? <Eyebrow>{game ? `${label} · ` : ''}{others.length} on</Eyebrow> : null}
      </View>
      {!ready ? (failed ? (
        <Notice><Text style={s.notice}>Couldn't load the games on now. Trying again in a minute.</Text></Notice>
      ) : <Empty><Spinner /> Loading…</Empty>) : null}
      {ready && others.length === 0 ? <Text style={[t.body, t.muted]}>{game ? `No other ${label} games on right now.` : theirs.length ? 'Nothing else is on right now.' : "Nothing's on right now."}</Text> : null}
      {ready ? inYourOrder(bySport(others), (g) => g.sport, following.sports).map((g) => <Games key={g.sport} title={g.label} games={g.games} follow={follow} />) : null}
      {ready && game && every.length > others.length ? <Btn small label="Every game on now" onPress={() => router.setParams({ game: undefined })} style={{ alignSelf: 'flex-start' }} /> : null}
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
    </Page>
  )
}

const s = StyleSheet.create({
  back: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', minHeight: 32 },
  notice: { fontFamily: font[600], fontSize: 13, flex: 1, color: colour.text },
})

// Live now, the web's LivePage.tsx: every game on now, whoever follows it (GET /api/live). Today's link to the
// games none of the fan's players are in opens it. The fan's own come first, under "Your players", then each
// sport's in the order the server ranks them, as Today's outlined cards with players to follow from each.
// Looked at again once a minute while the app is in front, as Today does (useLiveAnswer).

import { router } from 'expo-router'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { bySport, gamesOn, liveGames, type GameOn } from '../../../web/src/today'
import { Empty } from '../blocks'
import { useFollowing } from '../following'
import { BackIcon } from '../icons'
import { Page } from '../page'
import { colour, dsp, font, t } from '../theme'
import { OtherGame, useLiveAnswer } from '../today/live'
import { useFollowToggle } from '../today/starters'
import { ErrorBox, Eyebrow, Notice, Section, Spinner } from '../ui'

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
  // Once the follow list has answered too, so none of the fan's games shows under its sport first.
  const ready = loaded && (following.loaded || following.failed)
  const { theirs, others } = gamesOn(sports, liveGames(following.cards).flatMap(([, group]) => group))
  const count = theirs.length + others.length
  return (
    <Page>
      <View style={{ gap: 6 }}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} style={s.back}>
          <BackIcon size={14} strokeWidth={2.6} color={colour.muted} />
          <Eyebrow>Today</Eyebrow>
        </Pressable>
        <Text style={dsp(54)}>Live now</Text>
        {ready && count > 0 ? <Eyebrow>{count} on{theirs.length ? ` · ${theirs.length} yours` : ''}</Eyebrow> : null}
      </View>
      {!ready ? (failed ? (
        <Notice><Text style={s.notice}>Couldn't load the games on now. Trying again in a minute.</Text></Notice>
      ) : <Empty><Spinner /> Loading…</Empty>) : null}
      {ready && count === 0 ? <Text style={[t.body, t.muted]}>Nothing's on right now.</Text> : null}
      {ready && theirs.length > 0 ? <Games title="Your players" games={theirs} follow={follow} /> : null}
      {ready ? bySport(others).map((g) => <Games key={g.sport} title={g.label} games={g.games} follow={follow} />) : null}
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
    </Page>
  )
}

const s = StyleSheet.create({
  back: { flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start', minHeight: 32 },
  notice: { fontFamily: font[600], fontSize: 13, flex: 1, color: colour.text },
})

// The welcome, the web's WelcomePage.tsx, for a fan who follows nobody yet, in two steps: the sports they watch (every
// sport that's on, GET /api/sports), kept as their sports when they tap Next (Your sports, BHA-653: Following shows
// and changes them), then a few players from each to follow with a tap (the starters), or for a sport with none, its
// games on now and a way to find its players. Then Today, which keeps offering starters while they follow few
// players, and of their sports they follow nobody in after.

import { router } from 'expo-router'
import { useEffect, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { inYourOrder, toggled, toSave } from '../../../web/src/yourSports'
import type { LiveGame } from '../api'
import { useSports, useSportsKnown } from '../catalog'
import { useFollowing } from '../following'
import { CheckIcon, SearchIcon } from '../icons'
import { colour, dsp, font, t } from '../theme'
import { LiveNow, OtherGame, useLive } from '../today/live'
import { StarterPicker, useFollowToggle, useStarters } from '../today/starters'
import { Btn, ErrorBox, Eyebrow, Notice, Section, Spinner } from '../ui'
import { Page } from '../page'
import { markWelcomed } from '../welcome'
import { SportTiles } from '../yourSports'

export default function WelcomeScreen() {
  const sports = useSports()
  const { known, askAgain } = useSportsKnown()
  const tiles = sports.running()
  const { cards, sports: saved, saveSports } = useFollowing()
  const insets = useSafeAreaInsets()
  const [step, setStep] = useState(1)
  // Null until the fan taps a tile: their sports saved already (another device's, signed in) show ticked.
  const [tapped, setTapped] = useState<string[] | null>(null)
  const [unsaved, setUnsaved] = useState(false)
  const picked = tapped ?? saved.filter((k) => tiles.some((x) => x.key === k))

  const offering = tiles.length > 0
  useEffect(() => {
    if (offering) markWelcomed() // shown once: leaving doesn't bring a fan back here
  }, [offering])
  useEffect(() => {
    if (!offering && known === 'known') { markWelcomed(); router.replace('/') } // no sport on to pick: Today's own start
  }, [offering, known])

  const finish = () => {
    markWelcomed()
    router.replace('/')
  }

  if (!offering) {
    if (known !== 'failed') return <Page><Spinner /></Page>
    // Not marked unless skipped: the welcome comes back once the server answers.
    return (
      <Page>
        <ErrorBox>Couldn't load the sports. Try again in a moment, or find a player yourself.</ErrorBox>
        <View style={s.row}>
          <Btn kind="link" label="Skip" onPress={finish} />
          <Btn label="Try again" onPress={askAgain} />
          <Btn kind="primary" label="Find a player" onPress={() => { markWelcomed(); router.replace('/search') }} />
        </View>
      </Page>
    )
  }
  const next = () => {
    setStep(2)
    if (tapped) saveSports(toSave(tapped, saved, tiles)).then(() => setUnsaved(false), () => setUnsaved(true))
  }

  return (
    <View style={{ flex: 1, backgroundColor: colour.bg }}>
      <Page>
        <View style={{ gap: 6 }}>
          <Eyebrow>Welcome · {step} of 2</Eyebrow>
          <Text style={dsp(54)}>{step === 1 ? 'What do you watch?' : 'Pick your players'}</Text>
        </View>
        <Text style={[t.body, t.muted, { fontFamily: font[600], maxWidth: 360 }]}>
          {step === 1 ? 'Pick your sports. Next, a few players from each.' : 'Tap to follow: their live scores, results and alerts come to you.'}
        </Text>
        {step === 1 ? (
          <>
            <SportTiles tiles={tiles} picked={picked} onPick={(key) => setTapped(toggled(picked, key))} />
            <Notice>
              <CheckIcon size={18} />
              <Text style={s.kept}>Kept as your sports. Change them any time in Following.</Text>
            </Notice>
          </>
        ) : (
          <>
            {unsaved ? <ErrorBox>Couldn't keep your sports just now. Pick them again in Following.</ErrorBox> : null}
            <LiveNow first={picked} />
            <WelcomePlayers picked={picked} />
          </>
        )}
      </Page>
      <View style={[s.foot, { paddingBottom: 10 + insets.bottom }]}>
        {step === 1 ? <Btn kind="link" label="Skip" onPress={finish} /> : <Btn kind="link" label="Back" onPress={() => setStep(1)} />}
        {step === 1
          ? <Btn kind="primary" label={picked.length ? `Next · ${picked.length} ${picked.length === 1 ? 'sport' : 'sports'}` : 'Next'} onPress={next} />
          : <Btn kind="primary" label={cards.length ? `Done · ${cards.length} followed` : 'Done'} onPress={finish} />}
      </View>
    </View>
  )
}

/** The welcome's second step: the starters of each sport picked, in the fan's order (every sport's, none picked),
 * then each sport picked that has none (an esports game, often): its games on now, and a way to find its players. */
function WelcomePlayers({ picked }: { picked: string[] }) {
  const { sports: starters, failed } = useStarters()
  const live = useLive() // one look for every sport picked below
  if (starters === null && failed) return <ErrorBox>Couldn't load players to suggest. Try again in a moment, or find one yourself.</ErrorBox>
  if (starters === null) return <Spinner />
  const theirs = picked.length ? inYourOrder(starters.filter((x) => picked.includes(x.key)), (x) => x.key, picked) : starters
  const without = picked.filter((k) => !starters.some((x) => x.key === k))
  return (
    <>
      {theirs.length ? <StarterPicker sports={theirs} /> : null}
      {without.map((key) => <PickedOnNow key={key} sport={key} games={live.find((x) => x.key === key)?.games ?? []} />)}
      {!theirs.length && !without.length ? (
        <Btn kind="primary" label="Find a player" icon={<SearchIcon size={18} color={colour.bg} />} onPress={() => router.push('/search')} style={{ alignSelf: 'flex-start' }} />
      ) : null}
    </>
  )
}

/** A sport the fan picked that has no starters: its games on now (`games`, GET /api/live's), each with players to follow
 * from it, and a search for its players. */
function PickedOnNow({ sport, games }: { sport: string; games: LiveGame[] }) {
  const info = useSports().info(sport)
  const follow = useFollowToggle()
  return (
    <Section title={info.label} right={games.length ? <Eyebrow>On now</Eyebrow> : undefined}>
      {games.map((g) => <OtherGame key={g.key} game={{ ...g, sport }} label={info.label} follow={follow} fill />)}
      {follow.error ? <ErrorBox>{follow.error}</ErrorBox> : null}
      <Btn label={`Find a ${info.label} player`} icon={<SearchIcon size={18} />} onPress={() => router.push({ pathname: '/search', params: { sport } })} style={{ alignSelf: 'flex-start' }} />
    </Section>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' },
  kept: { flex: 1, fontFamily: font[700], fontSize: 13, lineHeight: 17.5, color: colour.text },
  foot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingTop: 10, paddingHorizontal: 16, backgroundColor: colour.bg },
})

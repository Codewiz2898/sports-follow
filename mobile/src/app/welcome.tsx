// The welcome, the web's WelcomePage.tsx, for a fan who follows nobody yet, in two steps: the sports they
// watch, then a few players from each to follow with a tap (the starters). Then Today, which keeps offering
// starters while they follow few players.

import { router } from 'expo-router'
import { useEffect, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useFollowing } from '../following'
import { colour, dsp, font, t } from '../theme'
import { LiveNow } from '../today/live'
import { PickTile, StarterPicker, Tiles, useStarters } from '../today/starters'
import { Btn, ErrorBox, Eyebrow, Spinner } from '../ui'
import { Page } from '../page'
import { markWelcomed } from '../welcome'

export default function WelcomeScreen() {
  const { sports, failed } = useStarters()
  const { cards } = useFollowing()
  const insets = useSafeAreaInsets()
  const [step, setStep] = useState(1)
  const [picked, setPicked] = useState<string[]>([])

  const offering = !!sports?.length
  useEffect(() => {
    if (offering) markWelcomed() // shown once: leaving doesn't bring a fan back here
  }, [offering])
  useEffect(() => {
    if (sports && sports.length === 0) { markWelcomed(); router.replace('/') } // nothing to offer: Today's own start
  }, [sports])

  const finish = () => {
    markWelcomed()
    router.replace('/')
  }

  if (sports === null && failed) {
    // Not marked unless skipped: the welcome comes back once the server answers.
    return (
      <Page>
        <ErrorBox>Couldn't load players to suggest. Try again in a moment, or find one yourself.</ErrorBox>
        <View style={s.row}>
          <Btn kind="link" label="Skip" onPress={finish} />
          <Btn kind="primary" label="Find a player" onPress={() => { markWelcomed(); router.replace('/search') }} />
        </View>
      </Page>
    )
  }
  if (!sports?.length) return <Page><Spinner /></Page>
  const pick = (key: string) => setPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))
  const offered = picked.length ? sports.filter((x) => picked.includes(x.key)) : sports // none picked: every sport

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
          <Tiles>
            {sports.map((x) => <PickTile key={x.key} sport={x.key} name={x.label} on={picked.includes(x.key)} onPress={() => pick(x.key)} big />)}
          </Tiles>
        ) : (
          <>
            <LiveNow first={picked} />
            <StarterPicker sports={offered} />
          </>
        )}
      </Page>
      <View style={[s.foot, { paddingBottom: 10 + insets.bottom }]}>
        {step === 1 ? <Btn kind="link" label="Skip" onPress={finish} /> : <Btn kind="link" label="Back" onPress={() => setStep(1)} />}
        {step === 1
          ? <Btn kind="primary" label="Next" onPress={() => setStep(2)} />
          : <Btn kind="primary" label={cards.length ? `Done · ${cards.length} followed` : 'Done'} onPress={finish} />}
      </View>
    </View>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  foot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingTop: 10, paddingHorizontal: 16, backgroundColor: colour.bg },
})

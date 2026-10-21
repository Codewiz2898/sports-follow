// SPIKE (recorder, BHA-314): the test page for instant replay. Start the buffer, go to the game, save with the
// notification's "Save last 30s" or the floating button, then share from here. Never merged.

import { useCallback, useEffect, useState } from 'react'
import { AppState, PermissionsAndroid, Pressable, StyleSheet, Text, View } from 'react-native'
import Recorder, { type Audio, type Clip, type State } from '../../modules/slam-recorder'
import { Page } from '../page'
import { colour, dsp, font, t } from '../theme'
import { Btn, Card, Eyebrow, Section, Switch } from '../ui'

const QUALITY = {
  '720p30': { height: 720, fps: 30, mbps: 5 },
  '1080p30': { height: 1080, fps: 30, mbps: 8 },
  '1080p60': { height: 1080, fps: 60, mbps: 12 },
} as const
type Quality = keyof typeof QUALITY

function Choice<T extends string>({ value, options, onChange }: { value: T; options: readonly T[]; onChange: (v: T) => void }) {
  return (
    <View style={s.choice}>
      {options.map((o) => (
        <Pressable key={o} onPress={() => onChange(o)} style={[s.opt, o === value && s.optOn]} accessibilityRole="radio" accessibilityState={{ checked: o === value }}>
          <Text style={[s.optText, o === value && { color: colour.bg }]}>{o}</Text>
        </Pressable>
      ))}
    </View>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={s.row}>
      <Text style={[t.title, { flex: 1 }]}>{label}</Text>
      {children}
    </View>
  )
}

export default function RecorderPage() {
  const [state, setState] = useState<State>(() => Recorder?.status() ?? { running: false })
  const [audio, setAudio] = useState<Audio>('game')
  const [quality, setQuality] = useState<Quality>('720p30')
  const [consent, setConsent] = useState<'choice' | 'display'>('choice')
  const [bubble, setBubble] = useState(true)
  const [secure, setSecure] = useState(false)
  const [cardNow, setCardNow] = useState(true)
  const [overlay, setOverlay] = useState(() => Recorder?.canOverlay() ?? false)
  const [clips, setClips] = useState<Clip[]>([])
  const [note, setNote] = useState('')

  const refresh = useCallback(() => {
    if (!Recorder) return
    setState(Recorder.status())
    setOverlay(Recorder.canOverlay())
    try { setClips((JSON.parse(Recorder.clips()) as Clip[]).reverse()) } catch { setClips([]) }
  }, [])

  useEffect(() => {
    refresh()
    const sub = Recorder?.addListener('onState', (s) => { setState(s); refresh() })
    const app = AppState.addEventListener('change', (a) => { if (a === 'active') refresh() })
    const timer = setInterval(refresh, 2000)
    return () => { sub?.remove(); app.remove(); clearInterval(timer) }
  }, [refresh])

  if (!Recorder) return <Page><Text style={t.body}>This build has no recorder.</Text></Page>

  async function start() {
    setNote('')
    await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS)
    if (audio !== 'none') {
      const mic = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO)
      if (mic !== 'granted') setNote('No microphone permission: the clip will have no sound.')
    }
    const ok = await Recorder!.start({ audio, seconds: 30, ...QUALITY[quality], cardNow, bubble: bubble && overlay, secure, consent })
    if (!ok) setNote('Android said no to recording the screen.')
    refresh()
  }

  const up = state.running
  return (
    <Page>
      <View style={{ gap: 6 }}>
        <Eyebrow>Spike · not in the app</Eyebrow>
        <Text style={dsp(44)}>Instant replay</Text>
        <Text style={[t.body, t.muted]}>Start it here, go to your game, and when something happens tap Save on the notification or the floating button. Slam keeps the last 30 seconds.</Text>
      </View>

      <Card pad style={{ gap: 12, backgroundColor: up ? colour.text : colour.surface }}>
        <Text style={[dsp(28), up && { color: colour.bg }]}>{up ? `Recording · ${Math.floor(state.seconds ?? 0)}s held` : 'Off'}</Text>
        {up ? (
          <Text style={[t.small, { color: colour.inkMuted }]}>
            {state.size} · {state.fps} fps · {state.mbps} Mbps · sound: {state.audio} · {state.saves ?? 0} saved{state.saving ? ' · saving…' : ''}
            {state.visible === false ? ' · capture hidden' : ''}
          </Text>
        ) : null}
        {state.error ? <Text style={[t.small, { color: colour.live }]}>{state.error}</Text> : null}
        {state.audioError ? <Text style={[t.small, { color: colour.live }]}>{state.audioError}</Text> : null}
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {up ? (
            <>
              <Btn kind="primary" label="Save last 30s" colours={{ bg: colour.bg, fg: colour.text }} busy={state.saving} onPress={() => Recorder!.save()} />
              <Btn label="Stop" colours={{ fg: colour.bg, border: colour.bg }} onPress={() => Recorder!.stop()} />
              <Btn label={state.bubble ? 'Hide button' : 'Floating button'} colours={{ fg: colour.bg, border: colour.bg }} onPress={() => Recorder!.setBubble(!state.bubble, secure)} />
            </>
          ) : (
            <Btn kind="primary" label="Start replay" onPress={start} />
          )}
        </View>
        {note ? <Text style={[t.small, { color: up ? colour.bg : colour.text }]}>{note}</Text> : null}
      </Card>

      {!up ? (
        <Section title="Settings">
          <Card pad style={{ gap: 14 }}>
            <Row label="Sound"><Choice value={audio} options={['game', 'mic', 'both', 'none'] as const} onChange={setAudio} /></Row>
            <Row label="Quality"><Choice value={quality} options={Object.keys(QUALITY) as Quality[]} onChange={setQuality} /></Row>
            <Row label="Consent"><Choice value={consent} options={['choice', 'display'] as const} onChange={setConsent} /></Row>
            <Row label="End card when saved"><Switch on={cardNow} onChange={setCardNow} label="End card when saved" /></Row>
            <Row label="Floating button"><Switch on={bubble} onChange={setBubble} label="Floating button" /></Row>
            <Row label="Hide button from clip"><Switch on={secure} onChange={setSecure} label="Hide button from clip" /></Row>
            {!overlay ? (
              <View style={{ gap: 6 }}>
                <Text style={t.small}>The floating button needs “Display over other apps”.</Text>
                <Btn small label="Allow it in Settings" onPress={() => Recorder!.openOverlaySettings()} />
              </View>
            ) : null}
          </Card>
        </Section>
      ) : null}

      <Section title={`Clips (${clips.length})`}>
        {clips.length === 0 ? <Text style={[t.body, t.muted]}>None yet.</Text> : null}
        {clips.map((c, i) => (
          <Card pad key={`${c.savedAt}-${i}`} style={{ gap: 8 }}>
            {c.error ? <Text style={[t.small, { color: colour.live }]}>Failed: {c.error}</Text> : (
              <>
                <Text style={t.title}>{c.seconds?.toFixed(1)} s · {c.size} · {c.mb?.toFixed(1)} MB · sound {c.audio}</Text>
                <Text style={[t.small, t.muted]}>
                  {c.savedAt ? new Date(c.savedAt).toLocaleTimeString() : ''} · written in {c.muxMs} ms · end card {c.endCard ? `${((c.cardMs ?? 0) / 1000).toFixed(1)} s` : 'none'}{c.cardError ? ` (failed: ${c.cardError})` : ''}
                </Text>
                {c.uri ? (
                  <View style={{ flexDirection: 'row', gap: 8 }}>
                    <Btn small kind="primary" label="Share" onPress={() => Recorder!.share(c.uri!)} />
                    <Btn small label="Play" onPress={() => Recorder!.open(c.uri!)} />
                  </View>
                ) : null}
              </>
            )}
          </Card>
        ))}
      </Section>
      {state.stats ? <Text style={[t.tiny, t.muted]}>Stats: {state.stats}</Text> : null}
    </Page>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  choice: { flexDirection: 'row', gap: 6, flexWrap: 'wrap' },
  opt: { paddingHorizontal: 10, height: 32, borderRadius: 999, borderWidth: 2, borderColor: colour.text, justifyContent: 'center' },
  optOn: { backgroundColor: colour.text },
  optText: { fontFamily: font[800], fontSize: 12, color: colour.text },
})

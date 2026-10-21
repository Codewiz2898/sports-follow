// Watch live on a match page, the web's components/Watch.tsx: where to watch a game while it's on or about to be
// (sports_follow/watch.py; web/src/match.ts's watchable says when). Its first stream, the organiser's main one where
// there is one, big in the sport's colour, and its others as rows. Where the app has a player for the first's
// platform (its `embed`: a Twitch or Kick channel, a YouTube video), a tap plays it over the whole screen: a YouTube
// one upright, with Expand to turn it sideways (BHA-358), a Twitch or Kick one turned sideways, as their players won't
// play narrower than a phone held upright is; "Open on Twitch" is under it. Any other opens outside the app, in its
// platform's app or the browser, as the rows do. The player is the site's page of it alone (/watch/<player>/<id>) in a WebView: Twitch's must sit
// in a page on the site it's told, which an app isn't. Which stream plays is the page's (`playing`, its address),
// so a player started stays while the page is open, though the game is called over before its broadcast has shown
// the end.

import * as ScreenOrientation from 'expo-screen-orientation'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { ActivityIndicator, AppState, Linking, Modal, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { WebView } from 'react-native-webview'
import { watchPage } from '../../web/src/endpoints'
import { safeHref } from '../../web/src/format'
import { streamNote } from '../../web/src/match'
import { API_URL, type MatchStream } from './api'
import type { Tone } from './catalog'
import { CloseIcon, CollapseIcon, ExpandIcon, OutIcon, PlayIcon } from './icons'
import { colour, dsp, RADIUS_TILE, t } from './theme'
import { Btn, ListCard, SectionHead } from './ui'

const open = (url: string) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

export function Watch({ streams, tone, playing, onPlay }: {
  streams: MatchStream[]; tone: Tone; playing: string | null; onPlay: (url: string | null) => void
}) {
  // The stream playing stays first while it's listed, though another comes to the top of the list meanwhile; one no
  // longer listed stops.
  const first = streams.find((x) => x.url === playing) ?? streams[0]
  const gone = playing != null && first?.url !== playing
  useEffect(() => { if (gone) onPlay(null) }, [gone, onPlay])
  if (!first) return null
  const more = streams.filter((x) => x !== first)
  const note = streamNote(first)
  const page = first.embed ? API_URL + watchPage(first.embed) : null
  const out = open(first.url)
  return (
    <View style={s.section}>
      <SectionHead title="Watch live" />
      {page ? (
        <>
          <Pressable onPress={() => onPlay(first.url)} accessibilityRole="button" accessibilityLabel={`Play on ${first.platform}${note ? `, ${note}` : ''}`}
            style={({ pressed }) => [s.poster, { backgroundColor: tone.sport }, pressed && { opacity: 0.85 }]}>
            <View style={[s.play, { backgroundColor: tone.on }]}><PlayIcon size={26} color={tone.sport} /></View>
            <View style={{ gap: 4 }}>
              <Text style={[dsp(34), { color: tone.on }]}>Watch on {first.platform}</Text>
              {note ? <Text style={[t.tag, { color: tone.onSoft }]}>{note}</Text> : null}
            </View>
          </Pressable>
          {out ? <Btn kind="link" label={`Open on ${first.platform}`} onPress={out} style={{ alignSelf: 'flex-start' }} /> : null}
          {playing === first.url ? (
            <WholeScreen page={page} platform={first.platform} shape={first.embed?.player === 'youtube' ? 'wide' : 'sideways'} onOut={out} onClose={() => onPlay(null)} />
          ) : null}
        </>
      ) : (
        <Pressable onPress={out} accessibilityRole="link" accessibilityLabel={`Watch on ${first.platform}${note ? `, ${note}` : ''}`}
          style={({ pressed }) => [s.big, { backgroundColor: tone.sport }, pressed && { opacity: 0.85 }]}>
          <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
            <Text style={[dsp(34), { color: tone.on }]}>Watch on {first.platform}</Text>
            {note ? <Text style={[t.tag, { color: tone.onSoft }]}>{note}</Text> : null}
          </View>
          <OutIcon size={26} color={tone.on} />
        </Pressable>
      )}
      {more.length > 0 ? (
        <ListCard>
          {more.map((x) => (
            <Pressable key={x.url} onPress={open(x.url)} accessibilityRole="link" accessibilityLabel={`Watch on ${x.platform}${streamNote(x) ? `, ${streamNote(x)}` : ''}`} style={s.row}>
              <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <Text style={t.title}>{x.platform}</Text>
                {streamNote(x) ? <Text style={[t.small, t.muted]}>{streamNote(x)}</Text> : null}
              </View>
              <OutIcon size={20} color={colour.muted} />
            </Pressable>
          ))}
        </ListCard>
      ) : null}
    </View>
  )
}

// Turning the screen, one step at a time, so a player closed and opened again quickly never leaves the app sideways.
let turning: Promise<unknown> = Promise.resolve()
const inTurn = (step: () => Promise<unknown>) => { turning = turning.then(step).catch(() => {}) }

/** The screen sideways (either way round) while `sideways`, upright while not, and once the player goes, back as the
 * app had it before it opened (upright: app.config.ts's orientation). Never turned by the phone alone: only by the
 * player's own Expand and Collapse (BHA-358: nothing turns on its own). */
function useTurned(sideways: boolean) {
  const before = useRef<number | null>(null)
  useEffect(() => {
    inTurn(async () => {
      if (before.current == null) before.current = (await ScreenOrientation.getPlatformOrientationLockAsync()).screenOrientationConstantAndroid ?? null
      await ScreenOrientation.lockAsync(sideways ? ScreenOrientation.OrientationLock.LANDSCAPE : ScreenOrientation.OrientationLock.PORTRAIT_UP)
    })
  }, [sideways])
  useEffect(() => () => inTurn(() => before.current != null
    ? ScreenOrientation.lockPlatformAsync({ screenOrientationConstantAndroid: before.current })
    : ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP)), [])
}

/** Whether the app is in front: a player behind it would play on, as a WebView isn't paused with the app. The Feed's
 * inline player stops by it too (feedLayouts.tsx). */
export function useInFront() {
  const [front, setFront] = useState(AppState.currentState !== 'background')
  useEffect(() => {
    const seen = AppState.addEventListener('change', (state) => setFront(state !== 'background'))
    return () => seen.remove()
  }, [])
  return front
}

/** How the player opens (BHA-358). `sideways`: turned, and kept so, for a player that won't play narrower than a
 * phone held upright is (Twitch's 400×300). `wide`: upright, the video across the screen in the middle, with Expand
 * to turn it sideways and Collapse (or Back) to turn it upright again. */
export type PlayerShape = 'sideways' | 'wide'

/**
 * The player over the whole screen, never under anything of the app's: × (and Expand or Collapse) sit in a bar
 * above it upright, and in a strip beside it sideways, as on the web (.watch-whole). Sideways it's as tall as the
 * screen, so it's never smaller than its platform allows. × closes it, and Back too (or turns an expanded one upright
 * again). Only its own page loads here: a link out of the player (its "Watch on Twitch") opens in the platform's app or
 * the browser. While the app is behind another, the player goes, and comes back (live again) when it's in front. A
 * page that doesn't load, or that the server refuses, says so, with the way to the platform. (The Feed's videos play in
 * their cards instead: feedLayouts.tsx.)
 */
export function WholeScreen({ page, platform, shape = 'sideways', onOut, onClose }: {
  page: string; platform: string; shape?: PlayerShape; onOut?: () => void; onClose: () => void
}) {
  const [turned, setTurned] = useState(shape === 'sideways')
  useTurned(turned)
  const front = useInFront()
  const insets = useSafeAreaInsets()
  const [refused, setRefused] = useState(false)
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose })
  const leave = (url: string) => {
    const href = safeHref(url)
    if (href) Linking.openURL(href).catch(() => {})
  }
  const failed = () => (
    <View style={s.cover}>
      <Text style={[t.body, { color: '#FFFFFF' }]}>Couldn't load the player.</Text>
      {onOut ? <Btn kind="link" label={`Open on ${platform}`} colours={{ fg: '#FFFFFF' }} onPress={onOut} /> : null}
    </View>
  )
  const button = (label: string, icon: ReactElement, onPress: () => void) => (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={label} hitSlop={8} style={({ pressed }) => [s.close, pressed && { opacity: 0.8 }]}>
      {icon}
    </Pressable>
  )
  const closer = button('Close the player', <CloseIcon size={20} color="#0D0D0D" />, onClose)
  const turner = shape !== 'wide' ? null : turned
    ? button('Collapse: turn the video upright', <CollapseIcon size={20} color="#0D0D0D" />, () => setTurned(false))
    : button('Expand: turn the video sideways', <ExpandIcon size={20} color="#0D0D0D" />, () => setTurned(true))
  // One tree whichever way it's turned, the player always at the same place in it, so turning never loads it anew.
  return (
    <Modal visible animationType="fade" statusBarTranslucent navigationBarTranslucent supportedOrientations={['portrait', 'landscape']}
      onRequestClose={() => (turned && shape === 'wide' ? setTurned(false) : onClose())}>
      <View style={turned ? [s.whole, { paddingTop: insets.top, paddingBottom: insets.bottom, paddingLeft: insets.left }]
        : [s.upright, { paddingTop: insets.top, paddingBottom: insets.bottom }]} accessibilityViewIsModal>
        {turned ? null : <View style={s.bar}>{closer}{turner}</View>}
        <View style={turned ? s.web : s.stage}>
          <View style={turned ? s.web : s.wide}>
            {refused ? <View style={s.web}>{failed()}</View> : front ? (
              <WebView
                source={{ uri: page }}
                style={s.web}
                containerStyle={s.web}
                originWhitelist={['*']} // every navigation comes to onShouldStartLoadWithRequest, which keeps only the page
                onShouldStartLoadWithRequest={(r) => {
                  if (r.url === page) return true
                  leave(r.url)
                  return false
                }}
                onOpenWindow={(e) => leave(e.nativeEvent.targetUrl)}
                mediaPlaybackRequiresUserAction={false} // it plays at once: the tap that opened it asked for that
                // No full screen of the player's own: Expand is the way to a bigger one, and the WebView's own would turn it upright.
                allowsInlineMediaPlayback
                setBuiltInZoomControls={false}
                overScrollMode="never"
                startInLoadingState
                renderLoading={() => <View style={s.cover}><ActivityIndicator color="#FFFFFF" /></View>}
                renderError={failed}
                onHttpError={(e) => { if (e.nativeEvent.url === page) setRefused(true) }}
                onRenderProcessGone={() => { close.current() }} // its process was ended (memory): closed rather than blank
              />
            ) : <View style={s.web} />}
          </View>
        </View>
        {turned ? <View style={[s.side, { paddingRight: 8 + insets.right }]}>{closer}{turner}</View> : null}
        {!turned && onOut ? <Btn kind="link" label={`Open on ${platform}`} colours={{ fg: '#FFFFFF' }} onPress={onOut} style={s.out} /> : null}
      </View>
    </Modal>
  )
}

const s = StyleSheet.create({
  section: { gap: 10 },
  poster: { aspectRatio: 16 / 9, justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_TILE },
  play: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center' },
  big: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 96, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_TILE },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  whole: { flex: 1, flexDirection: 'row', backgroundColor: '#000000' },  // sideways: the player, then the strip beside it
  upright: { flex: 1, backgroundColor: '#000000' },  // upright: the bar, then the player, then the way to its platform
  web: { flex: 1, backgroundColor: '#000000' },
  stage: { flex: 1, justifyContent: 'center', backgroundColor: '#000000' },  // a wide video across the middle
  wide: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#000000' },
  cover: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', gap: 12, backgroundColor: '#000000' },
  side: { gap: 12, paddingTop: 8, paddingLeft: 8 },  // 52 wide, as the web's, and the screen's edge beyond
  bar: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 12, paddingVertical: 8 },
  out: { alignSelf: 'center', marginBottom: 8 },
  close: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: '#FFFFFF' },
})

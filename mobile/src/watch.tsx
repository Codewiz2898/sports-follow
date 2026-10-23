// Watch live on a match page, the web's components/Watch.tsx: where to watch a game while it's on or about to be
// (sports_follow/watch.py; web/src/match.ts's watchable says when). Its first stream, the organiser's main one where
// there is one, big in the sport's colour, and its others as rows. Where the app has a player for the first's
// platform (its `embed`: a Twitch or Kick channel, a YouTube video), a tap plays it there in the page, at the
// content's width, 16:9, as on the web, with "Open on Twitch" under it and Expand beside that: Expand turns it
// sideways over the whole screen, and Collapse (or Back) puts it back in the page. On a phone the page's player is
// smaller than Twitch's stated minimum for an embedded one (400×300): Bharath chose on 21 Oct to have it play in the
// page anyway, as on the web. Till it's played, it shows the platform's picture of it once that loads (`embed.poster`:
// a live Twitch channel's latest frame, a YouTube video's thumbnail), darkened under the words, as on the web, and the
// sport's colour without one. Any other opens outside the app, in its platform's app or the browser, as the rows do.
// The player is the site's page of it alone (/watch/<player>/<id>) in a WebView: Twitch's must sit in a page on the
// site it's told, which an app isn't. Which stream plays is the page's (`playing`, its address), so a player started
// stays while the page is open, though the game is called over before its broadcast has shown the end.

import { LinearGradient } from 'expo-linear-gradient'
import * as ScreenOrientation from 'expo-screen-orientation'
import { useIsFocused } from 'expo-router'
import { useEffect, useState } from 'react'
import { ActivityIndicator, AppState, Image, Linking, Modal, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { WebView } from 'react-native-webview'
import { watchPage, watchPoster } from '../../web/src/endpoints'
import { safeHref } from '../../web/src/format'
import { streamNote } from '../../web/src/match'
import { API_URL, type MatchStream } from './api'
import type { Tone } from './catalog'
import { CollapseIcon, ExpandIcon, OutIcon, PlayIcon } from './icons'
import { colour, dsp, RADIUS_TILE, t } from './theme'
import { Btn, ListCard, SectionHead } from './ui'

const open = (url: string) => {
  const href = safeHref(url)
  return href ? () => { Linking.openURL(href).catch(() => {}) } : undefined
}

export function Watch({ streams, tone, playing, onPlay }: {
  streams: MatchStream[]; tone: Tone; playing: string | null; onPlay: (url: string | null) => void
}) {
  // Over the whole screen (Expand) while it plays; a player stopped, however, starts in the page the next time.
  const [expanded, setExpanded] = useState(false)
  // The poster's picture that has loaded, as of when the page opened (watchPoster says why): the sport's colour till
  // it does, and for good where it doesn't.
  const [opened] = useState(Date.now)
  const [shown, setShown] = useState<string | null>(null)
  // The play button in the middle and the words at the foot, both by the player's width, as on the web, so a
  // phone's (some 190 tall) keeps them apart.
  const [wide, setWide] = useState(0)
  const button = Math.min(64, Math.max(44, wide * 0.14))
  const words = Math.min(40, Math.max(18, wide * 0.065))
  // The stream playing stays first while it's listed, though another comes to the top of the list meanwhile; one no
  // longer listed stops.
  const first = streams.find((x) => x.url === playing) ?? streams[0]
  const gone = playing != null && first?.url !== playing
  useEffect(() => { if (gone) { setExpanded(false); onPlay(null) } }, [gone, onPlay])
  if (!first) return null
  const more = streams.filter((x) => x !== first)
  const note = streamNote(first)
  const page = first.embed ? API_URL + watchPage(first.embed) : null
  const poster = first.embed?.poster ? API_URL + watchPoster(first.embed.poster, opened) : null
  const pictured = poster != null && shown === poster
  const out = open(first.url)
  const on = playing === first.url
  const stop = () => { setExpanded(false); onPlay(null) }
  return (
    <View style={s.section}>
      <SectionHead title="Watch live" />
      {page ? (
        <>
          <View style={[s.frame, { backgroundColor: tone.sport }]} onLayout={(e) => setWide(e.nativeEvent.layout.width)}>
            {/* Over the whole screen, it isn't here as well: one player at a time. */}
            {on ? expanded ? <View style={s.web} /> : <Player page={page} platform={first.platform} onOut={out} onGone={stop} /> : (
              <Pressable onPress={() => { setExpanded(false); onPlay(first.url) }} accessibilityRole="button" accessibilityLabel={`Play on ${first.platform}${note ? `, ${note}` : ''}`}
                style={({ pressed }) => [s.poster, pressed && { opacity: 0.85 }]}>
                {poster ? (
                  <Image source={{ uri: poster }} resizeMode="cover" onLoad={() => setShown(poster)} onError={() => setShown(null)}
                    style={[StyleSheet.absoluteFill, !pictured && { opacity: 0 }]} accessibilityIgnoresInvertColors />
                ) : null}
                {pictured ? <LinearGradient colors={['rgba(0,0,0,0.05)', 'rgba(0,0,0,0.2)', 'rgba(0,0,0,0.78)']} locations={[0, 0.45, 1]} style={StyleSheet.absoluteFill} /> : null}
                <View style={s.middle} pointerEvents="none">
                  <View style={[s.play, { width: button, height: button, borderRadius: button / 2, backgroundColor: tone.on }, pictured && s.lifted]}>
                    <PlayIcon size={Math.round(button * 0.4)} color={tone.sport} />
                  </View>
                </View>
                <View style={{ gap: 4 }}>
                  <Text style={[dsp(words), { color: pictured ? '#FFFFFF' : tone.on }]}>Watch on {first.platform}</Text>
                  {note ? <Text style={[t.tag, { color: pictured ? 'rgba(255,255,255,0.85)' : tone.onSoft }]}>{note}</Text> : null}
                </View>
              </Pressable>
            )}
          </View>
          <View style={s.under}>
            {out ? <Btn kind="link" label={`Open on ${first.platform}`} onPress={out} /> : <View />}
            {on ? <Btn small label="Expand" accessibilityLabel="Expand: turn the video sideways" icon={<ExpandIcon size={16} color={colour.text} />} onPress={() => setExpanded(true)} /> : null}
          </View>
          {on && expanded ? <WholeScreen page={page} platform={first.platform} onOut={out} onCollapse={() => setExpanded(false)} onGone={stop} /> : null}
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

/**
 * The stream's player, filling where it's put: the site's page of it alone in a WebView, playing at once (the tap
 * that started it asked for that). Only its own page loads here: a link out of the player (its "Watch on Twitch")
 * opens in the platform's app or the browser. No full screen of the player's own: Expand is the way to a bigger one,
 * and the WebView's own would turn the screen upright. While the app is behind another, or the match page is under
 * another of the app's, the player goes, and comes back (live again) when it's in front. A page that doesn't load,
 * or that the server refuses, says so, with Try again and the way to the platform; one whose WebView was ended
 * (memory) stops (`onGone`), the poster back in its place, rather than staying blank.
 */
function Player({ page, platform, onOut, onGone }: { page: string; platform: string; onOut?: () => void; onGone: () => void }) {
  const front = useInFront()
  const focused = useIsFocused()
  const [refused, setRefused] = useState(false)
  const [tries, setTries] = useState(0) // a WebView made anew for each, as one that failed keeps saying so
  const leave = (url: string) => { open(url)?.() }
  const failed = () => (
    <View style={s.cover}>
      <Text style={[t.body, { color: '#FFFFFF' }]}>Couldn't load the player.</Text>
      <View style={s.ways}>
        <Btn kind="link" label="Try again" colours={{ fg: '#FFFFFF' }} onPress={() => { setRefused(false); setTries((n) => n + 1) }} />
        {onOut ? <Btn kind="link" label={`Open on ${platform}`} colours={{ fg: '#FFFFFF' }} onPress={onOut} /> : null}
      </View>
    </View>
  )
  if (refused) return <View style={s.web}>{failed()}</View>
  if (!front || !focused) return <View style={s.web} />
  return (
    <WebView
      key={tries}
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
      mediaPlaybackRequiresUserAction={false}
      allowsInlineMediaPlayback
      setBuiltInZoomControls={false}
      overScrollMode="never"
      startInLoadingState
      renderLoading={() => <View style={s.cover}><ActivityIndicator color="#FFFFFF" /></View>}
      renderError={failed}
      onHttpError={(e) => { if (e.nativeEvent.url === page) setRefused(true) }}
      onRenderProcessGone={onGone}
    />
  )
}

// Turning the screen, one step at a time, so a player expanded and collapsed again quickly never leaves the app sideways.
let turning: Promise<unknown> = Promise.resolve()
const inTurn = (step: () => Promise<unknown>) => { turning = turning.then(step).catch(() => {}) }

/** The screen sideways (either way round) while it's drawn, and once it goes, back as the app had it before
 * (upright: app.config.ts's orientation). Never turned by the phone alone: only by Expand and Collapse (BHA-358:
 * nothing turns on its own). */
function useSideways() {
  useEffect(() => {
    let before: number | null = null
    inTurn(async () => {
      before = (await ScreenOrientation.getPlatformOrientationLockAsync()).screenOrientationConstantAndroid ?? null
      await ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE)
    })
    return () => inTurn(() => before != null
      ? ScreenOrientation.lockPlatformAsync({ screenOrientationConstantAndroid: before })
      : ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP))
  }, [])
}

/**
 * The player over the whole screen, turned sideways (Expand), never under anything of the app's: Collapse sits in
 * a strip beside it. As tall as the screen, so it's as big as the phone can show it. Collapse, or Back, puts it back
 * in the page, upright. It's a player of its own, so it loads the stream anew, and so does the page's on Collapse:
 * a WebView can't move from the page to over it without being made again.
 */
function WholeScreen({ page, platform, onOut, onCollapse, onGone }: {
  page: string; platform: string; onOut?: () => void; onCollapse: () => void; onGone: () => void
}) {
  useSideways()
  const insets = useSafeAreaInsets()
  return (
    <Modal visible animationType="fade" statusBarTranslucent navigationBarTranslucent supportedOrientations={['portrait', 'landscape']} onRequestClose={onCollapse}>
      <View style={[s.whole, { paddingTop: insets.top, paddingBottom: insets.bottom, paddingLeft: insets.left }]} accessibilityViewIsModal>
        <View style={s.web}><Player page={page} platform={platform} onOut={onOut} onGone={onGone} /></View>
        <View style={[s.side, { paddingRight: 8 + insets.right }]}>
          <Pressable onPress={onCollapse} accessibilityRole="button" accessibilityLabel="Collapse: back into the page" hitSlop={8} style={({ pressed }) => [s.collapse, pressed && { opacity: 0.8 }]}>
            <CollapseIcon size={20} color="#0D0D0D" />
          </Pressable>
        </View>
      </View>
    </Modal>
  )
}

const s = StyleSheet.create({
  section: { gap: 10 },
  frame: { aspectRatio: 16 / 9, borderRadius: RADIUS_TILE, overflow: 'hidden' },  // the poster, then the player, in the page
  poster: { flex: 1, justifyContent: 'flex-end', alignItems: 'flex-start', paddingVertical: 16, paddingHorizontal: 18 },  // the words at the foot
  middle: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center' },  // the play button in the middle
  play: { alignItems: 'center', justifyContent: 'center' },  // as big as the player's width says (Watch)
  lifted: { elevation: 8, shadowColor: '#000000', shadowOpacity: 0.35, shadowRadius: 12, shadowOffset: { width: 0, height: 8 } },  // the play button over the picture
  under: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },  // "Open on Twitch", Expand
  big: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 96, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_TILE },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  whole: { flex: 1, flexDirection: 'row', backgroundColor: '#000000' },  // sideways: the player, then the strip beside it
  web: { flex: 1, backgroundColor: '#000000' },
  cover: { ...StyleSheet.absoluteFill, alignItems: 'center', justifyContent: 'center', gap: 12, backgroundColor: '#000000' },
  ways: { flexDirection: 'row', gap: 20 },  // Try again, "Open on Twitch"
  side: { gap: 12, paddingTop: 8, paddingLeft: 8 },  // 52 wide, and the screen's edge beyond
  collapse: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: '#FFFFFF' },
})

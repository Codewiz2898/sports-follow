// The web app's building blocks (web/src/styles.css, "building blocks"), one for one: buttons, pills, the
// live badge, cards and lists, section heads, and the page itself.

import { router, type ErrorBoundaryProps } from 'expo-router'
import { Component, useEffect, useState, type ReactNode } from 'react'
import { ActivityIndicator, Animated, Pressable, StyleSheet, Text, TextInput, View, type StyleProp, type TextStyle, type ViewStyle } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { BackIcon } from './icons'
import { colour, dsp, dspInk, font, GUTTER, RADIUS_CARD, t } from './theme'

/** A big word that may break onto two lines after its hyphen ("TRIPLE- / DOUBLE"), as a browser breaks it:
 * Android's line breaker doesn't take a hyphen as a place to break, so it gets an explicit one. */
export function breakable(word: string): string {
  return word.replace(/-(?=\S)/g, '-\u200B')
}

/** A colour with some of it see-through: "#0d0d0d" at 0.22. */
export function alpha(hex: string, a: number): string {
  if (!hex.startsWith('#') || hex.length !== 7) return hex
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
  return `rgba(${r}, ${g}, ${b}, ${a})`
}

// ---------------------------------------------------------------- live

let pulse: Animated.Value | null = null

/** One pulse for every live dot on screen, as the web's @keyframes pulse: full, then 0.3, every 1.4 s. */
function usePulse(): Animated.Value {
  if (!pulse) {
    pulse = new Animated.Value(1)
    Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 0.3, duration: 700, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 700, useNativeDriver: true }),
    ])).start()
  }
  return pulse
}

/** "● LIVE" on red (.badge-live). */
export function LiveBadge({ label = 'Live' }: { label?: string }) {
  const opacity = usePulse()
  return (
    <View style={s.badgeLive}>
      <Animated.View style={[s.badgeDot, { opacity }]} />
      <Text style={[t.tag, { color: '#FFFFFF' }]}>{label}</Text>
    </View>
  )
}

/** A pulsing red dot (.live-dot); `ring`, a ring of a block's ink round it, so it reads on any sport colour. */
export function LiveDot({ ring }: { ring?: string }) {
  const opacity = usePulse()
  return <Animated.View style={[s.liveDot, ring ? { borderWidth: 2, borderColor: ring, width: 12, height: 12, borderRadius: 6 } : null, { opacity }]} />
}

// ---------------------------------------------------------------- type

export function Dsp({ size, color, style, lines, children }: { size: number; color?: string; style?: StyleProp<TextStyle>; lines?: number; children: ReactNode }) {
  return <Text style={[dsp(size), color ? { color } : null, style, dspInk(size, style)]} numberOfLines={lines}>{children}</Text>
}

export function Eyebrow({ children, color, style, lines }: { children: ReactNode; color?: string; style?: StyleProp<TextStyle>; lines?: number }) {
  return <Text style={[t.eyebrow, color ? { color } : null, style]} numberOfLines={lines}>{children}</Text>
}

/** A small pill of a block's colour (.pill, .sport-tag, .player-chip). */
export function Pill({ label, bg, color, outline, small, style }: { label: string; bg?: string; color: string; outline?: boolean; small?: boolean; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[small ? s.chipSmall : s.pill, { backgroundColor: bg ?? 'transparent' }, outline ? { borderWidth: 2, borderColor: color } : null, style]}>
      <Text style={[t.tag, { color, fontSize: small ? 10 : 11 }]} numberOfLines={1}>{label}</Text>
    </View>
  )
}

// ---------------------------------------------------------------- buttons

export interface BtnColours { fg?: string; bg?: string; border?: string }

/**
 * The web's .btn: a 44px pill with an ink ring; `primary`, filled ink; `icon`, a round ink button; `small`,
 * 36px; `link`, underlined words. `colours` repaint it for a sport block (the player band).
 */
export function Btn({ label, icon, onPress, kind = 'outline', small, busy, disabled, colours, style, accessibilityLabel }: {
  label?: string; icon?: ReactNode; onPress?: () => void; kind?: 'outline' | 'primary' | 'icon' | 'link' | 'danger'; small?: boolean; busy?: boolean; disabled?: boolean
  colours?: BtnColours; style?: StyleProp<ViewStyle>; accessibilityLabel?: string
}) {
  const filled = kind === 'primary' || kind === 'icon'
  const fg = colours?.fg ?? (kind === 'danger' ? colour.danger : filled ? colour.bg : colour.text)
  const bg = colours?.bg ?? (filled ? colour.text : 'transparent')
  const border = colours?.border ?? (kind === 'danger' ? colour.danger : colour.text)
  const box: ViewStyle = kind === 'link'
    ? { minHeight: 24 }
    : kind === 'icon'
      ? { width: 44, height: 44, borderRadius: 22, backgroundColor: bg }
      : { height: small ? 36 : 44, paddingHorizontal: small ? 14 : 18, borderRadius: 999, borderWidth: 2, borderColor: kind === 'primary' ? bg : border, backgroundColor: bg }
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: disabled || busy, busy }}
      disabled={disabled || busy}
      onPress={onPress}
      style={({ pressed }) => [s.btn, box, (disabled || busy) && { opacity: 0.55 }, pressed && { opacity: 0.8 }, style]}
    >
      {busy ? <ActivityIndicator size="small" color={fg} /> : icon}
      {label && kind !== 'icon' ? (
        <Text style={[{ fontFamily: font[800], fontSize: kind === 'link' ? 13 : small ? 13 : 14, color: fg }, kind === 'link' && { textDecorationLine: 'underline' }]}>{label}</Text>
      ) : null}
    </Pressable>
  )
}

/** An on/off switch (.switch): a raised track with a grey knob, ink with a paper knob when on. */
export function Switch({ on, onChange, disabled, label }: { on: boolean; onChange: (on: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={label}
      accessibilityState={{ checked: on, disabled }}
      disabled={disabled}
      hitSlop={6}
      onPress={() => onChange(!on)}
      style={[s.switch, { backgroundColor: on ? colour.text : colour.raised }, disabled && { opacity: 0.5 }]}
    >
      <View style={[s.knob, { left: on ? 24 : 4, backgroundColor: on ? colour.bg : colour.muted }]} />
    </Pressable>
  )
}

const HH_MM = /^([01]\d|2[0-3]):[0-5]\d$/

/** A time of day, "22:00" (.time, the web's <input type="time">): told when a whole new time is typed, and
 * back to the last good one otherwise. */
export function TimeField({ value, onChange, label }: { value: string; onChange: (time: string) => void; label: string }) {
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  const done = () => {
    const time = text.trim().replace(/^(\d):/, '0$1:')
    if (HH_MM.test(time)) {
      setText(time)
      if (time !== value) onChange(time)
    } else setText(value)
  }
  return (
    <TextInput
      accessibilityLabel={label}
      value={text}
      onChangeText={setText}
      onEndEditing={done}
      maxLength={5}
      keyboardType="numbers-and-punctuation"
      selectTextOnFocus
      style={s.time}
    />
  )
}

// ---------------------------------------------------------------- surfaces

export function Card({ children, pad, style }: { children: ReactNode; pad?: boolean; style?: StyleProp<ViewStyle> }) {
  return <View style={[s.card, pad && s.cardPad, style]}>{children}</View>
}

/** A card of rows with a line between them (.card.list). */
export function ListCard({ children, style }: { children: ReactNode[]; style?: StyleProp<ViewStyle> }) {
  const rows = children.filter(Boolean)
  return (
    <View style={[s.card, s.list, style]}>
      {rows.map((row, i) => <View key={i} style={[s.listRow, i < rows.length - 1 && s.listRowLine]}>{row}</View>)}
    </View>
  )
}

/** The section heading every page uses (.section-head): a leaning title and, on the right, a note or a link. A long
 * title wraps rather than push the note off the screen ("Big games coming up" beside "Next 14 days · 3 · yours first"),
 * as the web's does. */
export function SectionHead({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <View style={s.sectionHead}>
      <View style={{ flexShrink: 1 }}><Dsp size={28}>{title}</Dsp></View>
      {right}
    </View>
  )
}

export function Section({ title, right, children, style }: { title?: string; right?: ReactNode; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[s.section, style]}>
      {title ? <SectionHead title={title} right={right} /> : null}
      {children}
    </View>
  )
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <View style={s.errorBox}><Text style={s.errorText}>{children}</Text></View>
}

/** A notice above a page (.notice); `ink`, the one asking for something (.notice.action). */
export function Notice({ children, ink }: { children: ReactNode; ink?: boolean }) {
  return <View style={[s.notice, ink && { backgroundColor: colour.text }]}>{children}</View>
}

export function Spinner({ color = colour.text, size = 16 }: { color?: string; size?: number }) {
  return <ActivityIndicator size={size > 20 ? 'large' : 'small'} color={color} style={{ width: size, height: size, transform: [{ scale: size / 20 }] }} />
}

/** Won, drew or lost, told apart by fill, not by red and green (.wl); `on`, in a sport block's own ink. */
export function WL({ mark, on, block }: { mark: 'W' | 'D' | 'L'; on?: string; block?: string }) {
  const ink = on ?? colour.text
  const paper = block ?? colour.bg
  const box: ViewStyle = mark === 'W' ? { backgroundColor: ink } : mark === 'D' ? { backgroundColor: on ? alpha(ink, 0.22) : colour.raised } : { borderWidth: 2, borderColor: ink }
  return (
    <View style={[s.wl, box]} accessible accessibilityLabel={{ W: 'Won', D: 'Drew', L: 'Lost' }[mark]}>
      <Text style={{ fontFamily: font[800], fontSize: 13, color: mark === 'W' ? paper : ink }}>{mark}</Text>
    </View>
  )
}

/** A sport filter (.chip): a dot of its colour; pressed, the whole chip is. `bare`, no dot (All, in ink). */
export function Chip({ label, dot, pressed, on, onPress, bare }: { label: string; dot: string; pressed: boolean; on: string; onPress: () => void; bare?: boolean }) {
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: pressed }} style={[s.chip, pressed && { backgroundColor: dot }]}>
      {bare ? null : <View style={[s.chipDot, { backgroundColor: pressed ? on : dot }]} />}
      <Text style={{ fontFamily: font[700], fontSize: 14, color: pressed ? on : colour.text }}>{label}</Text>
    </Pressable>
  )
}

// ---------------------------------------------------------------- the page

interface BoundaryProps { children: ReactNode; fallback?: ReactNode; resetKey?: unknown }

/** Keeps one bad render (a board or a match page's part in a shape the app doesn't expect) from taking the
 * whole screen with it, as the web's ErrorBoundary: what's inside shows its fallback instead. A new `resetKey`
 * (a newer read) tries again. */
export class ErrorBoundary extends Component<BoundaryProps, { failed: boolean; key: unknown }> {
  state = { failed: false, key: this.props.resetKey }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  static getDerivedStateFromProps(props: BoundaryProps, state: { failed: boolean; key: unknown }) {
    return props.resetKey !== state.key ? { failed: false, key: props.resetKey } : null
  }

  render() {
    return this.state.failed ? this.props.fallback ?? <BlockError /> : this.props.children
  }
}

/** A part of a page that couldn't be shown; the rest of the page still works. */
export function BlockError() {
  return <Card style={{ paddingVertical: 28, paddingHorizontal: 20 }}><Text accessibilityRole="alert" style={[t.small, t.muted, { textAlign: 'center' }]}>This part of the page couldn't be shown.</Text></Card>
}

/** A whole page that couldn't be shown, as the web's PageError: expo-router's ErrorBoundary for every page (the
 * Stack's, in _layout.tsx) and for the app around them (its own). Says so, with a way to try again and the way back
 * to Today; the tab bar stays over a page's. Nothing of the fan's (no provider): it may be what failed. */
export function PageError({ retry }: ErrorBoundaryProps) {
  const insets = useSafeAreaInsets()
  const again = () => { retry().catch(() => {}) }
  const today = () => {
    try { router.replace('/') } catch { /* the app around the pages failed: trying again draws Today */ }
    again()
  }
  return (
    <View style={{ flex: 1, backgroundColor: colour.bg, paddingTop: insets.top + 20, paddingHorizontal: GUTTER }}>
      <Card style={{ padding: 18, gap: 12 }}>
        <Text accessibilityRole="alert" style={t.title}>Something went wrong showing this page.</Text>
        <Text style={[t.small, t.muted]}>Try again, or go back to Today.</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
          <Btn kind="primary" label="Try again" onPress={again} />
          <Btn label="Back to Today" onPress={today} />
        </View>
      </Card>
    </View>
  )
}

/** No page at this address (the web's NotFound, main.tsx), and the way back to Today. */
export function NotFound() {
  return (
    <>
      <Text style={dsp(54)}>Not found</Text>
      <Card pad style={{ gap: 12 }}>
        <Text style={t.title}>There's no page at this address.</Text>
        <Btn kind="primary" label="Back to Today" style={{ alignSelf: 'flex-start' }} onPress={() => router.replace('/')} />
      </Card>
    </>
  )
}

/** A page's head with the way back (the web's back button beside the title). */
export function BackHead({ title }: { title: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
      <Btn kind="icon" accessibilityLabel="Back" icon={<BackIcon size={20} color={colour.bg} />} onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} />
      <Text style={dsp(54)}>{title}</Text>
    </View>
  )
}



export const s = StyleSheet.create({
  switch: { width: 52, height: 32, borderRadius: 999 },
  knob: { position: 'absolute', top: 4, width: 24, height: 24, borderRadius: 12 },
  time: { height: 36, minWidth: 72, paddingHorizontal: 10, paddingVertical: 0, borderRadius: 10, borderWidth: 2, borderColor: colour.text, backgroundColor: colour.surface, color: colour.text, fontFamily: font[700], fontSize: 14 },
  badgeLive: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 24, paddingHorizontal: 10, borderRadius: 999, backgroundColor: colour.live, flexShrink: 0, alignSelf: 'flex-start' },
  badgeDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#FFFFFF' },
  liveDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colour.live, flexShrink: 0 },
  pill: { height: 24, paddingHorizontal: 10, borderRadius: 999, justifyContent: 'center', alignSelf: 'flex-start', flexShrink: 0 },
  chipSmall: { height: 22, paddingHorizontal: 9, borderRadius: 999, justifyContent: 'center', alignSelf: 'flex-start', maxWidth: '100%' },
  btn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, flexShrink: 0 },
  card: { backgroundColor: colour.surface, borderRadius: RADIUS_CARD },
  cardPad: { paddingVertical: 16, paddingHorizontal: 18 },
  list: { paddingVertical: 4, paddingHorizontal: 16 },
  listRow: { paddingVertical: 14, minHeight: 44 },
  listRowLine: { borderBottomWidth: 1, borderBottomColor: colour.line },
  sectionHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, marginTop: 8 },
  section: { gap: 10 },
  errorBox: { paddingVertical: 14, paddingHorizontal: 16, borderRadius: 14, backgroundColor: colour.dangerBg },
  errorText: { fontFamily: font[600], fontSize: 14, color: colour.danger },
  notice: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingVertical: 12, paddingHorizontal: 16, borderRadius: 14, backgroundColor: colour.surface },
  wl: { width: 30, height: 30, borderRadius: 9, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 8, height: 38, paddingHorizontal: 14, borderRadius: 999, backgroundColor: colour.surface },
  chipDot: { width: 9, height: 9, borderRadius: 3 },
})

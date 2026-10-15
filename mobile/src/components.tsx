// The pieces every screen is built from, in the web app's matchday look: a sport-coloured tile per player,
// a live card, section titles, buttons.

import { Image } from 'expo-image'
import { router } from 'expo-router'
import type { ReactNode } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native'
import { initials, relative, shortWhen } from '../../web/src/format'
import { absolute, type Card, type LiveStatus } from './api'
import { useSports, useTone } from './sports'
import { colour, font, GUTTER, type } from './theme'

/** The app's name beside the fan badge, as the web's header. */
export function Brand({ name }: { name: string }) {
  return (
    <View style={styles.brand}>
      <Image source={require('../../web/public/icons/icon-192.png')} style={styles.badge} contentFit="contain" />
      <Text style={styles.brandName}>{name}</Text>
    </View>
  )
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <View style={styles.sectionTitle}>
      <Text style={type.eyebrow}>{children}</Text>
      {right}
    </View>
  )
}

export function LiveDot({ label = 'Live' }: { label?: string }) {
  return (
    <View style={styles.liveTag}>
      <View style={styles.liveDot} />
      <Text style={styles.liveText}>{label}</Text>
    </View>
  )
}

export function Button({ label, onPress, kind = 'primary', busy, style }: { label: string; onPress: () => void; kind?: 'primary' | 'plain'; busy?: boolean; style?: StyleProp<ViewStyle> }) {
  const primary = kind === 'primary'
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={busy}
      onPress={onPress}
      android_ripple={{ color: primary ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.08)' }}
      style={[styles.button, primary ? styles.primary : styles.plain, style]}
    >
      {busy ? <ActivityIndicator color={primary ? '#FFFFFF' : colour.ink} size="small" /> : <Text style={[styles.buttonText, { color: primary ? '#FFFFFF' : colour.ink }]}>{label}</Text>}
    </Pressable>
  )
}

/** A followed player as a block in their sport's colour: who, and what's happening (live, next, or last). */
export function PlayerTile({ card }: { card: Card }) {
  const tone = useTone(card.player.sport)
  const sports = useSports()
  const live = card.live?.is_live
  const next = card.upcoming?.[0]
  const last = card.recent_results?.[0]
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${card.player.name}${live ? ', live' : ''}`}
      onPress={() => router.push(`/player/${card.player_id}`)}
      android_ripple={{ color: 'rgba(0,0,0,0.12)' }}
      style={[styles.tile, { backgroundColor: tone.block }]}
    >
      <View style={styles.tileTop}>
        <Text style={[styles.tileSport, { color: tone.soft }]} numberOfLines={1}>{sports.name(card.player.sport)}</Text>
        {live ? <LiveDot /> : null}
      </View>
      <Text style={[type.display, styles.tileName, { color: tone.on }]} numberOfLines={2}>{card.player.name}</Text>
      {card.status === 'building' ? (
        <Text style={[styles.tileLine, { color: tone.soft }]}>Building the page…</Text>
      ) : live ? (
        <>
          <Text style={[type.number, styles.tileScore, { color: tone.on }]} numberOfLines={2}>{card.live.score || card.live.event}</Text>
          {card.live.clock ? <Text style={[styles.tileLine, { color: tone.soft }]}>{card.live.clock}</Text> : null}
        </>
      ) : next ? (
        <Text style={[styles.tileLine, { color: tone.soft }]} numberOfLines={2}>
          Next · {next.title} · {shortWhen(next.start_utc)}
        </Text>
      ) : last ? (
        <Text style={[styles.tileLine, { color: tone.soft }]} numberOfLines={2}>{last.result}</Text>
      ) : (
        <Text style={[styles.tileLine, { color: tone.soft }]} numberOfLines={1}>{card.player.teams?.[0] ?? ''}</Text>
      )}
    </Pressable>
  )
}

/** A live game: where, the score, the clock, and the player's line when there's one. */
export function LiveCard({ title, live, sport, onPress }: { title: string; live: LiveStatus; sport?: string | null; onPress?: () => void }) {
  const tone = useTone(sport ?? live.state?.kind)
  return (
    <Pressable onPress={onPress} disabled={!onPress} android_ripple={{ color: 'rgba(0,0,0,0.08)' }} style={styles.liveCard}>
      <View style={[styles.liveStripe, { backgroundColor: tone.block }]} />
      <View style={styles.liveBody}>
        <View style={styles.tileTop}>
          <Text style={[type.eyebrow, styles.shrink]} numberOfLines={1}>{live.competition || title}</Text>
          <LiveDot label={live.clock || 'Live'} />
        </View>
        <Text style={[type.number, styles.liveScore]} numberOfLines={2}>{live.score || live.event || title}</Text>
        {live.headline ? <Text style={type.small} numberOfLines={2}>{live.headline}</Text> : null}
        {live.player_stats?.length ? (
          <Text style={type.small} numberOfLines={2}>{live.player_stats.map((s) => `${s.value} ${s.label}`).join(' · ')}</Text>
        ) : null}
        {live.as_of ? <Text style={[type.small, styles.asOf]}>Updated {relative(live.as_of)}</Text> : null}
      </View>
    </Pressable>
  )
}

/** A player's photo, or their initials. (Framing on the face, photo.crop, comes with the player page's tabs.) */
export function Avatar({ card, size = 56 }: { card: Card; size?: number }) {
  const tone = useTone(card.player.sport)
  const photo = card.player.photo
  const box = { width: size, height: size, borderRadius: size / 2 }
  if (!photo?.url) {
    return (
      <View style={[box, styles.initials, { backgroundColor: tone.on }]}>
        <Text style={[styles.initialsText, { color: tone.block, fontSize: size * 0.36 }]}>{initials(card.player.name)}</Text>
      </View>
    )
  }
  return <Image source={{ uri: absolute(photo.url) }} style={[box, { backgroundColor: colour.raised }]} contentFit="cover" contentPosition="top" accessibilityLabel={photo.credit} />
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <View style={styles.empty}>
      <Text style={[type.display, styles.emptyTitle]}>{title}</Text>
      {children}
    </View>
  )
}

const styles = StyleSheet.create({
  brand: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  badge: { width: 34, height: 34 },
  brandName: { fontFamily: font.logo, fontSize: 26, color: colour.ink, letterSpacing: -0.3 },
  sectionTitle: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 22, marginBottom: 10 },
  liveTag: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: colour.surface, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colour.live },
  liveText: { fontFamily: font.heavy, fontSize: 11, letterSpacing: 0.8, textTransform: 'uppercase', color: colour.liveText },
  button: { minHeight: 44, borderRadius: 999, paddingHorizontal: 18, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
  primary: { backgroundColor: colour.ink },
  plain: { backgroundColor: colour.surface, borderWidth: 2, borderColor: colour.ink },
  buttonText: { fontFamily: font.heavy, fontSize: 14, letterSpacing: 0.3 },
  tile: { flex: 1, minHeight: 150, borderRadius: 20, padding: 14, gap: 6, overflow: 'hidden' },
  tileTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  shrink: { flexShrink: 1 },
  tileSport: { fontFamily: font.heavy, fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', flexShrink: 1 },
  tileName: { fontSize: 26, lineHeight: 27 },
  tileScore: { fontSize: 22, lineHeight: 24 },
  tileLine: { fontFamily: font.bold, fontSize: 13, lineHeight: 17, marginTop: 'auto' },
  liveCard: { flexDirection: 'row', backgroundColor: colour.surface, borderRadius: 22, overflow: 'hidden', marginBottom: 10 },
  liveStripe: { width: 8 },
  liveBody: { flex: 1, padding: 14, gap: 4 },
  liveScore: { fontSize: 26, lineHeight: 29, color: colour.ink },
  asOf: { fontSize: 12 },
  initials: { alignItems: 'center', justifyContent: 'center' },
  initialsText: { fontFamily: font.display },
  empty: { backgroundColor: colour.surface, borderRadius: 22, padding: GUTTER, gap: 10, marginTop: 12 },
  emptyTitle: { fontSize: 28, lineHeight: 30, color: colour.ink },
})

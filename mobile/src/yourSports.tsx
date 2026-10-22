// Your sports (BHA-653), the web's components/YourSports.tsx: the tiles of the sports a fan can pick (the welcome's
// first step and the Edit sheet), the Edit sheet itself (Following's, and Today's game search's way here), and the
// fan's sports at the top of Following. What they decide is web/src/yourSports.ts's; only the drawing is here.

import { useEffect, useState, type ReactNode } from 'react'
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { longestWordEms } from '../../web/src/format'
import { followedSports, sheetSummary, tickedAtFirst, tileGroups, toggled, toSave, yoursShown } from '../../web/src/yourSports'
import type { SportInfo } from './api'
import { useSports, useTone } from './catalog'
import { useFollowing } from './following'
import { CheckIcon, EditIcon, PlusIcon } from './icons'
import { colour, dsp, font, RADIUS_CARD, RADIUS_TILE } from './theme'
import { Btn, ErrorBox, Eyebrow } from './ui'

/** One sport to pick (.tile.pick.sport): a block of its colour, ringed and ticked once picked; `mini`, a group's
 * sport (each esports game), smaller. Its name as large as fits its longest word beside the corner mark (the web's
 * --ems, format.longestWordEms): BASKETBALL in a sheet's three across, LEAGUE OF LEGENDS on two lines. */
function SportTile({ sport, on, onPress, mini }: { sport: SportInfo; on: boolean; onPress: () => void; mini?: boolean }) {
  const tone = useTone(sport.key)
  const [width, setWidth] = useState(0)
  const mark = mini ? 16 : 20
  const beside = mini ? 18 : 24 // the corner mark's room
  const base = mini ? 17 : 30
  // Inside its padding, the ring a pick draws (on every tile, so a tick doesn't change the size) and the mark's room;
  // a little to spare, as Android's measure of the slanted face runs wider than the web's.
  const room = width - (mini ? 24 : 28) - 6 - beside
  const size = width ? Math.max(12, Math.min(base, room / (longestWordEms(sport.label) * 1.06))) : base
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={sport.label} accessibilityState={{ selected: on }} onPress={onPress} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
      style={[s.tile, mini && s.mini, { backgroundColor: tone.sport }, on && { borderWidth: 3, borderColor: tone.on }]}>
      <View style={[s.corner, mini && s.miniCorner]}>{on ? <CheckIcon size={mark} color={tone.on} /> : <PlusIcon size={mark} color={tone.on} />}</View>
      <Text style={[dsp(size), { color: tone.on, paddingRight: beside }]}>{sport.label}</Text>
    </Pressable>
  )
}

/** Tiles `across` to a row. */
function Grid({ across, children }: { across: number; children: ReactNode[] }) {
  return <View style={s.grid}>{children.map((c, i) => <View key={i} style={{ width: `${100 / across}%`, padding: 4 }}>{c}</View>)}</View>
}

/**
 * The sports a fan can pick, each ticked or not with a tap (the web's SportTiles): every sport that's on, those of a
 * group ("esports": each game its own) smaller, under the group's name (yourSports.tileGroups). `across`: the main
 * sports to a row (the welcome's two, the sheet's three).
 */
export function SportTiles({ tiles, picked, onPick, across = 2 }: { tiles: SportInfo[]; picked: string[]; onPick: (key: string) => void; across?: number }) {
  const sports = useSports()
  return (
    <>
      {tileGroups(tiles).map(({ group, sports: these }) => (
        <View key={group} style={{ gap: 10 }} accessibilityLabel={group ? `${sports.info(group).label} games` : 'Sports'}>
          {group ? <Eyebrow>{sports.info(group).label} · each game its own</Eyebrow> : null}
          <Grid across={group ? 3 : across}>
            {these.map((x) => <SportTile key={x.key} sport={x} on={picked.includes(x.key)} onPress={() => onPick(x.key)} mini={Boolean(group)} />)}
          </Grid>
        </View>
      ))}
    </>
  )
}

/**
 * Following's Edit (and Today's game search's way here), the web's YourSportsSheet: the fan's sports, ticked, to add or
 * drop one. With none saved yet, the sports of who they follow are ticked. Nothing is saved until Save, which sends
 * any of theirs this app shows no tile for too (yourSports.toSave). From the bottom, as the app's other sheets; Back,
 * or a tap above it, closes it.
 */
export function YourSportsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets()
  const sports = useSports()
  const { sports: saved, cards, teams, saveSports } = useFollowing()
  const tiles = sports.running()
  const [picked, setPicked] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Each time it opens, from what's saved now.
  useEffect(() => {
    if (!open) return
    setPicked(tickedAtFirst(saved, followedSports(cards, teams, (x) => sports.info(x).key), sports.running()))
    setError(null)
  }, [open]) // only on opening: what the fan ticks meanwhile is theirs
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await saveSports(toSave(picked, saved, tiles))
      onClose()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }
  const before = saved.filter((k) => tiles.some((x) => x.key === k))
  return (
    <Modal visible={open} transparent animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} accessibilityLabel="Close" />
      <View style={[s.sheet, { paddingBottom: 20 + insets.bottom, maxHeight: '92%' }]} accessibilityViewIsModal accessibilityLabel="Your sports">
        <ScrollView style={{ flexShrink: 1 }} contentContainerStyle={{ gap: 14, paddingBottom: 4 }}>
          <View style={{ gap: 6 }}>
            <Text accessibilityRole="header" style={dsp(44)}>Your sports</Text>
            <Text style={s.note}>Each one gets a chip on Today, and players to follow. Dropping one keeps everyone you follow in it.</Text>
          </View>
          <SportTiles tiles={tiles} picked={picked} onPick={(key) => setPicked((p) => toggled(p, key))} across={3} />
        </ScrollView>
        <View style={{ gap: 10, paddingTop: 10 }}>
          {error ? <ErrorBox>{error}</ErrorBox> : null}
          <Text style={s.summary}>{sheetSummary(picked, before, (key) => sports.info(key).label)}</Text>
          <View style={s.buttons}>
            <Btn label="Cancel" onPress={onClose} style={{ flex: 1, height: 48 }} />
            <Btn kind="primary" label="Save" busy={saving} onPress={() => void save()} style={{ flex: 2, height: 48 }} />
          </View>
        </View>
      </View>
    </Modal>
  )
}

/** The fan's sports at the top of Following, in their order, and Edit (the web's YourSportsRow; .your-sports).
 * Nothing until the server has said them. */
export function YourSportsRow() {
  const sports = useSports()
  const { sports: saved, sportsLoaded } = useFollowing()
  const [editing, setEditing] = useState(false)
  if (!sportsLoaded) return null
  const shown = yoursShown(saved, (key) => sports.info(key))
  return (
    <View style={s.row} accessibilityLabel="Your sports">
      <View style={s.head}>
        <Text accessibilityRole="header" style={dsp(26)}>Your sports</Text>
        <Btn small label={shown.length ? 'Edit' : 'Pick'} icon={<EditIcon size={14} strokeWidth={2.8} />} onPress={() => setEditing(true)} />
      </View>
      {shown.length ? (
        <View style={s.pills}>{shown.map((x) => <SportPill key={x.key} sport={x} />)}</View>
      ) : null}
      <Text style={s.note}>{shown.length ? 'Today gives each a chip, and keeps offering players from them.' : 'Pick the sports you watch: Today gives each a chip, and keeps offering players from them.'}</Text>
      <YourSportsSheet open={editing} onClose={() => setEditing(false)} />
    </View>
  )
}

/** One of the fan's sports (.sport-pill): its name on its colour. */
function SportPill({ sport }: { sport: SportInfo }) {
  const tone = useTone(sport.key)
  return (
    <View style={[s.pill, { backgroundColor: tone.sport }]}>
      <Text style={[dsp(19), { color: tone.on }]}>{sport.label}</Text>
    </View>
  )
}

const s = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -4 },
  tile: { flex: 1, minHeight: 104, padding: 14, borderRadius: RADIUS_TILE, justifyContent: 'flex-start' },
  mini: { minHeight: 64, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 16 },
  corner: { position: 'absolute', top: 12, right: 12, zIndex: 1 },
  miniCorner: { top: 9, right: 9 },
  backdrop: { flex: 1, backgroundColor: 'rgba(13,13,13,0.4)' },
  sheet: { paddingTop: 18, paddingHorizontal: 16, borderTopLeftRadius: 26, borderTopRightRadius: 26, backgroundColor: colour.bg },
  note: { fontFamily: font[600], fontSize: 13, lineHeight: 18, color: colour.muted },
  summary: { fontFamily: font[700], fontSize: 12.5, color: colour.muted, textAlign: 'center' },
  buttons: { flexDirection: 'row', gap: 8 },
  row: { gap: 10, padding: 14, borderRadius: RADIUS_CARD, backgroundColor: colour.surface },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: { height: 40, paddingHorizontal: 15, borderRadius: 999, justifyContent: 'center' },
})

// Today's game filter, the web's (pages/TodayPage.tsx: useGame, GameChips, FindGame, NothingIn; .game-filter in
// styles.css): under Today's head, All, a chip of each of the fan's own games on Today, and a round search at the
// row's end for the others there (filter.ts gameRow). The game picked narrows every block (filter.ts narrow).

import { LinearGradient } from 'expo-linear-gradient'
import { useLocalSearchParams, useNavigation, type NativeStackNavigationProp } from 'expo-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Keyboard, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { findGames, pickedGame, type GameRow } from '../../../web/src/filter'
import type { SportInfo } from '../api'
import { useTone } from '../catalog'
import { useFollowing } from '../following'
import { YourSportsSheet } from '../yourSports'
import { SearchIcon } from '../icons'
import { colour, font, GUTTER } from '../theme'
import { alpha, Btn, Eyebrow, LiveDot, Notice } from '../ui'

const FADE = 20 // with the search, the chips fade out under it, and the last one scrolls clear of the fade

/**
 * The game picked on Today (its screen's `game` param, as the web's ?game=), while it's one of the row's (`row`: a
 * chip, or behind its search); else none: All. A stann.in link to Today with a game (an App Link) opens on it, and
 * nothing keeps it past the screen: Today opens on All. A game picked that has left the page since (its live game
 * over) is let go, so it can't come back by itself; one not on the page yet (Today still loading) is waited for.
 * The param is this screen's own (its navigation's, not the router's), so letting a game go while a page is open
 * over Today changes nothing of that page's.
 */
export function useGame(row: GameRow): [SportInfo | null, (key: string | null) => void] {
  const navigation = useNavigation<NativeStackNavigationProp<{ index: { game?: string } }>>()
  const { game: asked } = useLocalSearchParams<{ game?: string }>()
  const game = pickedGame(asked, row)
  const pick = useCallback((key: string | null) => navigation.setParams({ game: key ?? undefined }), [navigation])
  const shown = useRef<string | null>(null) // the game last shown picked
  useEffect(() => {
    if (game) shown.current = game.key
    else if (asked && asked === shown.current) {
      shown.current = null
      pick(null)
    }
  }, [game, asked, pick])
  return [game, pick]
}

/** One game's chip (.game-chip): outlined in ink with a dot of its colour; picked, a block of its colour. While one
 * of it is on now (`live`), the pulsing red dot of a live player's pill instead. No game is All, ink when picked. */
function GameChip({ game, live = false, picked, onPress, onLayout }: { game: SportInfo | null; live?: boolean; picked: boolean; onPress: () => void; onLayout?: (x: number, width: number) => void }) {
  const tone = useTone(game?.key)
  const ground = !picked ? 'transparent' : game ? tone.sport : colour.text
  const ink = !picked ? colour.text : game ? tone.on : colour.bg
  return (
    <Pressable
      onPress={onPress}
      onLayout={onLayout && ((e) => onLayout(e.nativeEvent.layout.x, e.nativeEvent.layout.width))}
      accessibilityRole="button"
      accessibilityLabel={game ? `${game.label}${live ? ', live' : ''}` : 'All'}
      accessibilityState={{ selected: picked }}
      style={({ pressed }) => [s.chip, { backgroundColor: ground, borderColor: picked ? ground : colour.text }, pressed && !picked && { backgroundColor: alpha(colour.text, 0.06) }]}
    >
      {/* Ringed on a picked chip, so the red reads on any sport's colour. */}
      {game ? (live ? <LiveDot ring={picked ? ink : undefined} /> : <View style={[s.dot, { backgroundColor: picked ? ink : tone.sport }]} />) : null}
      <Text style={[s.chipText, { color: ink }]} numberOfLines={1}>{game ? game.label : 'All'}</Text>
    </Pressable>
  )
}

/**
 * Today's game filter, under its head: All, a chip of each of the fan's own games on the page (their sports' among
 * them), and a search for the others (filter.ts gameRow), which leads to their sports too, so a game found can keep
 * its chip; one of those picked has its chip after the fan's own while it's picked. A game with one on now (`live`,
 * filter.ts gamesOnNow) says so, picked or not, in the row and in its search. Not there while the page holds one
 * game or none. The chips scroll sideways out to the page's edge; the search stays at the row's end, in sight
 * however many games the fan has. A game picked off the row's edge (from the search, or a link) is brought into it.
 */
export function GameChips({ row, game, live, onPick }: { row: GameRow; game: SportInfo | null; live: Set<string>; onPick: (key: string | null) => void }) {
  const [finding, setFinding] = useState(false)
  const [editing, setEditing] = useState(false)
  const { sportsLoaded } = useFollowing() // the fan's sports as the server has them, before the sheet can save over them
  const line = useRef<ScrollView>(null)
  const view = useRef({ x: 0, width: 0 }) // how far the row is scrolled, and how wide it shows
  const places = useRef(new Map<string, { x: number; width: number }>()) // where each chip is along it
  const picked = game?.key
  const bring = useCallback((key: string) => {
    const at = places.current.get(key)
    const { x, width } = view.current
    if (!at || !width) return
    if (at.x < x || at.x + at.width > x + width - FADE) line.current?.scrollTo({ x: Math.max(0, at.x - (width - at.width) / 2), animated: true })
  }, [])
  // All, picked from further down (No … games today's "All games"), takes the row back to its start, where All is.
  useEffect(() => { if (picked) bring(picked); else line.current?.scrollTo({ x: 0, animated: true }) }, [picked, bring])
  // The sheet stays if the row goes while the fan edits (a refresh leaving one game or none).
  const sheet = editing ? <YourSportsSheet onClose={() => setEditing(false)} /> : null
  if (!row.chips.length && !row.more.length) return sheet
  const find = row.more.length > 0
  const chips = game && !row.chips.includes(game) ? [...row.chips, game] : row.chips
  const placed = (key: string) => (x: number, width: number) => {
    places.current.set(key, { x, width })
    if (key === picked) bring(key)
  }
  return (
    <View style={s.filter}>
      <View style={[s.line, !find && { marginRight: -GUTTER }]}>
        <ScrollView
          ref={line}
          horizontal
          showsHorizontalScrollIndicator={false}
          accessibilityLabel="Show games"
          contentContainerStyle={[s.chips, { paddingRight: find ? FADE : GUTTER }]}
          // Its width may come after the chips' places (a link's game, laid out with the row): bring it in then.
          onLayout={(e) => { view.current.width = e.nativeEvent.layout.width; if (picked) bring(picked) }}
          onScroll={(e) => { view.current.x = e.nativeEvent.contentOffset.x }}
          // A chip gone (a game from the search, no longer picked) leaves the row shorter: Android keeps the scroll
          // past its new end, a blank stretch before the search, where a browser would pull it back.
          onContentSizeChange={(w) => { const end = Math.max(0, w - view.current.width); if (view.current.x > end) line.current?.scrollTo({ x: end, animated: false }) }}
          scrollEventThrottle={32}
        >
          <GameChip game={null} picked={!game} onPress={() => onPick(null)} />
          {chips.map((x) => <GameChip key={x.key} game={x} live={live.has(x.key)} picked={picked === x.key} onPress={() => onPick(x.key)} onLayout={placed(x.key)} />)}
        </ScrollView>
        {find ? <LinearGradient pointerEvents="none" colors={[alpha(colour.bg, 0), colour.bg]} start={{ x: 0, y: 0.5 }} end={{ x: 1, y: 0.5 }} style={s.fade} /> : null}
      </View>
      {find ? (
        <Pressable onPress={() => setFinding(true)} accessibilityRole="button" accessibilityLabel="Find another game" style={({ pressed }) => [s.chip, s.find, pressed && { backgroundColor: alpha(colour.text, 0.06) }]}>
          <SearchIcon size={18} />
        </Pressable>
      ) : null}
      <FindGame open={finding} games={row.more} live={live} onPick={onPick} onClose={() => setFinding(false)}
        onYourSports={sportsLoaded ? () => { setFinding(false); setEditing(true) } : undefined} />
      {sheet}
    </View>
  )
}

/** How far the keyboard reaches above the navigation bar (React Native's height leaves the bar out), 0 while it's
 * down: a sheet with a box to type in sits above it. The sheet's Modal is edge to edge, so the keyboard doesn't
 * shrink it. */
function useKeyboard(): number {
  const [height, setHeight] = useState(0)
  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (e) => setHeight(e.endCoordinates.height))
    const hide = Keyboard.addListener('keyboardDidHide', () => setHeight(0))
    return () => { show.remove(); hide.remove() }
  }, [])
  return height
}

/** The row's search (the web's FindGame, a .feed-sheet as the Feed's "…" sheet, post.tsx's ItemSheet): the other games
 * with something on Today, those on now first, narrowed as the fan types; a tap shows that one. At its foot, the way
 * to the fan's sports (`onYourSports`: the Edit sheet, once their sports have come), so a game they came looking for
 * gets a chip every day. Back, or a tap above it, closes it. */
function FindGame({ open, games, live, onPick, onClose, onYourSports }: { open: boolean; games: SportInfo[]; live: Set<string>; onPick: (key: string) => void; onClose: () => void; onYourSports?: () => void }) {
  const insets = useSafeAreaInsets()
  const keyboard = useKeyboard()
  const [words, setWords] = useState('')
  useEffect(() => { if (open) setWords('') }, [open]) // each time it opens, every game again
  const found = findGames(games, words, live)
  const choose = (key: string) => {
    onPick(key)
    onClose()
  }
  return (
    <Modal visible={open} transparent animationType="slide" statusBarTranslucent navigationBarTranslucent onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} accessibilityLabel="Close" />
      <View style={[s.sheet, { paddingBottom: 28 + insets.bottom + keyboard, maxHeight: '88%' }]} accessibilityViewIsModal accessibilityLabel="Find a game">
        <Eyebrow>More games on today</Eyebrow>
        <TextInput
          value={words}
          onChangeText={setWords}
          placeholder="Search games"
          placeholderTextColor={colour.muted}
          accessibilityLabel="Search games"
          autoCorrect={false}
          returnKeyType="search"
          style={s.field}
        />
        <ScrollView style={{ flexShrink: 1 }} contentContainerStyle={s.list} keyboardShouldPersistTaps="handled">
          {found.map((x) => <GameChip key={x.key} game={x} live={live.has(x.key)} picked={false} onPress={() => choose(x.key)} />)}
          {!found.length ? <Text style={s.none}>No game by that name on today</Text> : null}
        </ScrollView>
        {onYourSports ? (
          <Pressable onPress={onYourSports} accessibilityRole="button" style={({ pressed }) => [s.yours, pressed && { opacity: 0.8 }]}>
            <View style={{ flex: 1, gap: 3 }}>
              <Text style={s.yoursTitle}>Want one in your row every day?</Text>
              <Text style={s.none}>Add it to your sports: it gets a chip, and players to follow.</Text>
            </View>
            <View style={s.go}><Text style={s.goText}>Your sports</Text></View>
          </Pressable>
        ) : null}
        <Btn kind="primary" label="Close" style={{ height: 48 }} onPress={onClose} />
      </View>
    </Modal>
  )
}

/** The game picked has nothing on Today but the fan's players in it (the strip): say so, and the way back to every
 * game. "Games": posts of theirs may still be From your players, at the foot. */
export function NothingIn({ game, onAll }: { game: SportInfo; onAll: () => void }) {
  return (
    <Notice>
      <Text style={s.notice} accessibilityLiveRegion="polite">No {game.label} games today</Text>
      <Btn small label="All games" onPress={onAll} />
    </Notice>
  )
}

const s = StyleSheet.create({
  filter: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  line: { flexShrink: 1, marginLeft: -GUTTER },
  chips: { gap: 8, paddingLeft: GUTTER },
  fade: { position: 'absolute', top: 0, right: 0, bottom: 0, width: FADE },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 8, height: 40, paddingHorizontal: 14, borderRadius: 999, borderWidth: 2, borderColor: colour.text, flexShrink: 0 },
  chipText: { fontFamily: font[800], fontSize: 14 },
  dot: { width: 9, height: 9, borderRadius: 3 },
  find: { width: 40, paddingHorizontal: 0, justifyContent: 'center' },
  backdrop: { flex: 1, backgroundColor: 'rgba(13,13,13,0.4)' },
  sheet: { gap: 12, paddingTop: 18, paddingHorizontal: 18, borderTopLeftRadius: 26, borderTopRightRadius: 26, backgroundColor: colour.surface },
  field: { height: 54, paddingHorizontal: 16, paddingVertical: 0, borderRadius: 16, borderWidth: 2, borderColor: colour.text, backgroundColor: colour.surface, color: colour.text, fontFamily: font[600], fontSize: 17 },
  list: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  none: { fontFamily: font[500], fontSize: 13, lineHeight: 18.5, color: colour.muted },
  notice: { flex: 1, fontFamily: font[600], fontSize: 13, color: colour.text },
  yours: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, paddingHorizontal: 16, borderRadius: 20, backgroundColor: colour.bg },
  yoursTitle: { fontFamily: font[800], fontSize: 14, color: colour.text },
  go: { height: 36, paddingHorizontal: 14, borderRadius: 999, borderWidth: 2, borderColor: colour.text, justifyContent: 'center', flexShrink: 0 },
  goText: { fontFamily: font[800], fontSize: 13, color: colour.text },
})

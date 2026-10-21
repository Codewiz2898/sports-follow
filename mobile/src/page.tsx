// A page (the web's .main): paper, 20px below the status bar, 16px a side, 120px at the foot so the floating
// tab bar covers nothing, and 16px between its blocks; what just happened to this phone's sign-in at its top,
// on every page, as the web says it.

import { createContext, useCallback, useEffect, useRef, type ReactElement, type ReactNode, type RefObject } from 'react'
import { FlatList, RefreshControl, ScrollView, StyleSheet, View, type FlatListProps } from 'react-native'
import { useReducedMotion } from 'react-native-reanimated'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { AccountNotice, useAccount } from './account'
import { colour, GUTTER } from './theme'

/** Brings a block of the page into view, its top a little below the status bar (the web's scrollIntoView): a
 * chess board, when a move in the list under it opens its analysis. None outside a Page. */
export const PageScroll = createContext<((block: View | null) => void) | null>(null)

/** `top`, false for a page whose header is a block of colour up to the top edge (a player's band): a notice
 * then sits above the band, below the status bar. `bar`, the colour behind the status bar. */
export function Page({ children, onRefresh, refreshing, top = true, bar = colour.bg }: { children: ReactNode; onRefresh?: () => void; refreshing?: boolean; top?: boolean; bar?: string }) {
  const insets = useSafeAreaInsets()
  const scroll = useRef<ScrollView>(null)
  const inner = useRef<View>(null)
  const still = useReducedMotion()
  const { notice } = useAccount()
  useEffect(() => { scroll.current?.scrollTo({ y: 0, animated: false }) }, [])
  const show = useCallback((block: View | null) => {
    if (block && inner.current) block.measureLayout(inner.current, (_x, y) => scroll.current?.scrollTo({ y: Math.max(0, y - insets.top - 12), animated: !still }))
  }, [insets.top, still])
  return (
    <PageScroll value={show}>
      <View style={s.page}>
        <ScrollView
          ref={scroll}
          innerViewRef={inner as RefObject<View>} // React Native's type has no room for the null it starts with
          style={s.page}
          contentContainerStyle={[s.content, { paddingTop: top ? insets.top + 20 : 0, paddingBottom: 120 + insets.bottom }]}
          keyboardShouldPersistTaps="handled"
          refreshControl={onRefresh ? <RefreshControl refreshing={Boolean(refreshing)} onRefresh={onRefresh} /> : undefined}
        >
          {notice ? <View style={top ? undefined : { paddingTop: insets.top + 16 }}><AccountNotice /></View> : null}
          {children}
        </ScrollView>
        {/* The status bar's own strip, of the page's ground (a band's colour on a player's page), over what scrolls under it. */}
        <View pointerEvents="none" style={[s.bar, { height: insets.top, backgroundColor: bar }]} />
      </View>
    </PageScroll>
  )
}

/** A page that's a long list (the Feed): the same paper, padding and notice, but only the rows near the screen
 * are drawn, so their pictures load as they come near. `head` goes above the rows, `foot` below them; `gap`
 * between them all. */
export function PageList<T>({ head, foot, onRefresh, refreshing, gap = 14, ...list }: Omit<FlatListProps<T>, 'ListHeaderComponent' | 'ListFooterComponent' | 'refreshControl'> & { head: ReactElement; foot?: ReactElement; onRefresh?: () => void; refreshing?: boolean; gap?: number }) {
  const insets = useSafeAreaInsets()
  const { notice } = useAccount()
  return (
    <View style={s.page}>
      <FlatList
        {...list}
        style={s.page}
        contentContainerStyle={[s.content, { gap, paddingTop: insets.top + 20, paddingBottom: 120 + insets.bottom }]}
        ListHeaderComponent={<View style={{ gap: 16 }}>{notice ? <AccountNotice /> : null}{head}</View>}
        ListFooterComponent={foot}
        refreshControl={onRefresh ? <RefreshControl refreshing={Boolean(refreshing)} onRefresh={onRefresh} /> : undefined}
      />
      <View pointerEvents="none" style={[s.bar, { height: insets.top, backgroundColor: colour.bg }]} />
    </View>
  )
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colour.bg },
  content: { paddingHorizontal: GUTTER, gap: 16 },
  bar: { position: 'absolute', top: 0, left: 0, right: 0 },
})

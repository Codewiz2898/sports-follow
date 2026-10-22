// This week and the Wire (GET /api/me/week), as the web's ThisWeek.tsx and Wire.tsx: the fan's players' big
// moments of the last week as posters in their sport's colour, and who's out and who's back, line-ups and news
// in an ink card, theirs first, then a few of the best-known players they don't follow.

import { router } from 'expo-router'
import { useEffect, useState } from 'react'
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { safeHref, shortName } from '../../../web/src/format'
import { sharePoster } from '../../../web/src/share'
import { longestWord, posterWhen, wireText, wireWhen } from '../../../web/src/today'
import { api, type WeekPoster, type WireRow } from '../api'
import { toneOf, useSports } from '../catalog'
import { useFollowing } from '../following'
import { Rail, type RailCard } from '../rail'
import { ShareButton } from '../share'
import { colour, dsp, font, RADIUS_TILE } from '../theme'
import { breakable, Pill, Section } from '../ui'

interface Week { posters: WeekPoster[]; wire: WireRow[] }
const NONE: Week = { posters: [], wire: [] }

/** This week and the Wire: loaded when the follow list changes, and again when it comes back changed. A fan who
 * follows nobody still has a Wire: the best-known players' facts. */
export function useWeek(): Week {
  const { cards, loaded, heard } = useFollowing()
  const [week, setWeek] = useState<Week>(NONE)
  const who = cards.map((c) => c.player_id).sort((a, b) => a - b).join(',')
  useEffect(() => {
    if (!loaded) return
    let stale = false
    api.week().then((r) => { if (!stale) setWeek(r) }).catch(() => {})
    return () => { stale = true }
  }, [loaded, who, heard])
  return week
}

const POSTER = 172
const POSTER_PAD = 14

/**
 * One big moment (.poster): a block of the player's sport colour, its word big, then their name and the line.
 * Its Share button sits on it, outside the press that opens the player (.poster-wrap), and shares the game
 * (/player/<id>?g=<event id>), whose link previews this moment.
 */
function Poster({ poster, now, fill }: { poster: WeekPoster; now: number } & RailCard) {
  const tone = toneOf(useSports(), poster.sport)
  const [width, setWidth] = useState(POSTER)
  // Its longest word on one line, at 52px or as large as the poster's width takes it: an Anton capital is up
  // to half an em wide, and the lean adds a little.
  const word = Math.min(52, (width - POSTER_PAD * 2) / (longestWord(poster.word) * 0.52))
  return (
    <View style={[s.posterWrap, fill && { width: undefined }]}>
      <Pressable onPress={() => router.push(`/player/${poster.player_id}`)} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} style={[s.poster, { backgroundColor: tone.sport }]}>
        <Pill small label="Yours" bg={colour.text} color={colour.bg} />
        <Text style={[dsp(word), { color: tone.on }]}>{breakable(poster.word)}</Text>
        <View style={{ gap: 2 }}>
          <Text style={[dsp(22), { color: tone.on }]}>{shortName(poster.player)}</Text>
          <Text style={[s.line, { color: tone.on }]} numberOfLines={2}>{[poster.line, posterWhen(poster.at, now)].filter(Boolean).join(' · ')}</Text>
        </View>
      </Pressable>
      <ShareButton {...sharePoster(poster)} poster style={s.posterShare} />
    </View>
  )
}

/** "This week" on Today: the big moments of the fan's players over the last seven days, a poster each. */
export function ThisWeek({ posters, now }: { posters: WeekPoster[]; now: number }) {
  return (
    <Rail title="This week" note="The big moments" all="Every big moment this week" columns={2}>
      {posters.map((p) => <Poster key={`${p.player_id}:${p.event_id ?? `m${p.id}`}`} poster={p} now={now} />)}
    </Rail>
  )
}

/** One row: its tag in the player's sport colour (outlined for a player the fan doesn't follow), the line (a
 * fact, a line-up, or a story's headline with the player's name first when it doesn't say it), where it's from,
 * and when. A story opens at its site; a fact or a line-up on the player's page, or the source's athlete page
 * for a player nobody follows yet. */
function Line({ row, now, last }: { row: WireRow; now: number; last: boolean }) {
  const tone = toneOf(useSports(), row.sport)
  const away = safeHref(row.url)
  const inside = row.url.startsWith('/') && !row.url.startsWith('//')
  const open = away ? () => Linking.openURL(away) : inside ? () => router.push(row.url as never) : undefined
  return (
    <Pressable disabled={!open} onPress={open} style={[s.row, !last && s.rowLine]}>
      {row.yours
        ? <Pill small label={row.tag} bg={tone.sport} color={tone.on} style={{ flexShrink: 0 }} />
        : <Pill small outline label={row.tag} color={colour.text} style={{ flexShrink: 0, borderColor: tone.sport }} />}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text style={s.text} numberOfLines={2}>{wireText(row)}</Text>
        {row.source ? <Text style={s.source} numberOfLines={1}>{row.source}</Text> : null}
      </View>
      <Text style={s.time}>{wireWhen(row.at, now)}</Text>
    </Pressable>
  )
}

/** The Wire on Today: a white card of who's out and who's back, line-ups and news, the fan's players' first. Nothing
 * when there's none. Its title always: under This week's posters, the card would otherwise read as part of This week. */
export function Wire({ rows, now }: { rows: WireRow[]; now: number }) {
  if (!rows.length) return null
  return (
    <Section title="The wire">
      <View style={s.wire}>
        {rows.map((r, i) => <Line key={r.id} row={r} now={now} last={i === rows.length - 1} />)}
      </View>
    </Section>
  )
}

const s = StyleSheet.create({
  posterWrap: { width: POSTER },
  poster: { overflow: 'hidden', justifyContent: 'space-between', gap: 8, height: 236, padding: POSTER_PAD, borderRadius: RADIUS_TILE },
  posterShare: { position: 'absolute', top: 10, right: 10 },
  line: { fontFamily: font[700], fontSize: 12, lineHeight: 15.6 },
  wire: { paddingVertical: 4, paddingHorizontal: 16, borderRadius: 18, backgroundColor: colour.surface },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 48, paddingVertical: 8 },
  rowLine: { borderBottomWidth: 1, borderBottomColor: colour.line },
  text: { fontFamily: font[700], fontSize: 13, lineHeight: 17, color: colour.text },
  source: { fontFamily: font[700], fontSize: 11, color: colour.muted },
  time: { flexShrink: 0, fontFamily: font[800], fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', color: colour.muted },
})

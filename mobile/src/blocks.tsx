// The web app's blocks (web/src/components/Blocks.tsx), one for one: the live scoreboard and its lines, a result
// card, a coming game's card, a player's tile, the player band, lists of fixtures, results and news, stats.
// What each says is worked out by the shared web/src/lines.ts and format.ts; only the drawing is here.

import { router } from 'expo-router'
import { useState, type ReactNode } from 'react'
import { Linking, Pressable, StyleSheet, Text, useWindowDimensions, View, type LayoutRectangle, type StyleProp, type ViewStyle } from 'react-native'
import { Image, type ImageStyle } from 'expo-image'
import { LinearGradient } from 'expo-linear-gradient'
import { outlets } from '../../web/src/feed'
import { clockPieces, countdown, dateOnly, dateTime, day, hostname, initials, lineStats, longestWordEms, outcome, parseDate, playedOn, relative, safeHref, shortName } from '../../web/src/format'
import { bareScore, comingCard, liveLine, playerLine, resultCard, statusLine, teamLine, teamNameSize, wordSize, type GameResult } from '../../web/src/lines'
import type { LatestMoment } from '../../web/src/today'
import { absolute, type Card, type FollowedTeam, type LiveStatus, type NewsItem, type Photo, type Provenance, type RecentResult, type Stat, type UpcomingEvent } from './api'
import { Backdrop } from './art'
import { toneOf, useSports, useTone, type Tone } from './catalog'
import { useNow } from './clock'
import type { RailCard } from './rail'
import { boardFor, MomentKind, onInk, onPaper, type Palette } from './sports'
import { colour, font, GUTTER, num, RADIUS_CARD, RADIUS_TILE, t } from './theme'
import { alpha, breakable, Card as Surface, Dsp, Eyebrow, LiveBadge, Pill, Spinner, WL } from './ui'

const go = (path: string) => () => router.push(path as never)
// The share of the player band the words take beside a photo: they stop where its fade starts to show, so
// they're read on the band (.player-band.with-photo on the web).
const BESIDE_PHOTO = 0.64

/** A game's match page (app/match/), by its address after /match/ ("<source>/<game id>", as the API gives it). */
export function matchHref(address: string) {
  const at = address.indexOf('/')
  return { pathname: '/match/[source]/[game]', params: { source: address.slice(0, at), game: address.slice(at + 1) } } as const
}

/** Opens a game's match page where it has one (`address`), else `otherwise` (a player's page), else nothing. */
export const openMatch = (address?: string | null, otherwise?: string) => () => {
  if (address) router.push(matchHref(address))
  else if (otherwise) router.push(otherwise as never)
}

// ---------------------------------------------------------------- small pieces

/** Initials on the sport's colour (.avatar), until there are headshots. */
export function Avatar({ name, sport, size }: { name: string; sport?: string | null; size?: 'sm' | 'lg' }) {
  const tone = useTone(sport)
  const box = size === 'sm' ? { width: 32, height: 32, borderRadius: 9, f: 11 } : size === 'lg' ? { width: 64, height: 64, borderRadius: 18, f: 20 } : { width: 44, height: 44, borderRadius: 12, f: 14 }
  return (
    <View style={{ width: box.width, height: box.height, borderRadius: box.borderRadius, backgroundColor: tone.sport, alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
      <Text style={{ fontFamily: font[800], fontSize: box.f, letterSpacing: box.f * 0.02, color: tone.on }}>{initials(name)}</Text>
    </View>
  )
}

/** Numbers in white tiles (.stat-grid); `paper`, tiles of the page's paper (inside a white card). */
export function StatGrid({ stats, empty = 'No stats yet.', paper }: { stats?: Stat[]; empty?: string; paper?: boolean }) {
  if (!stats?.length) return <Text style={[t.small, t.muted]}>{empty}</Text>
  return (
    <View style={b.statGrid}>
      {stats.map((x) => (
        <View key={x.label} style={[b.stat, paper && { backgroundColor: colour.bg }]}>
          <Text style={num(34)}>{x.value}</Text>
          <Text style={[t.tiny, { fontFamily: font[700], color: colour.muted }]}>{x.label}</Text>
        </View>
      ))}
    </View>
  )
}

/** The player's numbers from a finished game ("22 PTS · 11 REB · 10 AST") in blocks of their sport's colour. */
export function LineStats({ text, sport }: { text?: string | null; sport?: string | null }) {
  const tone = useTone(sport)
  const stats = lineStats(text)
  if (!stats) return text ? <Text style={t.title}>{text}</Text> : null
  return (
    <View style={b.lineStats}>
      {stats.slice(0, 4).map((x) => (
        <View key={x.label} style={[b.lineStat, { backgroundColor: tone.sport }]}>
          <Text style={[num(36), { color: tone.on }]}>{x.value}</Text>
          <Text style={[t.tag, { color: tone.on, letterSpacing: 0.66 }]}>{x.label}</Text>
        </View>
      ))}
    </View>
  )
}

// ---------------------------------------------------------------- Today's cards

/** A finished game of the fan's on Today (lines.resultCard says what): a block of its sport's colour with one
 * big word, the players and their lines, and their win, draw or loss. The hero leads a day with nothing live.
 * It opens the game's match page, what happened in it, where a source has the game; else the first player's. */
export function ResultCard({ game, hero, now, fill }: { game: GameResult; hero?: boolean; now: number } & RailCard) {
  const sports = useSports()
  const tone = toneOf(sports, game.players[0].sport)
  const c = resultCard(game, Boolean(hero), now, sports.drawn(game.players[0].sport))
  const badge = c.wl ? <WL mark={c.wl} on={tone.on} block={tone.sport} /> : null
  const line = (text: string, strong?: boolean) => <Text key={text} style={[b.resultLine, { color: tone.on, fontFamily: strong ? font[800] : font[700] }]} numberOfLines={2}>{text}</Text>
  const page = game.players.find((p) => p.result.match)?.result.match
  const lead = game.players[0]
  return (
    <Pressable onPress={openMatch(page, lead.team != null ? `/team/${lead.team}` : `/player/${c.r.player_id}`)} style={[b.result, hero && b.resultHero, { backgroundColor: tone.sport }, fill && { width: undefined }]}>
      <Backdrop sport={tone.key} seed={game.key} colour={tone.on} />
      <View style={b.row}>
        {hero ? <Pill label={c.when} bg={tone.on} color={tone.sport} /> : <Eyebrow color={tone.onSoft} lines={1} style={{ flexShrink: 1 }}>{[c.when, c.r.competition].filter(Boolean).join(' · ')}</Eyebrow>}
        {badge}
      </View>
      <Dsp size={wordSize(c.word, hero)} color={tone.on}>{breakable(c.word)}</Dsp>
      <View style={{ gap: hero ? 12 : 2 }}>
        <Dsp size={hero ? 40 : 26} color={tone.on}>{c.who}</Dsp>
        {c.stats ? (
          <View style={{ gap: 4 }}>
            {c.several ? <Text style={[t.tag, { color: tone.onSoft }]}>{lead.team != null ? c.r.player_name : shortName(c.r.player_name)}</Text> : null}
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {c.stats.map((x) => (
                <View key={x.label} style={{ flex: 1, gap: 2 }}>
                  <Dsp size={44} color={tone.on}>{x.value}</Dsp>
                  <Text style={[t.tag, { color: tone.onSoft }]}>{x.label}</Text>
                </View>
              ))}
            </View>
          </View>
        ) : null}
        {!hero && c.match !== c.word ? line(c.match) : null}
        {c.lines.map((l) => line(l, true))}
      </View>
      {hero ? (
        <View style={[b.heroFoot, { borderTopColor: alpha(tone.on, 0.18) }]}>
          {c.match !== c.word ? <View style={{ flex: 1 }}>{line(c.match)}</View> : null}
          {c.r.competition ? <Text style={[b.resultLine, { color: tone.onSoft, textAlign: 'right', marginLeft: 'auto', maxWidth: '40%' }]} numberOfLines={2}>{c.r.competition}</Text> : null}
        </View>
      ) : null}
    </Pressable>
  )
}

/** One of the fan's games still to come, on Today: a block of its sport's colour, as their results are (a big game
 * none of theirs are in is outlined), with the countdown big once it's less than a day away (else the day), then
 * the game and a chip naming the fan's teams and players in it, or saying they follow the game itself (`sport`: its sport, with no players). It opens the game's match page
 * (the line-ups as they come, both sides' players), else the first player's page. */
export function ComingCard({ event, players, seed, sport, teams = [], fill }: { event: UpcomingEvent; players: Card[]; seed: string; sport?: string; teams?: string[] } & RailCard) {
  const now = useNow()
  const tone = useTone(sport ?? players[0]?.player.sport)
  const { big, eyebrow } = comingCard(event, now)
  return (
    <Pressable onPress={openMatch(event.match, players[0] ? `/player/${players[0].player_id}` : undefined)} style={[b.coming, { backgroundColor: tone.sport }, fill && { width: undefined }]}>
      <Backdrop sport={tone.key} seed={seed} colour={tone.on} />
      <Eyebrow color={tone.onSoft} lines={1}>{eyebrow}</Eyebrow>
      <Dsp size={44} color={tone.on}>{big}</Dsp>
      <View style={{ gap: 6 }}>
        <Text style={[b.resultLine, { color: tone.on }]} numberOfLines={2}>{event.title}</Text>
        <Pill small label={[...teams, ...players.map((c) => shortName(c.player.name))].join(' · ') || 'Following this game'} bg={tone.on} color={tone.sport} />
      </View>
    </Pressable>
  )
}

/** A player's next game, on their page: a white card under their band, tagged in their sport's colour, the countdown
 * big. `children` sit at its foot beside when and where it is (the page's refresh button). */
export function NextCard({ event, sport, children }: { event: UpcomingEvent; sport?: string | null; children?: ReactNode }) {
  const tone = useTone(sport)
  const left = countdown(event.start_utc, useNow())
  return (
    <View style={b.next}>
      <View style={b.row}>
        <Pill small label="Next game" bg={tone.sport} color={tone.on} />
        {event.competition ? <Text style={[t.tiny, { fontFamily: font[700], color: colour.muted, textAlign: 'right', flexShrink: 1 }]}>{event.competition}</Text> : null}
      </View>
      <View style={[b.row, { alignItems: 'flex-end' }]}>
        <Text style={[t.title, { fontSize: 20, lineHeight: 26, color: colour.text, flexShrink: 1 }]}>{event.title}</Text>
        {left ? <Dsp size={44} color={colour.text}>{left}</Dsp> : null}
      </View>
      <View style={[b.row, { paddingTop: 12, borderTopWidth: 1, borderTopColor: colour.line, flexWrap: 'wrap' }]}>
        <Text style={[t.small, { fontFamily: font[700], color: colour.text, flexShrink: 1 }]}>{[event.start_utc ? dateTime(event.start_utc) : 'Date to be confirmed', event.venue].filter(Boolean).join(' · ')}</Text>
        {children}
      </View>
    </View>
  )
}

/** One followed player on the Following page: a block of their sport's colour (.tile). */
export function PlayerTile({ card, progress }: { card: Card; progress?: string[] }) {
  const now = useNow()
  const sports = useSports()
  const tone = toneOf(sports, card.player.sport)
  const live = card.live.is_live
  return (
    <Pressable onPress={go(`/player/${card.player_id}`)} style={[b.tile, { backgroundColor: tone.sport }]}>
      <Text style={[b.tileSport, { color: tone.onSoft }]}>{sports.name(card.player.sport) || 'Player'}</Text>
      <View style={b.corner}>{card.status === 'building' ? <Spinner color={tone.on} /> : live ? <LiveBadge /> : null}</View>
      <Dsp size={30} color={tone.on}>{shortName(card.player.name)}</Dsp>
      <Text style={[b.tileStatus, { color: tone.on }]} numberOfLines={2}>{statusLine(card, progress, now, sports.name(card.player.sport))}</Text>
    </Pressable>
  )
}

/** One followed team on the Following page, the web's TeamTile: a block of its sport's colour, as a player's
 * tile, saying its game on now, a game this week or its last result (lines.teamLine). */
export function TeamTile({ team }: { team: FollowedTeam }) {
  const now = useNow()
  const sports = useSports()
  const tone = toneOf(sports, team.sport)
  return (
    <Pressable onPress={go(`/team/${team.team_id}`)} style={[b.tile, { backgroundColor: tone.sport }]}>
      <Text style={[b.tileSport, { color: tone.onSoft }]}>{sports.name(team.sport) || 'Team'} · Team</Text>
      <View style={b.corner}>{team.live?.is_live ? <LiveBadge /> : null}</View>
      <Dsp size={teamNameSize(team.name)} color={tone.on}>{team.name}</Dsp>
      <Text style={[b.tileStatus, { color: tone.on }]} numberOfLines={2}>{teamLine(team, now)}</Text>
    </Pressable>
  )
}

// ---------------------------------------------------------------- the live scoreboard

/** A live game's score and clock as text: a score the research agent read off a web page, or a state no
 * board draws. */
export function PlainScore({ live, pal }: { live: LiveStatus; pal: Palette }) {
  return (
    <View style={[b.row, { alignItems: 'flex-end', flexWrap: 'wrap' }]}>
      <Text style={[num(pal.score), { color: pal.text, flexShrink: 1 }]}>{live.score || '—'}</Text>
      {live.clock ? <Text style={[num(22), { color: pal.clock }]}>{live.clock}</Text> : null}
    </View>
  )
}

/** A live game's score as its sport's board draws it (sports/), or the plain score where none draws this
 * state. `teams`: the followed player's, marked on a board that lists many (a battle royale lobby); `lines`: each
 * of the fan's players' numbers, on a card of several of theirs or a match page; `explore`: the match page's,
 * which the fan may explore where its sport has a way to (BoardProps). */
export function GameBoard({ live, compact, teams, lines, explore, pal }: { live: LiveStatus; compact?: boolean; teams?: string[]; lines?: Stat[][]; explore?: boolean; pal: Palette }) {
  const Board = boardFor(live)
  return Board && live.state ? <Board state={live.state as never} clock={live.clock} compact={compact} teams={teams} lines={lines} explore={explore} live={live} pal={pal} /> : <PlainScore live={live} pal={pal} />
}

/**
 * The live scoreboard (.board): an ink scorebug whatever the sport, drawn by the sport's board. Today's is
 * compact: with a `seed` (its game key) the sport's line art sits faint behind it, and the player's line is
 * one row with the game's `latest` moment under it; a player's page has their line on their sport's colour,
 * and the game's moments. With no `playerName` (a game on a campaign page), the board alone.
 */
export function LiveScoreboard({ live, playerName, sport, teams, lines, compact, seed, latest }: { live: LiveStatus; playerName: string; sport?: string | null; teams?: string[]; lines?: Stat[][]; compact?: boolean; seed?: string; latest?: LatestMoment | null }) {
  const now = useNow()
  const sports = useSports()
  const tone = toneOf(sports, sport ?? live.state?.kind)
  const pal = onInk(tone, compact)
  const url = safeHref(live.source_url)
  return (
    <View style={[b.board, compact && { gap: 12 }]}>
      {seed ? <Backdrop sport={tone.key} seed={seed} colour={tone.ink} /> : null}
      <View style={b.row}>
        <LiveBadge />
        {live.event ? <Eyebrow color={colour.inkMuted} style={{ textAlign: 'right', flexShrink: 1 }}>{live.event}</Eyebrow> : null}
      </View>
      <GameBoard live={live} compact={compact} teams={teams} lines={lines} pal={pal} />
      {!playerName ? null : compact ? <LiveLine live={live} playerName={playerName} latest={latest} tone={tone} /> : <PlayerLine live={live} playerName={playerName} tone={tone} />}
      {!compact && live.moments?.length ? (
        <View style={{ gap: 8 }}>
          {live.moments.slice(0, 5).map((m, i) => (
            <View key={`${m.clock}-${i}`} style={{ flexDirection: 'row', alignItems: 'baseline', gap: 10 }}>
              <Text style={[num(17), { minWidth: 44, color: '#FFFFFF' }]}>{m.clock}</Text>
              <MomentKind kind={m.kind} label={m.kind[0] + m.kind.slice(1).toLowerCase()} />
              <Text style={[t.small, { color: '#FFFFFF', flex: 1 }]}>{m.text}</Text>
            </View>
          ))}
        </View>
      ) : null}
      <View style={b.row}>
        <Text style={[t.tiny, { color: colour.inkMuted, flexShrink: 1 }]}>{live.as_of ? `Updated ${relative(live.as_of, now)}` : 'Live'}{live.source ? ` · ${live.source}` : live.source_url ? ` · ${hostname(live.source_url)}` : ''}</Text>
        {/* A compact board is a tap on its game already (Today's open its match page). */}
        {!compact && (live.match || url) ? (
          <View style={{ flexDirection: 'row', gap: 12 }}>
            {live.match ? <Text onPress={openMatch(live.match)} accessibilityRole="link" style={[t.tiny, t.link, { color: '#FFFFFF' }]}>The match</Text> : null}
            {url ? <Text onPress={() => Linking.openURL(url)} style={[t.tiny, t.link, { color: '#FFFFFF' }]}>Open source</Text> : null}
          </View>
        ) : null}
      </View>
    </View>
  )
}

/** The followed player's line on Today's board, in one row (lines.liveLine), and under it the newest thing
 * that happened to the fan's players in this game, which the fan missed if it came after they last looked. */
function LiveLine({ live, playerName, latest, tone }: { live: LiveStatus; playerName: string; latest?: LatestMoment | null; tone: Tone }) {
  const { numbers, why } = liveLine(live)
  if (!numbers.length && !why && !latest) return null
  return (
    <View style={b.liveLine}>
      {numbers.length || why ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'baseline', columnGap: 8, rowGap: 2 }}>
          <Dsp size={20} color={tone.ink}>{shortName(playerName)}</Dsp>
          <Text style={{ fontFamily: font[700], fontSize: 13, lineHeight: 18, color: '#FFFFFF', flexShrink: 1 }}>{numbers.length ? numbers.join(' · ') : why}</Text>
        </View>
      ) : null}
      {latest ? (
        <Text style={{ fontFamily: font[600], fontSize: 12, lineHeight: 17, color: colour.inkMuted }} numberOfLines={2}>
          <Text style={{ fontFamily: font[800], color: '#FFFFFF' }}>{latest.missed ? 'You missed' : 'Latest'}</Text> {latest.text}
        </Text>
      ) : null}
    </View>
  )
}

/** The followed player's own numbers in the game, on their sport's colour (lines.playerLine). */
function PlayerLine({ live, playerName, tone }: { live: LiveStatus; playerName: string; tone: Tone }) {
  const line = playerLine(live)
  if ('none' in line) return line.none ? <Text style={[t.small, { color: colour.inkMuted }]}>{playerName} {line.none}.</Text> : null
  return (
    <View style={[b.playerLine, { backgroundColor: tone.sport }]}>
      <View style={{ gap: 4 }}>
        <Eyebrow color={tone.onSoft}>{playerName}{line.headline ? '' : ` · ${line.lead.label}`}</Eyebrow>
        <Text style={[num(44), { color: tone.on }]}>{line.lead.value}</Text>
      </View>
      {line.rest.length ? (
        <View style={{ flexDirection: 'row', gap: 8, flex: 1, minWidth: 180 }}>
          {line.rest.map((x) => (
            <View key={x.label} style={{ flex: 1, alignItems: 'center', gap: 4 }}>
              <Text style={[num(24), { color: tone.on }]}>{x.value}</Text>
              <Text style={[t.tiny, { fontFamily: font[700], color: tone.onSoft, textAlign: 'center' }]}>{x.label}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  )
}

/** A game on now that none of the fan's players are in (.live-other): paper, an ink ring, its sport's tag and
 * line art, and its sport's own board in ink, where red is only the clock. The card opens its match page
 * (`match`), but its follow buttons (`children`), which take their own taps. To a screen reader the card isn't
 * one item: its tag and board are the link (`label`), and each follow button is its own. */
export function OtherGameCard({ live, sport, seed, tag, label, match, children, fill }: { live: LiveStatus; sport: string; seed: string; tag: string; label: string; match?: string | null; children?: ReactNode } & RailCard) {
  const sports = useSports()
  const tone = toneOf(sports, sport)
  const open = openMatch(match)
  return (
    <Pressable disabled={!match} onPress={open} accessible={false} focusable={false} importantForAccessibility="no" style={[b.other, fill && { width: undefined }]}>
      <Backdrop sport={tone.key} seed={seed} colour={tone.sport} opacity={0.2} />
      <Pressable disabled={!match} onPress={open} accessibilityRole={match ? 'link' : undefined} accessibilityLabel={label} style={{ flex: 1, gap: 12 }}>
        <Pill small label={tag} bg={tone.sport} color={tone.on} />
        <View style={{ marginVertical: 'auto' }}><GameBoard live={live} compact pal={onPaper(tone)} /></View>
      </Pressable>
      {children}
    </Pressable>
  )
}

// ---------------------------------------------------------------- a player's page

/** Where a section came from and when: "ESPNcricinfo · updated 4 min ago". */
export function SourceNote({ provenance, note }: { provenance?: Provenance; note?: string }) {
  const now = useNow()
  if (!provenance && !note) return null
  const url = safeHref(provenance?.url)
  return (
    <Text style={[t.tiny, t.muted]}>
      {[note, provenance && `${provenance.source} · updated ${relative(provenance.at, now)}`].filter(Boolean).join(' · ')}
      {url ? <> · <Text onPress={() => Linking.openURL(url)} style={t.link}>Profile</Text></> : null}
    </Text>
  )
}

/** Fixtures, the day and time in a block on the left. */
export function UpcomingList({ items, who, empty }: { items: (UpcomingEvent & { players?: string[] })[]; who?: boolean; empty: string }) {
  const now = useNow()
  if (!items.length) return <Empty>{empty}</Empty>
  return (
    <Surface style={b.list}>
      {items.map((u, i) => {
        const d = parseDate(u.start_utc)
        const timed = d && !dateOnly(u.start_utc)
        const clock = timed ? clockPieces(d) : [{ text: '–', period: false }]
        return (
          <View key={`${u.title}-${u.start_utc ?? ''}`} style={[b.fixture, i < items.length - 1 && b.listLine]}>
            {/* A 12-hour clock's AM/PM is set small beside the time, in a column a little wider, so the time never breaks. */}
            <View style={[b.when, clock.some((p) => p.period) && b.whenPeriod]}>
              <Text style={[t.tag, t.muted]}>{d ? d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }) : 'TBC'}</Text>
              <View style={b.clock}>
                {clock.map((p) => <Text key={p.text} numberOfLines={1} style={p.period ? [t.tag, t.muted] : num(22)}>{p.text}</Text>)}
              </View>
            </View>
            <View style={{ flex: 1, gap: 4 }}>
              <Text style={t.title}>{u.title}</Text>
              <Text style={[t.small, t.muted]}>{[who && u.players?.join(', '), u.competition, d ? (timed ? null : 'Time to be confirmed') : 'Date to be confirmed', u.venue].filter(Boolean).join(' · ')}</Text>
              {u.notes ? <Text style={[t.small, t.muted]}>{u.notes}</Text> : null}
            </View>
            {u.status === 'live' ? <LiveBadge /> : d ? <Text style={[t.tag, { color: colour.text }]}>{relative(u.start_utc, now)}</Text> : null}
          </View>
        )
      })}
    </Surface>
  )
}

export function NewsList({ items, who, brief, empty }: { items: (NewsItem & { players?: string[] })[]; who?: boolean; brief?: boolean; empty: string }) {
  if (!items.length) return <Empty>{empty}</Empty>
  return (
    <Surface style={b.list}>
      {items.map((n, i) => {
        const url = safeHref(n.url)
        return (
          <Pressable key={n.url || n.headline} disabled={!url} onPress={() => url && Linking.openURL(url)} style={[b.listItem, i < items.length - 1 && b.listLine, { gap: 4 }]}>
            <Text style={[t.title, { fontSize: 16, lineHeight: 21 }]}>{n.headline}</Text>
            <Text style={[t.tiny, t.muted, { fontFamily: font[700] }]}>{[who && n.players?.join(', '), n.source, outlets(n.outlets), n.published && day(n.published)].filter(Boolean).join(' · ')}</Text>
            {!who && !brief && n.summary ? <Text style={[t.small, t.muted]}>{n.summary}</Text> : null}
          </Pressable>
        )
      })}
    </Surface>
  )
}

export function ResultsList({ items, teams }: { items?: RecentResult[]; teams?: string[] }) {
  if (!items?.length) return <Empty>No recent results found.</Empty>
  return (
    <Surface style={b.list}>
      {items.map((r, i) => {
        const wl = outcome(r.result, teams)
        const score = bareScore(r.result)
        const url = safeHref(r.source_url)
        return (
          <View key={`${r.title}-${r.date ?? ''}`} style={[b.listItem, i < items.length - 1 && b.listLine, { flexDirection: 'row', gap: 12, alignItems: 'flex-start' }]}>
            {wl ? <WL mark={wl} /> : null}
            <View style={{ flex: 1, gap: 4 }}>
              <Text style={t.title} onPress={url ? () => Linking.openURL(url) : undefined}>{r.title}</Text>
              <Text style={[t.small, t.muted]}>{[day(playedOn(r)), r.competition].filter(Boolean).join(' · ')}</Text>
              {!score ? <Text style={[t.small, { fontFamily: font[800] }]}>{r.result}</Text> : null}
              {r.player_contribution ? <Text style={[t.small, { fontFamily: font[800] }]}>{r.player_contribution}</Text> : null}
            </View>
            {score ? <Text style={num(20)}>{score}</Text> : null}
          </View>
        )
      })}
    </Surface>
  )
}

/** Won, drew or lost for the last five results whose outcome is known, newest first. */
export function formMarks(results: RecentResult[], teams?: string[]): ('W' | 'D' | 'L')[] {
  return results.map((r) => outcome(r.result, teams)).filter((m): m is 'W' | 'D' | 'L' => m != null).slice(0, 5)
}

/** The last few results as a row of badges, newest first. */
export function FormStrip({ marks }: { marks: ('W' | 'D' | 'L')[] }) {
  return <View style={{ flexDirection: 'row', gap: 4, flexShrink: 0 }}>{marks.map((m, i) => <WL key={i} mark={m} />)}</View>
}

/** A page being built, or one that failed. Without the research agent (research false) it's a name being
 * looked up in the live sources. */
export function BuildProgress({ card, messages, research = true, onRetry }: { card: Card; messages: string[]; research?: boolean; onRetry?: () => void }) {
  if (card.status === 'failed') {
    return (
      <Surface pad style={{ gap: 12 }}>
        <Text style={t.title}>Couldn't build {card.player.name}'s page</Text>
        <View style={b.errorBox}><Text style={b.errorText}>{card.error || 'The research agent failed.'}</Text></View>
        {onRetry ? <Pressable onPress={onRetry} style={b.primary}><Text style={b.primaryText}>Try again</Text></Pressable> : null}
      </Surface>
    )
  }
  return (
    <Surface pad style={{ gap: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <Spinner />
        <Text style={t.title}>Building {card.player.name}'s page</Text>
      </View>
      <Text style={[t.small, t.muted]}>{research
        ? 'The first time anyone follows a player, the agent researches them. This takes one to two minutes; every later fan gets the shared page instantly.'
        : 'Looking them up in the live sources.'}</Text>
      {messages.map((m, i) => <Text key={i} style={[t.small, i === messages.length - 1 ? { fontFamily: font[700] } : t.muted]}>{m}</Text>)}
    </Surface>
  )
}

/** Where the photos job found the face (.photo.framed on the web): the square around it covers the box, its
 * centre 62% across and halfway down, but never so far that the picture runs past the photo's edges. */
function framed(crop: Photo['crop'], box: LayoutRectangle): ImageStyle {
  if (!crop) return { width: '100%', height: '100%' }
  const side = Math.max(box.width, box.height)
  const width = side / crop.w
  const height = side / crop.h
  return {
    position: 'absolute', width, height,
    left: Math.min(0, Math.max(box.width - width, 0.62 * box.width - (crop.x + crop.w / 2) * width)),
    top: Math.min(0, Math.max(box.height - height, box.height / 2 - (crop.y + crop.h / 2) * height)),
  }
}

/** The top of a player's page (and an athlete's preview): a block of their sport's colour edge to edge, with
 * their photo in its own colours fading in on the right and its credit when there is one. Beside the photo
 * the words stop short of it (b.besidePhoto, also for a note in `children`). The name is as large as fits
 * its longest word on a line in their room (longestWordEms), so it never breaks inside a word: the room
 * the screen leaves them until the words are laid out, then the room they have. */
export function PlayerBand({ name, sport, meta, photo, back, actions, children, topInset }: { name: string; sport?: string | null; meta?: string; photo?: Photo | null; back: ReactNode; actions?: ReactNode; children?: ReactNode; topInset: number }) {
  const sports = useSports()
  const tone = toneOf(sports, sport)
  const [broken, setBroken] = useState<string | null>(null)
  const [box, setBox] = useState<LayoutRectangle | null>(null)
  const [laidOut, setLaidOut] = useState<number | null>(null)
  const screen = useWindowDimensions().width
  const shown = photo && photo.url !== broken ? photo : null
  const room = laidOut ?? (screen - 2 * GUTTER) * (shown ? BESIDE_PHOTO : 1)
  const credit = shown ? safeHref(shown.page) : undefined
  return (
    <View style={[b.band, { backgroundColor: tone.sport, paddingTop: topInset + 16 }]}>
      {shown ? (
        <View pointerEvents="none" style={[b.bandPhoto, { top: topInset + 64 }]} onLayout={(e) => setBox(e.nativeEvent.layout)}>
          {box ? <Image source={{ uri: absolute(shown.url) }} style={framed(shown.crop, box)} contentFit="cover" contentPosition={{ top: '18%', left: '50%' }} onError={() => setBroken(shown.url)} /> : null}
          {/* Fading into the band on its left, as the web's mask does. */}
          <LinearGradient colors={[tone.sport, alpha(tone.sport, 0)]} start={{ x: 0, y: 0.5 }} end={{ x: 0.5, y: 0.5 }} style={StyleSheet.absoluteFill} />
        </View>
      ) : null}
      <View style={b.row}>
        {back}
        <View style={{ flexDirection: 'row', gap: 8 }}>{actions}</View>
      </View>
      <View style={[{ gap: 10 }, shown ? b.besidePhoto : null]} onLayout={(e) => setLaidOut(e.nativeEvent.layout.width)}>
        {sport ? <Text style={[t.tag, { color: tone.onSoft }]}>{sports.name(sport)}</Text> : null}
        <Dsp size={Math.min(56, room / longestWordEms(name))} color={tone.on}>{breakable(name)}</Dsp>
        {meta ? <Text style={{ fontFamily: font[700], fontSize: 15, lineHeight: 21, color: tone.onSoft }}>{meta}</Text> : null}
      </View>
      {children}
      {shown ? <Text onPress={credit ? () => Linking.openURL(credit) : undefined} style={[{ fontFamily: font[700], fontSize: 10, lineHeight: 13, color: tone.onSoft, marginTop: -6 }, b.besidePhoto]}>{shown.credit}</Text> : null}
    </View>
  )
}

/** "Nothing here" in a card (.card.empty). */
export function Empty({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <Surface style={[{ paddingVertical: 28, paddingHorizontal: 20 }, style]}><Text style={[t.small, t.muted, { textAlign: 'center' }]}>{children}</Text></Surface>
}

export const b = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  stat: { flexGrow: 1, flexBasis: 104, gap: 6, padding: 14, borderRadius: 16, backgroundColor: colour.surface, minWidth: 0 },
  lineStats: { flexDirection: 'row', gap: 8 },
  lineStat: { flex: 1, gap: 4, padding: 12, borderRadius: 14 },
  result: { overflow: 'hidden', justifyContent: 'space-between', gap: 10, width: 236, minHeight: 268, flexGrow: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: RADIUS_TILE },
  resultHero: { width: 322, minHeight: 356, padding: 18, borderRadius: 26 },
  resultLine: { fontSize: 13, lineHeight: 17, fontFamily: font[700] },
  heroFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, paddingTop: 10, borderTopWidth: 2 },
  coming: { overflow: 'hidden', justifyContent: 'space-between', gap: 8, width: 204, minHeight: 176, flexGrow: 1, paddingVertical: 14, paddingHorizontal: 16, borderRadius: RADIUS_TILE },  // its sport's colour (ComingCard)
  next: { gap: 14, padding: 18, borderRadius: RADIUS_CARD, backgroundColor: colour.surface },
  tile: { justifyContent: 'space-between', gap: 8, minHeight: 116, padding: 14, borderRadius: RADIUS_TILE },
  tileSport: { fontFamily: font[800], fontSize: 10, letterSpacing: 0.9, textTransform: 'uppercase', paddingRight: 60 },
  tileStatus: { fontFamily: font[800], fontSize: 12, lineHeight: 15.6 },
  corner: { position: 'absolute', top: 12, right: 12 },
  board: { overflow: 'hidden', gap: 16, padding: 18, borderRadius: RADIUS_CARD, backgroundColor: '#0D0D0D' },
  liveLine: { gap: 4, paddingTop: 10, borderTopWidth: 1, borderTopColor: colour.inkLine },
  playerLine: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 16, paddingVertical: 14, paddingHorizontal: 16, borderRadius: 16 },
  other: { overflow: 'hidden', gap: 12, width: 318, flexGrow: 1, paddingVertical: 16, paddingHorizontal: 18, borderRadius: RADIUS_CARD, backgroundColor: colour.surface, borderWidth: 2, borderColor: colour.text },
  list: { paddingVertical: 4, paddingHorizontal: 16 },
  listItem: { paddingVertical: 14, minHeight: 44 },
  listLine: { borderBottomWidth: 1, borderBottomColor: colour.line },
  fixture: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14 },
  when: { minWidth: 64, gap: 2, flexShrink: 0 },
  whenPeriod: { minWidth: 72 },
  clock: { flexDirection: 'row', alignItems: 'baseline', gap: 3 },
  errorBox: { paddingVertical: 14, paddingHorizontal: 16, borderRadius: 14, backgroundColor: colour.dangerBg },
  errorText: { fontFamily: font[600], fontSize: 14, color: colour.danger },
  primary: { alignSelf: 'flex-start', height: 44, paddingHorizontal: 18, borderRadius: 999, backgroundColor: colour.text, justifyContent: 'center' },
  primaryText: { fontFamily: font[800], fontSize: 14, color: colour.bg },
  band: { overflow: 'hidden', gap: 16, marginHorizontal: -16, paddingHorizontal: 16, paddingBottom: 22, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 },
  bandPhoto: { position: 'absolute', right: 0, bottom: 0, width: '46%', overflow: 'hidden' },
  besidePhoto: { maxWidth: `${BESIDE_PHOTO * 100}%` },
})

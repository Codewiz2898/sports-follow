// Today, the web's TodayPage.tsx: the day and the app's name, the fan's players as a strip, their live games as
// scoreboards, everything else on now, their results and their games coming up with the league's big ones after
// them, this week's big moments and the Wire, and players to follow while they follow few. A fan with no players gets the welcome first, once.

import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'
import { Image } from 'expo-image'
import { router } from 'expo-router'
import { useEffect, useMemo, useState } from 'react'
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { AHEAD_DAYS, comingUp, FEW, latestIn, liveGames, notTheirs, results, resultsDay, stillToCome, TAIL, theirEvents } from '../../../web/src/today'
import type { League } from '../api'
import { useAccount } from '../account'
import { useActivity } from '../activity'
import { ComingCard, LiveScoreboard, ResultCard } from '../blocks'
import { useSports } from '../catalog'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { CloseIcon, SearchIcon, UserIcon } from '../icons'
import { Rail } from '../rail'
import { colour, dsp, font, GUTTER } from '../theme'
import { LeagueComingCard, LeagueResultCard, useLeague } from '../today/league'
import { AlsoLive } from '../today/live'
import { StartersSection } from '../today/starters'
import { PlayersStrip } from '../today/strip'
import { ThisWeek, useWeek, Wire } from '../today/week'
import { Btn, Eyebrow, Notice } from '../ui'
import { Page } from '../page'
import { useWelcomed } from '../welcome'
import { LoadFailed } from '../today/load'

const NAME = Constants.expoConfig?.name ?? ''
const NUDGE = 'stann.save-nudge'

/** The page's head: the day, and the app's name as the logo writes it at a title's size; search and account. */
function Head({ now }: { now: number }) {
  const { width } = useWindowDimensions()
  const today = new Date(now).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })
  // As large as its column takes it, up to 54px: the badge and its gap are about 1.15em, each letter 0.5em.
  const column = width - GUTTER * 2 - (44 * 2 + 8) - 12
  const size = Math.min(54, column / (1.15 + 0.5 * NAME.length))
  return (
    <View style={s.head}>
      <View style={{ flex: 1, gap: 6 }}>
        <Eyebrow>{today}</Eyebrow>
        <View accessibilityRole="header" accessibilityLabel={NAME} style={{ flexDirection: 'row', alignItems: 'center', gap: size * 0.18 }}>
          <Image source={require('../../../web/public/icons/mark-192.png')} style={{ width: size * 0.95, height: size * 0.95 }} />
          <Text style={{ fontFamily: font.logo, fontSize: size, lineHeight: size * 1.1, letterSpacing: -size * 0.01, color: colour.text }}>{NAME}</Text>
        </View>
      </View>
      <View style={{ flexDirection: 'row', gap: 8, paddingBottom: 4 }}>
        <Btn kind="icon" icon={<SearchIcon size={20} color={colour.bg} />} accessibilityLabel="Search players" onPress={() => router.push('/search')} />
        <Btn kind="icon" icon={<UserIcon size={20} color={colour.bg} />} accessibilityLabel="Account" onPress={() => router.push('/account')} />
      </View>
    </View>
  )
}

/** Once a fan follows someone without an account: signing in keeps them. Dismissed for good. */
function SaveNudge() {
  const { me } = useAccount()
  const { cards } = useFollowing()
  const [hidden, setHidden] = useState(true)
  useEffect(() => { AsyncStorage.getItem(NUDGE).then((v) => setHidden(v === 'dismissed')).catch(() => {}) }, [])
  if (hidden || !me || me.signed_in || cards.length === 0) return null
  const dismiss = () => { setHidden(true); AsyncStorage.setItem(NUDGE, 'dismissed').catch(() => {}) }
  return (
    <Notice>
      <Pressable onPress={() => router.push('/account')} style={{ flex: 1, gap: 2 }}>
        <Text style={s.title}>Save your players</Text>
        <Text style={s.smallMuted}>Sign in to keep them, and their alerts, on every device.</Text>
      </Pressable>
      <Btn small label="Sign in" onPress={() => router.push('/account')} />
      <Pressable onPress={dismiss} accessibilityLabel="Not now" hitSlop={8} style={{ width: 32, height: 32, alignItems: 'center', justifyContent: 'center' }}>
        <CloseIcon size={16} />
      </Pressable>
    </Notice>
  )
}

/**
 * Today for a fan who follows nobody yet: the big results and the big games coming up in the competitions the
 * app covers (GET /api/league), outlined, each rail TAIL long and its "All N" card the rest. Nothing until the
 * server answers, and a rail with nothing in it isn't there.
 */
function BigGames({ league, now }: { league: League | null; now: number }) {
  if (!league) return null
  const done = league.results.games
  const coming = stillToCome(league.coming.games, now)
  return (
    <>
      {done.length > 0 ? (
        <Rail title="Big results" note={`${resultsDay(league.results.day)} · ${done.length}`} all="Every big result" max={TAIL}>
          {done.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />)}
        </Rail>
      ) : null}
      {coming.length > 0 ? (
        <Rail title="Big games coming up" note={`Next ${AHEAD_DAYS} days · ${coming.length}`} all={`Every big game in the next ${AHEAD_DAYS} days`} max={TAIL}>
          {coming.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />)}
        </Rail>
      ) : null}
    </>
  )
}

export default function TodayScreen() {
  const following = useFollowing()
  const { cards, loaded, failed } = following
  const { items: alerts, seen } = useActivity()
  const week = useWeek()
  // The events the fan's players are in, once their games have loaded: a change looks at the league again.
  const theirKey = useMemo(() => (loaded ? [...theirEvents(cards)].sort((a, b) => a - b).join(',') : null), [cards, loaded])
  const league = useLeague(theirKey)
  const now = useNow()
  const sports = useSports()
  const welcomed = useWelcomed()
  const [refreshing, setRefreshing] = useState(false)

  // A new fan: the welcome first, once.
  useEffect(() => {
    if (loaded && cards.length === 0 && welcomed === false) router.replace('/welcome')
  }, [loaded, cards.length, welcomed])

  const refresh = async () => {
    setRefreshing(true)
    await following.reload()
    setRefreshing(false)
  }

  if (loaded && cards.length === 0) {
    return (
      <Page onRefresh={refresh} refreshing={refreshing}>
        <Head now={now} />
        <AlsoLive mine={[]} />
        <BigGames league={league} now={now} />
        {/* Who's out and who's back among the best-known players. */}
        <Wire rows={week.wire} now={now} />
        <View style={s.hero}>
          <Text style={[dsp(48), { color: '#FFFFFF' }]}>Pick your players</Text>
          <Text style={s.heroText}>Any athlete in any sport: the live score and their own numbers while they play, then fixtures, results and news.</Text>
          <Btn label="Find a player" icon={<SearchIcon size={18} />} colours={{ bg: '#FFFFFF', fg: colour.text, border: '#FFFFFF' }} onPress={() => router.push('/search')} style={{ alignSelf: 'flex-start' }} />
        </View>
        <StartersSection />
      </Page>
    )
  }

  const games = liveGames(cards)
  const done = results(cards, now, (sport) => sports.info(sport).key)
  const coming = comingUp(cards, now)
  // After the fan's own, the big games none of their players are in, outlined.
  const theirs = theirEvents(cards)
  const bigDone = league ? notTheirs(league.results.games, theirs) : []
  const bigComing = league ? stillToCome(notTheirs(league.coming.games, theirs), now) : []
  const resultCount = (done?.games.length ?? 0) + bigDone.length
  // The day the rail's results are from: the fan's, the big ones', or, when those differ, the latest of each.
  const ownDay = done && (done.day ?? 'Since yesterday')
  const bigDay = bigDone.length ? resultsDay(league?.results.day ?? null) : null
  const resultsWhen = ownDay && bigDay && ownDay !== bigDay ? 'Latest' : ownDay ?? bigDay ?? ''
  const comingCount = coming.length + bigComing.length
  return (
    <Page onRefresh={refresh} refreshing={refreshing}>
      <Head now={now} />
      {cards.length > 0 ? <PlayersStrip cards={cards} now={now} /> : null}
      {failed && !loaded ? <LoadFailed /> : null}

      {games.map(([key, group]) => (
        <Pressable key={group[0].player_id} onPress={() => router.push(`/player/${group[0].player_id}`)} style={{ gap: 8 }}>
          <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} compact seed={key} latest={latestIn(alerts, group, seen)} />
          {group.length > 1 ? <Text style={[s.tinyMuted, { fontFamily: font[700] }]}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</Text> : null}
        </Pressable>
      ))}
      {/* Then every other game on now: once the follow list has answered, so a game of the fan's never shows here first. */}
      {loaded || failed ? <AlsoLive mine={games.flatMap(([, group]) => group)} /> : null}

      {resultCount > 0 ? (
        <Rail title={done ? 'Results' : 'Big results'} note={`${resultsWhen} · ${resultCount}`}
          all={done ? `Every result ${done.day ? `on ${done.day}` : 'since yesterday'}${bigDone.length ? ', and the big ones' : ''}` : 'Every big result'} max={(done?.games.length ?? 0) + TAIL}>
          {/* With nothing live, the biggest result leads the page. */}
          {[
            ...(done?.games ?? []).map((g, i) => <ResultCard key={g.key} game={g} hero={i === 0 && !games.length} now={now} />),
            ...bigDone.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />),
          ]}
        </Rail>
      ) : null}

      <SaveNudge />

      {comingCount > 0 ? (
        <Rail title={coming.length ? 'Coming up' : 'Big games coming up'} note={`Next ${AHEAD_DAYS} days · ${comingCount}`}
          all={`Every ${coming.length ? '' : 'big '}game in the next ${AHEAD_DAYS} days${coming.length && bigComing.length ? ', and the big ones' : ''}`} max={coming.length + TAIL}>
          {[
            ...coming.map((g) => <ComingCard key={g.key} event={g.event} players={g.players} seed={g.key} />),
            ...bigComing.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />),
          ]}
        </Rail>
      ) : null}

      {week.posters.length > 0 ? <ThisWeek posters={week.posters} now={now} /> : null}
      <Wire rows={week.wire} now={now} />

      {cards.length > 0 && cards.length < FEW ? <StartersSection /> : null}
    </Page>
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 19.5, color: colour.text },
  small: { fontFamily: font[600], fontSize: 13, color: colour.text },
  smallMuted: { fontFamily: font[500], fontSize: 13, lineHeight: 18, color: colour.muted },
  tinyMuted: { fontFamily: font[500], fontSize: 12, color: colour.muted },
  hero: { overflow: 'hidden', gap: 14, padding: 18, borderRadius: 26, backgroundColor: '#0D0D0D' },
  heroText: { fontFamily: font[600], fontSize: 15, lineHeight: 22, color: '#FFFFFF', maxWidth: 330 },
})

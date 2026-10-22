// Today, the web's TodayPage.tsx: the day and the app's name, a chip of each of the fan's games to show one alone
// (today/filter.tsx), the fan's players as a strip, their live games as scoreboards (each opening its match page)
// and the games they follow by themselves, a link to everything else on now (the Live page), their results, their
// games coming up with the league's big ones after them, this week's big moments and the Wire, the two best of their
// Feed (post.tsx), and players to follow while they follow few, and of their sports they follow nobody in after. The
// fan's sports (Your sports, BHA-653) get chips, and come first in Live now, the big rails and the Live page. A fan
// with no players and no games gets the welcome first, once.

import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'
import { Image } from 'expo-image'
import { router, useIsFocused } from 'expo-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native'
import { gameRow, gamesOnNow, gamesOnToday, narrow, NO_BLOCKS, nothingOn, theirGames, type TodayBlocks } from '../../../web/src/filter'
import { AHEAD_DAYS, comingUp, FEW, followedLive, gamesOn, latestIn, leagueComingUp, liveGames, notTheirs, results, resultsDay, TAIL, teamsLive, theirEvents, type GameOn, type TeamGame } from '../../../web/src/today'
import { yoursFirst, yoursMovedUp } from '../../../web/src/yourSports'
import type { FollowedMatch, League, LeagueGame } from '../api'
import { useAccount } from '../account'
import { useActivity } from '../activity'
import { ComingCard, LiveScoreboard, openMatch, ResultCard } from '../blocks'
import { useSports } from '../catalog'
import { useNow } from '../clock'
import { useFollowing } from '../following'
import { CloseIcon, SearchIcon, UserIcon } from '../icons'
import { Rail } from '../rail'
import { colour, dsp, font, GUTTER } from '../theme'
import { LeagueComingCard, LeagueResultCard, useLeague } from '../today/league'
import { GameChips, NothingIn, useGame } from '../today/filter'
import { AlsoLive, MoreLive, useLive } from '../today/live'
import { StartersSection } from '../today/starters'
import { PlayersStrip } from '../today/strip'
import { ThisWeek, useWeek, Wire } from '../today/week'
import { Btn, ErrorBoundary, Eyebrow, Notice } from '../ui'
import { Page } from '../page'
import { FromYourPlayers } from '../feedLayouts'
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
          <Text style={{ fontFamily: font.logo, fontSize: size, lineHeight: size * 1.1, letterSpacing: -size * 0.01, color: colour.text, textTransform: 'lowercase' }}>{NAME}</Text>
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
  const { cards, teams } = useFollowing()
  const [hidden, setHidden] = useState(true)
  useEffect(() => { AsyncStorage.getItem(NUDGE).then((v) => setHidden(v === 'dismissed')).catch(() => {}) }, [])
  if (hidden || !me || me.signed_in || cards.length + teams.length === 0) return null
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

/** A rail's note, and "yours first" where the fan's sports (`yours`) come before others' in it (the web's yoursNote). */
const yoursNote = (note: string, yours: boolean) => (yours ? `${note} · yours first` : note)

/**
 * Today for a fan who follows nobody yet: the big results and the big games coming up in the competitions the
 * app covers (GET /api/league), outlined, each rail TAIL long and its "All N" card the rest, the sports taking
 * turns, the fan's sports first (`ahead`: whether theirs went ahead of others' in each). `done` and `coming` are the
 * league's, narrowed to the game picked. Nothing until the server answers, and a rail with nothing in it isn't there.
 */
function BigGames({ league, done, coming, now, ahead }: { league: League | null; done: LeagueGame[]; coming: LeagueGame[]; now: number; ahead: (games: LeagueGame[]) => boolean }) {
  if (!league) return null
  return (
    <>
      {done.length > 0 ? (
        <Rail title="Big results" note={yoursNote(`${resultsDay(league.results.day)} · ${done.length}`, ahead(done))} all="Every big result" max={TAIL}>
          {done.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />)}
        </Rail>
      ) : null}
      {coming.length > 0 ? (
        <Rail title="Big games coming up" note={yoursNote(`Next ${AHEAD_DAYS} days · ${coming.length}`, ahead(coming))} all={`Every big game in the next ${AHEAD_DAYS} days`} max={TAIL}>
          {coming.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />)}
        </Rail>
      ) : null}
    </>
  )
}

/** The games of the fan's teams that are on now and none of their players is in, nor followed by itself, each its
 * board, opening its match page (the web's TeamsLive). */
function TeamsLive({ games }: { games: TeamGame[] }) {
  return games.map((g) => (
    <Pressable key={g.key} onPress={openMatch(g.live.match, `/team/${g.team_id}`)} accessibilityRole="link" style={{ gap: 8 }}>
      <LiveScoreboard live={g.live} playerName="" sport={g.sport} teams={g.names} compact seed={g.key} />
      <Text style={[s.tinyMuted, { fontFamily: font[700] }]}>Following {g.names.join(' and ')}</Text>
    </Pressable>
  ))
}

/** The games the fan follows by themselves that are on now and none of their players is in, each its board,
 * opening its match page. */
function FollowedLive({ matches }: { matches: FollowedMatch[] }) {
  return matches.map((m) => (m.live ? (
    <Pressable key={m.event_id} onPress={openMatch(m.match)} accessibilityRole="link" style={{ gap: 8 }}>
      <LiveScoreboard live={m.live} playerName="" sport={m.sport} compact seed={`event:${m.event_id}`} />
      <Text style={[s.tinyMuted, { fontFamily: font[700] }]}>{m.held ? 'Following this game · alerts paused for now' : 'Following this game'}</Text>
    </Pressable>
  ) : null))
}

export default function TodayScreen() {
  const following = useFollowing()
  const { cards, loaded, failed, matches, matchesLoaded, teams, sports: yours } = following
  const { items: alerts, seen } = useActivity()
  const week = useWeek()
  const live = useLive()
  const [pulled, setPulled] = useState(0)
  // The events the fan's players are in, once their games have loaded: a change looks at the league again.
  const theirKey = useMemo(() => (loaded ? [...theirEvents(cards, teams)].sort((a, b) => a - b).join(',') : null), [cards, teams, loaded])
  const league = useLeague(theirKey)
  // As the web's: a block that couldn't be shown says so, the rest of Today stays; it tries again on a newer read.
  const railKey = useMemo(() => [cards, teams, matches, league], [cards, teams, matches, league])
  const now = useNow()
  const sports = useSports()
  const welcomed = useWelcomed()
  const focused = useIsFocused()
  const { epoch } = useAccount()
  // A visit to Today: it stays mounted under every page opened from it, so its Players to follow starts over each time
  // it's back in front, and for another fan (the web's unmounts with the page).
  const [visit, setVisit] = useState(0)
  const wasFocused = useRef(focused)
  useEffect(() => {
    if (focused && !wasFocused.current) setVisit((n) => n + 1) // back in front: not the first time it shows
    wasFocused.current = focused
  }, [focused])
  const [refreshing, setRefreshing] = useState(false)

  // A fan who follows only games by themselves, or only teams, has a Today of their own too.
  const nobody = loaded && matchesLoaded && cards.length === 0 && matches.length === 0 && teams.length === 0
  // A new fan: the welcome first, once; not while Today is under a page opened straight from a link (_layout.tsx's
  // initialRouteName), which the welcome would replace: a campaign's.
  useEffect(() => {
    if (nobody && welcomed === false && focused) router.replace('/welcome')
  }, [nobody, welcomed, focused])

  const keyOf = (sport?: string | null) => sports.info(sport).key
  const games = liveGames(cards)
  const done = results(cards, now, sports.drawn, teams)
  // After the fan's own, the big games none of their players are in, outlined. Once the follow list has
  // answered: a rail of only big games that the fan's own then come in front of would stay on the big ones
  // (a snapped row keeps its card in view), and one of the fan's own would show as a big game until then.
  // The games they follow by themselves are theirs too: on Today as such, not again among the big ones.
  const theirs = new Set([...theirEvents(cards, teams), ...matches.map((m) => m.event_id)])
  const big = loaded || failed ? league : null
  // The games of every sport, the fan's sports' first (yourSports.ts): the big ones each lot as the server ranks it.
  const onFirst = (list: GameOn[]) => yoursFirst(list, (x) => x.game.sport, yours, keyOf)
  const bigOrdered = (list: LeagueGame[], arrange?: (lot: LeagueGame[]) => LeagueGame[]) => yoursFirst(list, (g) => g.sport, yours, keyOf, arrange)
  const ahead = (list: LeagueGame[]) => yoursMovedUp(list, (g) => g.sport, yours, keyOf)
  // Everything Today shows, block by block; then only the game picked's (filter.ts).
  const all: TodayBlocks = nobody ? {
    ...NO_BLOCKS,
    alsoLive: onFirst(gamesOn(live, []).others),
    bigResults: bigOrdered(league?.results.games ?? []),
    bigComing: league ? bigOrdered(league.coming.games, (lot) => leagueComingUp(lot, new Set(), now)) : [],
    wire: week.wire,
  } : {
    cards, teams,
    live: games,
    followed: followedLive(matches, cards),
    teamGames: teamsLive(teams, cards, matches),
    alsoLive: [],
    // Only once the follow list has answered, so a game of the fan's is never counted as another.
    moreLive: loaded || failed ? gamesOn(live, games.flatMap(([, group]) => group)).others : [],
    results: done?.games ?? [],
    bigResults: big ? bigOrdered(notTheirs(big.results.games, theirs)) : [],
    coming: comingUp(cards, now, matches, teams),
    bigComing: big ? bigOrdered(big.coming.games, (lot) => leagueComingUp(lot, theirs, now)) : [],
    posters: week.posters,
    wire: week.wire,
  }
  const row = gameRow(sports.listed(), gamesOnToday(all, keyOf), theirGames(all, keyOf, yours))
  const [game, pick] = useGame(row)
  const shown = narrow(all, game?.key ?? null, keyOf)
  const shownKey = useMemo(() => [railKey, game?.key], [railKey, game?.key]) // and a block that failed tries again on a pick

  const refresh = async () => {
    setRefreshing(true)
    setPulled((n) => n + 1)
    await Promise.all([following.reload(), following.reloadMatches()])
    setRefreshing(false)
  }
  const filter = <GameChips row={row} game={game} live={gamesOnNow(all, keyOf)} onPick={pick} />

  if (nobody) {
    return (
      <Page onRefresh={refresh} refreshing={refreshing}>
        <Head now={now} />
        {filter}
        <AlsoLive games={shown.alsoLive} />
        <ErrorBoundary resetKey={shownKey}>
          <BigGames league={league} done={shown.bigResults} coming={shown.bigComing} now={now} ahead={ahead} />
        </ErrorBoundary>
        {/* Who's out and who's back among the best-known players. */}
        <ErrorBoundary resetKey={week}>
          <Wire rows={shown.wire} now={now} />
        </ErrorBoundary>
        <View style={s.hero}>
          <Text style={[dsp(48), { color: colour.text }]}>Pick your players</Text>
          <Text style={s.heroText}>Any athlete in any sport: the live score and their own numbers while they play, then fixtures, results and news.</Text>
          <Btn label="Find a player" icon={<SearchIcon size={18} color={colour.bg} />} kind="primary" onPress={() => router.push('/search')} style={{ alignSelf: 'flex-start' }} />
        </View>
        <StartersSection key={`${epoch}:${visit}`} few game={game?.key ?? null} />
      </Page>
    )
  }

  const own = shown.results
  const resultCount = own.length + shown.bigResults.length
  // The day the rail's results are from: the fan's, the big ones', or, when those differ, the latest of each.
  const ownDay = own.length ? done?.day ?? 'Since yesterday' : null
  const bigDay = shown.bigResults.length ? resultsDay(league?.results.day ?? null) : null
  const resultsWhen = ownDay && bigDay && ownDay !== bigDay ? 'Latest' : ownDay ?? bigDay ?? ''
  const comingCount = shown.coming.length + shown.bigComing.length
  return (
    <Page onRefresh={refresh} refreshing={refreshing}>
      <Head now={now} />
      {filter}
      {shown.cards.length + shown.teams.length > 0 ? (
        <ErrorBoundary resetKey={shownKey}>
          <PlayersStrip cards={shown.cards} teams={shown.teams} now={now} />
        </ErrorBoundary>
      ) : null}
      {failed && !loaded ? <LoadFailed /> : null}
      {game && nothingOn(shown) ? <NothingIn game={game} onAll={() => pick(null)} /> : null}

      {/* Each opens its match page; a live score the research agent read names no game, and opens the player's page. */}
      {shown.live.map(([key, group]) => (
        <ErrorBoundary key={group[0].player_id} resetKey={group[0].live}>
          <Pressable onPress={openMatch(group[0].live.match, `/player/${group[0].player_id}`)} style={{ gap: 8 }}>
            <LiveScoreboard live={group[0].live} playerName={group[0].player.name} sport={group[0].player.sport} teams={group.flatMap((c) => c.player.teams ?? [])} lines={group.map((c) => c.live.player_stats)} compact seed={key} latest={latestIn(alerts, group, seen)} />
            {group.length > 1 ? <Text style={[s.tinyMuted, { fontFamily: font[700] }]}>Also in this game: {group.slice(1).map((c) => c.player.name).join(', ')}</Text> : null}
          </Pressable>
        </ErrorBoundary>
      ))}
      {/* Then the games they follow by themselves, nobody of theirs in them. */}
      <ErrorBoundary resetKey={shownKey}>
        <FollowedLive matches={shown.followed} />
      </ErrorBoundary>
      {/* And their teams' games, nobody of theirs in them, not followed by themselves. */}
      <ErrorBoundary resetKey={shownKey}>
        <TeamsLive games={shown.teamGames} />
      </ErrorBoundary>
      {/* Only the fan's own games are on Today; every other game on now is a tap away, on the Live page. */}
      {/* The Live page puts the fan's sports first, in their order: the link names the one that leads it. */}
      <MoreLive games={shown.moreLive} theirsOn={shown.live.length > 0} game={game?.key ?? null}
        first={yoursMovedUp(shown.moreLive, (x) => x.game.sport, yours, keyOf) ? sports.info(yours.find((k) => shown.moreLive.some((x) => keyOf(x.game.sport) === k))).label : null} />

      <ErrorBoundary resetKey={shownKey}>
        {resultCount > 0 ? (
          <Rail title={own.length ? 'Results' : 'Big results'} note={yoursNote(`${resultsWhen} · ${resultCount}`, !own.length && ahead(shown.bigResults))}
            all={own.length ? `Every result ${done?.day ? `on ${done.day}` : 'since yesterday'}${shown.bigResults.length ? ', and the big ones' : ''}` : 'Every big result'} max={own.length + TAIL}>
            {/* With nothing live, the biggest result leads the page. */}
            {[
              ...own.map((g, i) => <ResultCard key={g.key} game={g} hero={i === 0 && !shown.live.length} now={now} />),
              ...shown.bigResults.map((g) => <LeagueResultCard key={g.key} game={g} now={now} />),
            ]}
          </Rail>
        ) : null}
      </ErrorBoundary>

      <SaveNudge />

      <ErrorBoundary resetKey={shownKey}>
        {comingCount > 0 ? (
          <Rail title={shown.coming.length ? 'Coming up' : 'Big games coming up'} note={yoursNote(`Next ${AHEAD_DAYS} days · ${comingCount}`, !shown.coming.length && ahead(shown.bigComing))}
            all={`Every ${shown.coming.length ? '' : 'big '}game in the next ${AHEAD_DAYS} days${shown.coming.length && shown.bigComing.length ? ', and the big ones' : ''}`} max={shown.coming.length + TAIL}>
            {[
              ...shown.coming.map((g) => <ComingCard key={g.key} event={g.event} players={g.players} seed={g.key} sport={g.sport} teams={g.teams} />),
              ...shown.bigComing.map((g) => <LeagueComingCard key={g.key} game={g} now={now} />),
            ]}
          </Rail>
        ) : null}
      </ErrorBoundary>

      <ErrorBoundary resetKey={week}>
        {shown.posters.length > 0 ? <ThisWeek posters={shown.posters} now={now} /> : null}
      </ErrorBoundary>
      <ErrorBoundary resetKey={week}>
        <Wire rows={shown.wire} now={now} />
      </ErrorBoundary>

      {/* What fans and the players themselves shared: after the day's facts, two posts tall, so they don't push
          what's next down the page; the game picked's alone. */}
      <ErrorBoundary resetKey={shownKey}>
        <FromYourPlayers now={now} again={pulled} game={game?.key ?? null} />
      </ErrorBoundary>

      {/* While they follow few players, every sport's; then their sports' they follow nobody in yet. */}
      <StartersSection key={`${epoch}:${visit}`} few={cards.length > 0 && cards.length < FEW} game={game?.key ?? null} />
    </Page>
  )
}

const s = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  title: { fontFamily: font[700], fontSize: 15, lineHeight: 19.5, color: colour.text },
  small: { fontFamily: font[600], fontSize: 13, color: colour.text },
  smallMuted: { fontFamily: font[500], fontSize: 13, lineHeight: 18, color: colour.muted },
  tinyMuted: { fontFamily: font[500], fontSize: 12, color: colour.muted },
  hero: { overflow: 'hidden', gap: 14, padding: 18, borderRadius: 26, backgroundColor: colour.surface },
  heroText: { fontFamily: font[600], fontSize: 15, lineHeight: 22, color: colour.text, maxWidth: 330 },
})

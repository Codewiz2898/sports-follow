// One player's page, the web's PlayerPage.tsx: a band of their sport's colour edge to edge with share, alerts and
// follow, then Overview (the live board or next game, the last game, what fans shared, form, numbers, news,
// about), Matches, Stats and the Feed (feed.tsx). Always from the full card, read again every 30 s while the page
// is open (every 3 s while it's built). ?tab=feed opens the Feed (Today's "From your players" links there).

import Constants from 'expo-constants'
import { router, useFocusEffect, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useState } from 'react'
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { LEVELS, levelText } from '../../../../web/src/alerts'
import { day, playedOn, relative, resultOutcome, safeHref, shortName } from '../../../../web/src/format'
import { bareScore, highlight, lastChecked, liveLag, liveUpdates, newsOf, nextGame, pendingLine, profileFacts } from '../../../../web/src/lines'
import { sharePlayer } from '../../../../web/src/share'
import { useAccount } from '../../account'
import { useActivity } from '../../activity'
import { api, type AboutSource, type Card, type Feed } from '../../api'
import { BuildProgress, formMarks, FormStrip, LineStats, LiveScoreboard, NewsList, NextCard, PlayerBand, SourceNote, StatGrid, UpcomingList } from '../../blocks'
import { toneOf, useSports } from '../../catalog'
import { useNow } from '../../clock'
import { FeedTab, FromTheFans, useFeed } from '../../feed'
import { useFollowing } from '../../following'
import { BackIcon, BellIcon, CheckIcon, PlusIcon, RefreshIcon } from '../../icons'
import { ResultsHistory, Segmented, StatsWithForm } from '../../results'
import { ShareButton } from '../../share'
import { colour, font, num, t } from '../../theme'
import { Btn, Card as Surface, ErrorBoundary, ErrorBox, Eyebrow, Pill, SectionHead, Spinner, WL } from '../../ui'
import { Page } from '../../page'

type Tab = 'overview' | 'matches' | 'stats' | 'feed'
type MatchesView = 'upcoming' | 'results'

const APP = Constants.expoConfig?.name ?? ''
const EVERY_MS = 30_000
const BUILDING_MS = 3_000

export default function PlayerScreen() {
  const params = useLocalSearchParams<{ id: string; tab?: string }>()
  const playerId = /^\d+$/.test(params.id ?? '') ? Number(params.id) : NaN
  const insets = useSafeAreaInsets()
  const sports = useSports()
  const following = useFollowing()
  const isFollowing = following.isFollowing(playerId)
  const research = useAccount().config?.research !== false // without the agent a failed page isn't tried again
  const { muted, setMuted } = useActivity()
  const now = useNow()
  const [card, setCard] = useState<Card | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null) // a follow, retry or alert change the server refused
  const [tab, setTab] = useState<Tab>(params.tab === 'feed' ? 'feed' : 'overview')
  const [matches, setMatches] = useState<MatchesView | null>(null)
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  // The card, and again every 30 s while the page is in front (every 3 s while it's being built).
  useFocusEffect(useCallback(() => {
    if (!Number.isFinite(playerId)) { setError('No such player.'); return }
    let gone = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = () => api.card(playerId)
      .then((c) => { if (!gone) { setCard((prev) => (!prev || c.version >= prev.version ? c : prev)); setError(null) } return c })
      .catch((e: Error) => { if (!gone) setError((prev) => prev ?? e.message); return null })
      .then((c) => { if (!gone) timer = setTimeout(tick, c?.status === 'building' ? BUILDING_MS : EVERY_MS) })
    tick()
    return () => { gone = true; clearTimeout(timer) }
  }, [playerId]))
  useEffect(() => { if (params.tab === 'feed') setTab('feed') }, [params.tab]) // a link to this page's Feed, while on it

  // The Feed reads rows, never a model: it loads with the page, for Overview's "From the fans" and the tab.
  const feed = useFeed(playerId, card?.player.name ?? '', (card ? newsOf(card) : []).map((n) => n.url).join(' '))

  if (!card) {
    return (
      <Page>
        {error ? <ErrorBox>{error}</ErrorBox> : <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}><Spinner /><Text style={[t.body, t.muted]}>Loading…</Text></View>}
      </Page>
    )
  }

  const p = card.player
  const tone = toneOf(sports, p.sport)
  const retired = p.status === 'retired'
  const level = following.byId(playerId)?.alerts ?? card.alerts ?? 'key'
  // A follow, retry or alert change the server turns down says why, under the band.
  const attempt = (work: () => Promise<unknown>) => {
    setFailure(null)
    return work().catch((e: Error) => setFailure(e.message))
  }
  const retry = () => attempt(async () => setCard(await following.follow({ player_id: playerId })))
  const toggleFollow = async () => {
    setBusy(true)
    await attempt(async () => {
      if (isFollowing) { await following.unfollow(playerId); setAlertsOpen(false) } else await following.follow({ player_id: playerId })
    })
    setBusy(false)
  }
  const tabs: [Tab, string][] = retired
    ? [['overview', 'Overview'], ['stats', 'Career'], ['matches', 'Results'], ['feed', 'Feed']]
    : [['overview', 'Overview'], ['matches', 'Matches'], ['stats', 'Stats'], ['feed', 'Feed']]
  const onBand = { fg: tone.on, border: tone.on, bg: 'transparent' }
  const filled = { fg: tone.sport, border: tone.on, bg: tone.on }
  const eventId = card.live.is_live ? card.live.event_id : null

  return (
    <Page top={false} bar={tone.sport}>
      <PlayerBand
        topInset={insets.top}
        name={p.name}
        sport={p.sport}
        photo={p.photo}
        meta={[...(p.teams ?? []).slice(0, 2), p.role, retired && 'Retired'].filter(Boolean).join(' · ')}
        back={<Btn kind="icon" accessibilityLabel="Back" colours={{ bg: tone.on }} icon={<BackIcon size={20} color={tone.sport} />} onPress={() => (router.canGoBack() ? router.back() : router.replace('/'))} />}
        actions={
          <>
            <ShareButton {...sharePlayer(playerId, p.name, APP)} colours={{ bg: tone.on, fg: tone.sport }} />
            {isFollowing ? (
              <Btn kind="icon" accessibilityLabel={`Alerts: ${LEVELS.find((l) => l.value === level)?.label}`} colours={{ bg: tone.on }} icon={<BellIcon size={20} color={tone.sport} />} onPress={() => setAlertsOpen((o) => !o)} style={alertsOpen ? { borderWidth: 3, borderColor: tone.sport } : undefined} />
            ) : null}
            <Btn label={isFollowing ? 'Following' : 'Follow'} icon={isFollowing ? <CheckIcon size={16} color={tone.sport} /> : <PlusIcon size={16} color={tone.on} />} colours={isFollowing ? filled : onBand} busy={busy} onPress={toggleFollow} />
          </>
        }
      >
        {alertsOpen && isFollowing ? (
          <View style={s.alertPanel}>
            <View style={s.row}>
              <Text style={t.title}>Alerts for {shortName(p.name)}</Text>
              <Text onPress={() => router.push({ pathname: '/notifications', params: { tab: 'settings' } })} style={[t.small, t.link]}>Settings</Text>
            </View>
            <Segmented full options={LEVELS.map((l) => [l.value, l.short])} value={level} onChange={(l) => { attempt(() => following.setAlerts(playerId, l)) }} />
            <Text style={[t.small, t.muted]}>{levelText(level, sports.info(p.sport)).hint}</Text>
            {eventId != null ? (
              <Btn small label={muted.has(eventId) ? 'Unmute this game' : 'Mute this game'} style={{ alignSelf: 'flex-start' }} onPress={() => { attempt(() => setMuted(eventId, !muted.has(eventId))) }} />
            ) : null}
          </View>
        ) : null}
      </PlayerBand>
      {failure ? <ErrorBox>{failure}</ErrorBox> : null}

      {card.status !== 'ready' ? (
        <BuildProgress card={card} messages={[]} onRetry={research ? retry : undefined} research={research} />
      ) : (
        <>
          {card.pending ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Spinner />
              <Text style={[t.small, t.muted, { flex: 1 }]}>{pendingLine(card)}</Text>
            </View>
          ) : null}
          {card.pending_error ? <Text style={[t.small, t.muted]}>News and background couldn't be gathered: {card.pending_error}</Text> : null}

          <View style={s.tabs} accessibilityRole="tablist">
            {tabs.map(([id, label]) => (
              <Pressable key={id} accessibilityRole="tab" accessibilityState={{ selected: tab === id }} onPress={() => { setTab(id); setMatches(null) }} style={[s.tab, tab === id && s.tabOn]}>
                <Text style={[s.tabText, tab === id && { color: colour.text }]}>{label}</Text>
              </Pressable>
            ))}
          </View>

          {/* As the web's: a tab that couldn't be shown says so, under the band and the tabs; a newer card tries again. */}
          <ErrorBoundary resetKey={`${tab}:${card.version}`}>
            {tab === 'overview' ? <Overview card={card} feed={feed.feed} show={(x, view) => { setTab(x); setMatches(view ?? null) }} onError={setFailure} /> : null}
            {tab === 'matches' ? <Matches card={card} view={matches} setView={setMatches} /> : null}
            {tab === 'stats' ? <View style={{ gap: 10 }}><StatsWithForm card={card} /></View> : null}
            {tab === 'feed' ? <FeedTab feed={feed.feed} error={feed.error} name={p.name} sport={p.sport} photo={p.photo} now={now} onHide={feed.hide} /> : null}
          </ErrorBoundary>

          <Text style={[t.tiny, t.muted]}>
            {/* A page from a live source alone (no research agent yet, or none in this build) was never "built". */}
            {card.built_at ? `Page built ${relative(card.built_at, now)} from ${card.sources?.length ?? 0} sources. ` : ''}{liveLag(card)}{' '}
            <Text onPress={() => router.push('/credits')} style={t.link}>Credits</Text>
          </Text>
        </>
      )}
    </Page>
  )
}

/** The first tab: what's happening now or next, the last game, form, the key numbers and the news. */
function Overview({ card, feed, show, onError }: { card: Card; feed: Feed | null; show: (t: Tab, view?: MatchesView) => void; onError: (message: string) => void }) {
  const research = useAccount().config?.research !== false
  const sports = useSports()
  const { muted, setMuted } = useActivity()
  const now = useNow()
  const p = card.player
  const retired = p.status === 'retired'
  const next = nextGame(card, now)
  const checked = lastChecked(card)
  const results = card.recent_results ?? []
  const marks = formMarks(results, p.teams)
  const last = results[0]
  const wl = last && resultOutcome(last, p.teams)
  const word = last && highlight(sports.drawn(p.sport), last.player_contribution)
  const score = last && bareScore(last.result)
  const sportName = sports.name(p.sport) || 'this sport'
  const news = newsOf(card)
  const facts = profileFacts(p)

  return (
    <>
      {card.live.is_live ? (
        <>
          <LiveScoreboard live={card.live} playerName={shortName(p.name)} sport={p.sport} teams={p.teams} />
          {card.live.event_id != null && muted.has(card.live.event_id) ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text style={[t.small, t.muted]}>Alerts from this game are muted.</Text>
              <Btn kind="link" label="Unmute" onPress={() => { setMuted(card.live.event_id!, false).catch((e: Error) => onError(e.message)) }} />
            </View>
          ) : null}
          <View style={s.row}>
            <Text style={[t.tiny, t.muted, { flex: 1 }]}>{liveUpdates(card)}</Text>
            <RefreshButton card={card} />
          </View>
        </>
      ) : retired ? null : next ? (
        <NextCard event={next} sport={p.sport}><RefreshButton card={card} /></NextCard>
      ) : card.live.available === false ? (
        <Surface pad style={{ gap: 4 }}>
          <Text style={t.title}>No live scores for {sportName} yet</Text>
          <Text style={[t.small, t.muted]}>{research ? 'News, results and upcoming games on this page update a few times a day.' : "News, results and upcoming games on this page are as they were found, and aren't updated now."}</Text>
        </Surface>
      ) : (
        <Surface pad style={s.row}>
          <View style={{ gap: 4, flexShrink: 1 }}>
            <Text style={t.title}>Not playing right now</Text>
            <Text style={[t.small, t.muted]}>{checked ? `Checked ${relative(checked, now)}` : 'No upcoming games found'}</Text>
          </View>
          <RefreshButton card={card} />
        </Surface>
      )}
      {!card.live.is_live && !retired && next && card.live.available === false ? (
        <Text style={[t.tiny, t.muted]}>No live scores for {sportName} yet: {research ? 'results and news update a few times a day.' : "results and news aren't updated now."}</Text>
      ) : null}

      {last ? (
        <View style={s.game}>
          <View style={s.row}>
            <Eyebrow>Last game</Eyebrow>
            <Text style={[t.tiny, t.muted, { fontFamily: font[700], textAlign: 'right', flexShrink: 1 }]}>{[last.competition, day(playedOn(last))].filter(Boolean).join(' · ')}</Text>
          </View>
          <View style={[s.row, { justifyContent: 'flex-start' }]}>
            {wl ? <WL mark={wl} /> : null}
            <Text style={[t.title, { flex: 1 }]}>{last.title}</Text>
            {score ? <Text style={num(26)}>{score}</Text> : null}
          </View>
          {!score ? <Text style={[t.small, { fontFamily: font[800] }]}>{last.result}</Text> : null}
          <LineStats text={last.player_contribution} sport={p.sport} />
          <View style={s.row}>
            {word ? <Pill label={word} color={colour.text} outline /> : <View />}
            <Btn kind="link" label="All results" onPress={() => show('matches', 'results')} />
          </View>
        </View>
      ) : null}

      <FromTheFans feed={feed} name={p.name} sport={p.sport} now={now} onAll={() => show('feed')} />

      {marks.length >= 3 ? (
        <Surface pad style={s.row}>
          <View style={{ gap: 2 }}>
            <Eyebrow>Form</Eyebrow>
            <Text style={[t.small, { fontFamily: font[700] }]}>Last {marks.length}, newest first</Text>
          </View>
          <FormStrip marks={marks} />
        </Surface>
      ) : null}

      {card.season_stats?.length ? (
        <View style={{ gap: 10 }}>
          <SectionHead title={retired ? 'Career' : 'Numbers'} right={<Btn kind="link" label="All stats" onPress={() => show('stats')} />} />
          <StatGrid stats={card.season_stats.slice(0, 6)} />
          <SourceNote provenance={card.provenance?.season_stats} note={card.season_stats_note} />
        </View>
      ) : null}

      {news.length > 0 ? (
        <View style={{ gap: 10 }}>
          <SectionHead title="News" right={news.length > 2 ? <Btn kind="link" label="All news" onPress={() => show('feed')} /> : undefined} />
          <NewsList items={news.slice(0, 2)} brief empty="" />
        </View>
      ) : null}

      {p.summary || facts.length > 0 ? (
        <View style={{ gap: 10 }}>
          <SectionHead title="About" />
          {facts.length > 0 ? <Text style={[t.small, t.muted]}>{facts.join(' · ')}</Text> : null}
          {p.summary ? <Text style={t.body}>{p.summary}</Text> : null}
          {p.summary && p.about ? <AboutCredit about={p.about} /> : null}
          {p.disambiguation ? <Text style={[t.tiny, t.muted]}>{p.disambiguation}</Text> : null}
        </View>
      ) : null}
    </>
  )
}

/** "From Wikipedia, CC BY-SA 4.0" under text taken from it, each a link: the licence asks for it wherever the text shows. */
function AboutCredit({ about }: { about: AboutSource }) {
  const page = safeHref(about.url)
  const licence = safeHref(about.licence_url)
  return (
    <Text style={[t.tiny, t.muted]}>
      From {page ? <Text accessibilityRole="link" onPress={() => { Linking.openURL(page).catch(() => {}) }} style={t.link}>{about.source}</Text> : about.source}
      {', '}{licence ? <Text accessibilityRole="link" onPress={() => { Linking.openURL(licence).catch(() => {}) }} style={t.link}>{about.licence}</Text> : about.licence}
    </Text>
  )
}

/** Ask the source again, for when a game should be on and isn't showing. */
function RefreshButton({ card, colour: ink = colour.text }: { card: Card; colour?: string }) {
  const [refreshing, setRefreshing] = useState<string | null>(null)
  const refresh = async () => {
    setRefreshing('Checking…')
    try {
      const r = await api.refresh(card.player_id)
      setRefreshing(r.queued ? (card.provenance ? 'Re-reading fixtures and stats…' : 'Checking the live score…') : r.reason ?? null)
    } catch (e) {
      setRefreshing((e as Error).message)
    }
    setTimeout(() => setRefreshing(null), 45000)
  }
  return (
    <Pressable onPress={refresh} disabled={Boolean(refreshing)} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 0, opacity: refreshing ? 0.55 : 1 }}>
      <RefreshIcon size={14} color={ink} />
      <Text style={[t.small, t.link, { color: ink }]}>{refreshing ?? 'Refresh'}</Text>
    </Pressable>
  )
}

/** Fixtures and results, one at a time, results when asked for. With no fixtures, a line says so above the results. */
function Matches({ card, view: chosen, setView }: { card: Card; view: MatchesView | null; setView: (v: MatchesView) => void }) {
  const retired = card.player.status === 'retired'
  if (retired || !(card.upcoming ?? []).length) {
    return (
      <View style={{ gap: 10 }}>
        {!retired ? <Text style={[t.small, t.muted]}>No games scheduled.</Text> : null}
        <ResultsHistory card={card} />
      </View>
    )
  }
  const view = chosen ?? 'upcoming'
  return (
    <View style={{ gap: 10 }}>
      <Segmented<MatchesView> options={[['upcoming', 'Upcoming'], ['results', 'Results']]} value={view} onChange={setView} />
      {view === 'upcoming' ? (
        <>
          <UpcomingList items={card.upcoming ?? []} empty="No upcoming games found." />
          <SourceNote provenance={card.provenance?.upcoming} />
        </>
      ) : <ResultsHistory card={card} />}
    </View>
  )
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  alertPanel: { gap: 8, padding: 14, borderRadius: 18, backgroundColor: colour.surface },
  tabs: { flexDirection: 'row', gap: 18, borderBottomWidth: 2, borderBottomColor: colour.line },
  tab: { height: 44, justifyContent: 'center', borderBottomWidth: 3, borderBottomColor: 'transparent', marginBottom: -2 },
  tabOn: { borderBottomColor: colour.text },
  tabText: { fontFamily: font[800], fontSize: 13, letterSpacing: 1, textTransform: 'uppercase', color: colour.muted },
  game: { gap: 14, padding: 18, borderRadius: 22, backgroundColor: colour.surface },
})

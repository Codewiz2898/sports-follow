// An athlete from search, before following, the web's AthletePage.tsx: who they are, their next game, their last
// game with their line, and their stats, all from the live source. Following binds exactly this athlete.

import { router, useLocalSearchParams } from 'expo-router'
import { useEffect, useState } from 'react'
import { Linking, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { safeHref } from '../../../../../web/src/format'
import { previewAbout } from '../../../../../web/src/search'
import { useAccount } from '../../../account'
import { api, type AthletePreview, type SearchResult } from '../../../api'
import { b, PlayerBand, ResultsList, StatGrid, UpcomingList } from '../../../blocks'
import { toneOf, useSports } from '../../../catalog'
import { BackIcon, PlusIcon } from '../../../icons'
import { useSearchActions } from '../../../search'
import { font, t } from '../../../theme'
import { Btn, ErrorBox, Section, Spinner } from '../../../ui'
import { Page } from '../../../page'

export default function AthleteScreen() {
  const params = useLocalSearchParams<{ system: string; id: string; league?: string; sport?: string; result?: string }>()
  const { system = '', id = '' } = params
  const league = params.league ?? null
  const sport = params.sport ?? null
  const fromRegistry = system === 'wikidata'
  const hint: SearchResult | null = (() => { try { return params.result ? (JSON.parse(params.result) as SearchResult) : null } catch { return null } })()
  const insets = useSafeAreaInsets()
  const sports = useSports()
  const [preview, setPreview] = useState<AthletePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const actions = useSearchActions()
  // Without the research agent (GET /api/config), a page comes from a live source or not at all.
  const research = useAccount().config?.research !== false

  useEffect(() => {
    const ctrl = new AbortController()
    setPreview(null)
    setError(null)
    api.athlete(system, id, { league, sport }, ctrl.signal)
      .then((r) => {
        // Already on the app: their page is the preview.
        if ('player_id' in r) router.replace(`/player/${r.player_id}`)
        else setPreview(r)
      })
      .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
    return () => ctrl.abort()
  }, [system, id, league, sport])

  const name = preview?.name ?? hint?.name ?? ''
  const athleteSport = preview?.sport ?? hint?.sport ?? sport
  const tone = toneOf(sports, athleteSport)
  // Who they are, after the sport (the band names the sport on its own).
  const about = previewAbout(preview, hint)
  const detail = [athleteSport, about].filter(Boolean).join(' · ')
  // A registry athlete found in a live source follows by that source's id; one no source has, by registry id.
  const follow = () => actions.follow({
    key: `${system}:${id}`, name, sport: athleteSport ?? '', detail,
    system: preview?.system ?? (fromRegistry ? null : system), athlete_id: preview?.athlete_id ?? (fromRegistry ? null : id),
    league: preview?.league ?? league, qid: preview?.qid ?? (fromRegistry ? id : null),
    following: false, followers: 0, live_scores: true, inactive: false, exact: true,
  })
  const noSource = preview?.live === false
  const url = safeHref(preview?.source_url)

  return (
    <Page top={false} bar={tone.sport}>
      <PlayerBand
        topInset={insets.top}
        name={name || 'Athlete'}
        sport={athleteSport}
        meta={about}
        photo={preview?.photo}
        back={<Btn kind="icon" accessibilityLabel="Back to search" colours={{ bg: tone.on }} icon={<BackIcon size={20} color={tone.sport} />} onPress={() => (router.canGoBack() ? router.back() : router.replace('/search'))} />}
        actions={<Btn label="Follow" icon={<PlusIcon size={16} color={tone.sport} />} colours={{ fg: tone.sport, bg: tone.on, border: tone.on }} busy={Boolean(actions.pending)} disabled={!name || (noSource && !research)} onPress={follow} />}
      >
        <Text style={[{ fontFamily: font[600], fontSize: 13, lineHeight: 18.5, color: tone.onSoft }, preview?.photo ? b.besidePhoto : null]}>
          {noSource
            ? research
              ? 'No live source has this athlete yet, so the research agent builds their page (1 to 3 minutes). That uses one of your AI researches for today.'
              : "No live source has this athlete yet, so there's no page to follow."
            : research ? 'Live scores start right away. News and background follow in about two minutes.' : 'Live scores, games and stats start right away.'}
        </Text>
      </PlayerBand>
      {error || actions.error ? <ErrorBox>{error || actions.error}</ErrorBox> : null}

      {!preview && !error ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <Spinner />
          <Text style={[t.small, t.muted]}>{fromRegistry ? 'Finding them in the live sources…' : 'Reading their games and stats…'}</Text>
        </View>
      ) : null}

      {preview && !noSource ? (
        <>
          {preview.fixtures !== false ? (
            <>
              <Section title="Next"><UpcomingList items={preview.next ? [preview.next] : []} empty="No game scheduled in the source yet." /></Section>
              <Section title="Last game"><ResultsList items={preview.last ? [preview.last] : []} teams={preview.teams} /></Section>
            </>
          ) : null}
          <Section title="Stats">
            <StatGrid stats={preview.stats} />
            <Text style={[t.tiny, t.muted]}>
              {[preview.stats_note, `from ${preview.source}`].filter(Boolean).join(' · ')}
              {url ? <> · <Text onPress={() => Linking.openURL(url)} style={t.link}>Profile</Text></> : null}
            </Text>
            {preview.fixtures === false ? <Text style={[t.tiny, t.muted]}>Their tournaments and live boards are found once you follow.</Text> : null}
          </Section>
        </>
      ) : null}
    </Page>
  )
}

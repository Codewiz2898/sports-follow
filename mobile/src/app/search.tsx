// Search, the web's SearchPage.tsx: every source as you type, the exact athlete picked, or a name handed to the
// research agent; before anything's typed, a few players to try.

import { useLocalSearchParams } from 'expo-router'
import { useState } from 'react'
import { StyleSheet, Text, TextInput, View } from 'react-native'
import Constants from 'expo-constants'
import { enterTeam, sections, starterNames } from '../../../web/src/search'
import { inYourOrder } from '../../../web/src/yourSports'
import { useAccount } from '../account'
import { useFollowing } from '../following'
import { SearchIcon } from '../icons'
import { SearchResults, SportChips, useSearch, useSearchActions } from '../search'
import { colour, dsp, font, t } from '../theme'
import { useStarters } from '../today/starters'
import { Btn, ErrorBox, Section, Spinner } from '../ui'
import { Page } from '../page'

const APP = Constants.expoConfig?.name ?? ''

export default function SearchScreen() {
  const params = useLocalSearchParams<{ q?: string; sport?: string }>()
  const [query, setQuery] = useState(params.q ?? '')
  const [sport, setSport] = useState(params.sport ?? '')
  const { data, fresh, loading, error } = useSearch(query, sport)
  const actions = useSearchActions()
  const starters = useStarters().sports
  const yours = useFollowing().sports
  const tries = starterNames(starters && inYourOrder(starters, (x) => x.key, yours), 6) // the starters, a sport at a time, the fan's sports first
  const research = useAccount().config?.research !== false

  const submit = () => {
    // Still showing the previous query's results: they aren't what was asked for.
    if (!fresh || !data) return
    const first = sections(data, APP)[0]?.items[0]
    const team = enterTeam(data) // "Arsenal", or "lakers" with no athlete called that: first in the results, as the web's
    if (team) actions.openTeam(team)
    else if (first && (first.exact || data.results.length === 1)) actions.open(first)
  }

  return (
    <Page>
      <Text style={dsp(54)}>Search</Text>
      <View style={s.box}>
        <SearchIcon size={22} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={submit}
          autoFocus
          autoCorrect={false}
          autoCapitalize="words"
          returnKeyType="search"
          placeholder="Any athlete, any sport"
          placeholderTextColor={colour.muted}
          accessibilityLabel="Player name"
          style={s.input}
        />
        {loading ? <Spinner /> : null}
      </View>
      <SportChips sport={sport} onChange={setSport} />
      {actions.error || error ? <ErrorBox>{actions.error || error}</ErrorBox> : null}

      {data ? (
        <SearchResults data={data} query={query} loading={loading} actions={actions} />
      ) : (
        <Section title={tries.length ? 'Try' : undefined}>
          {tries.length ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {tries.map((name) => <Btn key={name} small label={name} onPress={() => setQuery(name)} />)}
            </View>
          ) : null}
          <Text style={[t.small, t.muted, { marginTop: 4 }]}>
            {research
              ? 'Pick an athlete and their live scores start at once; the research agent adds news and background in a couple of minutes. Names no source knows can be researched with AI, a few a day.'
              : 'Pick an athlete and their live scores, games and stats start at once.'}
          </Text>
        </Section>
      )}
    </Page>
  )
}

const s = StyleSheet.create({
  box: { flexDirection: 'row', alignItems: 'center', gap: 10, height: 54, paddingHorizontal: 16, borderRadius: 16, backgroundColor: colour.surface, borderWidth: 2, borderColor: colour.text },
  input: { flex: 1, minWidth: 0, fontFamily: font[600], fontSize: 17, color: colour.text, paddingVertical: 0 },
})

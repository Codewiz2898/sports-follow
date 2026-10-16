// Credits, the web's CreditsPage.tsx: where this build's scores, ratings, evaluations, words and code come from
// (sports_follow/credits.py).

import { router } from 'expo-router'
import { useEffect, useState } from 'react'
import { Linking, Text, View } from 'react-native'
import { safeHref } from '../../../web/src/format'
import { api, type Credit, type Credits, type PhotoCredit } from '../api'
import { Empty } from '../blocks'
import { t } from '../theme'
import { BackHead, Card, ErrorBox, ListCard, Section, Spinner } from '../ui'
import { Page } from '../page'

function open(url?: string | null) {
  const href = safeHref(url)
  return href ? () => Linking.openURL(href) : undefined
}

function Name({ credit }: { credit: Credit }) {
  const go = open(credit.url)
  return <Text onPress={go} style={[t.title, go && t.link]}>{credit.name}</Text>
}

function CreditList({ label, items }: { label: string; items: Credit[] }) {
  if (!items.length) return null
  return (
    <Section title={label}>
      <ListCard>
        {items.map((c) => (
          <View key={c.name} style={{ gap: 4 }}>
            <Name credit={c} />
            {c.what ? <Text style={t.small}>{c.what}</Text> : null}
            {c.licence || c.note ? (
              <Text style={[t.tiny, t.muted]}>
                {c.licence ? <Text onPress={open(c.licence_url)} style={open(c.licence_url) && t.link}>{c.licence}</Text> : null}
                {c.licence && c.note ? ' · ' : ''}
                {c.note}
              </Text>
            ) : null}
          </View>
        ))}
      </ListCard>
    </Section>
  )
}

/** Every player photo shown, with its author and licence: the credit each one's licence asks for. */
function PhotoList({ items }: { items: PhotoCredit[] }) {
  if (!items.length) return null
  return (
    <Section title="Photos">
      <ListCard>
        {items.map((p) => (
          <View key={p.page} style={{ gap: 4 }}>
            <Text style={t.title}>{p.name}</Text>
            <Text style={t.small}>
              <Text onPress={open(p.page)} style={open(p.page) && t.link}>Photo</Text>
              {` by ${p.author}, `}
              <Text onPress={open(p.licence_url)} style={open(p.licence_url) && t.link}>{p.licence}</Text>
              {', from Wikimedia Commons, cropped'}
            </Text>
          </View>
        ))}
      </ListCard>
    </Section>
  )
}

export default function CreditsScreen() {
  const [credits, setCredits] = useState<Credits | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.credits().then(setCredits).catch((e: Error) => setError(e.message)) }, [])
  return (
    <Page>
      <BackHead title="Credits" />
      {error ? <ErrorBox>{error}</ErrorBox> : null}
      {!credits && !error ? <Empty><Spinner /> Loading…</Empty> : null}
      {credits ? (
        <>
          <CreditList label="Scores and stats" items={credits.data} />
          <CreditList label="Also from" items={credits.also} />
          <PhotoList items={credits.photos ?? []} />
          <Section title="Notices">
            <Card pad style={{ gap: 10 }}>
              {credits.notices.map((n) => <Text key={n} style={t.small}>{n}</Text>)}
            </Card>
          </Section>
          <Section title="Built with">
            <ListCard>
              {credits.software.map((x) => (
                <View key={x.name} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                  <Name credit={x} />
                  <Text style={[t.tiny, t.muted, { textAlign: 'right', flexShrink: 1 }]}>{x.licence}</Text>
                </View>
              ))}
            </ListCard>
          </Section>
        </>
      ) : null}
      <Text onPress={() => router.push('/privacy')} style={[t.tiny, t.muted, t.link]}>Privacy: what we keep, and why</Text>
    </Page>
  )
}

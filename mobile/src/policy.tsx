// The privacy and terms pages' parts (app/privacy.tsx, app/terms.tsx), as the web's PrivacyPage.tsx draws them.

import type { ReactNode } from 'react'
import { Text } from 'react-native'
import { t } from './theme'
import { Card, Section } from './ui'

/** One part of a policy: its heading, and its paragraphs in a card. */
export function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Section title={title}>
      <Card pad style={{ gap: 10 }}>{children}</Card>
    </Section>
  )
}

/** A paragraph of a policy. */
export const P = ({ children }: { children: ReactNode }) => <Text style={[t.small, { lineHeight: 19.5 }]}>{children}</Text>

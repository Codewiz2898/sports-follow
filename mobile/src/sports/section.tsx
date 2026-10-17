// One part of a match page under its board (the web's sports/section.tsx), headed as the app's sections are
// (ui.SectionHead): a sport's part draws its own in these.

import type { ReactNode } from 'react'
import { Eyebrow, Section } from '../ui'

export function MatchSection({ title, note, children }: { title: string; note?: string | null; children: ReactNode }) {
  return <Section title={title} right={note ? <Eyebrow>{note}</Eyebrow> : undefined}>{children}</Section>
}

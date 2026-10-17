// One part of a match page under its board (the web's sports/section.tsx), headed as the app's sections are
// (ui.SectionHead): a sport's part draws its own in these. A long note ("352/5 (48.2/50 ov, target 352)") wraps
// under itself rather than running off the screen at a large text size.

import type { ReactNode } from 'react'
import { Eyebrow, Section } from '../ui'

export function MatchSection({ title, note, children }: { title: string; note?: string | null; children: ReactNode }) {
  return <Section title={title} right={note ? <Eyebrow style={{ flexShrink: 1, textAlign: 'right' }}>{note}</Eyebrow> : undefined}>{children}</Section>
}

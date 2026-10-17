import type { ReactNode } from 'react'

/** One part of a match page under its board, headed as the app's sections are (Blocks.SectionHead): a sport's
 * view draws its own parts in these. */
export function MatchSection({ title, note, children }: { title: string; note?: string | null; children: ReactNode }) {
  return (
    <section className="section" aria-label={title}>
      <div className="section-head">
        <h2 className="dsp section-title">{title}</h2>
        {note && <span className="eyebrow">{note}</span>}
      </div>
      {children}
    </section>
  )
}

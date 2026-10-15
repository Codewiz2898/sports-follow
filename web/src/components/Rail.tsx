import { Children, useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { SectionHead } from './Blocks'
import { BackIcon, ChevronIcon } from './Icons'

const ALL_FROM = 3  // a rail this long ends in an "All N" card that lays every card out at once

/** Scrolls smoothly, unless the fan asked for less motion. */
function motion(): ScrollBehavior {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

/**
 * A section whose cards run sideways in one row, out to the page's edge, so it can hold every game without
 * making the page taller: each card snaps to the page padding with the next one peeking in, the arrows page
 * it on a desktop, and with `all` (what the end card says) an "All N" card at the end lays them all out at
 * once. Its children are the cards, each a fixed width (styles.css, "rails"). A `head` takes the place of
 * the title row, and of its arrows with it (Today's players strip, which a desktop doesn't show).
 */
export function Rail({ title, note, all, head, className, children }: { title: string; note?: string; all?: string; head?: ReactNode; className?: string; children: ReactNode }) {
  const id = useId()
  const section = useRef<HTMLElement>(null)
  const row = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ start: true, end: true })
  const [open, setOpen] = useState(false)
  const closing = useRef(false)
  const count = Children.count(children)

  // Where the row is scrolled to: the arrows can't go past either end.
  const measure = useCallback(() => {
    const el = row.current
    if (!el) return
    const start = el.scrollLeft <= 1
    const end = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1
    setEdges((e) => (e.start === start && e.end === end ? e : { start, end }))
  }, [])
  useEffect(() => {
    const el = row.current
    if (!el) return
    const resized = new ResizeObserver(measure)
    resized.observe(el)
    el.addEventListener('scroll', measure, { passive: true })
    return () => {
      resized.disconnect()
      el.removeEventListener('scroll', measure)
    }
  }, [measure])
  // And after every render: the cards can change width (the hero comes and goes) while the row doesn't.
  useEffect(measure)

  const page = (direction: 1 | -1) => {
    const el = row.current
    if (el) el.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: motion() })
  }
  const toggle = () => {
    closing.current = open
    setOpen(!open)
  }
  // Back to one row: it starts at its first card, and the section, now short, is in view rather than above.
  useEffect(() => {
    if (!closing.current) return
    closing.current = false
    row.current?.scrollTo({ left: 0 })
    section.current?.scrollIntoView({ block: 'nearest', behavior: motion() })
  }, [open])

  return (
    <section ref={section} className={`section${className ? ` ${className}` : ''}`} aria-label={title}>
      {head ?? <SectionHead title={title}>
        <span className="row" style={{ gap: 12 }}>
          {note && <span className="eyebrow">{note}</span>}
          {!open && !(edges.start && edges.end) && (
            <span className="rail-arrows">
              <button type="button" className="rail-arrow" aria-label={`${title}: scroll back`} aria-controls={id} disabled={edges.start} onClick={() => page(-1)}><BackIcon width={18} height={18} /></button>
              <button type="button" className="rail-arrow" aria-label={`${title}: scroll on`} aria-controls={id} disabled={edges.end} onClick={() => page(1)}><ChevronIcon width={18} height={18} /></button>
            </span>
          )}
        </span>
      </SectionHead>}
      <div ref={row} id={id} className={`rail${open ? ' open' : ''}`}>
        {children}
        {all && count >= ALL_FROM && (
          <button type="button" className="rail-all" aria-expanded={open} aria-controls={id} onClick={toggle}>
            <span className="dsp">{open ? 'Fewer' : `All ${count}`}</span>
            <span className="small muted" style={{ fontWeight: 700 }}>{open ? 'Back to one row' : all}</span>
          </button>
        )}
      </div>
    </section>
  )
}

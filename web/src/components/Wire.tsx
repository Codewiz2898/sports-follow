import { Link } from 'react-router-dom'
import type { WireRow } from '../api'
import { daysAgo, day, parseDate, safeHref, shortName } from '../format'
import { sportKey } from '../sports'
import { SectionHead } from './Blocks'

/** When a row is from: the time today ("07:40"), the weekday within the week ("Wed"), else the date. */
function when(at: string, now: number): string {
  const d = parseDate(at)
  const days = daysAgo(at, new Date(now))
  if (!d || days == null) return ''
  if (days <= 0) return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return days < 7 ? d.toLocaleDateString(undefined, { weekday: 'short' }) : day(at)
}

/** One row: its tag in the player's sport colour, the line (a story's headline, with the site that ran it,
 * and the player's name first when the headline doesn't say it), and when. A story opens at its site, a
 * line-up on the player's page. */
function Line({ row, now }: { row: WireRow; now: number }) {
  const name = shortName(row.player)
  const named = row.text.toLowerCase().includes(name.toLowerCase())
  const body = (
    <>
      <span className="wire-tag" data-sport={sportKey(row.sport)}>{row.tag}</span>
      <span className="wire-what">
        <span className="wire-text">{!named && <>{name}: </>}{row.text}</span>
        {row.source && <span className="wire-source">{row.source}</span>}
      </span>
      <span className="wire-time">{when(row.at, now)}</span>
    </>
  )
  const away = safeHref(row.url)
  if (away) return <a href={away} target="_blank" rel="noreferrer" className="wire-row">{body}</a>
  if (row.url.startsWith('/') && !row.url.startsWith('//')) return <Link to={row.url} className="wire-row">{body}</Link>
  return <span className="wire-row">{body}</span>
}

/**
 * The Wire on Today: an ink card of the fan's players' line-ups and news, newest first, each a tag, one line
 * and a time. Its news is other sites' headlines, so each names its site; nothing here says "breaking".
 * Nothing at all when there's none.
 */
export function Wire({ rows, now }: { rows: WireRow[]; now: number }) {
  if (!rows.length) return null
  return (
    <section className="section" aria-label="The wire">
      <div className="desktop-only"><SectionHead title="The wire" /></div>
      <ul className="wire">
        {rows.map((r) => <li key={r.id}><Line row={r} now={now} /></li>)}
      </ul>
    </section>
  )
}

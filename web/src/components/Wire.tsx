import { Link } from 'react-router-dom'
import type { WireRow } from '../api'
import { safeHref } from '../format'
import { sportKey } from '../sports'
import { wireText, wireWhen } from '../today'
import { SectionHead } from './Blocks'

/** One row: its tag in the player's sport colour (outlined for a player the fan doesn't follow), the line (a
 * fact, a line-up, or a story's headline with the player's name first when it doesn't say it), where it's
 * from, and when. A story opens at its site; a fact or a line-up on the player's page here, or the source's
 * athlete page for a player nobody follows yet. */
function Line({ row, now }: { row: WireRow; now: number }) {
  const body = (
    <>
      <span className={`wire-tag${row.yours ? '' : ' theirs'}`} data-sport={sportKey(row.sport)}>{row.tag}</span>
      <span className="wire-what">
        <span className="wire-text">{wireText(row)}</span>
        {row.source && <span className="wire-source">{row.source}</span>}
      </span>
      <span className="wire-time">{wireWhen(row.at, now)}</span>
    </>
  )
  const away = safeHref(row.url)
  if (away) return <a href={away} target="_blank" rel="noreferrer" className="wire-row">{body}</a>
  if (row.url.startsWith('/') && !row.url.startsWith('//')) return <Link to={row.url} className="wire-row">{body}</Link>
  return <span className="wire-row">{body}</span>
}

/**
 * The Wire on Today: an ink card of who's out and who's back, line-ups and news, the fan's players' first, then
 * a few of the best-known players they don't follow; each a tag, one line, where it's from and a time. Nothing
 * here says "breaking". Nothing at all when there's none.
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

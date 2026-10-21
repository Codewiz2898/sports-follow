import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation } from 'react-router-dom'
import type { LiveGame } from '../api'
import { useNow } from '../clock'
import { countdown, parseDate, shortWhen } from '../format'
import { useFollowing } from '../following'
import { callNote } from '../predictions'
import { sportKey } from '../sports'
import { comingUp, leagueComingUp, liveShort, theirEvents } from '../today'
import { CallTag, useCalls } from './Calls'
import { BlockError, ErrorBoundary } from './ErrorBoundary'
import { ChevronIcon } from './Icons'
import { useLeague } from './League'
import { useLive } from './LiveNow'

/** A screen this wide has the side column (styles.css, .side-col: the same width). */
const WIDE = '(min-width: 1400px)'
const SCORES = 6  // games on, at most
const RESULTS = 3  // big results under them, at most
const SOON = 6  // games starting soon, at most

const watchWide = (changed: () => void) => {
  const query = window.matchMedia(WIDE)
  query.addEventListener('change', changed)
  return () => query.removeEventListener('change', changed)
}
const isWide = () => window.matchMedia(WIDE).matches

/** Whether this screen has the side column: none on a phone or a narrower window, so nothing in it looks then. */
export function useWide(): boolean {
  return useSyncExternalStore(watchWide, isWide)
}

/** The side column's top, where a page stands its own part (InSide); null while there's no side column. */
export const SideSlot = createContext<HTMLElement | null>(null)

/** A part of a page that, on a wide screen, stands at the top of the side column, beside the page (a match page's
 * Call it); on a narrower one, where the page puts it. */
export function InSide({ children }: { children: ReactNode }) {
  const slot = useContext(SideSlot)
  return slot ? createPortal(children, slot) : <>{children}</>
}

/** One line of the column: the game's sport as a dot (red while it's on), what it is, and under it its score or
 * when it starts; at its end, its call (Calls.tsx). It opens the game's match page, where it has one. */
function Row({ to, sport, live = false, title, line, end }: { to?: string | null; sport: string; live?: boolean; title: string; line: string; end?: ReactNode }) {
  const inside = (
    <>
      <span className={live ? 'live-dot' : 'side-dot'} aria-hidden="true" />
      <span className="side-what">
        <span className="side-title-line">{title}</span>
        {line && <span className="side-line">{line}</span>}
      </span>
      {end}
    </>
  )
  return to
    ? <Link to={`/match/${to}`} className="side-row" data-sport={sport} aria-label={live ? `${title}, live` : undefined}>{inside}</Link>
    : <div className="side-row" data-sport={sport}>{inside}</div>
}

/** A live game's score in a few characters as its sport says it ("1–0 · 67'"), else the source's score line, unless
 * it has no number in it: then it's only the sides' names again (a chess board's "Caruana vs Liang"). */
function scoreOf(game: LiveGame): string {
  const line = game.live.score ?? ''
  return liveShort(game.live) || (/\d/.test(line) ? line : '')
}

/** The games on now, whoever follows them (useLive: one look with Today's), each with its score; then the latest
 * big results (useLeague). Nothing when there's neither. */
function Scores() {
  const sports = useLive()
  const league = useLeague()
  const on = sports.flatMap((s) => s.games.map((game) => ({ game, sport: s.key })))
  const done = (league?.results.games ?? []).slice(0, RESULTS)
  if (!on.length && !done.length) return null
  return (
    <section className="section" aria-label="Scores">
      <div className="section-head">
        <h2 className="dsp side-head-title">Scores</h2>
        {on.length > 0 && <Link to="/live" className="eyebrow side-more">{on.length} on<ChevronIcon width={14} height={14} strokeWidth={2.6} /></Link>}
      </div>
      <ul className="side-list">
        {on.slice(0, SCORES).map(({ game, sport }) => (
          <li key={game.key}>
            <Row to={game.match} sport={sport} live title={game.title} line={[scoreOf(game), game.competition].filter(Boolean).join(' · ')} />
          </li>
        ))}
        {done.map((g) => (
          <li key={g.key}>
            <Row to={g.match} sport={sportKey(g.sport)} title={g.line || g.title} line={[g.word !== (g.line || g.title) ? g.word : '', g.competition].filter(Boolean).join(' · ')} end={<span className="eyebrow">Final</span>} />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The games starting next: the fan's own (their players', teams' and games they follow), then the big ones,
 * soonest first; each with the countdown within a day, else the day, and its call where they have one. */
function StartingSoon() {
  const { cards, teams, matches } = useFollowing()
  const league = useLeague()
  const calls = useCalls()
  const now = useNow()
  const theirs = new Set([...theirEvents(cards, teams), ...matches.map((m) => m.event_id)])
  const games = [
    ...comingUp(cards, now, matches, teams).map((g) => ({ key: g.key, title: g.event.title, competition: g.event.competition, start: g.event.start_utc, match: g.event.match, sport: sportKey(g.sport ?? g.players[0]?.player.sport) })),
    ...leagueComingUp(league?.coming.games ?? [], theirs, now).map((g) => ({ key: g.key, title: g.title, competition: g.competition, start: g.start_utc, match: g.match, sport: sportKey(g.sport) })),
  ]
  const at = (s?: string | null) => parseDate(s)?.getTime() ?? Infinity
  const soonest = games.sort((a, b) => at(a.start) - at(b.start)).slice(0, SOON)
  if (!soonest.length) return null
  return (
    <section className="section" aria-label="Starting soon">
      <div className="section-head"><h2 className="dsp side-head-title">Starting soon</h2></div>
      <ul className="side-list">
        {soonest.map((g) => {
          const when = at(g.start) - now < 864e5 ? countdown(g.start, now) : shortWhen(g.start, now)
          return (
            <li key={g.key}>
              <Row to={g.match} sport={g.sport} title={g.title} line={[when, g.competition].filter(Boolean).join(' · ')} end={<CallTag note={callNote(calls, g.match, now)} />} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** The column on the right of a wide screen, beside every page: the page's own part (InSide), then the scores and
 * the games starting soon. */
export function SideColumn({ slot }: { slot: (el: HTMLDivElement | null) => void }) {
  const { pathname } = useLocation()  // a part that failed tries again on the next page
  return (
    <aside className="side-col" aria-label="Scores and games starting soon">
      <div ref={slot} className="side-slot" />
      <ErrorBoundary fallback={<BlockError />} resetKey={pathname}>
        <Scores />
      </ErrorBoundary>
      <ErrorBoundary fallback={<BlockError />} resetKey={pathname}>
        <StartingSoon />
      </ErrorBoundary>
    </aside>
  )
}

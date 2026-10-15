import { useEffect, useState } from 'react'
import { api, type LiveSport } from '../api'
import { shortName } from '../format'
import { CheckIcon, PlusIcon } from './Icons'
import { Rail } from './Rail'
import { useFollowToggle } from './Starters'

const EVERY = 60_000  // the server reads a source again at most this often
const SOON = 5_000  // when it says a read is under way, the next look

/**
 * "Live now": every game the sources have on, whoever follows it (GET /api/live, live.py), a rail of cards
 * with the score and a few players to follow from each with a tap. Nothing while nothing is on. `first`:
 * these sports' games come first (those a fan picked in the welcome).
 */
export function LiveNow({ first = [] }: { first?: string[] }) {
  const [sports, setSports] = useState<LiveSport[]>([])
  const { followed, toggle, busy, error } = useFollowToggle()

  useEffect(() => {
    let current = true
    let timer: number | undefined
    const look = (tries: number) => {
      window.clearTimeout(timer)
      if (document.hidden) return  // nobody's looking: no reads asked for; showing the tab again looks
      api.live()
        .then((r) => {
          if (!current) return
          setSports(r.sports)
          // A read was just asked for: look again in a moment, a few times, for the new scores.
          timer = window.setTimeout(() => look(r.refreshing ? tries + 1 : 0), r.refreshing && tries < 3 ? SOON : EVERY)
        })
        .catch(() => { if (current) timer = window.setTimeout(() => look(tries), EVERY) })
    }
    const shown = () => { if (!document.hidden) look(0) }
    document.addEventListener('visibilitychange', shown)
    look(0)
    return () => { current = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', shown) }
  }, [])

  const ordered = [...sports].sort((a, b) => Number(!first.includes(a.key)) - Number(!first.includes(b.key)))
  const games = ordered.flatMap((s) => s.games.map((g) => ({ ...g, sport: s.key })))
  if (!games.length) return null
  return (
    <>
      <Rail title="Live now" note={`${games.length} on`}>
        {games.map((g) => (
          <article key={g.key} className="live-game" data-sport={g.sport} aria-label={`${g.title}, live`}>
            <span className="row" style={{ gap: 8 }}>
              <span className="badge-live">Live</span>
              {g.live.clock && <span className="eyebrow clock">{g.live.clock}</span>}
            </span>
            <span className="eyebrow">{g.competition}</span>
            <span className="dsp score">{g.live.score || g.title}</span>
            {g.players.length > 0 && (
              <div className="who" role="group" aria-label={`Follow from ${g.title}`}>
                {g.players.map((r) => {
                  const on = followed(r)
                  return (
                    <button key={r.key} type="button" className="follow-pill" aria-pressed={on} aria-busy={busy(r)} aria-label={`Follow ${r.name}`} onClick={() => void toggle(r)}>
                      {busy(r) ? <span className="spinner" /> : on ? <CheckIcon width={14} height={14} /> : <PlusIcon width={14} height={14} />}
                      {shortName(r.name)}
                    </button>
                  )
                })}
              </div>
            )}
          </article>
        ))}
      </Rail>
      {error && <div className="error-box">{error}</div>}
    </>
  )
}

import { useEffect, useState } from 'react'
import { api, type SearchResult, type StarterSport } from '../api'
import { sportKey } from '../sports'
import { starterReason } from '../today'
import { useFollowing } from '../following'
import { followedSports, startersOffered } from '../yourSports'
import { SectionHead } from './Blocks'
import { CheckIcon, PlusIcon } from './Icons'
import { followTarget } from '../follow'

// What this page load knows, shared by every picker: the last answer (shown at once by the next picker,
// while it asks again), the player each starter followed here became, and the sport last picked by chip.
let lastAnswer: StarterSport[] | null = null
const followedHere: Record<string, number> = {}
let lastChip = ''

/** The starters this server offers a new fan, by sport (GET /api/starters, starters.py): null while loading. */
export function useStarters(): { sports: StarterSport[] | null; failed: boolean } {
  const [sports, setSports] = useState<StarterSport[] | null>(lastAnswer)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let current = true
    api.starters()
      .then((r) => { lastAnswer = r.sports; if (current) setSports(r.sports) })
      .catch(() => { if (current) setFailed(true) })
    return () => { current = false }
  }, [])
  return { sports, failed }
}

export { starterNames } from '../search'

/**
 * Follow, or unfollow, a result with one tap: whether it's followed, whether a tap is on its way, and the
 * last error. What was followed here is kept for the whole page, by result key ("system:athlete id"), so a
 * starter tile and a live game's chip for one player agree, and a picker mounted again still knows.
 */
export function useFollowToggle() {
  const following = useFollowing()
  const [, setVersion] = useState(0)
  const [pending, setPending] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const playerOf = (r: SearchResult) => followedHere[r.key] ?? r.player_id ?? null
  const followed = (r: SearchResult) => {
    const id = playerOf(r)
    return id != null && following.isFollowing(id)
  }

  const toggle = async (r: SearchResult) => {
    if (pending.includes(r.key)) return
    setError(null)
    setPending((p) => [...p, r.key])
    try {
      const id = playerOf(r)
      if (id != null && following.isFollowing(id)) await following.unfollow(id)
      else {
        const card = await following.follow(followTarget(r))
        followedHere[r.key] = card.player_id
        setVersion((v) => v + 1)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending((p) => p.filter((k) => k !== r.key))
    }
  }

  return { followed, toggle, busy: (r: SearchResult) => pending.includes(r.key), error }
}

/**
 * The starter picker: a sport's starters as tiles in its colour, each followed, or unfollowed, with one tap.
 * In the welcome's second step (each sport the fan picked, under its name) and on Today (one sport at a time, by
 * chips), with a line about the sport shown where `note` has one.
 */
export function StarterPicker({ sports, chips = false, note }: { sports: StarterSport[]; chips?: boolean; note?: (sport: StarterSport) => string | null }) {
  const [shown, setShown] = useState(sports.some((s) => s.key === lastChip) ? lastChip : sports[0]?.key ?? '')
  const { followed, toggle, busy: pendingFor, error } = useFollowToggle()

  const groups = chips ? sports.filter((s) => s.key === shown) : sports
  return (
    <div className="stack" style={{ gap: 12 }}>
      {chips && sports.length > 1 && (
        <div className="chips" role="group" aria-label="Sport">
          {sports.map((s) => (
            <button key={s.key} type="button" className="chip" data-sport={s.key} aria-pressed={shown === s.key} onClick={() => { lastChip = s.key; setShown(s.key) }}>
              <span className="dot" />{s.label}
            </button>
          ))}
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
      {groups.map((s) => (
        <section key={s.key} className="section" aria-label={s.label}>
          {!chips && <SectionHead title={s.label} />}
          {note?.(s) && <span className="small muted" style={{ fontWeight: 600 }}>{note(s)}</span>}
          <div className="tiles">
            {s.players.map((r) => {
              const on = followed(r)
              const busy = pendingFor(r)
              return (
                <button key={r.key} type="button" className="tile pick" data-sport={s.key} aria-pressed={on} aria-busy={busy} aria-label={`Follow ${r.name}`} onClick={() => void toggle(r)}>
                  <span className="corner">{busy ? <span className="spinner" /> : on ? <CheckIcon width={20} height={20} /> : <PlusIcon width={20} height={20} />}</span>
                  <span className="sport">{starterReason(r)}</span>
                  <span className="dsp name">{r.name}</span>
                  {r.detail && <span className="status">{r.detail}</span>}
                </button>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

/**
 * Today's "Players to follow": the starter picker, a sport at a time, the fan's sports first. Every sport's while the
 * page says they follow few players (`few`); after, those of their sports they follow nobody in yet
 * (yourSports.startersOffered). With a game picked on Today (`game`), its own alone. Nothing when that leaves none.
 */
export function StartersSection({ few, game = null }: { few: boolean; game?: string | null }) {
  const { sports } = useStarters()
  const { cards, teams, sports: yours } = useFollowing()
  if (!sports?.length) return null
  const followed = followedSports(cards, teams, sportKey)
  const offered = startersOffered(sports, yours, followed, few, game)
  if (!offered.length) return null
  const note = (s: StarterSport) => (yours.includes(s.key) && !followed.has(s.key) ? `In ${s.label}, one of your sports. You follow nobody in it yet.` : null)
  return (
    <section className="section" aria-label="Players to follow">
      <SectionHead title="Players to follow" />
      <StarterPicker sports={offered} chips note={note} />
    </section>
  )
}

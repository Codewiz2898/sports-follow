import { useEffect, useState } from 'react'
import { api, type SearchResult, type StarterSport } from '../api'
import { useFollowing } from '../following'
import { PhotosNote, PlayerPhoto, SectionHead, usePhoto } from './Blocks'
import { CheckIcon, PlusIcon } from './Icons'
import { followTarget } from './Search'

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

/** Up to n starters' names, a sport at a time in turn: search's "Try" names. */
export function starterNames(sports: StarterSport[] | null, n: number): string[] {
  const out: string[] = []
  for (let i = 0; out.length < n && (sports ?? []).some((s) => i < s.players.length); i++) {
    for (const s of sports ?? []) if (i < s.players.length && out.length < n) out.push(s.players[i].name)
  }
  return out
}

/**
 * The starter picker: a sport's starters as tiles in its colour, each followed, or unfollowed, with one tap.
 * In the welcome's second step (each sport the fan picked, under its name) and on Today while a fan
 * follows few players (one sport at a time, by chips).
 */
export function StarterPicker({ sports, chips = false }: { sports: StarterSport[]; chips?: boolean }) {
  const following = useFollowing()
  const [shown, setShown] = useState(sports.some((s) => s.key === lastChip) ? lastChip : sports[0]?.key ?? '')
  const [ids, setIds] = useState<Record<string, number>>(() => ({ ...followedHere }))
  const [pending, setPending] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const playerOf = (r: SearchResult) => ids[r.key] ?? r.player_id ?? null
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
        setIds({ ...followedHere })
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending((p) => p.filter((k) => k !== r.key))
    }
  }

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
          <div className="tiles">
            {s.players.map((r) => (
              <StarterTile key={r.key} r={r} sport={s.key} on={followed(r)} busy={pending.includes(r.key)} onToggle={() => void toggle(r)} />
            ))}
          </div>
          <PhotosNote photos={s.players.map((r) => r.photo)} />
        </section>
      ))}
    </div>
  )
}

/** One starter, followed or unfollowed with a tap: their name, team and photo in their sport's colour. */
function StarterTile({ r, sport, on, busy, onToggle }: { r: SearchResult; sport: string; on: boolean; busy: boolean; onToggle: () => void }) {
  const [photo, drop] = usePhoto(r.photo)
  return (
    <button type="button" className={`tile pick${photo ? ' pictured' : ''}`} data-sport={sport} aria-pressed={on} aria-busy={busy} aria-label={`Follow ${r.name}`} onClick={onToggle}>
      {photo && <PlayerPhoto photo={photo} className="tile-photo" onError={drop} />}
      <span className="corner">{busy ? <span className="spinner" /> : on ? <CheckIcon width={20} height={20} /> : <PlusIcon width={20} height={20} />}</span>
      <span className="dsp name">{r.name}</span>
      {r.detail && <span className="status">{r.detail}</span>}
    </button>
  )
}

/** Today's "Players to follow": the starter picker, a sport at a time, while a fan follows few players. */
export function StartersSection() {
  const { sports } = useStarters()
  if (!sports?.length) return null
  return (
    <section className="section" aria-label="Players to follow">
      <SectionHead title="Players to follow" />
      <StarterPicker sports={sports} chips />
    </section>
  )
}

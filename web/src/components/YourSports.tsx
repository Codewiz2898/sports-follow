import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { longestWordEms } from '../format'
import { useFollowing } from '../following'
import { runningSports, sportInfo, sportKey, type SportInfo } from '../sports'
import { followedSports, sheetSummary, tickedAtFirst, tileGroups, toggled, toSave, yoursShown } from '../yourSports'
import { CheckIcon, EditIcon, PlusIcon } from './Icons'

/**
 * The sports a fan can pick, as tiles in their colours, each ticked or not with a tap: the welcome's first step and
 * the Edit sheet. Every sport that's on (GET /api/sports), those of a group ("esports": each game its own) smaller,
 * under the group's name (yourSports.tileGroups).
 */
export function SportTiles({ tiles, picked, onPick }: { tiles: SportInfo[]; picked: string[]; onPick: (key: string) => void }) {
  return (
    <>
      {tileGroups(tiles).map(({ group, sports }) => {
        const grid = (
          <div className={`tiles sport-tiles${group ? ' minis' : ''}`} role="group" aria-label={group ? `${sportInfo(group).label} games` : 'Sports'}>
            {sports.map((s) => {
              const on = picked.includes(s.key)
              const size = group ? 16 : 20
              return (
                <button key={s.key} type="button" className={`tile pick sport${group ? ' mini' : ''}`} data-sport={s.key} aria-pressed={on} onClick={() => onPick(s.key)}>
                  <span className="corner">{on ? <CheckIcon width={size} height={size} /> : <PlusIcon width={size} height={size} />}</span>
                  <span className="dsp name" style={{ '--ems': longestWordEms(s.label) } as CSSProperties}>{s.label}</span>
                </button>
              )
            })}
          </div>
        )
        return group ? (
          <div key={group} className="stack" style={{ gap: 10 }}>
            <span className="eyebrow">{sportInfo(group).label} · each game its own</span>
            {grid}
          </div>
        ) : <div key="" className="stack">{grid}</div>
      })}
    </>
  )
}

/**
 * Following's Edit (and Today's game search's way here): the fan's sports, ticked, to add or drop one. With none
 * saved yet, the sports of who they follow are ticked. Nothing is saved until Save, which sends any of theirs this app
 * shows no tile for too (yourSports.toSave). From the bottom on a phone, a box in the middle on a desktop.
 */
export function YourSportsSheet({ onClose }: { onClose: () => void }) {
  const { sports: saved, cards, teams, saveSports } = useFollowing()
  const tiles = runningSports()
  const [picked, setPicked] = useState(() => tickedAtFirst(saved, followedSports(cards, teams, sportKey), tiles))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = dialog.current
    if (d && !d.open) d.showModal()
  }, [])
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await saveSports(toSave(picked, saved, tiles))
      dialog.current?.close()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }
  const before = saved.filter((k) => tiles.some((t) => t.key === k))
  return (
    // A tap on the backdrop (the dialog itself, outside its box) closes it, as Escape does.
    <dialog ref={dialog} className="feed-sheet your-sports-sheet" aria-label="Your sports" onClose={onClose} onClick={(e) => { if (e.target === dialog.current) dialog.current?.close() }}>
      <div className="feed-sheet-box">
        <div className="stack" style={{ gap: 6 }}>
          <h2 className="dsp sheet-title">Your sports</h2>
          <p className="small muted" style={{ margin: 0, fontWeight: 600 }}>Each one gets a chip on Today, and players to follow. Dropping one keeps everyone you follow in it.</p>
        </div>
        <SportTiles tiles={tiles} picked={picked} onPick={(key) => setPicked((p) => toggled(p, key))} />
        <div className="sheet-foot">
          {error && <div className="error-box">{error}</div>}
          <span className="small muted summary">{sheetSummary(picked, before, (key) => sportInfo(key).label)}</span>
          <div className="sheet-buttons">
            <button type="button" className="btn" onClick={() => dialog.current?.close()}>Cancel</button>
            <button type="button" className="btn primary" onClick={() => void save()} disabled={saving}>{saving && <span className="spinner" />}Save</button>
          </div>
        </div>
      </div>
    </dialog>
  )
}

/** The fan's sports at the top of Following, in their order, and Edit. Nothing until the server has said them. */
export function YourSportsRow() {
  const { sports, sportsLoaded } = useFollowing()
  const [editing, setEditing] = useState(false)
  if (!sportsLoaded) return null
  const shown = yoursShown(sports, sportInfo)
  return (
    <section className="your-sports" aria-label="Your sports">
      <div className="your-sports-head">
        <span className="dsp">Your sports</span>
        <button type="button" className="btn small" aria-haspopup="dialog" onClick={() => setEditing(true)}><EditIcon width={14} height={14} strokeWidth={2.8} />{shown.length ? 'Edit' : 'Pick'}</button>
      </div>
      {shown.length > 0 && (
        <div className="sport-pills">
          {shown.map((s) => <span key={s.key} className="sport-pill" data-sport={s.key}><span className="dsp">{s.label}</span></span>)}
        </div>
      )}
      <span className="small muted" style={{ fontWeight: 600 }}>
        {shown.length ? 'Today gives each a chip, and keeps offering players from them.' : 'Pick the sports you watch: Today gives each a chip, and keeps offering players from them.'}
      </span>
      {editing && <YourSportsSheet onClose={() => setEditing(false)} />}
    </section>
  )
}

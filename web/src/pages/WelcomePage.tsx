import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { SectionHead } from '../components/Blocks'
import { LiveNow, OtherGame, useLive } from '../components/LiveNow'
import { StarterPicker, useFollowToggle, useStarters } from '../components/Starters'
import { SportTiles } from '../components/YourSports'
import { CheckIcon, SearchIcon } from '../components/Icons'
import { useFollowing } from '../following'
import { runningSports, sportInfo } from '../sports'
import { inYourOrder, toggled, toSave } from '../yourSports'

const WELCOMED = 'sf-welcomed'

/** Whether this browser has been through the welcome, or skipped it: Today sends a fan with no players here once. */
export function welcomed(): boolean {
  try { return localStorage.getItem(WELCOMED) === 'done' } catch { return true }  // storage blocked: never loop back here
}

function markWelcomed() {
  try { localStorage.setItem(WELCOMED, 'done') } catch { /* storage blocked */ }
}

/**
 * The welcome for a fan who follows nobody yet, in two steps: the sports they watch (every sport that's on, GET
 * /api/sports), kept as their sports when they tap Next (Your sports, BHA-653: Following shows and changes them), then
 * a few players from each to follow with a tap (the starters, starters.py), or for a sport with none, its games on
 * now and a way to find its players. Then Today, which keeps offering starters while they follow few players, and of
 * their sports they follow nobody in after.
 */
export function WelcomePage() {
  const tiles = runningSports()
  const { cards, sports: saved, saveSports } = useFollowing()
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  // Null until the fan taps a tile: their sports saved already (another device's, signed in) show ticked.
  const [tapped, setTapped] = useState<string[] | null>(null)
  const [unsaved, setUnsaved] = useState(false)
  const picked = tapped ?? saved.filter((k) => tiles.some((t) => t.key === k))

  const offering = tiles.length > 0
  useEffect(() => {
    if (offering) markWelcomed()  // shown once: leaving by the tab bar doesn't bring a fan back here
  }, [offering])

  const finish = () => {
    markWelcomed()
    navigate('/', { replace: true })
  }

  if (!offering) {
    markWelcomed()  // no sport on to pick: Today's own start, finding a player
    return <Navigate to="/" replace />
  }
  const next = () => {
    setStep(2)
    if (tapped) saveSports(toSave(tapped, saved, tiles)).then(() => setUnsaved(false), () => setUnsaved(true))
  }

  return (
    <>
      <header className="page-head">
        <div className="stack" style={{ gap: 6 }}>
          <span className="eyebrow">Welcome · {step} of 2</span>
          <h1 className="dsp page-title">{step === 1 ? 'What do you watch?' : 'Pick your players'}</h1>
        </div>
      </header>
      <p className="muted" style={{ margin: 0, fontWeight: 600, maxWidth: '40ch' }}>
        {step === 1 ? 'Pick your sports. Next, a few players from each.' : 'Tap to follow: their live scores, results and alerts come to you.'}
      </p>

      {step === 1 ? (
        <>
          <SportTiles tiles={tiles} picked={picked} onPick={(key) => setTapped(toggled(picked, key))} />
          <div className="notice">
            <CheckIcon width={18} height={18} style={{ flexShrink: 0 }} />
            <span style={{ flex: 1 }}>Kept as your sports. Change them any time in Following.</span>
          </div>
        </>
      ) : (
        <>
          {unsaved && <div className="error-box">Couldn't keep your sports just now. Pick them again in Following.</div>}
          <LiveNow first={picked} />
          <WelcomePlayers picked={picked} />
        </>
      )}

      <div className="row welcome-foot">
        {step === 1
          ? <button type="button" className="btn link" onClick={finish}>Skip</button>
          : <button type="button" className="btn link" onClick={() => setStep(1)}>Back</button>}
        {step === 1
          ? <button type="button" className="btn primary" onClick={next}>{picked.length ? `Next · ${picked.length} ${picked.length === 1 ? 'sport' : 'sports'}` : 'Next'}</button>
          : <button type="button" className="btn primary" onClick={finish}>{cards.length ? `Done · ${cards.length} followed` : 'Done'}</button>}
      </div>
    </>
  )
}

/** The welcome's second step: the starters of each sport picked, in the fan's order (every sport's, none picked),
 * then each sport picked that has none (an esports game, often): its games on now, and a way to find its players. */
function WelcomePlayers({ picked }: { picked: string[] }) {
  const { sports: starters, failed } = useStarters()
  if (starters === null && failed) {
    return <div className="error-box">Couldn't load players to suggest. Try again in a moment, or find one yourself.</div>
  }
  if (starters === null) return <span className="spinner" aria-label="Loading" />
  const theirs = picked.length ? inYourOrder(starters.filter((s) => picked.includes(s.key)), (s) => s.key, picked) : starters
  const without = picked.filter((k) => !starters.some((s) => s.key === k))
  return (
    <>
      {theirs.length > 0 && <StarterPicker sports={theirs} />}
      {without.map((key) => <PickedOnNow key={key} sport={key} />)}
      {!theirs.length && !without.length && <Link to="/search" className="btn primary" style={{ alignSelf: 'flex-start' }}><SearchIcon width={18} height={18} />Find a player</Link>}
    </>
  )
}

/** A sport the fan picked that has no starters: its games on now (GET /api/live), each with players to follow from it,
 * and a search for its players. */
function PickedOnNow({ sport }: { sport: string }) {
  const info = sportInfo(sport)
  const live = useLive().find((s) => s.key === sport)
  const follow = useFollowToggle()
  const games = live?.games ?? []
  return (
    <section className="section" aria-label={info.label}>
      <SectionHead title={info.label}>{games.length > 0 && <span className="eyebrow">On now</span>}</SectionHead>
      {games.length > 0 && (
        <div className="live-grid">
          {games.map((g) => <OtherGame key={g.key} game={{ ...g, sport }} label={info.label} follow={follow} />)}
        </div>
      )}
      {follow.error && <div className="error-box">{follow.error}</div>}
      <Link to={`/search?sport=${encodeURIComponent(sport)}`} className="btn" style={{ alignSelf: 'flex-start' }}><SearchIcon width={18} height={18} />Find a {info.label} player</Link>
    </section>
  )
}

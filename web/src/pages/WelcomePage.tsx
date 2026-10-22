import { useEffect, useState, type CSSProperties } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { LiveNow } from '../components/LiveNow'
import { StarterPicker, useStarters } from '../components/Starters'
import { CheckIcon, PlusIcon } from '../components/Icons'
import { longestWordEms } from '../format'
import { useFollowing } from '../following'

const WELCOMED = 'sf-welcomed'

/** Whether this browser has been through the welcome, or skipped it: Today sends a fan with no players here once. */
export function welcomed(): boolean {
  try { return localStorage.getItem(WELCOMED) === 'done' } catch { return true }  // storage blocked: never loop back here
}

function markWelcomed() {
  try { localStorage.setItem(WELCOMED, 'done') } catch { /* storage blocked */ }
}

/**
 * The welcome for a fan who follows nobody yet, in two steps: the sports they watch, then a few players
 * from each to follow with a tap (the starters, starters.py). Then Today, which keeps offering starters
 * while they follow few players.
 */
export function WelcomePage() {
  const { sports, failed } = useStarters()
  const { cards } = useFollowing()
  const navigate = useNavigate()
  const [step, setStep] = useState(1)
  const [picked, setPicked] = useState<string[]>([])

  const offering = !!sports?.length
  useEffect(() => {
    if (offering) markWelcomed()  // shown once: leaving by the tab bar doesn't bring a fan back here
  }, [offering])

  const finish = () => {
    markWelcomed()
    navigate('/', { replace: true })
  }

  if (sports === null && failed) {
    // Not marked unless skipped: the welcome comes back once the server answers.
    return (
      <div className="stack" style={{ gap: 12 }}>
        <div className="error-box">Couldn't load players to suggest. Try again in a moment, or find one yourself.</div>
        <div className="row">
          <button type="button" className="btn link" onClick={finish}>Skip</button>
          <Link to="/search" className="btn primary">Find a player</Link>
        </div>
      </div>
    )
  }
  if (sports === null) return <span className="spinner" aria-label="Loading" />
  if (sports.length === 0) {
    markWelcomed()  // nothing to offer: Today's own start, finding a player
    return <Navigate to="/" replace />
  }
  const pick = (key: string) => setPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]))
  const offered = picked.length ? sports.filter((s) => picked.includes(s.key)) : sports  // none picked: every sport

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
        <div className="tiles" role="group" aria-label="Sports">
          {sports.map((s) => {
            const on = picked.includes(s.key)
            return (
              <button key={s.key} type="button" className="tile pick sport" data-sport={s.key} aria-pressed={on} onClick={() => pick(s.key)}>
                <span className="corner">{on ? <CheckIcon width={20} height={20} /> : <PlusIcon width={20} height={20} />}</span>
                <span className="dsp name" style={{ '--ems': longestWordEms(s.label) } as CSSProperties}>{s.label}</span>
              </button>
            )
          })}
        </div>
      ) : (
        <>
          <LiveNow first={picked} />
          <StarterPicker sports={offered} />
        </>
      )}

      <div className="row welcome-foot">
        {step === 1
          ? <button type="button" className="btn link" onClick={finish}>Skip</button>
          : <button type="button" className="btn link" onClick={() => setStep(1)}>Back</button>}
        {step === 1
          ? <button type="button" className="btn primary" onClick={() => setStep(2)}>Next</button>
          : <button type="button" className="btn primary" onClick={finish}>{cards.length ? `Done · ${cards.length} followed` : 'Done'}</button>}
      </div>
    </>
  )
}

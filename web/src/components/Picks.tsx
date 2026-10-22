import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Prediction } from '../api'
import { useNow } from '../clock'
import { teamKey, useLooks } from '../look'
import { isOpen, outcomeOf, withWhom } from '../predictions'
import { shareQuestion } from '../share'
import { sportKey } from '../sports'
import { callsChanged } from './Calls'
import { Crest } from './Crest'
import { CheckIcon } from './Icons'
import { ShareButton } from './Share'

/**
 * A game's questions as the fan answers them (sports_follow/predictions.py), each kept as the server last
 * answered it: a pick shows at once, and comes back with how fans split. A newer copy from the page (its next
 * look) takes over.
 */
export function usePredictions(given: Prediction[] | undefined) {
  const [shown, setShown] = useState<Prediction[]>(given ?? [])
  useEffect(() => { setShown(given ?? []) }, [given])
  const replace = (q: Prediction) => setShown((all) => all.map((x) => (x.id === q.id ? q : x)))
  return { shown, replace }
}

/** The split as a bar: each answer's share, the fan's own in its sport's colour, the rest in greys. */
function SplitBar({ q }: { q: Prediction }) {
  const said = q.options.map((o) => `${o.label} ${o.share ?? 0}%`).join(', ')
  return (
    <div className="split" role="img" aria-label={said}>
      {q.options.map((o, i) => (
        <span key={o.key} className={`split-seg${o.key === q.mine ? ' mine' : i % 2 ? ' even' : ''}`} style={{ flexGrow: Math.max(o.share ?? 0, 1) }} />
      ))}
    </div>
  )
}

/**
 * One question: its answers to tap while it's open (the split shows once the fan has picked: the crowd doesn't
 * pick for them), then closed with their pick, then settled, right or wrong, and what it scored. A pick made or
 * taken back tells the cards that show the fan's calls (Calls.tsx).
 */
export function PickCard({ q, onChange }: { q: Prediction; onChange: (q: Prediction) => void }) {
  const now = useNow()
  const [busy, setBusy] = useState<string | null>(null)
  const looks = useLooks(q.options.map((o) => teamKey(q.match, o.key)))  // a side's crest, where an option is a team
  const [error, setError] = useState<string | null>(null)
  const open = isOpen(q, now)
  const outcome = outcomeOf(q)

  const choose = async (key: string) => {
    if (!open || busy) return
    setBusy(key)
    setError(null)
    try {
      onChange((key === q.mine ? await api.unpick(q.id) : await api.pick(q.id, key)).question)
      callsChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (outcome) {
    return (
      <article className={`pick-card settled ${outcome.kind}`} data-sport={sportKey(q.sport)} aria-label={q.title}>
        <span className="tag">{q.title}</span>
        <span className="row" style={{ alignItems: 'flex-end' }}>
          <span className="stack">
            <span className="dsp pick-head">{outcome.head}</span>
            <span className="small" style={{ fontWeight: 700 }}>{outcome.line}</span>
          </span>
          {outcome.kind === 'right' && <span className="num pick-points">+{q.points}</span>}
        </span>
        {outcome.kind === 'right' && <ShareButton {...shareQuestion(q)} className="btn small on-sport" text="Share it" />}
      </article>
    )
  }

  const cols = Math.min(q.options.length, 3)
  return (
    <article className="pick-card" data-sport={sportKey(q.sport)} aria-label={q.title}>
      <span className="row">
        <span className="eyebrow">{open ? 'Your pick' : 'Picks closed'}</span>
        <span className="tiny muted" style={{ fontWeight: 800 }}>{open ? 'Locks when it starts' : q.mine ? 'Settled when it ends' : ''}</span>
      </span>
      <span className="dsp pick-q">{q.title}</span>
      <div className="pick-options" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }} role="group" aria-label={q.title}>
        {q.options.map((o) => {
          const mine = o.key === q.mine
          const look = looks[teamKey(q.match, o.key) ?? '']
          return (
            <button key={o.key} type="button" className="pick-option" aria-pressed={mine} disabled={!open || busy !== null} onClick={() => { void choose(o.key) }}>
              {busy === o.key ? <span className="spinner" /> : mine ? <CheckIcon width={14} height={14} /> : null}
              {look && <Crest look={look} name={o.label} size={30} disc={look.logo ? '#ffffff' : null} />}
              <span className="pick-label">{o.label}</span>
              {o.share != null && <span className="tiny" style={{ fontWeight: 800 }}>{o.share}%</span>}
            </button>
          )
        })}
      </div>
      {q.mine && q.options.some((o) => o.share != null) && <SplitBar q={q} />}
      {q.mine ? (
        <span className="small" style={{ fontWeight: 800 }}>
          {withWhom(q) || `You said ${q.options.find((o) => o.key === q.mine)?.label ?? ''}. The split shows once ${q.split_from} fans have picked.`}
          {open && q.worth != null ? ` Worth ${q.worth} if right.` : ''}
        </span>
      ) : (
        <span className="tiny muted" style={{ fontWeight: 700 }}>
          {open ? `${q.picks ? `${q.picks.toLocaleString()}+ fans have picked. ` : ''}See how fans split once you pick.` : `Closed. ${(q.picks ?? 0).toLocaleString()} picked.`}
        </span>
      )}
      {q.mine && open && (
        <span className="row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
          <ShareButton {...shareQuestion(q)} className="btn small primary" text="Share the split" />
          <span className="tiny muted" style={{ fontWeight: 700 }}>Tap your pick again to take it back.</span>
        </span>
      )}
      {error && <div className="error-box" role="alert">{error}</div>}
    </article>
  )
}

/**
 * A match page's questions (usePredictions, kept by the page): "Call it", each open one to answer, each closed or
 * settled one as it stands; and a way to play with friends. None for a game whose sport asks nothing. On a wide
 * screen the page stands it in the side column, beside the game (Side.tsx).
 */
export function Picks({ shown, replace }: ReturnType<typeof usePredictions>) {
  if (!shown.length) return null
  return (
    <section className="section" aria-label="Call it">
      <div className="section-head">
        <h2 className="dsp section-title">Call it</h2>
        <Link to="/me/leagues" className="link small">Play with friends</Link>
      </div>
      {shown.map((q) => <PickCard key={q.id} q={q} onChange={replace} />)}
      <span className="tiny muted">Bragging rights only: no money, no prizes.</span>
    </section>
  )
}

/**
 * A campaign page's "Call it": its players' games to call, each as on its match page, and a league to start with
 * friends. None while there's nothing to call (the questions come a day before each game).
 */
export function CampaignCalls({ questions }: { questions: Prediction[] | undefined }) {
  const { shown, replace } = usePredictions(questions)
  if (!shown.length) return null
  return (
    <section className="section" aria-label="Call it">
      <h2 className="dsp section-title">Call it</h2>
      {shown.map((q) => (
        <div key={q.id} className="stack" style={{ gap: 6 }}>
          <Link to={`/match/${q.match}`} className="eyebrow">{q.game}</Link>
          <PickCard q={q} onChange={replace} />
        </div>
      ))}
      <div className="card card-pad stack friends-card" style={{ gap: 12 }}>
        <span className="dsp" style={{ fontSize: 30 }}>Play with your friends</span>
        <span className="small muted" style={{ fontWeight: 700 }}>Start a league, send the link to your group, and see who calls it best. Bragging rights only: no money, no prizes.</span>
        <Link to="/me/leagues" className="btn primary" style={{ alignSelf: 'flex-start' }}>Start a league</Link>
      </div>
    </section>
  )
}

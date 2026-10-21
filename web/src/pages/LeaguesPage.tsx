import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { useAccount } from '../account'
import { api, ApiError, type FriendLeague, type FriendLeagueSummary, type LeagueInvite } from '../api'
import { APP_NAME } from '../app'
import { SectionHead } from '../components/Blocks'
import { BackIcon } from '../components/Icons'
import { PickCard } from '../components/Picks'
import { ShareButton } from '../components/Share'
import { shareInvite } from '../share'
import { SignIn } from './AccountPage'

/**
 * Leagues of friends calling games together (sports_follow/predictions.py): bragging rights only, no money, no
 * prizes. Calling a game needs no account; making or joining a league does, so friends see a name they know and
 * keep their place on every phone.
 */

const RULES = 'Bragging rights only: no money, no prizes.'

function Head({ title, back = '/' }: { title: string; back?: string }) {
  return (
    <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
      <Link to={back} className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
      <h1 className="dsp page-title">{title}</h1>
    </div>
  )
}

/** How a league works, in three lines: for a fan deciding whether to start or join one. */
function HowItWorks() {
  return (
    <section className="stack" style={{ gap: 10 }} aria-label="How it works">
      <span className="dsp" style={{ fontSize: 26 }}>How it works</span>
      <ol className="stack small" style={{ gap: 8, margin: 0, paddingLeft: 20, fontWeight: 700 }}>
        <li>Before each game, call it. One tap.</li>
        <li>Picks close as it starts. Your friends' picks show then.</li>
        <li>{APP_NAME} scores it when it ends: the fewer fans who called it, the more a right call is worth.</li>
      </ol>
      <span className="tiny muted" style={{ fontWeight: 700 }}>{RULES}</span>
    </section>
  )
}

/** A name to give: the league's, or the fan's in it. */
function NameField({ id, label, value, onChange, max }: { id: string; label: string; value: string; onChange: (v: string) => void; max: number }) {
  return (
    <>
      <label htmlFor={id} className="title">{label}</label>
      <input id={id} className="field" type="text" autoComplete="off" maxLength={max} required value={value} onChange={(e) => onChange(e.target.value)} />
    </>
  )
}

// ---------------------------------------------------------------- the fan's leagues, and starting one

/** /me/leagues: the fan's leagues with their place in each, and starting one; for a fan not signed in, signing in first. */
export function LeaguesPage() {
  const { me } = useAccount()
  const navigate = useNavigate()
  const [leagues, setLeagues] = useState<FriendLeagueSummary[] | null>(null)
  const [name, setName] = useState('')
  const [member, setMember] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const signedIn = me?.signed_in === true

  useEffect(() => {
    if (signedIn) api.myLeagues().then((r) => setLeagues(r.leagues)).catch(() => setLeagues([]))
  }, [signedIn])

  const start = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { league } = await api.makeLeague(name.trim(), member.trim())
      navigate(`/league/${league.code}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  if (me === null) return <><Head title="Leagues" /><span className="spinner" /></>
  if (!signedIn) {
    return (
      <>
        <Head title="Leagues" />
        <SignIn failed={null} back="/me/leagues" intro={(
          <section className="league-band" aria-label="Play with friends">
            <span className="dsp league-name">Play with friends</span>
            <p style={{ fontWeight: 600, maxWidth: '38ch' }}>Start a league, send the link to your group, and see who calls the games best. Sign in first, so your friends see a name they know.</p>
          </section>
        )} />
        <HowItWorks />
      </>
    )
  }
  return (
    <>
      <Head title="Leagues" />
      {leagues === null ? <span className="spinner" /> : leagues.length > 0 && (
        <ul className="card list">
          {leagues.map((l) => (
            <li key={l.code}>
              <Link to={`/league/${l.code}`} className="row">
                <span className="stack">
                  <span className="title">{l.name}</span>
                  <span className="small muted">{l.members} {l.members === 1 ? 'member' : 'members'} · {l.points} points</span>
                </span>
                {l.place != null && <span className="num" style={{ fontSize: 30 }}>{ordinal(l.place)}</span>}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <form className="card card-pad stack" style={{ gap: 12 }} onSubmit={start} aria-label="Start a league">
        <span className="dsp" style={{ fontSize: 26 }}>Start a league</span>
        <NameField id="league-name" label="The league's name" value={name} onChange={setName} max={60} />
        <NameField id="league-member" label="Your name in it" value={member} onChange={setMember} max={40} />
        {error && <div className="error-box" role="alert">{error}</div>}
        <button type="submit" className="btn primary" disabled={busy || !name.trim() || !member.trim()}>{busy && <span className="spinner" />}Start it</button>
        <span className="small muted">Then share its link with your friends. They see your name, never your email.</span>
      </form>
      <HowItWorks />
    </>
  )
}

function ordinal(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${tail}`
}

// ---------------------------------------------------------------- one league

/** A league's band: a tag over its name in big type, and a line under it. */
function Band({ name, tag, line }: { name: string; tag: string; line?: ReactNode }) {
  return (
    <header className="league-band">
      <span className="tag">{tag}</span>
      <span className="dsp league-name">{name}</span>
      {line && <span className="small muted" style={{ fontWeight: 700 }}>{line}</span>}
    </header>
  )
}

/** /league/<code>: a league's table and its members' calls, for its members; anyone else is sent to its invite. */
export function FriendLeaguePage() {
  const { code = '' } = useParams()
  const navigate = useNavigate()
  const [league, setLeague] = useState<FriendLeague | null>(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => api.friendLeague(code).then((r) => setLeague(r.league)).catch((e: Error) => {
    if (e instanceof ApiError && e.status === 404) setMissing(true)
    else setError(e.message)
  }), [code])
  useEffect(() => { void load() }, [load])

  if (missing) return <Navigate to={`/j/${code}`} replace />
  if (!league) return <><Head title="League" back="/me/leagues" />{error ? <div className="error-box" role="alert">{error}</div> : <span className="spinner" />}</>
  const leave = async () => {
    if (!window.confirm(`Leave ${league.name}? Your calls stay yours; you leave its table.`)) return
    await api.leaveLeague(league.code).catch(() => {})
    navigate('/me/leagues')
  }
  const link = `${window.location.host}/j/${league.code}`
  return (
    <>
      <Head title="League" back="/me/leagues" />
      <Band name={league.name} tag={`${league.members} ${league.members === 1 ? 'member' : 'friends'}`} />
      <section className="invite-box" aria-label="Invite">
        <span className="eyebrow">Invite link</span>
        <span style={{ fontWeight: 800, fontSize: 17, overflowWrap: 'anywhere' }}>{link}</span>
        <ShareButton {...shareInvite(league, APP_NAME)} className="btn primary" text="Share invite" onShared={() => { void api.leagueShared(league.code).catch(() => {}) }} />
      </section>

      <section className="section" aria-label="Table">
        <SectionHead title="Table" />
        <ol className="card league-table">
          {league.table.map((r) => (
            <li key={`${r.place}-${r.name}`} className={r.you ? 'you' : undefined}>
              <span className="num">{r.place}</span>
              <span className="stack">
                <span className="title">{r.you ? `${r.name} (you)` : r.name}</span>
                <span className="tiny muted" style={{ fontWeight: 700 }}>{r.played ? `${r.right} of ${r.played} right` : 'No calls settled yet'}</span>
              </span>
              <span className="num">{r.points}</span>
            </li>
          ))}
        </ol>
      </section>

      {league.questions.length > 0 && (
        <section className="section" aria-label="Calls">
          <SectionHead title="Calls" />
          {league.questions.map((q) => (
            <div key={q.id} className="stack" style={{ gap: 8 }}>
              <Link to={`/match/${q.match}`} className="eyebrow">{q.game}</Link>
              <PickCard q={q} onChange={() => { void load() }} />
              {(q.friends ?? []).length > 0 && (
                <div className="friends" aria-label="Friends' picks">
                  {(q.friends ?? []).map((f) => (
                    <span key={f.name} className="friend">{f.you ? 'You' : f.name}{f.option ? ` · ${q.options.find((o) => o.key === f.option)?.label ?? ''}` : ' · picked'}</span>
                  ))}
                </div>
              )}
              {(q.waiting ?? []).length > 0 && <span className="tiny muted" style={{ fontWeight: 700 }}>Still to pick: {(q.waiting ?? []).join(', ')}. Picks show when it starts.</span>}
            </div>
          ))}
        </section>
      )}
      <span className="tiny muted" style={{ fontWeight: 700 }}>{RULES}</span>
      <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} onClick={() => { void leave() }}>Leave this league</button>
    </>
  )
}

// ---------------------------------------------------------------- an invite

/** /j/<code>: what a friend's link opens. Signed in, a name and Join; else signing in first, back here after. */
export function InvitePage() {
  const { code = '' } = useParams()
  const { me } = useAccount()
  const navigate = useNavigate()
  const [invite, setInvite] = useState<LeagueInvite | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [member, setMember] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.leagueInvite(code).then((r) => setInvite(r.invite)).catch((e: Error) => setFailed(e.message)) }, [code])

  if (failed) return <><Head title="League" /><div className="error-box" role="alert">{failed}</div></>
  if (!invite || me === null) return <><Head title="League" /><span className="spinner" /></>
  if (invite.member) return <Navigate to={`/league/${invite.code}`} replace />

  const join = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.joinLeague(invite.code, member.trim())
      navigate(`/league/${invite.code}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }
  const others = invite.members
  const band = (
    <Band name={invite.name} tag="You're invited"
      line={`${invite.by ? `${invite.by} asked you` : "You're asked"} to call the games together on ${APP_NAME} · ${others} in it so far.`} />
  )
  return (
    <>
      <Head title="League" />
      {me.signed_in ? (
        <>
          {band}
          <form className="card card-pad stack" style={{ gap: 12 }} onSubmit={join} aria-label={`Join ${invite.name}`}>
            <NameField id="join-member" label="Your name in the league" value={member} onChange={setMember} max={40} />
            {error && <div className="error-box" role="alert">{error}</div>}
            <button type="submit" className="btn primary" disabled={busy || !member.trim()}>{busy && <span className="spinner" />}Join {invite.name}</button>
            <span className="small muted">Friends see this name, never your email.</span>
          </form>
        </>
      ) : (
        <SignIn failed={null} back={`/j/${invite.code}`} intro={(
          <>
            {band}
            <span className="dsp" style={{ fontSize: 26 }}>Sign in to join</span>
            <span className="small muted" style={{ fontWeight: 700 }}>Calls you made on this device for games still to start come with you; those already under way stay on this device.</span>
          </>
        )} />
      )}
      <HowItWorks />
    </>
  )
}

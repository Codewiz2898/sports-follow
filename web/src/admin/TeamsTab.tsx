import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SearchIcon } from '../components/Icons'
import { admin, type AdminTeam, type TeamColours } from './api'
import { Note, useFailure } from './shared'

const TYPING_MS = 250

/** Teams' looks (sports_follow/logos.py): find a team, see its logo on its colours as the match page draws its side, set
 * its two colours where the ones read from its logo are wrong, or take its logo down. */
export function TeamsTab() {
  const failed = useFailure()
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const [typed, setTyped] = useState(query)
  const [rows, setRows] = useState<AdminTeam[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => { if (typed !== query) setParams(typed ? { q: typed } : {}, { replace: true }) }, TYPING_MS)
    return () => clearTimeout(timer)
  }, [typed, query, setParams])
  useEffect(() => {
    if (!query.trim()) {
      setRows([])
      return
    }
    let current = true
    admin.teams(query).then((r) => current && setRows(r.teams), (e) => current && setError(failed(e)))
    return () => { current = false }
  }, [query, failed])

  const changed = (team: AdminTeam) => setRows((old) => old?.map((t) => (t.id === team.id ? team : t)) ?? null)

  return (
    <section className="section" aria-label="Teams">
      <Note>
        Each team's logo comes from its source and its two colours are read from the logo; the match page draws each side in them.
        Where they're wrong (a crest's gold trim read as the team's colour), set them here. Taking a logo down shows our badge (the
        team's short code) in its place everywhere, at once; turn a whole sport's logos off in Sports.
      </Note>
      <form className="search" role="search" onSubmit={(e) => e.preventDefault()}>
        <SearchIcon width={18} height={18} style={{ flexShrink: 0 }} />
        <label htmlFor="admin-teams-q" className="sr-only">Find a team by name</label>
        <input id="admin-teams-q" type="search" autoComplete="off" spellCheck={false} placeholder="Find a team: Barcelona, India, T1" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </form>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!rows ? <div className="card empty"><span className="spinner" /></div> : !rows.length ? (
        <div className="card empty small">{query ? `No team is called "${query}".` : 'Type a team\'s name.'}</div>
      ) : (
        <div className="admin-grid">
          {rows.map((t) => <TeamCard key={t.id} team={t} onChange={changed} onError={(e) => setError(failed(e))} />)}
        </div>
      )}
    </section>
  )
}

function TeamCard({ team, onChange, onError }: { team: AdminTeam; onChange: (t: AdminTeam) => void; onError: (e: unknown) => void }) {
  const shown = team.look.colours
  const [fill, setFill] = useState(shown?.fill ?? '#ffffff')
  const [accent, setAccent] = useState(shown?.accent ?? '#0d0d0d')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setFill(team.look.colours?.fill ?? '#ffffff')
    setAccent(team.look.colours?.accent ?? '#0d0d0d')
  }, [team.look.colours])

  const save = async (change: { colours?: TeamColours | null; hidden?: boolean }) => {
    setBusy(true)
    try {
      onChange((await admin.setTeamLook(team.id, change)).team)
    } catch (e) {
      onError(e)
    } finally {
      setBusy(false)
    }
  }
  // A team without colours is drawn in its sport's, until colours are picked for it.
  const edited = shown ? fill !== shown.fill || accent !== shown.accent : fill !== '#ffffff' || accent !== '#0d0d0d'
  const half = shown || edited ? `linear-gradient(108deg, ${fill} 0 78%, ${accent} 78% 86%, transparent 86%)` : 'var(--sport)'

  return (
    <article className="card admin-team" data-sport={team.sport}>
      <div className="admin-team-half" style={{ background: half }}>
        {team.look.logo ? <img src={team.look.logo} alt="" width={56} height={56} /> : <span className="admin-team-badge">{team.look.short || team.name.slice(0, 3)}</span>}
      </div>
      <div className="stack" style={{ gap: 2 }}>
        <span className="title">{team.name}</span>
        <span className="small muted">{[team.sport, team.detail, team.key].filter(Boolean).join(' · ')}</span>
        <span className="small muted">
          {team.hidden ? 'Logo taken down' : { ok: 'Logo kept', failed: "Its logo couldn't be read", refused: 'Its logo was refused', waiting: 'Logo not fetched yet', none: 'Its source has no logo' }[team.logo_status]}
          {' · '}{team.look.set ? 'colours set here' : shown ? 'colours read from its logo' : 'no colours: its sport\'s'}
        </span>
      </div>
      <div className="row" style={{ gap: 10, flexWrap: 'wrap', justifyContent: 'flex-start' }}>
        <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          Colour <input type="color" value={fill} onChange={(e) => setFill(e.target.value)} />
        </label>
        <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          Accent <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} />
        </label>
      </div>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap', justifyContent: 'flex-start' }}>
        <button type="button" className="btn small" disabled={busy || !edited} onClick={() => void save({ colours: { fill, accent } })}>Save colours</button>
        {team.look.set && <button type="button" className="btn small" disabled={busy} onClick={() => void save({ colours: null })}>Use its logo's</button>}
        <button type="button" className="btn small danger" disabled={busy} onClick={() => void save({ hidden: !team.hidden })}>{team.hidden ? 'Show its logo' : 'Take its logo down'}</button>
      </div>
    </article>
  )
}

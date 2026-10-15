import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAccount } from '../account'
import { api, type AthletePreview, type SearchResult } from '../api'
import { PlayerBand, ResultsList, SectionHead, StatGrid, UpcomingList } from '../components/Blocks'
import { BackIcon, PlusIcon } from '../components/Icons'
import { useSearchActions } from '../components/Search'
import { safeHref } from '../format'

/**
 * An athlete from search, before following: who they are, their next game, their last game with
 * their line, and their stats, all from the live source. Following binds exactly this athlete.
 */
export function AthletePage() {
  const { system = '', id = '' } = useParams()
  const [params] = useSearchParams()
  const league = params.get('league')
  const sport = params.get('sport')
  const fromRegistry = system === 'wikidata'
  const location = useLocation()
  const navigate = useNavigate()
  const hint = (location.state as { result?: SearchResult; back?: string } | null) ?? {}
  const [preview, setPreview] = useState<AthletePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const actions = useSearchActions()

  useEffect(() => {
    const ctrl = new AbortController()
    setPreview(null)
    setError(null)
    api.athlete(system, id, { league, sport }, ctrl.signal)
      .then((r) => {
        // Already on Sports Follow: their page is the preview.
        if ('player_id' in r) navigate(`/player/${r.player_id}`, { replace: true })
        else setPreview(r)
      })
      .catch((e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
    return () => ctrl.abort()
  }, [system, id, league, sport, navigate])

  const name = preview?.name ?? hint.result?.name ?? ''
  const born = preview?.born ? `born ${preview.born.slice(0, 4)}` : hint.result?.born ? `born ${hint.result.born}` : null
  const athleteSport = preview?.sport ?? hint.result?.sport ?? sport
  // Who they are, after the sport (the band names the sport on its own).
  const about = preview
    ? [...preview.teams, preview.league && ['nba', 'wnba', 'atp', 'wta'].includes(preview.league) ? preview.league.toUpperCase() : null, preview.country, born].filter(Boolean).join(' · ')
    : hint.result?.detail ?? ''
  const detail = [athleteSport, about].filter(Boolean).join(' · ')
  // A registry athlete found in a live source follows by that source's id; one no source has, by registry id.
  const follow = () => actions.follow({
    key: `${system}:${id}`, name, sport: athleteSport ?? '', detail,
    system: preview?.system ?? (fromRegistry ? null : system), athlete_id: preview?.athlete_id ?? (fromRegistry ? null : id),
    league: preview?.league ?? league, qid: preview?.qid ?? (fromRegistry ? id : null),
    following: false, followers: 0, live_scores: true, inactive: false, exact: true,
  })
  const noSource = preview?.live === false
  // Without the research agent (GET /api/config), a page comes from a live source or not at all.
  const research = useAccount().config?.research !== false

  return (
    <>
      <PlayerBand
        name={name || 'Athlete'}
        sport={athleteSport}
        meta={about}
        back={<Link to={hint.back ?? '/search'} className="btn icon" aria-label="Back to search"><BackIcon width={20} height={20} /></Link>}
        actions={
          <button type="button" className="btn primary" disabled={!name || Boolean(actions.pending) || (noSource && !research)} onClick={follow}>
            {actions.pending ? <span className="spinner" /> : <PlusIcon width={16} height={16} />}Follow
          </button>
        }
      >
        <p className="small soft" style={{ fontWeight: 600 }}>
          {noSource
            ? research
              ? 'No live source has this athlete yet, so the research agent builds their page (1 to 3 minutes). That uses one of your AI researches for today.'
              : "No live source has this athlete yet, so there's no page to follow."
            : research ? 'Live scores start right away. News and background follow in about two minutes.' : 'Live scores, games and stats start right away.'}
        </p>
      </PlayerBand>
      {(error || actions.error) && <div className="error-box">{error || actions.error}</div>}

      {!preview && !error && (
        <div className="row" style={{ justifyContent: 'flex-start' }}><span className="spinner" /><span className="muted small">{fromRegistry ? 'Finding them in the live sources…' : 'Reading their games and stats…'}</span></div>
      )}

      {preview && !noSource && (
        <>
          {preview.system !== 'lichess_chess' && (
            <>
              <section className="section" aria-label="Next">
                <SectionHead title="Next" />
                <UpcomingList items={preview.next ? [preview.next] : []} empty="No game scheduled in the source yet." />
              </section>
              <section className="section" aria-label="Last game">
                <SectionHead title="Last game" />
                <ResultsList items={preview.last ? [preview.last] : []} teams={preview.teams} />
              </section>
            </>
          )}
          <section className="section" aria-label="Stats">
            <SectionHead title="Stats" />
            <StatGrid stats={preview.stats} />
            <p className="tiny muted">
              {[preview.stats_note, `from ${preview.source}`].filter(Boolean).join(' · ')}
              {safeHref(preview.source_url) && <> · <a href={safeHref(preview.source_url)} target="_blank" rel="noreferrer" className="link">Profile</a></>}
            </p>
            {preview.system === 'lichess_chess' && <p className="tiny muted">Their tournaments and live boards are found once you follow.</p>}
          </section>
        </>
      )}
    </>
  )
}

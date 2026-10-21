import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, type Clip, type ClipsOf } from '../api'
import { APP_NAME } from '../app'
import { useNow } from '../clock'
import { relative } from '../format'
import { shareClip } from '../share'
import { sportKey } from '../sports'
import { SectionHead } from './Blocks'
import { ShareButton } from './Share'

/**
 * Clips published from the Mac app (GET /api/clips): a game's, a competition's or a player's, newest first.
 * `null` while asked; an empty list when there are none, or the API couldn't say (a section then just isn't
 * shown: clips are a bonus on these pages, never in the way).
 */
export function useClips(of: ClipsOf): Clip[] | null {
  const [clips, setClips] = useState<Clip[] | null>(null)
  const key = `${of.match ?? ''}|${of.competition ?? ''}|${of.player ?? ''}`
  useEffect(() => {
    let live = true
    setClips(null)
    api.clips(of).then((r) => { if (live) setClips(r.clips) }).catch(() => { if (live) setClips([]) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return clips
}

/** A page of clips, as the server sends them (clips.PAGE): a shorter one is the last. */
export const CLIPS_PAGE = 30

/** The competition's clips page: its tournament feed. */
export const competitionClips = (competition: string) => `/clips?competition=${encodeURIComponent(competition)}`

/**
 * One clip: the video (it loads when played), what it shows, its game and competition, when it went up, and
 * Share and Report. In its sport's colours, as the rest of the app draws a sport's things.
 */
export function ClipCard({ clip, showGame = true }: { clip: Clip; showGame?: boolean }) {
  const now = useNow()
  const [report, setReport] = useState<'no' | 'sending' | 'done' | 'gone' | 'failed'>('no')
  const sendReport = () => {
    if (!window.confirm('Report this clip? It goes to the people who look after the app, who take down what shouldn’t be up.')) return
    setReport('sending')
    api.reportClip(clip.id, 'fan report')
      .then(() => setReport('done'))
      .catch((e) => setReport(e instanceof ApiError && e.status === 404 ? 'gone' : 'failed'))
  }
  const where = [
    showGame && clip.match ? <Link key="game" to={`/match/${clip.match}`}>{clip.game_title || 'The game'}</Link> : null,
    clip.competition ? <Link key="comp" to={competitionClips(clip.competition)}>{clip.competition}</Link> : null,
    clip.published ? <span key="when">{relative(clip.published, now)}</span> : null,
  ].filter(Boolean)
  return (
    <article className="card clip" data-sport={clip.sport ? sportKey(clip.sport) : undefined} aria-label={clip.caption || clip.line || 'A clip'}>
      <video
        className="clip-video"
        aria-label={`Clip: ${clip.caption || clip.line || clip.game_title || 'a moment'}`}
        controls
        playsInline
        preload="none"
        poster={clip.poster}
        src={clip.video}
        style={{ aspectRatio: `${clip.width} / ${clip.height}` }}
      />
      <div className="clip-body">
        {clip.caption && <p className="title">{clip.caption}</p>}
        {clip.line && <p className="clip-line">{clip.line}</p>}
        {where.length > 0 && (
          <p className="tiny muted clip-where">
            {where.map((w, i) => <span key={i}>{i > 0 && ' · '}{w}</span>)}
          </p>
        )}
        <div className="clip-actions">
          <ShareButton {...shareClip(clip, APP_NAME)} className="btn" text="Share" />
          <button type="button" className="btn" disabled={report === 'sending' || report === 'done' || report === 'gone'} onClick={sendReport}>
            {report === 'done' ? 'Reported' : report === 'sending' ? 'Reporting…' : report === 'failed' ? 'Report again' : 'Report'}
          </button>
        </div>
        {report === 'failed' && <p className="tiny muted" role="status">Couldn’t send the report: try again.</p>}
        {report === 'gone' && <p className="tiny muted" role="status">This clip is already down.</p>}
      </div>
    </article>
  )
}

/**
 * A page's Clips section: a game's (the game feed, on its match page) or a player's; with a link to the
 * competition's clips when they're all of one. Not shown when there are none.
 */
export function ClipsSection({ of, title = 'Clips', showGame = true }: { of: ClipsOf; title?: string; showGame?: boolean }) {
  const clips = useClips(of)
  if (!clips?.length) return null
  const competitions = new Set(clips.map((c) => c.competition).filter(Boolean))
  const competition = competitions.size === 1 ? [...competitions][0] : null
  return (
    <section className="section" aria-label={title}>
      <SectionHead title={title}>
        {competition && !of.competition && <Link className="small" to={competitionClips(competition)}>All from {competition}</Link>}
      </SectionHead>
      <div className="clip-list">
        {clips.map((c) => <ClipCard key={c.id} clip={c} showGame={showGame} />)}
      </div>
    </section>
  )
}

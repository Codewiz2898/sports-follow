import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, ApiError, type Clip } from '../api'
import { ClipCard, useClips } from '../components/Clips'
import { BackIcon } from '../components/Icons'

/**
 * Clips (/clips): everyone's, newest first, or a competition's (/clips?competition=…), its tournament feed.
 * Older ones a page at a time.
 */
export function ClipsPage() {
  const [params] = useSearchParams()
  const competition = params.get('competition') ?? undefined
  const first = useClips({ competition })
  const [older, setOlder] = useState<Clip[]>([])
  const [more, setMore] = useState<'idle' | 'asking' | 'none'>('idle')
  useEffect(() => { setOlder([]); setMore('idle') }, [competition])
  const clips = [...(first ?? []), ...older]
  const last = clips[clips.length - 1]
  const askOlder = () => {
    if (!last?.published) return
    setMore('asking')
    api.clips({ competition }, last.published)
      .then((r) => { setOlder((o) => [...o, ...r.clips]); setMore(r.clips.length ? 'idle' : 'none') })
      .catch(() => setMore('idle'))
  }
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/" className="btn icon" aria-label="Back"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">{competition ?? 'Clips'}</h1>
      </div>
      {competition && <p className="small muted">Clips of {competition}, newest first.</p>}
      {first === null && <p className="small muted">Looking for clips…</p>}
      {first !== null && clips.length === 0 && <p className="small muted">No clips here yet.</p>}
      <div className="clip-list">
        {clips.map((c) => <ClipCard key={c.id} clip={c} />)}
      </div>
      {clips.length > 0 && more !== 'none' && (
        <button type="button" className="btn" disabled={more === 'asking'} onClick={askOlder}>
          {more === 'asking' ? 'Looking…' : 'Older clips'}
        </button>
      )}
    </>
  )
}

/** One clip (/clip/<id>): where a shared link lands. */
export function ClipPage() {
  const { id = '' } = useParams()
  const [clip, setClip] = useState<Clip | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  useEffect(() => {
    setClip(null)
    setFailed(null)
    api.clip(id).then(setClip).catch((e) => setFailed(e instanceof ApiError && e.status === 404 ? 'This clip isn’t up any more.' : 'Couldn’t load this clip.'))
  }, [id])
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', gap: 14 }}>
        <Link to="/clips" className="btn icon" aria-label="All clips"><BackIcon width={20} height={20} /></Link>
        <h1 className="dsp page-title">Clip</h1>
      </div>
      {failed && <p className="small muted">{failed}</p>}
      {!clip && !failed && <p className="small muted">Loading…</p>}
      {clip && <ClipCard clip={clip} />}
    </>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { day, relative } from '../format'
import { admin, type AdminClip, type ClipKey, type ClipShow } from './api'
import { Note, count, useFailure } from './shared'

const SHOWS: [ClipShow, string][] = [['reported', 'Reported'], ['all', 'All'], ['live', 'Live'], ['hidden', 'Hidden']]
const STATUS: Record<AdminClip['status'], string> = { uploading: 'Uploading', live: 'Live', hidden: 'Hidden', failed: 'Failed', removed: 'Taken down' }

type Asking = { clip: AdminClip } | { key: ClipKey } | null

// Only a live clip's files are at their public address: a hidden one's were moved off it.
const shown = (clip: AdminClip) => clip.status === 'live' && Boolean(clip.poster)

/** Clips published from the Mac app (sports_follow/clips.py): those fans reported first, the most reported first; hide
 * one (off the app, and its files off their public address) or put it back, or take it down for good. And the
 * publishing keys: revoke one, and hide the clips put up with it too when it leaked. */
export function ClipsTab() {
  const failed = useFailure()
  const [show, setShow] = useState<ClipShow>('reported')
  const [clips, setClips] = useState<AdminClip[] | null>(null)
  const [keys, setKeys] = useState<ClipKey[]>([])
  const [setUp, setSetUp] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [asking, setAsking] = useState<Asking>(null)
  const [hideTheirs, setHideTheirs] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => admin.clips(show).then((r) => {
    setClips(r.clips)
    setKeys(r.keys)
    setSetUp(r.set_up)
  }, (e) => setError(failed(e))), [show, failed])
  useEffect(() => { setClips(null); void load() }, [load])

  const act = async (what: string, call: () => Promise<unknown>) => {
    setBusy(what)
    setError(null)
    try {
      await call()
      await load()
    } catch (e) {
      setError(failed(e))
      await load()  // what did change (some of a key's clips hidden before the bucket failed)
    } finally {
      setBusy(null)
      setAsking(null)
    }
  }

  return (
    <section className="section" aria-label="Clips">
      <Note>
        Clips published from the Mac app. Fans report a clip once each, and reports never hide one by themselves. Hiding a clip takes
        it off the app and moves its files off their public address, so a link to them plays nothing; putting it back moves them back.
        Taking one down deletes its files for good. Revoking a publishing key stops it at once; hide its clips too if it leaked. An upload
        that never finished is cleared once its upload links run out (the nightly prune).
      </Note>
      {!setUp && <div className="error-box" role="status">No clips bucket on this server (SPORTS_FOLLOW_CLIPS_*): nothing can be published.</div>}
      {error && <div className="error-box" role="alert">{error}</div>}
      <div className="segmented feed-admin-shows" role="group" aria-label="Show">
        {SHOWS.map(([key, label]) => <button key={key} type="button" aria-pressed={show === key} onClick={() => setShow(key)}>{label}</button>)}
      </div>
      {!clips ? <div className="card empty"><span className="spinner" /></div> : !clips.length ? (
        <div className="card empty small">{show === 'reported' ? 'No live clip has a report.' : 'No clips here.'}</div>
      ) : (
        <ul className="feed-admin">
          {clips.map((clip) => (
            <li key={clip.id} className="card feed-admin-row" data-served={clip.status === 'live' ? '' : undefined} data-bare={shown(clip) ? undefined : ''}>
              {shown(clip) && (
                <a href={clip.video} target="_blank" rel="noreferrer noopener" className="feed-admin-thumb">
                  <img src={clip.poster} alt="" loading="lazy" referrerPolicy="no-referrer" />
                </a>
              )}
              <div className="stack" style={{ gap: 6 }}>
                <span className="title">{clip.caption || clip.line || clip.id}</span>
                <span className="small muted">
                  {[clip.game_title, clip.competition].filter(Boolean).join(' · ') || 'No game'} · by {clip.by ?? 'a key since deleted'} · {relative(clip.created)}
                </span>
                <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 6 }}>
                  <span className={`pill${clip.status === 'live' ? ' ink' : ' soft'}`}>{STATUS[clip.status]}</span>
                  {clip.reports > 0 && <span className="pill danger">{count(clip.reports)} {clip.reports === 1 ? 'report' : 'reports'}</span>}
                </div>
                {asking && 'clip' in asking && asking.clip.id === clip.id ? (
                  <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 6 }}>
                    <span className="small">Take it down for good? Its files are deleted.</span>
                    <button type="button" className="btn small primary danger" disabled={busy != null} onClick={() => void act(clip.id, () => admin.removeClip(clip.id))}>Take down</button>
                    <button type="button" className="btn small" disabled={busy != null} onClick={() => setAsking(null)}>Cancel</button>
                  </div>
                ) : (clip.status === 'live' || clip.status === 'hidden') && (
                  <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 6 }}>
                    <button type="button" className="btn small" disabled={busy === clip.id} onClick={() => void act(clip.id, () => admin.hideClip(clip.id, clip.status === 'live'))}>
                      {busy === clip.id ? <span className="spinner" /> : clip.status === 'live' ? 'Hide' : 'Put back'}
                    </button>
                    <button type="button" className="btn small danger" disabled={busy === clip.id} onClick={() => setAsking({ clip })}>Take down</button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <h3 className="eyebrow">Publishing keys</h3>
      <div className="card">
        {keys.length ? (
          <ul className="list">
            {keys.map((k) => (
              <li key={k.id} className="admin-row">
                <div className="stack">
                  <span className="title">{k.name}</span>
                  <span className="small muted">
                    Made {day(k.created)} · {k.last_used ? `last used ${relative(k.last_used)}` : 'never used'}{k.revoked ? ` · revoked ${day(k.revoked)}` : ''}
                  </span>
                  {asking && 'key' in asking && asking.key.id === k.id && (
                    <div className="stack" style={{ gap: 8, paddingTop: 8 }}>
                      <label className="row small" style={{ justifyContent: 'flex-start', gap: 8 }}>
                        <input type="checkbox" checked={hideTheirs} onChange={(e) => setHideTheirs(e.target.checked)} />
                        Hide the clips put up with it too (it leaked)
                      </label>
                      <div className="row" style={{ justifyContent: 'flex-start', gap: 6 }}>
                        <button type="button" className="btn small primary danger" disabled={busy != null}
                          onClick={() => void act(`key${k.id}`, () => admin.revokeKey(k.id, hideTheirs))}>Revoke</button>
                        <button type="button" className="btn small" disabled={busy != null} onClick={() => setAsking(null)}>Cancel</button>
                      </div>
                    </div>
                  )}
                </div>
                {k.revoked ? <span className="pill soft">Revoked</span> : !(asking && 'key' in asking && asking.key.id === k.id) && (
                  <button type="button" className="btn small danger" onClick={() => { setHideTheirs(false); setAsking({ key: k }) }}>Revoke</button>
                )}
              </li>
            ))}
          </ul>
        ) : <div className="empty small">No keys yet: python -m sports_follow.clips key "&lt;name&gt;" makes one.</div>}
      </div>
    </section>
  )
}

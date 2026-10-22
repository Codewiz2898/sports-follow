import { useCallback, useEffect, useState } from 'react'
import { relative } from '../format'
import { admin, type AdminFeedLayout, type FeedCandidateRow, type FeedChannel, type FeedLayoutView, type FeedShow, type Review } from './api'
import { Note, count, useFailure } from './shared'

const SHOWS: [FeedShow, string][] = [['all', 'All'], ['served', 'Served'], ['held', 'Held back'], ['unjudged', 'Unjudged'], ['reviewed', 'Reviewed'], ['unplayable', "Won't play"]]
const REVIEWS: [Review, string][] = [['approved', 'Approve'], ['hidden', 'Hide'], ['pinned', 'Pin']]
const TRUSTS: [string, string][] = [['', 'Default'], ['1', 'High'], ['0.7', 'Some'], ['0.4', 'Low'], ['0', 'Hidden']]
const LAYOUTS: [AdminFeedLayout, string][] = [['edge', 'Edge to edge'], ['full', 'Full screen']]

/** The Feed's judge at work (sports_follow/rank.py): what the curator found about each player, newest first, with the
 * verdict, the score and the reason fans read; approve, hide or pin each one over the judge. And how far each video
 * channel is trusted. For the labelling pass before launch, and for checking on it after. */
export function FeedTab() {
  const failed = useFailure()
  const [show, setShow] = useState<FeedShow>('all')
  const [rows, setRows] = useState<FeedCandidateRow[] | null>(null)
  const [channels, setChannels] = useState<FeedChannel[]>([])
  const [layout, setLayout] = useState<FeedLayoutView | null>(null)
  const [totals, setTotals] = useState<Record<string, number>>({})
  const [next, setNext] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback((offset = 0) => admin.feed(show, offset).then((r) => {
    setRows((old) => (offset ? [...(old ?? []), ...r.candidates] : r.candidates))
    setChannels(r.channels)
    setLayout(r.layout)
    setTotals(r.totals)
    setNext(r.next)
  }, (e) => setError(failed(e))), [show, failed])
  useEffect(() => { setRows(null); void load() }, [load])

  const review = async (row: FeedCandidateRow, said: Review) => {
    setBusy(`row${row.id}`)
    setError(null)
    try {
      const now = await admin.review(row.id, row.review === said ? null : said)  // pressed again, it's taken back
      setRows((old) => old?.map((r) => (r.id === now.id ? now : r)) ?? null)  // in place: the page stays where it was
      if (show === 'unplayable' && row.unplayable_at && !now.unplayable_at) await load()  // put back: every row of the video leaves the list
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }
  const trust = async (channel: FeedChannel, value: string) => {
    setBusy(channel.channel_id)
    setError(null)
    try {
      setChannels((await admin.channelTrust(channel.channel_id, value === '' ? null : Number(value))).channels)
      await load()
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }

  const draw = async (to: AdminFeedLayout | null) => {
    setBusy('layout')
    setError(null)
    try {
      setLayout((await admin.feedLayout(to)).layout)
    } catch (e) {
      setError(failed(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="section" aria-label="Feed">
      {layout && (
        <div className="card card-pad stack feed-admin-layout" style={{ gap: 10 }} aria-label="Feed layout">
          <span className="eyebrow">How the apps draw the Feed tab</span>
          <div className="segmented" role="radiogroup" aria-label="Feed layout">
            {LAYOUTS.map(([key, label]) => (
              <button key={key} type="button" role="radio" aria-checked={layout.layout === key} disabled={busy === 'layout'}
                onClick={() => { if (layout.layout !== key) void draw(key) }}>{label}</button>
            ))}
          </div>
          <span className="tiny muted">
            {layout.set ? `Set here. The server's setting is ${layout.default === 'full' ? 'full screen' : 'edge to edge'}. ` : `The server's setting (SPORTS_FOLLOW_FEED_LAYOUT). `}
            A change reaches the web app and the Android app within seconds, at their next page of the Feed: no deploy.
          </span>
          {layout.set && <button type="button" className="btn link" style={{ alignSelf: 'flex-start' }} disabled={busy === 'layout'} onClick={() => { void draw(null) }}>Back to the server's setting</button>}
        </div>
      )}
      <Note>
        Every video the curator found about a followed player, with what the judge said. A video is served when it's from a channel its sport
        lists (official ones and a few top creators), about the player, isn't paid for and has no risk, or you approved or pinned it (one the judge hasn't read or
        held back too); hiding it keeps it out whatever the judge said, under every player it was found for. Pinned ones go to the
        top of This week (or of their moment), however old. A channel's trust multiplies its videos' scores: Hidden keeps them out, except one you approved or pinned. A channel no sport lists
        serves nothing whatever its trust: only a video of it you approve or pin. Signed-in
        fans reporting a channel's videos lower its default trust, to half at most; a trust you set stands as it is. A video fans' apps on
        three networks said won't play in the app (its owner keeps it off other sites) is held back for everyone, and listed under Won't play,
        newest first: Put back (or approve or pin) one that does play, and no app's word holds it back again while you have (pressing a pressed
        Approve or Pin takes it back instead). The weekly check drops one YouTube says is gone, private or no longer embeddable.
      </Note>
      <div className="stat-grid" aria-label="Candidates">
        <div className="stat"><span className="dsp num">{count(totals.all ?? 0)}</span><span className="tiny">Candidates</span></div>
        <div className="stat"><span className="dsp num">{count(totals.served ?? 0)}</span><span className="tiny">Served</span></div>
        <div className="stat"><span className="dsp num">{count(totals.unjudged ?? 0)}</span><span className="tiny">Waiting for the judge</span></div>
        <div className="stat">
          <span className="dsp num">{count((totals.review_approved ?? 0) + (totals.review_hidden ?? 0) + (totals.review_pinned ?? 0))}</span>
          <span className="tiny">Reviewed by you</span>
        </div>
        <div className="stat"><span className="dsp num">{count(totals.unplayable ?? 0)}</span><span className="tiny">Taken out by fans' reports</span></div>
      </div>
      {error && <div className="error-box" role="alert">{error}</div>}
      <div className="segmented feed-admin-shows" role="group" aria-label="Show">
        {SHOWS.map(([key, label]) => <button key={key} type="button" aria-pressed={show === key} onClick={() => setShow(key)}>{label}</button>)}
      </div>
      {!rows ? <div className="card empty"><span className="spinner" /></div> : !rows.length ? (
        <div className="card empty small">Nothing here yet. The curator fetches every half hour once YouTube has a key.</div>
      ) : (
        <ul className="feed-admin">
          {rows.map((row) => (
            <li key={row.id} className="card feed-admin-row" data-served={row.served ? '' : undefined}>
              {row.video && (
                <a href={row.url} target="_blank" rel="noreferrer noopener" className="feed-admin-thumb">
                  <img src={`https://i.ytimg.com/vi/${encodeURIComponent(row.video.youtube_id)}/mqdefault.jpg`} alt="" loading="lazy" referrerPolicy="no-referrer" />
                </a>
              )}
              <div className="stack" style={{ gap: 6 }}>
                <a href={row.url} target="_blank" rel="noreferrer noopener" className="title link">{row.title || row.url}</a>
                <span className="small muted">
                  {row.player.name} · {row.channel || 'Unknown channel'} · {row.published ? relative(row.published) : 'no date'}
                  {row.views != null && ` · ${count(row.views)} views`}{row.likes != null && ` · ${count(row.likes)} likes`}
                </span>
                <Verdict row={row} />
                <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 6 }}>
                  {row.unplayable_at && row.review !== 'hidden' && (
                    <button type="button" className="btn small primary" disabled={busy === `row${row.id}`} onClick={() => void review(row, 'approved')}>Put back</button>
                  )}
                  {REVIEWS.map(([said, label]) => (
                    <button key={said} type="button" className={`btn small${row.review === said ? ' primary' : ''}`} aria-pressed={row.review === said}
                      disabled={busy === `row${row.id}`} onClick={() => void review(row, said)}>{label}</button>
                  ))}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {next != null && <button type="button" className="btn small" style={{ alignSelf: 'flex-start' }} onClick={() => void load(next)}>More</button>}
      <h3 className="eyebrow">Channels</h3>
      <div className="card">
        <ul className="list">
          {channels.map((c) => (
            <li key={c.channel_id} className="admin-row">
              <div className="stack">
                <span className="title" style={{ overflowWrap: 'anywhere' }}>{c.name || c.channel_id}</span>
                <span className="small muted">
                  {c.listed ? 'Listed by a sport' : 'No sport lists it'} · trust {c.effective.toFixed(2)} · {count(c.candidates)} found, {count(c.served)} served
                  {c.feedback > 0 && ` · fans' feedback ${c.feedback}`}
                </span>
                <span className="small muted">
                  30 days: {count(c.yield.read)} uploads, {count(c.yield.matched)} about someone followed, {count(c.yield.kept)} kept, {count(c.yield.served)} served
                  {' · '}{c.yield.last_upload ? `last upload ${relative(c.yield.last_upload)}` : 'no upload seen'}
                </span>
              </div>
              <label className="sr-only" htmlFor={`trust-${c.channel_id}`}>Trust for {c.name || c.channel_id}</label>
              <select id={`trust-${c.channel_id}`} className="field compact" style={{ width: 'auto' }} disabled={busy === c.channel_id}
                value={c.trust == null ? '' : String(c.trust)} onChange={(e) => void trust(c, e.target.value)}>
                {TRUSTS.map(([value, label]) => <option key={value} value={value}>{value === '' ? `${label} (${c.default})` : label}</option>)}
              </select>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

/** The verdict in a line of pills, the reason under it, and the score. */
function Verdict({ row }: { row: FeedCandidateRow }) {
  const v = row.verdict
  if (!v) return <span className="pill soft" style={{ alignSelf: 'flex-start' }}>Not judged yet</span>
  return (
    <>
      <div className="row" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 6 }}>
        <span className={`pill ${row.served ? 'ink' : 'soft'}`}>{row.served ? `Served · ${row.score?.toFixed(2)}` : 'Held back'}</span>
        {row.unplayable_at && <span className="pill danger">Fans' apps said it won't play · {relative(row.unplayable_at)}</span>}
        <span className={`pill ${v.about_player === 'no' ? 'danger' : 'soft'}`}>{v.about_player === 'subject' ? 'About them' : v.about_player === 'team' ? 'About their team' : v.about_player === 'mention' ? 'Mentions them' : 'Not about them'}</span>
        <span className="pill soft">{v.kind.replace(/_/g, ' ')}</span>
        <span className="pill soft">by {v.by.replace(/_/g, ' ')}</span>
        <span className="pill soft">interest {v.interest.toFixed(2)}</span>
        {v.sponsored && <span className="pill danger">Sponsored</span>}
        {v.risks.map((r) => <span key={r} className="pill danger">{r}</span>)}
        <span className="pill outline">{v.stage === 'rules' ? 'Rules' : 'Model'}</span>
      </div>
      <span className="small">“{v.reason}”</span>
    </>
  )
}

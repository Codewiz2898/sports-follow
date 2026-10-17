import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Campaign, type SearchResult } from '../api'
import { APP_NAME } from '../app'
import { allFollowed, campaignLive, campaignRows, followAllFailed, followAllWords, keyAlertsLine, schedule } from '../campaign'
import { LiveScoreboard } from '../components/Blocks'
import { BlockError, ErrorBoundary } from '../components/ErrorBoundary'
import { CheckIcon, PlusIcon } from '../components/Icons'
import { useLive } from '../components/LiveNow'
import { useFollowToggle } from '../components/Starters'
import { useNow } from '../clock'
import { countdown, shortName } from '../format'
import { useFollowing } from '../following'
import { usePush } from '../notifications'
import { InstallButton, usePwa } from '../pwa'
import { Backdrop, sportInfo } from '../sports'

const IPHONE = /iPhone|iPad|iPod/
const here = () => Intl.DateTimeFormat().resolvedOptions().timeZone

/**
 * A campaign page, /<slug> (campaigns.py): the page a launch post links to. Its event in a block of the sport's
 * colour; the games on now, when one is; its players, or whole teams, to follow with a tap, with key alerts;
 * then turning alerts on, and on a phone how; and its games, in the home audience's time. `notFound` for an
 * address that isn't a campaign.
 */
export function CampaignPage({ notFound }: { notFound: ReactNode }) {
  const { slug = '' } = useParams()
  const [campaign, setCampaign] = useState<Campaign | null>(null)
  const [missing, setMissing] = useState(false)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => api.campaign(slug).then((c) => { setCampaign(c); setFailed(false) }, (e) => {
    if (e instanceof ApiError && e.status === 404) setMissing(true)
    else setFailed(true)
  }), [slug])
  useEffect(() => { setCampaign(null); setMissing(false); void load() }, [load])

  if (missing) return notFound
  if (!campaign) {
    return failed
      ? <div className="stack" style={{ gap: 12 }}><div className="error-box">Couldn't load this page. Check your connection.</div><button type="button" className="btn primary" style={{ alignSelf: 'flex-start' }} onClick={() => void load()}>Try again</button></div>
      : <span className="spinner" aria-label="Loading" />
  }
  return <CampaignView c={campaign} reload={load} />
}

function CampaignView({ c, reload }: { c: Campaign; reload: () => Promise<void> }) {
  const now = useNow()
  const following = useFollowing()
  const follow = useFollowToggle()
  const live = useLive()
  const info = sportInfo(c.sport)
  const games = schedule(c, now, here())
  const next = games.find((g) => g.next)
  const { mine, others } = campaignLive(c, following.cards, live)
  const anyFollowed = campaignRows(c).some(follow.followed)

  return (
    <div className="campaign" data-sport={c.sport}>
      <header className="campaign-band">
        <Backdrop sport={c.sport} seed={c.slug} />
        <div className="bar">
          <Link to="/" className="campaign-brand" aria-label={`${APP_NAME}, Today`}>{APP_NAME}</Link>
          {next && <span className="pill on-sport">In {countdown(next.start_utc, now)}</span>}
        </div>
        <div className="stack" style={{ gap: 10 }}>
          <span className="tag soft">{c.event || info.label}</span>
          <h1 className="dsp campaign-title">{c.title}</h1>
        </div>
        {c.lines.length > 0 && <div className="campaign-lines">{c.lines.map((line) => <p key={line}>{line}</p>)}</div>}
        {next && <p className="campaign-next"><b>{next.label}</b> · {next.day}, {next.time} {next.zone}</p>}
      </header>

      {(mine.length > 0 || others.length > 0) && (
        <section className="section" aria-label="Live now">
          <ErrorBoundary fallback={<BlockError />} resetKey={live}>
            {mine.map((card) => (
              <Link key={card.player_id} to={`/player/${card.player_id}`} className="stack">
                <LiveScoreboard live={card.live} playerName={card.player.name} sport={card.player.sport} teams={card.player.teams} compact seed={`${c.slug}:${card.live.event_id ?? card.player_id}`} />
              </Link>
            ))}
            {others.map((g) => <LiveScoreboard key={g.key} live={g.live} playerName="" sport={c.sport} compact seed={g.key} />)}
          </ErrorBoundary>
        </section>
      )}

      <FollowBlocks c={c} follow={follow} reload={reload} keyLine={keyAlertsLine(info)} />

      {anyFollowed && <AlertsPrompt />}

      <section className="section" aria-label="Games">
        <h2 className="dsp section-title">{games.length === 1 ? 'The game' : 'Games'}</h2>
        {games.length ? (
          <ol className="campaign-games">
            {games.map((g) => (
              <li key={`${g.start_utc}-${g.label}`} className={g.next ? 'next' : g.past ? 'past' : undefined}>
                <span className="dsp label">{g.label}</span>
                <span className="stack" style={{ gap: 2, alignItems: 'flex-end', textAlign: 'right' }}>
                  <span className="when">{g.today ? 'Today' : g.day} · {g.time} {g.zone}</span>
                  {g.yours && <span className="tiny muted">{g.yours}</span>}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted" style={{ fontWeight: 600 }}>The schedule is coming soon.</p>
        )}
      </section>

      <HowToGetAlerts />
    </div>
  )
}

type Follow = ReturnType<typeof useFollowToggle>

/**
 * The players to follow, side by side in the sport's colour, each with a tap, and all of them with one; then
 * each whole team: its roster's players, and its whole roster in one tap (POST /api/campaigns/<slug>/follow).
 * Following is with key alerts, which the sport words (`keyLine`). A tap on all of them followed unfollows them.
 */
function FollowBlocks({ c, follow, reload, keyLine }: { c: Campaign; follow: Follow; reload: () => Promise<void>; keyLine: string }) {
  const following = useFollowing()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const toggleOne = (r: SearchResult) => void follow.toggle(r).then(reload)
  const toggleAll = async (key: string, rows: SearchResult[], which: { team: string } | { players: true }, ask: string) => {
    if (busy) return
    setBusy(key)
    setError(null)
    try {
      if (allFollowed(rows, follow.followed)) {
        // Some may have been followed elsewhere first, with their own alerts: ask before they all go.
        if (!window.confirm(ask)) return
        for (const r of rows) if (r.player_id != null && following.isFollowing(r.player_id)) await following.unfollow(r.player_id)
      } else {
        const done = await api.campaignFollow(c.slug, which)
        await following.reload()
        if (done.failed) setError(followAllFailed(done.failed))
      }
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (!c.players.length && !c.teams.length) {
    return <div className="card card-pad"><span className="title">Who to follow is coming soon.</span></div>
  }
  const own = followAllWords(c)
  return (
    <section className="section" aria-label="Follow">
      {c.players.length > 0 && (
        <>
          <div className={`campaign-players${c.players.length === 2 ? ' versus' : ''}`}>
            {c.players.map((r) => {
              const on = follow.followed(r)
              return (
                <button key={r.key} type="button" className="tile pick campaign-player" data-sport={c.sport} aria-pressed={on} aria-busy={follow.busy(r)} aria-label={`Follow ${r.name}`} onClick={() => toggleOne(r)}>
                  <span className="corner">{follow.busy(r) ? <span className="spinner" /> : on ? <CheckIcon width={20} height={20} /> : <PlusIcon width={20} height={20} />}</span>
                  <span className="dsp name">{r.name}</span>
                  {r.detail && <span className="status">{r.detail}</span>}
                </button>
              )
            })}
          </div>
          {c.players.length > 1 && (
            <FollowAll on={allFollowed(c.players, follow.followed)} busy={busy === 'players'} onClick={() => void toggleAll('players', c.players, { players: true }, own.ask)}
              label={own.label} done={own.done} />
          )}
        </>
      )}
      {c.teams.length > 0 && <div className="campaign-teams">{c.teams.map((t) => { const words = followAllWords(c, t); return (
        <article key={t.team_id} className="campaign-team" data-sport={c.sport} aria-label={t.name}>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <h2 className="dsp team-name">{t.name}</h2>
            {t.game && <span className="tag soft">{t.game}</span>}
          </div>
          {t.players.length ? (
            <div className="who" role="group" aria-label={`${t.name}'s players`}>
              {t.players.map((r) => {
                const on = follow.followed(r)
                return (
                  <button key={r.key} type="button" className="follow-pill" aria-pressed={on} aria-busy={follow.busy(r)} aria-label={`Follow ${r.name}`} onClick={() => toggleOne(r)}>
                    {follow.busy(r) ? <span className="spinner" /> : on ? <CheckIcon width={14} height={14} /> : <PlusIcon width={14} height={14} />}
                    {shortName(r.name)}
                  </button>
                )
              })}
            </div>
          ) : (
            <span className="small soft">The line-up is coming soon.</span>
          )}
          {t.players.length > 0 && (
            <FollowAll on={allFollowed(t.players, follow.followed)} busy={busy === t.team_id} onClick={() => void toggleAll(t.team_id, t.players, { team: t.team_id }, words.ask)}
              label={words.label} done={words.done} onSport />
          )}
        </article>
      ) })}</div>}
      <p className="small muted" style={{ fontWeight: 600 }}>{keyLine}</p>
      {(error || follow.error) && <div className="error-box">{error || follow.error}</div>}
    </section>
  )
}

function FollowAll({ on, busy, onClick, label, done, onSport }: { on: boolean; busy: boolean; onClick: () => void; label: string; done: string; onSport?: boolean }) {
  return (
    <button type="button" className={`btn primary campaign-all${onSport ? ' on-sport' : ''}`} aria-pressed={on} aria-busy={busy} onClick={onClick}>
      {busy ? <span className="spinner" /> : on ? <CheckIcon width={18} height={18} /> : <PlusIcon width={18} height={18} />}
      {on ? done : label}
    </button>
  )
}

/** After a follow here: turn alerts on, on this device (usePush), or, on an iPhone's browser, how to first. */
function AlertsPrompt() {
  const push = usePush()
  if (push.on) {
    return <div className="campaign-alerts on" role="status"><CheckIcon width={18} height={18} /><span>Alerts are on for this device.</span></div>
  }
  return (
    <div className="campaign-alerts" role="region" aria-label="Alerts">
      <span className="title">Now turn alerts on</span>
      {push.unavailable ? (
        <span className="small">{push.unavailable}</span>
      ) : (
        <>
          <span className="small">So key alerts reach you, with {APP_NAME} closed too.</span>
          <button type="button" className="btn primary" style={{ alignSelf: 'flex-start' }} disabled={push.busy} onClick={() => void push.enable()}>
            {push.busy ? <span className="spinner" /> : null}Turn on alerts
          </button>
        </>
      )}
      {push.message && <span className="small" role="status">{push.message}</span>}
    </div>
  )
}

/**
 * How to get alerts on a phone, where a browser tab can't have them all the time: installing from Chrome on
 * Android (its own offer when it makes one), and Add to Home Screen on an iPhone, where Web Push reaches only
 * an app on the Home Screen. This phone's way first. Nothing once opened as the installed app.
 */
function HowToGetAlerts() {
  const { canInstall } = usePwa()
  const standalone = typeof window !== 'undefined' && window.matchMedia('(display-mode: standalone)').matches
  if (standalone) return null
  const android = (
    <li key="android">
      <span className="title">Android</span>
      <span className="small">In Chrome, tap <b>Install app</b> (or the <b>⋮</b> menu, then <b>Install app</b>). Open {APP_NAME} from your home screen and turn alerts on.</span>
      <span className="small muted">Opened from WhatsApp? Tap <b>⋮</b>, then <b>Open in Chrome</b> first.</span>
      {canInstall && <InstallButton compact />}
    </li>
  )
  const iphone = (
    <li key="iphone">
      <span className="title">iPhone</span>
      <span className="small">In Safari, tap <b>Share</b>, then <b>Add to Home Screen</b>. Open {APP_NAME} from your Home Screen and turn alerts on: an iPhone sends alerts only to apps on its Home Screen.</span>
      <span className="small muted">Opened from WhatsApp? Tap the <b>Safari</b> button (or <b>Open in Safari</b>) first.</span>
    </li>
  )
  return (
    <section className="section" aria-label="How to get alerts">
      <h2 className="dsp section-title">Get the alerts</h2>
      <ol className="card list campaign-how">{IPHONE.test(navigator.userAgent) ? [iphone, android] : [android, iphone]}</ol>
    </section>
  )
}

// Every call the API answers, by name: shared by the web app (api.ts, which asks the page's own origin)
// and the Android app (mobile/src/api.ts, which asks stann.in over the network), each with its own way of
// making a request. Types only and calls: nothing of a browser's.

import type { Account, ActivityItem, AlertLevel, AthletePreview, Campaign, Card, Clip, ClipsOf, Credits, Feed, FeedPage, FeedTabKey, FeedVerdict, FollowedMatch, FollowedTeam, FollowTarget, Form, FriendLeague, FriendLeagueSummary, GameDetail, League, LeagueInvite, LiveSport, Match, Me, MyPredictions, Prediction, PushSettings, Research, ResultsPage, SearchResponse, ServerConfig, StarterSport, TeamPage, WeekPoster, WireRow } from './types'

/** Asks the API for a path ("/api/…") and answers its JSON, or throws the refusal. */
export type Request = <T>(path: string, init?: RequestInit) => Promise<T>

export const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export function endpoints(request: Request) {
  return {
    following: () => request<{ players: Card[]; teams?: FollowedTeam[] }>('/api/me/following'),
    follow: (target: FollowTarget) =>
      request<{ player_id: number; card: Card }>('/api/follows', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(target),
      }),
    unfollow: (playerId: number) => request<{ ok: boolean }>(`/api/follows/${playerId}`, { method: 'DELETE' }),
    card: (playerId: number) => request<Card>(`/api/players/${playerId}/card`),
    results: (playerId: number, before?: string | null) =>
      request<ResultsPage>(`/api/players/${playerId}/results${before ? `?before=${encodeURIComponent(before)}` : ''}`),
    result: (playerId: number, eventId: number) => request<GameDetail>(`/api/players/${playerId}/results/${eventId}`),
    form: (playerId: number, last: number) => request<Form>(`/api/players/${playerId}/form?last=${last}`),
    refresh: (playerId: number) => request<{ queued: boolean; reason?: string }>(`/api/players/${playerId}/refresh`, { method: 'POST' }),
    starters: () => request<{ sports: StarterSport[] }>('/api/starters'),
    live: () => request<{ sports: LiveSport[]; refreshing: boolean }>('/api/live'),
    // A game's match page, by its address after /match/ ("<source>/<game id>": from a live block or Live now).
    match: (source: string, game: string) => request<Match>(`/api/match/${encodeURIComponent(source)}/${encodeURIComponent(game)}`),
    // Clips published from the Mac app, newest first (clips.py): a game's, a competition's, a player's, or
    // everyone's; `before` (a clip's `published`) pages back.
    clips: (of: ClipsOf = {}, before?: string | null) => {
      const q = new URLSearchParams()
      if (of.match) q.set('match', of.match)
      if (of.competition) q.set('competition', of.competition)
      if (of.player != null) q.set('player', String(of.player))
      if (before) q.set('before', before)
      const query = q.toString()  // not q.size: Safari before 17 and Hermes don't have it
      return request<{ clips: Clip[] }>(`/api/clips${query ? `?${query}` : ''}`)
    },
    clip: (id: string) => request<Clip>(`/api/clips/${encodeURIComponent(id)}`),
    // A fan saying a clip shouldn't be up: once each; it flags the clip for the admin panel.
    reportClip: (id: string, reason: string) => request<{ reported: boolean }>(`/api/clips/${encodeURIComponent(id)}/report`, json('POST', { reason })),
    // Follow a game by itself, or stop (sports_follow/matches.py): by its address after /match/, as its page has it.
    followMatch: (source: string, game: string, on: boolean) =>
      request<{ following: boolean; event_id?: number }>(`/api/match/${encodeURIComponent(source)}/${encodeURIComponent(game)}/follow`, { method: on ? 'PUT' : 'DELETE' }),
    matches: () => request<{ matches: FollowedMatch[] }>('/api/me/matches'),
    // The sports the fan said they watch, in their order (sports_follow/fansports.py): the server keeps those it offers.
    mySports: () => request<{ sports: string[] }>('/api/me/sports'),
    setMySports: (sports: string[]) => request<{ sports: string[] }>('/api/me/sports', json('PUT', { sports })),
    // A team's page, and following it (sports_follow/teams.py): each of its games' moments, its results from its side.
    team: (teamId: number) => request<TeamPage>(`/api/teams/${teamId}`),
    followTeam: (teamId: number, on: boolean) => request<{ team_id: number; following: boolean }>(`/api/teams/${teamId}/follow`, { method: on ? 'PUT' : 'DELETE' }),
    setTeamAlerts: (teamId: number, level: AlertLevel) => request<{ team_id: number; alerts: AlertLevel }>(`/api/teams/${teamId}/alerts`, json('PUT', { level })),
    unfollowMatch: (eventId: number) => request<{ event_id: number; following: false }>(`/api/me/matches/${eventId}`, { method: 'DELETE' }),
    setMatchAlerts: (eventId: number, level: AlertLevel) => request<{ event_id: number; alerts: AlertLevel }>(`/api/me/matches/${eventId}/alerts`, json('PUT', { level })),
    // The fan's UTC offset, so "since yesterday" is by their days; fresh: past any copy the browser keeps.
    league: (fresh = false) => request<League>(`/api/league?tz=${-new Date().getTimezoneOffset()}`, fresh ? { cache: 'no-store' } : undefined),
    search: (q: string, sport?: string | null, signal?: AbortSignal) =>
      request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}${sport ? `&sport=${encodeURIComponent(sport)}` : ''}`, { signal }),
    /** A search result before following: system "wikidata" with a qid and sport for registry athletes. */
    athlete: (system: string, id: string, params: Record<string, string | null | undefined>, signal?: AbortSignal) => {
      const query = new URLSearchParams(Object.entries(params).filter((e): e is [string, string] => Boolean(e[1]))).toString()
      return request<AthletePreview | { player_id: number }>(`/api/athletes/${encodeURIComponent(system)}/${encodeURIComponent(id)}${query ? `?${query}` : ''}`, { signal })
    },
    research: () => request<Research>('/api/me/research'),
    setAlerts: (playerId: number, level: AlertLevel) =>
      request<{ player_id: number; alerts: AlertLevel }>(`/api/follows/${playerId}/alerts`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ level }) }),
    // enabled: Web Push (its public key); fcm: the Android app's notifications.
    pushInfo: () => request<{ enabled: boolean; public_key: string | null; fcm: boolean }>('/api/push'),
    pushSubscribe: (subscription: PushSubscriptionJSON, quiet: { start: string | null; end: string | null }) =>
      request<PushSettings>('/api/push/subscriptions', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ subscription, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, quiet_start: quiet.start, quiet_end: quiet.end }),
      }),
    pushUnsubscribe: (endpoint: string) =>
      request<{ ok: boolean }>('/api/push/subscriptions', { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ endpoint }) }),
    pushTest: () => request<{ sent: number }>('/api/push/test', { method: 'POST' }),
    activity: (before?: number | null) => request<{ items: ActivityItem[]; next: number | null }>(`/api/me/activity${before ? `?before=${before}` : ''}`),
    week: () => request<{ posters: WeekPoster[]; wire: WireRow[] }>('/api/me/week'),
    // A player's Feed (sports_follow/feed.py), and what the fan says about an item there.
    feed: (playerId: number) => request<Feed>(`/api/players/${playerId}/feed`),
    feedFeedback: (playerId: number, itemId: string, verdict: FeedVerdict) =>
      request<{ item_id: string; hidden: boolean }>(`/api/players/${playerId}/feed/${encodeURIComponent(itemId)}/feedback`, json('POST', { verdict })),
    // A video whose player refused to play it here (feed.refusedHere): it leaves this fan's Feed, and every fan's once
    // enough networks have said so (feed.said_unplayable, feed.unplayable), counting only devices whose time zone is
    // the feed's country's (feed.counts_everywhere): abroad, a video may be refused that plays for the fans it's for.
    feedUnplayable: (playerId: number, itemId: string) =>
      request<{ item_id: string; unplayable: boolean; marked: boolean }>(`/api/players/${playerId}/feed/${encodeURIComponent(itemId)}/unplayable`,
        json('POST', { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })),
    // The fan's own Feed, a page at a time (feed.page): the next after `cursor`, one player's with `player`; days are
    // the device's. limit 0: the circles, the count and the newest only. tab: for_you mixes in players they don't follow.
    myFeedPage: ({ cursor, player, limit, tab }: { cursor?: string | null; player?: number | null; limit?: number; tab?: FeedTabKey } = {}) => {
      const q = new URLSearchParams({ tz: String(-new Date().getTimezoneOffset()) })
      if (tab) q.set('tab', tab)
      if (cursor) q.set('cursor', cursor)
      if (player != null) q.set('player', String(player))
      if (limit != null) q.set('limit', String(limit))
      return request<FeedPage>(`/api/me/feed/items?${q}`, { cache: 'no-store' })
    },
    mutes: () => request<{ event_ids: number[] }>('/api/me/mutes'),
    mute: (eventId: number, muted: boolean) => request<{ event_id: number; muted: boolean }>(`/api/me/mutes/${eventId}`, { method: muted ? 'PUT' : 'DELETE' }),
    config: () => request<ServerConfig>('/api/config'),
    account: () => request<Me>('/api/me/account'),
    /** The Android app's first request: who this device is, as `account`, and its anonymous fan made if it has none. */
    start: () => request<Me>('/api/me/start', { method: 'POST' }),
    emailStart: (email: string) => request<{ ok: boolean; resend_in: number }>('/api/auth/email/start', json('POST', { email })),
    emailVerify: (email: string, code: string) => request<Account>('/api/auth/email/verify', json('POST', { email, code })),
    signOut: (everywhere = false) => request<{ signed_in: false }>('/api/auth/signout', json('POST', { everywhere })),
    deleteAccount: () => request<{ deleted: boolean }>('/api/me/account', json('DELETE', { confirm: true })),
    credits: () => request<Credits>('/api/credits'),
    campaign: (slug: string) => request<Campaign>(`/api/campaigns/${encodeURIComponent(slug)}`),
    /** Follow a whole team on a campaign page (its roster's players), or all of its own players, in one call. */
    campaignFollow: (slug: string, which: { team: string } | { players: true }) =>
      request<{ player_ids: number[]; failed: number }>(`/api/campaigns/${encodeURIComponent(slug)}/follow`, json('POST', which)),
    // Calling games (sports_follow/predictions.py): a pick on a question, or taking it back, while it's open; today's calls.
    pick: (questionId: number, option: string) => request<{ question: Prediction }>(`/api/predictions/${questionId}/pick`, json('PUT', { option })),
    unpick: (questionId: number) => request<{ question: Prediction }>(`/api/predictions/${questionId}/pick`, { method: 'DELETE' }),
    myPredictions: () => request<MyPredictions>('/api/me/predictions'),
    // Leagues of friends: making or joining one needs an account (a 401 says so).
    myLeagues: () => request<{ leagues: FriendLeagueSummary[] }>('/api/me/leagues'),
    makeLeague: (name: string, member: string) => request<{ league: FriendLeague }>('/api/leagues', json('POST', { name, member })),
    friendLeague: (code: string) => request<{ league: FriendLeague }>(`/api/leagues/${encodeURIComponent(code)}`),
    leagueInvite: (code: string) => request<{ invite: LeagueInvite }>(`/api/leagues/${encodeURIComponent(code)}/invite`),
    joinLeague: (code: string, member: string) => request<{ league: FriendLeague }>(`/api/leagues/${encodeURIComponent(code)}/join`, json('POST', { member })),
    leaveLeague: (code: string) => request<{ left: boolean }>(`/api/leagues/${encodeURIComponent(code)}/me`, { method: 'DELETE' }),
    leagueShared: (code: string) => request<{ ok: boolean }>(`/api/leagues/${encodeURIComponent(code)}/shared`, { method: 'POST' }),
    /** Where this fan came from: the first link's tags (s=, utm_…) and its page; the server keeps the first. */
    visitSource: (tags: Record<string, string>, path: string) => request<{ ok: boolean }>('/api/me/source', json('POST', { ...tags, path })),
  }
}

/** The site's page of a stream's player alone (sports_follow/watch.py's page), for a stream's `embed`: the Android
 * app plays it in its match page, as Twitch's player must sit in a page on the site it's told. */
export const watchPage = (embed: { player: string; id: string }) => `/watch/${encodeURIComponent(embed.player)}/${encodeURIComponent(embed.id)}`

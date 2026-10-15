// The API client. It asks the same API as the web app (web/src/api.ts), over the network to stann.in rather
// than a page's own origin, and with the web app's types (web/src/types.ts). EXPO_PUBLIC_API_URL, when Metro
// bundles the app, points it elsewhere: `EXPO_PUBLIC_API_URL=http://10.0.2.2:8421 npm start` for the local
// stack from the emulator.
// The fan is the same cookie as on the web: React Native keeps cookies, so the app is one fan from its first
// request on.

import Constants from 'expo-constants'
import type { Card, FollowTarget, LiveSport, SearchResponse, SportCatalog } from '../../web/src/types'

export type * from '../../web/src/types'

export const API_URL = (process.env.EXPO_PUBLIC_API_URL || 'https://stann.in').replace(/\/$/, '')

/** A refusal from the API: its message for the fan, and the HTTP status. */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_URL + path, {
      ...init,
      credentials: 'include', // the fan's cookie, as on the web (React Native sends it by default too)
      headers: { accept: 'application/json', ...(init?.body ? { 'content-type': 'application/json' } : {}) },
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(`Can't reach ${Constants.expoConfig?.name ?? 'the server'}. Check your connection.`, 0)
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch { /* not JSON */ }
    throw new ApiError(message, res.status)
  }
  return res.json() as Promise<T>
}

export const api = {
  sports: () => request<SportCatalog>('/api/sports'),
  following: () => request<{ players: Card[] }>('/api/me/following'),
  follow: (target: FollowTarget) => request<{ player_id: number; card: Card }>('/api/follows', { method: 'POST', body: JSON.stringify(target) }),
  unfollow: (playerId: number) => request<{ ok: boolean }>(`/api/follows/${playerId}`, { method: 'DELETE' }),
  card: (playerId: number) => request<Card>(`/api/players/${playerId}/card`),
  live: () => request<{ sports: LiveSport[]; refreshing: boolean }>('/api/live'),
  search: (q: string, signal?: AbortSignal) => request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`, { signal }),
}

/** A URL the API gives relative to itself ("/api/photos/…"), made whole. */
export function absolute(url?: string | null): string | undefined {
  if (!url) return undefined
  return url.startsWith('/') ? API_URL + url : url
}

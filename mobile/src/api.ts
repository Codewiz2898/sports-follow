// The API client: every call the web app makes (web/src/endpoints.ts), asked of stann.in over the network
// rather than a page's own origin, with the web app's types (web/src/types.ts). EXPO_PUBLIC_API_URL, when
// Metro bundles the app, points it elsewhere: `EXPO_PUBLIC_API_URL=http://10.0.2.2:8421 npm start` for the
// local stack from the emulator. The fan is the same cookie as on the web: React Native keeps cookies, so
// the app is one fan from its first request on.

import Constants from 'expo-constants'
import { endpoints, json } from '../../web/src/endpoints'
import type { PushSettings, SportCatalog } from '../../web/src/types'

export type * from '../../web/src/types'

export const API_URL = (process.env.EXPO_PUBLIC_API_URL || 'https://stann.in').replace(/\/$/, '')

/** A refusal from the API: its message for the fan, and the HTTP status (0: the server couldn't be reached). */
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
      headers: { accept: 'application/json', ...(init?.headers as Record<string, string> | undefined) },
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
  ...endpoints(request),
  sports: () => request<SportCatalog>('/api/sports'),
  // Notifications on this phone (push.ts): its FCM token, as the web's pushSubscribe sends a browser's
  // subscription, and a tapped notification (the web's service worker reports its own).
  registerPush: (device: { token: string; timezone: string; quiet_start?: string | null; quiet_end?: string | null; live_cards: boolean; previous_token: string | null }) =>
    request<PushSettings>('/api/push/fcm', json('POST', device)),
  unregisterPush: (token: string) => request<{ ok: boolean }>('/api/push/fcm', json('DELETE', { token })),
  opened: (notificationId: number) => request<{ ok: boolean }>(`/api/notifications/${notificationId}/opened`, { method: 'POST' }),
}

/** A URL the API gives relative to itself ("/api/photos/…", "/player/7"), made whole. */
export function absolute(url?: string | null): string | undefined {
  if (!url) return undefined
  return url.startsWith('/') ? API_URL + url : url
}

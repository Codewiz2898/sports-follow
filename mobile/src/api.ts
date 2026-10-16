// The API client: every call the web app makes (web/src/endpoints.ts), asked of stann.in over the network
// rather than a page's own origin, with the web app's types (web/src/types.ts). EXPO_PUBLIC_API_URL, when
// Metro bundles the app, points it elsewhere: `EXPO_PUBLIC_API_URL=http://10.0.2.2:8421 npm start` for the
// local stack from the emulator. The fan is the same cookie as on the web: React Native keeps cookies, so
// the app is one fan from its first request on.

import Constants from 'expo-constants'
import { endpoints } from '../../web/src/endpoints'
import type { SportCatalog } from '../../web/src/types'

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
}

/** A URL the API gives relative to itself ("/api/photos/…", "/player/7"), made whole. */
export function absolute(url?: string | null): string | undefined {
  if (!url) return undefined
  return url.startsWith('/') ? API_URL + url : url
}

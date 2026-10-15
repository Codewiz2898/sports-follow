// The sports, from the server (GET /api/sports): what each is called, which free-text names are it (matched by
// the web app's own rules, web/src/sports/naming.ts), and the colours it wears. The app keeps no list of its
// own (CLAUDE.md). The last copy is kept on the phone, so the app opens in colour before the server answers.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { createContext, use, useEffect, useState, type ReactNode } from 'react'
import { NO_SPORTS, sportsOf, type Sports } from '../../web/src/sports/naming'
import { api, type SportCatalog } from './api'
import { colour } from './theme'

const KEPT = 'stann.sports'

const SportsContext = createContext<Sports>(sportsOf(NO_SPORTS))

/** Renders once the kept copy is read (a few milliseconds), then takes the server's answer when it comes. */
export function SportsProvider({ children }: { children: ReactNode }) {
  const [sports, setSports] = useState<Sports | null>(null)

  useEffect(() => {
    let fresh = false
    AsyncStorage.getItem(KEPT)
      .then((raw) => { if (raw && !fresh) setSports(sportsOf(JSON.parse(raw) as SportCatalog)) })
      .catch(() => {})
      .finally(() => setSports((s) => s ?? sportsOf(NO_SPORTS)))
    api.sports()
      .then((catalog) => {
        if (!catalog?.sports) return
        fresh = true
        setSports(sportsOf(catalog))
        AsyncStorage.setItem(KEPT, JSON.stringify(catalog)).catch(() => {})
      })
      .catch(() => {}) // offline: the kept copy, or none
  }, [])

  return sports ? <SportsContext value={sports}>{children}</SportsContext> : null
}

export function useSports(): Sports {
  return use(SportsContext)
}

export interface Tone { block: string; on: string; soft: string; ink: string }

/** The colours a sport wears: its block, text on it, softer text on it, and itself as text on ink. A sport
 * without colours (or not known yet) wears ink. */
export function useTone(sport?: string | null): Tone {
  const c = useSports().info(sport).colour
  return c ? { block: c.sport, on: c.on, soft: c.on_soft, ink: c.ink } : { block: colour.ink, on: '#FFFFFF', soft: 'rgba(255,255,255,0.8)', ink: '#FFFFFF' }
}

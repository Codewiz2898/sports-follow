// The sports, from the server (GET /api/sports): what each is called, which free-text names are it (matched by
// the web app's own rules, web/src/sports/naming.ts), and the colours it wears. The app keeps no list of its
// own (CLAUDE.md). The last copy is kept on the phone; as on the web, the first screen waits for the sports
// (the splash stays up), from that copy when there is one, so only a first start waits on the network.

import AsyncStorage from '@react-native-async-storage/async-storage'
import * as SplashScreen from 'expo-splash-screen'
import { createContext, use, useEffect, useState, type ReactNode } from 'react'
import { NO_SPORTS, sportsOf, type Sports } from '../../web/src/sports/naming'
import { api, type SportCatalog } from './api'

const KEPT = 'stann.sports'
const LONGEST_MS = 4000 // the most a first start waits for the server (offline: the app starts without sport names)

SplashScreen.preventAutoHideAsync().catch(() => {})

const SportsContext = createContext<Sports>(sportsOf(NO_SPORTS))

export function SportsProvider({ children }: { children: ReactNode }) {
  const [sports, setSports] = useState<Sports>(() => sportsOf(NO_SPORTS))
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let fresh = false
    const timer = setTimeout(() => setReady(true), LONGEST_MS)
    AsyncStorage.getItem(KEPT)
      .then((raw) => { if (raw && !fresh) { setSports(sportsOf(JSON.parse(raw) as SportCatalog)); setReady(true) } })
      .catch(() => {})
    api.sports()
      .then((catalog) => {
        if (!catalog?.sports) return
        fresh = true
        setSports(sportsOf(catalog))
        AsyncStorage.setItem(KEPT, JSON.stringify(catalog)).catch(() => {})
      })
      .catch(() => {}) // offline: the kept copy, or none
      .finally(() => setReady(true))
    return () => clearTimeout(timer)
  }, [])

  useEffect(() => { if (ready) SplashScreen.hideAsync().catch(() => {}) }, [ready])

  return <SportsContext value={sports}>{children}</SportsContext>
}

export function useSports(): Sports {
  return use(SportsContext)
}

/**
 * The colours a sport wears, as the web's [data-sport] custom properties: its block (--sport), the ink that
 * reads on it (--on-sport), that ink for secondary text (--on-sport-soft), and the sport's colour as text on
 * an ink surface (--sport-on-ink). A sport without colours (or not known yet) wears ink.
 */
export interface Tone { key: string; sport: string; on: string; onSoft: string; ink: string }

export function toneOf(sports: Sports, sport?: string | null): Tone {
  const info = sports.info(sport)
  const c = info.colour
  return c ? { key: info.key, sport: c.sport, on: c.on, onSoft: c.on_soft, ink: c.ink } : { key: info.key, sport: '#0D0D0D', on: '#FFFFFF', onSoft: 'rgba(255,255,255,0.78)', ink: '#FFFFFF' }
}

/** A sport's colours, by any name for it ("Football", "CS2", or its key). */
export function useTone(sport?: string | null): Tone {
  return toneOf(useSports(), sport)
}

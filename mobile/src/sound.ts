// Whether the Feed's videos play with their sound (BHA-381), as Instagram's and YouTube Shorts' do: off until the
// fan turns it on (the sound button beside a playing video, YouTube's own button, or a tap to play one), then on for
// every video after it until they turn it off. Kept while the app runs, and on this phone for the rest of the day, so
// a relaunch the same day keeps it and the next day starts muted again.

import AsyncStorage from '@react-native-async-storage/async-storage'
import { useEffect, useSyncExternalStore } from 'react'

const KEY = 'stann.feed-sound'

let on = false
let read = false
let chose = false // the fan chose since the app started: the phone's note is older, so it's not read over it
const listeners = new Set<() => void>()
const told = () => listeners.forEach((heard) => heard())
const today = () => new Date().toDateString() // the phone's own day

/** Turn the sound on or off, for the video playing and every one after it, and keep it for today. */
export function setSound(to: boolean) {
  chose = true
  if (on === to) return
  on = to
  told()
  AsyncStorage.setItem(KEY, JSON.stringify({ on: to, day: today() })).catch(() => {})
}

/** Whether videos play with their sound now (off until the phone's note of today's choice is read). */
export function useSound(): boolean {
  useEffect(() => {
    if (read) return
    read = true
    AsyncStorage.getItem(KEY).then((was) => {
      const kept = was ? (JSON.parse(was) as { on?: unknown; day?: unknown }) : null
      if (!chose && kept?.on === true && kept.day === today()) { on = true; told() }
    }).catch(() => {})
  }, [])
  return useSyncExternalStore((heard) => { listeners.add(heard); return () => { listeners.delete(heard) } }, () => on)
}

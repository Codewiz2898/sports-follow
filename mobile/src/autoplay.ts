// Whether the Feed's videos start by themselves as they come on screen (BHA-359): always (the default, Bharath's
// call), only on Wi-Fi, or never, the fan's choice on the Account page, kept on this phone. Muted when they do,
// one at a time (feedLayouts.tsx).

import AsyncStorage from '@react-native-async-storage/async-storage'
import { addNetworkStateListener, getNetworkStateAsync, NetworkStateType, type NetworkState } from 'expo-network'
import { useEffect, useState, useSyncExternalStore } from 'react'

export type Autoplay = 'always' | 'wifi' | 'off'
export const AUTOPLAY: [Autoplay, string][] = [['always', 'Always'], ['wifi', 'On Wi-Fi'], ['off', 'Off']]
const KEY = 'stann.autoplay'

let chosen: Autoplay = 'always'
let read = false
const listeners = new Set<() => void>()
const told = () => listeners.forEach((heard) => heard())

/** Change it, here and on every screen showing it, and keep it for the next start. */
export function setAutoplay(to: Autoplay) {
  chosen = to
  told()
  AsyncStorage.setItem(KEY, to).catch(() => {})
}

/** The fan's choice (Always until the phone's note of it is read). */
export function useAutoplaySetting(): Autoplay {
  useEffect(() => {
    if (read) return
    read = true
    AsyncStorage.getItem(KEY).then((was) => {
      if (was === 'wifi' || was === 'off') { chosen = was; told() }
    }).catch(() => {})
  }, [])
  return useSyncExternalStore((heard) => { listeners.add(heard); return () => { listeners.delete(heard) } }, () => chosen)
}

/** Whether the phone is on Wi-Fi (or a cable) now, watched only while `watch`; false till it's known. An answer that
 * comes once the screen's gone is dropped (expo-network's own hook can answer before the first screen is in). */
function useOnWifi(watch: boolean): boolean {
  const [wifi, setWifi] = useState(false)
  useEffect(() => {
    if (!watch) return
    let live = true
    const seen = (state: NetworkState) => { if (live) setWifi(state.type === NetworkStateType.WIFI || state.type === NetworkStateType.ETHERNET) }
    getNetworkStateAsync().then(seen).catch(() => {})
    const sub = addNetworkStateListener(seen)
    return () => { live = false; sub.remove() }
  }, [watch])
  return wifi
}

/** Whether a video may start by itself now: the fan's choice, and for "On Wi-Fi" the connection this moment. */
export function useAutoplay(): boolean {
  const setting = useAutoplaySetting()
  const wifi = useOnWifi(setting === 'wifi')
  return setting === 'always' || (setting === 'wifi' && wifi)
}

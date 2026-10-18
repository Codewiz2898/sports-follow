// Notifications on this phone, the web's notifications.tsx (usePush, PushResync) for the app: the server
// pushes the fan's moments here through Firebase Cloud Messaging (sports_follow/fcm.py), planned as for the
// web, one notification a game, replaced as its moments come (the server tags it with the game), loud only
// for a key moment outside the phone's quiet hours. The app draws them itself, in the sport's colour, with a
// live card for each game the fan follows while it's on (modules/stann-notifications); expo-notifications
// asks for permission and gives the token.
//
// The app makes its channels, and the first time the fan follows someone it asks to notify them (Android
// 13 and later ask the fan; earlier ones don't); after that, Alerts' Settings turns them on and off here, as
// on the web. While they're on, it registers the phone's FCM token with the API (POST /api/push/fcm) with its
// timezone, quiet hours and whether it shows live cards: at every start, after a sign-in or out (the cookie is
// another fan then), whenever FCM replaces the token, and when the fan changes either setting, so the phone
// belongs to whoever the app is now, one the server dropped comes back, and the server sends no live card the
// phone would throw away. Turned off here or in Android's settings, the phone comes off the server (FCM would go on
// taking its pushes). A tap opens the player's page, or a game's own moment its match page (the
// notification opens the app's own link to it), and tells the server it was opened.
//
// FCM needs the app's Firebase config (google-services.json, app.config.ts). A build without it, or a server
// without its key, has no notifications, and Alerts says why.

import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'
import * as Notifications from 'expo-notifications'
import { router } from 'expo-router'
import { useCallback, useEffect, useState } from 'react'
import { Linking, Platform } from 'react-native'
import { DEVICE, quietSaid, testSaid } from '../../web/src/alerts'
import StannNotifications from '../modules/stann-notifications'
import { useAccount } from './account'
import { API_URL, api } from './api'


const TOKEN = 'stann.push-token' // the token last registered, so a new one can replace it on the server
const ASKED = 'stann.push-asked' // the app asked Android's prompt on a follow: not again unless Alerts asks
const OFF = 'stann.push-off' // turned off on Alerts' Settings: not registered again until turned on there
const QUIET = 'stann.quiet-hours' // { start: "22:00", end: "07:00" } in the phone's time, or none

export type Quiet = { start: string | null; end: string | null }

// Told whenever this phone comes on or off the server, so what shows it (usePush) follows a registration the
// app made by itself: the first follow's, which a campaign page's alerts prompt is waiting on.
const changes = new Set<() => void>()
const changed = () => { for (const heard of changes) heard() }

// A button on a notification (Mute this game) calls the API while the app may be closed: it needs to know where.
StannNotifications?.configure(API_URL)

async function stored<T>(key: string): Promise<T | null> {
  try {
    const value = await AsyncStorage.getItem(key)
    return value ? (JSON.parse(value) as T) : null
  } catch {
    return null
  }
}

/** Why this phone can't have notifications, or null when it can. */
async function unavailable(): Promise<string | null> {
  if (Platform.OS !== 'android') return 'Notifications come to the Android app first.'
  if (!StannNotifications) return 'This build of the app was made without its notifications module (modules/stann-notifications).'
  if (!Constants.expoConfig?.extra?.firebase) return 'This build of the app was made without notifications (no google-services.json; README.md, "Android app").'
  const { fcm } = await api.pushInfo()
  return fcm ? null : DEVICE.noServer
}

/** Registers this phone's token with the API, replacing `previous` if FCM gave it a new one. Quiet hours go
 * when the app has them; without, a replaced token's carry over on the server. Live cards go as the phone has
 * them (a card its "Stop live card" stopped the module tells the server itself: ActionReceiver.kt). */
async function register(token: string, previous: string | null): Promise<void> {
  const quiet = await stored<Quiet>(QUIET)
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  await api.registerPush({
    token,
    timezone,
    ...(quiet ? { quiet_start: quiet.start, quiet_end: quiet.end } : {}),
    live_cards: StannNotifications?.liveCards() ?? false,
    previous_token: previous && previous !== token ? previous : null,
  })
  await AsyncStorage.setItem(TOKEN, JSON.stringify(token))
  StannNotifications?.registered(token)
  changed()
}

/** Takes this phone off the server, if it's on. */
async function unregister(): Promise<void> {
  const token = await stored<string>(TOKEN)
  if (!token) return
  await api.unregisterPush(token)
  await AsyncStorage.removeItem(TOKEN)
  StannNotifications?.registered(null)
  changed()
}

/** Registers this phone if Android lets the app notify, asking first when `ask` and it may still ask.
 * False when it may not: the phone is then taken off the server. */
async function turnOn(ask: boolean): Promise<boolean> {
  StannNotifications?.makeChannels() // before asking: Android 13 shows no prompt for an app without a channel
  let { granted, canAskAgain } = await Notifications.getPermissionsAsync()
  if (!granted && ask && canAskAgain) {
    await AsyncStorage.setItem(ASKED, 'true')
    ;({ granted } = await Notifications.requestPermissionsAsync())
  }
  if (!granted) {
    await unregister()
    return false
  }
  const { data } = await Notifications.getDevicePushTokenAsync()
  await register(String(data), await stored<string>(TOKEN))
  return true
}

/**
 * Keeps this phone registered while the app runs, as whoever the app is now: once it's known whether the fan
 * follows anyone or any game (`following`, null until then; the first time they do, it asks), again after a
 * sign-in or out (account.tsx's epoch), and whenever FCM replaces the token. Not while it's turned off on Alerts.
 */
export function usePushRegistration(following: boolean | null): void {
  const { epoch } = useAccount()
  useEffect(() => {
    if (following === null) return
    ;(async () => {
      if (await stored<boolean>(OFF)) return
      const why = await unavailable()
      if (why) return console.info(`push: ${why}`)
      await turnOn(following && !(await stored<boolean>(ASKED)))
    })().catch((e: Error) => console.warn(`push: not registered: ${e.message}`))
  }, [following, epoch])

  useEffect(() => {
    const sub = Notifications.addPushTokenListener(({ data }) => {
      ;(async () => {
        if ((await stored<boolean>(OFF)) || !(await Notifications.getPermissionsAsync()).granted) return
        await register(String(data), await stored<string>(TOKEN))
      })().catch((e: Error) => console.warn(`push: new token not registered: ${e.message}`))
    })
    return () => sub.remove()
  }, [])
}

/** Calls `arrived` whenever a moment comes to this phone while the app is running (the fan's activity list
 * has something new). */
export function useNotificationsArriving(arrived: () => void): void {
  useEffect(() => {
    const sub = StannNotifications?.addListener('onMoment', () => arrived())
    return () => sub?.remove()
  }, [arrived])
}

// The notifications counted as opened since the app started: the link that started it is read again on a remount.
const counted = new Set<number>()

/** Counts a tapped notification as opened. The app draws them itself, and a tap opens the app's own link to the
 * page (stann://player/7?n=31), which Expo Router follows; n is the notification. */
export function useNotificationTaps(): void {
  useEffect(() => {
    const heard = (url: string | null) => {
      const id = Number(/[?&]n=(\d+)/.exec(url ?? '')?.[1])
      if (!(id > 0) || counted.has(id)) return
      counted.add(id)
      api.opened(id).catch(() => { /* only the open rate misses it */ })
    }
    Linking.getInitialURL().then(heard).catch(() => {})
    const sub = Linking.addEventListener('url', ({ url }) => heard(url))
    return () => sub.remove()
  }, [])
  useExpoTaps()
}

/** Opens the page a notification expo-notifications showed is about (one from before the app drew its own, still
 * in the tray): the player's, or for a game followed by itself, its match page. */
function useExpoTaps(): void {
  useEffect(() => {
    const open = (response: Notifications.NotificationResponse) => {
      Notifications.clearLastNotificationResponse() // a remount mustn't open it again
      const data = response.notification.request.content.data ?? {}
      const id = Number(data.id)
      if (id > 0) api.opened(id).catch(() => { /* only the open rate misses it */ })
      const url = typeof data.url === 'string' ? data.url : ''
      const player = /^\/player\/(\d+)$/.exec(url)
      const game = /^\/match\/([^/?#]+)\/([^/?#]+)$/.exec(url)
      if (player) router.push({ pathname: '/player/[id]', params: { id: player[1] } })
      else if (game) router.push({ pathname: '/match/[source]/[game]', params: { source: game[1], game: game[2] } })
    }
    const last = Notifications.getLastNotificationResponse()
    if (last) open(last)
    const sub = Notifications.addNotificationResponseReceivedListener(open)
    return () => sub.remove()
  }, [])
}

/** Notifications on this phone for Alerts' Settings and a campaign page, as the web's usePush: on or off,
 * quiet hours, a test; and the app's own, live cards on or off (null where the app can't draw them). */
export function usePush() {
  const { epoch } = useAccount()
  const [why, setWhy] = useState<string | null>(null)
  const [on, setOn] = useState(false)
  const [quiet, setQuiet] = useState<Quiet>({ start: null, end: null })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [live, setLive] = useState(() => StannNotifications?.liveCards() ?? false)

  const load = useCallback(async () => {
    const [reason, permission, token, off, saved] = await Promise.all([
      unavailable().catch((e: Error) => e.message),
      Notifications.getPermissionsAsync(),
      stored<string>(TOKEN),
      stored<boolean>(OFF),
      stored<Quiet>(QUIET),
    ])
    setWhy(reason)
    setOn(!reason && permission.granted && Boolean(token) && !off)
    setQuiet(saved ?? { start: null, end: null })
  }, [])
  useEffect(() => { load() }, [load, epoch])
  useEffect(() => {
    const heard = () => { load().catch(() => {}) }
    changes.add(heard)
    return () => { changes.delete(heard) }
  }, [load])

  const run = (work: () => Promise<void>) => async () => {
    setBusy(true)
    setMessage(null)
    try {
      await work()
    } catch (e) {
      setMessage((e as Error).message || 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  const enable = run(async () => {
    await AsyncStorage.removeItem(OFF)
    // Android stops showing its prompt once the fan has said no twice: then only its settings can allow it.
    const { canAskAgain } = await Notifications.getPermissionsAsync()
    if (!(await turnOn(true))) {
      setMessage(`Notifications are off for ${Constants.expoConfig?.name ?? 'the app'} in Android's settings. Allow them there, then try again.`)
      if (!canAskAgain) Linking.openSettings().catch(() => {})
    }
    await load()
  })

  const disable = run(async () => {
    await unregister()
    await AsyncStorage.setItem(OFF, 'true')
    setOn(false)
  })

  const saveQuiet = (next: Quiet) => run(async () => {
    setQuiet(next)
    await AsyncStorage.setItem(QUIET, JSON.stringify(next))
    const token = await stored<string>(TOKEN)
    if (on && token) await register(token, null)
    setMessage(quietSaid(next.start, next.end))
  })()

  const test = run(async () => {
    const { sent } = await api.pushTest()
    setMessage(testSaid(sent))
  })

  // A live card for each game the fan follows while it's on, this phone's alone: kept by the app's notifications,
  // which take them down at once when it's off, and told to the server, which then sends this phone none.
  const saveLive = (next: boolean) => run(async () => {
    StannNotifications?.setLiveCards(next)
    setLive(next)
    const token = await stored<string>(TOKEN)
    if (on && token) await register(token, null)
  })()

  return { unavailable: why, on, quiet, busy, message, enable, disable, saveQuiet, test, live: StannNotifications ? live : null, saveLive }
}

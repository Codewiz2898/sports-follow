// Notifications on this phone, the web's notifications.tsx (usePush, PushResync) for the app: the server
// pushes the fan's moments here through Firebase Cloud Messaging (sports_follow/fcm.py), planned as for the
// web, one notification a game, replaced as its moments come (the server tags it with the game), loud only
// for a key moment outside the phone's quiet hours.
//
// The app makes its two channels, and the first time the fan follows someone it asks to notify them (Android
// 13 and later ask the fan; earlier ones don't); after that, Alerts' Settings turns them on and off here, as
// on the web. While they're on, it registers the phone's FCM token with the API (POST /api/push/fcm) with its
// timezone and quiet hours: at every start, after a sign-in or out (the cookie is another fan then), and
// whenever FCM replaces the token, so the phone belongs to whoever the app is now and one the server dropped
// comes back. Turned off here or in Android's settings, the phone comes off the server (FCM would go on
// taking its pushes). A tap opens the player's page, or a game's own moment its match page, and tells the
// server it was opened.
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
import { useAccount } from './account'
import { api } from './api'

// The channels the server sends on (fcm.LOUD_CHANNEL, fcm.QUIET_CHANNEL; tests/test_fcm.py holds them equal).
// Once made, only Android's settings change a channel's sound or importance: a new one needs a new id.
export const LOUD_CHANNEL = 'key'
export const QUIET_CHANNEL = 'other'
// The quiet channel's id before it moved up to default importance: a low one is "silent", and most phones
// leave silent notifications off the lock screen. Deleted, so Android's settings don't list it twice.
const OLD_QUIET_CHANNEL = 'quiet'

const TOKEN = 'stann.push-token' // the token last registered, so a new one can replace it on the server
const ASKED = 'stann.push-asked' // the app asked Android's prompt on a follow: not again unless Alerts asks
const OFF = 'stann.push-off' // turned off on Alerts' Settings: not registered again until turned on there
const QUIET = 'stann.quiet-hours' // { start: "22:00", end: "07:00" } in the phone's time, or none

export type Quiet = { start: string | null; end: string | null }

// Told whenever this phone comes on or off the server, so what shows it (usePush) follows a registration the
// app made by itself: the first follow's, which a campaign page's alerts prompt is waiting on.
const changes = new Set<() => void>()
const changed = () => { for (const heard of changes) heard() }

// With the app in front, a moment shows as it would behind it: in the tray, and as a banner with sound only
// when it's loud (on the loud channel).
Notifications.setNotificationHandler({
  handleNotification: async (notification) => {
    const trigger = notification.request.trigger
    const loud = trigger != null && 'remoteMessage' in trigger && trigger.remoteMessage?.notification?.channelId === LOUD_CHANNEL
    return { shouldShowBanner: loud, shouldShowList: true, shouldPlaySound: loud, shouldSetBadge: false }
  },
})

// Both say public for the lock screen, though what counts is each push's own visibility, which the server sets
// public (fcm.message): a score is no secret, and the lock screen is where a fan looks first. A moment that
// arrives with the app open is shown by expo-notifications, which sets none, so it stays private until the
// game's next push replaces it.
async function makeChannels(): Promise<void> {
  await Notifications.setNotificationChannelAsync(LOUD_CHANNEL, {
    name: 'Key moments',
    description: 'Your players’ key moments and results, with sound',
    importance: Notifications.AndroidImportance.HIGH, // and no `sound`: the phone's own (a name would be a sound file)
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
  })
  await Notifications.setNotificationChannelAsync(QUIET_CHANNEL, {
    name: 'Other moments',
    description: 'Everything else, and anything in your quiet hours, without sound but on the lock screen',
    importance: Notifications.AndroidImportance.DEFAULT, // not LOW: Android hides "silent" ones from most lock screens
    sound: null,
    enableVibrate: false,
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
  })
  await Notifications.deleteNotificationChannelAsync(OLD_QUIET_CHANNEL)
}

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
  if (!Constants.expoConfig?.extra?.firebase) return 'This build of the app was made without notifications (no google-services.json; README.md, "Android app").'
  const { fcm } = await api.pushInfo()
  return fcm ? null : DEVICE.noServer
}

/** Registers this phone's token with the API, replacing `previous` if FCM gave it a new one. Quiet hours go
 * when the app has them; without, a replaced token's carry over on the server. */
async function register(token: string, previous: string | null): Promise<void> {
  const quiet = await stored<Quiet>(QUIET)
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  await api.registerPush({
    token,
    timezone,
    ...(quiet ? { quiet_start: quiet.start, quiet_end: quiet.end } : {}),
    previous_token: previous && previous !== token ? previous : null,
  })
  await AsyncStorage.setItem(TOKEN, JSON.stringify(token))
  changed()
}

/** Takes this phone off the server, if it's on. */
async function unregister(): Promise<void> {
  const token = await stored<string>(TOKEN)
  if (!token) return
  await api.unregisterPush(token)
  await AsyncStorage.removeItem(TOKEN)
  changed()
}

/** Registers this phone if Android lets the app notify, asking first when `ask` and it may still ask.
 * False when it may not: the phone is then taken off the server. */
async function turnOn(ask: boolean): Promise<boolean> {
  await makeChannels() // before asking: Android 13 shows no prompt for an app without a channel
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

/** Calls `arrived` whenever a notification comes to this phone while the app is running (one of the fan's
 * moments: their activity list has something new). */
export function useNotificationsArriving(arrived: () => void): void {
  useEffect(() => {
    const sub = Notifications.addNotificationReceivedListener(() => arrived())
    return () => sub.remove()
  }, [arrived])
}

/** Opens the page a tapped notification is about (one that started the app, or any while it runs): the player's,
 * or for a game followed by itself, its match page. */
export function useNotificationTaps(): void {
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
 * quiet hours, a test. */
export function usePush() {
  const { epoch } = useAccount()
  const [why, setWhy] = useState<string | null>(null)
  const [on, setOn] = useState(false)
  const [quiet, setQuiet] = useState<Quiet>({ start: null, end: null })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

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

  return { unavailable: why, on, quiet, busy, message, enable, disable, saveQuiet, test }
}

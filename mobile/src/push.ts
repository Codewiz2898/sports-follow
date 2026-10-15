// Notifications: the server pushes the fan's moments to this device through Firebase Cloud Messaging
// (sports_follow/fcm.py), planned as for the web app: one notification a game, replaced as its moments come
// (the server tags it with the game), loud only for a key moment outside the device's quiet hours.
//
// On start the app makes its two channels, and once the fan follows someone it asks to notify them (Android
// 13 and later ask the fan; earlier ones don't). Allowed, it registers the device's FCM token with the API
// (POST /api/push/fcm) with its timezone and quiet hours, at every start and whenever FCM replaces the
// token, so the device belongs to whoever the app is now and one the server dropped comes back; turned off
// in Android's settings, the next start takes it off the server. A tap opens the player's page and tells the
// server it was opened.
//
// FCM needs the app's Firebase config (google-services.json, app.config.ts). A build without it, or a server
// without its key, has no notifications and says why in the log.

import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'
import * as Notifications from 'expo-notifications'
import { useRouter } from 'expo-router'
import { useEffect } from 'react'
import { Platform } from 'react-native'
import { api } from './api'

// The channels the server sends on (fcm.LOUD_CHANNEL, fcm.QUIET_CHANNEL; tests/test_fcm.py holds them equal).
// Once made, only Android's settings change a channel's sound: a new sound needs a new id.
export const LOUD_CHANNEL = 'key'
export const QUIET_CHANNEL = 'quiet'

const TOKEN = 'stann-push-token' // the token last registered, so a new one can replace it on the server
const ASKED = 'stann-push-asked' // asked once on following someone; later, from the app's settings (BHA-231)
const QUIET = 'stann-quiet-hours' // { start: "22:00", end: "07:00" } in the device's time, or none

// With the app in front, a moment shows as it would behind it: in the tray, and as a banner with sound only
// when it's loud (on the loud channel).
Notifications.setNotificationHandler({
  handleNotification: async (notification) => {
    const trigger = notification.request.trigger
    const loud = trigger != null && 'remoteMessage' in trigger && trigger.remoteMessage?.notification?.channelId === LOUD_CHANNEL
    return { shouldShowBanner: loud, shouldShowList: true, shouldPlaySound: loud, shouldSetBadge: false }
  },
})

async function makeChannels(): Promise<void> {
  await Notifications.setNotificationChannelAsync(LOUD_CHANNEL, {
    name: 'Key moments',
    description: 'Your players’ key moments and results, with sound',
    importance: Notifications.AndroidImportance.HIGH, // and no `sound`: the phone's own (a name would be a sound file)
  })
  await Notifications.setNotificationChannelAsync(QUIET_CHANNEL, {
    name: 'Other moments',
    description: 'Everything else, and anything in your quiet hours, without sound',
    importance: Notifications.AndroidImportance.LOW,
    sound: null,
    enableVibrate: false,
  })
}

async function stored<T>(key: string): Promise<T | null> {
  try {
    const value = await AsyncStorage.getItem(key)
    return value ? (JSON.parse(value) as T) : null
  } catch {
    return null
  }
}

/** Registers this device's token with the API, replacing `previous` if FCM gave it a new one. Quiet hours
 * go when the app has them; without, a replaced token's carry over on the server. */
async function register(token: string, previous: string | null): Promise<void> {
  const quiet = await stored<{ start: string | null; end: string | null }>(QUIET)
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  await api.registerPush({
    token,
    timezone,
    ...(quiet ? { quiet_start: quiet.start, quiet_end: quiet.end } : {}),
    previous_token: previous && previous !== token ? previous : null,
  })
  await AsyncStorage.setItem(TOKEN, JSON.stringify(token))
}

/** Notifications were turned off in Android's settings: the server stops sending to this device (FCM would
 * still take the pushes, so it would never find out). */
async function unregister(): Promise<void> {
  const token = await stored<string>(TOKEN)
  if (!token) return
  await api.unregisterPush(token)
  await AsyncStorage.removeItem(TOKEN)
}

/** Turns notifications on if they're allowed, asking first when `ask` and the app hasn't asked before. */
async function enable(ask: boolean): Promise<void> {
  await makeChannels() // before asking: Android 13 shows no prompt for an app without a channel
  let { granted, canAskAgain } = await Notifications.getPermissionsAsync()
  if (!granted && ask && canAskAgain && !(await stored<boolean>(ASKED))) {
    await AsyncStorage.setItem(ASKED, 'true')
    ;({ granted } = await Notifications.requestPermissionsAsync())
  }
  if (!granted) return unregister()
  if (!Constants.expoConfig?.extra?.firebase) {
    console.info('push: this build has no google-services.json, so notifications are off (README.md, "Android app")')
    return
  }
  const { fcm } = await api.pushInfo()
  if (!fcm) {
    console.info('push: the server has no FCM key, so notifications are off')
    return
  }
  const { data } = await Notifications.getDevicePushTokenAsync()
  await register(String(data), await stored<string>(TOKEN))
}

/**
 * Keeps this device registered for notifications while the app runs: once it's known whether the fan
 * follows anyone (`following`, null until then) it registers, asking first if they do; and again whenever
 * FCM replaces the token.
 */
export function usePushRegistration(following: boolean | null): void {
  useEffect(() => {
    if (Platform.OS !== 'android' || following === null) return
    enable(following).catch((e: Error) => console.warn(`push: not registered: ${e.message}`))
  }, [following])

  useEffect(() => {
    if (Platform.OS !== 'android') return
    const sub = Notifications.addPushTokenListener(({ data }) => {
      Notifications.getPermissionsAsync()
        .then(async ({ granted }) => granted && register(String(data), await stored<string>(TOKEN)))
        .catch((e: Error) => console.warn(`push: new token not registered: ${e.message}`))
    })
    return () => sub.remove()
  }, [])
}

/** Opens the player's page for a tapped notification (one that started the app, or any while it runs). */
export function useNotificationTaps(): void {
  const router = useRouter()
  useEffect(() => {
    const open = (response: Notifications.NotificationResponse) => {
      Notifications.clearLastNotificationResponse() // a remount mustn't open it again
      const data = response.notification.request.content.data ?? {}
      const id = Number(data.id)
      if (id > 0) api.opened(id).catch(() => { /* only the open rate misses it */ })
      const player = typeof data.url === 'string' ? /^\/player\/(\d+)$/.exec(data.url) : null
      if (player) router.push({ pathname: '/player/[id]', params: { id: player[1] } })
    }
    const last = Notifications.getLastNotificationResponse()
    if (last) open(last)
    const sub = Notifications.addNotificationResponseReceivedListener(open)
    return () => sub.remove()
  }, [router])
}

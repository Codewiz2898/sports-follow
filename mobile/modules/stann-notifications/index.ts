// The app's own notifications (android/src/main/java/io/stann/notifications): drawn by the app from what the
// server sends, in the designs' scorecard and live card. What the JavaScript asks of them is here; null in a
// build without the module (it's Android's only), where the app has no notifications.

import { type NativeModule, requireOptionalNativeModule } from 'expo'

type Events = {
  /** A moment arrived while the app runs (its notification id and kind). */
  onMoment(event: { id?: string; kind?: string }): void
}

declare class StannNotifications extends NativeModule<Events> {
  /** The channels the notifications go on (key, other, live), made before the app asks to notify. */
  makeChannels(): void
  /** Where the API is, for a button that calls it while the app is closed (Mute this game). */
  configure(api: string): void
  /** The FCM token the app registered with the API (null once it's off), for a button that names this phone. */
  registered(token: string | null): void
  /** Whether live cards show, on this phone. */
  liveCards(): boolean
  setLiveCards(on: boolean): void
}

export default requireOptionalNativeModule<StannNotifications>('StannNotifications')

package io.stann.notifications

import android.content.Context
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.lang.ref.WeakReference

/** What the app's JavaScript asks of its notifications (src/push.ts): make the channels, say where the API is
 *  and the token the app registered with it (a button calls it while the app's closed), sport colours or one,
 *  turn live cards on or off, take a game's or every live card down; and it hears each moment that
 *  arrives while it runs ("onMoment"), as it heard expo-notifications' before the app drew them itself. */
class StannNotificationsModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("StannNotifications")
    Events("onMoment")

    OnCreate { running = WeakReference(this@StannNotificationsModule) }
    OnDestroy { running = null }

    Function("makeChannels") { Channels.make(context) }
    Function("configure") { api: String -> Settings.setApi(context, api) }
    Function("registered") { token: String? -> Settings.setToken(context, token) }
    Function("sportColours") { Settings.sportColours(context) }
    Function("setSportColours") { on: Boolean -> Settings.setSportColours(context, on) }
    Function("liveCards") { Settings.liveCards(context) }
    Function("setLiveCards") { on: Boolean ->
      Settings.setLiveCards(context, on)
      if (!on) Cards.takeDownLive(context)
    }
    Function("takeDownLive") { event: String? -> Cards.takeDownLive(context, event) }
  }

  private val context: Context
    get() = requireNotNull(appContext.reactContext) { "the app's context is gone" }

  companion object {
    @Volatile private var running: WeakReference<StannNotificationsModule>? = null

    internal fun arrived(data: Map<String, String>) {
      running?.get()?.sendEvent("onMoment", mapOf("id" to data["id"], "kind" to data["kind"]))
    }
  }
}

package io.stann.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import android.webkit.CookieManager
import androidx.core.app.NotificationManagerCompat
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/** A notification's buttons that work without opening the app. "Mute this game" tells the API, as the web's
 *  does (PUT /api/me/mutes/<event>), as this fan: their cookie is the app's, which React Native keeps in
 *  Android's cookie store; the server then sends the game nothing more, its live card included (a mute that
 *  didn't reach it, offline, lets them come again). "Stop live card" is this phone's alone, and remembered.
 *  Either takes the game's cards down at once. */
class ActionReceiver : BroadcastReceiver() {
  override fun onReceive(c: Context, intent: Intent) {
    val event = intent.getStringExtra(EVENT) ?: return
    val manager = NotificationManagerCompat.from(c)
    manager.cancel(Cards.liveTag(event), Cards.ID)
    if (intent.action != MUTE) return Settings.stop(c, event)
    intent.getStringExtra(TAG)?.let { manager.cancel(it, Cards.ID) }
    val pending = goAsync()
    thread {
      try {
        mute(c, event)
      } finally {
        pending.finish()
      }
    }
  }

  private fun mute(c: Context, event: String) {
    val api = Settings.api(c)
    try {
      val connection = URL("$api/api/me/mutes/$event").openConnection() as HttpURLConnection
      connection.requestMethod = "PUT"
      connection.connectTimeout = 5000
      connection.readTimeout = 5000
      connection.setRequestProperty("accept", "application/json")
      CookieManager.getInstance().getCookie(api)?.let { connection.setRequestProperty("cookie", it) }
      val status = connection.responseCode
      if (status !in 200..299) Log.w(LOG, "mute $event: the API answered $status")
      connection.disconnect()
    } catch (e: Exception) {
      Log.w(LOG, "mute $event failed", e)
    }
  }

  companion object {
    const val MUTE = "io.stann.notifications.MUTE"
    const val STOP = "io.stann.notifications.STOP_LIVE"
    const val EVENT = "event"
    const val TAG = "tag"
    private const val LOG = "StannNotifications"
  }
}

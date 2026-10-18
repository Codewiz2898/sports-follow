package io.stann.notifications

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import android.webkit.CookieManager
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/** A notification's buttons that work without opening the app, each told to the API as this fan: their cookie is
 *  the app's, which React Native keeps in Android's cookie store. "Mute this game" mutes it, as the web's does
 *  (PUT /api/me/mutes/<event>): the server then sends the game nothing more, its live card included (a mute that
 *  didn't reach it, offline, lets them come again). "Stop live card" is this phone's alone: remembered here, and
 *  sent with the FCM token the app registered (PUT /api/push/fcm/stopped/<event>), so the server sends this phone
 *  no more of the game's cards; one that comes anyway (already on its way, or the stop didn't reach it) isn't
 *  shown. Either takes the game's cards down at once. */
class ActionReceiver : BroadcastReceiver() {
  override fun onReceive(c: Context, intent: Intent) {
    val event = intent.getStringExtra(EVENT) ?: return
    val mute = intent.action == MUTE
    if (!mute) Settings.stop(c, event) // first: a card being drawn meanwhile isn't posted (Cards.postLive)
    val manager = NotificationManagerCompat.from(c)
    manager.cancel(Cards.liveTag(event), Cards.ID)
    if (mute) intent.getStringExtra(TAG)?.let { manager.cancel(it, Cards.ID) }
    val pending = goAsync()
    thread {
      try {
        if (mute) call(c, "/api/me/mutes/$event") else stop(c, event)
      } finally {
        pending.finish()
      }
    }
  }

  /** Tells the API this phone stopped the game's live card: the token the app registered says which of the fan's
   *  phones it is (none: the phone isn't on the server, so nothing comes to stop). */
  private fun stop(c: Context, event: String) {
    val token = Settings.token(c) ?: return
    call(c, "/api/push/fcm/stopped/$event", JSONObject().put("token", token).toString())
  }

  private fun call(c: Context, path: String, body: String? = null) {
    val api = Settings.api(c)
    try {
      val connection = URL("$api$path").openConnection() as HttpURLConnection
      connection.requestMethod = "PUT"
      connection.connectTimeout = 5000
      connection.readTimeout = 5000
      connection.setRequestProperty("accept", "application/json")
      CookieManager.getInstance().getCookie(api)?.let { connection.setRequestProperty("cookie", it) }
      if (body != null) {
        connection.doOutput = true
        connection.setRequestProperty("content-type", "application/json")
        connection.outputStream.use { it.write(body.toByteArray()) }
      }
      val status = connection.responseCode
      if (status !in 200..299) Log.w(LOG, "PUT $path: the API answered $status")
      connection.disconnect()
    } catch (e: Exception) {
      Log.w(LOG, "PUT $path failed", e)
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

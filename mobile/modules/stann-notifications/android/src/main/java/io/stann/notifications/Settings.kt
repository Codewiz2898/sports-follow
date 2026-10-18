package io.stann.notifications

import android.content.Context

/** What the notifications keep on the phone, for when the app isn't running: where the API is (for a button
 *  that calls it), whether live cards are on, and the games whose live card the fan stopped. The server is told
 *  both (src/push.ts, ActionReceiver) and sends no card the phone would throw away; these catch any that come. */
internal object Settings {
  private const val DEFAULT_API = "https://stann.in"
  private const val STOPPED_KEPT = 30 // the newest games stopped; older ones are long over (the server keeps as many)

  private fun prefs(c: Context) = c.getSharedPreferences("io.stann.notifications", Context.MODE_PRIVATE)

  fun api(c: Context): String = prefs(c).getString("api", null) ?: DEFAULT_API

  fun setApi(c: Context, api: String) = prefs(c).edit().putString("api", api.trimEnd('/')).apply()

  fun liveCards(c: Context): Boolean = prefs(c).getBoolean("live", true)

  fun setLiveCards(c: Context, on: Boolean) = prefs(c).edit().putBoolean("live", on).apply()

  fun stopped(c: Context, event: String?): Boolean = event != null && event in stoppedGames(c)

  fun stop(c: Context, event: String) {
    val games = (stoppedGames(c) - event + event).takeLast(STOPPED_KEPT)
    prefs(c).edit().putString("stopped", games.joinToString(",")).apply()
  }

  private fun stoppedGames(c: Context): List<String> =
    prefs(c).getString("stopped", "").orEmpty().split(",").filter { it.isNotEmpty() }
}

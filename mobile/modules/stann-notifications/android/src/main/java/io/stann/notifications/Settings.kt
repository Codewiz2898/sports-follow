package io.stann.notifications

import android.content.Context

/** What the notifications keep on the phone, for when the app isn't running: where the API is and the FCM token
 *  the app registered with it (for a button that calls it), whether they wear each sport's colour or all one,
 *  whether live cards are on, and the games whose live card the fan stopped. The server is told about live cards
 *  and stopped games (src/push.ts, ActionReceiver) and sends no card the phone would throw away; these catch any
 *  that come. The colours are the phone's alone. */
internal object Settings {
  private const val DEFAULT_API = "https://stann.in" // TODO(BHA-348): the new domain, as app.config.ts's SITE
  private const val STOPPED_KEPT = 30 // the newest games stopped; older ones are long over (the server keeps as many)

  private fun prefs(c: Context) = c.getSharedPreferences("io.stann.notifications", Context.MODE_PRIVATE)

  fun api(c: Context): String = prefs(c).getString("api", null) ?: DEFAULT_API

  fun setApi(c: Context, api: String) = prefs(c).edit().putString("api", api.trimEnd('/')).apply()

  fun token(c: Context): String? = prefs(c).getString("token", null)

  fun setToken(c: Context, token: String?) = prefs(c).edit().putString("token", token).apply()

  /** Each sport's colour (the default), or all in the app's ink: quieter beside other apps' notifications. */
  fun sportColours(c: Context): Boolean = prefs(c).getBoolean("colours", true)

  fun setSportColours(c: Context, on: Boolean) = prefs(c).edit().putBoolean("colours", on).apply()

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

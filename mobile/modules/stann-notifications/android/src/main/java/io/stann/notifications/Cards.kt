package io.stann.notifications

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.util.Log
import android.view.View
import android.widget.RemoteViews
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.graphics.ColorUtils
import org.json.JSONArray

/**
 * The app's notifications, drawn from what the server sends (sports_follow/fcm.py's data message) to the
 * designs in the canvas (https://claude.ai/artifact/6YxZ16GcGD4zSMNaJLpwX9):
 *
 * - a **moment** (B, the scorecard): a strip in the sport's colour with the moment's word ("Goal", "Hundred")
 *   and its line; opened, the block with the live tag, the player's photo, the word large, the game's board,
 *   and the game's earlier moments under it. Match page and Mute this game under that. One per game: the
 *   server's tag replaces it, as on the web. Loud ones on the "key" channel, the rest silently on "other".
 * - a **live card** (C): one for each game the fan follows while it's on, changed in place every few polls,
 *   with the board, how far along the game is, and the fan's own players in it. Ongoing, silent, gone at the
 *   game's end (an "end" message) or if nothing comes for a while. Where Android 16 lets the app, a Live
 *   Update instead (promoted): Android's own progress layout, never bundled with the rest, first on the lock
 *   screen, with the score in the status bar.
 *
 * Android keeps the header (the app's icon, tinted the sport's colour, its name, the competition, the time)
 * and the buttons; the rest is the app's own layout (res/layout), its display type drawn as pictures (Type).
 * Every one is public: the lock screen shows it whole.
 */
internal object Cards {
  const val ID = 1 // each notification is told apart by its tag, which the server gives it
  private const val LOG = "StannNotifications"
  private const val STALE = 20 * 60 * 1000L // a live card nobody updates for this long goes by itself (the server resends every 10)
  private val BLUE = Color.parseColor("#1f5bef") // stann's own, for a push with no sport (a test)
  private val INK = Color.parseColor("#0d0d0d") // the app's text colour (web/src/styles.css --text)
  private val SHORT = Regex("\\d{1,3}") // a score the board can put side by side: "2", "104"

  fun liveTag(event: String) = "live-e$event"

  fun show(c: Context, d: Map<String, String>) {
    when (d["type"]) {
      "moment" -> moment(c, d)
      "live" -> live(c, d)
      "end" -> d["tag"]?.let { NotificationManagerCompat.from(c).cancel(it, ID) }
    }
  }

  /** Whatever couldn't be drawn, as Android draws a plain one: its title and body. */
  fun plain(c: Context, d: Map<String, String>) {
    if (d["type"] != "moment") return
    val builder = NotificationCompat.Builder(c, if (d["loud"] == "1") Channels.LOUD else Channels.QUIET)
      .setSmallIcon(icon(c))
      .setContentTitle(d["title"])
      .setContentText(d["body"])
      .setStyle(NotificationCompat.BigTextStyle().bigText(d["body"]))
      .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
      .setAutoCancel(true)
      .setContentIntent(open(c, d["url"].orEmpty(), d["id"]))
    post(c, d["tag"] ?: "moment-${d["id"]}", builder)
  }

  /** Takes every live card down (live cards turned off). */
  fun takeDownLive(c: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
    val manager = c.getSystemService(NotificationManager::class.java) ?: return
    manager.activeNotifications.filter { it.tag?.startsWith("live-") == true }.forEach { manager.cancel(it.tag, it.id) }
  }

  // ---------------------------------------------------------------- a moment (B)

  /** A moment at once, then again with the player's photo if it comes (Photos), without a second sound, unless
   *  the fan has cleared it meanwhile. */
  private fun moment(c: Context, d: Map<String, String>) {
    val tag = d["tag"] ?: "moment-${d["id"]}"
    post(c, tag, moment(c, d, tag, null))
    if (d["photo"].isNullOrBlank()) return
    val photo = Photos.get(c, d["photo"]) ?: return
    if (showing(c, tag)) post(c, tag, moment(c, d, tag, photo).setOnlyAlertOnce(true))
  }

  private fun moment(c: Context, d: Map<String, String>, tag: String, photo: Bitmap?): NotificationCompat.Builder {
    val look = Look(d, Settings.sportColours(c))
    val type = Type(c)
    val sides = sides(d["sides"])
    val word = d["word"].orEmpty()
    val line = d["line"].orEmpty().ifBlank { d["title"].orEmpty() }
    val scoreLine = scoreLine(sides, d["status"]).ifBlank { d["body"].orEmpty().lineSequence().firstOrNull().orEmpty() }

    val small = RemoteViews(c.packageName, R.layout.stann_moment_small)
    look.paint(small, R.id.stann_line, R.id.stann_score)
    picture(small, R.id.stann_word, type.word(word, 24f, look.ink, 120f), word)
    small.setTextViewText(R.id.stann_line, line)
    text(small, R.id.stann_score, scoreLine)

    val big = RemoteViews(c.packageName, R.layout.stann_moment_big)
    look.paint(big, R.id.stann_line)
    look.pill(big)
    tag(big, d)
    picture(big, R.id.stann_word, type.word(word, 44f, look.ink, type.wide - 80f), word)
    big.setTextViewText(R.id.stann_line, line)
    big.setInt(R.id.stann_rule, "setBackgroundColor", ColorUtils.setAlphaComponent(look.ink, 0x40))
    board(big, look, type, sides, if (sides.isEmpty()) scoreLine else "")
    strings(d["story"]).let { story ->
      text(big, R.id.stann_story1, story.getOrNull(0).orEmpty())
      text(big, R.id.stann_story2, story.getOrNull(1).orEmpty())
    }
    photo?.let {
      big.setImageViewBitmap(R.id.stann_photo, it)
      big.setViewVisibility(R.id.stann_photo, View.VISIBLE)
    }

    val builder = base(c, d, if (d["loud"] == "1") Channels.LOUD else Channels.QUIET, look, small, big)
      .setAutoCancel(true)
      .setContentIntent(open(c, d["url"].orEmpty(), d["id"]))
    d["match"]?.takeIf { it.isNotBlank() }?.let { builder.addAction(0, "Match page", open(c, it, d["id"])) }
    d["event_id"]?.takeIf { it.isNotBlank() }?.let { builder.addAction(0, "Mute this game", button(c, ActionReceiver.MUTE, it, tag)) }
    return builder
  }

  /** Whether a notification with this tag is still up (Android 6 and later can tell). */
  private fun showing(c: Context, tag: String): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
    return c.getSystemService(NotificationManager::class.java)?.activeNotifications?.any { it.tag == tag } != false
  }

  // ---------------------------------------------------------------- a live card (C)

  private fun live(c: Context, d: Map<String, String>) {
    val event = d["event_id"] ?: return
    if (!Settings.liveCards(c) || Settings.stopped(c, event)) return
    val look = Look(d, Settings.sportColours(c))
    val type = Type(c)
    val sides = sides(d["sides"])
    val players = strings(d["players"])
    val status = listOf("Live", d["status"].orEmpty()).filter { it.isNotBlank() }.joinToString(" · ")

    val small = RemoteViews(c.packageName, R.layout.stann_live_small)
    look.paint(small, R.id.stann_status, R.id.stann_player1)
    small.setTextViewText(R.id.stann_status, status)
    small.setTextViewCompoundDrawablesRelative(R.id.stann_status, R.drawable.stann_dot, 0, 0, 0)
    text(small, R.id.stann_player1, players.firstOrNull() ?: d["league"].orEmpty())
    compact(sides).ifBlank { d["title"].orEmpty() }.let { picture(small, R.id.stann_score, type.number(it, 24f, look.ink, 170f), it) }

    val big = RemoteViews(c.packageName, R.layout.stann_live_big)
    look.paint(big, R.id.stann_league)
    look.pill(big)
    big.setTextViewText(R.id.stann_tag, status)
    big.setTextViewCompoundDrawablesRelative(R.id.stann_tag, R.drawable.stann_dot, 0, 0, 0)
    text(big, R.id.stann_league, d["league"].orEmpty())
    board(big, look, type, sides, if (sides.isEmpty()) d["title"].orEmpty() else "")
    progress(d["progress"])?.let { done ->
      val bar = if (look.darkInk) R.id.stann_bar_dark else R.id.stann_bar_light
      big.setProgressBar(bar, 1000, done, false)
      big.setViewVisibility(bar, View.VISIBLE)
    }
    text(big, R.id.stann_player1, players.getOrNull(0).orEmpty())
    text(big, R.id.stann_player2, players.getOrNull(1).orEmpty())

    val match = d["match"].orEmpty()
    if (promoted(c)) return postLive(c, event, liveUpdate(c, d, look, sides, players, status))
    val builder = base(c, d, Channels.LIVE, look, small, big)
      .setOngoing(true)
      .setOnlyAlertOnce(true)
      .setShowWhen(false)
      .setTimeoutAfter(STALE)
      .setContentIntent(open(c, match, null))
    if (match.isNotBlank()) builder.addAction(0, "Match page", open(c, match, null))
    builder.addAction(0, "Stop live card", button(c, ActionReceiver.STOP, event, liveTag(event)))
    postLive(c, event, builder)
  }

  /** A game's live card, unless the fan stopped it or turned live cards off while it was being drawn: they took
   *  it down then, and the server sends this phone no end to take down one posted after (livecards.android). */
  private fun postLive(c: Context, event: String, builder: NotificationCompat.Builder) {
    if (Settings.liveCards(c) && !Settings.stopped(c, event)) post(c, liveTag(event), builder)
  }

  /** Whether Android lets this app's live cards be Live Updates (Android 16, and the fan hasn't said no). */
  private fun promoted(c: Context): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.BAKLAVA) return false
    return c.getSystemService(NotificationManager::class.java)?.canPostPromotedNotifications() == true
  }

  /** A live card as an Android 16 Live Update, which must be one of Android's own layouts: its progress, the
   *  match clock or the overs, with the game's turns (goals) as points along it, coloured by side; the score
   *  as the title, the fan's player under it, and in the status bar's chip. A game with no progress to show
   *  is the plain layout. */
  private fun liveUpdate(c: Context, d: Map<String, String>, look: Look, sides: List<Pair<String, String>>, players: List<String>, status: String): NotificationCompat.Builder {
    val event = d["event_id"].orEmpty()
    val match = d["match"].orEmpty()
    val score = compact(sides).ifBlank { d["title"].orEmpty() }
    val builder = NotificationCompat.Builder(c, Channels.LIVE)
      .setSmallIcon(icon(c))
      .setColor(look.accent)
      .setContentTitle(score)
      .setContentText(listOf(status, players.firstOrNull().orEmpty()).filter { it.isNotBlank() }.joinToString(" · "))
      .setSubText(d["league"]?.takeIf { it.isNotBlank() })
      .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
      .setOngoing(true)
      .setOnlyAlertOnce(true)
      .setShowWhen(false)
      .setTimeoutAfter(STALE)
      .setRequestPromotedOngoing(true)
      .setShortCriticalText(chip(sides).ifBlank { status })
      .setContentIntent(open(c, match, null))
    progress(d["progress"])?.let { done ->
      val dark = ColorUtils.blendARGB(look.accent, Color.BLACK, 0.45f)
      builder.setStyle(NotificationCompat.ProgressStyle()
        .setProgressSegments(listOf(NotificationCompat.ProgressStyle.Segment(1000).setColor(look.accent)))
        .setProgress(done)
        .setProgressPoints(marks(d["progress"], d["marks"]).map { (at, side) ->
          NotificationCompat.ProgressStyle.Point(at).setColor(if (side == 0) dark else Color.GRAY)
        }))
    }
    if (match.isNotBlank()) builder.addAction(0, "Match page", open(c, match, null))
    builder.addAction(0, "Stop live card", button(c, ActionReceiver.STOP, event, liveTag(event)))
    return builder
  }

  /** The status bar chip's few characters: the score alone ("2–1"), else the first side's ("245/3"). */
  private fun chip(sides: List<Pair<String, String>>): String {
    val (a, b) = sides.getOrNull(0) to sides.getOrNull(1)
    if (a == null || b == null) return ""
    return if (SHORT.matches(a.second) && SHORT.matches(b.second)) "${a.second}–${b.second}" else a.second.ifBlank { b.second }
  }

  /** The game's turns as thousandths along its progress, with their side: [[23, 1]] of "67/90". */
  private fun marks(progress: String?, json: String?): List<Pair<Int, Int>> {
    val of = progress?.split("/")?.getOrNull(1)?.trim()?.toDoubleOrNull()?.takeIf { it > 0 } ?: return emptyList()
    return try {
      val list = JSONArray(json ?: "")
      (0 until list.length()).map { i -> list.getJSONArray(i).let { (1000 * it.getDouble(0) / of).toInt().coerceIn(0, 1000) to it.optInt(1) } }
    } catch (_: Exception) {
      emptyList()
    }
  }

  // ---------------------------------------------------------------- the parts

  /** A notification's colours: its sport's block and the words on it (Sport.colour's "sport" and "on"), or, with
   *  sport colours off on this phone (Alerts' Settings), every sport's block in the app's ink, white on it, and
   *  the icon in stann's blue: quieter beside other apps' notifications. */
  private class Look(d: Map<String, String>, own: Boolean) {
    val colour = if (own) colour(d["colour"], BLUE) else INK
    val ink = if (own) colour(d["ink"], Color.WHITE) else Color.WHITE
    val accent = if (own) colour else BLUE // the icon's tint, and a Live Update's bar
    val darkInk = ColorUtils.calculateLuminance(ink) < 0.5

    /** The block in its colour, and the given words in its ink. */
    fun paint(v: RemoteViews, vararg words: Int) {
      v.setInt(R.id.stann_bg, "setColorFilter", colour)
      for (id in words) v.setTextColor(id, ink)
    }

    /** The live tag of an opened card: an ink pill, or on an ink block a white one with ink words. */
    fun pill(v: RemoteViews) {
      if (colour != INK) return
      v.setInt(R.id.stann_tag, "setBackgroundResource", R.drawable.stann_pill_light)
      v.setTextColor(R.id.stann_tag, INK)
    }
  }

  /** What every one shares: the channel, the icon tinted the sport's colour, the competition in the header,
   *  the plain title and body (for what reads it aloud, and a phone that can't show the layout), public. */
  private fun base(c: Context, d: Map<String, String>, channel: String, look: Look, small: RemoteViews, big: RemoteViews) =
    NotificationCompat.Builder(c, channel)
      .setSmallIcon(icon(c))
      .setColor(look.accent)
      .setContentTitle(d["title"])
      .setContentText(d["body"])
      .setSubText(d["league"]?.takeIf { it.isNotBlank() })
      .setStyle(NotificationCompat.DecoratedCustomViewStyle())
      .setCustomContentView(small)
      .setCustomBigContentView(big)
      .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
      .setPriority(if (channel == Channels.LOUD) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)

  /** The live tag on a moment's block: "Live · 67'" with the red dot while the game's on, else where it ended
   *  ("Full time"), else none. */
  private fun tag(v: RemoteViews, d: Map<String, String>) {
    val status = d["status"].orEmpty()
    if (d["live"] == "1") {
      v.setTextViewText(R.id.stann_tag, listOf("Live", status).filter { it.isNotBlank() }.joinToString(" · "))
      v.setTextViewCompoundDrawablesRelative(R.id.stann_tag, R.drawable.stann_dot, 0, 0, 0)
    } else {
      text(v, R.id.stann_tag, status)
    }
  }

  /** The game's two sides on the block: one row when both scores are short ("ARSENAL 2–1 CHELSEA"), else a
   *  row each ("INDIA 245/3" over "AUSTRALIA"); with no sides, `fallback` (the score as the source wrote it). */
  private fun board(v: RemoteViews, look: Look, type: Type, sides: List<Pair<String, String>>, fallback: String) {
    val (a, b) = sides.getOrNull(0) to sides.getOrNull(1)
    val room = type.wide - 28f // the block's padding
    v.setViewVisibility(R.id.stann_one_row, View.GONE)
    v.setViewVisibility(R.id.stann_two_rows, View.GONE)
    if (a != null && b != null && SHORT.matches(a.second) && SHORT.matches(b.second)) {
      val side = (room - 110f) / 2 // what the score in the middle leaves each side
      v.setViewVisibility(R.id.stann_one_row, View.VISIBLE)
      picture(v, R.id.stann_side_a, type.number(a.first, 17f, look.ink, side), a.first)
      picture(v, R.id.stann_score_big, type.number("${a.second}–${b.second}", 34f, look.ink, 110f), "${a.second} – ${b.second}")
      picture(v, R.id.stann_side_b, type.number(b.first, 17f, look.ink, side), b.first)
    } else if (a != null && b != null) {
      v.setViewVisibility(R.id.stann_two_rows, View.VISIBLE)
      picture(v, R.id.stann_name_a, type.number(a.first, 18f, look.ink, room - 100f), a.first)
      picture(v, R.id.stann_score_a, type.number(a.second, 22f, look.ink, 100f), a.second)
      picture(v, R.id.stann_name_b, type.number(b.first, 18f, look.ink, room - 100f), b.first)
      picture(v, R.id.stann_score_b, type.number(b.second, 22f, look.ink, 100f), b.second)
    }
    text(v, R.id.stann_score_line, fallback)
    v.setTextColor(R.id.stann_score_line, look.ink)
  }

  /** The board in a line, as the collapsed strip says it: "ARS 2–1 CHE · 67'", "IND 245/3 · AUS · 38.2 ov". */
  private fun scoreLine(sides: List<Pair<String, String>>, status: String?) =
    listOf(compact(sides), status.orEmpty()).filter { it.isNotBlank() }.joinToString(" · ")

  private fun compact(sides: List<Pair<String, String>>): String {
    val (a, b) = sides.getOrNull(0) to sides.getOrNull(1)
    if (a == null || b == null) return ""
    if (SHORT.matches(a.second) && SHORT.matches(b.second)) return "${a.first} ${a.second}–${b.second} ${b.first}"
    return listOf("${a.first} ${a.second}".trim(), "${b.first} ${b.second}".trim()).joinToString(" · ")
  }

  /** "67/90" as thousandths of the bar, or null for none. */
  private fun progress(value: String?): Int? {
    val (done, of) = value?.split("/")?.mapNotNull { it.trim().toDoubleOrNull() }?.takeIf { it.size == 2 } ?: return null
    if (of <= 0) return null
    return (1000 * done / of).toInt().coerceIn(0, 1000)
  }

  /** Words drawn in the display type (Type), said aloud as `words`; the view hidden when there are none. */
  private fun picture(v: RemoteViews, id: Int, image: Bitmap?, words: String) {
    if (image == null) return v.setViewVisibility(id, View.GONE)
    v.setImageViewBitmap(id, image)
    v.setContentDescription(id, words)
    v.setViewVisibility(id, View.VISIBLE)
  }

  /** A text, or its view hidden when there's none. */
  private fun text(v: RemoteViews, id: Int, value: String) {
    v.setTextViewText(id, value)
    v.setViewVisibility(id, if (value.isBlank()) View.GONE else View.VISIBLE)
  }

  private fun sides(json: String?): List<Pair<String, String>> = try {
    val list = JSONArray(json ?: "")
    (0 until minOf(2, list.length())).map { i -> list.getJSONArray(i).let { it.optString(0) to it.optString(1) } }
  } catch (_: Exception) {
    emptyList()
  }

  private fun strings(json: String?): List<String> = try {
    val list = JSONArray(json ?: "")
    (0 until list.length()).map { list.optString(it) }.filter { it.isNotBlank() }
  } catch (_: Exception) {
    emptyList()
  }

  private fun colour(value: String?, fallback: Int) = try {
    if (value.isNullOrBlank()) fallback else Color.parseColor(value)
  } catch (_: IllegalArgumentException) {
    fallback
  }

  /** The status bar's mark: the badge expo-notifications' plugin made from app.config.ts's icon. */
  private fun icon(c: Context): Int =
    c.resources.getIdentifier("notification_icon", "drawable", c.packageName).takeIf { it != 0 } ?: c.applicationInfo.icon

  /** Opens the app at one of its pages ("/player/7", "/match/espn/401"), by its own link (stann://), saying
   *  which notification was tapped (n=) so the app can count the open. */
  private fun open(c: Context, path: String, notification: String?): PendingIntent {
    val query = notification?.takeIf { it.isNotBlank() && it != "0" }?.let { (if ('?' in path) "&" else "?") + "n=$it" }.orEmpty()
    val uri = Uri.parse("stann://" + path.trimStart('/') + query)
    val intent = Intent(Intent.ACTION_VIEW, uri).setPackage(c.packageName).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    return PendingIntent.getActivity(c, uri.toString().hashCode(), intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
  }

  private fun button(c: Context, action: String, event: String, tag: String): PendingIntent {
    val intent = Intent(c, ActionReceiver::class.java).setAction(action).putExtra(ActionReceiver.EVENT, event).putExtra(ActionReceiver.TAG, tag)
    return PendingIntent.getBroadcast(c, "$action:$tag".hashCode(), intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
  }

  @SuppressLint("MissingPermission") // checked first: Android 13 asks the fan
  private fun post(c: Context, tag: String, builder: NotificationCompat.Builder) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
      ContextCompat.checkSelfPermission(c, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    Channels.make(c)
    // A group of its own: Android bundles an app's ungrouped notifications (four or more) under a summary of
    // their plain titles, which hides the cards until the fan opens the bundle. Android 16 bundles them all
    // the same, but for a Live Update.
    builder.setGroup(tag)
    NotificationManagerCompat.from(c).notify(tag, ID, builder.build())
  }
}

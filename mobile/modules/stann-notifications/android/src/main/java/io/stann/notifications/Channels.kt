package io.stann.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build

/** The app's notification channels, the one place they're made (the app asks for them before it asks to
 *  notify: src/push.ts). Once made, only Android's settings change a channel's sound or importance, so a
 *  change of either needs a new id. All three show on the lock screen whole: a score is no secret, and none is
 *  low importance, which Android counts as "silent" and most phones leave off the lock screen. */
internal object Channels {
  const val LOUD = "key"
  const val QUIET = "other"
  const val LIVE = "live"
  private const val OLD_QUIET = "quiet" // low importance, so off most lock screens: replaced by QUIET

  fun make(c: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val manager = c.getSystemService(NotificationManager::class.java) ?: return
    manager.createNotificationChannel(NotificationChannel(LOUD, "Key moments", NotificationManager.IMPORTANCE_HIGH).apply {
      description = "Your players’ key moments and results, with sound"
      lockscreenVisibility = Notification.VISIBILITY_PUBLIC
    })
    manager.createNotificationChannel(NotificationChannel(QUIET, "Other moments", NotificationManager.IMPORTANCE_DEFAULT).apply {
      description = "Everything else, and anything in your quiet hours, without sound but on the lock screen"
      setSound(null, null)
      enableVibration(false)
      lockscreenVisibility = Notification.VISIBILITY_PUBLIC
    })
    manager.createNotificationChannel(NotificationChannel(LIVE, "Live scores", NotificationManager.IMPORTANCE_DEFAULT).apply {
      description = "A card for each game you follow while it’s on, kept up to date, without sound"
      setSound(null, null)
      enableVibration(false)
      setShowBadge(false)
      lockscreenVisibility = Notification.VISIBILITY_PUBLIC
    })
    manager.deleteNotificationChannel(OLD_QUIET)
  }
}

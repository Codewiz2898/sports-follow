package io.stann.notifications

import android.util.Log
import com.google.firebase.messaging.RemoteMessage
import expo.modules.notifications.service.ExpoFirebaseMessagingService

/** Every FCM message comes here first (AndroidManifest.xml). One the server sends for the app to draw (data
 *  "v" 2: sports_follow/fcm.py) is drawn by Cards; anything else, and the token's changes, go on to
 *  expo-notifications as before. */
class StannMessagingService : ExpoFirebaseMessagingService() {
  override fun onMessageReceived(remoteMessage: RemoteMessage) {
    val data = remoteMessage.data
    if (data["v"] != "2") return super.onMessageReceived(remoteMessage)
    try {
      Cards.show(this, data)
    } catch (e: Exception) {
      Log.e(TAG, "couldn't draw a ${data["type"]}; showing it plain", e)
      Cards.plain(this, data)
    }
    if (data["type"] == "moment") StannNotificationsModule.arrived(data)
  }

  private companion object {
    const val TAG = "StannNotifications"
  }
}
